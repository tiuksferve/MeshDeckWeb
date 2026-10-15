"""
Testes de interface: mensagens, métricas e configuração do nó.
"""

import pytest

from core.metrics import SECTION_KEYS
from tests.ui.conftest import node_id

FRAME = "document.getElementById('metrics-frame')"


# ---------------------------------------------------------------------------
# Mensagens
# ---------------------------------------------------------------------------
def open_conversation(page, key):
    page.click("[data-tab=messages]")
    page.wait_for(f"document.querySelector('li[data-key=\"{key}\"]')", message=f"sem a conversa {key}")
    page.click(f"li[data-key=\"{key}\"]")


def test_channel_conversation_shows_received_messages(page):
    open_conversation(page, "ch:0")
    page.wait_for("document.getElementById('conv-view').innerText.includes('Hello everyone')",
                  message="as mensagens do canal não aparecem")
    assert "Good morning" in page.text("#conv-view")
    assert "Direct hello" not in page.text("#conv-view"), "uma DM apareceu no canal"


def test_direct_message_has_its_own_conversation(page):
    open_conversation(page, f"dm:{node_id(4)}")
    page.wait_for("document.getElementById('conv-view').innerText.includes('Direct hello')",
                  message="a DM não aparece na conversa do remetente")


def test_reply_shows_and_cancels_the_reply_banner(page):
    open_conversation(page, "ch:0")
    page.wait_for("document.querySelector('#conv-view .reply-btn')", message="sem botão ↩ nas mensagens")
    page.click("#conv-view .reply-btn")
    page.wait_for("!document.getElementById('reply-banner').hidden", message="a barra de resposta não abriu")
    page.click("#reply-cancel")
    page.wait_for("document.getElementById('reply-banner').hidden", message="a resposta não foi cancelada")


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------
def test_metrics_list_has_every_section(page):
    page.click("[data-tab=metrics]")
    page.wait_for("document.querySelectorAll('#metrics-list li[data-key]').length")
    keys = page.js("[...document.querySelectorAll('#metrics-list li[data-key]')].map((li) => li.dataset.key)")
    assert keys == list(SECTION_KEYS)


@pytest.mark.parametrize("section", SECTION_KEYS)
def test_metrics_section_renders(page, section):
    page.click("[data-tab=metrics]")
    page.click(f"#metrics-list li[data-key={section}]")
    page.wait_for(f"{FRAME}.src.includes('/api/metrics/{section}?')"
                  f" && {FRAME}.contentDocument?.readyState === 'complete'"
                  f" && {FRAME}.contentDocument.body?.innerText.trim().length > 20",
                  timeout=15, message=f"a secção {section} não carregou")
    text = page.js(f"{FRAME}.contentDocument.body.innerText")
    assert "Traceback" not in text and "Internal Server Error" not in text
    # Gráficos (Chart.js, servido localmente) desenhados sem erros JS — o
    # fixture page falha o teste se a página do iframe lançar excepções.
    page.wait_for("new Promise((r) => setTimeout(() => r(true), 500))")


def test_metrics_charts_are_drawn(page):
    page.click("[data-tab=metrics]")
    page.click("#metrics-list li[data-key=rf]")
    page.wait_for(f"{FRAME}.src.includes('/api/metrics/rf?')"
                  f" && {FRAME}.contentDocument?.querySelector('canvas')"
                  f" && {FRAME}.contentWindow.Chart"
                  f" && Object.keys({FRAME}.contentWindow.Chart.instances).length > 0",
                  timeout=15, message="a secção RF não desenhou gráficos")


# ---------------------------------------------------------------------------
# Configuração do nó
# ---------------------------------------------------------------------------
def test_config_sections_open_with_fields(page):
    page.click("[data-tab=config]")
    page.wait_for("document.querySelectorAll('#config-list li[data-key]').length >= 10",
                  message="a lista de secções da configuração não carregou")
    keys = page.js("[...document.querySelectorAll('#config-list li[data-key]')].map((li) => li.dataset.key)")
    for key in keys[:4]:
        page.click(f"#config-list li[data-key=\"{key}\"]")
        page.wait_for("document.querySelector('#config-body input, #config-body select')",
                      message=f"a secção {key} da configuração não tem campos")
