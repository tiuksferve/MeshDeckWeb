/*
 * web/js/tabs/map.js — Aba do mapa (Leaflet).
 *
 * Cada nó é uma etiqueta com o nome curto, na cor do estado (seleccionado,
 * activo agora, inactivo, via MQTT, via RF); nós sobrepostos juntam-se num
 * círculo com o número (Leaflet.markercluster), traceroutes a verde com SNR por segmento, vizinhança
 * NeighborInfo a roxo tracejado, e os temas Escuro, Claro e Satélite. Escuro
 * e Claro passam pela cache do servidor (funcionam offline nas zonas já
 * vistas); o Satélite vai directamente à Esri e precisa de internet.
 */
'use strict';

import { store } from '../store.js';
import { requestTraceroute } from '../traceroute.js';
import { dateLocale, t } from '../i18n.js';
import { escapeHtml, fmtNum, fmtAgo, isMobile, showDialog } from '../utils.js';

// As camadas CartoDB (dark_all/light_all) que o MeshDeck 1.x usava passaram a
// exigir uma chave de API (devolvem 200 OK com uma imagem "API KEY REQUIRED"
// em vez de erro HTTP, por isso o mapa parecia só não aparecer). Reaproveita-se
// o OpenStreetMap, livre, para os dois: "Claro" é OSM tal e qual, "Escuro"
// é o mesmo OSM com um filtro CSS a inverter as cores — uma técnica comum
// para mapas escuros sem depender de um provedor pago. (O antigo tema
// "🗺 OpenStreetMap" ficaria idêntico a "☀ Claro", por isso foi retirado.)
// Claro e Escuro vêm do servidor (server/tile_cache.py), que guarda cada
// mosaico visto: sem internet, as zonas já vistas continuam a aparecer.
const TILE_URLS = {
  dark:      { url: '/tiles/osm/{z}/{x}/{y}.png', attribution: '© OpenStreetMap', filter: true },
  light:     { url: '/tiles/osm/{z}/{x}/{y}.png', attribution: '© OpenStreetMap', filter: false },
  // Satélite "híbrido": a imagem da Esri sozinha não tem nomes nem estradas,
  // por isso sobrepõem-se as camadas de referência (transparentes, também
  // livres e sem chave) com estradas e com localidades/fronteiras.
  satellite: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: '© Esri', filter: false,
    overlays: [
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}',
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    ],
  },
};

const ONLINE_WINDOW_S = 2 * 3600;

let map = null;
let tileLayer = null;
let overlayLayers = [];        // nomes/estradas por cima do satélite
let markerLayer = null;        // L.markerClusterGroup
const markers = new Map();          // id_string -> L.marker com etiqueta
let traceLayer = null;
let neighborLayer = null;
let initialized = false;
let pendingCenterId = null;    // selecção feita com o mapa escondido
let lastActiveId = '';
let theme = 'light';

const neighborLinks = new Map();     // from_id -> [{id, snr}]
const traceroutes = [];              // {id, forward, back, origin, dest, time, visible}
let traceSeq = 0;

export function mount() {
  document.getElementById('map-themes').addEventListener('click', (event) => {
    const btn = event.target.closest('.theme-btn');
    if (!btn) return;
    setTheme(btn.dataset.theme);
  });
  // Telemóvel: o painel lateral é uma folha sobre o mapa (ver app.css).
  const layout = document.querySelector('#panel-map .map-layout');
  document.getElementById('map-side-toggle').addEventListener('click', () => layout.classList.add('side-open'));
  document.getElementById('map-side-close').addEventListener('click', closeSide);
  document.getElementById('tr-list').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-tr-focus]');
    if (!btn) return;
    const tr = traceroutes.find((x) => x.id === Number(btn.dataset.trFocus));
    if (tr) focusTraceroute(tr.origin, tr.dest);
  });
  document.getElementById('tile-cache-clear').addEventListener('click', clearTileCache);
  document.getElementById('tr-clear').addEventListener('click', () => {
    traceroutes.length = 0;
    renderTraceList();
    drawTraceroutes();
  });

  store.on('nodes-changed', drawMarkers);
  store.on('language-changed', renderTraceList);
  setInterval(renderTraceList, 60_000);   // "há 5 min" envelhece
  store.on('selection-changed', onSelectionChanged);
  store.on('packet', onPacket);
  store.on('neighbors', onNeighbors);
  store.on('traceroute', onTraceroute);
}

