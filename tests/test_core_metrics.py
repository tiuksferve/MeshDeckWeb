"""
Testes do MeshMetrics — as 12 secções a funcionar sem widget Qt.

O que se verifica é a extracção para core/: as secções renderizam, ingerem
pacotes reais e produzem dados coerentes fora de qualquer interface.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.metrics import SECTION_KEYS, SECTIONS, WAITING_SECTIONS, MeshMetrics
from tests.fake_meshtastic import LOCAL_ID, LOCAL_NUM, packet

PEER_NUM = 0x11110001
PEER_ID = "!11110001"


@pytest.fixture
def metrics():
    m = MeshMetrics()
    m.set_local_node_id(LOCAL_ID)
    return m


def test_core_metrics_imports_without_qt():
    """Num subprocesso limpo: o núcleo nunca pode depender de PyQt."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import core.metrics;"
        "leaked=[m for m in sys.modules if m.startswith('PyQt')];"
        "assert not leaked, leaked;"
        "print('ok')" % root
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


def test_sections_are_unique_and_complete():
    assert len(SECTIONS) == 12
    assert len(set(SECTION_KEYS)) == 12
    assert WAITING_SECTIONS <= set(SECTION_KEYS)


@pytest.mark.parametrize("key", SECTION_KEYS)
def test_every_section_renders_when_empty(metrics, key):
    html = metrics.html_for(key)
    assert html.startswith("<!DOCTYPE html>")
    assert "Chart.js" in html or "chart.umd" in html or "<h2>" in html


@pytest.mark.parametrize("key", SECTION_KEYS)
def test_every_section_data_is_json_serialisable(metrics, key):
    json.dumps(metrics.data_for(key))


def test_unknown_section_raises(metrics):
    with pytest.raises(KeyError):
        metrics.html_for("nao_existe")
    assert metrics.data_for("nao_existe") == {}


def test_ingesting_packets_fills_the_sections(metrics):
    now = int(time.time())
    for i in range(12):
        metrics.ingest_raw_packet(packet(
            "POSITION_APP", PEER_NUM, rx_snr=5.0 + i, rx_time=now - i,
            hopsAway=1, shortName="P1",
            decoded={"position": {"latitudeI": 385000000, "longitudeI": -91000000}}))

    overview = metrics.data_for("overview")
    assert overview["total_pkts"] >= 12

    rf = metrics.data_for("rf")
    assert rf["snr_avg"] is not None
    assert rf["n"] >= 12

    traffic = metrics.data_for("traffic")
    assert traffic["values"]          # contagem por tipo de pacote
    assert traffic["n_direct"] + traffic["n_1hop"] + traffic["n_multi"] >= 1

    assert metrics.has_data("rf")
    assert metrics.has_data("traffic")


def test_waiting_sections_start_without_data(metrics):
    for key in WAITING_SECTIONS:
        assert metrics.has_data(key) is False, key


def test_telemetry_feeds_battery_and_channel(metrics):
    metrics.ingest_raw_packet(packet("TELEMETRY_APP", PEER_NUM, decoded={
        "telemetry": {"deviceMetrics": {"batteryLevel": 55, "voltage": 3.9,
                                        "channelUtilization": 18.0,
                                        "airUtilTx": 2.0}}}))
    assert metrics.has_data("nodes")
    assert metrics.has_data("channel")
    nodes = metrics.data_for("nodes")
    assert nodes["batt_rows"]
    assert nodes["batt_avg"] is not None


def test_neighbor_info_feeds_the_neighbourhood(metrics):
    metrics.ingest_neighbor_info(PEER_ID, [(LOCAL_ID, 6.5)])
    assert metrics.has_data("neighbors")
    data = metrics.data_for("neighbors")
    assert data["rows"] and data["n_links"] >= 1


def test_message_ack_feeds_latency_and_reliability(metrics):
    metrics.ingest_message_sent(0x1234)
    # O RTT só é registado se for maior que zero: um ACK instantâneo arredonda
    # a 0.00 e é descartado, que é o correcto para tráfego real.
    time.sleep(0.02)
    metrics.ingest_message_status(0x1234, "ack")
    reliability = metrics.data_for("reliability")
    assert reliability["acked"] >= 1
    assert metrics.data_for("latency")["n"] >= 1


def test_clear_resets_everything(metrics):
    metrics.ingest_raw_packet(packet("POSITION_APP", PEER_NUM, decoded={}))
    assert metrics.data_for("overview")["total_pkts"] >= 1
    metrics.clear()
    assert metrics.data_for("overview")["total_pkts"] == 0


