"""Testes do NodeStore e da regra de fusão _safe_update."""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.node_store import NodeStore, _safe_update, node_record_from_nodedb


@pytest.fixture
def store():
    return NodeStore()


def nodedb_entry(num, *, long_name="Alpha", short_name="ALF", last_heard=None,
                 lat_i=None, lon_i=None, snr=None, battery=None, pubkey="",
                 uid=None, hops=None, via_mqtt=None, alt=None):
    entry = {
        "num": num,
        "user": {"longName": long_name, "shortName": short_name,
                 "hwModel": "TBEAM", "publicKey": pubkey},
    }
    if uid is not None:
        entry["user"]["id"] = uid
    if last_heard is not None:
        entry["lastHeard"] = last_heard
    if lat_i is not None or lon_i is not None:
        entry["position"] = {"latitudeI": lat_i, "longitudeI": lon_i, "altitude": alt}
    if snr is not None:
        entry["snr"] = snr
    if battery is not None:
        entry["deviceMetrics"] = {"batteryLevel": battery}
    if hops is not None:
        entry["hopsAway"] = hops
    if via_mqtt is not None:
        entry["viaMqtt"] = via_mqtt
    return entry


# ── _safe_update: a regra de fusão ───────────────────────────────────────────

def test_safe_update_never_regresses_last_heard():
    newer = datetime(2026, 5, 1, 12, 0)
    older = datetime(2026, 5, 1, 11, 0)
    target = {"last_heard": newer}
    _safe_update(target, {"last_heard": older})
    assert target["last_heard"] == newer


def test_safe_update_accepts_newer_last_heard():
    older = datetime(2026, 5, 1, 11, 0)
    newer = datetime(2026, 5, 1, 12, 0)
    target = {"last_heard": older}
    _safe_update(target, {"last_heard": newer})
    assert target["last_heard"] == newer


@pytest.mark.parametrize("bad", [None, 0, "", 1700000000])
def test_safe_update_ignores_non_datetime_last_heard(bad):
    """lastHeard=0 do NodeDB não pode apagar um last_heard real."""
    real = datetime(2026, 5, 1, 12, 0)
    target = {"last_heard": real}
    _safe_update(target, {"last_heard": bad})
    assert target["last_heard"] == real


def test_safe_update_keeps_numbers_against_none():
    target = {"snr": 7.5, "battery_level": 80, "latitude": 1.0}
    _safe_update(target, {"snr": None, "battery_level": None, "latitude": None})
    assert target == {"snr": 7.5, "battery_level": 80, "latitude": 1.0}


def test_safe_update_allows_zero_as_a_real_number():
    """0 dB de SNR e 0% de bateria são valores válidos, não ausência."""
    target = {"snr": 7.5, "battery_level": 80}
    _safe_update(target, {"snr": 0, "battery_level": 0})
    assert target == {"snr": 0, "battery_level": 0}


def test_safe_update_keeps_strings_against_empty():
    target = {"long_name": "Alpha", "short_name": "ALF", "public_key": "k"}
    _safe_update(target, {"long_name": "", "short_name": "", "public_key": ""})
    assert target == {"long_name": "Alpha", "short_name": "ALF", "public_key": "k"}


def test_safe_update_overwrites_booleans_and_plain_fields():
    target = {"via_mqtt": True, "last_packet_type": "POSITION_APP"}
    _safe_update(target, {"via_mqtt": False, "last_packet_type": "TELEMETRY_APP"})
    assert target == {"via_mqtt": False, "last_packet_type": "TELEMETRY_APP"}


# ── node_record_from_nodedb ──────────────────────────────────────────────────

def test_record_uses_user_id_when_present():
    nid, data = node_record_from_nodedb(0x1234abcd, nodedb_entry(0x1234abcd, uid="!1234ABCD"))
    assert nid == "!1234abcd"
    assert data["id_num"] == 0x1234abcd


def test_record_derives_id_from_num_when_user_id_missing():
    nid, _ = node_record_from_nodedb(0xdeadbeef, nodedb_entry(0xdeadbeef))
    assert nid == "!deadbeef"


def test_record_converts_position_to_degrees():
    _, data = node_record_from_nodedb(1, nodedb_entry(1, lat_i=385000000, lon_i=-91000000, alt=120))
    assert data["latitude"] == pytest.approx(38.5)
    assert data["longitude"] == pytest.approx(-9.1)
    assert data["altitude"] == 120