/* Chamado pelo app.js sempre que a aba do mapa fica visível — o Leaflet
   precisa de medir um contentor já visível, senão os tiles ficam cortados. */
export function onShow() {
  if (!initialized) initMap();
  else map.invalidateSize();
  refreshTileCacheInfo();
  // Uma selecção feita com o mapa escondido não pôde centrar (o Leaflet
  // mede um contentor de tamanho zero); aplica-se agora que é visível.
  if (pendingCenterId) {
    showNode(pendingCenterId);
    pendingCenterId = null;
  }
}

/* Acção 📍 da lista de nós: centra, traz o marcador para a frente e abre o
   popup com o detalhe, para o nó ficar inequivocamente destacado. Chamado
   depois de a aba do mapa estar visível. */
export function focusNode(nodeId) {
  if (!initialized) initMap();
  map.invalidateSize();
  pendingCenterId = null;
  drawMarkers();
  if (!centerOn(nodeId)) return;
  const marker = markers.get(nodeId);
  // Dentro de um grupo, o marcador não está no mapa: aproxima ou abre o
  // grupo até ele aparecer, e só então abre o popup.
  if (marker) markerLayer.zoomToShowLayer(marker, () => marker.openPopup());
}

/* Centra e, se o nó estiver dentro de um grupo, abre-o até ele aparecer. */
function showNode(nodeId) {
  if (!centerOn(nodeId)) return;
  const marker = markers.get(nodeId);
  if (marker) markerLayer.zoomToShowLayer(marker, () => {});
}

function centerOn(nodeId) {
  const node = store.nodes.get(nodeId);
  if (!node || node.latitude == null || node.longitude == null) return false;
  map.setView([node.latitude, node.longitude], Math.max(map.getZoom(), 13), { animate: false });
  return true;
}

function mapVisible() {
  return initialized && map.getContainer().offsetWidth > 0;
}

function initMap() {
  const nodesLoc = store.nodeList().filter((n) => n.latitude != null && n.longitude != null);
  let center = [0, 0];
  let zoom = 2;
  if (nodesLoc.length) {
    center = [
      nodesLoc.reduce((s, n) => s + n.latitude, 0) / nodesLoc.length,
      nodesLoc.reduce((s, n) => s + n.longitude, 0) / nodesLoc.length,
    ];
    zoom = 8;
  }

  map = L.map('map-canvas', { zoomControl: true }).setView(center, zoom);
  setTileLayer(theme);
  markerLayer = L.markerClusterGroup({
    // Do tamanho da área de toque: dois nós mais perto do que isso tapavam a
    // área um do outro (tocar num podia abrir o vizinho), por isso juntam-se.
    maxClusterRadius: HIT_W,
    showCoverageOnHover: false,
    spiderfyOnMaxZoom: true,        // nós na mesma posição abrem em leque
    iconCreateFunction: clusterIcon,
  }).addTo(map);
  map.getContainer().addEventListener('click', (event) => {
    const btn = event.target.closest('.popup-traceroute');
    if (btn) requestTraceroute(btn.dataset.id);
  });
  traceLayer = L.layerGroup().addTo(map);
  neighborLayer = L.layerGroup().addTo(map);
  initialized = true;
  drawMarkers();
  drawNeighborLines();
}

function setTileLayer(name) {
  const def = TILE_URLS[name] || TILE_URLS.dark;
  if (tileLayer) map.removeLayer(tileLayer);
  overlayLayers.forEach((layer) => map.removeLayer(layer));
  tileLayer = L.tileLayer(def.url, { attribution: def.attribution, maxZoom: 19 }).addTo(map);
  // Depois de carregar os mosaicos visíveis, a linha "Mapa offline" mostra o novo total.
  tileLayer.on('load', refreshTileCacheInfo);
  overlayLayers = (def.overlays || []).map((url) => L.tileLayer(url, { maxZoom: 19 }).addTo(map));
  map.getContainer().classList.toggle('theme-dark-filter', !!def.filter);
}

function setTheme(name) {
  theme = name;
  document.querySelectorAll('.theme-btn').forEach((b) => b.classList.toggle('active', b.dataset.theme === name));
  if (initialized) setTileLayer(name);
}

/* ── marcadores ───────────────────────────────────────────────────────── */

