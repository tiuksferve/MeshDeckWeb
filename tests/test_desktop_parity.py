"""
Paridade da aplicação desktop sobre o núcleo novo.

Constrói a MainWindow real, injecta uma interface Meshtastic falsa no worker
e verifica que os dados chegam à tabela de nós, às mensagens e às métricas.
É a verificação de que o port não quebrou nenhuma das ligações de sinais
feitas em _init_worker().

Não abre janelas (nunca chama show()) nem precisa de hardware Meshtastic.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from PyQt5.QtWidgets import QApplication

from tests.fake_meshtastic import (
    LOCAL_ID, LOCAL_NUM, FakeInterface, emit_connection_established, emit_packet,
    emit_text, packet,
)

PEER_NUM = 0x11110001
PEER_ID = "!11110001"

# Porta sem nada à escuta, para a ligação TCP real falhar sempre e o teste
# nunca tocar num meshtasticd verdadeiro.
UNUSED_PORT = 45999


@pytest.fixture(scope="module")
def window(qapp):
    """Uma só MainWindow para todos os testes, já ligada à interface falsa.

    O âmbito é de módulo porque o QWebEngine aborta quando se constroem e
    destroem várias QWebEngineView no mesmo processo. Os testes acrescentam
    pacotes ao mesmo estado em vez de recriarem a janela.
    """
    import main as main_module

    # Três métodos da MainWindow abrem diálogos modais que bloqueariam o teste:
    # o diálogo de ligação no arranque, o QMessageBox de erro (a ligação TCP
    # real a localhost:4403 falha neste ambiente) e o diálogo de resultado do
    # traceroute. Nenhum é o que está em teste — o que interessa é se os dados
    # chegam aos modelos — por isso são substituídos por registadores.
    errors = []
    patches = {
        "_open_connection_dialog": lambda self: None,
        "_on_worker_error": lambda self, message: errors.append(message),
    }
    originals = {name: getattr(main_module.MainWindow, name) for name in patches}
    for name, fn in patches.items():
        setattr(main_module.MainWindow, name, fn)

    win = main_module.MainWindow()
    # Porta fechada de propósito: se houver um meshtasticd a correr na 4403, o
    # teste ligava-se ao nó real do utilizador em vez da falsificação.
    win._hostname, win._port = "127.0.0.1", UNUSED_PORT
    win._init_worker()
    win.captured_errors = errors

    # _init_worker() chama worker.start(), que corre no CoreLoop e desiste se
    # a iface já estiver definida. Garante-se a subscrição pubsub antes de
    # injectar a falsificação, para não depender dessa corrida.
    win.worker._core._subscribe_pubsub()

    iface = FakeInterface(nodes_by_num={
        PEER_NUM: {
            "num": PEER_NUM,
            "user": {"id": PEER_ID, "longName": "Peer One",
                     "shortName": "P1", "hwModel": "HELTEC_V3"},
            "hopsAway": 2,
            "lastHeard": int(time.time()),
            "position": {"latitudeI": 400000000, "longitudeI": -85000000, "altitude": 150},
            "deviceMetrics": {"batteryLevel": 64},
            "isFavorite": True,
        },
    })
    win.worker._core.iface = iface

    emit_connection_established(iface)
    assert pump(lambda: win.source_model.get_node_count() >= 2), \
        f"a tabela ficou com {win.source_model.get_node_count()} nós"

    yield win, iface

    win.worker.stop()
    # A ConsoleWindow instala um handler no logger raiz; sem o retirar, a
    # primeira linha de log após a destruição da janela segfaulta.
    win._console_window.detach()
    from models import _FAVORITES
    _FAVORITES.set_interface(None)
    # Os patches têm de durar toda a vida do fixture: o diálogo de ligação é
    # agendado com singleShot(100) e dispararia depois da construção.
    for name, fn in originals.items():
        setattr(main_module.MainWindow, name, fn)


def pump(predicate, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return predicate()


def test_window_builds_all_tabs(window):
    win, _iface = window
    assert win.tab_widget.count() == 6


def test_connection_populates_the_node_table(window):
    win, _iface = window
    ids = {n.get("id_string") for n in win.source_model.get_all_nodes()}
    assert PEER_ID in ids
    assert LOCAL_ID in ids


def test_local_node_is_registered_before_the_batch(window):
    win, _iface = window
    assert pump(lambda: win.source_model._local_node_id == LOCAL_ID)
    assert win.proxy_model._local_node_id == LOCAL_ID


def test_channels_reach_the_messages_tab(window):
    win, _iface = window
    assert pump(lambda: win.messages_tab.channel_list.count() >= 2)


def test_connection_status_reaches_the_ui(window):
    win, _iface = window
    assert pump(lambda: win.worker._connected)
    assert pump(lambda: "🟢" in win.conn_indicator.text())


def test_packet_updates_node_and_metrics(window):
    win, iface = window
    before = len(win.metrics_tab._packets)
    emit_packet(packet("TELEMETRY_APP", PEER_NUM, rx_snr=8.0, decoded={
        "telemetry": {"deviceMetrics": {"batteryLevel": 42, "voltage": 3.9}}}), iface)

    assert pump(lambda: len(win.metrics_tab._packets) > before), \
        "o pacote não chegou às métricas"
    node = win.source_model.get_node_data(
        win.source_model._node_index[PEER_ID])
    assert pump(lambda: node.get("battery_level") == 42)
    assert node["snr"] == 8.0


def test_text_message_reaches_the_messages_tab(window):
    win, iface = window
    emit_text(packet("TEXT_MESSAGE_APP", PEER_NUM, channel=0,
                     decoded={"text": "olá do teste"}), iface)

    def arrived():
        return any("olá do teste" in m.get("text", "")
                   for msgs in win.messages_tab.messages.values()
                   for m in msgs)

    assert pump(arrived), "a mensagem não chegou à aba de mensagens"


def test_traceroute_result_reaches_qt(window):
    """O slot da MainWindow abre um diálogo modal, por isso o teste observa o
    sinal directamente: o que se verifica é a travessia core -> Qt."""
    win, iface = window
    results = []
    win.worker.traceroute_result.disconnect(win._on_traceroute_result)
    win.worker.traceroute_result.connect(
        lambda fwd, back, origin, dest: results.append((fwd, back, origin, dest)))
    try:
        emit_packet(packet("TRACEROUTE_APP", PEER_NUM, to=LOCAL_NUM, decoded={
            "traceroute": {"route": [], "snrTowards": [20], "routeBack": [], "snrBack": [16]},
        }), iface)
        assert pump(lambda: results), "o traceroute não chegou ao Qt"
        forward, back, origin, dest = results[0]
        assert origin == LOCAL_ID and dest == PEER_ID
        assert forward == [(LOCAL_ID, PEER_ID, 5.0)]     # 20 quartos de dB = 5 dB
        assert back == [(PEER_ID, LOCAL_ID, 4.0)]
    finally:
        win.worker.traceroute_result.connect(win._on_traceroute_result)


def test_neighbor_info_reaches_map_and_metrics(window):
    win, iface = window
    emit_packet(packet("NEIGHBORINFO_APP", PEER_NUM, decoded={
        "neighborinfo": {"neighbors": [{"nodeId": LOCAL_NUM, "snr": 6.0}]},
    }), iface)

    assert pump(lambda: win.map_widget._nb_links), \
        "a vizinhança não chegou ao mapa"
    assert win.map_widget._nb_links[PEER_ID] == [(LOCAL_ID, 6.0)]


def test_favorites_sync_from_firmware(window):
    """isFavorite vem do firmware; não há ficheiro local de favoritos."""
    _win, _iface = window
    from models import _FAVORITES
    assert pump(lambda: _FAVORITES.is_favorite(PEER_ID))


def test_sending_a_message_from_the_ui(window):
    win, iface = window
    win._on_send_channel_message(0, "enviado pela UI")
    assert pump(lambda: iface.sent_text)
    assert iface.sent_text[0]["text"] == "enviado pela UI"


def test_polling_timer_uses_the_core_sync(window):
    win, _iface = window
    batches = []
    win.worker.nodes_batch.connect(lambda b: batches.append(b))
    win._poll_nodedb()
    assert pump(lambda: batches), "o polling de segurança não emitiu nodes_batch"


def test_error_signal_reaches_the_handler(window):
    """Erros do core têm de chegar ao handler da UI.

    Emite-se o sinal directamente em vez de provocar uma falha de ligação: o
    start() do core é assíncrono e desiste se a iface já estiver injectada, o
    que tornaria o teste dependente dessa corrida.
    """
    win, _iface = window
    core = win.worker._core
    core._emit(core.error_occurred, "falha de teste")
    assert pump(lambda: "falha de teste" in win.captured_errors)
