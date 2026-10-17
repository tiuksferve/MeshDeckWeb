/*
 * web/js/tabs/navigation.js — Bússola, cartão do nó local, tabela de GPS.
 *
 * Rumo e distância do nó local ao nó seleccionado, estado do GPS local com
 * botão de refresh, e a lista de todos os nós com posição conhecida
 * ordenada por distância.
 */
'use strict';

import { store } from '../store.js';
import { commands } from '../socket.js';
import {
  escapeHtml, fmtNum, bearingDeg, cardinal, haversineKm, fmtAgo, fmtTime, freshness,
  nodeBadge, fmtDistance, snrClass, isMobile,
} from '../utils.js';
import { t } from '../i18n.js';

let onShowOnMap = null;   // (nodeId) => void, fornecido pelo app.js

export function mount(options = {}) {
  onShowOnMap = options.showOnMap;
  document.getElementById('btn-refresh-pos').addEventListener('click', () => {
    commands.refreshLocalPosition();
  });

  store.on('local-node-changed', renderLocalCard);
  store.on('local-position-changed', renderLocalCard);
  store.on('nodes-changed', () => { renderLocalCard(); renderCompass(); renderGpsTable(); });
  store.on('selection-changed', () => { renderCompass(); renderGpsTable(); });
  store.on('search-changed', renderGpsTable);
  store.on('local-position-changed', () => { renderCompass(); renderGpsTable(); });
  store.on('language-changed', renderGpsTable);
  setInterval(renderGpsTable, 60_000);   // "há 5 min" envelhece

  setupDeviceCompass();

  document.getElementById('gps-list').addEventListener('click', (event) => {
    const row = event.target.closest('li[data-id]');
    if (!row) return;
    store.setSelectedNode(row.dataset.id);
    // 📍: o mesmo que na lista de nós — abre o mapa centrado no nó.
    if (event.target.closest('[data-action="map"]')) { onShowOnMap?.(row.dataset.id); return; }
    // No telemóvel a bússola está acima da lista: sobe até ela para se ver o rumo.
    if (isMobile()) document.getElementById('compass').scrollIntoView({ behavior: 'smooth', block: 'center' });
  });

  renderLocalCard();
  renderCompass();
  renderGpsTable();
}

function localLatLon() {
  const pos = store.localPosition;
  if (pos && pos.found && pos.lat != null && pos.lon != null) return [pos.lat, pos.lon];
  return null;
}

function renderLocalCard() {
  const dl = document.getElementById('local-card');
  const ln = store.localNode || {};
  const pos = store.localPosition || {};
  const rows = [
    [t('Nome'), ln.node_id ? `${escapeHtml(ln.long_name || '—')} [${escapeHtml(ln.short_name || '')}]` : '—'],
    ['ID', escapeHtml(ln.node_id || '—')],
    ['GPS', t(ln.gps_enabled ? 'activo' : 'desactivado')],
    [t('Posição'), pos.found ? `${pos.lat.toFixed(5)}, ${pos.lon.toFixed(5)}` : t(ln.has_position ? '⏳ a ler…' : '⚠ sem posição')],
    ['Altitude', pos.alt != null ? `${pos.alt} m` : '—'],
  ];
  dl.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
}

// Centro e raios da rosa dos ventos, partilhados por todo o desenho.
const CX = 100, CY = 100;
const R_OUTER = 94, R_TICKS = 86, R_LABELS = 70, R_NEEDLE = 58, R_HUB = 5;

function polar(radiusPx, bearingDegrees) {
  const rad = (bearingDegrees - 90) * (Math.PI / 180);
  return [CX + radiusPx * Math.cos(rad), CY + radiusPx * Math.sin(rad)];
}

/* Anel graduado: traços finos a cada 10°, traços grossos a cada 30°, e as
   quatro letras cardeais em destaque — o aspecto habitual de uma bússola
   física em vez de uma simples circunferência. */
/* rotation: com a bússola do dispositivo, a rosa roda para o N real; os
   traços e letras mudam de posição, mas as letras ficam sempre direitas. */