// Cores dos marcadores; a legenda do pop-up usa as mesmas. `key` dá a classe
// da etiqueta (map-node-<key>), que define o tamanho e o texto de cada estado.
const MARKER_STATES = {
  selected: { key: 'selected', color: '#00cc66', fill: '#00ff88', label: 'Seleccionado' },
  active:   { key: 'active',   color: '#cc0000', fill: '#ff4444', label: 'Pacote recebido' },
  inactive: { key: 'inactive', color: '#444455', fill: '#555566', label: 'Inactivo (>2h)' },
  mqtt:     { key: 'mqtt',     color: '#cc6600', fill: '#ff8800', label: 'MQTT' },
  rf:       { key: 'rf',       color: '#1050aa', fill: '#2b7cd3', label: 'RF activo' },
};

// A caixa do marcador é maior do que a etiqueta e transparente: é a área que
// apanha o toque. 44 px é o mínimo confortável para um dedo; os antigos
// pontos tinham 10 a 18 px e no telemóvel quase nunca se acertava neles.
const HIT_W = 56;
const HIT_H = 44;

function markerLabel(node) {
  return node.short_name || node.id_string.replace(/^!/, '').slice(-4);
}

function markerIcon(node, style) {
  return L.divIcon({
    className: 'map-node',
    iconSize: [HIT_W, HIT_H],
    iconAnchor: [HIT_W / 2, HIT_H / 2],
    html: `<span class="map-node-pill map-node-${style.key}"
                 style="background:${style.fill};border-color:${style.color}">${escapeHtml(markerLabel(node))}</span>`,
  });
}

/* Grupo de nós sobrepostos: círculo com o número, na cor do "melhor" estado
   lá dentro — verde se tem o seleccionado, vermelho se tem o que acabou de
   falar, azul se tem algum activo, cinzento se estão todos inactivos. A
   caixa transparente à volta é, como nos nós, a área de toque. */
const CLUSTER_ORDER = ['selected', 'active', 'rf', 'mqtt', 'inactive'];

function clusterIcon(cluster) {
  const children = cluster.getAllChildMarkers();
  const keys = new Set(children.map((m) => m.stateKey));
  const key = CLUSTER_ORDER.find((k) => keys.has(k)) || 'inactive';
  const style = MARKER_STATES[key];
  const n = children.length;
  const size = n < 10 ? 'small' : n < 50 ? 'medium' : 'large';
  return L.divIcon({
    className: 'map-node map-cluster',
    iconSize: [HIT_H, HIT_H],
    html: `<span class="map-cluster-dot map-cluster-${size} map-cluster-${key}" data-count="${n}"
                 style="background:${style.fill};border-color:${style.color}">${n}</span>`,
  });
}

// Ordem de sobreposição: o seleccionado por cima de tudo, depois o nó que
// acabou de falar; os inactivos ficam por baixo dos outros.
const Z_OFFSET = { selected: 3000, active: 2000, rf: 1000, mqtt: 1000, inactive: 0 };

/* Estado do nó sem contar com a selecção: é a cor que o ponto tem quando
   não está seleccionado, e a que o popup explica. */
function baseState(node) {
  const isActiveNow = node.id_string.toLowerCase() === lastActiveId;
  const isInactive = node.last_heard == null || (Date.now() / 1000 - node.last_heard) > ONLINE_WINDOW_S;
  if (isActiveNow) return MARKER_STATES.active;
  if (isInactive) return MARKER_STATES.inactive;
  if (node.via_mqtt === true) return MARKER_STATES.mqtt;
  return MARKER_STATES.rf;
}

function markerStyle(node) {
  return node.id_string === store.selectedNodeId ? MARKER_STATES.selected : baseState(node);
}

function legendDot(state) {
  return `<span class="map-dot" style="background:${state.fill};border-color:${state.color}"></span>`;
}

