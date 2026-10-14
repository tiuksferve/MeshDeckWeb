"""
core/settings.py — Preferências persistentes sem Qt.

Substitui QSettings("CT7BRA", "MeshDeck"). Guarda em JSON sob
$XDG_CONFIG_HOME/meshdeck (por omissão ~/.config/meshdeck), o que torna o
ficheiro legível e editável à mão — útil num servidor sem interface.

A API espelha a do QSettings (value/set_value/contains/remove) para que tanto
a aplicação desktop como o servidor possam usar a mesma classe. Os aliases
camelCase existem só para substituição directa no código PyQt5 existente.

Ao contrário do QSettings, o JSON preserva os tipos, por isso value() devolve
int, bool e list tal como foram gravados. O parâmetro type= continua a ser
aceite para coagir valores vindos de um ficheiro escrito à mão ou de uma
migração do formato INI do Qt.
"""

import json
import logging
import os
import threading

logger = logging.getLogger("MeshDeck.settings")

APP_DIR_NAME = "meshdeck"

# Ficheiro INI que o QSettings criava, para migrar as preferências na
# primeira execução da versão nova.
_QT_LEGACY_PATH = os.path.join(
    os.path.expanduser("~"), ".config", "CT7BRA", "MeshDeck.conf"
)
_QT_LEGACY_KEYS = ("language", "sound_enabled")


def config_dir() -> str:
    """Directório de configuração, criado se não existir."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, APP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _coerce(value, type_):
    """Coage um valor lido do disco ao tipo pedido.

    Trata strings "true"/"false"/"1"/"0" porque o formato INI do Qt gravava
    booleanos como texto e um ficheiro editado à mão pode fazer o mesmo.
    """
    if type_ is None or value is None:
        return value
    if type_ is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)
    try:
        return type_(value)
    except (TypeError, ValueError):
        logger.warning("settings: não consegui coagir %r a %s", value, type_.__name__)
        return value


class Settings:
    """Dicionário persistente com escrita atómica.

    Instâncias com o mesmo filename partilham o ficheiro mas não o estado em
    memória; use settings() para obter a instância partilhada do processo.
    """

    def __init__(self, filename: str = "settings.json", directory: str = None):
        self._path = os.path.join(directory or config_dir(), filename)
        self._lock = threading.RLock()
        self._data: dict = {}
        self._load()

    @property
    def path(self) -> str:
        return self._path

    # -- leitura ----------------------------------------------------------
    def value(self, key: str, default=None, type=None):
        """Lê uma chave. `type` coage o resultado (compatível com QSettings)."""
        with self._lock:
            if key not in self._data:
                return default
            return _coerce(self._data[key], type)

    def contains(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def all(self) -> dict:
        """Cópia de todas as preferências."""
        with self._lock:
            return dict(self._data)

    # -- escrita ----------------------------------------------------------
    def set_value(self, key: str, value):
        with self._lock:
            if self._data.get(key, object()) == value and key in self._data:
                return
            self._data[key] = value
            self._save()

    def update(self, mapping: dict):
        """Grava várias chaves com uma só escrita em disco."""
        with self._lock:
            changed = False
            for k, v in mapping.items():
                if self._data.get(k, object()) != v or k not in self._data:
                    self._data[k] = v
                    changed = True
            if changed:
                self._save()

    def remove(self, key: str):
        with self._lock:
            if self._data.pop(key, None) is not None:
                self._save()

    def clear(self):
        with self._lock:
            self._data.clear()
            self._save()

    def sync(self):
        """Força a escrita em disco. Compatibilidade com QSettings.sync()."""
        with self._lock:
            self._save()

    # -- alias Qt, para substituição directa no código PyQt5 --------------
    setValue = set_value

    # -- interno ----------------------------------------------------------
    def _load(self):
        if not os.path.exists(self._path):
            migrated = self._migrate_from_qt()
            if migrated:
                with self._lock:
                    self._data = migrated
                    self._save()
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._data = data
            else:
                logger.warning("settings: %s não contém um objecto JSON — ignorado", self._path)
        except (OSError, json.JSONDecodeError) as e:
            # Não apagamos o ficheiro: o utilizador pode querer recuperá-lo.
            logger.warning("settings: não consegui ler %s (%s) — a usar defaults", self._path, e)

    def _save(self):
        """Escreve via ficheiro temporário + rename, para nunca deixar JSON truncado."""
        tmp = f"{self._path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, ensure_ascii=False, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        except OSError as e:
            logger.error("settings: não consegui gravar %s (%s)", self._path, e)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    @staticmethod
    def _migrate_from_qt() -> dict:
        """Lê o .conf do QSettings, se existir, para não perder as preferências."""
        if not os.path.exists(_QT_LEGACY_PATH):
            return {}
        out = {}
        try:
            with open(_QT_LEGACY_PATH, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if "=" not in line or line.startswith(("[", "#", ";")):
                        continue
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip()
                    if k not in _QT_LEGACY_KEYS:
                        continue
                    out[k] = _coerce(v, bool) if k == "sound_enabled" else v
        except OSError as e:
            logger.debug("settings: migração do QSettings falhou (%s)", e)
            return {}
        if out:
            logger.info("settings: preferências migradas do QSettings: %s", sorted(out))
        return out


_shared: Settings = None
_shared_lock = threading.Lock()


def settings() -> Settings:
    """Instância de preferências partilhada pelo processo."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Settings()
        return _shared
