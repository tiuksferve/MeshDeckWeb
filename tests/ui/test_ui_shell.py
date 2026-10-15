"""
Testes de interface: login, abas, idioma e layout no telemóvel.
"""

import pytest

from tests.ui.conftest import PASSWORD, node_id

TABS = ("nodes", "map", "messages", "navigation", "metrics", "config")


def test_login_rejects_wrong_password_then_opens_app(anonymous_page):
    page = anonymous_page
    page.wait_for("!document.getElementById('login').hidden", message="o login não apareceu")
    assert page.js("document.getElementById('app').hidden")

    page.js("document.getElementById('password').value = 'errada'")
    page.click("#login-form button[type=submit]")
    page.wait_for("!document.getElementById('login-error').hidden", message="sem mensagem de erro")
    assert page.js("document.getElementById('app').hidden")

    page.js(f"document.getElementById('password').value = '{PASSWORD}'")
    page.click("#login-form button[type=submit]")
    page.wait_for("!document.getElementById('app').hidden", message="a aplicação não abriu")
    assert page.js("document.getElementById('login').hidden")
    assert not page.drain_errors()


def test_logout_returns_to_login(page):
    page.click("#menu-btn")
    page.click("#logout")
    page.wait_for("!document.getElementById('login').hidden", message="o logout não voltou ao login")


VISIBLE_NODES = ("[...document.querySelectorAll('#panel-nodes [data-id]')]"
                 ".filter((e) => e.offsetParent !== null).map((e) => e.dataset.id)")


@pytest.mark.parametrize("view", ("cards", "table"))
def test_nodes_list_shows_seeded_nodes(page, view):
    page.click(f"#nodes-view [data-view={view}]")
    container = "#nodes-cards" if view == "cards" else "#nodes-table"
    page.wait_for(f"document.querySelector('{container} [data-id=\"{node_id(1)}\"]')?.offsetParent")
    assert page.text("#n-total") == "12"
    ids = page.js(VISIBLE_NODES)
    assert len(ids) == 12 and ids[0] == node_id(0), "o nó local vem primeiro"
    assert "Estação Norte" in page.text(f"{container} [data-id=\"{node_id(1)}\"]")
    page.click("#nodes-view [data-view=cards]")


def search(page, text):
    page.js(f"{{ const s = document.getElementById('search'); s.value = '{text}';"
            " s.dispatchEvent(new Event('input', {bubbles: true})); }")


def test_search_filters_nodes(page):
    search(page, "sintra")
    # O nó local fica sempre na lista; dos outros só sobra o Sintra Repetidor.
    page.wait_for(f"JSON.stringify({VISIBLE_NODES}) === JSON.stringify(['{node_id(0)}', '{node_id(2)}'])",
                  message="a pesquisa não deixou só o nó local e o Sintra Repetidor")
    search(page, "")
    page.wait_for(f"{VISIBLE_NODES}.length === 12")


@pytest.mark.parametrize("tab", TABS)
def test_every_tab_opens_without_js_errors(page, tab):
    page.click(f"[data-tab={tab}]")
    page.wait_for(f"document.getElementById('panel-{tab}').classList.contains('active')")
    # dá tempo ao conteúdo (mapa, iframe das métricas, configuração) para carregar
    page.wait_for("new Promise((r) => setTimeout(() => r(true), 800))")


@pytest.mark.parametrize("tab", TABS)
def test_phone_layout_has_no_horizontal_scroll(page, tab):
    page.viewport(390, 844, mobile=True, touch=True)
    page.click(f"[data-tab={tab}]")
    page.wait_for("new Promise((r) => setTimeout(() => r(true), 600))")
    width = page.js("document.documentElement.scrollWidth")
    assert width <= 390, f"a aba {tab} transborda: {width}px de largura num ecrã de 390px"


def test_language_switch_translates_the_interface(page):
    page.click("#menu-btn")
    page.click("#lang-seg [data-lang=pt]")
    try:
        page.wait_for("document.documentElement.lang === 'pt'", message="o idioma não mudou para PT")
        assert "Mapa" in page.text("[data-tab=map]")
        assert "Mensagens" in page.text("[data-tab=messages]")
        # As secções das métricas vêm do servidor, também traduzidas.
        page.click("[data-tab=metrics]")
        page.wait_for("document.querySelector('#metrics-list li[data-key=overview]')?.innerText.includes('Visão Geral')")
    finally:
        page.js("document.querySelector('#lang-seg [data-lang=en]').click()")
        page.wait_for("document.documentElement.lang === 'en'", message="o idioma não voltou a EN")
    assert "Map" in page.text("[data-tab=map]")


def test_snr_colours_follow_the_preset_sent_by_the_server(page):
    """LongFast por omissão: −8 dB ainda é aceitável (laranja); se o servidor
    anunciar um preset rápido (SF7), o mesmo valor passa a fraco (vermelho)."""
    classes = """import('/js/store.js').then(({store}) => [...document.querySelectorAll('#panel-nodes [data-id] .snr')]
        .filter((e) => e.offsetParent !== null)
        .map((e) => [store.nodes.get(e.closest('[data-id]').dataset.id).snr, e.classList[1]]))"""
    def expected(snr, good, fair):
        return "ok" if snr >= good else "warn" if snr >= fair else "bad"

    pairs = page.js(classes)
    assert pairs, "nenhum nó com SNR visível"
    assert all(cls == expected(snr, -7.5, -12.5) for snr, cls in pairs), pairs

    try:
        page.js("import('/js/store.js').then(({store}) => store.applySnrLevels({good: 2.5, fair: -2.5}))")
        page.wait_for(f"{classes}.then((p) => p.every(([snr, cls]) =>"
                      " cls === (snr >= 2.5 ? 'ok' : snr >= -2.5 ? 'warn' : 'bad')))",
                      message="as cores do SNR não acompanharam a mudança de preset")
    finally:
        page.js("import('/js/store.js').then(({store}) => store.applySnrLevels({good: -7.5, fair: -12.5}))")
