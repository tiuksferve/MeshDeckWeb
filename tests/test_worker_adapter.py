"""
Testes do adaptador Qt em worker.py.

Confirmam que cada Signal do core chega como pyqtSignal e que os slots
correm na thread da GUI, que é a garantia de que a aplicação desktop pode
continuar a correr sobre o núcleo novo sem alterações.
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from PyQt5.QtWidgets import QApplication

from tests.fake_meshtastic import (
    LOCAL_ID, FakeInterface, emit_connection_established, emit_packet, packet,
)

PEER_NUM = 0x11110001
PEER_ID = "!11110001"


@pytest.fixture
def adapter(qapp):
    from worker import MeshtasticWorker
    w = MeshtasticWorker(hostname="localhost", port=4403)
    yield w
    w.stop()


def pump(predicate, timeout=4.0):
    """Corre o ciclo de eventos Qt até a condição ser verdadeira.

    A QApplication vem do fixture de sessão em conftest.py.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    QApplication.processEvents()
    return predicate()


def test_adapter_declares_every_core_signal(adapter):
    from core.signals import Signal
    core_signals = {n for n, v in vars(adapter._core).items() if isinstance(v, Signal)}
    assert core_signals, "o core devia expor sinais"
    for name in core_signals:
        assert hasattr(adapter, name), f"adaptador sem o sinal {name}"


def test_packet_reaches_a_qt_slot(adapter):
    iface = FakeInterface()
    adapter._core._subscribe_pubsub()
    adapter._core.iface = iface
    adapter._core._connected = True
    try:
        seen = []
        adapter.node_updated.connect(lambda nid, data, pkt: seen.append((nid, data)))

        emit_packet(packet("POSITION_APP", PEER_NUM, rx_snr=5.0, decoded={
            "position": {"latitudeI": 385000000, "longitudeI": -91000000}}), iface)

        assert pump(lambda: seen), "o pyqtSignal não chegou"
        nid, data = seen[0]
        assert nid == PEER_ID
        assert data["latitude"] == pytest.approx(38.5)
        assert data["snr"] == 5.0
    finally:
        adapter._core._unsubscribe_pubsub()


def test_slots_run_on_the_gui_thread(adapter):
    """O core emite na sua própria thread; o Qt tem de reenviar para a GUI."""
    iface = FakeInterface()
    adapter._core._subscribe_pubsub()
    adapter._core.iface = iface
    adapter._core._connected = True
    try:
        main_thread = threading.current_thread()
        threads = []
        adapter.node_updated.connect(
            lambda *a: threads.append(threading.current_thread()))

        for i in range(10):
            emit_packet(packet("POSITION_APP", PEER_NUM, decoded={
                "position": {"latitudeI": 385000000 + i, "longitudeI": -91000000}}), iface)

        assert pump(lambda: len(threads) == 10)
        assert set(threads) == {main_thread}
    finally:
        adapter._core._unsubscribe_pubsub()


def test_connection_sequence_through_the_adapter(adapter):
    iface = FakeInterface()
    adapter._core._subscribe_pubsub()
    adapter._core.iface = iface
    try:
        events = []
        adapter.connection_changed.connect(lambda c: events.append(("conn", c)))
        adapter.my_node_id_ready.connect(lambda nid: events.append(("id", nid)))
        adapter.nodes_batch.connect(lambda b: events.append(("batch", len(b))))
        adapter.local_node_ready.connect(lambda *a: events.append(("local", a[2])))
        adapter.channels_updated.connect(lambda ch: events.append(("channels", len(ch))))

        emit_connection_established(iface)
        assert pump(lambda: len(events) >= 5), f"só chegaram: {events}"

        kinds = [k for k, _ in events]
        assert kinds.index("id") < kinds.index("batch")
        assert ("conn", True) in events
        assert ("id", LOCAL_ID) in events
        assert adapter._connected is True
        assert adapter.iface is iface
    finally:
        adapter._core._unsubscribe_pubsub()


def test_commands_forward_to_the_core(adapter):
    iface = FakeInterface()
    adapter._core.iface = iface
    adapter._core._connected = True

    sent = []
    adapter.channel_message_sent.connect(lambda *a: sent.append(a))
    adapter.send_message(1, "através do adaptador")
    assert pump(lambda: sent)
    assert sent[0][:2] == (1, "através do adaptador")
    assert iface.sent_text[0]["channelIndex"] == 1


def test_stop_delivers_the_final_disconnect(adapter):
    """O stop() drena a fila do loop, por isso o último sinal não se perde."""
    iface = FakeInterface()
    adapter._core.iface = iface
    adapter._core._connected = True

    events = []
    adapter.connection_changed.connect(lambda c: events.append(c))
    adapter.stop()
    assert pump(lambda: events)
    assert events[-1] is False


def test_properties_mirror_the_core(adapter):
    assert adapter.hostname == "localhost"
    assert adapter.port == 4403
    assert adapter.serial_port is None
    assert adapter.NODE_POLL_INTERVAL_MS == adapter._core.NODE_POLL_INTERVAL_MS
