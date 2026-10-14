/*
 * web/js/app.js — Shell da aplicação: login, ligação ao nó, abas, estado global.
 *
 * Cada aba vive no seu próprio módulo em js/tabs/ e subscreve o store
 * directamente; este ficheiro só trata do que é transversal — autenticação,
 * troca de abas, barra de estado, consola, idioma e toasts de erro.
 */
'use strict';

import { api, postJSON, getJSON } from './api.js';
import { store } from './store.js';
import { connectSocket, commands } from './socket.js';
import { debounce, escapeHtml, MOBILE_QUERY } from './utils.js';
import { getLanguage, loadLanguage, t } from './i18n.js';

import * as NodesTab from './tabs/nodes.js';
import * as MapTab from './tabs/map.js';
import * as MessagesTab from './tabs/messages.js';
import * as NavigationTab from './tabs/navigation.js';
import * as MetricsTab from './tabs/metrics.js';
import * as ConfigTab from './tabs/config.js';
import * as Traceroute from './traceroute.js';
import { initSound, isSoundEnabled, setSoundEnabled, playMessageSound } from './sound.js';

const $ = (id) => document.getElementById(id);

const TABS = {
  nodes: NodesTab,
  map: MapTab,
  messages: MessagesTab,
  navigation: NavigationTab,
  metrics: MetricsTab,
  config: ConfigTab,
};

let activeTab = 'nodes';
let wantedLanguage = null;   // escolhido aqui, ainda não confirmado pelo servidor

/* ── arranque ─────────────────────────────────────────────────────────── */

async function boot() {
  const { data } = await api('/api/whoami');
  store.httpsPort = data.https_port || null;   // HTTPS ao lado, para a bússola
  await loadLanguage(data.language);
  setLanguageUi(data.language);
  if (data.authenticated) {
    showApp();
  } else {
    $('login').hidden = false;
    $('password').focus();
  }
}

/* ── login ────────────────────────────────────────────────────────────── */

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitBtn = event.target.querySelector('button[type="submit"]');
  if (submitBtn.disabled) return;
  submitBtn.disabled = true;
  const error = $('login-error');
  error.hidden = true;

  const result = await postJSON('/api/login', {
    password: $('password').value,
    remember: $('remember').checked,
  });

  if (!result.ok) {
    error.textContent = result.data.error || t('Login falhou.');
    error.hidden = false;
    $('password').select();
    submitBtn.disabled = false;
    return;
  }
  $('login').hidden = true;
  showApp();
});

$('logout').addEventListener('click', async () => {
  await postJSON('/api/logout', {});
  location.reload();
});

function showApp() {
  $('app').hidden = false;
  wireLayout();
  connectSocket(showToast);
  wireTabs();
  wireHeader();
  wireConnectModal();
  mountAllTabs();
  switchTab('nodes');
}

/* ── layout móvel ─────────────────────────────────────────────────────── */

/* O CSS escolhe o layout pelo @media; aqui marca-se o body com .mobile e
   mede-se a altura do cabeçalho + abas, que no telemóvel varia (o cabeçalho
   quebra linha), para as abas de altura fixa (mapa, mensagens, métricas)
   ocuparem exactamente o resto do ecrã. */
function wireLayout() {
  const chrome = [document.querySelector('header'), $('tabs'), $('reconnect-bar')];
  const measure = () => {
    const h = chrome.reduce((sum, el) => sum + (el.hidden ? 0 : el.offsetHeight), 0);
    document.documentElement.style.setProperty('--chrome-h', `${h}px`);
  };
  const apply = () => {
    document.body.classList.toggle('mobile', MOBILE_QUERY.matches);
    measure();
    // O Leaflet tem de voltar a medir o contentor quando o layout muda.
    TABS[activeTab]?.onShow?.();
  };
  const observer = new ResizeObserver(measure);
  chrome.forEach((el) => observer.observe(el));
  MOBILE_QUERY.addEventListener('change', apply);
  apply();
}

/* ── abas ─────────────────────────────────────────────────────────────── */