def test_record_last_heard_zero_becomes_none():
    _, data = node_record_from_nodedb(1, nodedb_entry(1, last_heard=0))
    assert data["last_heard"] is None


def test_record_last_heard_converted_to_datetime():
    _, data = node_record_from_nodedb(1, nodedb_entry(1, last_heard=1767323045))
    assert isinstance(data["last_heard"], datetime)


def test_record_rejects_unusable_entries():
    assert node_record_from_nodedb(None, {"user": {}}) is None
    assert node_record_from_nodedb(1, "não é um dict") is None


def test_record_survives_missing_subdicts():
    nid, data = node_record_from_nodedb(5, {"num": 5})
    assert nid == "!00000005"
    assert data["latitude"] is None
    assert data["battery_level"] is None


# ── NodeStore ────────────────────────────────────────────────────────────────

def test_update_inserts_then_merges(store):
    assert store.update("!AAAA0001", {"long_name": "Alpha"}) is True
    assert store.update("!aaaa0001", {"short_name": "ALF"}) is False
    rec = store.get("!AAAA0001")
    assert rec["long_name"] == "Alpha" and rec["short_name"] == "ALF"
    assert store.count() == 1


def test_ids_are_case_insensitive(store):
    store.update("!ABCD1234", {"long_name": "X"})
    assert store.get("!abcd1234") is not None
    assert store.get("!ABCD1234")["id_string"] == "!abcd1234"


def test_update_ignores_empty_id(store):
    assert store.update("", {"long_name": "X"}) is False
    assert store.count() == 0


def test_last_packet_stored_only_when_given(store):
    store.update("!a", {"long_name": "X"}, packet={"portnum": "TEXT"})
    assert store.get("!a")["last_packet"] == {"portnum": "TEXT"}
    store.update("!a", {"short_name": "Y"})
    assert store.get("!a")["last_packet"] == {"portnum": "TEXT"}


def test_apply_batch_reports_new_nodes(store):
    batch = [(1, nodedb_entry(1, uid="!00000001")),
             (2, nodedb_entry(2, uid="!00000002"))]
    assert sorted(store.apply_batch(batch)) == ["!00000001", "!00000002"]
    # Segundo batch com os mesmos nós não reporta nada como novo.
    assert store.apply_batch(batch) == []
    assert store.count() == 2


def test_apply_batch_skips_broken_entries(store):
    batch = [(1, nodedb_entry(1, uid="!00000001")), (None, {"user": {}}), (3, None)]
    assert store.apply_batch(batch) == ["!00000001"]
    assert store.count() == 1


def test_batch_does_not_erase_fresher_packet_data(store):
    """O batch do NodeDB chega com lastHeard=0; não pode apagar o pacote vivo."""
    live = datetime.now()
    store.update("!00000001", {"last_heard": live, "snr": 9.0})
    store.apply_batch([(1, nodedb_entry(1, uid="!00000001", last_heard=0))])
    rec = store.get("!00000001")
    assert rec["last_heard"] == live
    assert rec["snr"] == 9.0


# ── nó local ─────────────────────────────────────────────────────────────────

def test_is_local_by_id_and_by_num(store):
    store.set_local_node("!ABCD0001", 0xABCD0001)
    assert store.is_local("!abcd0001")
    assert store.is_local("qualquer", 0xABCD0001)
    assert not store.is_local("!00000002", 2)


def test_is_local_handles_signed_node_num(store):
    """nodeNum chega às vezes como inteiro com sinal; a máscara resolve."""
    store.set_local_node("!ffffffff", -1)
    assert store.is_local("!ffffffff")
    assert store.is_local("", 0xFFFFFFFF)


def test_counts_exclude_local_node(store):
    now = datetime.now()
    store.set_local_node("!00000001", 1)
    store.update("!00000001", {"id_num": 1, "last_heard": now})
    store.update("!00000002", {"id_num": 2, "last_heard": now})
    store.update("!00000003", {"id_num": 3, "last_heard": now - timedelta(hours=5)})
    store.update("!00000004", {"id_num": 4})          # nunca contactado

    assert store.count() == 4
    assert store.contacted_count() == 2               # 2 e 3, sem o local nem o 4
    assert store.online_count() == 1                  # só o 2


def test_online_window_is_configurable(store):
    store.update("!00000002", {"id_num": 2, "last_heard": datetime.now() - timedelta(minutes=30)})
    assert store.online_count() == 1
    assert store.online_count(window=timedelta(minutes=10)) == 0


