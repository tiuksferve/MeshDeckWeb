"""
Testes do arranque (run_server.py): o --help na língua do instalador e o
filtro que esconde o aviso de "production deployment" do Werkzeug.
"""

import logging
import sys

import pytest

import i18n
import run_server


@pytest.fixture
def language():
    previous = i18n.get_language()
    yield i18n.set_language
    i18n.set_language(previous)


def help_text(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_server.py", "--help"])
    with pytest.raises(SystemExit):
        run_server.parse_args()
    return capsys.readouterr().out


def test_help_follows_the_installer_language(language, monkeypatch, capsys):
    language("en")
    english = help_text(monkeypatch, capsys)
    assert "Examples:" in english
    assert "set the access password and exit" in english
    assert "--tls-cert FILE" in english

    language("pt")
    portuguese = help_text(monkeypatch, capsys)
    assert "Exemplos:" in portuguese
    assert "definir a password de acesso e sair" in portuguese
    assert "--tls-cert FICHEIRO" in portuguese


def test_every_help_text_is_translated():
    keys = [k for k in i18n._STRINGS if k.startswith("help_")]
    assert len(keys) >= 15
    for key in keys:
        assert i18n._STRINGS[key]["pt"] != i18n._STRINGS[key]["en"], key


def record(message, level=logging.WARNING):
    return logging.LogRecord("werkzeug", level, __file__, 1, message, None, None)


def test_production_warning_is_hidden_and_the_rest_kept():
    hide = run_server.HideProductionWarning()
    assert not hide.filter(record(
        "Werkzeug appears to be used in a production deployment. "
        "Consider switching to a production web server instead."))
    assert hide.filter(record("Address already in use", logging.ERROR))
