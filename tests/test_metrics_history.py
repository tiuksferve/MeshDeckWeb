"""
Testes do histórico das métricas (core/metrics_history.py e a ligação na
sessão): o que se grava, a reposição depois de reiniciar, a retenção de 24 h,
as protecções de disco e a janela dos gráficos (1 h / 6 h / 24 h).
"""

import json
import time

from core import metrics_history as mh
from core.metrics import SECTION_KEYS, MeshMetrics
from core.metrics_data import downsample, trim_series, use_window
from core.settings import Settings
from server.app import create_app
from server.auth import ServerConfig
from tests.fake_meshtastic import LOCAL_ID, packet

PEER = 0x11110001


def sample_packets(now: float) -> list:
    """Um pouco de tudo o que as métricas contam, com a hora de cada pacote."""
    out = []
    for i in range(40):
        p = packet("POSITION_APP", PEER + i % 5, rx_snr=4.0 - i % 7, rxRssi=-90 - i % 9,
                   hopStart=3, hopLimit=3 - i % 3, id=1000 + i, shortName=f"N{i % 5}",
                   relayNode=0x0B if i % 3 else None,
                   decoded={"position": {"latitudeI": 387000000 + i, "longitudeI": -91000000},
                            "payload": b"\x01\x02"})
        out.append((now - 3000 + i * 60, p))
    out.append((now - 900, packet("TELEMETRY_APP", PEER, id=5000, decoded={"telemetry": {
        "deviceMetrics": {"batteryLevel": 80, "voltage": 4.0, "channelUtilization": 12.5,
                          "airUtilTx": 2.5, "uptimeSeconds": 3600},
        "environmentMetrics": {"temperature": 18.5, "relativeHumidity": 60},
        "raw": object()}})))
    out.append((now - 800, packet("TELEMETRY_APP", int(LOCAL_ID[1:], 16), id=5001, decoded={
        "telemetry": {"localStats": {"uptimeSeconds": 7200, "numPacketsRx": 500,
                                     "numPacketsTx": 90, "noiseFloor": -110,
                                     "channelUtilization": 15.0, "airUtilTx": 3.0}}})))
    out.append((now - 700, packet("NEIGHBORINFO_APP", PEER + 1, id=5002, decoded={
        "neighborinfo": {"neighbors": [{"nodeId": PEER + 2, "snr": 6.5}]}})))
    out.append((now - 600, packet("ROUTING_APP", PEER + 2, id=5003, decoded={
        "requestId": 77, "routing": {"errorReason": "NO_RESPONSE"}})))
    out.append((now - 500, packet("NODEINFO_APP", PEER + 3, id=5004, decoded={
        "user": {"hwModel": "HELTEC_V3", "longName": "Name"}})))
    out.append((now - 400, packet("TEXT_MESSAGE_APP", PEER + 4, id=5005, decoded={
        "text": "segredo", "payload": b"segredo"})))
    return out


def comparable(metrics: MeshMetrics) -> dict:
    """Os dados de todas as secções, sem os campos que dependem do relógio."""
    out = {}
    for key in SECTION_KEYS:
        data = json.loads(json.dumps(metrics.data_for(key), default=str))
        for field in ("now", "uptime_ts"):
            data.pop(field, None)
        out[key] = data
    return out


# ── o que se grava ─────────────────────────────────────────────────────────

def test_distilled_packet_has_no_text_payload_or_protobuf():
    now = time.time()
    for _ts, p in sample_packets(now):
        text = json.dumps(mh.distill_packet(p))
        assert "segredo" not in text
        assert "payload" not in text
        assert "raw" not in text


def test_distilled_packets_give_the_same_metrics_as_the_originals():
    """A reposição só é fiel se a destilação guarda tudo o que a ingestão lê."""
    now = time.time()
    full, distilled = MeshMetrics(), MeshMetrics()
    for m in (full, distilled):
        m.set_local_node_id(LOCAL_ID)
        m._start_time = now - 4000
    for ts, p in sample_packets(now):
        full.ingest_raw_packet(p, ts)
        distilled.ingest_raw_packet(json.loads(json.dumps(mh.distill_packet(p))), ts)
    assert comparable(full) == comparable(distilled)


# ── ficheiro ───────────────────────────────────────────────────────────────

def test_history_keeps_order_and_only_the_last_24_hours(tmp_path):
    now = [1_000_000.0]
    history = mh.MetricsHistory(str(tmp_path), "!a1b2c3d4", clock=lambda: now[0])
    assert history.path.endswith("a1b2c3d4.sqlite")
    history.record(mh.SENT, now[0] - 90_000, {"id": 1, "kind": "dm"})     # mais de 24 h
    history.record(mh.SENT, now[0] - 10, {"id": 2, "kind": "dm"})
    history.record(mh.STATUS, now[0] - 5, {"id": 2, "status": "ack"})
    history.flush()
    assert [(kind, data["id"]) for _ts, kind, data in history.events()] == [
        (mh.SENT, 2), (mh.STATUS, 2)]
    history.close()

    reopened = mh.MetricsHistory(str(tmp_path), "!a1b2c3d4", clock=lambda: now[0])
    assert len(list(reopened.events())) == 2
    reopened.clear()
    assert list(reopened.events()) == []
    reopened.close()


def test_history_stops_writing_when_the_disk_is_almost_full(tmp_path, monkeypatch):
    history = mh.MetricsHistory(str(tmp_path), "!a1b2c3d4")
    monkeypatch.setattr(mh.shutil, "disk_usage",
                        lambda _path: type("U", (), {"free": mh.MIN_FREE_BYTES - 1})())
    history.record(mh.SENT, time.time(), {"id": 1, "kind": "dm"})
    history.flush()
    assert list(history.events()) == []
    history.close()