function compassRoseSvg(rotation = 0) {
  let out = `
    <defs>
      <radialGradient id="compass-face" cx="50%" cy="42%" r="70%">
        <stop offset="0%" stop-color="#1c2230"/>
        <stop offset="100%" stop-color="#0d1117"/>
      </radialGradient>
    </defs>
    <circle cx="${CX}" cy="${CY}" r="${R_OUTER}" fill="url(#compass-face)" stroke="#30363d" stroke-width="1.5"/>
    <circle cx="${CX}" cy="${CY}" r="${R_OUTER - 10}" fill="none" stroke="#21262d" stroke-width="1"/>`;

  for (let deg = 0; deg < 360; deg += 10) {
    const major = deg % 30 === 0;
    const [x1, y1] = polar(R_TICKS, deg - rotation);
    const [x2, y2] = polar(major ? R_TICKS - 10 : R_TICKS - 5, deg - rotation);
    out += `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"
                  stroke="${major ? '#8b949e' : '#30363d'}" stroke-width="${major ? 1.5 : 1}"/>`;
  }

  const cardinals = [['N', 0, '#f85149'], ['E', 90, '#8b949e'], ['S', 180, '#8b949e'], [cardinal(270), 270, '#8b949e']];
  for (const [label, deg, color] of cardinals) {
    const [x, y] = polar(R_LABELS, deg - rotation);
    out += `<text x="${x.toFixed(1)}" y="${(y + 5).toFixed(1)}" text-anchor="middle"
                  fill="${color}" font-size="15" font-weight="700">${label}</text>`;
  }
  return out;
}

/* Agulha a duas cores — vermelha a apontar ao alvo, cinzenta a apontar para
   trás — como uma bússola física, em vez de uma linha simples. */
function needleSvg(bearingDeg_, onTarget = false) {
  const [tipX, tipY] = polar(R_NEEDLE, bearingDeg_);
  const [tailX, tailY] = polar(R_NEEDLE, bearingDeg_ + 180);
  const [wingAX, wingAY] = polar(7, bearingDeg_ + 90);
  const [wingBX, wingBY] = polar(7, bearingDeg_ - 90);
  const pt = (x, y) => `${x.toFixed(1)},${y.toFixed(1)}`;

  return `
    <polygon points="${pt(tipX, tipY)} ${pt(wingAX, wingAY)} ${pt(wingBX, wingBY)}"
             fill="${onTarget ? '#3fb950' : '#f85149'}" stroke="${onTarget ? '#238636' : '#a82a24'}" stroke-width="0.5"/>
    <polygon points="${pt(tailX, tailY)} ${pt(wingAX, wingAY)} ${pt(wingBX, wingBY)}"
             fill="#c9d1d9" stroke="#6e7681" stroke-width="0.5"/>
    <circle cx="${CX}" cy="${CY}" r="${R_HUB}" fill="#e6edf3" stroke="#30363d" stroke-width="1"/>`;
}

/* ── bússola do dispositivo ──────────────────────────────────────────────
   Com o sensor ligado, a rosa roda para o N apontar ao norte real e a agulha
   aponta para o nó em relação ao topo do telemóvel: basta rodar até a
   agulha ficar para cima. O iOS dá o rumo já pronto (webkitCompassHeading);
   o Android dá o alpha absoluto (deviceorientationabsolute). Os dois exigem
   HTTPS, e o iOS também uma autorização pedida dentro de um toque. */
let deviceHeading = null;     // graus, 0 = topo do ecrã a apontar ao norte
let sensorOn = false;
let sensorEventName = null;
let paintQueued = false;

function setupDeviceCompass() {
  // Em HTTP o browser esconde a API por completo; o botão aparece na mesma,
  // para levar ao endereço HTTPS. Em HTTPS sem a API não há sensor.
  if (window.isSecureContext && typeof window.DeviceOrientationEvent === 'undefined') return;
  const box = document.getElementById('compass-sensor');
  // Só faz sentido em telemóvel/tablet; num portátil não há magnetómetro.
  const touch = window.matchMedia('(pointer: coarse)').matches;
  box.hidden = !touch;
  document.getElementById('btn-device-compass').addEventListener('click', toggleDeviceCompass);
  store.on('language-changed', updateSensorUi);
}

function sensorNote(text, url = null) {
  const note = document.getElementById('compass-sensor-note');
  note.innerHTML = escapeHtml(text || '')
    + (url ? `<br><a href="${escapeHtml(url)}">${escapeHtml(url)}</a>` : '');
  note.hidden = !text;
}

