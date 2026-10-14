"""
server/session.py — A sessão de rede partilhada por todos os browsers.

Há uma só ligação ao nó Meshtastic e N clientes a vê-la. Este módulo é o
ponto onde os sinais do core se tornam eventos Socket.IO e onde vive o estado
que um browser precisa quando abre a página a meio de uma sessão: nós, canais,
nó local, histórico de mensagens, estado da ligação.

A aplicação desktop nunca precisou deste estado consolidado porque nascia com
o worker e morria com ele. Um browser pode entrar tarde, recarregar, ou abrir
num segundo dispositivo, e tem de receber tudo de uma vez.
"""

import base64
import logging
import threading
import time
from collections import deque
from typing import Optional

from core.favorites import FirmwareFavorites
from core.loop import CoreLoop
from core.mesh_worker import MeshtasticWorker
from core.messages import MessageStore, channel_key, dm_key
from core.metrics import SECTIONS, MeshMetrics
from core.node_store import NodeStore, node_record_from_nodedb
from core import config_writer
from core.serialize import to_jsonable
from core.settings import settings
from i18n import get_language, set_language, tr
from server import events as ev
from server.bridge_manager import BridgeManager, list_serial_ports

logger = logging.getLogger("MeshDeck.session")

PACKET_STATS_INTERVAL_MS = 5_000    # ritmo do indicador de SNR/pkt-min
PACKET_RATE_WINDOW_S = 60
LOG_BUFFER_LINES = 500
CONFIG_LOCK_TIMEOUT_S = 120         # um lock abandonado não bloqueia para sempre


class LogStream(logging.Handler):
    """Encaminha o log da aplicação para a consola do browser.

    Guarda as últimas linhas para que um cliente que abra agora veja o que
    já aconteceu, em vez de uma consola vazia.
    """

    def __init__(self, broadcast):
        super().__init__()
        self._broadcast = broadcast
        self._lines = deque(maxlen=LOG_BUFFER_LINES)
        self.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s %(name)s: %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        try:
            line = self.format(record)
        except Exception:
            return
        self._lines.append(line)
        try:
            self._broadcast(ev.LOG, {"line": line})
        except Exception:
            # Nunca deixar o logging rebentar por causa do transporte.
            pass

    def backlog(self) -> list:
        return list(self._lines)

    def attach(self):
        logging.getLogger().addHandler(self)

    def detach(self):
        logging.getLogger().removeHandler(self)


