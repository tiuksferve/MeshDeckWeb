"""
Testes da cache de mosaicos do mapa (server/tile_cache.py) e das suas rotas.

A rede e o relógio são simulados: nenhum teste vai à internet.
"""

import os
import ssl
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from server import tile_cache
from server.tile_cache import REFRESH_AFTER_S, OFFLINE_BACKOFF_S, TileCache

PNG = b"\x89PNG-tile"


class FakeNet:
    def __init__(self):
        self.online = True
        self.calls = []

    def __call__(self, z, x, y):
        self.calls.append((z, x, y))
        if not self.online:
            raise OSError("sem rede")
        return PNG + f"{z}/{x}/{y}".encode()


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def cache(tmp_path):
    net, clock = FakeNet(), Clock()
    c = TileCache(directory=str(tmp_path), limit_mb=1, fetch=net, clock=clock)
    return c, net, clock


def age(c, path_parts, seconds, clock):
    path = c._path(*path_parts)
    os.utime(path, (clock.now - seconds, clock.now - seconds))


def test_first_view_downloads_and_later_views_use_the_disk(cache):
    c, net, _clock = cache
    assert c.get(10, 500, 380) == PNG + b"10/500/380"
    assert c.get(10, 500, 380) == PNG + b"10/500/380"
    assert net.calls == [(10, 500, 380)]
    assert os.path.exists(c._path(10, 500, 380))


def test_offline_serves_cached_tiles_even_when_old(cache):
    c, net, clock = cache
    c.get(12, 1, 2)
    age(c, (12, 1, 2), REFRESH_AFTER_S + 10, clock)      # antigo: tentaria refrescar
    net.online = False
    assert c.get(12, 1, 2) == PNG + b"12/1/2"           # mas sem rede serve o antigo
    assert c.stats()["offline"] is True


def test_offline_unknown_tile_is_none_and_the_network_is_not_retried_for_a_minute(cache):
    c, net, clock = cache
    net.online = False
    assert c.get(5, 1, 1) is None
    assert c.get(5, 1, 2) is None                       # sem nova tentativa
    assert len(net.calls) == 1
    clock.now += OFFLINE_BACKOFF_S + 1
    net.online = True
    assert c.get(5, 1, 2) is not None                   # passado 1 min volta a tentar
    assert c.stats()["offline"] is False


def test_old_tiles_are_refreshed_when_online(cache):
    c, net, clock = cache
    c.get(8, 3, 4)
    age(c, (8, 3, 4), REFRESH_AFTER_S + 10, clock)
    c.get(8, 3, 4)
    assert len(net.calls) == 2


def test_invalid_coordinates_never_reach_the_network(cache):
    c, net, _clock = cache
    for z, x, y in ((20, 0, 0), (3, 8, 0), (3, 0, -1), (-1, 0, 0)):
        assert c.get(z, x, y) is None
    assert net.calls == []


def test_size_limit_removes_the_oldest_tiles(tmp_path):
    clock = Clock()
    big = b"x" * 300_000                                  # 4 mosaicos > 1 MB
    c = TileCache(directory=str(tmp_path), limit_mb=1, fetch=lambda z, x, y: big, clock=clock)
    for i in range(4):
        c.get(10, i, 0)
        os.utime(c._path(10, i, 0), (clock.now + i, clock.now + i))   # 0 é o mais antigo
    assert not os.path.exists(c._path(10, 0, 0))                     # o mais antigo saiu
    assert not os.path.exists(os.path.dirname(c._path(10, 0, 0)))    # e a pasta vazia também
    assert os.path.exists(c._path(10, 3, 0))
    assert c.stats()["bytes"] <= 1024 * 1024


def test_limit_zero_passes_through_without_saving(tmp_path):
    net = FakeNet()
    c = TileCache(directory=str(tmp_path), limit_mb=0, fetch=net)
    assert c.get(3, 1, 1) is not None
    assert c.stats()["tiles"] == 0


def test_clear_deletes_everything(cache):
    c, _net, _clock = cache
    c.get(4, 1, 1); c.get(4, 2, 1)
    assert c.stats()["tiles"] == 2
    c.clear()
    assert c.stats() == {**c.stats(), "tiles": 0, "bytes": 0}


# ── rotas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def app_client(tmp_path, monkeypatch):
    from core.settings import Settings
    from server.app import create_app
    from server.auth import ServerConfig
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr("core.settings._shared", Settings(directory=str(tmp_path)))
    net = FakeNet()
    monkeypatch.setattr(tile_cache, "fetch_upstream", net)
    config = ServerConfig(Settings(filename="server.json", directory=str(tmp_path)))
    password = config.generated_password
    app, _socketio, mesh = create_app(config)
    client = app.test_client()
    yield client, password, net
    mesh.shutdown()


def test_tile_route_requires_login_and_serves_png(app_client):
    client, password, net = app_client
    assert client.get("/tiles/osm/3/1/2.png").status_code == 401
    client.post("/api/login", json={"password": password})
    response = client.get("/tiles/osm/3/1/2.png")
    assert response.status_code == 200 and response.mimetype == "image/png"
    assert "max-age" in response.headers["Cache-Control"]
    assert client.get("/api/tiles").get_json()["tiles"] == 1


def test_tile_route_offline_unknown_is_404_not_cached(app_client):
    client, password, net = app_client
    client.post("/api/login", json={"password": password})
    net.online = False
    response = client.get("/tiles/osm/3/1/2.png")
    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "no-store"


def test_ssl_context_includes_certifi_roots():
    """No macOS, o Python de python.org não traz certificados: sem os do
    certifi, nenhum mosaico do OpenStreetMap passava a verificação."""
    import certifi

    context = tile_cache.ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    bundled = ssl.create_default_context(cafile=certifi.where()).cert_store_stats()["x509_ca"]
    assert context.cert_store_stats()["x509_ca"] >= bundled > 0
