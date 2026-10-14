"""Testes do MessageStore — encaminhamento de conversas e estado de entrega."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.messages import MessageStore, channel_key, dm_key, is_dm_key

LOCAL_ID = "!aabb0001"
LOCAL_NUM = 0xAABB0001
PEER_ID = "!11110001"
PEER_NUM = 0x11110001
BROADCAST = 0xFFFFFFFF


@pytest.fixture
def store():
    s = MessageStore()
    s.set_local_node(LOCAL_ID)
    return s


def pkt(**kw):
    base = {"to": BROADCAST, "toId": "^all", "rxTime": 1767323045}
    base.update(kw)
    return base


# ── chaves ───────────────────────────────────────────────────────────────────

def test_key_helpers():
    assert channel_key(3) == "ch:3"
    assert dm_key("!ABCD1234") == "dm:!abcd1234"
    assert is_dm_key("dm:!a") and not is_dm_key("ch:0")


# ── encaminhamento ───────────────────────────────────────────────────────────

def test_broadcast_goes_to_the_channel(store):
    key, entry = store.add_incoming(2, PEER_ID, "olá rede", pkt())
    assert key == "ch:2"
    assert entry["direct"] is False
    assert entry["transport"] == "rf"


def test_message_addressed_to_us_by_num_is_a_dm(store):
    key, entry = store.add_incoming(0, PEER_ID, "só para ti",
                                    pkt(to=LOCAL_NUM, toId=""))
    assert key == dm_key(PEER_ID)
    assert entry["direct"] is True


def test_message_addressed_to_us_by_id_is_a_dm(store):
    key, _ = store.add_incoming(0, PEER_ID, "x", pkt(to=0, toId=LOCAL_ID))
    assert key == dm_key(PEER_ID)


def test_pki_message_is_always_a_dm(store):
    key, entry = store.add_incoming(0, PEER_ID, "cifrado",
                                    pkt(pkiEncrypted=True))
    assert key == dm_key(PEER_ID)
    assert entry["transport"] == "pki"


def test_message_to_a_third_party_is_still_a_dm_conversation(store):
    """Destino não-broadcast que não somos nós: continua a ser tráfego directo."""
    key, entry = store.add_incoming(0, PEER_ID, "x", pkt(to=0x33330003, toId="!33330003"))
    assert key == dm_key(PEER_ID)
    assert entry["direct"] is True


def test_mqtt_transport_is_recorded(store):
    _key, entry = store.add_incoming(0, PEER_ID, "x", pkt(viaMqtt=True))
    assert entry["transport"] == "mqtt"


def test_hops_are_recorded(store):
    _key, entry = store.add_incoming(0, PEER_ID, "x", pkt(hopsAway=3))
    assert entry["hops"] == 3


def test_our_own_echo_is_dropped(store):
    """O daemon reenvia as nossas mensagens; não podem duplicar na conversa."""
    assert store.add_incoming(0, LOCAL_ID, "eco", pkt()) is None
    assert store.keys() == []


def test_message_without_sender_is_dropped(store):
    assert store.add_incoming(0, "", "sem remetente", pkt()) is None


def test_sender_id_is_normalised_to_lowercase(store):
    key, entry = store.add_incoming(0, "!11110001".upper(), "x", pkt())
    assert entry["from_id"] == PEER_ID
    assert key == "ch:0"


def test_missing_rx_time_falls_back_to_now(store):
    _key, entry = store.add_incoming(0, PEER_ID, "x", {"to": BROADCAST})
    assert entry["time"] > 0


def test_broken_destination_is_treated_as_broadcast(store):
    key, _ = store.add_incoming(1, PEER_ID, "x", pkt(to="não é número", toId=""))
    assert key == "ch:1"


# ── envio e estado ───────────────────────────────────────────────────────────

def test_outgoing_channel_message(store):
    key, entry = store.add_outgoing_channel(0, "enviado", packet_id=0x111)
    assert key == "ch:0"
    assert entry["outgoing"] is True
    assert entry["status"] == "sending"
    assert entry["from_id"] == LOCAL_ID


def test_outgoing_dm_records_transport(store):
    _key, pki_entry = store.add_outgoing_dm(PEER_ID, "a", pki=True, packet_id=1)
    _key, psk_entry = store.add_outgoing_dm(PEER_ID, "b", pki=False, packet_id=2)
    assert pki_entry["transport"] == "pki"
    assert psk_entry["transport"] == "psk"
    assert psk_entry["to_id"] == PEER_ID


def test_ack_updates_the_right_message(store):
    store.add_outgoing_channel(0, "primeira", packet_id=0x111)
    store.add_outgoing_channel(0, "segunda", packet_id=0x222)
    updated = store.update_status(0x222, "ack")
    assert updated["text"] == "segunda"
    assert updated["status"] == "ack"
    assert store.conversation("ch:0")[0]["status"] == "sending"


def test_nak_carries_the_reason(store):
    store.add_outgoing_dm(PEER_ID, "x", packet_id=0x333)
    updated = store.update_status(0x333, "nak", "NO_ROUTE")
    assert updated["status"] == "nak"
    assert updated["status_detail"] == "NO_ROUTE"


def test_status_for_unknown_packet_is_ignored(store):
    assert store.update_status(0x999, "ack") is None


def test_status_is_only_applied_once(store):
    store.add_outgoing_channel(0, "x", packet_id=0x111)
    assert store.update_status(0x111, "ack") is not None
    assert store.update_status(0x111, "nak") is None


def test_outgoing_without_packet_id_is_not_tracked_for_ack(store):
    store.add_outgoing_channel(0, "x", packet_id=0)
    assert store.update_status(0, "ack") is None


# ── não lidas e ordenação ────────────────────────────────────────────────────

def test_incoming_counts_as_unread_but_outgoing_does_not(store):
    store.add_incoming(0, PEER_ID, "a", pkt())
    store.add_incoming(0, PEER_ID, "b", pkt())
    store.add_outgoing_channel(0, "minha")
    assert store.unread() == {"ch:0": 2}
    store.mark_read("ch:0")
    assert store.unread() == {}


def test_dm_partners_ordered_by_recent_activity(store):
    other = "!22220002"
    store.add_incoming(0, PEER_ID, "a", pkt(to=LOCAL_NUM, toId="", rxTime=1000))
    store.add_incoming(0, other, "b", pkt(to=LOCAL_NUM, toId="", rxTime=2000))
    assert store.dm_partners()[0] == other
    store.add_outgoing_dm(PEER_ID, "resposta")
    assert store.dm_partners()[0] == PEER_ID


def test_conversation_is_sorted_by_time(store):
    store.add_incoming(0, PEER_ID, "mais recente", pkt(rxTime=2000))
    store.add_incoming(0, PEER_ID, "mais antiga", pkt(rxTime=1000))
    assert [e["text"] for e in store.conversation("ch:0")] == ["mais antiga", "mais recente"]


def test_conversation_is_capped(store):
    for i in range(MessageStore.MAX_PER_CONVERSATION + 40):
        store.add_incoming(0, PEER_ID, f"m{i}", pkt(rxTime=1000 + i))
    conv = store.conversation("ch:0")
    assert len(conv) == MessageStore.MAX_PER_CONVERSATION
    assert conv[-1]["text"] == f"m{MessageStore.MAX_PER_CONVERSATION + 39}"


def test_conversation_returns_copies(store):
    store.add_incoming(0, PEER_ID, "original", pkt())
    conv = store.conversation("ch:0")
    conv[0]["text"] = "alterado"
    assert store.conversation("ch:0")[0]["text"] == "original"


def test_unknown_conversation_is_empty(store):
    assert store.conversation("ch:7") == []


# ── snapshot ─────────────────────────────────────────────────────────────────

def test_snapshot_is_json_serialisable(store):
    store.add_incoming(0, PEER_ID, "recebida", pkt())
    store.add_outgoing_dm(PEER_ID, "enviada", pki=True, packet_id=5)
    snap = store.snapshot()
    json.dumps(snap)
    assert "ch:0" in snap["conversations"]
    assert dm_key(PEER_ID) in snap["conversations"]
    assert snap["unread"]["ch:0"] == 1
    assert snap["dm_partners"] == [PEER_ID]


def test_clear(store):
    store.add_incoming(0, PEER_ID, "x", pkt())
    store.clear()
    assert store.keys() == [] and store.unread() == {}


def test_local_node_can_be_set_later(store):
    """Antes de o ID local ser conhecido, nada deve ser tratado como eco."""
    fresh = MessageStore()
    key, _ = fresh.add_incoming(0, LOCAL_ID, "x", pkt())
    assert key == "ch:0"
    fresh.set_local_node(LOCAL_ID)
    assert fresh.add_incoming(0, LOCAL_ID, "y", pkt()) is None


def test_ids_are_unique(store):
    store.add_incoming(0, PEER_ID, "a", pkt())
    store.add_outgoing_channel(0, "b")
    ids = [e["id"] for e in store.conversation("ch:0")]
    assert len(set(ids)) == 2