function popupHtml(node) {
  const isSelected = node.id_string === store.selectedNodeId;
  const state = baseState(node);
  const isFav = store.favorites.has(node.id_string);
  const via = node.via_mqtt === true ? '☁ MQTT' : 'RF';
  const name = node.long_name || node.id_string;
  const lh = node.last_heard
    ? new Date(node.last_heard * 1000).toLocaleString(dateLocale(), {
        hour: '2-digit', minute: '2-digit', second: '2-digit', day: '2-digit', month: '2-digit',
      })
    : '—';
  return `
    <div class="mono" style="font-size:12px;min-width:200px">
      <b>${isSelected ? '★ ' : ''}${isFav ? '⭐ ' : ''}${escapeHtml(name)}</b>
      <hr style="margin:4px 0;border-color:#30363d">
      <table style="border-spacing:2px;width:100%">
        <tr><td>🆔</td><td>${escapeHtml(node.id_string)}</td></tr>
        <tr><td>📡</td><td>via <b>${via}</b></td></tr>
        <tr><td>📶</td><td>SNR ${fmtNum(node.snr)} dB</td></tr>
        <tr><td>🔋</td><td>${node.battery_level ?? '—'}%</td></tr>
        <tr><td>💬</td><td>DM ${node.public_key ? 'PKI' : 'PSK'}</td></tr>
        <tr><td>🕐</td><td>${lh}</td></tr>
      </table>
      <div class="map-legend-row">${legendDot(state)} ${escapeHtml(t(state.label))}
        ${isSelected ? `<span class="map-legend-sel">${legendDot(MARKER_STATES.selected)} ${escapeHtml(t('Seleccionado'))}</span>` : ''}</div>
      <div style="margin-top:8px;text-align:center">
        <button class="popup-traceroute" data-id="${escapeHtml(node.id_string)}"
                style="color:#58a6ff;background:none;border:1px solid #58a6ff;border-radius:4px;
                       padding:2px 8px;font-size:11px;cursor:pointer">📡 Traceroute</button>
      </div>
    </div>`;
}

/* Os marcadores são criados uma vez e depois actualizados no lugar. Apagar e
   recriar todos a cada pacote ou selecção fechava o popup no instante em
   que abria: clicar num nó selecciona-o, a selecção redesenha, e o popup
   desaparecia com o marcador antigo. */
function tooltipText(node) {
  return `${store.favorites.has(node.id_string) ? '⭐ ' : ''}${node.short_name || node.id_string} · ${
    node.via_mqtt === true ? '☁ MQTT' : 'RF'} · SNR ${fmtNum(node.snr)}`;
}

function drawMarkers() {
  if (!initialized) return;
  const seen = new Set();
  const changed = [];
  const nodesLoc = store.nodeList().filter((n) => n.latitude != null && n.longitude != null);
  for (const node of nodesLoc) {
    const id = node.id_string;
    const style = markerStyle(node);
    const latLng = [node.latitude, node.longitude];
    seen.add(id);

    let marker = markers.get(id);
    if (!marker) {
      // Já com a etiqueta e o estado: o grupo calcula a cor ao receber o marcador.
      marker = L.marker(latLng, { keyboard: false, icon: markerIcon(node, style), zIndexOffset: Z_OFFSET[style.key] })
        .bindTooltip('', { direction: 'top', offset: L.point(0, -12) })
        .bindPopup('', { offset: L.point(0, -8) });
      marker.on('click', () => store.setSelectedNode(id));
      // A dica repete o que o popup mostra e ficava por trás dele.
      marker.on('popupopen', () => marker.closeTooltip());
      marker.iconKey = `${style.key}|${markerLabel(node)}`;
      marker.stateKey = style.key;
      markerLayer.addLayer(marker);
      markers.set(id, marker);
    } else if (!marker.getLatLng().equals(latLng)) {
      marker.setLatLng(latLng);
    }
    // Trocar o ícone recria o elemento; só se faz quando a etiqueta muda,
    // não a cada pacote.
    const icon = `${style.key}|${markerLabel(node)}`;
    if (marker.iconKey !== icon) {
      marker.setIcon(markerIcon(node, style));
      marker.setZIndexOffset(Z_OFFSET[style.key]);
      marker.iconKey = icon;
      // A cor do grupo depende do estado dos nós lá dentro.
      if (marker.stateKey !== style.key) changed.push(marker);
      marker.stateKey = style.key;
    }
    marker.setTooltipContent(tooltipText(node));
    // Só se reescreve o popup quando o conteúdo muda, para não o redesenhar
    // por baixo do utilizador a cada pacote.
    const html = popupHtml(node);
    if (marker.popupHtml !== html) {
      marker.setPopupContent(html);
      marker.popupHtml = html;
    }
  }
  for (const [id, marker] of markers) {
    if (!seen.has(id)) { markerLayer.removeLayer(marker); markers.delete(id); }
  }
  if (changed.length) markerLayer.refreshClusters(changed);
  drawNeighborLines();
}

function onSelectionChanged(nodeId) {
  if (!initialized) { pendingCenterId = nodeId; return; }
  drawMarkers();
  if (mapVisible()) showNode(nodeId);
  else pendingCenterId = nodeId;
}

function onPacket(payload) {
  if (!payload.from_id) return;
  lastActiveId = payload.from_id.toLowerCase();
  drawMarkers();
}

/* ── vizinhança ───────────────────────────────────────────────────────── */

