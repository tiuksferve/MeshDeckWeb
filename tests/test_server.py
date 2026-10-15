"""
Testes do servidor: autenticação, endpoints e difusão de eventos.

Usa o cliente de teste do Flask (sem abrir socket real) e inspecciona
directamente a MeshSession para o que passa por Socket.IO, de modo a não
depender de um servidor a correr.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.config_defs import MESHTASTIC_CONFIG_DEFS
from core.settings import Settings
from server.app import config_defs_json, create_app, i18n_catalog
from server.auth import ServerConfig
from tests.fake_meshtastic import (
    LOCAL_ID, LOCAL_NUM, FakeInterface, emit_connection_established, emit_packet,
    emit_text, packet,
)

PEER_NUM = 0x11110001
PEER_ID = "!11110001"


@pytest.fixture
def server(tmp_path, monkeypatch):
    """App, cliente HTTP e sessão, com configuração isolada em tmp_path."""
    # As preferências partilhadas (idioma, …) também: set_language grava-as.
    monkeypatch.setattr("core.settings._shared", Settings(directory=str(tmp_path)))
    config = ServerConfig(Settings(filename="server.json", directory=str(tmp_path)))
    password = config.generated_password
    app, socketio, mesh = create_app(config)
    app.config["TESTING"] = True

    broadcasts = []
    mesh._broadcast = lambda event, payload: broadcasts.append((event, payload))

    with app.test_client() as client:
        yield {"app": app, "client": client, "mesh": mesh,
               "password": password, "broadcasts": broadcasts}

    mesh.shutdown()


def login(server):
    return server["client"].post("/api/login",
                                 json={"password": server["password"]})


@pytest.fixture
def wired(server):
    """Servidor autenticado com um worker ligado a uma interface falsa."""
    login(server)
    mesh = server["mesh"]
    from core.mesh_worker import MeshtasticWorker

    iface = FakeInterface(nodes_by_num={
        PEER_NUM: {
            "num": PEER_NUM,
            "user": {"id": PEER_ID, "longName": "Peer One",
                     "shortName": "P1", "hwModel": "HELTEC_V3"},
            "hopsAway": 2,
        },
    })
    worker = MeshtasticWorker(hostname="127.0.0.1", port=45999, loop=mesh.loop)
    mesh.worker = worker
    mesh._wire_signals(worker)
    worker._subscribe_pubsub()
    worker.iface = iface
    worker._connected = True
    mesh.connected = True
    yield server, iface
    worker._unsubscribe_pubsub()


def wait_for(broadcasts, event, count=1, timeout=3.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hits = [p for name, p in broadcasts if name == event]
        if len(hits) >= count:
            return hits
        time.sleep(0.005)
    seen = sorted({name for name, _ in broadcasts})
    raise AssertionError(f"evento {event} não chegou; recebidos: {seen}")


# ── autenticação ─────────────────────────────────────────────────────────────

def test_index_is_served(server):
    response = server["client"].get("/")
    assert response.status_code == 200
    assert b"MeshDeck" in response.data


def test_whoami_before_login(server):
    data = server["client"].get("/api/whoami").get_json()
    assert data["authenticated"] is False
    assert data["app"] == "MeshDeck"


def test_protected_endpoints_require_login(server):
    for path in ("/api/state", "/api/config/defs",
                 "/api/channels", "/api/serial-ports",
                 "/api/metrics/overview", "/api/node/!a/packet"):
        response = server["client"].get(path)
        assert response.status_code == 401, path
        assert response.get_json()["error"]


def test_i18n_catalog_is_public_for_the_login_screen(server):
    response = server["client"].get("/api/i18n?lang=en")
    assert response.status_code == 200
    assert response.get_json()["strings"]["Entrar"] == "Sign in"


def test_login_with_the_generated_password(server):
    assert login(server).status_code == 200
    assert server["client"].get("/api/whoami").get_json()["authenticated"] is True


def test_login_with_a_wrong_password(server):
    response = server["client"].post("/api/login", json={"password": "errada"})
    assert response.status_code == 401
    assert response.get_json()["error"]
    assert server["client"].get("/api/state").status_code == 401


def test_login_without_a_body(server):
    assert server["client"].post("/api/login").status_code == 401


def test_logout_revokes_access(server):
    login(server)
    server["client"].post("/api/logout")
    assert server["client"].get("/api/state").status_code == 401


def test_password_is_never_stored_in_clear(server, tmp_path):
    stored = json.loads((tmp_path / "server.json").read_text())
    assert "password_hash" in stored
    assert server["password"] not in json.dumps(stored)


# ── endpoints de recursos ────────────────────────────────────────────────────

def test_state_snapshot_shape(server):
    login(server)
    snapshot = server["client"].get("/api/state").get_json()
    for key in ("language", "connection", "nodes", "counts",
                "local_node", "channels", "messages", "favorites",
                "metric_sections", "config_lock", "serial_ports", "log"):
        assert key in snapshot, key
    assert len(snapshot["metric_sections"]) == 12
    json.dumps(snapshot)


def test_config_defs_endpoint_drives_every_section(server):
    login(server)
    data = server["client"].get("/api/config/defs").get_json()
    assert len(data["sections"]) == len(MESHTASTIC_CONFIG_DEFS) == 24
    assert sum(len(s["fields"]) for s in data["sections"]) == sum(map(len, MESHTASTIC_CONFIG_DEFS.values()))
    combos = [f for s in data["sections"] for f in s["fields"] if f["type"] == "combo"]
    assert all(f["choices"] for f in combos)
    spins = [f for s in data["sections"] for f in s["fields"]
             if f["type"] in ("spin_int", "spin_float")]
    assert all("min" in f and "max" in f for f in spins)


def test_i18n_endpoint_serves_both_languages(server):
    login(server)
    pt = server["client"].get("/api/i18n?lang=pt").get_json()
    en = server["client"].get("/api/i18n?lang=en").get_json()
    assert pt["language"] == "pt" and en["language"] == "en"
    assert len(pt["strings"]) == len(en["strings"]) > 500
    assert pt["strings"]["Cancelar resposta"] != en["strings"]["Cancelar resposta"]


def test_metrics_html_endpoint(server):
    login(server)
    response = server["client"].get("/api/metrics/overview")
    assert response.status_code == 200
    assert response.data.startswith(b"<!DOCTYPE html>")
    assert "text/html" in response.headers["Content-Type"]


def test_metrics_data_endpoint(server):
    login(server)
    data = server["client"].get("/api/metrics/rf/data").get_json()
    assert "snr_avg" in data


def test_metrics_refresh_endpoint(server):
    login(server)
    rf = server["client"].get("/api/metrics/rf/refresh").get_json()
    assert rf["has_data"] is False          # rf tem ecrã de espera
    assert "snr_avg" in rf["data"]
    local = server["client"].get("/api/metrics/local_node/refresh").get_json()
    assert local["reload_key"] == "_waiting" and local["data"] == {}
    assert server["client"].get("/api/metrics/inexistente/refresh").status_code == 404


def test_unknown_metrics_section_is_404(server):
    login(server)
    assert server["client"].get("/api/metrics/inexistente").status_code == 404
    assert server["client"].get("/api/metrics/inexistente/data").status_code == 404


def test_serial_ports_endpoint(server):
    login(server)
    data = server["client"].get("/api/serial-ports").get_json()
    assert isinstance(data["ports"], list)
    assert data["bridge"]["running"] is False


def test_node_packet_endpoint_is_404_when_unknown(server):
    login(server)
    assert server["client"].get("/api/node/!deadbeef/packet").status_code == 404


def test_vendored_assets_are_served(server):
    for path in ("/vendor/socket.io.min.js", "/vendor/chart.umd.min.js",
                 "/vendor/leaflet/leaflet.js", "/vendor/leaflet/leaflet.css",
                 "/vendor/leaflet/images/marker-icon.png",
                 "/css/app.css", "/js/app.js"):
        response = server["client"].get(path)
        assert response.status_code == 200, path
        assert len(response.data) > 100, path


# ── difusão de eventos ───────────────────────────────────────────────────────

def test_connection_established_broadcasts_state(wired):
    server, iface = wired
    broadcasts = server["broadcasts"]
    emit_connection_established(iface)

    wait_for(broadcasts, "connection")
    wait_for(broadcasts, "my_node_id")
    wait_for(broadcasts, "nodes")
    wait_for(broadcasts, "local_node")
    wait_for(broadcasts, "channels")
    wait_for(broadcasts, "node_ready")

    assert server["mesh"].nodes.count() >= 2
    assert server["mesh"].local_node["node_id"] == LOCAL_ID


def test_packet_updates_nodes_and_metrics(wired):
    server, iface = wired
    mesh = server["mesh"]
    emit_packet(packet("TELEMETRY_APP", PEER_NUM, rx_snr=7.0, decoded={
        "telemetry": {"deviceMetrics": {"batteryLevel": 61, "voltage": 3.95}}}), iface)

    hits = wait_for(server["broadcasts"], "nodes")
    node = hits[-1]["nodes"][0]
    assert node["id_string"] == PEER_ID
    assert node["battery_level"] == 61
    assert mesh.metrics.data_for("overview")["total_pkts"] >= 1


def test_packet_projection_is_compact(wired):
    """O pacote bruto não vai para o browser: só os campos que a UI mostra."""
    server, iface = wired
    emit_packet(packet("POSITION_APP", PEER_NUM, rx_snr=3.0, decoded={
        "position": {"latitudeI": 385000000, "longitudeI": -91000000}}), iface)
    projection = wait_for(server["broadcasts"], "packet")[-1]
    assert set(projection) == {"from_id", "to_id", "portnum", "snr", "rssi",
                               "hops", "via_mqtt", "channel", "time"}
    assert projection["portnum"] == "POSITION_APP"
    json.dumps(projection)


def test_incoming_message_is_stored_and_broadcast(wired):
    server, iface = wired
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, channel=0,
                     decoded={"text": "olá servidor"}), iface)
    payload = wait_for(server["broadcasts"], "message")[-1]
    assert payload["key"] == "ch:0"
    assert payload["message"]["text"] == "olá servidor"
    assert payload["unread"]["ch:0"] == 1
    assert server["mesh"].messages.conversation("ch:0")[0]["text"] == "olá servidor"


def test_sent_message_is_echoed_with_pending_status(wired):
    server, iface = wired
    server["mesh"].send_channel_message(0, "do servidor")
    payload = wait_for(server["broadcasts"], "message_sent")[-1]
    assert payload["message"]["outgoing"] is True
    assert payload["message"]["status"] == "sending"
    assert iface.sent_text[0]["text"] == "do servidor"


def test_reply_links_to_the_original_message(wired):
    """↩ no browser: a resposta sai com o reply_id nativo do Meshtastic e
    fica guardada ligada à mensagem original."""
    server, iface = wired
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, channel=0, id=0x1234,
                     decoded={"text": "alguém na escuta?"}), iface)
    received = wait_for(server["broadcasts"], "message")[-1]["message"]
    assert received["packet_id"] == 0x1234

    server["mesh"].send_channel_message(0, "eu!", reply_id=received["packet_id"])
    sent = wait_for(server["broadcasts"], "message_sent")[-1]["message"]
    assert iface.sent_text[-1]["replyId"] == 0x1234
    assert sent["reply_id"] == 0x1234


def test_incoming_reply_keeps_the_reply_id(wired):
    server, iface = wired
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, channel=0, id=0x99,
                     decoded={"text": "resposta", "replyId": 0x55}), iface)
    message = wait_for(server["broadcasts"], "message")[-1]["message"]
    assert message["reply_id"] == 0x55


def test_message_without_reply_sends_no_reply_id(wired):
    server, iface = wired
    server["mesh"].send_channel_message(0, "normal")
    wait_for(server["broadcasts"], "message_sent")
    assert iface.sent_text[-1]["replyId"] is None


def test_ack_updates_the_stored_message(wired):
    server, iface = wired
    server["mesh"].send_channel_message(0, "x")
    sent = wait_for(server["broadcasts"], "message_sent")[-1]
    packet_id = sent["message"]["packet_id"]

    emit_packet(packet("ROUTING_APP", PEER_NUM, decoded={
        "routing": {"errorReason": "NONE"}, "requestId": packet_id}), iface)
    status = wait_for(server["broadcasts"], "message_status")[-1]
    assert status["status"] == "ack"
    assert status["message"]["status"] == "ack"


def test_traceroute_result_is_broadcast(wired):
    server, iface = wired
    emit_packet(packet("TRACEROUTE_APP", PEER_NUM, to=LOCAL_NUM, decoded={
        "traceroute": {"route": [], "snrTowards": [20], "routeBack": [], "snrBack": [16]},
    }), iface)
    payload = wait_for(server["broadcasts"], "traceroute")[-1]
    assert payload["origin"] == LOCAL_ID and payload["dest"] == PEER_ID
    assert payload["forward"] == [{"from": LOCAL_ID, "to": PEER_ID, "snr": 5.0}]


def test_neighbor_info_is_broadcast_and_fed_to_metrics(wired):
    server, iface = wired
    emit_packet(packet("NEIGHBORINFO_APP", PEER_NUM, decoded={
        "neighborinfo": {"neighbors": [{"nodeId": LOCAL_NUM, "snr": 6.0}]}}), iface)
    payload = wait_for(server["broadcasts"], "neighbors")[-1]
    assert payload["from_id"] == PEER_ID
    assert payload["neighbors"] == [{"id": LOCAL_ID, "snr": 6.0}]
    assert server["mesh"].metrics.has_data("neighbors")


def test_channels_event_hides_the_psk(wired):
    """A PSK só sai pelo endpoint autenticado do editor de canais."""
    server, iface = wired
    emit_connection_established(iface)
    payload = wait_for(server["broadcasts"], "channels")[-1]
    for channel in payload["channels"]:
        assert set(channel) == {"index", "name", "has_psk"}

    served = server["client"].get("/api/channels").get_json()["channels"]
    secondary = [c for c in served if c["index"] == 1][0]
    assert secondary["psk"], "o editor precisa da PSK"


def test_snapshot_after_traffic_is_serialisable(wired):
    server, iface = wired
    emit_connection_established(iface)
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, decoded={"text": "x"}), iface)
    wait_for(server["broadcasts"], "message")
    snapshot = server["client"].get("/api/state").get_json()
    json.dumps(snapshot)
    assert snapshot["counts"]["total"] >= 2
    assert snapshot["messages"]["conversations"]["ch:0"]


# ── lock de configuração ─────────────────────────────────────────────────────

def test_config_lock_is_exclusive(server):
    lock = server["mesh"].config_lock
    assert lock.acquire("cliente-a") is True
    assert lock.acquire("cliente-b") is False
    assert lock.acquire("cliente-a") is True      # reentrante para o dono
    assert lock.state()["held_by"] == "cliente-a"
    lock.release("cliente-a")
    assert lock.acquire("cliente-b") is True


def test_config_lock_release_by_a_non_holder_is_ignored(server):
    lock = server["mesh"].config_lock
    lock.acquire("cliente-a")
    lock.release("cliente-b")
    assert lock.state()["held_by"] == "cliente-a"


def test_config_lock_expires(server, monkeypatch):
    """Um cliente que desapareça sem libertar não bloqueia o nó para sempre."""
    import server.session as session_module
    lock = server["mesh"].config_lock
    lock.acquire("cliente-a")
    monkeypatch.setattr(session_module.time, "time",
                        lambda: lock._since + session_module.CONFIG_LOCK_TIMEOUT_S + 1)
    assert lock.acquire("cliente-b") is True


# ── idioma ───────────────────────────────────────────────────────────────────

def test_set_language_switches_metric_labels(server):
    mesh = server["mesh"]
    pt = mesh.set_language("pt")
    assert pt["language"] == "pt"
    overview_pt = [s["label"] for s in pt["metric_sections"] if s["key"] == "overview"][0]
    en = mesh.set_language("en")
    overview_en = [s["label"] for s in en["metric_sections"] if s["key"] == "overview"][0]
    assert overview_pt != overview_en


def test_unknown_language_falls_back(server):
    assert server["mesh"].set_language("klingon")["language"] == "en"


def test_set_language_is_persisted(server):
    from core.settings import settings
    server["mesh"].set_language("pt")
    assert settings().value("language") == "pt"


# ── exportações usadas pelo frontend ─────────────────────────────────────────

def test_config_defs_json_is_serialisable():
    json.dumps(config_defs_json())


def test_i18n_catalog_falls_back_for_unknown_language():
    assert i18n_catalog("klingon") == i18n_catalog("en")


def test_node_events_and_snapshot_share_one_shape(wired):
    """O cliente recebe a mesma forma pelo snapshot e pelos eventos."""
    server, iface = wired
    emit_connection_established(iface)
    event_nodes = wait_for(server["broadcasts"], "nodes")[-1]["nodes"]
    snapshot_nodes = server["client"].get("/api/state").get_json()["nodes"]

    shapes = {tuple(sorted(n)) for n in event_nodes + snapshot_nodes}
    assert len(shapes) == 1, f"{len(shapes)} formas diferentes"
    assert "favorite" in shapes.pop()


def test_clear_metrics_command(wired):
    server, iface = wired
    mesh = server["mesh"]
    emit_packet(packet("POSITION_APP", PEER_NUM, decoded={
        "position": {"latitudeI": 385000000, "longitudeI": -91000000}}), iface)
    wait_for(server["broadcasts"], "nodes")
    assert mesh.metrics.data_for("overview")["total_pkts"] >= 1

    mesh.clear_metrics()
    assert mesh.metrics.data_for("overview")["total_pkts"] == 0


def test_metrics_endpoint_survives_a_malformed_node_name(wired):
    """Regressão: um nó real com bytes UTF-8 inválidos no shortName fazia
    /api/metrics/<secção> devolver 500 Internal Server Error, porque HTML em
    texto simples (ao contrário do JSON) não escapa surrogates Unicode soltos
    antes de serem codificados para a resposta HTTP."""
    server, iface = wired
    dirty = "Bo\udcc3Nome"
    emit_packet(packet("NODEINFO_APP", PEER_NUM, decoded={"user": {
        "longName": dirty, "shortName": dirty, "hwModel": "TBEAM",
    }}), iface)
    wait_for(server["broadcasts"], "nodes")

    for section in ("overview", "nodes", "local_node"):
        response = server["client"].get(f"/api/metrics/{section}")
        assert response.status_code == 200, f"{section}: {response.status_code}"
        assert response.data.decode("utf-8")   # a própria asserção já falharia se inválido


def test_metrics_endpoint_survives_corruption_that_bypasses_source_cleaning(wired):
    """Rede de segurança à saída: mesmo que um surrogate chegue a _node_short
    por um caminho não coberto pela limpeza em core.mesh_worker (o ingestor de
    métricas tem múltiplos pontos de entrada), o endpoint HTML nunca pode
    devolver 500 — a sanitização final em server.app cobre qualquer campo."""
    server, _iface = wired
    mesh = server["mesh"]
    mesh.metrics.set_local_node_id(LOCAL_ID)
    # Injecta directamente no cache interno, contornando toda a limpeza de entrada —
    # simula um campo que uma fonte futura ainda não sanitize alimente.
    mesh.metrics._node_short[LOCAL_ID] = "Bo\udcc3Nome"

    response = server["client"].get("/api/metrics/local_node")
    assert response.status_code == 200
    assert response.data.decode("utf-8")


# ── nós TCP guardados ────────────────────────────────────────────────────────

def test_saved_targets_record_favorite_and_forget(server):
    login(server)
    config = server["app"].server_config
    config.record_target("192.168.1.50", 4403)
    config.record_target("192.168.1.60", 4403, name="Base [BS1]")

    targets = server["client"].get("/api/targets").get_json()["targets"]
    assert [t["host"] for t in targets] == ["192.168.1.60", "192.168.1.50"]
    assert targets[0]["name"] == "Base [BS1]"

    # Um favorito sobe ao topo mesmo sendo o menos recente.
    r = server["client"].post("/api/targets/favorite",
                              json={"host": "192.168.1.50", "port": 4403, "favorite": True})
    assert [t["host"] for t in r.get_json()["targets"]] == ["192.168.1.50", "192.168.1.60"]

    r = server["client"].post("/api/targets/forget", json={"host": "192.168.1.60", "port": 4403})
    assert [t["host"] for t in r.get_json()["targets"]] == ["192.168.1.50"]


def test_saved_targets_keep_favorites_and_cap_recent(server):
    import server.auth as auth
    config = server["app"].server_config
    config.record_target("10.0.0.1", 4403)
    config.set_target_favorite("10.0.0.1", 4403, True)
    for i in range(auth.MAX_RECENT_TARGETS + 3):
        config.record_target(f"10.0.1.{i}", 4403)
    targets = config.saved_targets
    assert targets[0]["host"] == "10.0.0.1"
    assert len(targets) == auth.MAX_RECENT_TARGETS + 1


def test_saved_targets_endpoints_require_login(server):
    assert server["client"].get("/api/targets").status_code == 401
    assert server["client"].post("/api/targets/forget", json={}).status_code == 401


def test_successful_tcp_connection_is_recorded_with_node_name(server):
    mesh = server["mesh"]
    mesh.target = {"mode": "tcp", "host": "192.168.1.77", "port": 4403, "serial_port": ""}
    mesh._on_connection_changed(True)
    mesh._on_local_node_ready("Estação Norte", "EN1", "!a1b2c3d4", False, False)
    saved = server["app"].server_config.saved_targets
    assert saved[0]["host"] == "192.168.1.77"
    assert saved[0]["name"] == "Estação Norte [EN1]"


def test_serial_connection_is_not_recorded(server):
    mesh = server["mesh"]
    mesh.target = {"mode": "serial", "host": "127.0.0.1", "port": 4403, "serial_port": "/dev/ttyACM0"}
    mesh._on_connection_changed(True)
    assert server["app"].server_config.saved_targets == []


# ── mensagens não lidas ──────────────────────────────────────────────────────

def test_mark_read_survives_the_next_message(server):
    """Lida uma conversa, a mensagem seguinte conta 1 — não volta o total antigo."""
    mesh, broadcasts = server["mesh"], server["broadcasts"]
    for i in range(3):
        mesh._on_message_received(0, PEER_ID, f"msg {i}", {"id": 100 + i})
    assert mesh.messages.unread() == {"ch:0": 3}

    mesh.mark_read("ch:0")
    assert broadcasts[-1] == ("unread", {"unread": {}})

    mesh._on_message_received(0, PEER_ID, "nova", {"id": 200})
    event, payload = broadcasts[-1]
    assert event == "message"
    assert payload["unread"] == {"ch:0": 1}
    assert mesh.snapshot()["messages"]["unread"] == {"ch:0": 1}


def test_software_version_is_not_exposed(server):
    """A versão não aparece na interface nem na API (o whoami é público)."""
    from constants import APP_VERSION
    client = server["client"]
    assert "version" not in client.get("/api/whoami").get_json()
    login(server)
    assert "version" not in client.get("/api/state").get_json()
    for path in ["/api/whoami", "/api/state", "/index.html", "/js/app.js",
                 "/api/metrics/overview", "/api/metrics/local_node"]:
        assert APP_VERSION not in client.get(path).get_data(as_text=True), path


def test_toggle_favorite_broadcasts_the_favorites_list(server):
    mesh, broadcasts = server["mesh"], server["broadcasts"]
    mesh.toggle_favorite(PEER_ID)
    assert ("favorites", {"favorites": [PEER_ID]}) in broadcasts
    mesh.toggle_favorite(PEER_ID)
    assert broadcasts[-1] == ("favorites", {"favorites": []})


# ── configuração do nó ───────────────────────────────────────────────────────

@pytest.fixture
def config_node(server):
    """Sessão com um nó 'pronto' cuja interface usa o Node real da biblioteca."""
    from tests.test_config_writer import FakeIface
    login(server)
    mesh = server["mesh"]
    mesh.connected = True
    mesh.node_ready = True
    mesh._iface = FakeIface()
    return mesh._iface


def test_config_values_need_a_ready_node(server):
    login(server)
    response = server["client"].get("/api/config/values")
    assert response.status_code == 409
    assert response.get_json()["error"]


def test_config_values_and_save(server, config_node):
    client = server["client"]
    values = client.get("/api/config/values").get_json()
    assert values["sections"]["localConfig.device"]["role"] == "CLIENT"
    assert values["channels"][0]["name"] == "LongFast"

    result = client.post("/api/config/save", json={
        "sections": {"localConfig.lora": {"region": "EU_868"}},
        "owner": {"long_name": "Base Norte"},
    }).get_json()
    assert result["saved"] == ["lora"] and result["owner"] is True and not result["errors"]
    # o lock foi libertado no fim
    assert server["mesh"].config_lock.state()["held_by"] is None


def test_config_save_refused_while_another_client_holds_the_lock(server, config_node):
    server["mesh"].config_lock.acquire("outro-browser")
    response = server["client"].post("/api/config/save",
                                     json={"sections": {"localConfig.lora": {"region": "EU_868"}}})
    assert response.status_code == 409
    assert config_node.localNode.sent == []


def test_channels_save_endpoint(server, config_node):
    result = server["client"].post("/api/channels/save", json={"channels": [
        {"index": 0, "role": "PRIMARY", "name": "Rede", "psk": "AQ=="},
    ]}).get_json()
    assert result == {"saved": [0], "errors": []}
    assert config_node.localNode.channels[0].settings.name == "Rede"


def test_config_endpoints_require_login(server):
    client = server["client"]
    assert client.get("/api/config/values").status_code == 401
    assert client.post("/api/config/save", json={}).status_code == 401
    assert client.post("/api/channels/save", json={}).status_code == 401


# ── métricas: cada pacote conta uma vez ──────────────────────────────────────

def _settle(mesh):
    import time
    done = []
    mesh.loop.post(lambda: done.append(True))
    deadline = time.monotonic() + 3
    while not done and time.monotonic() < deadline:
        time.sleep(0.005)


@pytest.mark.parametrize("portnum, decoded, topic", [
    ("TELEMETRY_APP", {"telemetry": {"deviceMetrics": {"batteryLevel": 80, "airUtilTx": 1.5}}}, "receive"),
    ("POSITION_APP", {"position": {"latitudeI": 387000000, "longitudeI": -91000000}}, "receive"),
    ("TEXT_MESSAGE_APP", {"text": "olá"}, "receive"),
    ("NODEINFO_APP", {"user": {"id": PEER_ID, "longName": "Peer One", "shortName": "P1",
                               "hwModel": "HELTEC_V3"}}, "user"),
])
def test_each_packet_is_counted_once_in_the_metrics(wired, portnum, decoded, topic):
    """Antes a sessão também ingeria o pacote via node_updated: 2× (NODEINFO 3×),
    e a taxa de duplicados dava sempre ~50%."""
    from pubsub import pub
    server, iface = wired
    mesh = server["mesh"]
    pkt = packet(portnum, PEER_NUM, decoded=decoded, rx_snr=5.0, id=0x5150)
    pub.sendMessage(f"meshtastic.{topic}" if topic == "receive" else f"meshtastic.receive.{topic}",
                    packet=pkt, interface=iface)
    _settle(mesh)
    assert len(mesh.metrics._packets) == 1
    assert mesh.metrics._count_duplicates() == 0


def test_nodedb_batch_feeds_the_metrics(server):
    """Posições e nomes da NodeDB chegam às métricas sem esperar por pacotes."""
    mesh = server["mesh"]
    mesh._on_nodes_batch([(PEER_NUM, {
        "num": PEER_NUM,
        "user": {"id": PEER_ID, "longName": "Peer One", "shortName": "P1", "hwModel": "HELTEC_V3"},
        "position": {"latitudeI": 387000000, "longitudeI": -91000000},
        "deviceMetrics": {"batteryLevel": 64},
    })])
    m = mesh.metrics
    assert m._node_pos[PEER_ID] == (38.7, -9.1)
    assert m._battery[PEER_ID] == 64 and m._name(PEER_ID) == "P1"
    assert m._packets == []          # não é tráfego: não conta como pacote


# ── protecção do login ───────────────────────────────────────────────────────

def test_login_throttle_locks_after_repeated_failures_and_doubles():
    from server.auth import LoginThrottle
    now = [1000.0]
    th = LoginThrottle(clock=lambda: now[0])
    for _ in range(LoginThrottle.MAX_FAILURES - 1):
        assert th.failure("10.0.0.5") == 0
    assert th.failure("10.0.0.5") == 60                 # 5.ª falha: 1 min
    assert th.retry_after("10.0.0.5") == 60
    assert th.retry_after("10.0.0.6") == 0              # outros endereços não são afectados
    now[0] += 61
    assert th.retry_after("10.0.0.5") == 0
    for _ in range(LoginThrottle.MAX_FAILURES):
        locked = th.failure("10.0.0.5")
    assert locked == 120                                # bloqueio seguinte: o dobro
    th.success("10.0.0.5")
    assert th.retry_after("10.0.0.5") == 0


def test_login_throttle_forgets_old_failures():
    from server.auth import LoginThrottle
    now = [0.0]
    th = LoginThrottle(clock=lambda: now[0])
    for _ in range(LoginThrottle.MAX_FAILURES - 1):
        th.failure("ip")
    now[0] += LoginThrottle.WINDOW_S + 1                # as falhas antigas expiram
    assert th.failure("ip") == 0


def test_login_route_blocks_guessing_even_with_the_right_password(server):
    client = server["client"]
    now = [5000.0]
    server["app"].login_throttle._clock = lambda: now[0]
    for _ in range(4):
        assert client.post("/api/login", json={"password": "errada!!"}).status_code == 401
    blocked = client.post("/api/login", json={"password": "errada!!"})
    assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "60"
    assert "60" in blocked.get_json()["error"]
    # Durante o bloqueio nem a password certa entra (não é sequer verificada).
    assert client.post("/api/login", json={"password": server["password"]}).status_code == 429
    now[0] += 61
    assert login(server).status_code == 200
    assert client.get("/api/state").status_code == 200
