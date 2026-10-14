/*
 * web/js/tabs/metrics.js — Aba de métricas (11 secções).
 *
 * O servidor já gera o HTML completo de cada secção (Chart.js incluído) em
 * core/metrics.py, exactamente como o QWebEngineView injectava no desktop.
 * Aqui isola-se num <iframe>: o HTML referencia globais próprias
 * (window.Chart, funções auxiliares) que colidiriam com a página principal
 * se fossem injectadas directamente via innerHTML — e um <script> inserido
 * por innerHTML não executa de qualquer forma.
 *
 * O HTML carrega-se uma vez por secção. Depois, a cada REFRESH_MS, só os
 * dados vêm do servidor e entram por window._metricsUpdateData — a mesma
 * função que o desktop chama via runJavaScript — sem recarregar a página,
 * por isso o scroll e o estado dos gráficos ficam onde o utilizador os
 * deixou. Recarrega-se apenas quando não há alternativa (ver tick()).
 */
'use strict';

import { store } from '../store.js';
import { commands } from '../socket.js';
import { escapeHtml } from '../utils.js';
import { t } from '../i18n.js';
import { getJSON } from '../api.js';

const REFRESH_MS = 5000;

let activeKey = null;
let refreshTimer = null;
let visible = false;
let ticking = false;
// Estado da secção carregada, para decidir se basta actualizar dados.
let loadedHasData = true;
let loadedReloadKey = '';

export function mount() {
  document.getElementById('metrics-clear').addEventListener('click', () => {
    if (!confirm(t('Limpar todos os dados de métricas recolhidos nesta sessão?'))) return;
    commands.clearMetrics();
    loadFrame();   // a secção activa reflecte o esquecimento de imediato
  });

  // O snapshot chega também depois de uma troca de idioma; o HTML de cada
  // secção é gerado no servidor, no idioma activo, por isso recarrega-se,
  // mas mantendo a posição de scroll.
  store.on('snapshot', () => { renderSectionList(); if (visible) loadFrame({ keepScroll: true }); });
  // A pesquisa global filtra as tabelas da secção, como no desktop.
  store.on('search-changed', applyFilter);

  document.getElementById('metrics-frame').addEventListener('load', onFrameLoad);
  renderSectionList();
}

export function onShow() {
  visible = true;
  if (!activeKey && store.metricSections.length) activate(store.metricSections[0].key);
  scheduleRefresh();
}

export function onHide() {
  visible = false;
  clearTimeout(refreshTimer);
}

function renderSectionList() {
  const list = document.getElementById('metrics-list');
  list.innerHTML = store.metricSections.map((s) => `
    <li data-key="${s.key}" class="${s.key === activeKey ? 'active' : ''}">${escapeHtml(s.label)}</li>
  `).join('');
  list.onclick = (event) => {
    const li = event.target.closest('li[data-key]');
    if (li) activate(li.dataset.key);
  };
}

function activate(key) {
  activeKey = key;
  renderSectionList();
  document.querySelector('#metrics-list li.active')
    ?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  loadFrame();
}

async function fetchState(key) {
  const { ok, data } = await getJSON(`/api/metrics/${encodeURIComponent(key)}/refresh`);
  return ok ? data : null;
}

let pendingScroll = null;

async function loadFrame({ keepScroll = false } = {}) {
  if (!activeKey) return;
  const key = activeKey;
  const frame = document.getElementById('metrics-frame');
  pendingScroll = null;
  if (keepScroll) {
    try { pendingScroll = { x: frame.contentWindow.scrollX, y: frame.contentWindow.scrollY }; } catch { /* sem página */ }
  }
  // O estado é lido antes do HTML: se os dados chegarem entre os dois
  // pedidos, o próximo tick vê a diferença e recarrega uma vez — nunca o
  // contrário (ficar preso no ecrã de espera).
  const state = await fetchState(key);
  if (key !== activeKey) return;
  loadedHasData = state ? state.has_data : true;
  loadedReloadKey = state ? state.reload_key : '';
  // Cache-busting: o browser não pode servir uma versão antiga da secção.
  frame.src = `/api/metrics/${encodeURIComponent(key)}?t=${Date.now()}`;
  scheduleRefresh();
}

function onFrameLoad() {
  const win = document.getElementById('metrics-frame').contentWindow;
  if (pendingScroll && win) win.scrollTo(pendingScroll.x, pendingScroll.y);
  pendingScroll = null;
  applyFilter();
}

function applyFilter() {
  try {
    document.getElementById('metrics-frame').contentWindow?._metricsFilterTable?.(store.searchText || '');
  } catch { /* página ainda a carregar */ }
}

/* Um ciclo de actualização. Recarrega o HTML só em dois casos, tal como o
   desktop: a secção saiu do ecrã de espera (que não tem gráficos), ou é o
   nó local e um dos seus valores mudou (essa página não tem actualização
   incremental). Em todos os outros, os dados entram na página já aberta. */
async function tick() {
  if (!activeKey || ticking) return;
  ticking = true;
  const key = activeKey;
  try {
    const state = await fetchState(key);
    if (!state || key !== activeKey || !visible) return;
    if (!loadedHasData && state.has_data) {
      loadFrame({ keepScroll: true });
    } else if (state.reload_key && state.reload_key !== loadedReloadKey) {
      loadFrame({ keepScroll: true });
    } else if (state.data && Object.keys(state.data).length) {
      const win = document.getElementById('metrics-frame').contentWindow;
      win?._metricsUpdateData?.(state.data);
    }
  } catch (err) {
    console.debug('[metrics] refresh falhou', err);
  } finally {
    ticking = false;
  }
}

function scheduleRefresh() {
  clearTimeout(refreshTimer);
  if (!visible) return;
  refreshTimer = setTimeout(async () => {
    await tick();
    scheduleRefresh();
  }, REFRESH_MS);
}
