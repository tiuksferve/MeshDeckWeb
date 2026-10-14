"""
constants.py — Versão, paleta de cores das métricas, logging e utilitários
partilhados pelo núcleo e pelo servidor.
"""
import logging

APP_VERSION = "2.0.0-beta"
APP_NAME    = "MeshDeck"
from meshtastic import BROADCAST_NUM

logging.getLogger().setLevel(logging.INFO)
for _h in logging.root.handlers[:]:
    logging.root.removeHandler(_h)
logging.getLogger("meshtastic").setLevel(logging.WARNING)
logging.getLogger("pubsub").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("MeshDeck")

DARK_BG        = "#0d1117"
PANEL_BG       = "#161b22"
BORDER_COLOR   = "#30363d"
ACCENT_GREEN   = "#39d353"
ACCENT_BLUE    = "#58a6ff"
ACCENT_ORANGE  = "#f0883e"
ACCENT_RED     = "#f85149"
TEXT_PRIMARY   = "#e6edf3"
TEXT_MUTED     = "#8b949e"


_BROADCAST_NUMS = {BROADCAST_NUM, 0xFFFFFFFF, 4294967295, -1}


def _is_broadcast(to_num) -> bool:
    try:
        n = int(to_num)
        if n < 0:
            n = n & 0xFFFFFFFF
        return n in _BROADCAST_NUMS or n == 0
    except (TypeError, ValueError):
        return True