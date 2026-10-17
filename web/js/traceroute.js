/*
 * web/js/traceroute.js — Fluxo do traceroute com as mensagens ao utilizador.
 *
 * Validação antes de enviar (ligado? outro em curso? já existe para este
 * nó?), aviso com contagem decrescente enquanto se espera a resposta, e a
 * janela "Resultado do Traceroute" com cada hop, SNR e o botão para ver a
 * rota no mapa. Um traceroute que outro nó fez ao nó local pergunta antes
 * de abrir o resultado.
 *
 * O desenho no mapa continua em tabs/map.js; aqui só se trata do diálogo.
 */
'use strict';

import { store } from './store.js';
import { commands } from './socket.js';
import { t } from './i18n.js';
import { escapeHtml, showDialog } from './utils.js';

const WAIT_S = 30;   // espera mínima entre traceroutes

let pending = null;          // { nodeId, name } do traceroute enviado por nós
let countdown = 0;
let countdownTimer = null;
let statusToast = null;
let showOnMap = null;        // (origin, dest) => void, fornecido pelo app.js
let hasTracerouteWith = null;
let drawableSegments = null;   // (forward, back) => segmentos que o mapa consegue desenhar

export function mount(options) {
  showOnMap = options.showOnMap;
  hasTracerouteWith = options.hasTracerouteWith;
  drawableSegments = options.drawableSegments;
  store.on('traceroute', onResult);
}

function nodeName(nodeId) {
  const node = store.nodes.get((nodeId || '').toLowerCase()) || store.nodes.get(nodeId);
  if (!node) return nodeId;
  const ln = (node.long_name || '').trim();
  const sn = (node.short_name || '').trim();
  return ln ? `${ln} [${sn}]` : nodeId;
}

function hasCoords(nodeId) {
  const node = store.nodes.get((nodeId || '').toLowerCase()) || store.nodes.get(nodeId);
  return !!(node && node.latitude != null && node.longitude != null);
}

function info(title, text) {
  return showDialog(escapeHtml(title), `<p class="modal-text">${escapeHtml(text)}</p>`,
    [{ label: 'OK', value: true, primary: true }]);
}

/* Ponto de entrada: botão 📡 da lista de nós e do popup do mapa. */
export async function requestTraceroute(nodeId) {
  const name = nodeName(nodeId);

  if (!store.connection.connected) {
    await info(t('Desconectado'), t('Conecte-se primeiro para enviar traceroute.'));
    return;
  }
  if (countdown > 0) {
    await info(t('Traceroute em curso'),
      t('Aguarde {n}s até o traceroute anterior terminar.', { n: countdown }));
    return;
  }
  if (hasTracerouteWith?.(store.localNode?.node_id, nodeId)) {
    const ok = await showDialog(escapeHtml(t('Traceroute já existente')),
      `<p class="modal-text">${escapeHtml(t('traceroute_existente_msg', { name }))}</p>`,
      [{ label: t('Cancelar'), value: false }, { label: t('Enviar'), value: true, primary: true }]);
    if (!ok) return;
  }

  pending = { nodeId, name };
  commands.traceroute(nodeId);
  startCountdown(t('📡 Traceroute enviado para {name} — aguardando resposta…', { name }));
}

/* Aviso no canto com os segundos que faltam. Desaparece ao chegar a zero ou quando chega a resposta. */
function startCountdown(text, seconds = WAIT_S) {
  stopCountdown();
  countdown = seconds;
  statusToast = document.createElement('div');
  statusToast.className = 'toast info';
  document.getElementById('toasts').appendChild(statusToast);
  const paint = () => { statusToast.textContent = `${text} (${countdown}s)`; };
  paint();
  countdownTimer = setInterval(() => {
    countdown -= 1;
    if (countdown <= 0) stopCountdown();
    else paint();
  }, 1000);
}

function stopCountdown() {
  clearInterval(countdownTimer);
  countdownTimer = null;
  countdown = 0;
  statusToast?.remove();
  statusToast = null;
}