# ── correcções da revisão de métricas ────────────────────────────────────────

def telemetry(air_tx, ch_util=10.0, from_num=PEER_NUM, **extra):
    return packet("TELEMETRY_APP", from_num, decoded={"telemetry": {"deviceMetrics": {
        "airUtilTx": air_tx, "channelUtilization": ch_util, "batteryLevel": 80}}}, **extra)


def test_duty_cycle_is_air_util_tx_without_scaling(metrics):
    """O protobuf define airUtilTx como a % de TX na última hora: já é o
    duty cycle horário. Antes era multiplicado por 6."""
    metrics.ingest_raw_packet(telemetry(2.0))
    metrics.ingest_raw_packet(telemetry(3.5, from_num=LOCAL_NUM))
    channel = metrics.data_for("channel")
    assert channel["worst_dc"] == 3.5
    assert {row[0]: row[4] for row in channel["rows"]}[PEER_ID] == 2.0
    assert metrics.data_for("local_node")["dc_est"] == 3.5


def test_channel_table_has_the_same_columns_as_its_header(metrics):
    import re
    metrics.ingest_raw_packet(telemetry(2.0))
    page = metrics.html_for("channel")
    table = page[page.index('<table>'):page.index('</table>', page.index('ch-node-tbody'))]
    header_cols = len(re.findall(r"<th>", table.split("</tr>")[0]))
    first_row = table[table.index('<tbody'):].split("</tr>")[0]
    assert len(re.findall(r"<td>", first_row)) == header_cols == 5
    assert 'id="air-avg-val"' in page      # o KPI que o JS actualiza


def test_node_data_without_packet_reaches_the_metrics(metrics):
    """Lote da NodeDB e leitura do nó local: antes eram descartados."""
    metrics.ingest_packet(None, {"battery_level": 55, "voltage": 3.9, "hw_model": "",
                                 "short_name": "P1"}, PEER_ID)
    metrics.ingest_packet(None, {"battery_level": 101, "hw_model": "TBEAM"}, LOCAL_ID)
    assert metrics._battery[PEER_ID] == 55
    assert metrics._name(PEER_ID) == "P1"
    assert PEER_ID not in metrics._hw_model           # vazio = desconhecido
    local = metrics.data_for("local_node")
    assert local["battery"] == 101 and local["hw_model"] == "TBEAM"


def test_channel_messages_do_not_stay_pending_or_count_for_delivery(metrics):
    metrics.ingest_message_sent(1, kind="channel")
    metrics.ingest_message_status(1, "ack_implicit")      # alguém retransmitiu
    metrics.ingest_message_sent(2, kind="dm")
    metrics.ingest_message_status(2, "ack_implicit")
    metrics.ingest_message_status(2, "ack")               # e depois o destino confirmou
    metrics.ingest_message_sent(3, kind="dm")
    metrics.ingest_message_status(3, "nak")
    metrics.ingest_message_sent(4, kind="dm")             # sem resposta ainda

    data = metrics.data_for("reliability")
    assert (data["sent"], data["sent_dm"], data["sent_channel"]) == (4, 3, 1)
    assert (data["acked"], data["naked"], data["ack_implicit"], data["pending"]) == (1, 1, 1, 1)
    assert data["delivery"] == 50.0                      # 1 ACK ÷ (1 ACK + 1 NAK), só DMs
    metrics.ingest_message_status(2, "ack")               # repetido: ignorado
    assert metrics.data_for("reliability")["acked"] == 1


def test_reliability_no_longer_judges_flooding_from_client_duplicates(metrics):
    metrics.ingest_raw_packet(telemetry(1.0, id=77))
    page = metrics.html_for("reliability")
    assert "Flood saudável" not in page and "Healthy flood" not in page


def test_rendered_scripts_have_no_raw_newlines_inside_js_strings(metrics):
    """Um '\\n' num f-string Python vira uma quebra de linha real dentro de
    uma string JS e invalida o script inteiro (o Nó Local deixava de
    actualizar, incluindo o uptime ao vivo)."""
    import re
    metrics.ingest_raw_packet(packet("TELEMETRY_APP", LOCAL_NUM, decoded={"telemetry": {
        "deviceMetrics": {"airUtilTx": 1.0, "uptimeSeconds": 60}}}))
    for key in SECTION_KEYS:
        for script in re.findall(r"<script>([\s\S]*?)</script>", metrics.html_for(key)):
            for line in script.splitlines():
                code = re.sub(r"//.*$", "", line)
                assert code.count("'") % 2 == 0, f"{key}: aspa por fechar em {line.strip()!r}"