function mountAllTabs() {
  NodesTab.mount({
    openMessages: (nodeId) => { switchTab('messages'); MessagesTab.openDm(nodeId); },
    showOnMap: (nodeId) => { switchTab('map'); MapTab.focusNode(nodeId); },
  });
  MapTab.mount();
  Traceroute.mount({
    hasTracerouteWith: MapTab.hasTracerouteWith,
    drawableSegments: MapTab.tracerouteSegments,
    showOnMap: (origin, dest) => { switchTab('map'); MapTab.focusTraceroute(origin, dest); },
  });
  MessagesTab.mount();
  NavigationTab.mount({
    showOnMap: (nodeId) => { switchTab('map'); MapTab.focusNode(nodeId); },
  });
  MetricsTab.mount();
  ConfigTab.mount();
}

function wireTabs() {
  document.getElementById('tabs').addEventListener('click', (event) => {
    const btn = event.target.closest('.tab');
    if (btn) switchTab(btn.dataset.tab);
  });
}

function switchTab(name) {
  if (!TABS[name]) return;
  TABS[activeTab]?.onHide?.();
  activeTab = name;

  document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  // No telemóvel a barra de abas rola na horizontal: mostrar a aba activa.
  document.querySelector(`.tab[data-tab="${name}"]`)?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  document.querySelectorAll('.panel-tab').forEach((p) => p.classList.toggle('active', p.id === `panel-${name}`));

  TABS[name]?.onShow?.();
}

/* ── cabeçalho: estado da ligação, pesquisa, idioma, consola ────────────── */

function wireHeader() {
  const search = $('search');
  search.addEventListener('input', debounce(() => store.setSearchText(search.value), 150));

  // O catálogo carrega primeiro; o snapshot que o servidor devolve a seguir
  // já redesenha tudo com os textos novos.
  $('lang-seg').addEventListener('click', async (event) => {
    const lang = event.target.closest('[data-lang]')?.dataset.lang;
    if (!lang || lang === (wantedLanguage || getLanguage())) return;
    wantedLanguage = lang;
    setLanguageUi(lang);
    await switchLanguage(lang);
    commands.setLanguage(lang);
  });

  wireMenu();
  wireSound();
  $('btn-console').addEventListener('click', () => { closeMenu(); $('console-drawer').hidden = false; });
  $('console-close').addEventListener('click', () => { $('console-drawer').hidden = true; });

  store.on('snapshot', (snapshot) => {
    // O idioma é do servidor: se outro cliente o mudou, este acompanha.
    // Excepto se este cliente pediu outro e o servidor ainda não o aplicou
    // (o comando perde-se com o socket em baixo, e o snapshot de (re)ligação
    // traz o idioma antigo): aí reenvia-se o pedido em vez de reverter.
    if (wantedLanguage && snapshot.language !== wantedLanguage) {
      commands.setLanguage(wantedLanguage);
    } else if (snapshot.language) {
      wantedLanguage = null;
      setLanguageUi(snapshot.language);
      switchLanguage(snapshot.language);
    }
    $('console-log').textContent = (snapshot.log || []).join('\n') + '\n';
    $('console-log').scrollTop = $('console-log').scrollHeight;
  });
  store.on('log-line', (line) => appendLog(line));

  store.on('connection-changed', renderConnectionState);
  store.on('local-node-changed', renderNodeLabel);
  store.on('packet-stats-changed', renderPacketStats);
  store.on('messages-changed', renderMessagesBadge);

  renderConnectionState();
  renderNodeLabel();
  renderPacketStats();
  renderMessagesBadge();
}

function appendLog(line) {
  const el = $('console-log');
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
  el.textContent += `${line}\n`;
  const lines = el.textContent.split('\n');
  if (lines.length > 600) el.textContent = lines.slice(-500).join('\n');
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function renderConnectionState() {
  const pill = $('conn-state');
  const { connected, target } = store.connection;
  const reconnect = store.reconnect;

  if (connected) {
    pill.textContent = target?.mode === 'serial'
      ? `🟢 ${target.serial_port}` : `🟢 ${target?.host}:${target?.port}`;
    pill.className = 'pill on';
  } else if (reconnect?.attempt > 0) {
    pill.textContent = t('tentativa {n}', { n: reconnect.attempt });
    pill.className = 'pill wait';
  } else {
    // Sem ligação, o pill é o ponto de entrada para ligar ao nó: tem de se
    // ver como um botão, não como um estado.
    pill.textContent = t('Conectar');
    pill.className = 'pill connect';
  }
  pill.onclick = () => {
    if (store.connection.connected) commands.disconnectNode();
    else openConnectModal();
  };
  pill.onkeydown = (event) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pill.onclick(); }
  };
  pill.style.cursor = 'pointer';

  const bar = $('reconnect-bar');
  if (!connected && reconnect?.attempt > 0) {
    bar.hidden = false;
    bar.textContent = t('🔄 Reconexão: tentativa {attempt}, próxima dentro de {delay}s',
      { attempt: reconnect.attempt, delay: reconnect.delay_s });
  } else {
    bar.hidden = true;
  }
}

