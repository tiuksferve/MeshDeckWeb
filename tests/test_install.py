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
