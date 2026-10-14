/*
 * web/js/tabs/config.js — Aba de configuração: ler e gravar a do nó.
 *
 * O esquema das secções vem de /api/config/defs (core/config_defs.py, o
 * mesmo do desktop) e os valores actuais de /api/config/values. Cada campo
 * vira o controlo do seu tipo; o que o utilizador muda fica guardado como
 * alteração pendente (destacada, com contagem por secção) até carregar em
 * "Gravar no nó". A gravação é uma só transacção no servidor
 * (core/config_writer.py): o nó reinicia uma vez e a configuração é relida
 * quando volta a ficar pronto.
 *
 * Canais e dono (nomes) têm páginas próprias. Sem nó ligado, mostra-se só o
 * esquema, como antes.
 */
'use strict';

import { getJSON, postJSON } from '../api.js';
import { escapeHtml, isMobile, showDialog } from '../utils.js';
import { t } from '../i18n.js';
import { store } from '../store.js';

const CHANNELS = '__channels__';
const OWNER = '__owner__';
const CANNED = '__canned_messages__';
const MAX_CHANNELS = 8;

let sections = [];             // esquema
let values = null;             // {owner, device, sections, channels} ou null sem nó
let valuesError = '';
let edits = {};                // {secKey: {campo: valor}} — só o que difere do nó
let ownerEdits = {};
let channelsDraft = null;      // cópia editável dos canais
let activeKey = null;
let loading = false;
let serverLanguage = null;

export function mount() {
  const list = document.getElementById('config-list');
  list.addEventListener('click', (event) => {
    const li = event.target.closest('li[data-key]');
    if (li) activate(li.dataset.key);
  });

  const body = document.getElementById('config-body');
  body.addEventListener('input', onFieldInput);
  body.addEventListener('change', onFieldInput);
  body.addEventListener('click', onBodyClick);

  // Os rótulos vêm traduzidos do servidor: noutro idioma relê-se o esquema.
  store.on('snapshot', (snapshot) => {
    if (!snapshot.language || snapshot.language === serverLanguage) return;
    serverLanguage = snapshot.language;
    sections = [];
    if (isVisible()) load();
  });
  // Nó pronto (ligação nova ou regresso após o reinício de uma gravação).
  store.on('connection-changed', () => {
    const ready = store.connection.connected && store.connection.node_ready;
    if (ready && !values && isVisible()) load();
    if (!store.connection.connected && values) { values = null; render(); }
  });
}

export function onShow() {
  if (!sections.length || !values) load();
}

function isVisible() {
  return document.getElementById('panel-config').classList.contains('active');
}

async function load() {
  if (loading) return;
  loading = true;
  try {
    if (!sections.length) {
      const { ok, data } = await getJSON('/api/config/defs');
      if (ok) sections = data.sections;
    }
    const { ok, data } = await getJSON('/api/config/values');
    if (ok) {
      // Um rascunho de canais com alterações sobrevive a uma releitura.
      const keepDraft = channelsDraft && values && channelsDirty();
      values = data;
      valuesError = '';
      if (!keepDraft) channelsDraft = cloneChannels(values.channels);
    } else {
      values = null;
      valuesError = data.error || '';
    }
  } finally {
    loading = false;
  }
  render();
}

function render() {
  if (!activeKey) activeKey = values ? CHANNELS : (sections[0]?.key || null);
  renderBanner();
  renderList();
  renderPage();
}

function renderBanner() {
  const banner = document.getElementById('config-banner');
  if (values) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  banner.textContent = valuesError || t('Ligue-se a um nó para ver e editar a configuração.');
}

/* ── lista de secções ─────────────────────────────────────────────────── */

function pendingCount(key) {
  if (key === CHANNELS) return channelsDirty() ? 1 : 0;
  if (key === OWNER) return Object.keys(ownerEdits).length;
  return Object.keys(edits[key] || {}).length;
}

function hasPendingEdits() {
  return Object.keys(edits).length > 0 || Object.keys(ownerEdits).length > 0;
}

