"""
server/auth.py — Autenticação do servidor.

A aplicação desktop era de utilizador único na própria máquina. Um servidor
na rede local expõe escrita completa da configuração do nó, incluindo nomes
de canais e PSK, por isso há uma password.

Na primeira execução é gerada uma password aleatória e impressa na consola;
só o seu hash fica em disco. O utilizador pode trocá-la depois.

Isto é o mínimo, não é segurança de transporte: numa rede não confiável,
HTTP simples continua a expor a sessão. O ficheiro de configuração aceita
caminhos de certificado para servir por TLS.
"""

import logging
import os
import secrets
import time
from functools import wraps

from flask import jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash

from core.settings import Settings
from i18n import tr

logger = logging.getLogger("MeshDeck.auth")

SESSION_FLAG = "meshdeck_auth"
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8080
DEFAULT_HTTPS_PORT = 8443   # HTTPS ao lado do HTTP, para quem precisa (bússola)
PASSWORD_LENGTH_BYTES = 9   # ~12 caracteres em base64url
MAX_RECENT_TARGETS = 10     # nós TCP não favoritos guardados no histórico


class ServerConfig:
    """Configuração persistente do servidor, em ~/.config/meshdeck/server.json.

    Guarda a chave de sessão do Flask, o hash da password e as preferências
    de bind e TLS. Nunca guarda a password em claro.
    """

    def __init__(self, settings: Settings = None):
        self._s = settings or Settings(filename="server.json")
        self.generated_password = None   # só preenchido na primeira execução
        self._ensure_secrets()

    @property
    def path(self) -> str:
        return self._s.path

    def _ensure_secrets(self):
        updates = {}
        if not self._s.value("secret_key"):
            updates["secret_key"] = secrets.token_hex(32)
        if not self._s.value("password_hash"):
            password = secrets.token_urlsafe(PASSWORD_LENGTH_BYTES)
            updates["password_hash"] = generate_password_hash(password)
            self.generated_password = password
        if updates:
            self._s.update(updates)

    # -- credenciais ------------------------------------------------------
    @property
    def secret_key(self) -> str:
        return self._s.value("secret_key")

    def check_password(self, password: str) -> bool:
        stored = self._s.value("password_hash")
        if not stored or not password:
            return False
        return check_password_hash(stored, password)

    def set_password(self, password: str):
        if not password or len(password) < 8:
            raise ValueError(tr("a password tem de ter pelo menos 8 caracteres"))
        self._s.set_value("password_hash", generate_password_hash(password))
        self.generated_password = None
        logger.info("password do servidor alterada")

    # -- rede -------------------------------------------------------------
    @property
    def bind_host(self) -> str:
        return self._s.value("bind_host", DEFAULT_BIND_HOST)

    @property
    def bind_port(self) -> int:
        return int(self._s.value("bind_port", DEFAULT_BIND_PORT, type=int))

    @property
    def tls_cert(self):
        return self._s.value("tls_cert")

    @property
    def tls_key(self):
        return self._s.value("tls_key")

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_cert and self.tls_key)

    @property
    def https_port(self) -> int:
        return int(self._s.value("https_port", DEFAULT_HTTPS_PORT, type=int))

    def set_https_port(self, port: int):
        self._s.set_value("https_port", int(port))

    def set_tls(self, cert: str, key: str):
        self._s.update({"tls_cert": os.path.abspath(cert), "tls_key": os.path.abspath(key)})

    def clear_tls(self):
        self._s.update({"tls_cert": "", "tls_key": ""})

    def set_bind(self, host: str, port: int):
        self._s.update({"bind_host": host, "bind_port": int(port)})

    # -- último alvo usado, para o diálogo de ligação sugerir -------------
    @property
    def last_target(self) -> dict:
        return {
            "mode":        self._s.value("conn_mode", "tcp"),
            "host":        self._s.value("node_host", "localhost"),
            "port":        int(self._s.value("node_port", 4403, type=int)),
            "serial_port": self._s.value("serial_port", ""),
        }

    def remember_target(self, mode: str, host: str, port: int, serial_port: str):
        self._s.update({
            "conn_mode":   mode,
            "node_host":   host,
            "node_port":   int(port),
            "serial_port": serial_port or "",
        })

    # -- nós TCP usados: histórico + favoritos ----------------------------
    # Lista de {host, port, name, favorite, last_used}. Os favoritos ficam
    # sempre; dos restantes guardam-se só os MAX_RECENT_TARGETS mais recentes.
    @property
    def saved_targets(self) -> list:
        targets = [dict(t) for t in (self._s.value("saved_targets") or [])]
        targets.sort(key=lambda t: (not t.get("favorite"), -t.get("last_used", 0)))
        return targets

    def _store_targets(self, targets: list):
        favorites = [t for t in targets if t.get("favorite")]
        recent = sorted((t for t in targets if not t.get("favorite")),
                        key=lambda t: -t.get("last_used", 0))[:MAX_RECENT_TARGETS]
        self._s.set_value("saved_targets", favorites + recent)

    @staticmethod
    def _same(target: dict, host: str, port: int) -> bool:
        return target.get("host") == host and int(target.get("port", 0)) == int(port)

    def record_target(self, host: str, port: int, name: str = None):
        """Regista uma ligação bem-sucedida (ou o nome do nó, quando chega)."""
        host = (host or "").strip()
        if not host:
            return
        targets = self.saved_targets
        entry = next((t for t in targets if self._same(t, host, port)), None)
        if entry is None:
            entry = {"host": host, "port": int(port), "name": "", "favorite": False}
            targets.append(entry)
        entry["last_used"] = time.time()
        if name:
            entry["name"] = name
        self._store_targets(targets)

    def set_target_favorite(self, host: str, port: int, favorite: bool) -> bool:
        targets = self.saved_targets
        entry = next((t for t in targets if self._same(t, host, port)), None)
        if entry is None:
            return False
        entry["favorite"] = bool(favorite)
        self._store_targets(targets)
        return True

    def forget_target(self, host: str, port: int) -> bool:
        targets = self.saved_targets
        kept = [t for t in targets if not self._same(t, host, port)]
        if len(kept) == len(targets):
            return False
        self._store_targets(kept)
        return True


