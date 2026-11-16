/*
 * web/js/tabs/automations.js — Aba de automações.
 *
 * O servidor responde sozinho a comandos ("ping", "info", …) recebidos por
 * DM ou nos canais escolhidos (core/automations.py). Aqui edita-se a
 * configuração — que só muda no servidor ao carregar em Gravar — e vê-se o
 * registo dos comandos recebidos, ao vivo.
 *
 * Cada resposta tem uma pré-visualização feita no servidor com dados reais,
 * como se o nó ouvido há menos tempo tivesse mandado o comando, e o tamanho
 * em bytes (o limite de uma mensagem é ~200; um emoji conta 4).
 */
'use strict';

import { store } from '../store.js';
import { getJSON, postJSON } from '../api.js';
import { escapeHtml, fmtTime } from '../utils.js';
import { t } from '../i18n.js';

const ORDER = ['ping', 'help', 'info', 'signal', 'nodes', 'dist', 'pos', 'time'];

let state = null;        // resposta de GET /api/automations
let draft = null;        // configuração a ser editada
let dirty = false;
let saving = false;
let visible = false;
let showToast = () => {};
const expanded = new Set();   // comandos abertos (sobrevivem a um redesenho)

export function mount({ toast } = {}) {
  if (toast) showToast = toast;
  const root = document.getElementById('auto-root');
  root.addEventListener('input', onInput);
  root.addEventListener('change', onInput);
  root.addEventListener('click', onClick);

  store.on('automations-log', renderLog);
  store.on('snapshot', () => { if (visible) load(); else renderLog(); });
  store.on('channels-changed', () => { if (draft) renderChannels(); });
  store.on('automations-changed', () => { if (visible && !dirty) load(); });
  store.on('language-changed', () => { if (state) render(); });
}

export function onShow() {
  visible = true;
  if (!dirty) load();
}

export function onHide() {
  visible = false;
}

async function load() {
  const { ok, data } = await getJSON('/api/automations');
  if (!ok) return;
  state = data;
  draft = structuredClone(data.config);
  dirty = false;
  render();
}

/* ── desenho ───────────────────────────────────────────────────────────── */

function render() {
  const root = document.getElementById('auto-root');
  if (!draft) {
    root.innerHTML = `<p class="muted">…</p>`;
    return;
  }
  root.innerHTML = `
    <div class="auto-main">
      <div class="panel">
        <div class="auto-master">
          <label class="switch"><input type="checkbox" data-field="enabled" ${draft.enabled ? 'checked' : ''}><span></span></label>
          <b>${escapeHtml(t('Automações activas'))}</b>
        </div>
        <p class="muted auto-intro">${escapeHtml(t('auto_intro'))}</p>

        <h2>${escapeHtml(t('Onde responder'))}</h2>
        <label class="auto-check"><input type="checkbox" data-field="dm" ${draft.dm ? 'checked' : ''}>
          ${escapeHtml(t('Mensagens directas (DM)'))}</label>
        <div class="auto-channels" id="auto-channels"></div>
        <p class="muted">${escapeHtml(t('auto_channels_hint'))}</p>

        <h2>${escapeHtml(t('Travões'))}</h2>
        <div class="row auto-limits">
          <label>${escapeHtml(t('Intervalo por nó (s)'))}
            <input type="number" inputmode="numeric" min="5" max="3600" data-field="cooldown_s" value="${draft.cooldown_s}"></label>
          <label>${escapeHtml(t('Respostas por minuto'))}
            <input type="number" inputmode="numeric" min="1" max="30" data-field="max_per_min" value="${draft.max_per_min}"></label>
        </div>
        <label class="auto-check"><input type="checkbox" data-field="ignore_mqtt" ${draft.ignore_mqtt ? 'checked' : ''}>
          ${escapeHtml(t('Ignorar pedidos vindos por MQTT'))}</label>
        <p class="muted">${escapeHtml(t('auto_airtime_hint', { pct: state.airtime_limit }))}</p>
      </div>

      <div class="panel">
        <h2>${escapeHtml(t('Comandos'))}</h2>
        <div class="auto-cmds">${ORDER.filter((k) => draft.commands[k]).map(builtinHtml).join('')}</div>
      </div>

      <div class="panel">
        <h2>${escapeHtml(t('Comandos personalizados'))}</h2>
        <div class="auto-cmds" id="auto-custom">${draft.custom.map(customHtml).join('')}</div>
        <button type="button" class="small" data-action="add-custom">${escapeHtml(t('+ Comando personalizado'))}</button>
      </div>

      <details class="panel auto-vars">
        <summary>${escapeHtml(t('Variáveis'))}</summary>
        <p class="muted">${escapeHtml(t('auto_vars_hint'))}</p>
        <dl class="auto-var-list">${state.variables.map((v) =>
          `<dt class="mono">{${v}}</dt><dd>${escapeHtml(t(`auto_var_${v}`))}</dd>`).join('')}</dl>
      </details>
    </div>

    <div class="auto-side">
      <div class="panel">
        <h2>${escapeHtml(t('Registo'))}</h2>
        <ul class="auto-log" id="auto-log"></ul>
      </div>
    </div>

    <div class="auto-savebar">
      <span id="auto-save-status" class="muted"></span>
      <button type="button" id="auto-save" data-action="save">${escapeHtml(t('Gravar'))}</button>
    </div>`;
  renderChannels();
  renderLog();
  updateSaveBar();
  root.querySelectorAll('textarea[data-template], textarea[data-custom-reply]').forEach(requestPreview);
}

