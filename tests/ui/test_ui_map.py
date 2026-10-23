"""
Testes de interface: mapa (marcadores, mosaicos do servidor, legenda,
satélite, mapa offline, painel lateral no telemóvel) e o 📍 da navegação.
"""

import time

from tests.ui.conftest import node_id

# Nós no mapa: os soltos e os que estão dentro de um grupo (o círculo traz o número).
MARKERS = ("document.querySelectorAll('#map-canvas .map-node-pill').length"
           " + [...document.querySelectorAll('#map-canvas .map-cluster-dot')]"
           ".reduce((s, c) => s + Number(c.dataset.count), 0)")
WITH_GPS = 9            # 12 nós, 3 sem posição


def open_map(page):
    page.click("[data-tab=map]")
    page.wait_for(f"{MARKERS} >= {WITH_GPS}", message="os marcadores dos nós não apareceram")


PILLS = "document.querySelectorAll('#map-canvas .map-node-pill').length"
CLUSTERS = "document.querySelectorAll('#map-canvas .map-cluster-dot').length"


def zoom_until_ungrouped(page):
    for _ in range(8):
        if page.js(PILLS) == WITH_GPS:
            return
        page.js("document.querySelector('.leaflet-control-zoom-in').click()")
        time.sleep(0.4)
    assert page.js(PILLS) == WITH_GPS, "os grupos não se desfizeram ao aproximar"


def click_dialog_button(page, label):
    page.wait_for(f"[...document.querySelectorAll('.modal-actions button')].find((b) => b.offsetParent"
                  f" && b.textContent.trim() === {label!r})",
                  message=f"sem diálogo com o botão {label}")
    page.js(f"[...document.querySelectorAll('.modal-actions button')].find((b) => b.offsetParent"
            f" && b.textContent.trim() === {label!r}).click()")


def test_map_draws_a_marker_per_node_with_gps(page):
    open_map(page)
    assert page.js(MARKERS) == WITH_GPS


def test_markers_show_the_short_name_with_a_finger_sized_hit_area(page):
    open_map(page)
    zoom_until_ungrouped(page)
    labels = page.js("[...document.querySelectorAll('#map-canvas .map-node-pill')].map((p) => p.textContent)")
    assert all(1 <= len(label) <= 4 for label in labels)
    sizes = page.js("[...document.querySelectorAll('#map-canvas .map-node')]"
                    ".map((m) => [m.offsetWidth, m.offsetHeight])")
    assert all(w >= 44 and h >= 44 for w, h in sizes)


def test_overlapping_nodes_group_and_open_on_tap(page):
    open_map(page)
    # Na vista inicial (zoom 8) os nós de teste, a ~30 km uns dos outros, sobrepõem-se.
    page.wait_for(f"{CLUSTERS} > 0", message="os nós sobrepostos não se agruparam")
    assert page.js(MARKERS) == WITH_GPS, "um grupo não conta os nós que tem dentro"
    pills = page.js(PILLS)
    page.js("document.querySelector('#map-canvas .map-cluster-dot').click()")
    page.wait_for(f"{PILLS} > {pills}", message="tocar no grupo não o abriu")
    # Durante a animação os grupos antigos ainda estão a desaparecer.
    page.wait_for(f"{MARKERS} === {WITH_GPS}", message="nós a mais ou a menos depois de abrir o grupo")


def test_focusing_a_grouped_node_opens_its_group(page):
    open_map(page)
    page.wait_for(f"{CLUSTERS} > 0")
    # Um nó que está dentro de um grupo na vista inicial.
    grouped = page.js("""(() => {
      const shown = new Set([...document.querySelectorAll('#map-canvas .map-node-pill')].map((p) => p.textContent));
      return ['BL', 'EN1', 'SNT'].find((n) => !shown.has(n));
    })()""")
    assert grouped, "nenhum dos nós esperados está agrupado"
    target = {"BL": node_id(0), "EN1": node_id(1), "SNT": node_id(2)}[grouped]
    page.js(f"import('/js/tabs/map.js').then((m) => m.focusNode('{target}'))")
    page.wait_for("document.querySelector('.leaflet-popup-content')",
                  message="o popup de um nó agrupado não abriu")
    assert page.js(f"[...document.querySelectorAll('#map-canvas .map-node-pill')]"
                   f".some((p) => p.textContent === {grouped!r})"), "o grupo não se abriu"


def test_tapping_a_marker_selects_the_node_and_keeps_the_popup(page):
    page.viewport(390, 844, mobile=True, touch=True)
    open_map(page)
    target = node_id(2)
    page.js(f"import('/js/tabs/map.js').then((m) => m.focusNode('{target}'))")
    page.wait_for("document.querySelector('.leaflet-popup-content')")
    page.js("document.querySelector('.leaflet-popup-close-button').click()")
    # Toca na borda da área de toque, fora da etiqueta: tem de contar na mesma.
    page.js("""(() => {
      const pill = [...document.querySelectorAll('#map-canvas .map-node-pill')]
        .find((p) => p.textContent === 'SNT');
      const box = pill.parentElement.getBoundingClientRect();
      const el = document.elementFromPoint(box.left + 2, box.top + 2);
      el.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: box.left + 2, clientY: box.top + 2 }));
    })()""")
    page.wait_for("document.querySelector('.leaflet-popup-content')?.innerText.includes('Sintra Repetidor')",
                  message="o toque na borda do marcador não abriu o popup")
    page.wait_for("document.querySelector('#map-canvas .map-node-selected')?.textContent === 'SNT'",
                  message="o nó tocado não ficou seleccionado")
    assert page.js("!!document.querySelector('.leaflet-popup-content')"), "o popup fechou ao seleccionar"


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
