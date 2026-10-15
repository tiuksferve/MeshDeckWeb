"""
Testes de interface: mapa (marcadores, mosaicos do servidor, legenda,
satélite, mapa offline, painel lateral no telemóvel) e o 📍 da navegação.
"""

from tests.ui.conftest import node_id

MARKERS = "document.querySelectorAll('#map-canvas path.leaflet-interactive').length"
WITH_GPS = 9            # 12 nós, 3 sem posição


def open_map(page):
    page.click("[data-tab=map]")
    page.wait_for(f"{MARKERS} >= {WITH_GPS}", message="os marcadores dos nós não apareceram")


def click_dialog_button(page, label):
    page.wait_for(f"[...document.querySelectorAll('.modal-actions button')].find((b) => b.offsetParent"
                  f" && b.textContent.trim() === {label!r})",
                  message=f"sem diálogo com o botão {label}")
    page.js(f"[...document.querySelectorAll('.modal-actions button')].find((b) => b.offsetParent"
            f" && b.textContent.trim() === {label!r}).click()")


def test_map_draws_a_marker_per_node_with_gps(page):
    open_map(page)
    assert page.js(MARKERS) == WITH_GPS


def test_osm_tiles_come_from_the_server_cache(page, ui_server):
    open_map(page)
    page.wait_for("[...document.querySelectorAll('#map-canvas .leaflet-tile-pane img')]"
                  ".some((i) => i.complete && i.naturalWidth === 1)",
                  message="nenhum mosaico carregou")
    sources = page.js("[...document.querySelectorAll('#map-canvas .leaflet-tile-pane img')].map((i) => i.src)")
    assert sources and all("/tiles/osm/" in src for src in sources)
    assert ui_server["app"].tiles.stats()["tiles"] > 0, "o servidor não guardou os mosaicos"


def test_node_popup_has_colour_legend(page):
    open_map(page)
    page.js(f"import('/js/tabs/map.js').then((m) => m.focusNode('{node_id(2)}'))")
    page.wait_for("document.querySelector('.leaflet-popup-content .map-legend-row')",
                  message="o popup não tem a legenda das cores")
    assert "Sintra Repetidor" in page.text(".leaflet-popup-content")


def test_satellite_theme_adds_label_overlays(page):
    open_map(page)
    page.click("[data-theme=satellite]")
    try:
        page.wait_for("document.querySelectorAll('#map-canvas .leaflet-tile-pane .leaflet-layer').length === 3",
                      message="o satélite não tem as camadas de estradas e nomes por cima")
        sources = page.js("[...document.querySelectorAll('#map-canvas .leaflet-tile-pane img')].map((i) => i.src)")
        assert any("World_Imagery" in s for s in sources)
        assert any("World_Boundaries_and_Places" in s for s in sources)
        assert not any("/tiles/osm/" in s for s in sources), "o satélite não passa pela cache do OSM"
    finally:
        page.click("[data-theme=light]")
    page.wait_for("document.querySelectorAll('#map-canvas .leaflet-tile-pane .leaflet-layer').length === 1")


def test_offline_map_shows_usage_and_clears(page, ui_server):
    open_map(page)
    page.wait_for("document.querySelector('#map-canvas .leaflet-tile-pane img')?.complete")
    page.wait_for("document.getElementById('tile-cache-info').innerText.trim()",
                  message="o mapa offline não mostra quanto ocupa")
    tiles = ui_server["app"].tiles
    cleared = []
    tiles.clear = lambda: cleared.append(1) or type(tiles).clear(tiles)
    try:
        page.click("#tile-cache-clear")
        click_dialog_button(page, "Clear")
        # A página mostra a contagem devolvida pelo pedido de limpeza (0); o mapa
        # pode voltar a pedir mosaicos logo a seguir, por isso não se conta no disco.
        page.wait_for("/· 0 tiles$/.test(document.getElementById('tile-cache-info').innerText.trim())",
                      message="a página não mostrou o mapa offline vazio")
        assert cleared == [1]
    finally:
        del tiles.clear


def test_phone_side_sheet_opens_and_closes(page):
    page.viewport(390, 844, mobile=True, touch=True)
    open_map(page)
    assert page.js("getComputedStyle(document.getElementById('map-side')).visibility") == "hidden"
    page.click("#map-side-toggle")
    page.wait_for("getComputedStyle(document.getElementById('map-side')).visibility === 'visible'",
                  message="o painel lateral não abriu")
    assert page.js("document.getElementById('tr-list').offsetParent !== null")
    page.click("#map-side-close")
    page.wait_for("getComputedStyle(document.getElementById('map-side')).visibility === 'hidden'",
                  message="o painel lateral não fechou")


def test_gps_list_pin_opens_the_map_on_that_node(page):
    page.click("[data-tab=navigation]")
    target = node_id(5)
    page.wait_for(f"document.querySelector('#gps-list li[data-id=\"{target}\"] [data-action=map]')",
                  message="a lista de GPS não tem o botão 📍")
    page.click(f"#gps-list li[data-id=\"{target}\"] [data-action=map]")
    page.wait_for("document.getElementById('panel-map').classList.contains('active')",
                  message="o 📍 não abriu o mapa")
    page.wait_for("document.querySelector('.leaflet-popup-content')?.innerText.includes('T-Beam Tiago')",
                  message="o mapa não abriu o popup do nó escolhido")