function renderList() {
  const items = [];
  if (values) {
    items.push({ key: CHANNELS, label: t('📡 Canais') });
    items.push({ key: OWNER, label: t('👤 Utilizador') });
  }
  items.push(...sections.map((s) => ({ key: s.key, label: s.label })));
  document.getElementById('config-list').innerHTML = items.map((item) => {
    const n = pendingCount(item.key);
    return `
      <li data-key="${item.key}" class="${item.key === activeKey ? 'active' : ''}">
        <span>${escapeHtml(item.label)}</span>${n ? `<span class="unread pending">${n}</span>` : ''}
      </li>`;
  }).join('');
}

function activate(key) {
  activeKey = key;
  renderList();
  document.querySelector('#config-list li.active')
    ?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  renderPage();
  if (isMobile()) document.getElementById('config-body').scrollIntoView({ block: 'nearest' });
}

function renderPage() {
  const body = document.getElementById('config-body');
  if (activeKey === CHANNELS && values) body.innerHTML = channelsPage();
  else if (activeKey === OWNER && values) body.innerHTML = ownerPage();
  else body.innerHTML = sectionPage(sections.find((s) => s.key === activeKey));
  body.insertAdjacentHTML('beforeend', saveBar());
}

/* ── secções do esquema ───────────────────────────────────────────────── */

function currentValue(secKey, field) {
  const pending = edits[secKey];
  if (pending && field in pending) return pending[field];
  return values?.sections?.[secKey]?.[field];
}

function isDirty(secKey, field) {
  return !!(edits[secKey] && field in edits[secKey]);
}

function control(secKey, field) {
  const name = field.name;
  const value = currentValue(secKey, name);
  const attrs = `data-sec="${escapeHtml(secKey)}" data-field="${escapeHtml(name)}" id="f-${escapeHtml(secKey)}-${escapeHtml(name)}"`;
  const disabled = values ? '' : 'disabled';

  switch (field.type) {
    case 'bool':
      return `<label class="switch"><input type="checkbox" ${attrs} ${value ? 'checked' : ''} ${disabled}><span></span></label>`;
    case 'spin_int':
    case 'spin_float': {
      const step = field.type === 'spin_int' ? '1' : 'any';
      return `<input type="number" inputmode="decimal" step="${step}" min="${field.min}" max="${field.max}"
                     value="${value ?? ''}" ${attrs} ${disabled}>
              <span class="range-hint">${field.min} – ${field.max}</span>`;
    }
    case 'combo': {
      const choices = field.choices || [];
      const known = choices.includes(value);
      return `<select ${attrs} ${disabled}>
        ${known || value == null ? '' : `<option value="${escapeHtml(value)}" selected>${escapeHtml(value)}</option>`}
        ${choices.map((c) => `<option value="${escapeHtml(c)}" ${c === value ? 'selected' : ''}>${escapeHtml(c)}</option>`).join('')}
      </select>`;
    }
    case 'text': {
      const secret = /psk|password/i.test(name);
      return `<input type="${secret ? 'password' : 'text'}" autocomplete="off" value="${escapeHtml(value ?? '')}" ${attrs} ${disabled}>
              ${secret ? `<button type="button" class="ghost small" data-reveal="${escapeHtml(name)}">👁</button>` : ''}`;
    }
    case 'canned_messages': {
      const lines = String(value ?? '').split('|').filter((s) => s.trim()).join('\n');
      return `<div class="canned">
        <textarea rows="5" ${attrs} ${disabled} placeholder="${escapeHtml(t('Uma mensagem por linha'))}">${escapeHtml(lines)}</textarea>
        <span class="range-hint" data-canned-count>${cannedLength(lines)} / 200</span>
      </div>`;
    }
    case 'readonly_with_note':
      return `<span class="value">${escapeHtml(value == null ? '—' : String(value))}</span>
              ${field.note ? `<span class="note">${escapeHtml(field.note)}</span>` : ''}`;
    default:
      return `<span class="value">${escapeHtml(value == null || value === '' ? '—' : String(value))}</span>`;
  }
}

function schemaNote(field) {
  if (field.type === 'combo') return (field.choices || []).join(' · ');
  if (field.type === 'spin_int' || field.type === 'spin_float') return `${field.min} – ${field.max}`;
  if (field.type === 'readonly_with_note' && field.note) return field.note;
  return field.type;
}