function flash(text) {
  const el = document.createElement('div');
  el.className = 'toast info';
  el.textContent = text;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

/* origin = quem fez o pedido, dest = quem respondeu (como no protocolo).
   É "nosso" se a origem é o nó local e o destino o nó pedido. */
async function onResult(payload) {
  const { origin, dest } = payload;
  const forward = payload.forward || [];
  const back = payload.back || [];
  const myId = (store.localNode?.node_id || '').toLowerCase();

  const isMine = !!(pending && myId && (origin || '').toLowerCase() === myId
    && (dest || '').toLowerCase() === pending.nodeId.toLowerCase());
  if (isMine) {
    pending = null;
    stopCountdown();
  } else {
    // Traceroute que um nó remoto fez ao nó local: perguntar primeiro.
    const body = t('Foi recebido um traceroute direcionado ao nó local:\n\n')
      + `  ${t('Origem:')}  ${nodeName(dest)}\n`
      + `  ${t('Destino:')} ${nodeName(origin)}\n\n`
      + t('Deseja visualizar o resultado?');
    const ok = await showDialog(escapeHtml(t('Traceroute de terceiro recebido')),
      `<p class="modal-text">${escapeHtml(body)}</p>`,
      [{ label: t('Fechar'), value: false }, { label: 'OK', value: true, primary: true }]);
    if (!ok) return;
  }

  flash(t('📡 Traceroute: {origin} → {dest}  ({n} links)', {
    origin: nodeName(origin), dest: nodeName(dest), n: forward.length + back.length,
  }));

  if (!forward.length && !back.length) {
    await info('Traceroute', t('Resposta recebida mas sem rota.'));
    return;
  }
  showResult({ origin, dest, forward, back, isMine });
}

function showResult({ origin, dest, forward, back, isMine }) {
  // Recebido de outro nó: a origem apresentada é o remoto, e as rotas trocam
  // (a "ida" do protocolo é a "volta" do ponto de vista de quem pediu).
  const nameA = nodeName(isMine ? origin : dest);
  const nameB = nodeName(isMine ? dest : origin);
  const shownFwd = isMine ? forward : back;
  const shownBck = isMine ? back : forward;

  const section = (edges, label) => {
    if (!edges.length) return [];
    return [`  ${label}:`, ...edges.map((e, i) => {
      const snr = e.snr ? `${e.snr > 0 ? '+' : ''}${Number(e.snr).toFixed(1)} dB` : t('SNR desconhecido');
      const ca = hasCoords(e.from) ? '📍' : '❓';
      const cb = hasCoords(e.to) ? '📍' : '❓';
      return `    ${t('Hop')} ${i + 1}/${edges.length}: ${ca}${nodeName(e.from)}  →  ${cb}${nodeName(e.to)}  [${snr}]`;
    })];
  };
  const fwdLabel = `${t('Rota de ida')}  (${t('origem')} → ${t('destino')})`;
  const bckLabel = `${t('Rota de volta')} (${t('destino')} → ${t('origem')})`;

  const text = [
    `${t('Origem:')}     ${nameA}`,
    `${t('Destino:')}    ${nameB}`,
    t('Hops ida:   {n}', { n: shownFwd.length }),
    t('Hops volta: {n}', { n: shownBck.length }),
    '',
    ...section(shownFwd, fwdLabel),
    ...section(shownBck, bckLabel),
  ].join('\n');

  // Só se oferece o mapa se houver pelo menos um segmento desenhável (dois
  // nós com posição na mesma rota), com o mesmo cálculo que o mapa usa.
  const canShowMap = (drawableSegments?.(forward, back) || []).length > 0;

  const body = `
    <div class="tr-title">🔍 ${escapeHtml(nameA)} → ${escapeHtml(nameB)}</div>
    <div class="tr-legend">${escapeHtml(t('📍 com localização  ❓ sem localização'))}</div>
    <div class="tr-result mono">${escapeHtml(text)}</div>
    ${canShowMap ? '' : `<p class="muted" style="margin-top:8px">${escapeHtml(t('⚠ Menos de dois nós da rota têm localização — não há o que desenhar no mapa.'))}</p>`}`;

  const buttons = [{ label: t('Fechar'), value: false }];
  if (canShowMap) buttons.push({ label: t('🗺  Mostrar no Mapa'), value: true, primary: true });

  showDialog(escapeHtml(t('Resultado do Traceroute')), body, buttons).then((choice) => {
    if (choice) showOnMap?.(origin, dest);
  });
}
