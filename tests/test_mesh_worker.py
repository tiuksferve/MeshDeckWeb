"""
Testes de integração do MeshtasticWorker portado.

Os pacotes entram pelos tópicos pubsub reais da biblioteca meshtastic, por
isso o caminho exercitado é o mesmo de uma ligação verdadeira: handler ->
_emit -> CoreLoop -> slot. O objectivo é provar que o port não alterou o
comportamento de processamento de pacotes.
"""

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.loop import CoreLoop
from core.mesh_worker import MeshtasticWorker
from tests.fake_meshtastic import (
    LOCAL_ID, LOCAL_NUM, FakeInterface, emit_connection_established,
    emit_connection_lost, emit_node_updated, emit_packet, emit_text, emit_user, packet,
)

PEER_NUM = 0x11110001
PEER_ID = "!11110001"


class Recorder:
    """Guarda tudo o que cada sinal emitir, para inspecção nos testes."""

    def __init__(self, worker):
        self.calls = {}
        from core.signals import Signal
        for name, sig in vars(worker).items():
            if isinstance(sig, Signal):
                self.calls[name] = []
                sig.connect(self._make(name))

    def _make(self, name):
        def handler(*args):
            self.calls[name].append(args)
        return handler

    def __getitem__(self, name):
        return self.calls[name]

    def wait(self, name, count=1, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.calls[name]) >= count:
                return self.calls[name]
            time.sleep(0.005)
        raise AssertionError(
            f"sinal {name} não chegou a {count} emissão(ões); "
            f"obtidas {len(self.calls[name])}"
        )

    def quiesce(self, loop, timeout=2.0):
        """Espera que o loop drene tudo o que está pendente."""
        done = []
        loop.post(lambda: done.append(True))
        deadline = time.monotonic() + timeout
        while not done and time.monotonic() < deadline:
            time.sleep(0.005)


@pytest.fixture
def loop():
    lp = CoreLoop(name="worker-test")
    lp.start()
    yield lp
    lp.stop()


@pytest.fixture
def wired(loop):
    """Worker ligado a uma interface falsa, com todos os sinais gravados."""
    iface = FakeInterface(nodes_by_num={
        PEER_NUM: {
            "num": PEER_NUM,
            "user": {"id": PEER_ID, "longName": "Peer One",
                     "shortName": "P1", "hwModel": "HELTEC_V3"},
            "hopsAway": 2,
        },
    })
    worker = MeshtasticWorker(loop=loop)
    rec = Recorder(worker)
    worker._subscribe_pubsub()
    worker.iface = iface
    yield worker, iface, rec
    worker._unsubscribe_pubsub()


# ── ligação estabelecida ─────────────────────────────────────────────────────

def test_connection_established_sequence(wired, loop):
    worker, iface, rec = wired
    emit_connection_established(iface)

    rec.wait("connection_changed")
    rec.wait("my_node_id_ready")
    rec.wait("nodes_batch")
    rec.wait("local_node_ready")
    rec.wait("interface_ready")

    assert rec["connection_changed"][0] == (True,)
    assert rec["reconnect_status"][0] == (0, 0)
    assert rec["my_node_id_ready"][0] == (LOCAL_ID,)
    assert worker.is_connected


def test_my_node_id_arrives_before_the_node_batch(wired, loop):
    """A ordem importa: sem o ID local primeiro, o nó local entra duplicado."""
    worker, iface, rec = wired
    order = []
    worker.my_node_id_ready.connect(lambda _: order.append("id"))
    worker.nodes_batch.connect(lambda _: order.append("batch"))

    emit_connection_established(iface)
    rec.wait("nodes_batch")
    assert order[:2] == ["id", "batch"]


def test_initial_batch_carries_every_node(wired):
    worker, iface, rec = wired
    emit_connection_established(iface)
    batch = rec.wait("nodes_batch")[0][0]
    nums = {num for num, _ in batch}
    assert nums == {LOCAL_NUM, PEER_NUM}


def test_channels_loaded_from_local_node(wired):
    worker, iface, rec = wired
    emit_connection_established(iface)
    channels = rec.wait("channels_updated")[0][0]
    # O canal 0 sem nome mostra-se como "Primary"; o 1 mantém o nome dado.
    assert [(idx, name) for idx, name, _psk in channels] == [(0, "Primary"), (1, "Secundário")]