function updateSensorUi() {
  const btn = document.getElementById('btn-device-compass');
  btn.classList.toggle('active', sensorOn);
  btn.textContent = t(sensorOn ? '🧭 Bússola do dispositivo: ligada' : '🧭 Usar bússola do dispositivo');
}

async function toggleDeviceCompass() {
  if (sensorOn) { stopSensor(); return; }
  if (!window.isSecureContext) {
    // O servidor pode ter HTTPS noutra porta: aponta directamente para lá.
    if (store.httpsPort) {
      const url = `https://${location.hostname}:${store.httpsPort}/`;
      sensorNote(t('O browser só dá acesso à bússola em páginas HTTPS. Abra o endereço seguro:'), url);
    } else {
      sensorNote(t('O browser só dá acesso à bússola em páginas HTTPS. Abra o MeshDeckWeb por https:// para usar esta função.'));
    }
    return;
  }
  // iOS 13+: a autorização tem de ser pedida aqui, dentro do toque.
  const ask = window.DeviceOrientationEvent.requestPermission;
  if (typeof ask === 'function') {
    try {
      if (await ask.call(window.DeviceOrientationEvent) !== 'granted') {
        sensorNote(t('Acesso à bússola recusado. Pode autorizar nas definições do Safari (Movimento e orientação).'));
        return;
      }
    } catch {
      sensorNote(t('Não foi possível pedir acesso à bússola.'));
      return;
    }
  }
  sensorEventName = 'ondeviceorientationabsolute' in window ? 'deviceorientationabsolute' : 'deviceorientation';
  window.addEventListener(sensorEventName, onOrientation);
  sensorOn = true;
  deviceHeading = null;
  sensorNote(t('A aguardar o sensor… rode o telemóvel em forma de 8 se a bússola não estabilizar.'));
  updateSensorUi();
  // Sem leituras ao fim de uns segundos: o dispositivo não tem bússola.
  setTimeout(() => {
    if (sensorOn && deviceHeading == null) sensorNote(t('Este dispositivo não está a enviar a orientação da bússola.'));
  }, 3000);
}

function stopSensor() {
  if (sensorEventName) window.removeEventListener(sensorEventName, onOrientation);
  sensorOn = false;
  deviceHeading = null;
  sensorNote('');
  updateSensorUi();
  renderCompass();
}

function screenAngle() {
  const angle = screen.orientation?.angle ?? window.orientation ?? 0;
  return Number(angle) || 0;
}

function onOrientation(event) {
  let heading = null;
  if (typeof event.webkitCompassHeading === 'number' && event.webkitCompassHeading >= 0) {
    heading = event.webkitCompassHeading;                 // iOS: já é o rumo
  } else if (event.absolute && event.alpha != null) {
    heading = 360 - event.alpha;                          // Android: alpha anti-horário
  }
  if (heading == null) return;
  heading = (heading + screenAngle() + 360) % 360;        // ecrã na horizontal

  // Suaviza o tremor do magnetómetro, pelo caminho mais curto do círculo.
  if (deviceHeading == null) {
    deviceHeading = heading;
    sensorNote('');
  } else {
    const diff = ((heading - deviceHeading + 540) % 360) - 180;
    deviceHeading = (deviceHeading + diff * 0.25 + 360) % 360;
  }
  if (!paintQueued) {
    paintQueued = true;
    requestAnimationFrame(() => { paintQueued = false; renderCompass(); });
  }
}

/* Marca fixa no topo (a "linha de fé"): mostra para onde o telemóvel aponta. */
function lubberSvg() {
  return `<polygon points="${CX - 7},4 ${CX + 7},4 ${CX},16" fill="#e3b341"/>`;
}

