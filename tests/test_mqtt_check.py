"""Testes do teste ao servidor MQTT do nó (core/mqtt_check.py)."""

import socket
from types import SimpleNamespace as NS

from core import mqtt_check


def mqtt(**fields):
    base = dict(enabled=True, proxy_to_client_enabled=False, address="", tls_enabled=False)
    base.update(fields)
    return NS(**base)


def test_broker_follows_the_node_configuration():
    assert mqtt_check.broker_of(mqtt(address="mqtt.meshtastic.pt")) == ("mqtt.meshtastic.pt", 1883)
    assert mqtt_check.broker_of(mqtt(address="mqtt.example.org:1884")) == ("mqtt.example.org", 1884)
    assert mqtt_check.broker_of(mqtt(address="mqtt.example.org", tls_enabled=True)) == ("mqtt.example.org", 8883)
    # Endereço vazio: o firmware usa o servidor público por omissão.
    assert mqtt_check.broker_of(mqtt(address="  ")) == ("mqtt.meshtastic.org", 1883)


def test_nothing_to_check_when_the_node_does_not_connect_itself():
    assert mqtt_check.broker_of(mqtt(enabled=False)) is None
    assert mqtt_check.broker_of(mqtt(proxy_to_client_enabled=True)) is None     # a aplicação liga-se
    assert mqtt_check.broker_of(None) is None


def test_reachable_opens_and_closes_a_connection():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        assert mqtt_check.reachable("127.0.0.1", server.getsockname()[1], timeout=2)
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]          # fechado ao sair do with: ninguém escuta
    assert not mqtt_check.reachable("127.0.0.1", port, timeout=2)
