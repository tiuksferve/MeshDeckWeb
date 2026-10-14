"""
Configuração partilhada dos testes: põe a raiz do projecto no sys.path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