function renderCompass() {
  const svg = document.getElementById('compass');
  const info = document.getElementById('compass-info');
  const local = localLatLon();
  const target = store.nodes.get(store.selectedNodeId);
  const heading = sensorOn && deviceHeading != null ? deviceHeading : null;

  let html = heading == null ? compassRoseSvg()
    : compassRoseSvg(heading) + lubberSvg();
  const headingNote = heading == null ? ''
    : ` · ${escapeHtml(t('a apontar {deg}° ({cardinal})', { deg: heading.toFixed(0), cardinal: cardinal(heading) }))}`;

  if (!local) {
    info.textContent = t('O nó local ainda não tem posição GPS conhecida.');
  } else if (!target || target.latitude == null || target.longitude == null) {
    info.textContent = t('Selecciona um nó com GPS na lista.');
  } else {
    const bearing = bearingDeg(local[0], local[1], target.latitude, target.longitude);
    const distanceKm = haversineKm(local[0], local[1], target.latitude, target.longitude);
    // Com o sensor, a agulha é relativa ao topo do telemóvel.
    const relative = heading == null ? bearing : (bearing - heading + 360) % 360;
    const onTarget = heading != null && Math.min(relative, 360 - relative) <= 10;
    html += needleSvg(relative, onTarget);
    const snrNote = target.snr != null ? ` · SNR ${fmtNum(target.snr)} dB` : '';
    info.innerHTML = `<b>${escapeHtml(target.long_name || target.id_string)}</b> — `
      + escapeHtml(t('{dist} km, rumo {bearing}° ({cardinal})', {
        dist: distanceKm.toFixed(distanceKm < 10 ? 2 : 0), bearing: bearing.toFixed(0), cardinal: cardinal(bearing),
      })) + snrNote + headingNote
      + (onTarget ? ` <span class="on-target">${escapeHtml(t('✔ em frente'))}</span>` : '');
  }
  if (heading != null && !(local && target && target.latitude != null && target.longitude != null)) {
    info.innerHTML = escapeHtml(info.textContent) + headingNote;
  }
  svg.innerHTML = html;
}

/* Seta pequena a apontar para o rumo do nó, a partir do nó local. */
function arrowSvg(bearing) {
  return `<svg class="gps-arrow" viewBox="0 0 24 24" style="transform:rotate(${bearing.toFixed(0)}deg)">
    <path d="M12 2 L19 20 L12 16 L5 20 Z"/></svg>`;
}

function renderGpsTable() {
  const local = localLatLon();
  const rows = store.nodeList()
    .filter((n) => !n.is_local && n.latitude != null && n.longitude != null && store.matchesSearch(n))
    .map((n) => {
      const distanceKm = local ? haversineKm(local[0], local[1], n.latitude, n.longitude) : null;
      const bearing = local ? bearingDeg(local[0], local[1], n.latitude, n.longitude) : null;
      return { n, distanceKm, bearing };
    });

  // Por distância; sem posição local, pelo contacto mais recente.
  rows.sort((a, b) => (local
    ? (a.distanceKm - b.distanceKm)
    : (b.n.last_heard || 0) - (a.n.last_heard || 0)));

  document.getElementById('gps-count').textContent = rows.length || '';
  const note = document.getElementById('gps-note');
  note.hidden = !!local || rows.length === 0;
  note.textContent = t('Sem posição GPS local — distâncias indisponíveis.');

  document.getElementById('gps-list').innerHTML = rows.map(({ n, distanceKm, bearing }) => {
    const details = [
      `<span class="seen ${freshness(n.last_heard)}" title="${escapeHtml(fmtTime(n.last_heard))}">${escapeHtml(fmtAgo(n.last_heard))}</span>`,
      n.snr != null ? `<span class="snr ${snrClass(n.snr)}">SNR ${fmtNum(n.snr)}</span>` : '',
      n.hops_away != null ? `<span>${escapeHtml(n.hops_away === 0 ? t('directo') : n.hops_away === 1 ? t('1 hop') : t('{n} hops', { n: n.hops_away }))}</span>` : '',
      n.altitude != null ? `<span>⛰ ${n.altitude} m</span>` : '',
    ].filter(Boolean).join('');
    return `
      <li data-id="${escapeHtml(n.id_string)}" class="${n.id_string === store.selectedNodeId ? 'is-selected' : ''}">
        ${nodeBadge(n)}
        <div class="gps-main">
          <b>${escapeHtml(n.long_name || n.id_string)}</b>
          <div class="gps-details">${details}</div>
        </div>
        ${bearing != null ? `
        <div class="gps-dir">${arrowSvg(bearing)}
          <span>${cardinal(bearing)} ${bearing.toFixed(0)}°</span></div>` : ''}
        <div class="gps-dist">${escapeHtml(fmtDistance(distanceKm))}</div>
        <button type="button" class="node-act gps-map" data-action="map"
                title="${escapeHtml(t('Ver no mapa'))}" aria-label="${escapeHtml(t('Ver no mapa'))}">📍</button>
      </li>`;
  }).join('') || `<li class="muted gps-empty">${escapeHtml(t('Nenhum nó remoto com GPS ainda.'))}</li>`;
}