function sectionPage(section) {
  if (!section) return '';
  const rows = section.fields.map((field) => {
    if (!values) {
      return `<div class="config-field"><label>${escapeHtml(field.label)}</label>
              <span class="value">${escapeHtml(schemaNote(field))}</span></div>`;
    }
    const dirty = isDirty(section.key, field.name) ? ' dirty' : '';
    return `<div class="config-field${dirty}">
      <label for="f-${escapeHtml(section.key)}-${escapeHtml(field.name)}">${escapeHtml(field.label)}</label>
      <div class="control">${control(section.key, field)}</div>
    </div>`;
  }).join('');
  return `${pageHead(section.label)}${rows}`;
}

function cannedLength(text) {
  return String(text).split('\n').map((s) => s.trim()).filter(Boolean).join('|').length;
}

function readInput(el) {
  if (el.type === 'checkbox') return el.checked;
  if (el.type === 'number') return el.value === '' ? null : Number(el.value);
  if (el.tagName === 'TEXTAREA') return el.value.split('\n').map((s) => s.trim()).filter(Boolean).join('|');
  return el.value;
}

function onFieldInput(event) {
  const el = event.target;
  if (el.dataset.channel !== undefined) { onChannelInput(el); return; }
  if (el.dataset.owner) { onOwnerInput(el); return; }
  const secKey = el.dataset.sec;
  const field = el.dataset.field;
  if (!secKey || !field) return;

  let value = readInput(el);
  if (el.tagName === 'TEXTAREA') {
    const counter = el.parentElement.querySelector('[data-canned-count]');
    if (counter) {
      counter.textContent = `${value.length} / 200`;
      counter.classList.toggle('over', value.length > 200);
    }
  }
  const original = values?.sections?.[secKey]?.[field];
  if (field === CANNED) value = String(value);
  const same = original === value || (original == null && (value === '' || value === null));
  edits[secKey] = edits[secKey] || {};
  if (same) delete edits[secKey][field];
  else edits[secKey][field] = value;
  if (!Object.keys(edits[secKey]).length) delete edits[secKey];

  el.closest('.config-field')?.classList.toggle('dirty', !same);
  refreshPendingUi();
}

/* ── dono do nó ───────────────────────────────────────────────────────── */

function ownerPage() {
  const owner = { ...values.owner, ...ownerEdits };
  const dev = values.device || {};
  const row = (key, label, html) => `
    <div class="config-field${key in ownerEdits ? ' dirty' : ''}">
      <label for="owner-${key}">${escapeHtml(label)}</label><div class="control">${html}</div>
    </div>`;
  return `${pageHead(t('👤 Utilizador'))}
    ${row('long_name', t('Nome Longo'), `<input id="owner-long_name" data-owner="long_name" maxlength="39" value="${escapeHtml(owner.long_name || '')}">`)}
    ${row('short_name', t('Nome Curto'), `<input id="owner-short_name" data-owner="short_name" maxlength="4" value="${escapeHtml(owner.short_name || '')}">
      <span class="range-hint">${escapeHtml(t('máx. 4 caracteres'))}</span>`)}
    ${row('is_licensed', t('Licenciado (Ham)'), `<label class="switch"><input type="checkbox" id="owner-is_licensed" data-owner="is_licensed" ${owner.is_licensed ? 'checked' : ''}><span></span></label>`)}
    <div class="config-field"><label>${escapeHtml(t('ID do Nó'))}</label><span class="value">${escapeHtml(dev.id || '—')}</span></div>
    <div class="config-field"><label>${escapeHtml(t('Modelo HW'))}</label><span class="value">${escapeHtml(dev.hw_model || '—')}</span></div>
    <div class="config-field"><label>Firmware</label><span class="value">${escapeHtml(dev.firmware || '—')}</span></div>`;
}

function onOwnerInput(el) {
  const key = el.dataset.owner;
  const value = el.type === 'checkbox' ? el.checked : el.value;
  if (values.owner[key] === value) delete ownerEdits[key];
  else ownerEdits[key] = value;
  el.closest('.config-field')?.classList.toggle('dirty', key in ownerEdits);
  refreshPendingUi();
}

/* ── canais ───────────────────────────────────────────────────────────── */

function cloneChannels(list) {
  return (list || []).map((c) => ({ ...c }));
}

