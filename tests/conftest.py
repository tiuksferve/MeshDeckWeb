"""
Configuração partilhada dos testes: põe a raiz do projecto no sys.path e
regista em INFO, como o servidor (run_server.py) — a consola do browser e
os relatórios de falhas mostram as mesmas linhas.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.getLogger().setLevel(logging.INFO)
for _name in ("meshtastic", "pubsub", "urllib3"):
    logging.getLogger(_name).setLevel(logging.WARNING)


# O GPS do uConsole nunca se lê nos testes: não há gpsmux, ninguém tem o GPS
# e a porta não abre, esteja ou não o meshtasticd a correr na máquina. Os
# testes do GPS usam as verdadeiras (REAL_*) ou as fontes falsas deles.
from core import gps_feed as _gps_feed  # noqa: E402


class _NoSerialReader:
    def __init__(self, path, on_line, on_error):
        self._on_error = on_error
        self.alive = False

    def start(self):
        self._on_error("no serial in tests")

    def stop(self):
        pass


# O conftest pode ser importado duas vezes (conftest e tests.conftest): as
# verdadeiras ficam no módulo da primeira vez.
if not hasattr(_gps_feed, "_real_gps_owner"):
    _gps_feed._real_gps_owner = _gps_feed.gps_owner
    _gps_feed._real_detect_serial_path = _gps_feed.detect_serial_path
REAL_GPS_OWNER = _gps_feed._real_gps_owner
REAL_DETECT_SERIAL_PATH = _gps_feed._real_detect_serial_path
_gps_feed.gps_owner = lambda *args, **kwargs: None
_gps_feed.detect_serial_path = lambda *args, **kwargs: "/dev/ttyTEST"
_gps_feed.mux_available = lambda *args, **kwargs: False
_gps_feed.SerialNmeaReader = _NoSerialReader
