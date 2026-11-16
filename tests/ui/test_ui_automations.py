"""
Testes de interface da aba Automações: editar, gravar, pré-visualizar e
ver o registo ao vivo.
"""

import pytest

from core import automations as auto
from tests.ui.conftest import node_id


@pytest.fixture
def automations(page, ui_server):
    """Abre a aba; no fim repõe a configuração por omissão (o servidor dos
    testes é partilhado)."""
    mesh = ui_server["mesh"]
    page.click("[data-tab=automations]")
    page.wait_for("document.querySelector('#auto-root [data-cmd=ping]')",
                  message="os comandos não apareceram")
    yield page, mesh
    mesh.automations.save(auto.default_config())


def wait_saved(page):
    # O botão fica desactivado logo ao carregar; a gravação acaba quando a
    # aba se redesenha com o que o servidor devolveu (e avisa "Saved.").
    page.wait_for("[...document.querySelectorAll('.toast')].some((t) => t.textContent === 'Saved.')",
                  message="não gravou")


def test_lists_every_command_with_a_live_preview(automations):
    page, _ = automations
    keys = page.js("[...document.querySelectorAll('#auto-root [data-cmd]')].map((e) => e.dataset.cmd)")
    assert keys == ["ping", "help", "info", "signal", "nodes", "dist", "pos", "time"]
    assert page.js("document.querySelector('[data-field=enabled]').checked") is False
    page.click("[data-action=toggle][data-key=ping]")
    page.wait_for("document.querySelector('[data-preview=ping] .auto-preview-text')",
                  message="sem pré-visualização")
    assert page.text("[data-preview=ping] .auto-preview-text").startswith("🏓 pong")
    assert "/200 B" in page.text("[data-preview=ping] .auto-bytes")


def test_save_enables_and_persists(automations):
    page, mesh = automations
    assert page.js("document.getElementById('auto-save').disabled")
    page.click("[data-field=enabled]")
    page.click("[data-channel='0']")
    page.js("""(() => { const s = document.querySelector('[data-cmd-who=info]');
      s.value = 'favorites'; s.dispatchEvent(new Event('change', {bubbles: true})); })()""")
    assert not page.js("document.getElementById('auto-save').disabled")
    page.click("#auto-save")
    wait_saved(page)
    config = mesh.automations.config
    assert config["enabled"] and config["channels"] == [0]
    assert config["commands"]["info"]["who"] == "favorites"


def test_custom_command_and_its_errors(automations):
    page, mesh = automations
    page.click("[data-action=add-custom]")
    page.wait_for("document.querySelector('[data-custom-word=\"0\"]')")
    page.js("""(() => {
      const w = document.querySelector('[data-custom-word="0"]');
      w.value = 'duas palavras'; w.dispatchEvent(new Event('input', {bubbles: true}));
      const r = document.querySelector('[data-custom-reply="0"]');
      r.value = 'Olá {short}'; r.dispatchEvent(new Event('input', {bubbles: true}));
    })()""")
    page.click("#auto-save")
    page.wait_for("[...document.querySelectorAll('.toast')].some((t) => t.textContent.includes('duas palavras'))",
                  message="sem aviso do comando inválido")
    assert mesh.automations.config["custom"] == []

    page.js("""(() => { const w = document.querySelector('[data-custom-word="0"]');
      w.value = 'Regras'; w.dispatchEvent(new Event('input', {bubbles: true})); })()""")
    page.click("#auto-save")
    wait_saved(page)
    assert mesh.automations.config["custom"][0]["word"] == "regras"
    assert page.js("document.querySelector('[data-custom-word=\"0\"]').value") == "regras"


def test_log_updates_live(automations):
    page, mesh = automations
    page.js("""import('/js/store.js').then(({ store }) => store.applyAutomationLog({ entry: {
      time: Date.now() / 1000, from_id: '%s', name: 'TEST', command: 'ping',
      channel: null, outcome: 'cooldown', reply: '' } }))""" % node_id(1))
    page.wait_for("document.querySelector('#auto-log .auto-log-blocked')", message="o registo não actualizou")
    assert "TEST" in page.text("#auto-log") and "ping" in page.text("#auto-log")


def test_phone_layout_fits(automations):
    page, _ = automations
    page.viewport(390, 844, mobile=True, touch=True)
    page.wait_for("new Promise((r) => setTimeout(() => r(true), 400))")
    page.click("[data-action=toggle][data-key=ping]")
    assert page.js("document.documentElement.scrollWidth") <= 390