class ConfigLock:
    """Impede dois browsers de gravar configuração ao mesmo tempo.

    O nó aceita uma transacção de cada vez; dois clientes a gravar em paralelo
    produziriam uma mistura das duas configurações e dois reinícios.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._holder: Optional[str] = None
        self._since = 0.0

    def acquire(self, client_id: str) -> bool:
        with self._lock:
            if self._holder and self._holder != client_id:
                if time.time() - self._since < CONFIG_LOCK_TIMEOUT_S:
                    return False
                logger.warning("lock de configuração de %s expirou", self._holder)
            self._holder = client_id
            self._since = time.time()
            return True

    def release(self, client_id: str):
        with self._lock:
            if self._holder == client_id:
                self._holder = None
                self._since = 0.0

    def release_if_held(self, client_id: str):
        self.release(client_id)

    def state(self) -> dict:
        with self._lock:
            return {"held_by": self._holder,
                    "since": self._since or None}


class ConfigUnavailable(Exception):
    """Sem nó ligado e pronto: não há configuração para ler ou gravar."""


class ConfigBusy(Exception):
    """Outro cliente está a gravar configuração neste momento."""


class MeshSession:
    """Estado da rede e ponte entre os sinais do core e o Socket.IO."""

    def __init__(self, broadcast, config):
        """broadcast(event_name, payload) difunde a todos os clientes."""
        self._broadcast = broadcast
        self._config = config

        self.loop = CoreLoop(name="meshdeck-core")
        self.loop.start()

        self.nodes = NodeStore()
        self.metrics = MeshMetrics()
        self.messages = MessageStore()
        self._pending_replies: dict = {}   # (chave, texto) → reply_id até a mensagem sair
        self._reply_lock = threading.Lock()
        self.favorites = FirmwareFavorites()
        self.config_lock = ConfigLock()
        self.bridge = BridgeManager()
        self.log = LogStream(broadcast)
        self.log.attach()

        self.worker: Optional[MeshtasticWorker] = None
        self.connected = False
        self.node_ready = False
        self._iface = None             # interface viva, só para ler/gravar configuração
        self.channels: list = []
        # Lista com PSK, só servida pelo endpoint autenticado do editor.
        self._channels_raw: list = []
        self.local_node: dict = {}
        self.local_position: dict = {}
        self.reconnect: dict = {"attempt": 0, "delay_s": 0}
        self.target: dict = dict(config.last_target)

        self._packet_times: deque = deque()
        self._last_snr = None
        self._stats_timer = self.loop.every(PACKET_STATS_INTERVAL_MS,
                                            self._emit_packet_stats)

    # ------------------------------------------------------------------
    # Ciclo de vida da ligação
    # ------------------------------------------------------------------
    def connect(self, mode: str = "tcp", host: str = "localhost", port: int = 4403,
                serial_port: str = "") -> dict:
        """Liga ao nó. Em modo serial arranca primeiro a ponte USB→TCP."""
        self.disconnect()

        if mode == "serial":
            if not serial_port:
                return {"ok": False, "error": tr("err_no_serial_port")}
            result = self.bridge.start(serial_port)
            if not result["ok"]:
                return result
            # A ponte reexpõe o nó em TCP, por isso a ligação é sempre TCP.
            host, port = self.bridge.host, self.bridge.port

        self.target = {"mode": mode, "host": host, "port": int(port),
                       "serial_port": serial_port}
        self._config.remember_target(mode, host, port, serial_port)

        self.worker = MeshtasticWorker(hostname=host, port=int(port), loop=self.loop)
        self._wire_signals(self.worker)
        self.worker.start()
        logger.info("sessão a ligar a %s:%s (modo %s)", host, port, mode)
        return {"ok": True, "error": ""}

    def disconnect(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
        self.bridge.stop()
        self.connected = False
        self.node_ready = False
        self._iface = None
        self.favorites.set_interface(None)
        self.nodes.clear()
        self.channels = []
        self.local_node = {}
        self.local_position = {}
        self.reconnect = {"attempt": 0, "delay_s": 0}
        self._broadcast(ev.CONNECTION, self._connection_payload())

    def shutdown(self):
        """Paragem ordenada: ponte, worker, loop e handler de log."""
        self.disconnect()
        self._stats_timer.stop()
        self.loop.stop()
        self.log.detach()

    # ------------------------------------------------------------------
    # Ligação dos sinais do core aos eventos Socket.IO
    # ------------------------------------------------------------------
    def _wire_signals(self, worker: MeshtasticWorker):
        worker.connection_changed.connect(self._on_connection_changed)
        worker.reconnect_status.connect(self._on_reconnect_status)
        worker.error_occurred.connect(
            lambda message: self._broadcast(ev.ERROR, ev.error_occurred(message)))

        worker.my_node_id_ready.connect(self._on_my_node_id)
        worker.local_node_ready.connect(self._on_local_node_ready)
        worker.local_position_updated.connect(self._on_local_position)
        worker.interface_ready.connect(self._on_interface_ready)

        worker.nodes_batch.connect(self._on_nodes_batch)
        worker.node_updated.connect(self._on_node_updated)
        worker.nodedb_reset.connect(self._on_nodedb_reset)

        worker.channels_updated.connect(self._on_channels_updated)

        worker.message_received.connect(self._on_message_received)
        worker.channel_message_sent.connect(self._on_channel_message_sent)
        worker.dm_sent.connect(self._on_dm_sent)
        worker.message_status_updated.connect(self._on_message_status)

        worker.traceroute_result.connect(self._on_traceroute_result)
        worker.neighbor_info_received.connect(self._on_neighbor_info)
        worker.position_sent.connect(
            lambda ok, message: self._broadcast(ev.POSITION_SENT,
                                                ev.position_sent(ok, message)))
        worker.raw_packet_received.connect(self._on_raw_packet)

    # -- ligação ----------------------------------------------------------
    def _connection_payload(self) -> dict:
        return {"connected": self.connected, "target": dict(self.target),
                "bridge": self.bridge.status(), "node_ready": self.node_ready}

    def _on_connection_changed(self, connected: bool):
        self.connected = bool(connected)
        if not connected:
            self.node_ready = False
            self._iface = None
        elif self.target.get("mode") == "tcp":
            # Só ligações TCP directas: em serial o host é a ponte local.
            self._config.record_target(self.target["host"], self.target["port"])
        self._broadcast(ev.CONNECTION, self._connection_payload())

    def _on_reconnect_status(self, attempt: int, delay_s: int):
        self.reconnect = ev.reconnect_status(attempt, delay_s)
        self._broadcast(ev.RECONNECT, self.reconnect)

    def _on_interface_ready(self, iface):
        # O objecto da interface nunca vai para o browser; só a indicação de
        # que a configuração do nó já pode ser lida.
        self.favorites.set_interface(iface)
        self._iface = iface
        self.node_ready = True
        self._broadcast(ev.NODE_READY, {"ready": True,
                                        "favorites": sorted(self.favorites.get_all())})

    # -- nó local ---------------------------------------------------------
    def _on_my_node_id(self, node_id: str):
        self.nodes.set_local_node(node_id)
        self.messages.set_local_node(node_id)
        self.metrics.set_local_node_id(node_id)
        self._broadcast(ev.MY_NODE_ID, ev.my_node_id_ready(node_id))

    def _on_local_node_ready(self, long_name, short_name, node_id,
                             gps_enabled, has_position):
        self.local_node = ev.local_node_ready(long_name, short_name, node_id,
                                              gps_enabled, has_position)
        self._broadcast(ev.LOCAL_NODE, self.local_node)
        if self.target.get("mode") == "tcp" and long_name:
            # O nome do nó distingue as entradas da lista melhor que o IP.
            self._config.record_target(self.target["host"], self.target["port"],
                                       name=f"{long_name} [{short_name}]" if short_name else long_name)

    def _on_local_position(self, lat, lon, alt, found):
        self.local_position = ev.local_position_updated(lat, lon, alt, found)
        # O alcance directo mede a partir daqui (Métricas → Alcance & Links).
        if found and self.metrics._local_nid:
            self.metrics.ingest_node_position(self.metrics._local_nid, lat, lon)
        self._broadcast(ev.LOCAL_POSITION, self.local_position)

    # -- nós --------------------------------------------------------------
    def _on_nodes_batch(self, batch: list):
        self.nodes.apply_batch(batch)
        # Envia-se o lote inteiro: o cliente substitui as linhas que conhece e
        # acrescenta as novas, sem precisar de distinguir umas das outras.
        records = []
        for num, node in batch:
            parsed = node_record_from_nodedb(num, node)
            if parsed is None:
                continue
            nid, data = parsed
            # Posições, bateria e nomes conhecidos da NodeDB: as métricas
            # (alcance directo, retransmissores) não esperam por pacotes novos.
            self.metrics.ingest_packet(None, data, nid)
            record = self.nodes.get(nid)
            if record is not None:
                records.append(self._node_payload(record))
        if records:
            self._broadcast(ev.NODES, {"nodes": records, "counts": self._counts()})

    def _on_node_updated(self, node_id: str, updates: dict, packet):
        self.nodes.update(node_id, updates, packet)
        # Com pacote, o raw_packet_received já o contou nas métricas; aqui só
        # entram as actualizações sem pacote (NodeDB, leitura do nó local).
        # Como no desktop (main.py, _on_node_updated_metrics).
        if packet is None:
            self.metrics.ingest_packet(None, updates, node_id)
        lat, lon = updates.get("latitude"), updates.get("longitude")
        if lat is not None and lon is not None:
            self.metrics.ingest_node_position(node_id, lat, lon)
        record = self.nodes.get(node_id)
        if record is not None:
            self._broadcast(ev.NODES, {"nodes": [self._node_payload(record)],
                                       "counts": self._counts()})

    def _on_nodedb_reset(self):
        self.nodes.clear()
        self._broadcast(ev.NODEDB_RESET, {"counts": self._counts()})

    def _node_payload(self, record: dict) -> dict:
        """A forma estável do NodeStore, mais o estado de favorito."""
        item = self.nodes.payload(record)
        item["favorite"] = self.favorites.is_favorite(record.get("id_string", ""))
        return item

    def _counts(self) -> dict:
        return {"total": self.nodes.count(),
                "contacted": self.nodes.contacted_count(),
                "online": self.nodes.online_count()}

    # -- canais -----------------------------------------------------------
    def _on_channels_updated(self, channels: list):
        # Guarda-se a lista com PSK para o editor de canais, mas só o índice e
        # o nome vão no evento; a chave é servida pelo endpoint autenticado.
        self._channels_raw = channels
        self.channels = ev.channels_updated(channels)["channels"]
        self._broadcast(ev.CHANNELS, {"channels": self.channels})

    def channels_with_psk(self) -> list:
        """Canais com a PSK em base64, para o editor. Só via HTTP autenticado."""
        out = []
        for idx, name, psk in self._channels_raw:
            encoded = base64.b64encode(psk).decode() if isinstance(psk, bytes) else ""
            out.append({"index": idx, "name": name, "psk": encoded})
        return out

    # -- mensagens --------------------------------------------------------
    def _on_message_received(self, channel: int, from_id: str, text: str, packet):
        stored = self.messages.add_incoming(channel, from_id, text, packet or {})
        if stored is None:
            return
        key, entry = stored
        self._broadcast(ev.MESSAGE, {"key": key, "message": to_jsonable(entry),
                                     "unread": self.messages.unread()})

    def mark_read(self, key: str):
        """Lida num cliente é lida em todos: o contador vive no servidor,
        e é ele que o reenvia a cada mensagem nova e em cada snapshot."""
        self.messages.mark_read(key)
        self._broadcast(ev.UNREAD, {"unread": self.messages.unread()})

    def _on_channel_message_sent(self, channel: int, text: str, packet_id: int):
        key, entry = self.messages.add_outgoing_channel(
            channel, text, packet_id, self._take_reply(channel_key(channel), text))
        self.metrics.ingest_message_sent(packet_id, kind="channel")
        self._broadcast(ev.MESSAGE_SENT, {"key": key, "message": to_jsonable(entry)})

    def _on_dm_sent(self, dest_id: str, text: str, pki: bool, packet_id: int):
        key, entry = self.messages.add_outgoing_dm(
            dest_id, text, pki, packet_id, self._take_reply(dm_key(dest_id), text))
        self.metrics.ingest_message_sent(packet_id, kind="dm")
        self._broadcast(ev.MESSAGE_SENT, {"key": key, "message": to_jsonable(entry)})

    def _on_message_status(self, packet_id: int, status: str, error_reason: str):
        self.metrics.ingest_message_status(packet_id, status)
        entry = self.messages.update_status(packet_id, status, error_reason)
        payload = ev.message_status_updated(packet_id, status, error_reason)
        if entry is not None:
            payload["message"] = to_jsonable(entry)
        self._broadcast(ev.MESSAGE_STATUS, payload)

    # -- traceroute e vizinhança ------------------------------------------
    def _on_traceroute_result(self, forward, back, origin, dest):
        payload = ev.traceroute_result(forward, back, origin, dest)
        payload["time"] = time.time()
        self._broadcast(ev.TRACEROUTE, payload)

    def _on_neighbor_info(self, from_id: str, neighbors: list):
        self.metrics.ingest_neighbor_info(from_id, neighbors)
        self._broadcast(ev.NEIGHBORS, ev.neighbor_info_received(from_id, neighbors))

    # -- pacotes ----------------------------------------------------------
    def _on_raw_packet(self, packet: dict):
        self.metrics.ingest_raw_packet(packet)
        now = time.time()
        self._packet_times.append(now)
        snr = packet.get("rxSnr")
        if snr is not None:
            self._last_snr = snr
        self._broadcast(ev.PACKET, ev.packet_projection(packet))

    def _emit_packet_stats(self):
        """Difunde SNR e pacotes/min a ritmo fixo.

        No desktop isto era recalculado a cada pacote; numa rede movimentada
        seria uma mensagem Socket.IO por pacote só para mexer um rótulo.
        """
        cutoff = time.time() - PACKET_RATE_WINDOW_S
        while self._packet_times and self._packet_times[0] < cutoff:
            self._packet_times.popleft()
        self._broadcast(ev.PACKET_STATS, self.packet_stats())

    def packet_stats(self) -> dict:
        return {"snr": self._last_snr, "packets_per_min": len(self._packet_times)}

    # ------------------------------------------------------------------
    # Snapshot para um cliente que acaba de ligar
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "language":       get_language(),
            "connection":     self._connection_payload(),
            "reconnect":      dict(self.reconnect),
            "nodes":          [self._node_payload(n) for n in self.nodes.all()],
            "counts":         self._counts(),
            "local_node":     dict(self.local_node),
            "local_position": dict(self.local_position),
            "channels":       list(self.channels),
            "messages":       self.messages.snapshot(),
            "favorites":      sorted(self.favorites.get_all()),
            "packet_stats":   self.packet_stats(),
            "metric_sections": [{"key": key, "label": tr(label)}
                                for key, label in SECTIONS],
            "config_lock":    self.config_lock.state(),
            "serial_ports":   list_serial_ports(),
            "log":            self.log.backlog(),
        }

    # ------------------------------------------------------------------
    # Comandos vindos do browser
    # ------------------------------------------------------------------
    def _require_worker(self) -> Optional[MeshtasticWorker]:
        if self.worker is None or not self.connected:
            self._broadcast(ev.ERROR, ev.error_occurred(tr("err_send_msg")))
            return None
        return self.worker

    def send_channel_message(self, channel: int, text: str, reply_id: int = 0):
        worker = self._require_worker()
        if worker:
            self._remember_reply(channel_key(int(channel)), text, reply_id)
            worker.send_message(int(channel), text, int(reply_id or 0))

    def send_dm(self, dest_id: str, text: str, reply_id: int = 0):
        worker = self._require_worker()
        if worker:
            self._remember_reply(dm_key(dest_id), text, reply_id)
            worker.send_direct_message(dest_id, text, int(reply_id or 0))

    def _remember_reply(self, key: str, text: str, reply_id) -> None:
        """Os sinais de "enviada" do worker (partilhados com o desktop) não
        trazem o reply_id; guarda-se aqui até a mensagem voltar como enviada."""
        if reply_id:
            with self._reply_lock:
                self._pending_replies[(key, text)] = int(reply_id)

    def _take_reply(self, key: str, text: str) -> int:
        with self._reply_lock:
            return self._pending_replies.pop((key, text), 0)

    def send_traceroute(self, dest_id: str, hop_limit: int = 3):
        worker = self._require_worker()
        if worker:
            worker.send_traceroute(dest_id, int(hop_limit))

    def send_position(self):
        worker = self._require_worker()
        if worker:
            worker.send_position()

    def send_node_info(self):
        worker = self._require_worker()
        if worker:
            worker.send_node_info()

    def refresh_local_position(self):
        worker = self._require_worker()
        if worker:
            worker.refresh_local_position()

    def reset_nodedb(self):
        worker = self._require_worker()
        if worker:
            worker.reset_nodedb()

    def toggle_favorite(self, node_id: str) -> dict:
        record = self.nodes.get(node_id)
        now_favorite = self.favorites.toggle(node_id, record)
        # A lista vai a todos os clientes: é ela que decide a estrela e a
        # ordenação (favoritos no topo), não o registo do nó.
        self._broadcast(ev.FAVORITES, {"favorites": sorted(self.favorites.get_all())})
        if record is not None:
            self._broadcast(ev.NODES, {"nodes": [self._node_payload(record)],
                                       "counts": self._counts()})
        return {"node_id": node_id, "favorite": now_favorite}

    def set_language(self, language: str) -> dict:
        set_language(language)
        # Gravado na mesma chave que o desktop e o run_server.py lêem no arranque.
        settings().set_value("language", get_language())
        # As etiquetas das secções de métricas e os textos do HTML gerado
        # dependem do idioma activo, por isso o cliente recarrega-as.
        return {"language": get_language(),
                "metric_sections": [{"key": key, "label": tr(label)}
                                    for key, label in SECTIONS]}

    # -- configuração do nó -------------------------------------------------
    def _config_iface(self):
        if not (self.connected and self.node_ready and self._iface is not None):
            raise ConfigUnavailable()
        return self._iface

    def config_values(self) -> dict:
        iface = self._config_iface()
        values = config_writer.read_config(iface)
        values["channels"] = config_writer.read_channels(iface)
        return values

    def _with_config_lock(self, client_id: str, action):
        if not self.config_lock.acquire(client_id):
            raise ConfigBusy()
        self._broadcast(ev.CONFIG_LOCK, self.config_lock.state())
        try:
            return action()
        finally:
            self.config_lock.release(client_id)
            self._broadcast(ev.CONFIG_LOCK, self.config_lock.state())

    def save_config(self, client_id: str, sections: dict, owner: dict) -> dict:
        iface = self._config_iface()
        result = self._with_config_lock(
            client_id, lambda: config_writer.save_config(iface, sections, owner))
        logger.info("configuração gravada: %s", result)
        return result

    def save_channels(self, client_id: str, channels: list) -> dict:
        iface = self._config_iface()
        result = self._with_config_lock(
            client_id, lambda: config_writer.save_channels(iface, channels))
        logger.info("canais gravados: %s", result)
        return result

    def clear_metrics(self):
        self.metrics.clear()

    def metrics_html(self, section: str) -> str:
        return self.metrics.html_for(section)

    def metrics_data(self, section: str) -> dict:
        return self.metrics.data_for(section)

    def metrics_refresh(self, section: str) -> dict:
        return self.metrics.refresh_state(section)

    def node_packet(self, node_id: str):
        return self.nodes.packet_snapshot(node_id)
