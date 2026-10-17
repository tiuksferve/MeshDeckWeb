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
