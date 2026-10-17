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
    assert len(ids) == 11 and node_id(0) not in ids, "o nó local fica no cabeçalho, não na lista"
    assert "Estação Norte" in page.text(f"{container} [data-id=\"{node_id(1)}\"]")
    page.click("#nodes-view [data-view=cards]")


def search(page, text):
    page.js(f"{{ const s = document.getElementById('search'); s.value = '{text}';"
            " s.dispatchEvent(new Event('input', {bubbles: true})); }")


def test_search_filters_nodes(page):
    search(page, "sintra")
    page.wait_for(f"JSON.stringify({VISIBLE_NODES}) === JSON.stringify(['{node_id(2)}'])",
                  message="a pesquisa não deixou só o Sintra Repetidor")
    search(page, "")
    page.wait_for(f"{VISIBLE_NODES}.length === 11")


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


def test_connection_label_is_short_on_phones(page):
    cases = page.js("""import('/js/utils.js').then(({connectionLabel: c}) => [
        c({mode: 'serial', serial_port: '/dev/cu.usbmodem1101'}, true),
        c({mode: 'serial', serial_port: '/dev/ttyACM0'}, true),
        c({mode: 'serial', serial_port: '/dev/cu.usbserial-0001'}, true),
        c({mode: 'serial', serial_port: 'COM3'}, true),
        c({mode: 'serial', serial_port: '/dev/cu.usbmodem1101'}),
        c({mode: 'tcp', host: 'localhost', port: 4403}, true),
        c({mode: 'tcp', host: '192.168.1.50', port: 4500}, true),
        c({mode: 'tcp', host: 'localhost', port: 4403})])""")
    assert cases == ["usb1101", "ACM0", "usb0001", "COM3", "/dev/cu.usbmodem1101",
                     "localhost", "192.168.1.50:4500", "localhost:4403"]


def test_phone_header_shows_local_short_name_on_the_connection_line(page):
    page.viewport(390, 844, mobile=True, touch=True)
    saved = page.js("import('/js/store.js').then(({store}) => JSON.stringify(store.connection))")
    try:
        page.js("""import('/js/store.js').then(({store}) => store.applyConnection({connected: true,
            target: {mode: 'serial', serial_port: '/dev/cu.usbmodem1101'}, bridge: {}, node_ready: true}))""")
        page.wait_for("document.getElementById('conn-state').innerText.trim() === '🟢 usb1101'",
                      message="o botão de ligação não mostrou o rótulo curto")
        assert page.js("document.querySelector('#node-short .node-badge')?.innerText") == "BL"
        # Botão e nome curto na mesma linha, sem quebra.
        tops = page.js("['brand', 'conn-state', 'node-short'].map((c) =>"
                       " (document.getElementById(c) || document.querySelector('.' + c)).getBoundingClientRect().top)")
        assert abs(tops[1] - tops[2]) < 8 and abs(tops[0] - tops[1]) < 12, tops
        # O nó local já está no cabeçalho: não aparece na lista.
        ids = page.js(VISIBLE_NODES)
        assert node_id(0) not in ids and node_id(1) in ids
    finally:
        page.js(f"import('/js/store.js').then(({{store}}) => store.applyConnection({saved}))")
        page.viewport(1280, 820)
    assert not page.js("document.getElementById('node-short').offsetParent"), "no desktop fica o nome completo"
    assert page.text("#node-label") == "🏠 Base Lisboa [BL]"


def test_language_switch_translates_the_interface(page):
    import i18n
    page.click("#menu-btn")
    page.click("#lang-seg [data-lang=pt]")
    try:
        page.wait_for("document.documentElement.lang === 'pt'", message="o idioma não mudou para PT")
        assert "Mapa" in page.text("[data-tab=map]")
        assert "Mensagens" in page.text("[data-tab=messages]")
        # As secções das métricas e o HTML de cada uma vêm do servidor, também traduzidos.
        page.click("[data-tab=metrics]")
        page.wait_for("document.querySelector('#metrics-list li[data-key=overview]')?.innerText.includes('Visão Geral')")
        page.wait_for("document.getElementById('metrics-frame').contentDocument?.body?.innerText.includes('Pacotes')",
                      message="o HTML das métricas não veio em PT")
        # A escolha é deste dispositivo: fica no cookie e não muda o servidor.
        assert "meshdeck_lang=pt" in page.js("document.cookie")
        assert i18n.get_language() == "en"
    finally:
        page.js("document.querySelector('#lang-seg [data-lang=en]').click()")
        page.wait_for("document.documentElement.lang === 'en'", message="o idioma não voltou a EN")
    assert "Map" in page.text("[data-tab=map]")


def test_language_defaults_to_the_device_and_the_choice_is_remembered(page):
    detect = ("import('/js/i18n.js').then(({preferredLanguage}) => preferredLanguage())")
    page.js("document.cookie = 'meshdeck_lang=; path=/; max-age=0'")
    try:
        page.js("Object.defineProperty(navigator, 'languages', {value: ['pt-PT', 'en'], configurable: true})")
        assert page.js(detect) == "pt", "sem escolha, segue a língua do dispositivo"
        page.js("Object.defineProperty(navigator, 'languages', {value: ['de-DE'], configurable: true})")
        assert page.js(detect) == "en"
        page.js("import('/js/i18n.js').then(({rememberLanguage}) => rememberLanguage('pt'))")
        assert page.js(detect) == "pt", "a escolha no menu ganha à língua do dispositivo"
    finally:
        page.js("document.cookie = 'meshdeck_lang=en; path=/'")


def test_dates_follow_the_language_and_the_device_region(page):
    cookie = "document.cookie.split('; ').find((c) => c.startsWith('meshdeck_dates='))"
    locale = "import('/js/i18n.js').then(({dateLocale}) => dateLocale())"
    assert page.js(cookie) == "meshdeck_dates=en-US.mdy.12"
    page.js("Object.defineProperty(navigator, 'languages', {value: ['en-GB'], configurable: true})")
    assert page.js(locale) == "en-GB", "dispositivo em inglês: segue a região dele"
    page.js("Object.defineProperty(navigator, 'languages', {value: ['en-US'], configurable: true})")
    page.click("#menu-btn")
    page.click("#lang-seg [data-lang=pt]")
    try:
        page.wait_for("document.documentElement.lang === 'pt'")
        assert page.js(locale) == "pt-PT", "dispositivo noutra língua: o habitual da escolhida"
        assert page.js(cookie) == "meshdeck_dates=pt-PT.dmy.24"
        when = "import('/js/utils.js').then(({fmtTime}) => fmtTime(Date.UTC(2020, 0, 2, 15, 4) / 1000))"
        assert "PM" not in page.js(when)
    finally:
        page.js("document.querySelector('#lang-seg [data-lang=en]').click()")
        page.wait_for("document.documentElement.lang === 'en'")
    assert page.js(cookie) == "meshdeck_dates=en-US.mdy.12"


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
