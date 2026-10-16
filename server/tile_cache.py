"""
server/tile_cache.py — Cache em disco dos mosaicos do mapa (OpenStreetMap).

O mapa pede os mosaicos ao servidor em vez de ir directamente ao
OpenStreetMap. O servidor guarda cada mosaico que alguém viu e, sem
internet, continua a servi-lo — mesmo antigo, porque um mapa desactualizado
é melhor do que nenhum. Assim o mapa funciona no campo nas zonas já vistas,
em todos os dispositivos e tanto por HTTP como por HTTPS.

Regras de utilização do OpenStreetMap (operations.osmfoundation.org/policies/tiles):
o servidor identifica-se com um User-Agent próprio, guarda cada mosaico
pelo menos 7 dias (refresca-o ao fim de 30) e só descarrega o que alguém
está a ver — nunca zonas inteiras de antemão.

O tema Satélite (Esri) não passa por aqui: as condições da Esri restringem o
uso offline, por isso continua a ir directamente à Esri.
"""

import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger("MeshDeck.tiles")

UPSTREAM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
# Contacto para os operadores do OSM: o MeshDeck original é público
# (o repositório do MeshDeckWeb é privado e daria 404 a quem o abrisse).
USER_AGENT = "MeshDeckWeb/2.0 (+https://github.com/tiuksferve/MeshDeck)"
MAX_ZOOM = 19
REFRESH_AFTER_S = 30 * 86400     # mais velho do que isto: tenta actualizar
FETCH_TIMEOUT_S = 6
OFFLINE_BACKOFF_S = 60           # depois de uma falha, não tenta a rede durante 1 min
DEFAULT_LIMIT_MB = 200


def cache_dir() -> str:
    """$XDG_CACHE_HOME/meshdeck/tiles (por omissão ~/.cache/meshdeck/tiles):
    dados que se podem recriar não pertencem ao directório de configuração."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "meshdeck", "tiles")


def ssl_context() -> ssl.SSLContext:
    """Certificados do sistema mais os do certifi. O Python de python.org no
    macOS não usa os certificados do sistema (até correr "Install
    Certificates.command"), e sem o certifi todos os pedidos ao OpenStreetMap
    falhavam a verificar o certificado — o mapa ficava "sem internet"."""
    context = ssl.create_default_context()
    try:
        import certifi                   # vem com o meshtastic (via requests)
        context.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError):
        pass
    return context


_SSL_CONTEXT = None


def fetch_upstream(z: int, x: int, y: int) -> bytes:
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        _SSL_CONTEXT = ssl_context()
    request = urllib.request.Request(UPSTREAM.format(z=z, x=x, y=y),
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S, context=_SSL_CONTEXT) as response:
        if response.status != 200:
            raise urllib.error.HTTPError(request.full_url, response.status, "", None, None)
        return response.read()


class TileCache:
    """get(z, x, y) → bytes do PNG, ou None se não houver como o obter."""

    def __init__(self, directory: str = None, limit_mb: int = DEFAULT_LIMIT_MB,
                 fetch=None, clock=time.time):
        self.directory = os.path.join(directory or cache_dir(), "osm")
        self.limit_bytes = max(int(limit_mb), 0) * 1024 * 1024
        self._fetch = fetch              # None: fetch_upstream (procurado a cada pedido)
        self._clock = clock
        self._lock = threading.Lock()
        self._size = None                # calculado na primeira vez que é preciso
        self._offline_until = 0.0
        self._offline_logged = False

    # -- caminhos ---------------------------------------------------------
    @staticmethod
    def valid(z: int, x: int, y: int) -> bool:
        return 0 <= z <= MAX_ZOOM and 0 <= x < 2 ** z and 0 <= y < 2 ** z

    def _path(self, z, x, y) -> str:
        return os.path.join(self.directory, str(z), str(x), f"{y}.png")

    # -- leitura ----------------------------------------------------------
    def get(self, z: int, x: int, y: int):
        if not self.valid(z, x, y):
            return None
        path = self._path(z, x, y)
        cached = None
        try:
            with open(path, "rb") as fh:
                cached = fh.read()
            age = self._clock() - os.path.getmtime(path)
        except OSError:
            age = None
        if cached is not None and age < REFRESH_AFTER_S:
            return cached

        # Em falta ou com mais de 30 dias: tenta a rede, se não estiver offline.
        if self._clock() < self._offline_until:
            return cached
        try:
            data = (self._fetch or fetch_upstream)(z, x, y)
        except (OSError, urllib.error.URLError, ValueError) as e:
            self._went_offline(e)
            return cached                 # antigo é melhor do que nada
        self._back_online()
        if self.limit_bytes:
            self._store(path, data, replaced=len(cached) if cached is not None else 0)
        return data

    def _went_offline(self, error):
        self._offline_until = self._clock() + OFFLINE_BACKOFF_S
        if not self._offline_logged:
            logger.info("Mapa sem internet — a servir os mosaicos guardados (%s)", error)
            self._offline_logged = True

    def _back_online(self):
        self._offline_until = 0.0
        if self._offline_logged:
            logger.info("Mapa: internet de volta")
            self._offline_logged = False

    # -- escrita e limite -------------------------------------------------
    def _store(self, path: str, data: bytes, replaced: int):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp{threading.get_ident()}"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)        # nunca deixa um mosaico a meio
        except OSError as e:
            logger.debug("não consegui guardar o mosaico %s: %s", path, e)
            return
        with self._lock:
            self._size = self._disk_size() if self._size is None else self._size + len(data) - replaced
            if self._size > self.limit_bytes:
                self._evict()

    def _files(self):
        for root, _dirs, names in os.walk(self.directory):
            for name in names:
                if name.endswith(".png"):
                    full = os.path.join(root, name)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    yield full, st.st_size, st.st_mtime

    def _disk_size(self) -> int:
        return sum(size for _p, size, _m in self._files())

    def _evict(self):
        """Apaga os mosaicos descarregados há mais tempo até ficar a 90 % do
        limite (folga para não varrer o disco a cada mosaico novo). Os que
        se vêem com frequência são refrescados e ficam sempre recentes."""
        target = int(self.limit_bytes * 0.9)
        files = sorted(self._files(), key=lambda f: f[2])
        size = sum(f[1] for f in files)
        removed = 0
        for path, fsize, _mtime in files:
            if size <= target:
                break
            try:
                os.remove(path)
                size -= fsize
                removed += 1
            except OSError:
                continue
            self._remove_empty_dirs(os.path.dirname(path))
        self._size = size
        logger.debug("cache do mapa: %d mosaicos antigos apagados (%.1f MB)", removed, size / 1e6)

    def _remove_empty_dirs(self, folder: str):
        """Apaga as pastas z/x que ficaram vazias, sem subir acima da cache."""
        while os.path.abspath(folder).startswith(os.path.abspath(self.directory) + os.sep):
            try:
                os.rmdir(folder)              # só funciona se estiver vazia
            except OSError:
                return
            folder = os.path.dirname(folder)

    # -- estado e limpeza -------------------------------------------------
    def stats(self) -> dict:
        with self._lock:
            files = list(self._files())
            self._size = sum(f[1] for f in files)
            return {"bytes": self._size, "tiles": len(files),
                    "limit_bytes": self.limit_bytes,
                    "offline": self._clock() < self._offline_until}

    def clear(self):
        with self._lock:
            for path, _size, _mtime in list(self._files()):
                try:
                    os.remove(path)
                except OSError:
                    continue
                self._remove_empty_dirs(os.path.dirname(path))
            self._size = 0
