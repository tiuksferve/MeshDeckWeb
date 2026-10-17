/*
 * web/js/tabs/messages.js — Aba de mensagens (canais + DMs).
 *
 * Lista de canais com não-lidas, DMs ordenadas por actividade recente,
 * indicador PKI/PSK, estado de ACK/NAK por mensagem enviada, separadores de
 * data.
 */
'use strict';

import { store } from '../store.js';
import { commands } from '../socket.js';
import { escapeHtml, fmtTime, isMobile } from '../utils.js';
import { dateLocale, t } from '../i18n.js';

let activeKey = null;
let visible = false;
let replyTo = null;   // { packetId, from, text } enquanto se responde a uma mensagem

export function mount() {
  document.getElementById('channel-list').addEventListener('click', (event) => {
    const li = event.target.closest('li[data-key]');
    if (li) activate(li.dataset.key);
  });
  document.getElementById('dm-list').addEventListener('click', (event) => {
    const li = event.target.closest('li[data-key]');
    if (li) activate(li.dataset.key);
  });

  // Em ecrã estreito a lista e a conversa ocupam o mesmo espaço; a seta
  // volta à lista.
  document.getElementById('conv-back').addEventListener('click', () => {
    document.querySelector('.messages-layout').classList.remove('conv-open');
  });

  // ↩ numa mensagem recebida: a próxima mensagem sai como resposta nativa
  // (reply_id), que as apps Meshtastic mostram ligada à original.
  document.getElementById('conv-view').addEventListener('click', (event) => {
    const button = event.target.closest('[data-reply]');
    if (!button) return;
    const entry = (store.conversations.get(activeKey) || [])
      .find((e) => String(e.packet_id) === button.dataset.reply);
    if (entry) startReply(entry);
  });
  document.getElementById('reply-cancel').addEventListener('click', cancelReply);
  document.getElementById('composer-text').addEventListener('keydown', (event) => {
    if (event.key === 'Escape') cancelReply();
  });

  const form = document.getElementById('composer');
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const input = document.getElementById('composer-text');
    const text = input.value.trim();
    if (!text || !activeKey) return;
    const replyId = replyTo?.packetId || 0;
    if (activeKey.startsWith('ch:')) {
      commands.sendChannelMessage(Number(activeKey.slice(3)), text, replyId);
    } else {
      commands.sendDm(activeKey.slice(3), text, replyId);
    }
    input.value = '';
    cancelReply();
  });

  store.on('channels-changed', renderChannelList);
  store.on('nodes-changed', renderDmList);
  store.on('messages-changed', (detail) => {
    // Mensagem nova na conversa que o utilizador tem à frente: já está lida.
    if (detail?.key && detail.key === activeKey && store.unread.get(activeKey)
        && conversationOnScreen()) {
      markRead(activeKey);
      return;   // markRead volta a emitir messages-changed, já sem não-lidas
    }
    renderChannelList();
    renderDmList();
    if (!detail?.key || detail.key === activeKey) renderConversation();
    updateHeaderBadge();
  });
  document.addEventListener('visibilitychange', markActiveIfOnScreen);
  renderChannelList();
  renderDmList();
  updateHeaderBadge();
}

/* Chamado pelo app.js, para abrir directamente a conversa de um nó a partir
   da aba de Nós (acção 💬). */
export function openDm(nodeId) {
  activate(`dm:${nodeId.toLowerCase()}`);
}

export function onShow() {
  visible = true;
  markActiveIfOnScreen();
}

export function onHide() {
  visible = false;
}

/* A conversa activa só conta como vista se a aba está aberta, o browser está
   em primeiro plano e, no telemóvel, a conversa (e não a lista) está aberta. */
function conversationOnScreen() {
  if (!visible || document.visibilityState !== 'visible') return false;
  return !isMobile() || document.querySelector('.messages-layout').classList.contains('conv-open');
}

function markActiveIfOnScreen() {
  if (activeKey && store.unread.get(activeKey) && conversationOnScreen()) markRead(activeKey);
}

