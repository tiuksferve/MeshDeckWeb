"""
core/mesh_worker.py — Ligação ao nó Meshtastic, sem dependências de interface.

Port de worker.py. A lógica (subscrição pubsub, processamento de pacotes,
reconexão com backoff e watchdog, sincronização da NodeDB, envio de mensagens,
traceroute e posição) é a mesma; só o acoplamento ao Qt foi substituído:

    pyqtSignal                                 -> core.signals.Signal
    QTimer                                     -> core.loop.Timer
    QTimer.singleShot                          -> loop.single_shot
    QMetaObject.invokeMethod(..., QueuedConn.) -> loop.post / @on_loop

Afinidade de thread: os handlers pubsub são invocados em threads internas da
biblioteca meshtastic. Todos os sinais são entregues na thread do CoreLoop e
todos os comandos públicos são encaminhados para lá — o equivalente ao
Qt.AutoConnection que o código original usava. Nenhum callback corre em
paralelo com outro, que é a garantia de que o estado interno depende.
"""
import base64
import functools
import threading
import time
from typing import Optional, Dict, Any, Set
from datetime import datetime

from pubsub import pub
from meshtastic.tcp_interface import TCPInterface
from meshtastic.serial_interface import SerialInterface
from meshtastic import BROADCAST_NUM
from meshtastic.protobuf import mesh_pb2, portnums_pb2, admin_pb2

from constants import logger, _is_broadcast
from core.loop import CoreLoop, default_loop, on_loop
from core.signals import Signal
from i18n import tr


def _clean_text(value):
    """Remove surrogates Unicode soltos de texto livre vindo do firmware.

    Firmware ou hardware danificado por vezes envia bytes que não são UTF-8
    válido no longName/shortName de um nó; a biblioteca meshtastic decodifica-os
    de forma permissiva e o resultado fica com surrogates sem par — válidos
    como str do Python, mas que nenhuma codificação UTF-8 estrita consegue
    serializar (JSON em texto simples, HTML, ou um novo pacote protobuf).
    Isso rebentava bem mais tarde e longe da origem, ex: "/api/metrics/<secção>"
    como 500 Internal Server Error. Limpo aqui, assim que o texto entra no
    sistema, substituindo o que não é representável por "�" em vez de o deixar
    passar ou apagar o campo todo.
    """
    if not isinstance(value, str) or not value:
        return value
    return value.encode("utf-8", errors="replace").decode("utf-8")


