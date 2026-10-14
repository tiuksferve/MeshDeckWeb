"""
Configuração partilhada dos testes.

O ponto crítico é a ordem de importação do Qt: o QtWebEngineWidgets tem de ser
importado antes de existir qualquer QCoreApplication, senão o PyQt5 levanta
ImportError e, em alguns casos, aborta o processo. Como o conftest é carregado
antes dos módulos de teste, é aqui que isso fica garantido — e é também por
isso que só pode existir uma aplicação Qt por sessão de testes.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")

try:
    import PyQt5.QtWebEngineWidgets  # noqa: F401  (antes de qualquer QCoreApplication)
    from PyQt5.QtWidgets import QApplication
    _QT_AVAILABLE = True
except ImportError:  # pragma: no cover — ambiente sem PyQt5
    _QT_AVAILABLE = False


@pytest.fixture(scope="session")
def qapp():
    """QApplication única da sessão de testes.

    QApplication (e não QCoreApplication) porque o QWebEngine precisa da
    pilha de widgets. Nenhum teste chama show(), por isso não aparecem janelas.
    """
    if not _QT_AVAILABLE:
        pytest.skip("PyQt5 com QtWebEngine não disponível")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app