def is_authenticated() -> bool:
    return bool(session.get(SESSION_FLAG))


def login(remember: bool = True):
    session[SESSION_FLAG] = True
    session.permanent = bool(remember)


def logout():
    session.pop(SESSION_FLAG, None)


def login_required(view):
    """Devolve 401 em JSON em vez de redirecionar: o cliente é uma SPA."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            return jsonify({"error": tr("autenticação necessária")}), 401
        return view(*args, **kwargs)
    return wrapper


def announce_first_run(config: ServerConfig, bind_host: str, bind_port: int):
    """Imprime a password gerada e o aviso de exposição de rede."""
    lines = ["", "=" * 68]
    if config.generated_password:
        lines += [
            "  MeshDeckWeb — primeira execução",
            "",
            f"  Password gerada:  {config.generated_password}",
            "",
            "  Guarde-a agora: só o hash fica em disco e esta linha não",
            f"  volta a aparecer. Pode trocá-la em {config.path}",
            "  apagando a chave password_hash, ou pela interface.",
        ]
    else:
        lines.append("  MeshDeckWeb — servidor web")
    lines += ["", f"  Interface:  http://{bind_host}:{bind_port}/"]
    if config.tls_enabled:
        https = f"https://{bind_host}:{config.https_port}"
        lines += [
            f"  HTTPS:      {https}/   (necessário para a bússola do telemóvel)",
            f"  CA local para instalar nos dispositivos:  {https}/ca.crt",
        ]
    if bind_host not in ("127.0.0.1", "localhost", "::1"):
        lines += [
            "",
            "  AVISO: ligado a " + bind_host + ", acessível a toda a rede local.",
            "  Quem entrar pode reconfigurar o nó, incluindo canais e PSK.",
        ]
        lines += [
            "  Em HTTP simples a sessão viaja em claro — use o endereço HTTPS",
            "  (run_server.py --make-cert) ou uma rede de confiança.",
        ]
    lines += ["=" * 68, ""]
    print("\n".join(lines), flush=True)