/* Marca no servidor, não só aqui: o servidor guarda os contadores e
   reenvia-os a cada mensagem nova, e repô-los-ia se só o browser soubesse. */
function markRead(key) {
  store.markRead(key);
  commands.markRead(key);
}

function activate(key) {
  if (key !== activeKey) cancelReply();
  activeKey = key;
  markRead(key);
  renderChannelList();
  renderDmList();
  renderConversation();

  const input = document.getElementById('composer-text');
  const button = document.querySelector('#composer button');
  input.disabled = false;
  button.disabled = false;
  document.querySelector('.messages-layout').classList.add('conv-open');
  // No telemóvel o foco abriria o teclado por cima da conversa acabada de abrir.
  if (!isMobile()) input.focus();
}

function updateHeaderBadge() {
  const total = store.totalUnread();
  const badge = document.getElementById('badge-messages');
  badge.textContent = total > 99 ? '99+' : String(total);
  badge.hidden = total === 0;
}

function channelName(index) {
  const ch = store.channels.find((c) => c.index === index);
  const fallback = index === 0 ? 'Primary' : t('Canal {n}', { n: index });
  return ch ? (ch.name || fallback) : t('Canal {n}', { n: index });
}

function nodeLabel(nodeId) {
  const node = store.nodes.get(nodeId);
  if (!node) return nodeId;
  return node.long_name ? `${node.long_name} [${node.short_name || ''}]` : nodeId;
}

function renderChannelList() {
  const list = document.getElementById('channel-list');
  list.innerHTML = store.channels.map((ch) => {
    const key = `ch:${ch.index}`;
    const unread = store.unread.get(key) || 0;
    return `
      <li data-key="${key}" class="${key === activeKey ? 'active' : ''}">
        <span>${escapeHtml(channelName(ch.index))}</span>
        ${unread ? `<span class="unread">${unread}</span>` : ''}
      </li>`;
  }).join('') || `<li class="muted">${escapeHtml(t('Sem canais ainda.'))}</li>`;
}

function renderDmList() {
  // Ordenado por actividade: a conversa com o registo mais recente primeiro.
  const dmKeys = [...store.conversations.keys()].filter((k) => k.startsWith('dm:'));
  dmKeys.sort((a, b) => {
    const la = store.conversations.get(a);
    const lb = store.conversations.get(b);
    const ta = la?.length ? la[la.length - 1].time : 0;
    const tb = lb?.length ? lb[lb.length - 1].time : 0;
    return tb - ta;
  });

  const list = document.getElementById('dm-list');
  list.innerHTML = dmKeys.map((key) => {
    const nodeId = key.slice(3);
    const unread = store.unread.get(key) || 0;
    return `
      <li data-key="${key}" class="${key === activeKey ? 'active' : ''}">
        <span>${escapeHtml(nodeLabel(nodeId))}</span>
        ${unread ? `<span class="unread">${unread}</span>` : ''}
      </li>`;
  }).join('') || `<li class="muted">${escapeHtml(t('Nenhuma mensagem directa ainda.'))}</li>`;
}

function startReply(entry) {
  replyTo = { packetId: entry.packet_id, from: nodeLabel(entry.from_id), text: entry.text };
  const preview = entry.text.length > 80 ? `${entry.text.slice(0, 80)}…` : entry.text;
  document.getElementById('reply-banner-text').innerHTML =
    `↩ ${escapeHtml(t('Responder a'))} <b>${escapeHtml(replyTo.from)}</b>: ${escapeHtml(preview)}`;
  document.getElementById('reply-banner').hidden = false;
  document.getElementById('composer-text').focus();
}

function cancelReply() {
  replyTo = null;
  document.getElementById('reply-banner').hidden = true;
}

/* Citação da mensagem respondida. Resposta nativa: procura-se a original
   pelo packet_id na mesma conversa. Mensagens do MeshDeck desktop trazem a
   citação no texto ("↩ Nome: excerto" na primeira linha). */