function onNeighbors(payload) {
  if (payload.from_id) neighborLinks.set(payload.from_id.toLowerCase(), payload.neighbors || []);
  drawNeighborLines();
}

function drawNeighborLines() {
  if (!initialized) return;
  neighborLayer.clearLayers();
  for (const [fromId, neighbors] of neighborLinks) {
    const from = store.nodes.get(fromId);
    if (!from || from.latitude == null) continue;
    for (const nb of neighbors) {
      const to = store.nodes.get((nb.id || '').toLowerCase());
      if (!to || to.latitude == null) continue;
      L.polyline([[from.latitude, from.longitude], [to.latitude, to.longitude]], {
        color: '#bc8cff', weight: 1.5, dashArray: '4 4', opacity: .7,
      }).bindTooltip(`SNR ${fmtNum(nb.snr)} dB`).addTo(neighborLayer);
    }
  }
}

/* ── traceroutes ──────────────────────────────────────────────────────── */

/* Linha "Mapa offline: 23 MB de 200 MB" no painel, com o botão de limpar. */
async function refreshTileCacheInfo() {
  const info = document.getElementById('tile-cache-info');
  try {
    const res = await fetch('/api/tiles', { credentials: 'same-origin' });
    if (!res.ok) return;
    renderTileCacheInfo(await res.json());
  } catch {
    info.textContent = '';
  }
}

function renderTileCacheInfo(s) {
  const mb = (b) => (b / 1048576).toFixed(b < 10485760 ? 1 : 0);
  const info = document.getElementById('tile-cache-info');
  info.textContent = s.limit_bytes
    ? t('{used} MB de {limit} MB · {n} mosaicos', { used: mb(s.bytes), limit: mb(s.limit_bytes), n: s.tiles })
    : t('desactivado');
  document.getElementById('tile-cache-offline').hidden = !s.offline;
}

async function clearTileCache() {
  const ok = await showDialog(escapeHtml(t('Limpar mapa offline')),
    `<p class="modal-text">${escapeHtml(t('Apagar os mosaicos guardados? Sem internet, o mapa fica sem fundo até voltar a ver essas zonas com ligação.'))}</p>`,
    [{ label: t('Cancelar'), value: false }, { label: t('Limpar'), value: true, primary: true }]);
  if (!ok) return;
  const res = await fetch('/api/tiles/clear', { method: 'POST', credentials: 'same-origin' });
  if (res.ok) renderTileCacheInfo(await res.json());
}

function closeSide() {
  document.querySelector('#panel-map .map-layout').classList.remove('side-open');
}

function onTraceroute(payload) {
  traceroutes.unshift({ id: ++traceSeq, ...payload, visible: true, received: Date.now() / 1000 });
  if (traceroutes.length > 20) traceroutes.length = 20;
  renderTraceList();
  drawTraceroutes();
}

function renderTraceList() {
  const list = document.getElementById('tr-list');
  const focusTitle = escapeHtml(t('Enquadrar rota no mapa'));
  list.innerHTML = traceroutes.map((tr) => {
    const hops = (tr.forward || []).length + (tr.back || []).length;
    const drawable = tracerouteSegments(tr.forward || [], tr.back || []).length > 0;
    return `
    <li data-id="${tr.id}">
      <label class="tr-item">
        <input type="checkbox" data-tr-toggle="${tr.id}" ${tr.visible ? 'checked' : ''}>
        <span style="min-width:0">
          <span class="mono tr-route" title="${escapeHtml(tr.origin)} → ${escapeHtml(tr.dest)}">${escapeHtml(nodeShort(tr.origin))} → ${escapeHtml(nodeShort(tr.dest))}</span>
          <span class="tr-meta">${escapeHtml(fmtAgo(tr.received))} · ${escapeHtml((hops === 1 ? t('1 link') : t('{n} links', { n: hops })))}</span>
        </span>
      </label>
      <button type="button" class="ghost small tr-focus" data-tr-focus="${tr.id}"
              title="${focusTitle}" aria-label="${focusTitle}" ${drawable ? '' : 'disabled'}>🔍</button>
    </li>`;
  }).join('') || `<li class="muted">${escapeHtml(t('Sem traceroutes ainda.'))}</li>`;

  const count = document.getElementById('tr-count');
  count.textContent = traceroutes.length;
  count.hidden = !traceroutes.length;

  list.querySelectorAll('[data-tr-toggle]').forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      const t = traceroutes.find((x) => x.id === Number(checkbox.dataset.trToggle));
      if (t) { t.visible = checkbox.checked; drawTraceroutes(); }
    });
  });
}