function whoSelect(attr, who) {
  return `<select ${attr}>
      <option value="all" ${who !== 'favorites' ? 'selected' : ''}>${escapeHtml(t('auto_who_all'))}</option>
      <option value="favorites" ${who === 'favorites' ? 'selected' : ''}>${escapeHtml(t('auto_who_fav'))}</option>
    </select>`;
}

function builtinHtml(key) {
  const cmd = draft.commands[key];
  const words = state.words[key] || [key];
  const shown = state.labels?.[key] || words[0];
  const others = words.filter((w) => w !== shown);
  const open = expanded.has(key);
  return `
    <div class="auto-cmd ${cmd.enabled ? '' : 'off'}" data-cmd="${key}">
      <div class="auto-cmd-head">
        <label class="switch"><input type="checkbox" data-cmd-enabled="${key}" ${cmd.enabled ? 'checked' : ''}><span></span></label>
        <button type="button" class="auto-cmd-name" data-action="toggle" data-key="${key}" aria-expanded="${open}">
          <b class="mono">${escapeHtml(shown)}</b>
          <span class="muted">${escapeHtml(t(`auto_desc_${key}`))}</span>
        </button>
        ${whoSelect(`data-cmd-who="${key}"`, cmd.who)}
      </div>
      <div class="auto-cmd-body" ${open ? '' : 'hidden'}>
        ${others.length ? `<p class="muted">${escapeHtml(t('Também aceita: {words}', { words: others.join(', ') }))}</p>` : ''}
        <label>${escapeHtml(t('Resposta'))}
          <textarea rows="2" data-template="${key}">${escapeHtml(cmd.template || state.defaults[key])}</textarea></label>
        <div class="auto-preview-row">
          <span class="auto-preview" data-preview="${key}"></span>
          <button type="button" class="ghost small" data-action="reset" data-key="${key}">${escapeHtml(t('repor'))}</button>
        </div>
      </div>
    </div>`;
}

function customHtml(entry, index) {
  return `
    <div class="auto-cmd ${entry.enabled ? '' : 'off'}" data-custom="${index}">
      <div class="auto-cmd-head">
        <label class="switch"><input type="checkbox" data-custom-enabled="${index}" ${entry.enabled ? 'checked' : ''}><span></span></label>
        <input class="mono auto-word" data-custom-word="${index}" value="${escapeHtml(entry.word)}"
               placeholder="${escapeHtml(t('palavra'))}" autocapitalize="none" autocomplete="off" spellcheck="false" maxlength="24">
        ${whoSelect(`data-custom-who="${index}"`, entry.who)}
      </div>
      <div class="auto-cmd-body">
        <label>${escapeHtml(t('Resposta'))}
          <textarea rows="2" data-custom-reply="${index}">${escapeHtml(entry.reply)}</textarea></label>
        <div class="auto-preview-row">
          <span class="auto-preview" data-preview="custom:${index}"></span>
          <button type="button" class="ghost small danger" data-action="remove" data-index="${index}">${escapeHtml(t('Remover'))}</button>
        </div>
      </div>
    </div>`;
}

function renderChannels() {
  const box = document.getElementById('auto-channels');
  if (!box || !draft) return;
  const channels = store.channels.length
    ? store.channels
    : draft.channels.map((index) => ({ index, name: '' }));
  box.innerHTML = channels.map((ch) => `
    <label class="auto-check"><input type="checkbox" data-channel="${ch.index}" ${draft.channels.includes(ch.index) ? 'checked' : ''}>
      ${escapeHtml(t('Canal'))} ${ch.index}${ch.name ? ` · ${escapeHtml(ch.name)}` : ''}</label>`).join('');
}

function renderLog() {
  const list = document.getElementById('auto-log');
  if (!list) return;
  const entries = store.automations?.log || [];
  if (!entries.length) {
    list.innerHTML = `<li class="muted">${escapeHtml(t('Ainda nenhum comando recebido.'))}</li>`;
    return;
  }
  list.innerHTML = entries.map((e) => {
    const where = e.channel == null ? 'DM' : `${t('Canal')} ${e.channel}`;
    return `<li class="auto-log-${e.outcome === 'sent' ? 'sent' : 'blocked'}">
      <div><span class="muted">${fmtTime(e.time)}</span> <b>${escapeHtml(e.name || e.from_id)}</b>
        → <span class="mono">${escapeHtml(e.command)}</span> <span class="muted">· ${escapeHtml(where)}</span>
        <span class="auto-outcome">${escapeHtml(t(`auto_out_${e.outcome}`))}</span></div>
      ${e.reply ? `<div class="muted auto-log-reply">${escapeHtml(e.reply)}</div>` : ''}
    </li>`;
  }).join('');
}

