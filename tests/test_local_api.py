"""API local (/api/local/*): só para a própria máquina, sem login."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fake_meshtastic import emit_text, packet
from tests.test_server import PEER_ID, PEER_NUM, server, wait_for, wired  # noqa: F401 (fixtures)

REMOTE = {"REMOTE_ADDR": "192.168.1.40"}


def sent_texts(iface, n, timeout=3.0):
    """O envio passa pela thread do worker: espera até haver n textos enviados."""
    end = time.time() + timeout
    while len(iface.sent_text) < n and time.time() < end:
        time.sleep(0.02)
    return [t["text"] for t in iface.sent_text]


import pytest

from core.settings import Settings
from server.auth import ServerConfig


@pytest.fixture(autouse=True)
def enable_local_api(request, monkeypatch):
    """Liga a API em todos os testes, menos nos que verificam o estado por omissão."""
    if "default" not in request.node.name:
        monkeypatch.setattr(ServerConfig, "local_api", property(lambda self: True))


def test_disabled_by_default_is_404(server):  # noqa: F811
    client = server["client"]
    assert client.get("/api/local/dash").status_code == 404
    assert client.post("/api/local/send", json={"text": "x"}).status_code == 404
    assert client.post("/api/local/read", json={"all": True}).status_code == 404


def test_setting_default_off_and_saved(tmp_path):
    cfg = ServerConfig(Settings(filename="server.json", directory=str(tmp_path)))
    assert cfg.local_api is False
    cfg.set_local_api(True)
    again = ServerConfig(Settings(filename="server.json", directory=str(tmp_path)))
    assert again.local_api is True


def test_remote_requests_are_refused(server):  # noqa: F811
    client = server["client"]
    assert client.get("/api/local/dash", environ_base=REMOTE).status_code == 403
    assert client.post("/api/local/send", json={"text": "x"}, environ_base=REMOTE).status_code == 403
    assert client.post("/api/local/read", json={"all": True}, environ_base=REMOTE).status_code == 403


def test_dash_works_without_login(server):  # noqa: F811
    client = server["client"]
    client.post("/api/logout")
    data = client.get("/api/local/dash").get_json()
    assert data["connected"] is False
    assert data["unread_total"] == 0
    assert data["nodes"] == [] and data["messages"] == []


def test_dash_lists_messages_with_sender_name_and_unread(wired):  # noqa: F811
    server, iface = wired
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, channel=0, decoded={"text": "olá dash"}), iface)
    wait_for(server["broadcasts"], "message")
    data = server["client"].get("/api/local/dash").get_json()
    assert data["unread"] == {"ch:0": 1} and data["unread_total"] == 1
    msg = data["messages"][0]
    assert msg["text"] == "olá dash" and msg["key"] == "ch:0" and msg["from_id"] == PEER_ID
    assert msg["transport"] == "rf" and "hops" in msg
    assert "public_key" not in data["nodes"][0] if data["nodes"] else True


def test_dash_marks_mqtt_messages(wired):  # noqa: F811
    server, iface = wired
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, channel=0, decoded={"text": "via mqtt"}, via_mqtt=True), iface)
    wait_for(server["broadcasts"], "message")
    msg = server["client"].get("/api/local/dash").get_json()["messages"][0]
    assert msg["transport"] == "mqtt"


def test_read_clears_unread(wired):  # noqa: F811
    server, iface = wired
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, channel=0, decoded={"text": "ler"}), iface)
    wait_for(server["broadcasts"], "message")
    r = server["client"].post("/api/local/read", json={"key": "ch:0"}).get_json()
    assert r["unread"] == {}


def test_send_channel_and_dm(wired):  # noqa: F811
    server, iface = wired
    client = server["client"]
    assert client.post("/api/local/send", json={"text": "do dash", "channel": 0}).get_json()["ok"]
    assert sent_texts(iface, 1)[-1] == "do dash"
    r = client.post("/api/local/send", json={"text": "direta", "dest": PEER_ID}).get_json()
    assert r["key"] == f"dm:{PEER_ID}"
    assert sent_texts(iface, 2)[-1] == "direta"


def test_send_validates_input(wired):  # noqa: F811
    server, _ = wired
    client = server["client"]
    assert client.post("/api/local/send", json={"text": "  "}).status_code == 400
    assert client.post("/api/local/send", json={"text": "x" * 300}).status_code == 400
    assert client.post("/api/local/send", json={"text": "x", "channel": 9}).status_code == 400
    assert client.post("/api/local/send", json={"text": "x", "dest": "abc"}).status_code == 400


def test_send_without_connection_is_503(server):  # noqa: F811
    assert server["client"].post("/api/local/send", json={"text": "x"}).status_code == 503