def test_local_node_metadata(wired):
    worker, iface, rec = wired
    emit_connection_established(iface)
    long_name, short_name, nid, gps_enabled, has_position = rec.wait("local_node_ready")[0]
    assert (long_name, short_name, nid) == ("Base CT7BRA", "BASE", LOCAL_ID)
    assert gps_enabled is True       # gps_mode == 1
    assert has_position is True      # nodesByNum tem latitudeI/longitudeI


def test_connection_lost_schedules_reconnect(wired, loop):
    worker, iface, rec = wired
    emit_connection_established(iface)
    rec.wait("connection_changed")

    emit_connection_lost(iface)
    rec.wait("connection_changed", 2)
    assert rec["connection_changed"][1] == (False,)

    # Primeira tentativa do backoff: 15s.
    status = rec.wait("reconnect_status", 2)
    assert status[1] == (1, 15)
    worker._reconnect_timer.stop()


# ── processamento de pacotes ─────────────────────────────────────────────────

def test_raw_packet_is_emitted_for_metrics(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("POSITION_APP", PEER_NUM, rx_snr=6.5,
                       decoded={"position": {"latitudeI": 385000000,
                                             "longitudeI": -91000000}}), iface)
    raw = rec.wait("raw_packet_received")[0][0]
    assert raw["fromId"] == PEER_ID
    assert raw["shortName"] == "P1"     # enriquecido do NodeDB
    assert raw["hopsAway"] == 2


def test_position_packet_converts_coordinates(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("POSITION_APP", PEER_NUM, rx_snr=6.5,
                       decoded={"position": {"latitudeI": 385000000,
                                             "longitudeI": -91000000,
                                             "altitude": 120}}), iface)
    nid, updates, _pkt = rec.wait("node_updated")[0]
    assert nid == PEER_ID
    assert updates["latitude"] == pytest.approx(38.5)
    assert updates["longitude"] == pytest.approx(-9.1)
    assert updates["altitude"] == 120
    assert updates["snr"] == 6.5
    assert updates["hops_away"] == 2
    assert updates["last_packet_type"] == "POSITION_APP"


def test_telemetry_device_metrics(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("TELEMETRY_APP", PEER_NUM, decoded={"telemetry": {
        "deviceMetrics": {"batteryLevel": 77, "channelUtilization": 12.5,
                          "airUtilTx": 3.25, "voltage": 4.01,
                          "uptimeSeconds": 98765},
    }}), iface)
    _nid, updates, _ = rec.wait("node_updated")[0]
    assert updates["battery_level"] == 77
    assert updates["channel_utilization"] == pytest.approx(12.5)
    assert updates["air_util_tx"] == pytest.approx(3.25)
    assert updates["voltage"] == pytest.approx(4.01)
    assert updates["uptime_seconds"] == 98765


def test_telemetry_environment_and_power_metrics(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("TELEMETRY_APP", PEER_NUM, decoded={"telemetry": {
        "environmentMetrics": {"temperature": 21.5, "relativeHumidity": 55, "iaq": 40},
        "powerMetrics": {"ch1Voltage": 3.3, "ch1Current": 120},
        "healthMetrics": {"heartBpm": 70, "spO2": 98},
    }}), iface)
    _nid, updates, _ = rec.wait("node_updated")[0]
    assert updates["env_temperature"] == pytest.approx(21.5)
    assert updates["env_relativeHumidity"] == 55
    assert updates["env_iaq"] == 40
    assert updates["pwr_ch1Voltage"] == pytest.approx(3.3)
    assert updates["health_heartBpm"] == 70
    assert updates["health_spO2"] == 98


def test_nodeinfo_extracts_public_key_for_pki(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("NODEINFO_APP", PEER_NUM, decoded={"user": {
        "longName": "Peer One", "shortName": "P1", "hwModel": "HELTEC_V3",
        "publicKey": b"\x01\x02\x03",
    }}), iface)
    _nid, updates, _ = rec.wait("node_updated")[0]
    assert updates["public_key"] == "AQID"      # base64
    assert updates["long_name"] == "Peer One"