class MeshtasticWorker:
    """Publica tudo o que acontece na rede Meshtastic através de Signals."""

    NODE_POLL_INTERVAL_MS = 30_000   # 30s — safety-net; pubsub cobre updates em tempo real

    # Tópicos pubsub da biblioteca meshtastic e os handlers respectivos.
    # Lista única: start(), stop() e _do_reconnect() subscrevem e cancelam a
    # partir daqui, em vez de repetirem os seis pares três vezes.
    def _pubsub_bindings(self):
        return [
            ("meshtastic.connection.established", self._on_connection_established),
            ("meshtastic.connection.lost",        self._on_connection_lost),
            ("meshtastic.node.updated",           self._on_node_updated),
            ("meshtastic.receive.text",           self._on_text_message),
            ("meshtastic.receive.user",           self._on_receive_user),
            ("meshtastic.receive",                self._on_packet_received),
        ]

    def _subscribe_pubsub(self):
        for topic, handler in self._pubsub_bindings():
            pub.subscribe(handler, topic)

    def _unsubscribe_pubsub(self):
        for topic, handler in self._pubsub_bindings():
            try:
                pub.unsubscribe(handler, topic)
            except Exception:
                pass

    def __init__(self, hostname="localhost", port=4403, serial_port=None,
                 loop: CoreLoop = None):
        self._loop = loop or default_loop()

        self.hostname    = hostname
        self.port        = port
        self.serial_port = serial_port   # if set, use SerialInterface instead of TCP
        self.iface: Optional[TCPInterface] = None
        self._connected = False
        self._known_nodes: Set[str] = set()
        self._channels:    Dict[int, tuple] = {}
        self._reconnect_attempts = 0

        # ── Sinais ───────────────────────────────────────────────────────────
        self.connection_changed     = Signal("connection_changed")      # (connected: bool)
        self.node_updated           = Signal("node_updated")            # (node_id: str, updates: dict, packet: dict | None)
        self.error_occurred         = Signal("error_occurred")          # (message: str)
        self.message_received       = Signal("message_received")        # (channel: int, from_id: str, text: str, packet: dict)
        self.channels_updated       = Signal("channels_updated")        # ([(index, name, psk), ...])
        self.my_node_id_ready       = Signal("my_node_id_ready")        # (node_id: str)
        self.interface_ready        = Signal("interface_ready")         # (iface)
        self.dm_sent                = Signal("dm_sent")                 # (dest_id: str, text: str, pki: bool, packet_id: int)
        self.channel_message_sent   = Signal("channel_message_sent")    # (channel_index: int, text: str, packet_id: int)
        self.message_status_updated = Signal("message_status_updated")  # (request_id: int, status: str, error_reason: str)
        self.traceroute_result      = Signal("traceroute_result")       # (forward_edges: list, back_edges: list, origin: str, dest: str)
        self.local_node_ready       = Signal("local_node_ready")        # (long_name, short_name, node_id, gps_enabled, has_position)
        self.nodes_batch            = Signal("nodes_batch")             # ([(node_num, node_dict), ...])
        self.nodedb_reset           = Signal("nodedb_reset")            # ()
        self.neighbor_info_received = Signal("neighbor_info_received")  # (from_id: str, [(neighbor_id, snr), ...])
        self.position_sent          = Signal("position_sent")           # (success: bool, message_for_ui: str)
        self.local_position_updated = Signal("local_position_updated")  # (lat: float, lon: float, alt: int | None, found: bool)
        self.raw_packet_received    = Signal("raw_packet_received")     # (packet: dict)
        self.reconnect_status       = Signal("reconnect_status")        # (attempt: int, delay_s: int)   # attempt=0 -> reconectado

        # ── Temporizadores ───────────────────────────────────────────────────
        # Criados parados; o intervalo é passado em cada start().
        self._reconnect_timer = self._loop.timer(0, self._do_reconnect, repeat=False)
        # Watchdog: se após 12s de _do_reconnect _connected ainda for False,
        # o TCPInterface ficou pendurado sem lançar excepção nem evento pubsub.
        # Força nova tentativa via _schedule_reconnect.
        self._connect_watchdog = self._loop.timer(0, self._on_connect_watchdog, repeat=False)

        # ID do nó local — usado por _emit_node para evitar inserção na tabela
        self._local_id_known:  Optional[str] = None
        self._local_num_known: Optional[int] = None

    # ------------------------------------------------------------------
    # Entrega de sinais
    # ------------------------------------------------------------------
    def _emit(self, signal: Signal, *args):
        """Entrega um sinal na thread do loop.

        Replica Qt.AutoConnection: emissão directa quando já estamos na
        thread do loop, enfileirada quando vem de uma thread da biblioteca
        meshtastic. Sem isto, os slots corriam na thread do leitor TCP e
        tocariam no estado partilhado em paralelo.
        """
        if self._loop.is_core_thread():
            signal.emit(*args)
        else:
            self._loop.post(signal.emit, *args)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def loop(self) -> CoreLoop:
        return self._loop

    @on_loop
    def start(self):
        if self.iface is not None:
            return
        logger.info(f"Connecting to {self.hostname}:{self.port}…")
        self._known_nodes.clear()
        self._reconnect_attempts = 0
        self._subscribe_pubsub()
        # Defer the actual socket creation by one event-loop cycle so the
        # "Connecting…" status reaches the UI before anything blocks.
        self._loop.single_shot(50, self._do_connect)

    def _do_connect(self):
        """Creates the TCPInterface in a background thread so the UI never
        freezes waiting for the node to respond (especially after a reboot
        where the node may take several seconds to re-enumerate on serial)."""
        t = threading.Thread(target=self._connect_thread, daemon=True,
                             name="meshdeck-tcp-connect")
        t.start()

    def _connect_thread(self):
        """Background thread: creates TCPInterface or SerialInterface."""
        try:
            if self.serial_port:
                logger.info(f"Connecting via SerialInterface on {self.serial_port}")
                iface = SerialInterface(self.serial_port)
            else:
                iface = TCPInterface(self.hostname, portNumber=self.port)
            self.iface = iface
        except Exception as e:
            self._emit(self.error_occurred,
                       tr("err_tcp_create", host=self.hostname, port=self.port, err=e))
            logger.exception("Error starting worker")

    def stop(self):
        self._reconnect_timer.stop()
        self._connect_watchdog.stop()
        self._unsubscribe_pubsub()
        self._known_nodes.clear()
        if self.iface:
            iface_to_close = self.iface
            self.iface      = None
            self._connected = False
            self._emit(self.connection_changed, False)
            # iface.close() can block indefinitely when the TCP reader thread
            # is stuck waiting for data (known Meshtastic Python bug, especially
            # after a node reboot over serial where the bridge goes silent).
            # Run it in a daemon thread so the UI is never frozen.
            def _close_bg():
                try:
                    iface_to_close.close()
                except Exception as e:
                    logger.debug(f"iface.close() in background: {e}")
            t = threading.Thread(target=_close_bg, daemon=True, name="iface-close")
            t.start()

    def _on_connection_lost(self, interface=None):
        """Chamado pela thread interna do TCPInterface — delega para a thread do loop."""
        logger.warning("Connection lost — delegating to core loop…")
        self._loop.post(self._handle_connection_lost)

    def _handle_connection_lost(self):
        """Corre na thread do loop — seguro para timers e sinais."""
        if self._connected:
            self._connected = False
            self._emit(self.connection_changed, False)
        self._schedule_reconnect()

    def _schedule_reconnect(self):
        """Backoff exponencial: 15s → 30s → 60s → 120s (máx)."""
        delays = [15_000, 30_000, 60_000, 120_000]
        idx    = min(self._reconnect_attempts, len(delays) - 1)
        delay  = delays[idx]
        self._reconnect_attempts += 1
        logger.info(f"Reconnect #{self._reconnect_attempts} in {delay//1000}s…")
        self._emit(self.reconnect_status, self._reconnect_attempts, delay // 1000)
        self._reconnect_timer.start(delay)

    def _do_reconnect(self):
        """Tenta fechar a interface antiga e criar uma nova.
        Re-subscreve os handlers pubsub antes de criar o TCPInterface.
        Após criar a interface arranca um watchdog de 12s: se _connected
        ainda for False no timeout, o TCPInterface ficou pendurado sem
        lançar excepção nem disparar o evento pubsub — forçamos nova tentativa.
        """
        if self._connected:
            return
        try:
            if self.iface:
                _old_iface = self.iface
                self.iface = None
                threading.Thread(target=lambda: _old_iface.close(), daemon=True, name="iface-close-reconnect").start()
            self._known_nodes.clear()
            # Re-subscreve handlers pubsub
            self._unsubscribe_pubsub()
            self._subscribe_pubsub()
            logger.info(f"Reconnecting to {self.hostname}:{self.port}…")
            # Use a background thread so the UI never freezes waiting for
            # the node to respond (critical after serial reboot)
            threading.Thread(target=self._reconnect_thread, daemon=True,
                             name="meshdeck-tcp-reconnect").start()
            # Watchdog: 12s para o evento pubsub chegar; caso contrário tenta de novo
            self._connect_watchdog.start(12_000)
        except Exception as e:
            logger.warning(f"Reconnection failed: {e}")
            self._connect_watchdog.stop()
            self._schedule_reconnect()

    def _reconnect_thread(self):
        """Background thread: creates TCPInterface or SerialInterface for reconnection."""
        try:
            if self.serial_port:
                self.iface = SerialInterface(self.serial_port)
            else:
                self.iface = TCPInterface(self.hostname, portNumber=self.port)
        except Exception as e:
            logger.warning(f"Reconnection thread failed: {e}")
            # Estamos numa thread de fundo: o watchdog e _reconnect_attempts
            # pertencem à thread do loop, por isso delegamos em vez de os tocar
            # aqui — no original isto era uma corrida com o QTimer.
            self._loop.post(self._handle_connect_failure)

    def _handle_connect_failure(self):
        """Corre na thread do loop: desarma o watchdog e agenda nova tentativa."""
        self._connect_watchdog.stop()
        self._schedule_reconnect()

    def _on_connect_watchdog(self):
        """Chamado 12s após _do_reconnect se _connected ainda for False.
        O TCPInterface criou o socket mas o handshake Meshtastic não completou
        (daemon lento, rede instável). Fecha e agenda nova tentativa.
        """
        if self._connected:
            return
        logger.warning("Watchdog: no connection after 12s — rescheduling attempt…")
        if self.iface:
            _old_iface = self.iface
            self.iface = None
            threading.Thread(target=lambda: _old_iface.close(), daemon=True, name="iface-close-watchdog").start()
        self._schedule_reconnect()

    @on_loop
    def send_message(self, channel_index: int, text: str, reply_id: int = 0):
        if not self.iface or not self._connected:
            self._emit(self.error_occurred, tr("err_send_msg"))
            return
        try:
            result = self.iface.sendText(text, channelIndex=channel_index, wantAck=True,
                                         replyId=reply_id or None)
            pkt_id = result.id if result and hasattr(result, 'id') else 0
            logger.info(f"Channel {channel_index} message sent (pkt_id={pkt_id:#010x})")
            logger.debug(f"Channel {channel_index} message reply_id={reply_id:#010x}")
            self._emit(self.channel_message_sent, channel_index, text, pkt_id)
        except Exception as e:
            self._emit(self.error_occurred, tr("err_send_msg_detail", err=e))

    @on_loop
    def reset_nodedb(self):
        if not self.iface or not self._connected:
            self._emit(self.error_occurred, tr("err_reset_nodedb_nc"))
            return
        try:
            local_node = self.iface.localNode
            if hasattr(local_node, 'resetNodeDb'):
                local_node.resetNodeDb()
                logger.info("NodeDB reset via localNode.resetNodeDb()")
            else:
                p = admin_pb2.AdminMessage()
                p.nodedb_reset = 1
                self.iface.localNode._sendAdmin(p)
                logger.info("NodeDB reset via admin packet")
            self._emit(self.nodedb_reset)
        except Exception as e:
            logger.error(f"Error resetting NodeDB: {e}", exc_info=True)
            self._emit(self.error_occurred, tr("err_reset_nodedb", err=e))

    @on_loop
    def send_traceroute(self, dest_id: str, hop_limit: int = 3):
        if not self.iface or not self._connected:
            self._emit(self.error_occurred, tr("err_traceroute_nc"))
            return
        try:
            r = mesh_pb2.RouteDiscovery()
            self.iface.sendData(
                r,
                destinationId=dest_id,
                portNum=portnums_pb2.PortNum.TRACEROUTE_APP,
                wantResponse=True,
                hopLimit=hop_limit,
                channelIndex=0,
            )
            logger.info(f"Traceroute sent to {dest_id} (hopLimit={hop_limit})")
        except Exception as e:
            logger.error(f"Error sending traceroute: {e}", exc_info=True)
            self._emit(self.error_occurred, tr("err_traceroute", err=e))

    @on_loop
    def send_position(self):
        """
        Envia a posição do nó local para a rede.

        Estratégia (por ordem de preferência):
          1. localNode.setPosition() — a biblioteca lê lat/lon da config interna
             e faz o broadcast directamente via firmware.
          2. Fallback manual: lê latitudeI/longitudeI do nodesByNum e constrói
             o pacote POSITION_APP à mão.

        Em ambos os casos emite position_sent(True/False, mensagem) para que
        _on_send_position possa actualizar a statusBar *apenas* após saber
        o resultado real.
        """
        if not self.iface or not self._connected:
            self._emit(self.position_sent, False, tr("Não conectado."))
            return
        try:

            local_node = self.iface.localNode
            local_num  = local_node.nodeNum

            # ── Tentativa 1: API de alto nível da biblioteca ──────────────
            # localNode.setPosition() envia directamente sem precisar de
            # ler coordenadas da cache TCP — funciona mesmo que nodesByNum
            # ainda não tenha recebido a posição do firmware.
            sent_via_api = False
            try:
                local_node.setPosition(
                    lat=None, lon=None, alt=None,
                    # Sem argumentos → firmware usa a posição interna (GPS ou fixa)
                )
                sent_via_api = True
                logger.info("send_position: sent via localNode.setPosition()")
                self._emit(self.position_sent, True, tr("📍 Posição enviada para a rede (via firmware)."))
                return
            except TypeError:
                # Versões mais antigas da lib exigem lat/lon explícitos
                pass
            except Exception as api_err:
                logger.debug(f"send_position: setPosition() failed ({api_err}), trying fallback")

            # ── Tentativa 2: lê coordenadas da cache e envia manualmente ──
            # Procura em nodesByNum (cache do daemon) e também na config fixa
            pos_data: dict = {}
            if hasattr(self.iface, 'nodesByNum'):
                pos_data = self.iface.nodesByNum.get(local_num, {}).get('position', {})

            lat_i = pos_data.get('latitudeI')
            lon_i = pos_data.get('longitudeI')

            # Tenta também a posição fixa na configuração do localNode
            if not lat_i or not lon_i:
                try:
                    pos_cfg = getattr(local_node, 'localConfig', None)
                    if pos_cfg:
                        pos_cfg = getattr(pos_cfg, 'position', None)
                    if pos_cfg:
                        fixed_lat = getattr(pos_cfg, 'fixed_lat',  None)
                        fixed_lon = getattr(pos_cfg, 'fixed_lon',  None)
                        fixed_alt = getattr(pos_cfg, 'fixed_alt',  None)
                        if fixed_lat and fixed_lon:
                            # Converte graus → inteiros × 1e7 se necessário
                            lat_i = int(fixed_lat * 1e7) if isinstance(fixed_lat, float) else int(fixed_lat)
                            lon_i = int(fixed_lon * 1e7) if isinstance(fixed_lon, float) else int(fixed_lon)
                            if fixed_alt:
                                pos_data['altitude'] = int(fixed_alt)
                            logger.info(f"send_position: using fixed position from config ({lat_i/1e7:.6f}, {lon_i/1e7:.6f})")
                except Exception as cfg_err:
                    logger.debug(f"send_position: could not read fixed position from config: {cfg_err}")

            if not lat_i or not lon_i:
                msg = tr("pos_no_coords_msg")
                logger.warning("send_position: no coordinates — aborting")
                self._emit(self.position_sent, False, msg)
                return

            pos             = mesh_pb2.Position()
            pos.latitude_i  = int(lat_i)
            pos.longitude_i = int(lon_i)
            alt = pos_data.get('altitude')
            if alt:
                pos.altitude = int(alt)
            pos.time      = int(time.time())
            pos.timestamp = pos.time

            self.iface.sendData(
                pos,
                destinationId=BROADCAST_NUM,
                portNum=portnums_pb2.PortNum.POSITION_APP,
                wantAck=False,
                wantResponse=False,
                channelIndex=0,
            )
            msg = tr("📍 Posição enviada ({lat}, {lon}).", lat=f"{lat_i/1e7:.6f}", lon=f"{lon_i/1e7:.6f}")
            logger.info(f"Position sent ({lat_i/1e7:.6f}, {lon_i/1e7:.6f})")
            self._emit(self.position_sent, True, msg)

        except Exception as e:
            logger.error(f"Error sending position: {e}", exc_info=True)
            self._emit(self.position_sent, False, tr("Erro ao enviar posição: {msg}", msg=str(e)))

    @on_loop
    def refresh_local_position(self):
        """
        Relê a posição GPS do nó local da cache do daemon (nodesByNum)
        e emite local_position_updated(lat, lon, alt, found).

        Estratégia:
          1. nodesByNum[local_num]['position'] — cache actualizada pelo daemon
             com cada pacote POSITION_APP recebido do firmware.
          2. localConfig.position fixed_lat/fixed_lon — fallback para posição fixa.

        Emite found=False se nenhuma posição disponível, para que a UI
        possa mostrar feedback adequado (ex: "⚠ Sem posição GPS").
        """
        if not self.iface or not self._connected:
            self._emit(self.local_position_updated, 0.0, 0.0, None, False)
            return
        try:
            local_node = self.iface.localNode
            local_num  = local_node.nodeNum if local_node else None

            lat_i = lon_i = alt = None

            # 1. Cache do daemon
            if local_num and hasattr(self.iface, 'nodesByNum'):
                pos   = self.iface.nodesByNum.get(local_num, {}).get('position', {})
                lat_i = pos.get('latitudeI')
                lon_i = pos.get('longitudeI')
                alt   = pos.get('altitude')

            # 2. Posição fixa na config do localNode
            if not lat_i or not lon_i:
                try:
                    pos_cfg = getattr(local_node, 'localConfig', None)
                    if pos_cfg:
                        pos_cfg = getattr(pos_cfg, 'position', None)
                    if pos_cfg:
                        fixed_lat = getattr(pos_cfg, 'fixed_lat', None)
                        fixed_lon = getattr(pos_cfg, 'fixed_lon', None)
                        fixed_alt = getattr(pos_cfg, 'fixed_alt', None)
                        if fixed_lat and fixed_lon:
                            lat_i = int(fixed_lat * 1e7) if isinstance(fixed_lat, float) else int(fixed_lat)
                            lon_i = int(fixed_lon * 1e7) if isinstance(fixed_lon, float) else int(fixed_lon)
                            if fixed_alt:
                                alt = int(fixed_alt)
                            logger.debug(f"refresh_local_position: using fixed pos ({lat_i/1e7:.6f}, {lon_i/1e7:.6f})")
                except Exception as cfg_err:
                    logger.debug(f"refresh_local_position: fixed pos read failed: {cfg_err}")

            if lat_i and lon_i:
                lat = lat_i / 1e7
                lon = lon_i / 1e7
                logger.debug(f"refresh_local_position: found ({lat:.6f}, {lon:.6f})")
                self._emit(self.local_position_updated, lat, lon, alt, True)
            else:
                logger.debug("refresh_local_position: no position available")
                self._emit(self.local_position_updated, 0.0, 0.0, None, False)

        except Exception as e:
            logger.error(f"refresh_local_position error: {e}", exc_info=True)
            self._emit(self.local_position_updated, 0.0, 0.0, None, False)

    @on_loop
    def send_node_info(self):
        if not self.iface or not self._connected:
            return
        try:
            local_node = self.iface.localNode
            if not local_node:
                self._emit(self.error_occurred, tr("err_nodeinfo_nc"))
                return
            local_num  = local_node.nodeNum
            me         = (self.iface.nodesByNum.get(local_num, {})
                          if hasattr(self.iface, 'nodesByNum') else {})
            user_data  = me.get('user', {})
            long_name  = _clean_text(user_data.get('longName', ''))
            short_name = _clean_text(user_data.get('shortName', ''))

            # Construct User protobuf message manually to avoid administrative save & reboot
            from meshtastic.protobuf import mesh_pb2, portnums_pb2
            user_pkt = mesh_pb2.User()
            # Ensure we have a valid, normalized ID for the User packet
            nid = user_data.get('id')
            if not nid and local_num:
                nid = f"!{local_num & 0xffffffff:08x}"
            user_pkt.id = nid.lower() if nid else ""
            user_pkt.long_name = long_name
            user_pkt.short_name = short_name

            hw_model = user_data.get('hwModel')
            if hw_model is not None:
                try:
                    user_pkt.hw_model = int(hw_model)
                except (ValueError, TypeError):
                    pass

            pk = user_data.get('publicKey')
            if pk:
                if isinstance(pk, str):
                    try:
                        user_pkt.public_key = base64.b64decode(pk)
                    except Exception:
                        pass
                elif isinstance(pk, bytes):
                    user_pkt.public_key = pk

            mac = user_data.get('macaddr')
            if mac:
                if isinstance(mac, bytes):
                    user_pkt.macaddr = mac

            # Broadcast to the mesh via channel 0 (Primary)
            self.iface.sendData(
                user_pkt,
                destinationId=BROADCAST_NUM,
                portNum=portnums_pb2.PortNum.NODEINFO_APP,
                wantAck=False,
                wantResponse=False,
                channelIndex=0
            )
            logger.info(f"send_node_info: broadcasted User packet manually (no reboot)")
        except Exception as e:
            logger.error(f"Error sending manual NODEINFO: {e}", exc_info=True)
            self._emit(self.error_occurred, tr("err_nodeinfo", err=e))

    @on_loop
    def send_direct_message(self, dest_id: str, text: str, reply_id: int = 0):
        if not self.iface or not self._connected:
            self._emit(self.error_occurred, tr("err_dm_nc"))
            return
        try:

            dest_num: Optional[int] = None
            if dest_id.startswith('!'):
                try:
                    dest_num = int(dest_id[1:], 16) & 0xFFFFFFFF
                except ValueError:
                    pass

            dest_node = None
            if dest_num is not None and hasattr(self.iface, 'nodesByNum'):
                dest_node = self.iface.nodesByNum.get(dest_num)
            if dest_node is None and hasattr(self.iface, 'nodes'):
                dest_node = self.iface.nodes.get(dest_id)

            has_pubkey = bool(dest_node and dest_node.get('user', {}).get('publicKey', ''))

            if has_pubkey:
                try:
                    result = self.iface.sendData(
                        text.encode("utf-8"),
                        destinationId=dest_id,
                        portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                        wantAck=True,
                        channelIndex=0,
                        pkiEncrypted=True,
                        replyId=reply_id or None,
                    )
                    pkt_id = result.id if result and hasattr(result, 'id') else 0
                    logger.info(f"DM PKI sent to {dest_id} (pkt_id={pkt_id:#010x})")
                    logger.debug(f"DM PKI to {dest_id} reply_id={reply_id:#010x}")
                    self._emit(self.dm_sent, dest_id, text, True, pkt_id)
                    return
                except Exception as pki_err:
                    err_str = str(pki_err)
                    if 'PKI_UNKNOWN_PUBKEY' in err_str or 'unknown' in err_str.lower():
                        logger.warning(f"DM PKI failed for {dest_id}: {pki_err}")
                        self._emit(self.error_occurred, tr("err_dm_pki", dest=dest_id))
                        return
                    raise

            # Fallback PSK
            logger.info(f"DM to {dest_id}: no publicKey → PSK channel 0")
            result = self.iface.sendText(
                text, destinationId=dest_id, wantAck=True, channelIndex=0,
                replyId=reply_id or None,
            )
            pkt_id = result.id if result and hasattr(result, 'id') else 0
            logger.debug(f"DM PSK sent to {dest_id} (pkt_id={pkt_id:#010x}, reply_id={reply_id:#010x})")
            self._emit(self.dm_sent, dest_id, text, False, pkt_id)

        except Exception as e:
            logger.error(f"Error sending DM to {dest_id}: {e}", exc_info=True)
            self._emit(self.error_occurred, tr("err_dm", err=e))

    def _on_text_message(self, packet, interface=None):
        try:
            from_id = packet.get('fromId', '')
            if not from_id:
                from_num = packet.get('from')
                if from_num:
                    if self.iface and hasattr(self.iface, 'nodesByNum'):
                        node    = self.iface.nodesByNum.get(int(from_num), {})
                        from_id = node.get('user', {}).get('id', '')
                    if not from_id and self.iface and hasattr(self.iface, 'nodes'):
                        node    = self.iface.nodes.get(from_num, {})
                        from_id = node.get('user', {}).get('id', '')
                    if not from_id:
                        from_id = f"!{int(from_num) & 0xffffffff:08x}".lower()
            if not from_id:
                return

            text = packet.get('decoded', {}).get('text', '')
            if not text:
                return

            pki_encrypted = bool(packet.get('pkiEncrypted', False))
            channel       = int(packet.get('channel', 0))
            to_raw        = packet.get('to', 0xFFFFFFFF)
            try:
                to_num = int(to_raw) & 0xFFFFFFFF
            except (TypeError, ValueError):
                to_num = 0xFFFFFFFF

            logger.info(
                f"MSG: from={from_id} to_raw={to_raw!r} to_num=0x{to_num:08x} "
                f"ch={channel} pki={pki_encrypted} txt={text[:40]!r}"
            )

            to_id = packet.get('toId', '')
            if not to_id and not _is_broadcast(to_num):
                if self.iface and hasattr(self.iface, 'nodesByNum'):
                    to_node = self.iface.nodesByNum.get(to_num, {})
                    to_id   = to_node.get('user', {}).get('id', '')
                if not to_id:
                    to_id = f"!{to_num & 0xffffffff:08x}"

            if pki_encrypted:
                pk_raw = packet.get('publicKey')
                if pk_raw and self.iface:
                    try:
                        from_num_int = packet.get('from')
                        if from_num_int:
                            from_num_int = int(from_num_int)
                            node_entry   = None
                            if hasattr(self.iface, 'nodesByNum'):
                                node_entry = self.iface.nodesByNum.get(from_num_int)
                            if node_entry is None and hasattr(self.iface, 'nodes'):
                                node_entry = self.iface.nodes.get(from_id)
                            if node_entry and 'user' in node_entry:
                                if isinstance(pk_raw, bytes):
                                    node_entry['user']['publicKey'] = base64.b64encode(pk_raw).decode()
                                else:
                                    node_entry['user']['publicKey'] = str(pk_raw)
                    except Exception as e:
                        logger.debug(f"Could not update publicKey for {from_id}: {e}")

            _rx = packet.get('rxTime') or 0
            pkt_safe = {
                'fromId':       from_id,
                'toId':         to_id,
                'to':           to_num,
                'channel':      channel,
                'rxTime':       _rx if _rx else int(time.time()),
                'viaMqtt':      packet.get('viaMqtt', False),
                'pkiEncrypted': pki_encrypted,
                'decoded':      {'text': text,
                                 # Resposta nativa: id da mensagem a que esta responde.
                                 'replyId': (packet.get('decoded') or {}).get('replyId') or 0},
                'id':           packet.get('id') or 0,
                'hopsAway':     None,
            }
            # Tenta obter hopsAway do NodeDB (mais fiável que hopLimit do pacote)
            try:
                from_num_int = packet.get('from')
                if from_num_int and self.iface:
                    nd = (getattr(self.iface, 'nodesByNum', {}) or {}).get(from_num_int, {})
                    ha = nd.get('hopsAway')
                    if ha is not None:
                        pkt_safe['hopsAway'] = ha
                    elif packet.get('hopStart') is not None and packet.get('hopLimit') is not None:
                        pkt_safe['hopsAway'] = int(packet['hopStart']) - int(packet['hopLimit'])
            except Exception:
                pass

            if pki_encrypted:
                pk_raw = packet.get('publicKey')
                if pk_raw and from_id:
                    try:
                        pk_b64 = base64.b64encode(pk_raw).decode() if isinstance(pk_raw, bytes) else str(pk_raw)
                        self._emit(self.node_updated, from_id, {'public_key': pk_b64}, None)
                    except Exception:
                        pass

            logger.debug(f"Text from {from_id} on ch{channel} (pkt_id={pkt_safe['id']:#010x}, "
                        f"reply_id={pkt_safe['decoded']['replyId']:#010x})")
            self._emit(self.message_received, channel, from_id, text, pkt_safe)
        except Exception as e:
            logger.error(f"Error in text message handler: {e}", exc_info=True)

    @on_loop
    def _sync_nodedb(self) -> int:
        """Relê a NodeDB e emite um nodes_batch. Devolve quantos nós leu.

        Chamado pelo polling de segurança, que vem de outra thread; daí o
        @on_loop, para a leitura não correr em paralelo com o processamento
        de pacotes. Fora da thread do loop devolve None em vez da contagem.
        """
        if not self.iface or not self._connected:
            return 0

        nodes_src = getattr(self.iface, 'nodesByNum', None) or {}
        if not nodes_src:
            nodes_str = getattr(self.iface, 'nodes', None) or {}
            nodes_src = {}
            for k, v in nodes_str.items():
                try:
                    nodes_src[int(str(k).lstrip('!'), 16)] = v
                except Exception:
                    pass

        deduped: Dict[int, dict] = {}
        for raw_key, node in nodes_src.items():
            if not isinstance(node, dict):
                continue
            try:
                num = int(node.get('num') or raw_key)
            except (TypeError, ValueError):
                continue
            if num not in deduped:
                deduped[num] = node
            else:
                if (node.get('user', {}).get('publicKey') and
                        not deduped[num].get('user', {}).get('publicKey')):
                    deduped[num] = node

        if deduped:
            self._emit(self.nodes_batch, list(deduped.items()))
        return len(deduped)

    def _on_connection_established(self, interface=None):
        """Chamado pela thread interna do TCPInterface — delega para a thread do loop."""
        logger.info("Connection established — delegating to core loop…")
        self._loop.post(self._handle_connection_established)

    def _handle_connection_established(self):
        """Corre na thread do loop — seguro para timers e sinais."""
        self._connected          = True
        self._reconnect_attempts = 0
        self._reconnect_timer.stop()
        self._connect_watchdog.stop()
        self._known_nodes.clear()
        self._emit(self.reconnect_status, 0, 0)   # 0 = reconectado com sucesso
        self._emit(self.connection_changed, True)

        # Determina o ID do nó local com a melhor fonte disponível:
        # 1ª opção — getMyNodeInfo() (mais fiável, lê do NodeDB completo)
        # 2ª opção — localNode.nodeNum convertido para !hex (fallback rápido)
        # O ID é registado ANTES do batch para que o proxy bloqueie o nó local.
        local_num = None
        my_id     = None
        try:
            if self.iface and self.iface.localNode:
                local_num = self.iface.localNode.nodeNum
                if local_num:
                    self._local_num_known = local_num
                    self._local_id_known  = f"!{int(local_num) & 0xffffffff:08x}"
        except Exception as e:
            logger.warning(f"Could not determine local nodeNum: {e}")

        try:
            my_id = self._get_my_node_id()
            if my_id:
                self._local_id_known = my_id   # ID canónico confirmado
        except Exception as e:
            logger.warning(f"_get_my_node_id failed: {e}")

        # Emite my_node_id_ready exactamente uma vez, com o melhor ID disponível
        final_id = my_id or self._local_id_known
        if final_id:
            logger.info(f"Local node: num={local_num} id={final_id}")
            self._emit(self.my_node_id_ready, final_id)

        # Defer the heavy NodeDB load so the event loop can deliver the
        # "connecting…" status before the CPU is busy.
        # single_shot(0) yields to the loop for one cycle.
        self._loop.single_shot(
            0, functools.partial(self._deferred_initial_load, local_num, my_id, final_id))

    def _deferred_initial_load(self, local_num, my_id, final_id):
        """Heavy initialisation deferred from _handle_connection_established.
        Runs one event-loop cycle after the connection status went out."""
        # Carga inicial completa
        try:
            loaded = self._sync_nodedb()
            logger.info(f"Initial NodeDB: {loaded} nodes loaded")
        except Exception as e:
            logger.error(f"Error loading initial NodeDB: {e}", exc_info=True)

        # NeighborInfo inicial do NodeDB
        try:
            nodes_src = getattr(self.iface, 'nodesByNum', None) or {}
            for raw_key, node in nodes_src.items():
                if not isinstance(node, dict):
                    continue
                neighbors_raw = node.get('neighborInfo', {}).get('neighbors', [])
                if not neighbors_raw:
                    continue
                try:
                    num     = int(node.get('num') or raw_key)
                    from_id = (node.get('user', {}).get('id') or f"!{num & 0xffffffff:08x}").lower()
                except (TypeError, ValueError):
                    continue
                neighbors = []
                for nb in neighbors_raw:
                    nb_num = nb.get('nodeId') or nb.get('node_id')
                    if nb_num:
                        neighbors.append((f"!{int(nb_num) & 0xffffffff:08x}", float(nb.get('snr', 0.0))))
                if neighbors:
                    self._emit(self.neighbor_info_received, from_id, neighbors)
        except Exception as e:
            logger.debug(f"Error loading initial NeighborInfo: {e}")

        # Metadados do nó local
        try:
            local_user = {}
            if local_num and hasattr(self.iface, "nodesByNum"):
                local_user = self.iface.nodesByNum.get(local_num, {}).get("user", {})
            if not local_user and my_id and hasattr(self.iface, "nodes"):
                local_user = self.iface.nodes.get(my_id, {}).get("user", {})
            ln  = _clean_text(local_user.get("longName", ""))
            sn  = _clean_text(local_user.get("shortName", ""))
            nid = local_user.get("id", final_id or "")

            gps_enabled  = False
            has_position = False
            try:
                local_node_obj = self.iface.localNode if self.iface else None
                if local_node_obj:
                    pos_cfg = getattr(local_node_obj, 'localConfig', None)
                    if pos_cfg:
                        pos_cfg = getattr(pos_cfg, 'position', None)
                    if pos_cfg:
                        gps_mode    = getattr(pos_cfg, 'gps_mode', None)
                        gps_enabled = (gps_mode == 1) if gps_mode is not None else False
            except Exception:
                pass
            try:
                if local_num and hasattr(self.iface, 'nodesByNum'):
                    pos          = self.iface.nodesByNum.get(local_num, {}).get('position', {})
                    has_position = bool(pos and pos.get('latitudeI') and pos.get('longitudeI'))
            except Exception:
                pass

            self._emit(self.local_node_ready, ln, sn, nid, gps_enabled, has_position)
        except Exception as e:
            logger.error(f"Error getting local node metadata: {e}")

        self._emit(self.interface_ready, self.iface)
        self._attempt_load_channels(retry_count=0)

    def _get_my_node_id(self):
        try:
            if not self.iface:
                return None
            my_info = self.iface.getMyNodeInfo()
            if my_info and 'user' in my_info:
                return my_info['user'].get('id')
            for node_id, node in self.iface.nodes.items():
                if node.get('user', {}).get('id') and node.get('isMe'):
                    return node['user']['id']
        except Exception as e:
            logger.error(f"Error getting local node ID: {e}")
        return None

    @on_loop
    def _attempt_load_channels(self, retry_count=0):
        if not self._load_channels_from_node() and retry_count < 5:
            self._loop.single_shot(
                2000, functools.partial(self._attempt_load_channels, retry_count + 1))

    def _on_node_updated(self, node, interface=None):
        if not isinstance(node, dict):
            return
        num = node.get('num')
        nid = node.get('user', {}).get('id')
        if not nid and num:
            try:
                nid = f"!{int(num) & 0xffffffff:08x}".lower()
            except (TypeError, ValueError):
                return
        if not nid:
            return
        self._emit_node(num, node)

    def _on_receive_user(self, packet, interface=None):
        """
        Handler dedicado para meshtastic.receive.user (NODEINFO_APP).
        A biblioteca publica este tópico específico com o protobuf já decodificado.
        Garante que publicKey e hwModel são extraídos mesmo antes do batch completo.
        """
        try:
            from_id_num    = packet.get('from')
            from_id_string = packet.get('fromId', '')
            if not from_id_string and from_id_num:
                try:
                    from_id_string = f"!{int(from_id_num) & 0xffffffff:08x}".lower()
                except (TypeError, ValueError):
                    return
            if not from_id_string:
                return

            decoded  = packet.get('decoded') or {}
            user     = decoded.get('user', {})
            if not user:
                return

            updates = {
                'long_name':  _clean_text(user.get('longName', '')),
                'short_name': _clean_text(user.get('shortName', '')),
                'hw_model':   user.get('hwModel', ''),
                'id_num':     from_id_num,
            }
            # publicKey — tenta várias representações
            pk = user.get('publicKey') or user.get('public_key')
            if pk:
                try:
                    if isinstance(pk, bytes):
                        updates['public_key'] = base64.b64encode(pk).decode()
                    else:
                        updates['public_key'] = str(pk)
                except Exception:
                    pass

            for key, pkt_key in (('snr', 'rxSnr'), ('via_mqtt', 'viaMqtt')):
                val = packet.get(pkt_key)
                if val is not None:
                    updates[key] = val

            rx_time = packet.get('rxTime')
            updates['last_heard'] = (datetime.fromtimestamp(int(rx_time))
                                     if rx_time else datetime.now())

            updates['last_packet_type'] = 'NODEINFO_APP'
            self._emit(self.node_updated, from_id_string, updates, packet)
        except Exception as e:
            logger.debug(f"Error in _on_receive_user: {e}")

    def _on_packet_received(self, packet, interface=None):
        try:
            from_id_num    = packet.get('from')
            from_id_string = packet.get('fromId')
            if not from_id_string and from_id_num and self.iface:
                from_id_string = (self.iface.nodes.get(from_id_num, {})
                                  .get('user', {}).get('id'))

            # Emite o pacote raw para métricas com fromId resolvido
            try:
                pkt_copy = dict(packet)
                if from_id_string and not pkt_copy.get('fromId'):
                    pkt_copy['fromId'] = from_id_string

                # Enriquece com contexto do NodeDB (shortName e hopsAway)
                if self.iface and from_id_num:
                    nodes_src = getattr(self.iface, 'nodesByNum', {}) or {}
                    node_db_entry = nodes_src.get(int(from_id_num), {})
                    if node_db_entry:
                        if not pkt_copy.get('shortName'):
                            pkt_copy['shortName'] = _clean_text(
                                node_db_entry.get('user', {}).get('shortName'))
                        if pkt_copy.get('hopsAway') is None:
                            pkt_copy['hopsAway'] = node_db_entry.get('hopsAway')

                self._emit(self.raw_packet_received, pkt_copy)
            except Exception:
                pass

            if not from_id_string:
                return

            decoded = packet.get('decoded') or {}
            portnum = decoded.get('portnum')

            if portnum != 'ROUTING_APP':
                if self.iface and self.iface.localNode:
                    local_num = self.iface.localNode.nodeNum
                    is_local  = False
                    if from_id_num is not None and local_num is not None:
                        try:
                            is_local = int(from_id_num) == int(local_num)
                        except (TypeError, ValueError):
                            pass
                    if not is_local and local_num is not None and from_id_string:
                        try:
                            is_local = (from_id_string.lower() == f'!{int(local_num) & 0xffffffff:08x}')
                        except (TypeError, ValueError):
                            pass
                    if is_local:
                        logger.debug(f'Loopback ignored: portnum={portnum} from={from_id_string}')
                        return

            if portnum == 'TEXT_MESSAGE_APP':
                # Actualiza lastHeard/snr/last_packet_type do remetente
                _txt_updates: Dict[str, Any] = {}
                for key, pkt_key in (('snr', 'rxSnr'), ('via_mqtt', 'viaMqtt')):
                    val = packet.get(pkt_key)
                    if val is not None:
                        _txt_updates[key] = val
                if self.iface:
                    _num = packet.get('from')
                    if _num:
                        _nd = (getattr(self.iface, 'nodesByNum', {}) or {}).get(_num, {})
                        _ha = _nd.get('hopsAway')
                        if _ha is not None:
                            _txt_updates['hops_away'] = _ha
                rx_time = packet.get('rxTime')
                _txt_updates['last_heard'] = (datetime.fromtimestamp(int(rx_time))
                                              if rx_time else datetime.now())
                _txt_updates['last_packet_type'] = 'TEXT_MESSAGE_APP'
                if 'id_num' not in _txt_updates and from_id_num:
                    _txt_updates['id_num'] = from_id_num
                if _txt_updates:
                    self._emit(self.node_updated, from_id_string, _txt_updates, packet)
                return

            if portnum == 'ROUTING_APP':
                try:
                    routing    = decoded.get('routing', {})
                    request_id = decoded.get('requestId', 0)
                    if request_id:
                        error_reason = routing.get('errorReason', 'NONE')
                        if error_reason and error_reason != 'NONE':
                            status = 'nak'
                        else:
                            from_num  = packet.get('from')
                            local_num = self.iface.localNode.nodeNum if self.iface else None
                            if from_num and local_num and int(from_num) == int(local_num):
                                status = 'ack_implicit'
                            else:
                                status = 'ack'
                        self._emit(self.message_status_updated, request_id, status, error_reason or 'NONE')
                except Exception as e:
                    logger.debug(f"Error processing ROUTING_APP: {e}")
                # Actualiza last_heard e last_packet_type do remetente
                _rout_updates: Dict[str, Any] = {'last_packet_type': 'ROUTING_APP'}
                if from_id_num:
                    _rout_updates['id_num'] = from_id_num
                for key, pkt_key in (('snr', 'rxSnr'), ('via_mqtt', 'viaMqtt')):
                    val = packet.get(pkt_key)
                    if val is not None:
                        _rout_updates[key] = val
                rx_time = packet.get('rxTime')
                _rout_updates['last_heard'] = (datetime.fromtimestamp(int(rx_time))
                                               if rx_time else datetime.now())
                if from_id_string:
                    self._emit(self.node_updated, from_id_string, _rout_updates, packet)
                return

            if portnum == 'TRACEROUTE_APP':
                try:
                    want_response = packet.get('wantResponse', False)
                    if want_response:
                        return

                    tr = decoded.get('traceroute', {})
                    if not tr:
                        return

                    origin_num = packet.get('to')
                    dest_num   = packet.get('from')

                    def num_to_id(n):
                        try:
                            ni = int(n)
                            if ni == 0 or ni == 0xFFFFFFFF:
                                return None
                            if self.iface and hasattr(self.iface, 'nodesByNum'):
                                uid = self.iface.nodesByNum.get(ni, {}).get('user', {}).get('id', '')
                                if uid:
                                    return uid
                            return f"!{ni & 0xffffffff:08x}"
                        except (TypeError, ValueError):
                            return None

                    forward_edges = []
                    back_edges    = []

                    route_toward = [r for r in (tr.get('route') or []) if r]
                    snr_toward   = list(tr.get('snrTowards') or tr.get('snrToward') or [])
                    full_toward  = [origin_num] + route_toward + [dest_num]
                    for i in range(len(full_toward) - 1):
                        a_id = num_to_id(full_toward[i])
                        b_id = num_to_id(full_toward[i + 1])
                        if not a_id or not b_id:
                            continue
                        snr = float(snr_toward[i]) / 4.0 if i < len(snr_toward) else 0.0
                        forward_edges.append((a_id, b_id, snr))

                    route_back = [r for r in (tr.get('routeBack') or []) if r]
                    snr_back   = list(tr.get('snrBack') or [])
                    full_back  = [dest_num] + route_back + [origin_num]
                    for i in range(len(full_back) - 1):
                        a_id = num_to_id(full_back[i])
                        b_id = num_to_id(full_back[i + 1])
                        if not a_id or not b_id:
                            continue
                        snr = float(snr_back[i]) / 4.0 if i < len(snr_back) else 0.0
                        back_edges.append((a_id, b_id, snr))

                    if forward_edges or back_edges:
                        origin_str = num_to_id(origin_num) or '?'
                        dest_str   = num_to_id(dest_num) or '?'
                        self._emit(self.traceroute_result, forward_edges, back_edges, origin_str, dest_str)
                except Exception as e:
                    logger.debug(f"Error processing TRACEROUTE_APP: {e}", exc_info=True)
                # Actualiza last_heard e last_packet_type do remetente
                _tr_updates: Dict[str, Any] = {'last_packet_type': 'TRACEROUTE_APP'}
                if from_id_num:
                    _tr_updates['id_num'] = from_id_num
                for key, pkt_key in (('snr', 'rxSnr'), ('via_mqtt', 'viaMqtt')):
                    val = packet.get(pkt_key)
                    if val is not None:
                        _tr_updates[key] = val
                rx_time = packet.get('rxTime')
                _tr_updates['last_heard'] = (datetime.fromtimestamp(int(rx_time))
                                             if rx_time else datetime.now())
                if from_id_string:
                    self._emit(self.node_updated, from_id_string, _tr_updates, packet)
                return

            # FIX-2: processa NEIGHBORINFO_APP em tempo real
            if portnum == 'NEIGHBORINFO_APP':
                try:
                    ni_data   = decoded.get('neighborinfo', {})
                    if not ni_data:
                        ni_data = decoded.get('neighborInfo', {})
                    neighbors_raw = ni_data.get('neighbors', [])
                    if neighbors_raw:
                        neighbors = []
                        for nb in neighbors_raw:
                            nb_num = nb.get('nodeId') or nb.get('node_id')
                            if nb_num:
                                neighbors.append((f"!{int(nb_num) & 0xffffffff:08x}",
                                                  float(nb.get('snr', 0.0))))
                        if neighbors:
                            self._emit(self.neighbor_info_received, from_id_string, neighbors)
                            logger.info(
                                f"NeighborInfo from {from_id_string}: "
                                f"{len(neighbors)} neighbors"
                            )
                except Exception as e:
                    logger.debug(f"Error processing NEIGHBORINFO_APP: {e}")
                # Continua para o processamento genérico de updates

            updates: Dict[str, Any] = {}

            if portnum == 'NODEINFO_APP':
                user = decoded.get('user', {})
                updates.update({
                    'long_name':  _clean_text(user.get('longName', '')),
                    'short_name': _clean_text(user.get('shortName', '')),
                    'hw_model':   user.get('hwModel', ''),
                    'id_num':     from_id_num,
                })
                # Extrai publicKey — necessário para DM PKI
                pk = user.get('publicKey') or user.get('public_key')
                if pk:
                    try:
                        updates['public_key'] = (base64.b64encode(pk).decode()
                                                 if isinstance(pk, bytes) else str(pk))
                    except Exception:
                        pass
                self._extract_position(decoded.get('position', {}), updates)
                self._known_nodes.add(from_id_string)

            elif portnum == 'POSITION_APP':
                self._extract_position(decoded.get('position', {}), updates)
                if from_id_string not in self._known_nodes:
                    self._known_nodes.add(from_id_string)

            elif portnum == 'TELEMETRY_APP':
                tel = decoded.get('telemetry', {})
                dm  = tel.get('deviceMetrics', {})
                if dm:
                    batt = dm.get('batteryLevel')
                    if batt is not None:
                        updates['battery_level'] = batt
                    ch_util = dm.get('channelUtilization')
                    if ch_util is not None:
                        updates['channel_utilization'] = float(ch_util)
                    air_tx = dm.get('airUtilTx')
                    if air_tx is not None:
                        updates['air_util_tx'] = float(air_tx)
                    voltage = dm.get('voltage')
                    if voltage is not None:
                        updates['voltage'] = float(voltage)
                    uptm = dm.get('uptimeSeconds')
                    if uptm is not None:
                        updates['uptime_seconds'] = int(uptm)
                em = tel.get('environmentMetrics', {})
                if em:
                    for field in ('temperature', 'relativeHumidity', 'barometricPressure',
                                  'gasResistance', 'iaq', 'distance', 'lux',
                                  'windSpeed', 'windDirection', 'windGust',
                                  'radiation', 'rainfall1h', 'rainfall24h'):
                        val = em.get(field)
                        if val is not None:
                            updates[f'env_{field}'] = val
                pm = tel.get('powerMetrics', {})
                if pm:
                    for field in ('ch1Voltage', 'ch1Current', 'ch2Voltage', 'ch2Current',
                                  'ch3Voltage', 'ch3Current'):
                        val = pm.get(field)
                        if val is not None:
                            updates[f'pwr_{field}'] = val
                hm = tel.get('healthMetrics', {})
                if hm:
                    for field in ('heartBpm', 'spO2', 'temperature'):
                        val = hm.get(field)
                        if val is not None:
                            updates[f'health_{field}'] = val

            for key, pkt_key in (('snr', 'rxSnr'), ('via_mqtt', 'viaMqtt')):
                val = packet.get(pkt_key)
                if val is not None:
                    updates[key] = val

            # hopsAway vem do NodeDB actualizado pela biblioteca (não do hopLimit do pacote)
            if self.iface and from_id_num:
                _nd = (getattr(self.iface, 'nodesByNum', {}) or {}).get(from_id_num, {})
                _ha = _nd.get('hopsAway')
                if _ha is not None:
                    updates['hops_away'] = _ha

            # last_heard: usa rxTime do pacote quando disponível; cai para now() como
            # fallback — rxTime pode ser 0 ou ausente em pacotes via TCP/daemon
            rx_time = packet.get('rxTime')
            if rx_time:
                try:
                    updates['last_heard'] = datetime.fromtimestamp(int(rx_time))
                except Exception:
                    updates['last_heard'] = datetime.now()
            else:
                # Sem rxTime — o pacote chegou agora via TCP, usa tempo actual
                updates['last_heard'] = datetime.now()

            updates['last_packet_type'] = portnum or 'UNKNOWN'
            if 'id_num' not in updates and from_id_num:
                updates['id_num'] = from_id_num

            # Para nós MQTT que não enviam NODEINFO/POSITION/TELEMETRY frequentemente,
            # garantimos que pelo menos o last_heard e via_mqtt são actualizados
            # mesmo que não haja mais nenhum campo relevante.
            if updates:
                self._emit(self.node_updated, from_id_string, updates, packet)

        except Exception as e:
            logger.error(f"Error processing packet: {e}", exc_info=True)

    @staticmethod
    def _extract_position(pos: dict, updates: dict):
        lat_i = pos.get('latitudeI')
        lon_i = pos.get('longitudeI')
        if lat_i is not None: updates['latitude']  = lat_i / 1e7
        if lon_i is not None: updates['longitude'] = lon_i / 1e7
        alt = pos.get('altitude')
        if alt is not None:   updates['altitude']  = alt

    def _emit_node(self, node_id_num, node_obj: dict, packet=None):
        user = node_obj.get('user', {})
        nid  = user.get('id')
        if not nid:
            if node_id_num:
                try:
                    nid = f"!{int(node_id_num) & 0xffffffff:08x}".lower()
                except (TypeError, ValueError):
                    return
            else:
                return

        # Não emite o nó local — ele é gerido por local_node_ready
        if self._local_id_known and nid.lower() == self._local_id_known.lower():
            return
        if self._local_num_known is not None and node_id_num is not None:
            try:
                if int(node_id_num) == self._local_num_known:
                    return
            except (TypeError, ValueError):
                pass

        self._known_nodes.add(nid)
        # lastHeard=0 significa "desconhecido" — converte para None
        last_heard_raw = node_obj.get('lastHeard')
        last_heard = None
        if last_heard_raw:
            try:
                last_heard = datetime.fromtimestamp(int(last_heard_raw))
            except Exception:
                pass

        pos   = node_obj.get('position', {})
        lat_i = pos.get('latitudeI')
        lon_i = pos.get('longitudeI')

        data = {
            "id_num":        node_id_num,
            "long_name":     _clean_text(user.get('longName', '')),
            "short_name":    _clean_text(user.get('shortName', '')),
            "last_heard":    last_heard,
            "snr":           node_obj.get('snr'),
            "hops_away":     node_obj.get('hopsAway'),
            "via_mqtt":      node_obj.get('viaMqtt'),
            "latitude":      lat_i / 1e7 if lat_i is not None else None,
            "longitude":     lon_i / 1e7 if lon_i is not None else None,
            "altitude":      pos.get('altitude'),
            "battery_level": node_obj.get('deviceMetrics', {}).get('batteryLevel'),
            "hw_model":      user.get('hwModel', ''),
            "public_key":    user.get('publicKey', ''),
        }
        self._emit(self.node_updated, nid, data, packet)

    def _load_channels_from_node(self) -> bool:
        try:
            if not self.iface:
                return False
            local_node = self.iface.getNode("^local")
            if not local_node or not getattr(local_node, 'channels', None):
                return False

            self._channels.clear()
            for ch in local_node.channels:
                index    = ch.index
                raw_name = (ch.settings.name if ch.settings else "") or ""
                name     = raw_name if raw_name else ("Primary" if index == 0 else tr("Canal {index}", index=index))
                psk      = ch.settings.psk if ch.settings else b""
                self._channels[index] = (name, psk)

            if not self._channels:
                return False

            self._emit(self.channels_updated, [
                (idx, name, psk) for idx, (name, psk) in self._channels.items()
            ])
            return True
        except Exception as e:
            logger.error(f"Error loading channels: {e}", exc_info=True)
            return False