# ── fase 1: dados de rádio por pacote ────────────────────────────────────────

def radio(from_num=PEER_NUM, snr=None, rssi=None, hop_start=None, hop_limit=None, **kw):
    extra = {}
    if rssi is not None: extra["rxRssi"] = rssi
    if hop_start is not None: extra["hopStart"] = hop_start
    if hop_limit:            extra["hopLimit"] = hop_limit   # a biblioteca omite o 0
    return packet("POSITION_APP", from_num, decoded={}, rx_snr=snr, **extra, **kw)


def test_mqtt_and_own_packets_stay_out_of_radio_statistics(metrics):
    metrics.ingest_raw_packet(radio(snr=6.0, rssi=-90))
    metrics.ingest_raw_packet(radio(snr=0.0, via_mqtt=True))          # MQTT: SNR sem significado
    metrics.ingest_raw_packet(radio(from_num=LOCAL_NUM, snr=0.0))      # eco do próprio nó
    assert metrics._snr_values == [6.0]
    assert metrics._rssi_values == [-90]
    data = metrics.data_for("rf")
    assert data["mqtt_excluded"] == 1
    assert data["n"] == 1
    # o tráfego continua a contar os três pacotes
    assert metrics.data_for("overview")["total_pkts"] == 3


def test_hops_come_from_each_packet_and_missing_hop_limit_means_zero(metrics):
    metrics.ingest_raw_packet(radio(snr=1.0, hop_start=3, hop_limit=3))  # directo
    metrics.ingest_raw_packet(radio(snr=1.0, hop_start=3, hop_limit=1))  # 2 hops
    metrics.ingest_raw_packet(radio(snr=1.0, hop_start=3))               # hopLimit omitido = 0 → 3 hops, esgotado
    p = radio(snr=1.0); p["hopsAway"] = 4                                # firmware antigo: NodeDB
    metrics.ingest_raw_packet(p)
    assert metrics._hops_values == [0, 2, 3, 4]
    data = metrics.data_for("rf")
    assert (data["hop_exhausted"], data["hop_measured"]) == (1, 3)
    assert data["hop_exhausted_pct"] == 33.3


def test_radio_diagnosis_separates_noise_from_weak_signal(metrics):
    assert MeshMetrics.radio_diagnosis(5.0, -110) == "ok"
    assert MeshMetrics.radio_diagnosis(-2.0, -85) == "ok"       # normal em LoRa
    assert MeshMetrics.radio_diagnosis(-8.0, -85) == "noise"     # forte mas sujo
    assert MeshMetrics.radio_diagnosis(-8.0, -120) == "weak"     # longe
    assert MeshMetrics.radio_diagnosis(None, -90) == ""

    metrics.ingest_raw_packet(radio(snr=-8.0, rssi=-85))
    metrics.ingest_raw_packet(radio(from_num=0x11110002, snr=7.0, rssi=-95))
    rows = metrics.data_for("rf")["node_rows"]
    assert [r[6] for r in rows] == ["noise", "ok"]              # piores primeiro
    page = metrics.html_for("rf")
    assert 'id="rf-node-tbody"' in page and "-85 dBm" in page


# ── fase 2: LocalStats e HostMetrics ─────────────────────────────────────────

def local_stats(**fields):
    base = {"uptimeSeconds": 3600, "numPacketsRx": 200, "numPacketsTx": 40,
            "numRxDupe": 60, "numPacketsRxBad": 10, "numTxRelay": 30,
            "numTxRelayCanceled": 10, "numOnlineNodes": 12, "numTotalNodes": 40,
            "noiseFloor": -112, "channelUtilization": 14.5, "airUtilTx": 2.25}
    base.update(fields)
    base = {k: v for k, v in base.items() if v is not None}
    return packet("TELEMETRY_APP", LOCAL_NUM, decoded={"telemetry": {"localStats": base}})


def test_local_stats_give_real_duplicate_and_error_rates(metrics):
    metrics.ingest_raw_packet(local_stats(numTxDropped=None))   # 0 omitido pela biblioteca
    r = metrics._data_radio_health()
    assert r["has_stats"] and r["dropped"] == 0
    assert (r["dupe_pct"], r["bad_pct"], r["relay_cancel_pct"]) == (30.0, 5.0, 25.0)
    assert r["noise"] == -112 and (r["online"], r["total"]) == (12, 40)
    assert r["delta"] == {}                                         # primeira amostra
    # também alimentam o canal e o duty cycle do nó local
    local = metrics.data_for("local_node")
    assert local["ch_util"] == 14.5 and local["dc_est"] == 2.25
    # e a Fiabilidade passa a ter duplicados reais
    assert metrics.data_for("reliability")["ls_dupe_pct"] == 30.0