def test_receive_user_topic_also_extracts_public_key(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_user(packet("NODEINFO_APP", PEER_NUM, rx_snr=4.0, decoded={"user": {
        "longName": "Peer One", "shortName": "P1", "publicKey": b"\xaa",
    }}), iface)
    _nid, updates, _ = rec.wait("node_updated")[0]
    assert updates["public_key"] == "qg=="
    assert updates["last_packet_type"] == "NODEINFO_APP"
    assert isinstance(updates["last_heard"], datetime)


def test_loopback_from_local_node_is_ignored(wired):
    """Telemetria do próprio nó volta pelo daemon e não deve criar uma linha."""
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("TELEMETRY_APP", LOCAL_NUM,
                       decoded={"telemetry": {"deviceMetrics": {"batteryLevel": 50}}}), iface)
    rec.wait("raw_packet_received")      # as métricas recebem-no
    rec.quiesce(worker.loop)
    assert rec["node_updated"] == []     # a tabela de nós não


def test_routing_app_ack(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("ROUTING_APP", PEER_NUM, decoded={
        "routing": {"errorReason": "NONE"}, "requestId": 0x1234,
    }), iface)
    req_id, status, reason = rec.wait("message_status_updated")[0]
    assert (req_id, status, reason) == (0x1234, "ack", "NONE")


def test_routing_app_nak_carries_reason(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("ROUTING_APP", PEER_NUM, decoded={
        "routing": {"errorReason": "NO_ROUTE"}, "requestId": 0x99,
    }), iface)
    req_id, status, reason = rec.wait("message_status_updated")[0]
    assert (req_id, status, reason) == (0x99, "nak", "NO_ROUTE")


def test_routing_from_local_node_is_an_implicit_ack(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("ROUTING_APP", LOCAL_NUM, decoded={
        "routing": {"errorReason": "NONE"}, "requestId": 0x55,
    }), iface)
    req_id, status, _ = rec.wait("message_status_updated")[0]
    assert (req_id, status) == (0x55, "ack_implicit")


def test_traceroute_result_edges_and_snr(wired):
    """SNR vem em quartos de dB no protocolo; o worker divide por 4."""
    worker, iface, rec = wired
    worker._connected = True
    mid = 0x22220002
    iface.nodesByNum[mid] = {"num": mid, "user": {"id": "!22220002"}}
    emit_packet(packet("TRACEROUTE_APP", PEER_NUM, to=LOCAL_NUM, decoded={
        "traceroute": {"route": [mid], "snrTowards": [20, 16],
                       "routeBack": [mid], "snrBack": [12, 8]},
    }), iface)
    forward, back, origin, dest = rec.wait("traceroute_result")[0]
    assert origin == LOCAL_ID and dest == PEER_ID
    assert forward == [(LOCAL_ID, "!22220002", 5.0), ("!22220002", PEER_ID, 4.0)]
    assert back == [(PEER_ID, "!22220002", 3.0), ("!22220002", LOCAL_ID, 2.0)]


def test_traceroute_request_is_not_treated_as_a_result(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("TRACEROUTE_APP", PEER_NUM, to=LOCAL_NUM, wantResponse=True,
                       decoded={"traceroute": {"route": []}}), iface)
    rec.quiesce(worker.loop)
    assert rec["traceroute_result"] == []


def test_neighbor_info_realtime(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("NEIGHBORINFO_APP", PEER_NUM, decoded={
        "neighborinfo": {"neighbors": [{"nodeId": LOCAL_NUM, "snr": 7.5},
                                       {"nodeId": 0x33330003, "snr": -2.0}]},
    }), iface)
    from_id, neighbors = rec.wait("neighbor_info_received")[0]
    assert from_id == PEER_ID
    assert neighbors == [(LOCAL_ID, 7.5), ("!33330003", -2.0)]