function renderNodeLabel() {
  const local = store.localNode || {};
  $('node-label').textContent = local.node_id
    ? `🏠 ${local.long_name || local.node_id} [${local.short_name || ''}]` : '';
}

function renderPacketStats() {
  const { snr, packets_per_min: ppm } = store.packetStats;
  $('packet-stats').textContent = `SNR ${snr != null ? snr.toFixed(1) : '—'} dB · ${ppm || 0} pkt/min`;
}

function renderMessagesBadge() {
  const total = store.totalUnread();
  const badge = $('badge-messages');
  badge.textContent = total > 99 ? '99+' : String(total);
  badge.hidden = total === 0;
}

/* ── menu do cabeçalho (idioma, consola, sair) ───────────────────────── */

function wireMenu() {
  $('menu-btn').addEventListener('click', (event) => {
    event.stopPropagation();
    if ($('menu').hidden) openMenu(); else closeMenu();
  });
  document.addEventListener('click', (event) => {
    if (!$('menu').hidden && !event.target.closest('.menu-wrap')) closeMenu();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('menu').hidden) { closeMenu(); $('menu-btn').focus(); }
  });
}

/* Som de mensagens: preferência deste dispositivo. Ao ligar toca uma
   amostra — e o próprio toque no botão desbloqueia o áudio no iPhone. */
function wireSound() {
  initSound();
  setSoundUi();
  $('sound-seg').addEventListener('click', (event) => {
    const button = event.target.closest('[data-sound]');
    if (!button) return;
    const on = button.dataset.sound === 'on';
    setSoundEnabled(on);
    setSoundUi();
    if (on) playMessageSound('channel', { force: true });
  });
}

function setSoundUi() {
  const on = isSoundEnabled();
  document.querySelectorAll('#sound-seg [data-sound]').forEach((b) => {
    const active = (b.dataset.sound === 'on') === on;
    b.classList.toggle('active', active);
    b.setAttribute('aria-pressed', String(active));
  });
}

function openMenu() {
  $('menu').hidden = false;
  $('menu-btn').setAttribute('aria-expanded', 'true');
}

function closeMenu() {
  $('menu').hidden = true;
  $('menu-btn').setAttribute('aria-expanded', 'false');
}

