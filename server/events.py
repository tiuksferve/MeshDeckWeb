"""
server/events.py — Contrato Socket.IO entre o servidor e o browser.

Cada sinal do core tem aqui um nome de evento e um serializador que devolve
um dicionário pronto para JSON. Manter os dois lados neste ficheiro evita o
desvio silencioso entre o que o servidor envia e o que o cliente espera.

Duas decisões que divergem de um encaminhamento cego dos sinais:

  * raw_packet_received não vai para o browser em bruto. Numa rede movimentada
    são centenas de pacotes por minuto, cada um com protobuf descodificado, e
    as métricas são calculadas no servidor com o metrics_data existente. Em vez
    disso vai PACKET, uma projecção com os campos que a interface mostra.

  * As PSK dos canais não vão no evento de canais. A aba de mensagens só precisa
    do índice e do nome; a chave só é servida pelo endpoint autenticado do
    editor de canais, quando o utilizador o abre. Reduz a exposição de chaves
    sem perder funcionalidade.
"""

from core.serialize import to_epoch, to_jsonable

# ── Servidor -> cliente ─────────────────────────────────────────────────────
SNAPSHOT        = "snapshot"          # estado completo, ao ligar ou após reload
CONNECTION      = "connection"        # estado da ligação ao nó
RECONNECT       = "reconnect"         # contagem do backoff de reconexão
ERROR           = "error"             # mensagem de erro para toast
LOG             = "log"               # linha de log para a consola
NODES           = "nodes"             # lote de nós criados ou actualizados
NODE_REMOVED    = "node_removed"      # após reset da NodeDB
LOCAL_NODE      = "local_node"        # metadados do nó local
LOCAL_POSITION  = "local_position"    # resultado do refresh de posição
MY_NODE_ID      = "my_node_id"        # ID canónico do nó local
CHANNELS        = "channels"          # lista de canais (sem PSK)
MESSAGE         = "message"           # mensagem recebida
MESSAGE_SENT    = "message_sent"      # eco de uma mensagem enviada
MESSAGE_STATUS  = "message_status"    # ACK/NAK por packet id
TRACEROUTE      = "traceroute"        # resultado de um traceroute
NEIGHBORS       = "neighbors"         # vizinhança NeighborInfo
POSITION_SENT   = "position_sent"     # resultado do envio de posição
PACKET          = "packet"            # projecção compacta de um pacote
PACKET_STATS    = "packet_stats"      # SNR médio e pacotes/min da barra de estado
NODEDB_RESET    = "nodedb_reset"      # a NodeDB do nó foi limpa
NODE_READY      = "node_ready"        # a configuração do nó já pode ser lida
SNR_LEVELS      = "snr_levels"        # limiares de cor do SNR para o preset do nó
CONFIG_LOCK     = "config_lock"       # quem está a gravar configuração
METRICS         = "metrics"           # HTML de uma secção de métricas
UNREAD          = "unread"            # contadores de não-lidas, após marcar como lida
FAVORITES       = "favorites"         # lista completa de favoritos, após alterar um

# ── Cliente -> servidor ─────────────────────────────────────────────────────
CMD_CONNECT          = "connect_node"
CMD_DISCONNECT       = "disconnect_node"
CMD_SEND_CHANNEL_MSG = "send_channel_message"
CMD_SEND_DM          = "send_dm"
CMD_TRACEROUTE       = "traceroute"
CMD_SEND_POSITION    = "send_position"
CMD_SEND_NODEINFO    = "send_nodeinfo"
CMD_REFRESH_POSITION = "refresh_local_position"
CMD_RESET_NODEDB     = "reset_nodedb"
CMD_TOGGLE_FAVORITE  = "toggle_favorite"
CMD_SET_LANGUAGE     = "set_language"
CMD_SAVE_CONFIG      = "save_config"
CMD_SAVE_CHANNELS    = "save_channels"
CMD_REQUEST_SNAPSHOT = "request_snapshot"
CMD_CLEAR_METRICS    = "clear_metrics"
CMD_MARK_READ        = "mark_read"


# ── Serializadores dos sinais do core ───────────────────────────────────────

def connection_changed(connected: bool) -> dict:
    return {"connected": bool(connected)}


def reconnect_status(attempt: int, delay_s: int) -> dict:
    # attempt == 0 significa "reconectado com sucesso"
    return {"attempt": int(attempt), "delay_s": int(delay_s)}


def error_occurred(message: str) -> dict:
    return {"message": str(message)}


def my_node_id_ready(node_id: str) -> dict:
    return {"node_id": node_id}


def local_node_ready(long_name, short_name, node_id, gps_enabled, has_position) -> dict:
    return {
        "long_name":    long_name,
        "short_name":   short_name,
        "node_id":      node_id,
        "gps_enabled":  bool(gps_enabled),
        "has_position": bool(has_position),
    }


def local_position_updated(lat, lon, alt, found) -> dict:
    return {"lat": lat, "lon": lon, "alt": alt, "found": bool(found)}


def channels_updated(channels: list) -> dict:
    """Índice e nome de cada canal. A PSK fica no endpoint do editor."""
    return {"channels": [{"index": idx, "name": name, "has_psk": bool(psk)}
                         for idx, name, psk in channels]}


def message_received(channel: int, from_id: str, text: str, packet: dict) -> dict:
    packet = packet or {}
    return {
        "channel":  int(channel),
        "from_id":  from_id,
        "to_id":    packet.get("toId", ""),
        "text":     text,
        "time":     to_epoch(packet.get("rxTime")),
        "via_mqtt": bool(packet.get("viaMqtt")),
        "pki":      bool(packet.get("pkiEncrypted")),
        "hops":     packet.get("hopsAway"),
        "outgoing": False,
    }


def channel_message_sent(channel_index: int, text: str, packet_id: int) -> dict:
    return {"channel": int(channel_index), "text": text,
            "packet_id": int(packet_id), "outgoing": True, "pki": False}


def dm_sent(dest_id: str, text: str, pki: bool, packet_id: int) -> dict:
    return {"to_id": dest_id, "text": text, "pki": bool(pki),
            "packet_id": int(packet_id), "outgoing": True}


def message_status_updated(request_id: int, status: str, error_reason: str) -> dict:
    return {"packet_id": int(request_id), "status": status,
            "error_reason": error_reason}


def traceroute_result(forward_edges, back_edges, origin, dest) -> dict:
    def edges(raw):
        return [{"from": a, "to": b, "snr": snr} for a, b, snr in raw]

    return {"forward": edges(forward_edges), "back": edges(back_edges),
            "origin": origin, "dest": dest}


def neighbor_info_received(from_id: str, neighbors: list) -> dict:
    return {"from_id": from_id,
            "neighbors": [{"id": nid, "snr": snr} for nid, snr in neighbors]}


def position_sent(success: bool, message: str) -> dict:
    return {"success": bool(success), "message": message}


def packet_projection(packet: dict) -> dict:
    """Só o que a interface mostra de um pacote, não o protobuf inteiro."""
    decoded = packet.get("decoded") or {}
    return {
        "from_id":  packet.get("fromId"),
        "to_id":    packet.get("toId"),
        "portnum":  decoded.get("portnum"),
        "snr":      packet.get("rxSnr"),
        "rssi":     packet.get("rxRssi"),
        "hops":     packet.get("hopsAway"),
        "via_mqtt": bool(packet.get("viaMqtt")),
        "channel":  packet.get("channel"),
        "time":     to_epoch(packet.get("rxTime")),
    }


def node_detail(packet: dict) -> dict:
    """Pacote completo, para o diálogo de detalhe de um nó."""
    return to_jsonable(packet)
