"""
Testes de interface (browser headless): fixtures partilhadas.

Um servidor MeshDeckWeb real corre numa thread, com nós, mensagens e métricas
simulados (sem rádio), e um Chromium headless abre a página por CDP. Cada
teste começa na aba Nós, com sessão iniciada, em inglês, no tamanho desktop.

Sem Chromium/Chrome instalado, estes testes são saltados. Para correr só os
rápidos: pytest -m "not ui". Para usar outro browser: MESHDECK_BROWSER=…
Se um teste falhar, fica uma captura do ecrã em .pytest_cache/ui-failures/.
"""

import os
import random
import struct
import threading
import time
import zlib

import pytest

from tests.ui.cdp import Browser, find_browser

PASSWORD = "testpass123"
LOCAL_ID = "!11110000"


def _png_1x1() -> bytes:
    """PNG 1×1 cinzento: o mapa recebe mosaicos "a sério" sem ir ao OpenStreetMap."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xc0")) + chunk(b"IEND", b""))


TILE_PNG = _png_1x1()

NODE_NAMES = [
    ("Base Lisboa", "BL"), ("Estação Norte", "EN1"), ("Sintra Repetidor", "SNT"),
    ("Cascais Mobile", "CSC"), ("Almada Sul", "ALM"), ("T-Beam Tiago", "TBT"),
    ("Relay Monsanto", "RMS"), ("Barreiro Node", "BRR"), ("Seixal QTH", "SXL"),
    ("Oeiras Heltec", "OEH"), ("Setúbal Hill", "SET"), ("Mafra Solar", "MFS"),
]
HW_MODELS = ["TBEAM", "HELTEC_V3", "RAK4631", "T_ECHO", "STATION_G2"]


def node_id(index: int) -> str:
    return f"!{0x11110000 + index:08x}"


def pytest_collection_modifyitems(config, items):
    here = os.path.dirname(__file__)
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.ui)


# ---------------------------------------------------------------------------
# Servidor
# ---------------------------------------------------------------------------
def seed(mesh):
    """Uma rede pequena e determinística: 12 nós (3 sem GPS), mensagens e
    métricas suficientes para todas as secções terem conteúdo."""
    rnd = random.Random(4)
    now = int(time.time())
    batch = []
    for i, (long_name, short_name) in enumerate(NODE_NAMES):
        num = 0x11110000 + i
        node = {"num": num,
                "user": {"id": node_id(i), "longName": long_name, "shortName": short_name,
                         "hwModel": HW_MODELS[i % len(HW_MODELS)]},
                "lastHeard": now - rnd.choice([30, 300, 1800, 5400, 90000]),
                "snr": round(rnd.uniform(-15, 10), 2), "hopsAway": rnd.choice([0, 0, 1, 2]),
                "deviceMetrics": {"batteryLevel": rnd.choice([12, 45, 78, 100])}}
        if i % 4 != 3:
            node["position"] = {"latitudeI": int((38.72 + rnd.uniform(-0.3, 0.3)) * 1e7),
                                "longitudeI": int((-9.14 + rnd.uniform(-0.3, 0.3)) * 1e7),
                                "altitude": rnd.randint(5, 400)}
        batch.append((num, node))
    batch[0][1]["isLocal"] = True

    mesh._on_my_node_id(LOCAL_ID)
    mesh._on_local_node_ready("Base Lisboa", "BL", LOCAL_ID, True, True)
    mesh._on_local_position(38.7223, -9.1393, 80, True)
    mesh._on_nodes_batch(batch)

    metrics = mesh.metrics
    for i, (num, node) in enumerate(batch):
        metrics.ingest_raw_packet({
            "id": 9000 + i, "fromId": node_id(i), "rxSnr": node["snr"], "hopsAway": node["hopsAway"],
            "decoded": {"portnum": "TELEMETRY_APP", "telemetry": {"deviceMetrics": {
                "batteryLevel": 70, "voltage": 3.9, "channelUtilization": 8 + i * 1.7,
                "airUtilTx": 1.5 + i * 0.4, "uptimeSeconds": 3600 * (i + 1)}}}})
    for k in range(80):
        i = 1 + k % (len(batch) - 1)
        packet = {"id": 20000 + k, "fromId": node_id(i), "rxSnr": round(rnd.gauss(6 - i * 0.5, 2), 2),
                  "rxRssi": int(-70 - i * 3 + rnd.gauss(0, 4)), "hopStart": 3,
                  "decoded": {"portnum": "POSITION_APP"}}
        hop_limit = 3 - (i % 3)
        packet["hopLimit"] = hop_limit
        if hop_limit != 3:
            packet["relayNode"] = [0x06, 0x02, 0x0b][k % 3]
        metrics.ingest_raw_packet(packet)
    metrics.ingest_raw_packet({"id": 30000, "fromId": LOCAL_ID, "decoded": {
        "portnum": "TELEMETRY_APP", "telemetry": {"localStats": {
            "uptimeSeconds": 7200, "numPacketsRx": 820, "numPacketsTx": 140, "numRxDupe": 250,
            "numPacketsRxBad": 31, "numTxRelay": 96, "numTxRelayCanceled": 41,
            "numOnlineNodes": 9, "numTotalNodes": 12, "noiseFloor": -112,
            "channelUtilization": 18.2, "airUtilTx": 3.1}}}})
    metrics.ingest_raw_packet({"id": 31000, "fromId": node_id(2), "decoded": {
        "portnum": "TELEMETRY_APP", "telemetry": {"environmentMetrics": {
            "temperature": 17.5, "relativeHumidity": 61, "barometricPressure": 1011.2}}}})
    metrics.ingest_message_sent(1, kind="dm")
    metrics.ingest_message_status(1, "ack")

    from tests.test_config_writer import FakeIface
    mesh.connected = True
    mesh.node_ready = True
    mesh._iface = FakeIface()
    mesh.channels = [{"index": 0, "name": "LongFast", "has_psk": True}]
    mesh._on_message_received(0, node_id(1), "Hello everyone", {"id": 501})
    mesh._on_message_received(0, node_id(2), "Good morning", {"id": 502})
    mesh._on_message_received(0, node_id(4), "Direct hello", {"id": 503, "toId": LOCAL_ID,
                                                              "to": 0x11110000})


@pytest.fixture(scope="session")
def ui_server(tmp_path_factory):
    import core.settings as core_settings
    import i18n
    from werkzeug.serving import make_server

    from server.app import create_app
    from server.auth import ServerConfig

    home = tmp_path_factory.mktemp("ui-server")
    old_cache = os.environ.get("XDG_CACHE_HOME")
    os.environ["XDG_CACHE_HOME"] = str(home / "cache")      # cache do mapa isolada
    old_shared, old_language = core_settings._shared, i18n.get_language()
    core_settings._shared = core_settings.Settings(directory=str(home))
    i18n.set_language("en")

    config = ServerConfig(core_settings.Settings(filename="server.json", directory=str(home)))
    config.set_password(PASSWORD)
    app, _socketio, mesh = create_app(config)
    app.tiles._fetch = lambda z, x, y: TILE_PNG
    seed(mesh)

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, name="ui-server", daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{server.server_port}", "app": app, "mesh": mesh}

    server.shutdown()
    mesh.shutdown()
    core_settings._shared = old_shared
    i18n.set_language(old_language)
    if old_cache is None:
        os.environ.pop("XDG_CACHE_HOME", None)
    else:
        os.environ["XDG_CACHE_HOME"] = old_cache


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def browser():
    executable = find_browser()
    if not executable:
        pytest.skip("sem Chromium/Chrome (instale-o ou defina MESHDECK_BROWSER)")
    instance = Browser(executable)
    yield instance
    instance.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed and "page" in item.funcargs:
        folder = os.path.join(str(item.config.rootpath), ".pytest_cache", "ui-failures")
        os.makedirs(folder, exist_ok=True)
        try:
            item.funcargs["page"].screenshot(os.path.join(folder, f"{item.name}.png"))
        except Exception:
            pass


@pytest.fixture
def anonymous_page(browser, ui_server):
    """Página sem sessão iniciada (cookies apagados)."""
    browser.call("Network.clearBrowserCookies")
    browser.viewport(1280, 820)
    browser.navigate(ui_server["url"] + "/")
    browser.drain_errors()
    yield browser


@pytest.fixture
def page(browser, ui_server):
    """Página com sessão iniciada, na aba Nós, sem erros JS acumulados.
    No fim, o teste falha se a página tiver lançado erros JS."""
    browser.viewport(1280, 820)
    browser.navigate(ui_server["url"] + "/")
    if browser.wait_for("(!document.getElementById('login').hidden || !document.getElementById('app').hidden)"
                        " && (document.getElementById('app').hidden ? 'login' : 'app')") == "login":
        browser.js(f"""fetch('/api/login', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
                       body: JSON.stringify({{password: '{PASSWORD}', remember: true}})}}).then((r) => r.status)""")
        browser.navigate(ui_server["url"] + "/")
    browser.wait_for("!document.getElementById('app').hidden", message="a aplicação não abriu")
    try:
        browser.wait_for(f"document.querySelector('#panel-nodes [data-id=\"{node_id(1)}\"]')")
    except AssertionError:
        state = browser.js("[document.getElementById('conn-state').innerText,"
                           " document.querySelectorAll('#panel-nodes [data-id]').length]")
        raise AssertionError(f"a lista de nós não chegou pelo Socket.IO (estado: {state},"
                             f" erros JS: {browser.errors})") from None
    browser.drain_errors()
    yield browser
    errors = browser.drain_errors()
    assert not errors, "erros JS na página:\n" + "\n".join(errors)