def test_local_stats_delta_and_node_restart(metrics):
    metrics.ingest_raw_packet(local_stats())
    metrics.ingest_raw_packet(local_stats(uptimeSeconds=4500, numPacketsRx=260, numRxDupe=75))
    delta = metrics._data_radio_health()["delta"]
    assert (delta["numPacketsRx"], delta["numRxDupe"], delta["minutes"]) == (60, 15, 15.0)
    metrics.ingest_raw_packet(local_stats(uptimeSeconds=120, numPacketsRx=5, numRxDupe=1))  # reiniciou
    assert metrics._data_radio_health()["delta"] == {}


def test_host_metrics_convert_uint64_text_and_load(metrics):
    metrics.ingest_raw_packet(packet("TELEMETRY_APP", LOCAL_NUM, decoded={"telemetry": {"hostMetrics": {
        "uptimeSeconds": 90000, "freememBytes": "1500000000", "diskfree1Bytes": "25300000000",
        "load1": 85, "load5": 120, "load15": 99, "userString": "uConsole"}}}))
    h = metrics._data_radio_health()
    assert h["has_host"] and h["host_load"] == [0.85, 1.2, 0.99]
    assert h["host_mem_mb"] == 1500 and h["host_disk_gb"][0] == 25.3
    assert "uConsole" in metrics.html_for("local_node")


def test_local_stats_redraw_the_local_node_and_render(metrics):
    before = metrics.refresh_state("local_node")["reload_key"]
    metrics.ingest_raw_packet(local_stats())
    assert metrics.refresh_state("local_node")["reload_key"] != before
    page = metrics.html_for("local_node")
    assert "-112 dBm" in page and "30.0%" in page


def test_local_stats_are_logged_at_debug_only(metrics, caplog):
    """O console fica com mensagens de alto nível: a chegada dos LocalStats
    é diagnóstico e só aparece com --debug."""
    import logging
    with caplog.at_level(logging.DEBUG, logger="MeshDeck.metrics"):
        metrics.ingest_raw_packet(local_stats())
    records = [r for r in caplog.records if "LocalStats" in r.getMessage()]
    assert records and all(r.levelno == logging.DEBUG for r in records)


# ── fase 3: alcance directo e retransmissores ────────────────────────────────

def test_direct_range_uses_only_zero_hop_radio_packets(metrics):
    metrics.ingest_node_position(LOCAL_ID, 38.7223, -9.1393)
    metrics.ingest_node_position(PEER_ID, 38.8000, -9.1393)          # ≈ 8,6 km a norte
    metrics.ingest_raw_packet(radio(snr=4.0, hop_start=3, hop_limit=3))
    metrics.ingest_raw_packet(radio(snr=6.5, hop_start=3, hop_limit=3))
    metrics.ingest_raw_packet(radio(from_num=0x11110002, snr=1.0, hop_start=3, hop_limit=1))  # 2 hops: fora
    metrics.ingest_raw_packet(radio(from_num=0x11110003, snr=1.0, hop_start=3, hop_limit=3,
                                    via_mqtt=True))                                          # MQTT: fora
    d = metrics.data_for("range_links")
    assert [r[0] for r in d["direct_rows"]] == [PEER_ID]
    nid, _name, dist, best, last, n = d["direct_rows"][0]
    assert 8.5 < dist < 8.8 and (best, last, n) == (6.5, 6.5, 2)
    assert d["max_direct"] == dist
    assert metrics.has_data("range_links")


def test_relayers_are_matched_by_last_byte_and_flag_ambiguity(metrics):
    metrics.ingest_node_position("!aaaa0042", 38.7, -9.1)              # dois nós conhecidos
    metrics.ingest_node_position("!bbbb0042", 38.7, -9.2)              # com o mesmo byte 0x42
    metrics._node_short["!cccc0017"] = "R17"
    for relay in (0x17, 0x17, 0x17, 0x42, 0x99):
        metrics.ingest_raw_packet(radio(snr=1.0, hop_start=3, hop_limit=2, relayNode=relay))
    metrics.ingest_raw_packet(radio(snr=1.0, hop_start=3, hop_limit=3, relayNode=0x01))  # directo: não conta
    rows = {r[0]: r for r in metrics.data_for("range_links")["relay_rows"]}
    assert metrics.data_for("range_links")["relayed_total"] == 5
    assert rows["…17"][1:4] == ["R17", 3, 60.0]
    assert rows["…42"][4] == 2                                          # ambíguo
    assert rows["…99"][1] == ""                                         # desconhecido
    assert "…01" not in rows
    page = metrics.html_for("range_links")
    assert "R17" in page and 'id="relay-tbody"' in page


