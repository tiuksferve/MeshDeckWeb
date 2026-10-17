"""
core/messages.py — Conversas e estado de entrega, sem interface.

As mensagens vivem no servidor, fora da interface: há vários browsers a ver
a mesma conversa e um reload não pode apagar o histórico.

A regra de encaminhamento é a mesma da aba de mensagens: uma mensagem é
directa se vier cifrada com PKI, se o destino for o nó local, ou se o destino
não for broadcast. Caso contrário pertence ao canal por onde chegou.

Persistência em disco está fora de âmbito: o histórico vive na memória do
processo e perde-se ao reiniciar o servidor.
"""

import itertools
import threading
from datetime import datetime
from typing import Dict, List, Optional

from constants import _is_broadcast
from core.serialize import to_epoch

CHANNEL_PREFIX = "ch:"
DM_PREFIX = "dm:"


def channel_key(index: int) -> str:
    return f"{CHANNEL_PREFIX}{int(index)}"


def dm_key(node_id: str) -> str:
    return f"{DM_PREFIX}{node_id.lower()}"


def is_dm_key(key: str) -> bool:
    return key.startswith(DM_PREFIX)


def _node_num(node_id: Optional[str]) -> Optional[int]:
    if not node_id or not node_id.startswith("!"):
        return None
    try:
        return int(node_id[1:], 16) & 0xFFFFFFFF
    except ValueError:
        return None