/* Para o aviso "traceroute já existente": há algum na lista entre o nó
   local e este nó, em qualquer sentido? */
export function hasTracerouteWith(localId, nodeId) {
  const a = (localId || '').toLowerCase();
  const b = (nodeId || '').toLowerCase();
  if (!a || !b) return false;
  return traceroutes.some((tr) => {
    const o = (tr.origin || '').toLowerCase();
    const d = (tr.dest || '').toLowerCase();
    return (o === a && d === b) || (o === b && d === a);
  });
}

/* Botão "Mostrar no Mapa" do resultado: torna a rota visível e enquadra-a.
   Chamado com a aba do mapa já visível. */
export function focusTraceroute(origin, dest) {
  if (!initialized) initMap();
  // No telemóvel a folha taparia metade da rota: fecha-se para a ver inteira.
  if (isMobile()) closeSide();
  map.invalidateSize();
  const tr = traceroutes.find((x) => x.origin === origin && x.dest === dest);
  if (!tr) return;
  tr.visible = true;
  renderTraceList();
  drawTraceroutes();
  const points = tracerouteSegments(tr.forward, tr.back).flatMap((s) => [s.a, s.b]);
  if (points.length) map.fitBounds(points, { padding: [40, 40], maxZoom: 15, animate: false });
}

/* Posição de um nó para o traceroute. O nó local pode não ter posição na
   NodeDB mas ter a lida do próprio GPS (store.localPosition). */
function edgeLatLng(nodeId) {
  const key = (nodeId || '').toLowerCase();
  if (!key) return null;
  const node = store.nodes.get(key) || store.nodes.get(nodeId)
    || store.nodeList().find((n) => (n.id_string || '').toLowerCase() === key);
  if (node && node.latitude != null && node.longitude != null) return [node.latitude, node.longitude];
  if (key === (store.localNode?.node_id || '').toLowerCase()) {
    const pos = store.localPosition || {};
    if (pos.found && pos.lat != null && pos.lon != null) return [pos.lat, pos.lon];
  }
  return null;
}

/* Segmentos desenháveis de um traceroute: em cada sentido a rota é uma cadeia de nós; os que não têm GPS
   (muitas vezes os relays) são saltados e liga-se directamente o nó com
   posição seguinte. Sem isto, uma rota com um relay sem GPS não desenhava
   nada. skipped lista os hops saltados, para o tooltip. */
export function tracerouteSegments(forward, back) {
  const out = [];
  for (const [direction, edges] of [['ida', forward || []], ['volta', back || []]]) {
    if (!edges.length) continue;
    const chain = [{ id: edges[0].from, snr: null }, ...edges.map((e) => ({ id: e.to, snr: e.snr }))];
    const located = chain.map((c, i) => ({ ...c, i, pos: edgeLatLng(c.id) })).filter((c) => c.pos);
    for (let k = 0; k < located.length - 1; k += 1) {
      const from = located[k];
      const to = located[k + 1];
      out.push({
        direction, a: from.pos, b: to.pos, from: from.id, to: to.id, snr: to.snr,
        skipped: chain.slice(from.i + 1, to.i).map((c) => c.id),
      });
    }
  }
  return out;
}

function nodeShort(nodeId) {
  const node = store.nodes.get((nodeId || '').toLowerCase());
  return node?.short_name || node?.long_name || nodeId;
}

function drawTraceroutes() {
  if (!initialized) return;
  traceLayer.clearLayers();
  for (const tr of traceroutes) {
    if (!tr.visible) continue;
    for (const seg of tracerouteSegments(tr.forward, tr.back)) {
      const outbound = seg.direction === 'ida';
      const jumped = seg.skipped.length > 0;
      let tip = `${outbound ? t('Traceroute ida') : t('Traceroute volta')} · `
        + `${nodeShort(seg.from)} → ${nodeShort(seg.to)}`;
      if (seg.snr) tip += ` · SNR ${fmtNum(seg.snr)} dB`;
      if (jumped) tip += `<br>${t('Hops sem GPS')}: ${seg.skipped.map(nodeShort).join(', ')}`;
      L.polyline([seg.a, seg.b], {
        color: outbound ? '#39d353' : '#1f8a3a',
        weight: outbound ? 3.5 : 3,
        opacity: .9,
        dashArray: jumped ? '8 6' : null,
      }).bindTooltip(tip).addTo(traceLayer);
    }
  }
}