def test_neighbor_info_accepts_camel_case_key(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_packet(packet("NEIGHBORINFO_APP", PEER_NUM, decoded={
        "neighborInfo": {"neighbors": [{"node_id": LOCAL_NUM, "snr": 1.0}]},
    }), iface)
    _from_id, neighbors = rec.wait("neighbor_info_received")[0]
    assert neighbors == [(LOCAL_ID, 1.0)]


def test_initial_neighbor_info_from_nodedb(wired):
    worker, iface, rec = wired
    iface.nodesByNum[PEER_NUM]["neighborInfo"] = {
        "neighbors": [{"nodeId": LOCAL_NUM, "snr": 3.0}]
    }
    emit_connection_established(iface)
    from_id, neighbors = rec.wait("neighbor_info_received")[0]
    assert from_id == PEER_ID and neighbors == [(LOCAL_ID, 3.0)]


# ── mensagens de texto ───────────────────────────────────────────────────────

def test_text_message_broadcast(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, channel=0,
                     decoded={"text": "olá rede"}), iface)
    channel, from_id, text, pkt = rec.wait("message_received")[0]
    assert (channel, from_id, text) == (0, PEER_ID, "olá rede")
    assert pkt["hopsAway"] == 2
    assert pkt["rxTime"] > 0            # preenchido quando o pacote não o traz


def test_text_message_direct_resolves_destination(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, to=LOCAL_NUM,
                     decoded={"text": "só para ti"}), iface)
    _channel, _from_id, _text, pkt = rec.wait("message_received")[0]
    assert pkt["toId"] == LOCAL_ID
    assert pkt["to"] == LOCAL_NUM


def test_pki_message_records_public_key(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, to=LOCAL_NUM,
                     pkiEncrypted=True, publicKey=b"\x07\x08",
                     decoded={"text": "cifrado"}), iface)
    rec.wait("message_received")
    rec.quiesce(worker.loop)
    # pubsub propaga receive.text para receive, por isso há duas actualizações
    # do nó: a genérica do pacote e a da chave pública. Qualquer ordem serve.
    keys = [u.get("public_key") for _nid, u, _p in rec["node_updated"]]
    assert "Bwg=" in keys
    # A chave também fica na entrada do NodeDB, para o DM PKI seguinte.
    assert iface.nodesByNum[PEER_NUM]["user"]["publicKey"] == "Bwg="


def test_empty_text_is_dropped(wired):
    worker, iface, rec = wired
    worker._connected = True
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, decoded={"text": ""}), iface)
    rec.quiesce(worker.loop)
    assert rec["message_received"] == []


# ── envio ────────────────────────────────────────────────────────────────────

def test_send_channel_message(wired):
    worker, iface, rec = wired
    worker._connected = True
    worker.send_message(2, "teste")
    ch, text, pid = rec.wait("channel_message_sent")[0]
    assert (ch, text) == (2, "teste") and pid > 0
    assert iface.sent_text[0]["channelIndex"] == 2
    assert iface.sent_text[0]["wantAck"] is True


def test_send_message_without_connection_reports_error(wired):
    worker, iface, rec = wired
    worker._connected = False
    worker.send_message(0, "teste")
    rec.wait("error_occurred")
    assert iface.sent_text == []


def test_direct_message_uses_pki_when_public_key_known(wired):
    worker, iface, rec = wired
    worker._connected = True
    iface.nodesByNum[PEER_NUM]["user"]["publicKey"] = "AQID"
    worker.send_direct_message(PEER_ID, "privado")
    dest, text, pki, pid = rec.wait("dm_sent")[0]
    assert (dest, text, pki) == (PEER_ID, "privado", True) and pid > 0
    assert iface.sent_data[0]["pkiEncrypted"] is True


def test_direct_message_falls_back_to_psk_without_public_key(wired):
    worker, iface, rec = wired
    worker._connected = True
    worker.send_direct_message(PEER_ID, "privado")
    dest, text, pki, _pid = rec.wait("dm_sent")[0]
    assert (dest, text, pki) == (PEER_ID, "privado", False)
    assert iface.sent_text[0]["destinationId"] == PEER_ID
    assert iface.sent_data == []


def test_traceroute_sent_on_channel_zero(wired):
    worker, iface, rec = wired
    worker._connected = True
    worker.send_traceroute(PEER_ID, hop_limit=5)
    rec.quiesce(worker.loop)
    assert iface.sent_data[0]["destinationId"] == PEER_ID
    assert iface.sent_data[0]["hopLimit"] == 5
    assert iface.sent_data[0]["wantResponse"] is True


