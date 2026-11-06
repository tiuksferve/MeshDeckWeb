"""
Testes do instalador e da mensagem de arranque: a parte do macOS (firewall,
IP que muda) só se vê num Mac, por isso os comandos do sistema são simulados.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import i18n
import install
from core.settings import Settings
from server import auth, tls
from server.auth import ServerConfig


def fake_firewall(state="enabled", app_line="The application is not part of the firewall", blockall="disabled"):
    def run(args):
        option = args[1]
        return {"--getglobalstate": f"Firewall is {state}. (State = 1)",
                "--getappblocked": app_line,
                "--getblockall": f"Firewall has block all state set to {blockall}."}[option]
    return run


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(install.sys, "platform", "darwin")
    monkeypatch.setattr(install.os.path, "exists", lambda p: True)
    monkeypatch.setattr(install, "lang", "en")
    calls = []
    monkeypatch.setattr(install.subprocess, "call", lambda c: calls.append(c) or 0)
    return calls


def test_firewall_on_offers_to_allow_the_python_app(mac, monkeypatch, capsys):
    monkeypatch.setattr(install, "ask_yes", lambda *a, **k: True)
    install.macos_firewall(run=fake_firewall())
    app = install.macos_python_app()
    assert mac == [["sudo", install.MAC_FIREWALL, "--add", app],
                   ["sudo", install.MAC_FIREWALL, "--unblockapp", app]]
    assert "Python allowed" in capsys.readouterr().out


def test_firewall_declined_prints_the_commands(mac, monkeypatch, capsys):
    monkeypatch.setattr(install, "ask_yes", lambda *a, **k: False)
    install.macos_firewall(run=fake_firewall(blockall="enabled"))
    out = capsys.readouterr().out
    assert mac == []
    assert "--unblockapp" in out and "Block all incoming" in out


@pytest.mark.parametrize("firewall", [
    fake_firewall(state="disabled"),
    fake_firewall(app_line="The application /x/Python.app is permitted"),
])
def test_firewall_quiet_when_nothing_to_do(mac, monkeypatch, firewall):
    monkeypatch.setattr(install, "ask_yes", lambda *a, **k: pytest.fail("não devia perguntar"))
    install.macos_firewall(run=firewall)
    assert mac == []


def test_firewall_ignored_outside_macos(monkeypatch):
    monkeypatch.setattr(install.sys, "platform", "linux")
    install.macos_firewall(run=lambda args: pytest.fail("não devia correr nada"))


# ---------------------------------------------------------------------------
# Mensagem de arranque
# ---------------------------------------------------------------------------
@pytest.fixture
def config(tmp_path):
    cfg = ServerConfig(Settings(filename="server.json", directory=str(tmp_path)))
    cfg.set_password("abcdefgh1")
    old = i18n.get_language()
    yield cfg
    i18n.set_language(old)


def test_banner_shows_real_ips_in_the_chosen_language(config, monkeypatch, capsys):
    monkeypatch.setattr(tls, "local_addresses", lambda: ["192.168.1.165"])
    i18n.set_language("en")
    auth.announce_first_run(config, "0.0.0.0", 8080)
    out = capsys.readouterr().out
    assert "http://192.168.1.165:8080/" in out and "0.0.0.0" not in out
    assert "web server" in out and "servidor" not in out


def test_banner_warns_when_the_certificate_misses_the_new_ip(config, tmp_path, monkeypatch, capsys):
    made = tls.make_certificates(str(tmp_path), ["192.168.1.155", "localhost"])
    config.set_tls(made["cert"], made["key"])
    monkeypatch.setattr(tls, "local_addresses", lambda: ["192.168.1.165"])
    i18n.set_language("en")
    auth.announce_first_run(config, "0.0.0.0", 8080)
    out = capsys.readouterr().out
    assert "https://192.168.1.165:8443/" in out
    assert "does not include 192.168.1.165" in out and "--make-cert" in out

    capsys.readouterr()
    monkeypatch.setattr(tls, "local_addresses", lambda: ["192.168.1.155"])
    auth.announce_first_run(config, "0.0.0.0", 8080)
    assert "does not include" not in capsys.readouterr().out


# ── ícone no menu de aplicações (Linux) ──────────────────────────────────────

FAKE_SERVER = """\
import socket, sys, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1])))
s.listen()
open("started", "a").write("x")
while True:
    time.sleep(1)