function channelsDirty() {
  if (!values || !channelsDraft) return false;
  return JSON.stringify(channelsDraft) !== JSON.stringify(values.channels);
}

function randomPsk(bytes) {
  const key = new Uint8Array(bytes);
  crypto.getRandomValues(key);
  return btoa(String.fromCharCode(...key));
}

function channelsPage() {
  const cards = channelsDraft.map((ch, i) => {
    const role = ['PRIMARY', 'SECONDARY', 'DISABLED'];
    return `
    <div class="channel-card role-${ch.role.toLowerCase()}">
      <div class="channel-head">
        <b>📻 ${escapeHtml(t('Canal {n}', { n: ch.index }))}</b>
        ${ch.index === 0 ? '' : `<button type="button" class="ghost small danger" data-remove-channel="${i}" title="${escapeHtml(t('Remover canal'))}">🗑</button>`}
      </div>
      <div class="config-field"><label>${escapeHtml(t('Nome do canal'))}</label>
        <div class="control"><input data-channel="${i}" data-key="name" maxlength="11" value="${escapeHtml(ch.name)}"></div></div>
      <div class="config-field"><label>${escapeHtml(t('Papel'))}</label>
        <div class="control"><select data-channel="${i}" data-key="role" ${ch.index === 0 ? 'disabled' : ''}>
          ${role.map((r) => `<option ${r === ch.role ? 'selected' : ''}>${r}</option>`).join('')}
        </select></div></div>
      <div class="config-field"><label>PSK</label>
        <div class="control psk-control">
          <input data-channel="${i}" data-key="psk" value="${escapeHtml(ch.psk)}" placeholder="${escapeHtml(t('Base64, default ou none'))}" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false">
          <select data-psk-size="${i}">
            <option value="32">256-bit</option><option value="16">128-bit</option><option value="default">Default (AQ==)</option>
          </select>
          <button type="button" class="small" data-gen-psk="${i}">🔑 ${escapeHtml(t('Gerar'))}</button>
        </div></div>
      <div class="config-field"><label>${escapeHtml(t('Uplink MQTT'))}</label>
        <div class="control"><label class="switch"><input type="checkbox" data-channel="${i}" data-key="uplink_enabled" ${ch.uplink_enabled ? 'checked' : ''}><span></span></label></div></div>
      <div class="config-field"><label>${escapeHtml(t('Downlink MQTT'))}</label>
        <div class="control"><label class="switch"><input type="checkbox" data-channel="${i}" data-key="downlink_enabled" ${ch.downlink_enabled ? 'checked' : ''}><span></span></label></div></div>
      <div class="config-field"><label>${escapeHtml(t('Silenciar'))}</label>
        <div class="control"><label class="switch"><input type="checkbox" data-channel="${i}" data-key="is_muted" ${ch.is_muted ? 'checked' : ''}><span></span></label></div></div>
      <div class="config-field"><label>${escapeHtml(t('Precisão posição'))}</label>
        <div class="control"><input type="number" min="0" max="32" step="1" data-channel="${i}" data-key="position_precision" value="${ch.position_precision}">
        <span class="range-hint">0 – 32</span></div></div>
    </div>`;
  }).join('');
  const canAdd = channelsDraft.length < MAX_CHANNELS;
  return `${pageHead(t('📡 Canais'))}
    ${cards}
    ${canAdd ? `<button type="button" class="add-channel" data-add-channel>➕ ${escapeHtml(t('Adicionar Canal'))}</button>` : ''}`;
}

function onChannelInput(el) {
  const ch = channelsDraft[Number(el.dataset.channel)];
  const key = el.dataset.key;
  if (!ch || !key) return;
  if (el.type === 'checkbox') ch[key] = el.checked;
  else if (el.type === 'number') ch[key] = Number(el.value || 0);
  else ch[key] = el.value;
  if (key === 'role') el.closest('.channel-card').className = `channel-card role-${ch.role.toLowerCase()}`;
  refreshPendingUi();
}