def test_reset_nodedb_prefers_the_library_call(wired):
    worker, iface, rec = wired
    worker._connected = True
    worker.reset_nodedb()
    rec.wait("nodedb_reset")
    assert iface.localNode.reset_nodedb_calls == 1


def test_send_node_info_broadcasts_a_user_packet(wired):
    """Tem de ser um pacote User manual: setOwner reiniciaria o nó."""
    worker, iface, rec = wired
    worker._connected = True
    worker.send_node_info()
    rec.quiesce(worker.loop)
    sent = iface.sent_data[0]
    assert sent["destinationId"] == 0xFFFFFFFF
    assert sent["payload"].long_name == "Base CT7BRA"
    assert sent["payload"].id == LOCAL_ID
    assert iface.localNode.admin_messages == []


def test_send_position_broadcasts_the_known_position(wired):
    """Um pacote POSITION_APP em broadcast, sem mexer na configuração do nó."""
    worker, iface, rec = wired
    worker._connected = True
    worker.send_position()
    ok, msg = rec.wait("position_sent")[0]
    assert ok is True and "38.500000" in msg
    sent = iface.sent_data[0]
    assert sent["destinationId"] == 0xFFFFFFFF
    assert sent["payload"].latitude_i == 385000000
    assert sent["payload"].longitude_i == -91000000
    assert iface.localNode.admin_messages == []


def test_send_position_reports_failure_without_coordinates(wired):
    worker, iface, rec = wired
    worker._connected = True
    iface.nodesByNum[LOCAL_NUM]["position"] = {}
    iface.localNode.localConfig.position.fixed_lat = 0.0
    iface.localNode.localConfig.position.fixed_lon = 0.0
    worker.send_position()
    ok, msg = rec.wait("position_sent")[0]
    assert ok is False and msg


def test_refresh_local_position_from_daemon_cache(wired):
    worker, iface, rec = wired
    worker._connected = True
    worker.refresh_local_position()
    lat, lon, alt, found = rec.wait("local_position_updated")[0]
    assert found is True
    assert lat == pytest.approx(38.5) and lon == pytest.approx(-9.1)
    assert alt == 95


def test_refresh_local_position_falls_back_to_fixed_config(wired):
    worker, iface, rec = wired
    worker._connected = True
    iface.nodesByNum[LOCAL_NUM]["position"] = {}
    iface.localNode.localConfig.position.fixed_lat = 40.25
    iface.localNode.localConfig.position.fixed_lon = -8.5
    iface.localNode.localConfig.position.fixed_alt = 200
    worker.refresh_local_position()
    lat, lon, alt, found = rec.wait("local_position_updated")[0]
    assert found is True
    assert lat == pytest.approx(40.25) and lon == pytest.approx(-8.5) and alt == 200


def test_refresh_local_position_reports_not_found(wired):
    worker, iface, rec = wired
    worker._connected = True
    iface.nodesByNum[LOCAL_NUM]["position"] = {}
    iface.localNode.localConfig.position.fixed_lat = 0.0
    iface.localNode.localConfig.position.fixed_lon = 0.0
    worker.refresh_local_position()
    _lat, _lon, _alt, found = rec.wait("local_position_updated")[0]
    assert found is False


# ── afinidade de thread ──────────────────────────────────────────────────────

def test_signals_are_delivered_on_the_loop_thread(wired):
    """A garantia central do port: nenhum slot corre na thread do leitor TCP."""
    worker, iface, rec = wired
    worker._connected = True
    threads = []
    worker.node_updated.connect(
        lambda *a: threads.append(__import__("threading").current_thread().name))

    for i in range(20):
        emit_packet(packet("POSITION_APP", PEER_NUM, decoded={
            "position": {"latitudeI": 385000000 + i, "longitudeI": -91000000}}), iface)

    rec.wait("node_updated", 20)
    assert set(threads) == {"worker-test"}