# ── leitura e snapshot ───────────────────────────────────────────────────────

def test_with_position_filters(store):
    store.update("!a", {"latitude": 1.0, "longitude": 2.0})
    store.update("!b", {"latitude": None, "longitude": None})
    assert [n["id_string"] for n in store.with_position()] == ["!a"]


def test_choices_shape(store):
    store.update("!a", {"long_name": "Alpha", "short_name": "ALF"})
    assert store.choices() == [("!a", "Alpha", "ALF")]


def test_clear(store):
    store.update("!a", {"long_name": "X"})
    store.clear()
    assert store.count() == 0 and store.all() == []


def test_snapshot_is_json_serialisable(store):
    import json
    store.set_local_node("!00000001", 1)
    store.update("!00000001", {"id_num": 1, "last_heard": datetime(2026, 1, 2, 3, 4, 5)})
    store.update("!00000002", {"id_num": 2, "public_key": b"\x01\x02"},
                 packet={"raw": b"\xff", "when": datetime(2026, 1, 1)})
    snap = store.snapshot()
    json.dumps(snap)                        # não pode levantar

    by_id = {n["id_string"]: n for n in snap}
    assert by_id["!00000001"]["is_local"] is True
    assert by_id["!00000002"]["is_local"] is False
    assert by_id["!00000001"]["last_heard"] == datetime(2026, 1, 2, 3, 4, 5).timestamp()
    # O pacote bruto fica fora do snapshot por omissão.
    assert "last_packet" not in by_id["!00000002"]


def test_snapshot_can_include_packets(store):
    import json
    store.update("!a", {"long_name": "X"}, packet={"raw": b"\xff"})
    snap = store.snapshot(include_packets=True)
    json.dumps(snap)
    assert snap[0]["last_packet"]["raw"] == "/w=="      # base64 de 0xff


def test_packet_snapshot(store):
    store.update("!a", {"long_name": "X"}, packet={"raw": b"\xff"})
    assert store.packet_snapshot("!a") == {"raw": "/w=="}
    assert store.packet_snapshot("!b") is None
    store.update("!c", {"long_name": "Y"})
    assert store.packet_snapshot("!c") is None


# ── forma estável do registo enviado ao cliente ──────────────────────────────

def test_payload_always_has_every_base_field(store):
    from core.node_store import NODE_PAYLOAD_FIELDS
    store.update("!00000002", {"id_num": 2, "snr": None, "long_name": "X"})
    item = store.payload(store.get("!00000002"))
    for field in NODE_PAYLOAD_FIELDS:
        assert field in item, field
    assert item["snr"] is None            # a null, não ausente
    assert item["latitude"] is None


def test_snapshot_has_a_single_shape(store):
    """Antes disto o servidor enviava dezenas de formas e o cliente tinha de
    testar a existência de cada campo."""
    from core.node_store import NODE_PAYLOAD_FIELDS
    store.set_local_node("!00000001", 1)
    store.update("!00000001", {"id_num": 1})
    store.update("!00000002", {"id_num": 2, "snr": None})
    store.update("!00000003", {"id_num": 3, "snr": 5.0, "battery_level": 80,
                               "latitude": 1.0, "longitude": 2.0})
    shapes = {tuple(sorted(item)) for item in store.snapshot()}
    assert len(shapes) == 1
    assert set(shapes.pop()) == set(NODE_PAYLOAD_FIELDS) | {"is_local"}


def test_optional_telemetry_only_appears_when_present(store):
    """env_*, pwr_* e health_* são dezenas de campos; não se enviam vazios."""
    store.update("!00000002", {"id_num": 2})
    store.update("!00000003", {"id_num": 3, "env_temperature": 21.5,
                               "health_heartBpm": 70})
    by_id = {item["id_string"]: item for item in store.snapshot()}
    assert "env_temperature" not in by_id["!00000002"]
    assert by_id["!00000003"]["env_temperature"] == 21.5
    assert by_id["!00000003"]["health_heartBpm"] == 70


def test_payload_can_include_the_packet(store):
    store.update("!a", {"long_name": "X"}, packet={"raw": b"\xff"})
    assert "last_packet" not in store.payload(store.get("!a"))
    assert store.payload(store.get("!a"), include_packet=True)["last_packet"] == {"raw": "/w=="}
