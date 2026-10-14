/*
 * web/js/tabs/nodes.js — Aba de lista de nós.
 *
 * Espelha NodeTableModel/tabs/tab_nodes.py do desktop: nó local fixo no topo,
 * favoritos logo a seguir, pesquisa por ID/nome, acções rápidas por linha
 * (favorito, DM, centrar no mapa, traceroute) e detalhe do último pacote.
 */
'use strict';

import { store } from '../store.js';
import { commands } from '../socket.js';
import { requestTraceroute } from '../traceroute.js';
import { api } from '../api.js';
import {
  escapeHtml, fmtNum, fmtTime, fmtAgo, freshness, nodeBadge, fmtBattery,
  snrClass, showModal, MOBILE_QUERY, isMobile,
} from '../utils.js';
import { t } from '../i18n.js';

let onOpenMessages = null;
let onShowOnMap = null;

// Vista no desktop: cartões (como no telemóvel) ou tabela compacta. É uma
// preferência deste browser; no telemóvel usam-se sempre cartões.
const VIEW_KEY = 'meshdeck.nodesView';
let desktopView = readView();

function readView() {
  try { return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'cards'; } catch { return 'cards'; }
}

function useCards() {
  return isMobile() || desktopView === 'cards';
}

export function mount({ openMessages, showOnMap }) {
  onOpenMessages = openMessages;
  onShowOnMap = showOnMap;

  // Tabela (desktop) e cartões (telemóvel) partilham as acções: botões com
  // data-action dentro de um elemento com data-id.
  for (const container of [document.querySelector('#nodes-table tbody'),
                           document.getElementById('nodes-cards')]) {
    container.addEventListener('click', onClick);
    container.addEventListener('dblclick', (event) => {
      const item = event.target.closest('[data-id]');
      if (item && !event.target.closest('[data-action]')) showPacketDetail(item.dataset.id);
    });
  }

  document.getElementById('nodes-view').addEventListener('click', (event) => {
    const button = event.target.closest('[data-view]');
    if (!button) return;
    desktopView = button.dataset.view;
    try { localStorage.setItem(VIEW_KEY, desktopView); } catch { /* sem storage */ }
    render();
  });

  store.on('nodes-changed', render);
  store.on('search-changed', render);
  store.on('selection-changed', render);
  store.on('language-changed', render);
  MOBILE_QUERY.addEventListener('change', render);
  // "há 5 min" envelhece sozinho: refresca os tempos uma vez por minuto.
  setInterval(render, 60_000);
  render();
}

function onClick(event) {
  const item = event.target.closest('[data-id]');
  if (!item) return;
  const nodeId = item.dataset.id;
  const button = event.target.closest('[data-action]');
  if (!button) {
    // Toque no nó: fica seleccionado (bússola da Navegação, destaque no mapa).
    store.setSelectedNode(nodeId);
    return;
  }
  if (button.disabled) return;
  const action = button.dataset.action;
  if (action === 'fav') { store.toggleFavoriteLocal(nodeId); commands.toggleFavorite(nodeId); }
  else if (action === 'dm') onOpenMessages?.(nodeId);
  else if (action === 'map') { store.setSelectedNode(nodeId); onShowOnMap?.(nodeId); }
  else if (action === 'tr') requestTraceroute(nodeId);
  else if (action === 'info') showPacketDetail(nodeId);
}

async function showPacketDetail(nodeId) {
  const { ok, data } = await api(`/api/node/${encodeURIComponent(nodeId)}/packet`);
  const node = store.nodes.get(nodeId);
  const title = node ? `${escapeHtml(node.long_name || nodeId)} [${escapeHtml(node.short_name || '')}]` : escapeHtml(nodeId);
  const body = ok
    ? `<pre class="mono">${escapeHtml(JSON.stringify(data.packet, null, 2))}</pre>`
    : `<p class="muted">${escapeHtml(t('Sem pacote registado para este nó ainda.'))}</p>`;
  showModal(title, body);
}

/* ── peças comuns à tabela e aos cartões ──────────────────────────────── */

function facts(node) {
  const id = node.id_string;
  return {
    id,
    isFav: store.favorites.has(id),
    hasGps: node.latitude != null && node.longitude != null,
    canDm: !node.is_local && node.last_heard != null,
    name: node.long_name || (node.is_local ? t('Nó local') : id),
  };
}

function itemClass(node, f) {
  return [node.is_local && 'is-local', f.isFav && !node.is_local && 'is-fav',
          f.id === store.selectedNodeId && 'is-selected'].filter(Boolean).join(' ');
}

function actionButtons(node, f, { withInfo, withFav = true }) {
  const btn = (action, icon, title, enabled = true) =>
    `<button type="button" class="node-act" data-action="${action}" title="${escapeHtml(title)}"
             ${enabled ? '' : 'disabled'}>${icon}</button>`;
  return [
    node.is_local || !withFav ? '' : btn('fav', f.isFav ? '⭐' : '☆',
      t(f.isFav ? 'Remover dos favoritos' : 'Adicionar aos favoritos')),
    btn('dm', '💬', t(f.canDm ? 'Enviar DM' : 'Nó nunca contactado'), f.canDm),
    btn('map', '📍', t(f.hasGps ? 'Ver no mapa' : 'Sem posição'), f.hasGps),
    node.is_local ? '' : btn('tr', '📡', 'Traceroute'),
    withInfo ? btn('info', 'ℹ️', t('Último pacote')) : '',
  ].join('');
}