def test_commands_from_another_thread_run_on_the_loop(wired):
    import threading
    worker, iface, rec = wired
    worker._connected = True
    seen = []
    worker.channel_message_sent.connect(
        lambda *a: seen.append(threading.current_thread().name))

    t = threading.Thread(target=lambda: worker.send_message(0, "de outra thread"))
    t.start()
    t.join()

    rec.wait("channel_message_sent")
    assert seen == ["worker-test"]


def test_node_updated_topic_skips_the_local_node(wired):
    """O nó local é gerido por local_node_ready, não pela tabela."""
    worker, iface, rec = wired
    worker._local_id_known = LOCAL_ID
    worker._local_num_known = LOCAL_NUM
    emit_node_updated(iface.nodesByNum[LOCAL_NUM], iface)
    emit_node_updated(iface.nodesByNum[PEER_NUM], iface)
    rec.wait("node_updated")
    rec.quiesce(worker.loop)
    assert [nid for nid, _u, _p in rec["node_updated"]] == [PEER_ID]


def test_tcp_port_is_passed_as_a_keyword(loop, monkeypatch):
    """TCPInterface(hostname, port) punha o porto em debugOut.

    A assinatura é TCPInterface(hostname, debugOut, noProto, connectNow,
    portNumber, ...), por isso o segundo posicional nunca era o porto e a
    ligação ia sempre para a 4403, ignorando a escolha do utilizador.
    """
    import core.mesh_worker as mw

    captured = {}

    class FakeTCP:
        def __init__(self, hostname, debugOut=None, noProto=False,
                     connectNow=True, portNumber=4403, **kwargs):
            captured.update(hostname=hostname, debugOut=debugOut,
                            portNumber=portNumber)

    monkeypatch.setattr(mw, "TCPInterface", FakeTCP)
    worker = MeshtasticWorker(hostname="10.0.0.9", port=4404, loop=loop)
    worker._connect_thread()

    assert captured["hostname"] == "10.0.0.9"
    assert captured["portNumber"] == 4404
    assert captured["debugOut"] is None


# ── nomes de nó com texto malformado (bug real de produção) ─────────────────
# Um nó real na rede enviou um shortName com bytes UTF-8 inválidos; a
# biblioteca meshtastic decodificou-os de forma permissiva e o resultado
# ficou com um surrogate Unicode sem par — válido como str do Python, mas
# que nenhuma codificação UTF-8 estrita (JSON em texto simples, HTML, um novo
# pacote protobuf) consegue serializar. Isto rebentava em "/api/metrics/
# <secção>" como 500 Internal Server Error, longe da origem do problema.

def test_clean_text_strips_lone_surrogates():
    from core.mesh_worker import _clean_text
    dirty = "Bo\udcc3Nome"          # surrogate solto, como a lib decodifica bytes inválidos
    cleaned = _clean_text(dirty)
    assert cleaned.encode("utf-8")   # não pode levantar UnicodeEncodeError
    assert "\udcc3" not in cleaned
    assert cleaned.startswith("Bo") and cleaned.endswith("Nome")


def test_clean_text_leaves_normal_text_untouched():
    from core.mesh_worker import _clean_text
    assert _clean_text("TKSC ⭐") == "TKSC ⭐"
    assert _clean_text("") == ""
    assert _clean_text(None) is None


def test_nodeinfo_with_malformed_name_is_sanitised(wired):
    worker, iface, rec = wired
    worker._connected = True
    dirty = "Bo\udcc3Nome"
    emit_packet(packet("NODEINFO_APP", PEER_NUM, decoded={"user": {
        "longName": dirty, "shortName": dirty, "hwModel": "TBEAM",
    }}), iface)
    _nid, updates, _ = rec.wait("node_updated")[0]
    assert updates["long_name"].encode("utf-8")
    assert updates["short_name"].encode("utf-8")
    assert "\udcc3" not in updates["long_name"]


def test_receive_user_topic_also_sanitises_the_name(wired):
    worker, iface, rec = wired
    worker._connected = True
    dirty = "Bo\udcc3Nome"
    emit_user(packet("NODEINFO_APP", PEER_NUM, decoded={"user": {
        "longName": dirty, "shortName": dirty,
    }}), iface)
    _nid, updates, _ = rec.wait("node_updated")[0]
    assert updates["long_name"].encode("utf-8")
    assert updates["short_name"].encode("utf-8")