class MessageStore:
    """Conversas por canal e por correspondente, com estado de ACK.

    Seguro para uso concorrente: os comandos chegam de threads de pedido HTTP
    e as mensagens recebidas da thread do CoreLoop.
    """

    MAX_PER_CONVERSATION = 500

    def __init__(self):
        self._lock = threading.RLock()
        self._conversations: Dict[str, List[dict]] = {}
        self._pending_ack: Dict[int, dict] = {}
        self._dm_last_activity: Dict[str, float] = {}
        self._unread: Dict[str, int] = {}
        self._ids = itertools.count(1)
        self._local_id: Optional[str] = None
        self._local_num: Optional[int] = None

    def set_local_node(self, node_id: Optional[str]):
        with self._lock:
            self._local_id = node_id.lower() if node_id else None
            self._local_num = _node_num(self._local_id)

    # -- entrada ----------------------------------------------------------
    def add_incoming(self, channel: int, from_id: str, text: str,
                     packet: dict) -> Optional[tuple]:
        """Guarda uma mensagem recebida. Devolve (chave, entrada) ou None.

        Devolve None para o eco das nossas próprias mensagens, que o daemon
        reenvia e que já estão na conversa como enviadas.
        """
        packet = packet or {}
        from_id = (from_id or "").lower()
        if not from_id or (self._local_id and from_id == self._local_id):
            return None

        pki = bool(packet.get("pkiEncrypted"))
        via_mqtt = bool(packet.get("viaMqtt"))
        to_id = (packet.get("toId") or "").lower()

        try:
            to_num = int(packet.get("to")) & 0xFFFFFFFF
        except (TypeError, ValueError):
            to_num = 0xFFFFFFFF

        direct = bool(
            pki
            or (to_id and to_id == self._local_id)
            or (self._local_num is not None and to_num == self._local_num)
            or (not _is_broadcast(to_num) and to_num != 0)
        )

        key = dm_key(from_id) if direct else channel_key(channel)
        entry = self._entry(
            from_id=from_id, text=text, outgoing=False, direct=direct,
            timestamp=to_epoch(packet.get("rxTime")) or datetime.now().timestamp(),
            transport="pki" if pki else ("mqtt" if via_mqtt else "rf"),
            hops=packet.get("hopsAway"),
            packet_id=packet.get("id") or 0,
            reply_id=(packet.get("decoded") or {}).get("replyId") or 0,
        )
        with self._lock:
            if direct:
                self._dm_last_activity[from_id] = entry["time"]
            self._append(key, entry, count_unread=True)
        return key, entry

    def add_outgoing_channel(self, channel: int, text: str, packet_id: int = 0,
                             reply_id: int = 0) -> tuple:
        key = channel_key(channel)
        entry = self._entry(from_id=self._local_id or "", text=text, outgoing=True,
                            direct=False, transport="rf", packet_id=packet_id,
                            reply_id=reply_id)
        with self._lock:
            self._append(key, entry, count_unread=False)
        return key, entry

    def add_outgoing_dm(self, dest_id: str, text: str, pki: bool = False,
                        packet_id: int = 0, reply_id: int = 0) -> tuple:
        key = dm_key(dest_id)
        entry = self._entry(from_id=self._local_id or "", text=text, outgoing=True,
                            direct=True, transport="pki" if pki else "psk",
                            packet_id=packet_id, to_id=dest_id.lower(),
                            reply_id=reply_id)
        with self._lock:
            self._dm_last_activity[dest_id.lower()] = entry["time"]
            self._append(key, entry, count_unread=False)
        return key, entry

    def update_status(self, packet_id: int, status: str,
                      error_reason: str = "") -> Optional[dict]:
        """Marca ACK/NAK na mensagem enviada. Devolve a entrada actualizada."""
        with self._lock:
            entry = self._pending_ack.get(packet_id)
            if entry is None:
                return None
            entry["status"] = status
            entry["status_detail"] = error_reason or ""
            if status in ("ack", "ack_implicit", "nak"):
                self._pending_ack.pop(packet_id, None)
            return dict(entry)

    # -- leitura ----------------------------------------------------------
    def conversation(self, key: str) -> List[dict]:
        with self._lock:
            return [dict(e) for e in self._conversations.get(key, ())]

    def keys(self) -> List[str]:
        with self._lock:
            return list(self._conversations)

    def unread(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._unread)

    def mark_read(self, key: str):
        with self._lock:
            self._unread.pop(key, None)

    def dm_partners(self) -> List[str]:
        """Correspondentes ordenados pela actividade mais recente."""
        with self._lock:
            return [nid for nid, _ts in sorted(self._dm_last_activity.items(),
                                               key=lambda kv: kv[1], reverse=True)]

    def snapshot(self) -> dict:
        """Histórico completo, pronto para JSON, para um browser que abre agora."""
        with self._lock:
            return {
                "conversations": {k: [dict(e) for e in v]
                                  for k, v in self._conversations.items()},
                "unread": dict(self._unread),
                "dm_partners": [nid for nid, _ts in
                                sorted(self._dm_last_activity.items(),
                                       key=lambda kv: kv[1], reverse=True)],
            }

    def clear(self):
        with self._lock:
            self._conversations.clear()
            self._pending_ack.clear()
            self._dm_last_activity.clear()
            self._unread.clear()

    # -- interno ----------------------------------------------------------
    def _entry(self, *, from_id: str, text: str, outgoing: bool, direct: bool,
               transport: str, timestamp: float = None, packet_id: int = 0,
               hops=None, to_id: str = "", reply_id: int = 0) -> dict:
        return {
            "id":            next(self._ids),
            "time":          timestamp if timestamp is not None else datetime.now().timestamp(),
            "from_id":       from_id,
            "to_id":         to_id,
            "text":          text,
            "outgoing":      outgoing,
            "direct":        direct,
            "transport":     transport,   # rf | mqtt | pki | psk
            "hops":          hops,
            "packet_id":     int(packet_id or 0),
            "reply_id":      int(reply_id or 0),   # packet_id da mensagem respondida
            "status":        "sending" if outgoing else "",
            "status_detail": "",
        }

    def _append(self, key: str, entry: dict, count_unread: bool):
        conv = self._conversations.setdefault(key, [])
        conv.append(entry)
        conv.sort(key=lambda e: e["time"])
        if len(conv) > self.MAX_PER_CONVERSATION:
            del conv[:-self.MAX_PER_CONVERSATION]
        if entry["outgoing"] and entry["packet_id"]:
            self._pending_ack[entry["packet_id"]] = entry
        if count_unread:
            self._unread[key] = self._unread.get(key, 0) + 1