function quoteFor(entry, entries) {
  if (entry.reply_id) {
    const original = entries.find((e) => e.packet_id === entry.reply_id);
    if (!original) return { quote: `↩ ${t('mensagem anterior')}`, text: entry.text };
    const who = original.outgoing ? t('Eu') : nodeLabel(original.from_id);
    const excerpt = original.text.length > 80 ? `${original.text.slice(0, 80)}…` : original.text;
    return { quote: `↩ ${who}: ${excerpt}`, text: entry.text };
  }
  const newline = entry.text.indexOf('\n');
  if (entry.text.startsWith('↩ ') && newline > 0) {
    return { quote: entry.text.slice(0, newline), text: entry.text.slice(newline + 1) };
  }
  return { quote: '', text: entry.text };
}

function transportLabel(entry) {
  if (entry.transport === 'pki') return '🔒 PKI';
  if (entry.transport === 'psk') return '📤 PSK';
  if (entry.transport === 'mqtt') return '☁ MQTT';
  return '📡 RF';
}

function statusLabel(entry) {
  if (!entry.outgoing) return '';
  if (entry.status === 'ack' || entry.status === 'ack_implicit') return `<span class="status ack">${t('✓ entregue')}</span>`;
  if (entry.status === 'nak') return `<span class="status nak">${t('✗ falhou')}${entry.status_detail ? ` (${escapeHtml(entry.status_detail)})` : ''}</span>`;
  return `<span class="status sending">${t('… a enviar')}</span>`;
}

function dateLabel(epochSeconds) {
  const d = new Date(epochSeconds * 1000);
  const today = new Date();
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return t('Hoje');
  if (d.toDateString() === yesterday.toDateString()) return t('Ontem');
  return d.toLocaleDateString(dateLocale(), { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function renderConversation() {
  const header = document.getElementById('conv-header');
  const view = document.getElementById('conv-view');

  if (!activeKey) {
    header.textContent = t('Selecciona uma conversa');
    header.className = 'muted';
    view.innerHTML = '';
    return;
  }

  header.className = '';
  header.textContent = activeKey.startsWith('ch:')
    ? `#${channelName(Number(activeKey.slice(3)))}`
    : nodeLabel(activeKey.slice(3));

  const entries = store.conversations.get(activeKey) || [];
  const isChannel = activeKey.startsWith('ch:');
  let prevDate = null;
  const html = entries.map((entry) => {
    const label = dateLabel(entry.time);
    const sep = label !== prevDate ? `<div class="date-sep">${label}</div>` : '';
    prevDate = label;
    const fromLabel = entry.outgoing ? t('Eu') : nodeLabel(entry.from_id);
    // Nos canais, o que chegou pela rádio distingue-se (lilás suave) do que
    // veio por MQTT.
    const rf = isChannel && !entry.outgoing && entry.transport === 'rf';
    const { quote, text } = quoteFor(entry, entries);
    const canReply = !entry.outgoing && entry.packet_id;
    return `${sep}
      <div class="msg ${entry.outgoing ? 'out' : ''} ${rf ? 'rf' : ''}">
        <div class="meta"><b>${escapeHtml(fromLabel)}</b><span>${transportLabel(entry)}</span>
          <span>${fmtTime(entry.time)}</span>
          ${entry.hops != null ? `<span>· ${entry.hops} hop${entry.hops === 1 ? '' : 's'}</span>` : ''}
          ${canReply ? `<button type="button" class="reply-btn" data-reply="${entry.packet_id}" title="${escapeHtml(t('Responder'))}">↩</button>` : ''}
        </div>
        ${quote ? `<div class="reply-quote">${escapeHtml(quote)}</div>` : ''}
        <div class="text">${escapeHtml(text)}</div>
        ${statusLabel(entry)}
      </div>`;
  }).join('');

  view.innerHTML = html || `<p class="muted">${escapeHtml(t('Sem mensagens nesta conversa ainda.'))}</p>`;
  view.scrollTop = view.scrollHeight;
}