"""


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def desktop_root(tmp_path, monkeypatch):
    """Pasta de projecto falsa: o start.sh arranca um "run_server.py" que só
    escuta na porta, e o xdg-open apenas regista o endereço que recebeu."""
    if not sys.platform.startswith("linux"):
        pytest.skip("o ícone só existe em Linux")
    root = tmp_path / "MeshDeckWeb"
    (root / ".venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, root / ".venv" / "bin" / "python")
    port = free_port()
    (root / "run_server.py").write_text(FAKE_SERVER)
    (root / "start.sh").write_text(f'#!/bin/sh\ncd "$(dirname "$0")"\nexec .venv/bin/python run_server.py {port}\n')
    os.chmod(root / "start.sh", 0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "xdg-open").write_text(f'#!/bin/sh\necho "$1" >> "{tmp_path}/opened"\n')
    os.chmod(bin_dir / "xdg-open", 0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(install, "lang", "pt")
    entry = install.write_desktop_entry(port, str(tmp_path / "logs" / "server.log"),
                                        root=str(root), apps_dir=str(tmp_path / "apps"))
    yield {"root": root, "port": port, "entry": entry, "tmp": tmp_path}
    install_stop = root / install.DESKTOP_SCRIPT
    import subprocess
    subprocess.call([str(install_stop), "stop"])


def run_icon(env, *args):
    import subprocess
    return subprocess.run([str(env["root"] / install.DESKTOP_SCRIPT), *args], timeout=30)


def test_desktop_entry_points_at_the_script_and_the_logo(desktop_root):
    text = open(desktop_root["entry"]).read()
    root = desktop_root["root"]
    assert f'Exec="{root}/{install.DESKTOP_SCRIPT}"' in text
    assert f"Icon={root}/web/img/logo.svg" in text
    assert "Terminal=false" in text
    assert "Categories=Utility;" in text      # Acessórios no menu
    assert f'Exec="{root}/{install.DESKTOP_SCRIPT}" stop' not in text
    # Parar fica no ☰ da página; o menu de aplicações só tem esta entrada.
    entries = [f for f in os.listdir(os.path.dirname(desktop_root["entry"])) if f.endswith(".desktop")]
    assert entries == [install.DESKTOP_FILE]


def test_icon_starts_the_server_once_and_opens_the_browser(desktop_root):
    env = desktop_root
    assert run_icon(env).returncode == 0
    url = f"http://localhost:{env['port']}/"
    assert (env["tmp"] / "opened").read_text().split() == [url], "o browser abriu antes de o servidor responder"
    assert (env["root"] / "started").read_text() == "x"
    # Segundo clique, com o servidor a correr: só abre o browser.
    assert run_icon(env).returncode == 0
    assert (env["root"] / "started").read_text() == "x", "arrancou um segundo servidor"
    assert (env["tmp"] / "opened").read_text().split() == [url, url]
    assert "---" in (env["tmp"] / "logs" / "server.log").read_text()


def test_icon_stop_stops_only_this_folders_server(desktop_root):
    import socket
    import subprocess
    import time
    env = desktop_root
    run_icon(env)
    # Um processo na mesma pasta com "run_server.py" no comando (uma shell, um
    # grep) não é o servidor. A primeira versão do stop matava-o.
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "run_server.py"],
                                 cwd=env["root"])
    try:
        assert run_icon(env, "stop").returncode == 0
        time.sleep(0.3)
        assert bystander.poll() is None, "o stop matou um processo que não era o servidor"
    finally:
        bystander.kill()
    for _ in range(50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", env["port"])) != 0:
                return
        time.sleep(0.1)
    pytest.fail("o servidor continuou a correr depois de 'stop'")


def test_desktop_step_only_on_linux_with_xdg_open(monkeypatch):
    monkeypatch.setattr(install.sys, "platform", "darwin")
    assert not install.desktop_supported()
    monkeypatch.setattr(install.sys, "platform", "linux")
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    assert not install.desktop_supported()
    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/xdg-open")
    assert install.desktop_supported()


def test_desktop_shortcut_links_to_a_system_entry(tmp_path, monkeypatch):
    """Como o tar1090: uma ligação a uma entrada na pasta de aplicações do
    sistema, que o ambiente de trabalho do Raspberry Pi abre sem perguntar."""
    monkeypatch.setattr(install, "lang", "en")
    system = tmp_path / "system"
    system.mkdir()
    desk = tmp_path / "Desktop"
    desk.mkdir()
    path = install.write_desktop_shortcut(str(desk), root="/opt/MeshDeckWeb", system_dir=str(system))
    text = open(path).read()
    assert "Type=Link" in text and f"URL={system}/{install.DESKTOP_FILE}" in text
    assert "Icon=/opt/MeshDeckWeb/web/img/logo.svg" in text
    entry = open(system / install.DESKTOP_FILE).read()
    assert "Type=Application" in entry and 'Exec="/opt/MeshDeckWeb/start-desktop.sh"' in entry


def test_desktop_shortcut_without_the_system_folder_is_a_copy(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install, "lang", "en")
    monkeypatch.setattr(install.os, "access", lambda path, mode: False)
    monkeypatch.setattr(install.shutil, "which", lambda name: None)       # sem sudo
    path = install.write_desktop_shortcut(str(tmp_path), root="/opt/MeshDeckWeb",
                                          system_dir=str(tmp_path / "nope"))
    text = open(path).read()
    assert "Type=Application" in text and 'Exec="/opt/MeshDeckWeb/start-desktop.sh"' in text
    assert "asks first" in capsys.readouterr().out


def test_no_desktop_shortcut_without_a_desktop_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(install.os.path, "expanduser", lambda p: str(tmp_path))
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    assert install.desktop_folder() is None          # não há ~/Desktop
    (tmp_path / "Desktop").mkdir()
    assert install.desktop_folder() == str(tmp_path / "Desktop")
