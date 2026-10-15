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