function lastHeard(node) {
  return `<span class="seen ${freshness(node.last_heard)}" title="${escapeHtml(fmtTime(node.last_heard))}">`
    + `${escapeHtml(node.is_local ? t('Nó local') : fmtAgo(node.last_heard))}</span>`;
}

function snrText(node) {
  if (node.is_local) return '';
  return node.snr != null ? `<span class="snr ${snrClass(node.snr)}">${fmtNum(node.snr)} dB</span>` : '';
}

function hopsText(node) {
  if (node.is_local || node.hops_away == null) return '';
  if (node.hops_away === 0) return t('directo');
  return node.hops_away === 1 ? t('1 hop') : t('{n} hops', { n: node.hops_away });
}

/* ── desktop: tabela ──────────────────────────────────────────────────── */

function tableRow(node) {
  const f = facts(node);
  const pos = f.hasGps ? `${node.latitude.toFixed(5)}, ${node.longitude.toFixed(5)}` : '—';
  return `
    <tr class="${itemClass(node, f)}" data-id="${escapeHtml(f.id)}">
      <td class="node-actions">${actionButtons(node, f, { withInfo: true })}</td>
      <td class="node-name">${nodeBadge(node)} <span>${escapeHtml(f.name)}</span>
        ${node.via_mqtt ? '<span class="tag">☁ MQTT</span>' : ''}</td>
      <td class="mono muted">${escapeHtml(f.id)}</td>
      <td>${lastHeard(node)}</td>
      <td class="num">${snrText(node) || '—'}</td>
      <td class="num">${node.is_local ? '' : (node.hops_away ?? '—')}</td>
      <td class="num">${escapeHtml(fmtBattery(node.battery_level)) || '—'}</td>
      <td class="muted">${escapeHtml(node.hw_model || '—')}</td>
      <td class="mono">${pos}</td>
      <td class="num">${node.altitude != null ? `${node.altitude} m` : '—'}</td>
      <td class="muted">${escapeHtml(node.last_packet_type || '—')}</td>
    </tr>`;
}

/* ── telemóvel: cartões ───────────────────────────────────────────────── */

function card(node) {
  const f = facts(node);
  const sub = [f.id, node.hw_model].filter(Boolean).map(escapeHtml).join(' · ');
  const chips = [
    lastHeard(node),
    snrText(node),
    hopsText(node) && `<span>${escapeHtml(hopsText(node))}</span>`,
    node.battery_level != null && `<span>${escapeHtml(fmtBattery(node.battery_level))}</span>`,
    node.via_mqtt && '<span class="tag">☁ MQTT</span>',
  ].filter(Boolean).join('');
  // Linha extra só visível no desktop (há espaço): o que a tabela mostrava.
  const extra = [
    f.hasGps && `📍 ${node.latitude.toFixed(5)}, ${node.longitude.toFixed(5)}`,
    node.altitude != null && `⛰ ${node.altitude} m`,
    node.last_packet_type && `✉ ${node.last_packet_type}`,
  ].filter(Boolean).map(escapeHtml).join(' · ');
  return `
    <div class="node-card ${itemClass(node, f)}" data-id="${escapeHtml(f.id)}">
      <div class="nc-head">
        ${nodeBadge(node)}
        <div class="nc-title"><b>${node.is_local ? '🏠 ' : ''}${escapeHtml(f.name)}</b>
          <span class="mono">${sub}</span></div>
        ${node.is_local ? '' : `<button type="button" class="node-act nc-fav" data-action="fav"
          title="${escapeHtml(t(f.isFav ? 'Remover dos favoritos' : 'Adicionar aos favoritos'))}">${f.isFav ? '⭐' : '☆'}</button>`}
      </div>
      <div class="nc-foot">
        <div class="nc-chips">${chips}</div>
        <div class="nc-actions">${actionButtons(node, f, { withInfo: true, withFav: false })}</div>
      </div>
      ${extra ? `<div class="nc-extra mono">${extra}</div>` : ''}
    </div>`;
}

function render() {
  document.getElementById('n-total').textContent = store.counts.total || 0;
  document.getElementById('n-contacted').textContent = store.counts.contacted || 0;
  document.getElementById('n-online').textContent = store.counts.online || 0;

  const nodes = store.visibleNodes();
  document.getElementById('n-gps').textContent =
    store.nodeList().filter((n) => n.latitude != null && n.longitude != null).length;

  nodes.sort((a, b) => {
    if (a.is_local !== b.is_local) return a.is_local ? -1 : 1;
    const favA = store.favorites.has(a.id_string);
    const favB = store.favorites.has(b.id_string);
    if (favA !== favB) return favA ? -1 : 1;
    return (b.last_heard || 0) - (a.last_heard || 0);
  });

  // Só se desenha a vista visível; a outra fica vazia até mudar o layout.
  const cards = useCards();
  document.getElementById('nodes-cards').hidden = !cards;
  document.querySelector('.nodes-table-wrap').hidden = cards;
  document.querySelectorAll('#nodes-view [data-view]').forEach((b) => {
    b.classList.toggle('active', b.dataset.view === desktopView);
  });
  const empty = `<p class="muted center">${escapeHtml(t('Nenhum nó encontrado.'))}</p>`;
  if (cards) {
    document.querySelector('#nodes-table tbody').innerHTML = '';
    document.getElementById('nodes-cards').innerHTML = nodes.map(card).join('') || empty;
  } else {
    document.getElementById('nodes-cards').innerHTML = '';
    document.querySelector('#nodes-table tbody').innerHTML = nodes.map(tableRow).join('')
      || `<tr><td colspan="11">${empty}</td></tr>`;
  }
}
