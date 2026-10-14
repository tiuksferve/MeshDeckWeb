/*
 * web/js/utils.js — Formatação e pequenas funções partilhadas entre as abas.
 */
'use strict';

import { getLanguage, t } from './i18n.js';

export function fmtTime(epochSeconds) {
  if (!epochSeconds) return '—';
  const d = new Date(epochSeconds * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString([], { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

/* Tempo decorrido em linguagem corrente ("há 5 min"), para listas onde a
   hora exacta interessa menos do que "está vivo?". A hora fica no title. */
export function fmtAgo(epochSeconds) {
  if (!epochSeconds) return '—';
  const s = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (s < 60) return t('agora');
  if (s < 3600) return t('há {n} min', { n: Math.floor(s / 60) });
  if (s < 86400) return t('há {n} h', { n: Math.floor(s / 3600) });
  return t('há {n} d', { n: Math.floor(s / 86400) });
}

/* Frescura do último contacto: <15 min, <2 h (o "activos 2h" das
   estatísticas) ou mais antigo. Dá a cor do ponto de estado. */
export function freshness(epochSeconds) {
  if (!epochSeconds) return 'stale';
  const s = Date.now() / 1000 - epochSeconds;
  if (s < 900) return 'fresh';
  if (s < 7200) return 'recent';
  return 'stale';
}

/* Cor estável por nó, para o distintivo com o nome curto: o mesmo nó tem
   sempre a mesma cor em todas as listas. */
export function nodeHue(nodeId) {
  let h = 0;
  for (const ch of nodeId || '') h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % 360;
}

export function nodeBadge(node) {
  const label = (node.short_name || '').trim() || (node.id_string || '').slice(-4);
  return `<span class="node-badge" style="--hue:${nodeHue(node.id_string)}">${escapeHtml(label)}</span>`;
}

export function fmtDistance(km) {
  if (km == null) return '—';
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${km.toFixed(km < 10 ? 2 : km < 100 ? 1 : 0)} km`;
}

export function fmtBattery(level) {
  if (level == null) return '';
  if (level > 100) return '🔌';
  return `🔋 ${level}%`;
}

export function fmtNum(value, digits = 1) {
  return (value === null || value === undefined) ? '—' : Number(value).toFixed(digits);
}

export function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* Cor por SNR, coerente com constants.py: >=5 verde, >=0 laranja, abaixo vermelho. */
export function snrClass(snr) {
  if (snr == null) return '';
  if (snr >= 5) return 'ok';
  if (snr >= 0) return 'warn';
  return 'bad';
}

export function isLive(nodeId) {
  return !!nodeId && nodeId.startsWith('!');
}

/* Distância Haversine em km e rumo inicial em graus, entre dois pontos. */
export function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

export function bearingDeg(lat1, lon1, lat2, lon2) {
  const toRad = (d) => (d * Math.PI) / 180;
  const toDeg = (r) => (r * 180) / Math.PI;
  const y = Math.sin(toRad(lon2 - lon1)) * Math.cos(toRad(lat2));
  const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2))
    - Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(toRad(lon2 - lon1));
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

export function cardinal(deg) {
  const dirs = getLanguage() === 'pt'
    ? ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO']
    : ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  return dirs[Math.round(deg / 45) % 8];
}

export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/* Modal genérico e descartável, para detalhe de um nó ou confirmações simples.
   Fecha com ✕, clique fora, ou Escape. */
export function showModal(titleHtml, bodyHtml) {
  const overlay = document.createElement('div');
  overlay.className = 'overlay modal-overlay';
  overlay.innerHTML = `
    <div class="card modal-card">
      <div class="modal-head">
        <span>${titleHtml}</span>
        <button type="button" class="ghost small modal-close">✕</button>
      </div>
      <div class="modal-body">${bodyHtml}</div>
    </div>`;
  const close = () => overlay.remove();
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  overlay.querySelector('.modal-close').addEventListener('click', close);
  document.addEventListener('keydown', function onEsc(e) {
    if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onEsc); }
  });
  document.body.appendChild(overlay);
  return overlay;
}

/* Verificação de ecrã móvel. O mesmo limite que o @media de app.css, para
   o JS e o CSS nunca discordarem sobre qual layout está activo. */
export const MOBILE_QUERY = window.matchMedia('(max-width: 760px), (pointer: coarse) and (max-height: 500px)');

export function isMobile() {
  return MOBILE_QUERY.matches;
}

/* Diálogo com botões; resolve com o value do botão escolhido, ou null se for
   fechado (✕, clique fora, Escape). bodyHtml já tem de vir escapado. */
export function showDialog(titleHtml, bodyHtml, buttons) {
  return new Promise((resolve) => {
    const overlay = showModal(titleHtml, bodyHtml);
    const row = document.createElement('div');
    row.className = 'modal-actions';
    for (const b of buttons) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = b.label;
      if (b.primary) btn.className = 'primary';
      btn.addEventListener('click', () => { overlay.remove(); resolve(b.value); });
      row.appendChild(btn);
    }
    overlay.querySelector('.modal-card').appendChild(row);
    // Fecho sem escolher: o showModal remove o overlay; detecta-se aqui.
    new MutationObserver((_m, obs) => {
      if (!overlay.isConnected) { obs.disconnect(); resolve(null); }
    }).observe(document.body, { childList: true });
    row.querySelector('.primary, button')?.focus();
  });
}
