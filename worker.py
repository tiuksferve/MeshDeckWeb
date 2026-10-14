"""
worker.py — Adaptador Qt para core.mesh_worker.MeshtasticWorker.

Toda a lógica (pubsub, processamento de pacotes, reconexão com backoff e
watchdog, sincronização da NodeDB, envio de mensagens, traceroute e posição)
vive em core/mesh_worker.py, sem qualquer dependência do Qt, para o servidor
web poder usá-la. Esta classe envolve-a num QObject e reemite cada Signal do
core como pyqtSignal, de modo a que a MainWindow continue a ligar-se com
.connect() exactamente como antes.

Threads: os sinais do core são entregues na thread do CoreLoop, que não é a
da GUI. Reemitir um pyqtSignal a partir de outra thread é seguro — o Qt
promove a ligação a QueuedConnection e o slot corre na thread da GUI. É a
mesma garantia que existia antes, quando os handlers pubsub emitiam da thread
do leitor TCP.
"""

from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

from constants import logger
from core.loop import CoreLoop
from core.mesh_worker import MeshtasticWorker as CoreWorker

# Nome do sinal no core -> assinatura do pyqtSignal correspondente.
# A ordem e os tipos replicam as declarações originais, para os slots da
# MainWindow não precisarem de alteração.
_SIGNAL_TYPES = {
    "connection_changed":     (bool,),
    "node_updated":           (str, dict, object),
    "error_occurred":         (str,),
    "message_received":       (int, str, str, object),
    "channels_updated":       (list,),
    "my_node_id_ready":       (str,),
    "interface_ready":        (object,),
    "dm_sent":                (str, str, bool, int),
    "channel_message_sent":   (int, str, int),
    "message_status_updated": (int, str, str),
    "traceroute_result":      (list, list, str, str),
    "local_node_ready":       (str, str, str, bool, bool),
    "nodes_batch":            (list,),
    "nodedb_reset":           (),
    "neighbor_info_received": (str, list),
    "position_sent":          (bool, str),
    "local_position_updated": (float, float, object, bool),
    "raw_packet_received":    (dict,),
    "reconnect_status":       (int, int),
}


class MeshtasticWorker(QObject):
    """QObject que reexpõe o worker do core com sinais Qt."""

    connection_changed      = pyqtSignal(*_SIGNAL_TYPES["connection_changed"])
    node_updated            = pyqtSignal(*_SIGNAL_TYPES["node_updated"])
    error_occurred          = pyqtSignal(*_SIGNAL_TYPES["error_occurred"])
    message_received        = pyqtSignal(*_SIGNAL_TYPES["message_received"])
    channels_updated        = pyqtSignal(*_SIGNAL_TYPES["channels_updated"])
    my_node_id_ready        = pyqtSignal(*_SIGNAL_TYPES["my_node_id_ready"])
    interface_ready         = pyqtSignal(*_SIGNAL_TYPES["interface_ready"])
    dm_sent                 = pyqtSignal(*_SIGNAL_TYPES["dm_sent"])
    channel_message_sent    = pyqtSignal(*_SIGNAL_TYPES["channel_message_sent"])
    message_status_updated  = pyqtSignal(*_SIGNAL_TYPES["message_status_updated"])
    traceroute_result       = pyqtSignal(*_SIGNAL_TYPES["traceroute_result"])
    local_node_ready        = pyqtSignal(*_SIGNAL_TYPES["local_node_ready"])
    nodes_batch             = pyqtSignal(*_SIGNAL_TYPES["nodes_batch"])
    nodedb_reset            = pyqtSignal()
    neighbor_info_received  = pyqtSignal(*_SIGNAL_TYPES["neighbor_info_received"])
    position_sent           = pyqtSignal(*_SIGNAL_TYPES["position_sent"])
    local_position_updated  = pyqtSignal(*_SIGNAL_TYPES["local_position_updated"])
    raw_packet_received     = pyqtSignal(*_SIGNAL_TYPES["raw_packet_received"])
    reconnect_status        = pyqtSignal(*_SIGNAL_TYPES["reconnect_status"])

    NODE_POLL_INTERVAL_MS = CoreWorker.NODE_POLL_INTERVAL_MS

    def __init__(self, hostname="localhost", port=4403, serial_port=None, parent=None):
        super().__init__(parent)
        # Um loop por worker: a MainWindow cria um worker novo a cada ligação
        # e descarta o anterior, por isso a thread acompanha esse ciclo de vida.
        self._loop = CoreLoop(name="meshdeck-core")
        self._loop.start()
        self._core = CoreWorker(hostname=hostname, port=port,
                                serial_port=serial_port, loop=self._loop)
        self._bridge_signals()

    def _bridge_signals(self):
        """Liga cada Signal do core ao pyqtSignal homónimo."""
        for name in _SIGNAL_TYPES:
            core_signal = getattr(self._core, name)
            core_signal.connect(getattr(self, name).emit)

    # -- estado lido pela MainWindow --------------------------------------
    @property
    def iface(self):
        return self._core.iface

    @property
    def _connected(self) -> bool:
        return self._core.is_connected

    @property
    def hostname(self) -> str:
        return self._core.hostname

    @property
    def port(self) -> int:
        return self._core.port

    @property
    def serial_port(self) -> Optional[str]:
        return self._core.serial_port

    # -- ciclo de vida ----------------------------------------------------
    def start(self):
        self._core.start()

    def stop(self):
        self._core.stop()
        # O stop() do loop drena o que está na fila, por isso o
        # connection_changed(False) emitido acima ainda chega à GUI.
        self._loop.stop()

    # -- comandos ---------------------------------------------------------
    def send_message(self, channel_index: int, text: str, reply_id: int = 0):
        self._core.send_message(channel_index, text, reply_id)

    def send_direct_message(self, dest_id: str, text: str, reply_id: int = 0):
        self._core.send_direct_message(dest_id, text, reply_id)

    def send_traceroute(self, dest_id: str, hop_limit: int = 3):
        self._core.send_traceroute(dest_id, hop_limit)

    def send_position(self):
        self._core.send_position()

    def send_node_info(self):
        self._core.send_node_info()

    def refresh_local_position(self):
        self._core.refresh_local_position()

    def reset_nodedb(self):
        self._core.reset_nodedb()

    # -- usados pelos timers de polling da MainWindow ---------------------
    def _sync_nodedb(self):
        return self._core._sync_nodedb()

    def _attempt_load_channels(self, retry_count=0):
        self._core._attempt_load_channels(retry_count)