function onBodyClick(event) {
  const target = event.target;
  const reveal = target.closest('[data-reveal]');
  if (reveal) {
    const input = reveal.parentElement.querySelector('input');
    input.type = input.type === 'password' ? 'text' : 'password';
    return;
  }
  const gen = target.closest('[data-gen-psk]');
  if (gen) {
    const i = Number(gen.dataset.genPsk);
    const size = document.querySelector(`[data-psk-size="${i}"]`).value;
    channelsDraft[i].psk = size === 'default' ? 'AQ==' : randomPsk(Number(size));
    document.querySelector(`[data-channel="${i}"][data-key="psk"]`).value = channelsDraft[i].psk;
    refreshPendingUi();
    return;
  }
  const remove = target.closest('[data-remove-channel]');
  if (remove) {
    channelsDraft.splice(Number(remove.dataset.removeChannel), 1);
    renderPage();
    refreshPendingUi();
    return;
  }
  if (target.closest('[data-add-channel]')) {
    const used = new Set(channelsDraft.map((c) => c.index));
    const index = [...Array(MAX_CHANNELS).keys()].find((n) => !used.has(n));
    if (index == null) return;
    channelsDraft.push({ index, role: 'SECONDARY', name: t('Canal {n}', { n: index }).slice(0, 11),
      psk: randomPsk(32), uplink_enabled: false, downlink_enabled: false, is_muted: false, position_precision: 0 });
    channelsDraft.sort((a, b) => a.index - b.index);
    renderPage();
    refreshPendingUi();
    return;
  }
  if (target.closest('[data-discard]')) { discard(); return; }
  if (target.closest('[data-save]')) { activeKey === CHANNELS ? saveChannels() : saveConfig(); }
}

/* ── botão de gravar no topo de cada ecrã ─────────────────────────────── */

/* Título do ecrã com o botão de gravar sempre visível (desactivado sem
   alterações). Grava TODAS as alterações pendentes, de todos os ecrãs,
   numa só transacção — o nó reinicia uma vez, não uma por ecrã. Os canais
   têm gravação própria. */
function pageHead(title) {
  return `<div class="config-head">
      <h2 class="config-title">${escapeHtml(title)}</h2>
      ${values ? headSaveButton() : ''}
    </div>
    ${values ? `<p class="config-hint muted">${escapeHtml(activeKey === CHANNELS
      ? t('As alterações aos canais só são enviadas ao nó quando carregar em Gravar canais.')
      : t('As alterações só são enviadas ao nó quando carregar em Gravar. Pode alterar vários ecrãs e gravar tudo de uma vez.'))}</p>` : ''}`;
}

function headSaveButton() {
  const isChannels = activeKey === CHANNELS;
  const n = isChannels ? (channelsDirty() ? 1 : 0) : configPendingTotal();
  const label = isChannels ? t('Gravar canais') : t('Gravar');
  const count = !isChannels && n ? ` (${n})` : '';
  return `<button type="button" class="primary head-save" data-save ${n ? '' : 'disabled'}>💾 ${escapeHtml(label)}${count}</button>`;
}

/* ── barra de gravação ────────────────────────────────────────────────── */

function configPendingTotal() {
  return Object.values(edits).reduce((n, f) => n + Object.keys(f).length, 0) + Object.keys(ownerEdits).length;
}

function saveBar() {
  if (!values) return '';
  const isChannels = activeKey === CHANNELS;
  const n = isChannels ? (channelsDirty() ? 1 : 0) : configPendingTotal();
  const text = isChannels
    ? t('Canais com alterações por gravar')
    : t('{n} alteração(ões) por gravar', { n });
  return `<div class="save-bar" ${n ? '' : 'hidden'}>
    <span>${escapeHtml(text)}</span>
    <span class="spacer"></span>
    <button type="button" class="ghost" data-discard>${escapeHtml(t('Descartar'))}</button>
    <button type="button" class="primary" data-save>💾 ${escapeHtml(isChannels ? t('Gravar canais') : t('Gravar no nó'))}</button>
  </div>`;
}

function refreshPendingUi() {
  renderList();
  const head = document.querySelector('#config-body .head-save');
  if (head) head.outerHTML = headSaveButton();
  const old = document.querySelector('#config-body .save-bar');
  if (old) old.outerHTML = saveBar();
  else document.getElementById('config-body').insertAdjacentHTML('beforeend', saveBar());
}

function discard() {
  if (activeKey === CHANNELS) channelsDraft = cloneChannels(values.channels);
  else { edits = {}; ownerEdits = {}; }
  renderList();
  renderPage();
}