function updateSaveBar() {
  const button = document.getElementById('auto-save');
  const status = document.getElementById('auto-save-status');
  if (!button) return;
  button.disabled = !dirty || saving;
  button.classList.toggle('primary', dirty);
  status.textContent = dirty ? t('Alterações por gravar.') : '';
}

/* ── edição ────────────────────────────────────────────────────────────── */

function markDirty() {
  dirty = true;
  updateSaveBar();
}

function onInput(event) {
  const el = event.target;
  const d = el.dataset;
  if (d.field) {
    draft[d.field] = el.type === 'checkbox' ? el.checked
      : el.type === 'number' ? Number(el.value) : el.value;
  } else if (d.channel !== undefined) {
    const index = Number(d.channel);
    draft.channels = draft.channels.filter((c) => c !== index);
    if (el.checked) draft.channels.push(index);
  } else if (d.cmdEnabled) {
    draft.commands[d.cmdEnabled].enabled = el.checked;
    el.closest('.auto-cmd').classList.toggle('off', !el.checked);
  } else if (d.cmdWho) {
    draft.commands[d.cmdWho].who = el.value;
  } else if (d.template) {
    draft.commands[d.template].template = el.value;
    if (event.type === 'input') requestPreview(el);
  } else if (d.customEnabled !== undefined) {
    draft.custom[Number(d.customEnabled)].enabled = el.checked;
    el.closest('.auto-cmd').classList.toggle('off', !el.checked);
  } else if (d.customWord !== undefined) {
    draft.custom[Number(d.customWord)].word = el.value;
  } else if (d.customWho !== undefined) {
    draft.custom[Number(d.customWho)].who = el.value;
  } else if (d.customReply !== undefined) {
    draft.custom[Number(d.customReply)].reply = el.value;
    if (event.type === 'input') requestPreview(el);
  } else {
    return;
  }
  if (event.type === 'input' || el.type === 'checkbox' || el.tagName === 'SELECT') markDirty();
}

function onClick(event) {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const { action } = button.dataset;
  if (action === 'toggle') {
    const key = button.dataset.key;
    const body = button.closest('.auto-cmd').querySelector('.auto-cmd-body');
    body.hidden = !body.hidden;
    button.setAttribute('aria-expanded', String(!body.hidden));
    if (body.hidden) expanded.delete(key); else expanded.add(key);
  } else if (action === 'reset') {
    const key = button.dataset.key;
    const area = document.querySelector(`textarea[data-template="${key}"]`);
    area.value = state.defaults[key];
    draft.commands[key].template = '';
    requestPreview(area);
    markDirty();
  } else if (action === 'add-custom') {
    draft.custom.push({ word: '', reply: '', enabled: true, who: 'all' });
    markDirty();
    rerenderKeepingDraft();
    document.querySelector(`[data-custom-word="${draft.custom.length - 1}"]`)?.focus();
  } else if (action === 'remove') {
    draft.custom.splice(Number(button.dataset.index), 1);
    markDirty();
    rerenderKeepingDraft();
  } else if (action === 'save') {
    save();
  }
}

function rerenderKeepingDraft() {
  const scroll = document.querySelector('main')?.scrollTop;
  render();
  if (scroll != null) document.querySelector('main').scrollTop = scroll;
}

async function save() {
  saving = true;
  updateSaveBar();
  const { ok, data } = await postJSON('/api/automations', draft);
  saving = false;
  if (!ok) {
    showToast(data.error || t('pedido inválido'));
    updateSaveBar();
    return;
  }
  state = data;
  draft = structuredClone(data.config);
  dirty = false;
  render();
  showToast(t('Gravado.'), 'info');
}

/* ── pré-visualização ──────────────────────────────────────────────────── */

const pending = new Map();   // alvo → temporizador debounce

function requestPreview(area) {
  const target = area.dataset.template || `custom:${area.dataset.customReply}`;
  const command = area.dataset.template || 'custom';
  clearTimeout(pending.get(target));
  pending.set(target, setTimeout(async () => {
    pending.delete(target);
    const { ok, data } = await postJSON('/api/automations/preview',
      { command, template: area.value });
    const el = document.querySelector(`[data-preview="${target}"]`);
    if (!ok || !el) return;
    const over = data.bytes >= state.max_bytes;
    el.innerHTML = `<span class="auto-preview-text">${escapeHtml(data.text)}</span>
      <span class="auto-bytes ${over ? 'over' : ''}">${data.bytes}/${state.max_bytes} B</span>`;
    el.title = data.sender ? t('como se {node} tivesse enviado', { node: nodeName(data.sender) }) : '';
  }, 350));
}

function nodeName(id) {
  const node = store.nodes.get(id);
  return node?.short_name || node?.long_name || id;
}