function setLanguageUi(lang) {
  const value = lang === 'pt' ? 'pt' : 'en';
  document.querySelectorAll('#lang-seg [data-lang]').forEach((b) => {
    const on = b.dataset.lang === value;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
}

/* ── idioma ───────────────────────────────────────────────────────────── */

/* O HTML estático é traduzido pelo i18n.js; o conteúdo gerado em JS é
   redesenhado reemitindo os eventos do store que cada aba já escuta. */
async function switchLanguage(lang) {
  if (!(await loadLanguage(lang))) return;
  renderConnectionState();
  ['language-changed', 'nodes-changed', 'channels-changed', 'messages-changed',
   'local-node-changed'].forEach((topic) => store.emit(topic));
}

/* ── modal de ligação ─────────────────────────────────────────────────── */

function wireConnectModal() {
  const modeSelect = $('c-mode');
  modeSelect.addEventListener('change', () => {
    updateConnectFields();
    if (modeSelect.value === 'serial') loadSerialPorts();
  });
  $('c-serial-refresh').addEventListener('click', loadSerialPorts);

  $('c-cancel').addEventListener('click', () => { $('connect-modal').hidden = true; });

  // Nós guardados: tocar preenche host/porta; ⭐ fixa como favorito; ✕ esquece.
  $('c-targets').addEventListener('click', async (event) => {
    const li = event.target.closest('li[data-host]');
    if (!li) return;
    const target = { host: li.dataset.host, port: Number(li.dataset.port) };
    const action = event.target.closest('[data-target-action]')?.dataset.targetAction;
    if (action === 'fav') {
      const { ok, data } = await postJSON('/api/targets/favorite',
        { ...target, favorite: li.dataset.favorite !== 'true' });
      if (ok) renderSavedTargets(data.targets);
    } else if (action === 'forget') {
      const { ok, data } = await postJSON('/api/targets/forget', target);
      if (ok) renderSavedTargets(data.targets);
    } else {
      $('c-host').value = target.host;
      $('c-port').value = target.port;
      highlightSavedTarget();
    }
  });
  $('c-host').addEventListener('input', highlightSavedTarget);
  $('c-port').addEventListener('input', highlightSavedTarget);

  $('connect-form').addEventListener('submit', (event) => {
    event.preventDefault();
    commands.connectNode({
      mode: modeSelect.value,
      host: $('c-host').value,
      port: Number($('c-port').value),
      serial_port: $('c-serial').value,
    });
    $('connect-modal').hidden = true;
  });
}

async function openConnectModal() {
  const target = store.connection.target || {};
  $('c-mode').value = target.mode || 'tcp';
  $('c-host').value = target.host || 'localhost';
  $('c-port').value = target.port || 4403;
  updateConnectFields();

  getJSON('/api/targets').then(({ ok, data }) => { if (ok) renderSavedTargets(data.targets); });

  loadSerialPorts();
  $('connect-modal').hidden = false;
}

/* Lê as portas USB do servidor. Chamado ao abrir o modal, ao escolher o
   modo serial e pelo botão ⟳ — um nó ligado depois de abrir o modal
   aparece sem fechar e reabrir. Mantém a porta escolhida se ainda existir. */
async function loadSerialPorts() {
  const select = $('c-serial');
  const button = $('c-serial-refresh');
  const previous = select.value || store.connection.target?.serial_port || '';
  button.disabled = true;
  try {
    const { ok, data } = await getJSON('/api/serial-ports');
    if (!ok) return;
    const ports = data.ports || [];
    select.innerHTML = ports.map((p) => {
      const label = [p.device, p.vendor || p.description].filter(Boolean).join(' — ');
      return `<option value="${escapeHtml(p.device)}">${escapeHtml(label)}</option>`;
    }).join('') || `<option value="">${escapeHtml(t('nenhuma porta detectada'))}</option>`;
    if (ports.some((p) => p.device === previous)) select.value = previous;
  } finally {
    button.disabled = false;
  }
}

let savedTargets = [];

function renderSavedTargets(targets) {
  savedTargets = targets || [];
  $('c-targets').innerHTML = savedTargets.map((tg) => `
    <li data-host="${escapeHtml(tg.host)}" data-port="${tg.port}" data-favorite="${!!tg.favorite}">
      <button type="button" class="ghost" data-target-action="fav"
              title="${escapeHtml(t(tg.favorite ? 'Remover dos favoritos' : 'Marcar como favorito'))}">${tg.favorite ? '⭐' : '☆'}</button>
      <span class="t-main">
        <span class="t-name">${escapeHtml(tg.name || tg.host)}</span>
        <span class="t-addr mono">${escapeHtml(tg.host)}:${tg.port}</span>
      </span>
      <button type="button" class="ghost" data-target-action="forget"
              title="${escapeHtml(t('Esquecer este nó'))}">✕</button>
    </li>`).join('');
  updateConnectFields();
  highlightSavedTarget();
}

function highlightSavedTarget() {
  const host = $('c-host').value.trim();
  const port = Number($('c-port').value);
  $('c-targets').querySelectorAll('li').forEach((li) => {
    li.classList.toggle('active', li.dataset.host === host && Number(li.dataset.port) === port);
  });
}

function updateConnectFields() {
  const serial = $('c-mode').value === 'serial';
  $('c-tcp-fields').hidden = serial;
  $('c-serial-fields').hidden = !serial;
  $('c-saved').hidden = serial || savedTargets.length === 0;
}

/* ── toasts ───────────────────────────────────────────────────────────── */

function showToast(message) {
  if (!message) return;
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = message;
  $('toasts').appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

boot();
