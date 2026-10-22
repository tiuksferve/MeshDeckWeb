"""
constants.py — Nome e versão da aplicação, o logger partilhado e utilitários
usados pelo núcleo e pelo servidor.
"""
import logging

APP_VERSION = "2.3.0-beta"
APP_NAME    = "MeshDeck"
from meshtastic import BROADCAST_NUM

logger = logging.getLogger("MeshDeck")


_BROADCAST_NUMS = {BROADCAST_NUM, 0xFFFFFFFF, 4294967295, -1}


def _is_broadcast(to_num) -> bool:
    try:
        n = int(to_num)
        if n < 0:
            n = n & 0xFFFFFFFF
        return n in _BROADCAST_NUMS or n == 0
    except (TypeError, ValueError):
        return True
