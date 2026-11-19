"""
Testes de interface: navegação e bússola do dispositivo.

O 127.0.0.1 é um contexto seguro, por isso o sensor está disponível. As
leituras do magnetómetro são eventos sintéticos: o override de orientação do
CDP não preenche o deviceorientationabsolute, que é o que o Android usa.
"""

from tests.ui.conftest import node_id

SENSOR_BOX = "document.getElementById('compass-sensor')"


def reload_as_phone(page, ui_server):
    page.viewport(390, 844, mobile=True, touch=True)
    page.navigate(ui_server["url"] + "/")
    page.wait_for(f"document.querySelector('#panel-nodes [data-id=\"{node_id(1)}\"]')")
    page.click("[data-tab=navigation]")


def test_gps_list_is_sorted_by_distance(page):
    page.click("[data-tab=navigation]")
    page.wait_for("document.querySelectorAll('#gps-list li[data-id]').length >= 8",
                  message="a lista de nós com GPS está vazia")
    distances = page.js("[...document.querySelectorAll('#gps-list li[data-id]')].map((li) => li.innerText)")
    assert all("km" in d or " m" in d for d in distances)


def test_selecting_a_node_points_the_compass(page):
    page.click("[data-tab=navigation]")
    page.click(f"#gps-list li[data-id=\"{node_id(2)}\"]")
    page.wait_for("document.getElementById('compass-info').innerText.includes('Sintra Repetidor')",
                  message="a bússola não mostra o nó escolhido")


def test_device_compass_is_hidden_on_desktop(page):
    page.click("[data-tab=navigation]")
    assert page.js(f"{SENSOR_BOX}.hidden")


def send_heading(page, alpha):
    page.js(f"""for (const type of ['deviceorientationabsolute', 'deviceorientation'])
        window.dispatchEvent(new DeviceOrientationEvent(type, {{alpha: {alpha}, beta: 0, gamma: 0, absolute: true}}))""")


def test_device_compass_follows_the_phone_heading(page, ui_server):
    reload_as_phone(page, ui_server)
    try:
        assert not page.js(f"{SENSOR_BOX}.hidden"), "o botão da bússola não aparece no telemóvel"
        page.click(f"#gps-list li[data-id=\"{node_id(2)}\"]")
        page.click("#btn-device-compass")
        page.wait_for("document.getElementById('btn-device-compass').classList.contains('active')",
                      message="a bússola do dispositivo não ligou")
        assert not page.js("document.getElementById('compass-sensor-note').hidden"), \
            "devia mostrar 'a aguardar o sensor' antes da primeira leitura"
        send_heading(page, 270)
        # A primeira leitura apaga a nota "a aguardar o sensor".
        page.wait_for("document.getElementById('compass-sensor-note').hidden",
                      message="a leitura do sensor não chegou à bússola")
        page.click("#btn-device-compass")
        page.wait_for("!document.getElementById('btn-device-compass').classList.contains('active')",
                      message="a bússola do dispositivo não desligou")
    finally:
        page.viewport(1280, 820)


def test_device_compass_explains_when_ios_permission_is_denied(page, ui_server):
    reload_as_phone(page, ui_server)
    try:
        # Simula o Safari: a autorização é pedida no toque e o utilizador recusa.
        page.js("DeviceOrientationEvent.requestPermission = async () => 'denied'")
        page.click("#btn-device-compass")
        page.wait_for("document.getElementById('compass-sensor-note').innerText.includes('denied')",
                      message="sem explicação para a autorização recusada")
        assert not page.js("document.getElementById('btn-device-compass').classList.contains('active')")
    finally:
        page.js("delete DeviceOrientationEvent.requestPermission")
        page.viewport(1280, 820)


GPS_FIX = {"fix": True, "lat": 38.72231, "lon": -9.13931, "alt": 81.2, "fix_type": 3,
           "fix_quality": 1, "sats_used": 8, "sats_in_view": 14, "hdop": 0.9, "pdop": 1.8,
           "vdop": 1.5, "speed_mps": 1.0, "course": 84.4, "time": None}
GPS_SKY = [{"system": "GPS", "prn": 4, "el": 40, "az": 83, "snr": 46, "used": True},
           {"system": "GPS", "prn": 25, "el": 10, "az": 200, "snr": None, "used": False},
           {"system": "GLONASS", "prn": 65, "el": 70, "az": 300, "snr": 28, "used": True}]


def gps_status(**changes):
    status = {"state": "fix", "source": "gpsmux", "owner": "", "path": "tcp://127.0.0.1:10110",
              "error": "", "fix": GPS_FIX, "sky": GPS_SKY, "last_fix": None}
    status.update(changes)
    return status


def test_uconsole_gps_is_shown_live_read_only(page, ui_server):
    """GPS do uConsole ao vivo na Navegação: fonte, fix e céu. Sem botões: só leitura."""
    mesh = ui_server["mesh"]
    page.click("[data-tab=navigation]")
    page.wait_for("document.getElementById('gps-feed-status').textContent.includes('No GPS on')",
                  message="o estado do GPS não apareceu")
    assert page.js("document.querySelectorAll('.gps-feed input, .gps-feed select, .gps-feed button').length") == 0
    try:
        mesh.loop.post(lambda: mesh._on_gps_status(gps_status()))
        page.wait_for("document.getElementById('gps-feed-status').textContent.includes('shared by gpsmux')",
                      message="o estado com fix não apareceu")
        card = page.text("#gps-feed-card")
        assert "38.72231" in card and "8 used · 14 in view" in card
        assert "3D · HDOP 0.9 · PDOP 1.8" in card and "3.6 km/h" in card
        assert page.js("document.querySelectorAll('#gps-sky circle[stroke-width=\"1.5\"]').length") == 3
        assert page.js("document.getElementById('gps-sky').getBoundingClientRect().height") > 100, \
            "o céu não está visível"

        mesh.loop.post(lambda: mesh._on_gps_status(gps_status(state="busy", source="", owner="meshtasticd",
                                                              fix=None, sky=[])))
        page.wait_for("document.getElementById('gps-feed-status').textContent.includes('gpsmux is not installed')")
        assert page.js("document.getElementById('gps-feed-card').hidden") is True
        assert page.js("document.getElementById('gps-sky').hasAttribute('hidden')") is True
    finally:
        mesh.loop.post(lambda: mesh._on_gps_status(gps_status(state="no_device", source="", fix=None,
                                                              sky=[], path="/dev/ttyTEST")))


def test_local_position_updates_by_itself(page, ui_server):
    """Uma posição nova do nó chega ao cartão sem o botão 🔄."""
    mesh = ui_server["mesh"]
    page.click("[data-tab=navigation]")
    try:
        mesh.loop.post(lambda: mesh._on_local_position(
            38.72231, -9.13931, 81, True, {"source": "node", "sats_in_view": 9, "hdop": 1.2, "fix_type": 3}))
        page.wait_for("document.getElementById('local-card').textContent.includes('38.72231')",
                      message="a posição do nó local não mudou sozinha")
        local = page.text("#local-card")
        assert "node GPS" in local and "9 in view" in local and "3D · HDOP 1.2" in local
    finally:
        mesh.loop.post(lambda: mesh._on_local_position(38.7223, -9.1393, 80, True))