function labelOf(secKey, field) {
  const section = sections.find((s) => s.key === secKey);
  const def = section?.fields.find((f) => f.name === field);
  return [section?.label || secKey, def?.label || field];
}

function fmt(value) {
  if (value === true) return '✓';
  if (value === false) return '✗';
  if (value == null || value === '') return '—';
  return String(value);
}

async function saveConfig() {
  const lines = [];
  for (const [secKey, fields] of Object.entries(edits)) {
    for (const [field, value] of Object.entries(fields)) {
      const [secLabel, fieldLabel] = labelOf(secKey, field);
      lines.push(`${secLabel} · ${fieldLabel}: ${fmt(values.sections[secKey]?.[field])} → ${fmt(value)}`);
    }
  }
  for (const [key, value] of Object.entries(ownerEdits)) {
    lines.push(`${t('👤 Utilizador')} · ${key}: ${fmt(values.owner[key])} → ${fmt(value)}`);
  }
  const ok = await showDialog(escapeHtml(t('Guardar Configuração')),
    `<p class="modal-text">${escapeHtml(t('⚠  O nó irá reiniciar após guardar para aplicar as configurações.\n'))}</p>
     <div class="tr-result mono">${escapeHtml(lines.join('\n'))}</div>`,
    [{ label: t('Cancelar'), value: false }, { label: `💾 ${t('Gravar no nó')}`, value: true, primary: true }]);
  if (!ok) return;

  setSaving(true);
  const { ok: httpOk, data } = await postJSON('/api/config/save', { sections: edits, owner: ownerEdits });
  setSaving(false);
  if (!httpOk) { await info(t('Erro'), data.error || t('Não foi possível gravar.')); return; }
  await reportResult(data, () => {
    edits = {};
    ownerEdits = {};
  });
}

async function saveChannels() {
  const ok = await showDialog(escapeHtml(t('Guardar Canais')),
    `<p class="modal-text">${escapeHtml(t('Guardar todas as alterações de canais no nó?\n\n') + t('⚠  O nó irá reiniciar para aplicar as alterações.\n'))}</p>`,
    [{ label: t('Cancelar'), value: false }, { label: `💾 ${t('Gravar canais')}`, value: true, primary: true }]);
  if (!ok) return;

  setSaving(true);
  const { ok: httpOk, data } = await postJSON('/api/channels/save', { channels: channelsDraft });
  setSaving(false);
  if (!httpOk) { await info(t('Erro'), data.error || t('Não foi possível gravar.')); return; }
  await reportResult(data, () => { values.channels = cloneChannels(channelsDraft); });
}

/* Resultado de uma gravação. Com erros de validação nada foi gravado e as
   alterações ficam para o utilizador corrigir; com sucesso, o nó reinicia e
   a configuração é relida quando ele voltar. */
async function reportResult(result, onSaved) {
  const saved = (result.saved || []).length || result.owner || result.canned;
  const errors = result.errors || [];
  if (saved) {
    onSaved();
    values = null;           // relê-se quando o nó voltar a ficar pronto
    const parts = [...(result.saved || []).map(String)];
    if (result.owner) parts.push(t('setOwner (nome do nó)'));
    if (result.canned) parts.push(t('setCannedMessages'));
    await info(t('Configuração Guardada'),
      `${t('Configuração guardada com sucesso!')}\n\n• ${parts.join('\n• ')}\n\n`
      + t('O nó vai reiniciar; a configuração é relida quando voltar a ligar.')
      + (errors.length ? `\n\n⚠ ${errors.join('\n')}` : ''));
    load();
  } else if (errors.length) {
    await info(t('Nada foi gravado'), errors.join('\n'));
  } else {
    await info(t('Sem Alterações'), t('Não foram detectadas alterações para guardar.'));
  }
}

function setSaving(on) {
  document.querySelectorAll('#config-body [data-save], #config-body [data-discard]')
    .forEach((b) => { b.disabled = on; });
  if (on) document.querySelectorAll('#config-body [data-save]').forEach((b) => { b.textContent = t('A guardar…'); });
}

function info(title, text) {
  return showDialog(escapeHtml(title), `<p class="modal-text">${escapeHtml(text)}</p>`,
    [{ label: 'OK', value: true, primary: true }]);
}
