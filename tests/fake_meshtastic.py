"""
Interface Meshtastic falsa, para exercitar o core sem hardware.

Imita só a superfície que o MeshtasticWorker toca: nodesByNum, nodes,
localNode, getMyNodeInfo, sendText, sendData e getNode. Os pacotes são
injectados pelos tópicos pubsub reais da biblioteca, por isso o caminho de
código exercitado é o mesmo de uma ligação verdadeira.
"""

from pubsub import pub

LOCAL_NUM = 0xAABB0001
LOCAL_ID = "!aabb0001"


class SentPacket:
    """O que sendText/sendData devolvem — o worker só lê .id."""

    def __init__(self, pid):
        self.id = pid


class FakeChannelSettings:
    def __init__(self, name, psk=b""):
        self.name = name
        self.psk = psk


class FakeChannel:
    def __init__(self, index, name, psk=b""):
        self.index = index
        self.settings = FakeChannelSettings(name, psk)


class FakePositionConfig:
    def __init__(self, gps_mode=1, fixed_lat=0.0, fixed_lon=0.0, fixed_alt=0):
        self.gps_mode = gps_mode
        self.fixed_lat = fixed_lat
        self.fixed_lon = fixed_lon
        self.fixed_alt = fixed_alt


class FakeLocalConfig:
    def __init__(self, position=None):
        self.position = position or FakePositionConfig()


class FakeLocalNode:
    def __init__(self, num=LOCAL_NUM, channels=None, local_config=None):
        self.nodeNum = num
        self.channels = channels if channels is not None else [
            FakeChannel(0, ""), FakeChannel(1, "Secundário", b"\x01" * 16),
        ]
        self.localConfig = local_config or FakeLocalConfig()
        self.favorites = set()
        self.admin_messages = []
        self.set_position_calls = []
        self.reset_nodedb_calls = 0
        self.raise_on_set_position = None

    def setFavorite(self, node_id):
        self.favorites.add(node_id)

    def removeFavorite(self, node_id):
        self.favorites.discard(node_id)

    def resetNodeDb(self):
        self.reset_nodedb_calls += 1

    def setPosition(self, **kwargs):
        if self.raise_on_set_position is not None:
            raise self.raise_on_set_position
        self.set_position_calls.append(kwargs)

    def _sendAdmin(self, msg):
        self.admin_messages.append(msg)


class FakeInterface:
    def __init__(self, nodes_by_num=None, local_node=None, next_packet_id=0x1000):
        self.nodesByNum = dict(nodes_by_num or {})
        self.localNode = local_node or FakeLocalNode()
        self._next_id = next_packet_id
        self.sent_text = []
        self.sent_data = []
        self.closed = False
        self.raise_on_send = None

        if LOCAL_NUM not in self.nodesByNum:
            self.nodesByNum[LOCAL_NUM] = {
                "num": LOCAL_NUM,
                "user": {"id": LOCAL_ID, "longName": "Base CT7BRA",
                         "shortName": "BASE", "hwModel": "TBEAM"},
                "position": {"latitudeI": 385000000, "longitudeI": -91000000, "altitude": 95},
            }

    # -- a vista por id_string que a biblioteca também expõe --------------
    @property
    def nodes(self):
        out = {}
        for num, node in self.nodesByNum.items():
            uid = (node.get("user", {}) or {}).get("id") or f"!{num & 0xffffffff:08x}"
            out[uid] = node
        return out

    def getMyNodeInfo(self):
        return self.nodesByNum.get(self.localNode.nodeNum)

    def getNode(self, which):
        return self.localNode

    def isConnected(self):
        return not self.closed

    def _claim_id(self):
        self._next_id += 1
        return self._next_id

    def sendText(self, text, **kwargs):
        if self.raise_on_send is not None:
            raise self.raise_on_send
        pid = self._claim_id()
        self.sent_text.append({"text": text, "id": pid, **kwargs})
        return SentPacket(pid)

    def sendData(self, payload, **kwargs):
        if self.raise_on_send is not None:
            raise self.raise_on_send
        pid = self._claim_id()
        self.sent_data.append({"payload": payload, "id": pid, **kwargs})
        return SentPacket(pid)

    def close(self):
        self.closed = True


# ── injecção de eventos pelos tópicos pubsub reais ──────────────────────────

def emit_connection_established(iface):
    pub.sendMessage("meshtastic.connection.established", interface=iface)


def emit_connection_lost(iface):
    pub.sendMessage("meshtastic.connection.lost", interface=iface)


def emit_packet(packet, iface):
    pub.sendMessage("meshtastic.receive", packet=packet, interface=iface)


def emit_text(packet, iface):
    pub.sendMessage("meshtastic.receive.text", packet=packet, interface=iface)


def emit_user(packet, iface):
    pub.sendMessage("meshtastic.receive.user", packet=packet, interface=iface)


def emit_node_updated(node, iface):
    pub.sendMessage("meshtastic.node.updated", node=node, interface=iface)


# ── construtores de pacotes ─────────────────────────────────────────────────

def num_to_id(num):
    """Como _nodeNumToId da biblioteca: broadcast vira "^all", o resto !hex."""
    if num is None:
        return None
    if int(num) & 0xFFFFFFFF == 0xFFFFFFFF:
        return "^all"
    return f"!{int(num) & 0xffffffff:08x}"


def packet(portnum, from_num, *, decoded=None, to=0xFFFFFFFF, rx_snr=None,
           rx_time=None, channel=0, via_mqtt=False, from_id=None, to_id=None,
           **extra):
    """Pacote como a biblioteca o entrega depois de descodificar.

    fromId e toId vão sempre preenchidos: _handlePacketFromRadio adiciona-os
    a todos os pacotes, e o worker depende de fromId para identificar o
    remetente. Passar from_id=None explicitamente não os remove — para testar
    a ausência, apague a chave no próprio teste.
    """
    pkt = {
        "from": from_num,
        "fromId": from_id if from_id is not None else num_to_id(from_num),
        "to": to,
        "toId": to_id if to_id is not None else num_to_id(to),
        "channel": channel,
        "viaMqtt": via_mqtt,
        "decoded": {"portnum": portnum, **(decoded or {})},
    }
    if rx_snr is not None:
        pkt["rxSnr"] = rx_snr
    if rx_time is not None:
        pkt["rxTime"] = rx_time
    pkt.update(extra)
    return pkt