def test_history_drops_the_oldest_events_above_the_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(mh, "LIMIT_BYTES", 64 * 1024)
    history = mh.MetricsHistory(str(tmp_path), "!a1b2c3d4")
    now = time.time()
    for i in range(3000):
        history.record(mh.PACKET, now - 3000 + i, {"id": i, "pad": "x" * 60})
    history.flush()
    events = list(history.events())
    assert 0 < len(events) < 3000
    assert events[-1][2]["id"] == 2999          # ficam os mais recentes
    assert history._used_bytes() <= mh.LIMIT_BYTES
    history.close()


def test_node_file_name_never_leaves_the_directory(tmp_path):
    history = mh.MetricsHistory(str(tmp_path), "../../etc/!x")
    assert history.path.startswith(str(tmp_path))
    history.close()


# ── sessão: gravar, reiniciar, repor ───────────────────────────────────────

def make_server(tmp_path, monkeypatch):
    monkeypatch.setattr("core.settings._shared", Settings(directory=str(tmp_path)))
    config = ServerConfig(Settings(filename="server.json", directory=str(tmp_path)))
    _app, _socketio, mesh = create_app(config)
    mesh._broadcast = lambda *_args: None
    return mesh


def wait_for_loop(mesh):
    """Espera que a thread do loop esvazie o que já tem na fila."""
    done = []
    mesh.loop.post(lambda: done.append(True))
    deadline = time.time() + 10
    while not done and time.time() < deadline:
        time.sleep(0.01)
    assert done


def test_metrics_survive_a_server_restart(tmp_path, monkeypatch):
    mesh = make_server(tmp_path, monkeypatch)
    now = time.time()
    # Eventos antes de se saber o nó local também ficam no histórico.
    mesh._on_raw_packet(packet("POSITION_APP", PEER, rx_snr=3.0, id=1))
    mesh._on_my_node_id(LOCAL_ID)
    for _ts, p in sample_packets(now):
        mesh._on_raw_packet(p)
    mesh._on_dm_sent("!11110002", "olá", False, 4242)
    mesh._on_message_status(4242, "ack", "")
    mesh._on_neighbor_info(LOCAL_ID, [("!11110003", 4.5)])
    before = comparable(mesh.metrics)
    packets_before = len(mesh.metrics._packets)
    mesh.shutdown()                          # grava o que estava pendente

    again = make_server(tmp_path, monkeypatch)
    wait_for_loop(again)                     # a reposição corre na thread do loop
    assert again.metrics._local_nid == LOCAL_ID
    assert len(again.metrics._packets) == packets_before
    after = comparable(again.metrics)
    for key in ("traffic", "nodes", "environment", "neighbors", "reliability", "rf"):
        assert after[key] == before[key], key
    again.shutdown()


def test_another_node_gets_its_own_history(tmp_path, monkeypatch):
    mesh = make_server(tmp_path, monkeypatch)
    mesh._on_my_node_id(LOCAL_ID)
    mesh._on_raw_packet(packet("POSITION_APP", PEER, rx_snr=3.0, id=1))
    mesh._on_my_node_id("!99990000")         # ligado a outro nó
    assert mesh.metrics._packets == []
    mesh._on_my_node_id(LOCAL_ID)            # e de volta: o histórico regressa
    assert len(mesh.metrics._packets) == 1
    mesh.shutdown()


def test_clearing_forgets_the_history_but_not_the_local_node(tmp_path, monkeypatch):
    mesh = make_server(tmp_path, monkeypatch)
    mesh._on_my_node_id(LOCAL_ID)
    mesh._on_raw_packet(packet("POSITION_APP", PEER, rx_snr=3.0, id=1))
    mesh._flush_history()
    mesh.clear_metrics()
    assert mesh.metrics._local_nid == LOCAL_ID
    assert list(mesh.history.events()) == []
    mesh.shutdown()


# ── janela dos gráficos ────────────────────────────────────────────────────

def test_traffic_bins_follow_the_chosen_window():
    m = MeshMetrics()
    now = time.time()
    for i in reversed(range(100)):           # por ordem de chegada, de 10 em 10 min
        m.ingest_raw_packet(packet("POSITION_APP", PEER, id=i), now - i * 600)
    sizes = {}
    for window in (3600, 21600, 86400):
        with use_window(window):
            data = m.data_for("traffic")
        sizes[window] = (len(data["ppm_vals"]), sum(data["ppm_vals"]))
    assert sizes[3600] == (60, 6)
    assert sizes[21600] == (72, 36)
    assert sizes[86400] == (96, 100)
    with use_window("99"):                   # valor desconhecido: a janela por omissão
        assert len(m.data_for("traffic")["ppm_vals"]) == 72


def test_series_are_kept_for_24_hours_and_shown_per_window():
    m = MeshMetrics()
    now = time.time()
    start = now - 30 * 3600
    for i in range(30 * 60):                 # 30 h, um pacote por minuto
        m.ingest_raw_packet(packet("POSITION_APP", PEER + i % 3, id=i), start + i * 60)
    assert m._packets[0][0] >= now - 24 * 3600 - 60
    with use_window(3600):
        short = m.data_for("nodes")["ts_vals"]
    with use_window(86400):
        long = m.data_for("nodes")["ts_vals"]
    assert 50 <= len(short) <= 61
    assert len(long) <= 240 < 24 * 60


def test_trim_and_downsample_helpers():
    series = [(float(t), t) for t in range(100)]
    trim_series(series, now=100.0, keep_s=10)
    assert series[0][0] == 90.0
    points = [(float(t), 1.0) for t in range(1000)]
    out = downsample(points, 0.0, 1000.0, max_points=100)
    assert len(out) == 100
    assert all(v == 1.0 for _t, v in out)