# ── fase 4: ambiente e energia ───────────────────────────────────────────────

def env(from_num=PEER_NUM, **metrics_):
    return packet("TELEMETRY_APP", from_num, decoded={"telemetry": {"environmentMetrics": metrics_}})


def test_environment_keeps_latest_values_and_merges_partial_reports(metrics):
    assert not metrics.has_data("environment")
    metrics.ingest_raw_packet(env(temperature=21.37, relativeHumidity=58.0, barometricPressure=1013.2))
    metrics.ingest_raw_packet(env(temperature=22.0))                     # envio parcial
    metrics.ingest_raw_packet(env(from_num=0x11110002, temperature=15.5))
    metrics.ingest_raw_packet(packet("TELEMETRY_APP", 0x11110003, decoded={"telemetry": {
        "airQualityMetrics": {"co2": 640, "pm25Standard": 12}}}))
    assert metrics.has_data("environment")
    d = metrics.data_for("environment")
    labels = [label for label, _unit in d["cols"]]
    assert labels[:3] == ["Temperatura", "Humidade", "Pressão"] or labels[:3] == ["Temperature", "Humidity", "Pressure"]
    row = next(r for r in d["env_rows"] if r[0] == PEER_ID)
    assert row[2][:3] == ["22.0 °C", "58 %", "1013 hPa"]               # humidade mantida
    assert d["temp_max"][1] == 22.0 and d["temp_min"][1] == 15.5
    assert d["n_env"] == 3
    assert len(next(s for s in d["temp_series"] if s["points"] and s["points"][-1]["y"] == 22.0)["points"]) == 2


def test_power_channels_and_page_render(metrics):
    metrics.ingest_raw_packet(packet("TELEMETRY_APP", PEER_NUM, decoded={"telemetry": {"powerMetrics": {
        "ch1Voltage": 12.613, "ch1Current": 231.4, "ch2Voltage": 5.02}}}))
    d = metrics.data_for("environment")
    assert d["power_rows"][0][2] == ["ch1: 12.61 V · 231 mA", "ch2: 5.02 V"]
    page = metrics.html_for("environment")
    assert "12.61 V" in page and 'id="power-tbody"' in page


def test_environment_ignores_nan_from_faulty_sensors(metrics):
    metrics.ingest_raw_packet(env(temperature=float("nan"), relativeHumidity=40.0))
    assert metrics._env[PEER_ID]["values"] == {"relativeHumidity": 40.0}


def test_metric_pages_load_no_external_scripts(metrics):
    """Os gráficos têm de funcionar sem internet: nada de CDNs."""
    import re
    for key in SECTION_KEYS:
        page = metrics.html_for(key)
        assert not re.search(r"""<script[^>]+src=["']https?://""", page), key
        assert "/vendor/chart.umd.min.js" in page, key


@pytest.mark.parametrize("lang, expected, absent", [
    ("pt", ["Limite UE (10%)", "Aviso (7%)", "Utilização do canal (%)"], ["EU limit", "Warning (7%)", "Ch. Utilization"]),
    ("en", ["EU limit (10%)", "Warning (7%)", "Channel utilisation (%)"], ["Limite UE", "Aviso (7%)"]),
])
def test_chart_legends_follow_the_language(metrics, lang, expected, absent):
    """As legendas dos gráficos são desenhadas no canvas: não aparecem no
    texto da página, por isso uma verificação visual não as apanha."""
    import i18n
    previous = i18n.get_language()
    i18n.set_language(lang)
    try:
        for i in range(3):
            metrics.ingest_raw_packet(packet("TELEMETRY_APP", LOCAL_NUM, decoded={"telemetry": {
                "deviceMetrics": {"airUtilTx": 1.0 + i, "channelUtilization": 10.0 + i}}}))
        pages = metrics.html_for("channel") + metrics.html_for("local_node")
    finally:
        i18n.set_language(previous)
    for text in expected:
        assert text in pages, text
    for text in absent:
        assert text not in pages, text
