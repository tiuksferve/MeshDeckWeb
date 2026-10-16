/*
 * web/js/store.js — Estado partilhado entre as abas.
 *
 * Um EventTarget nativo serve de pub/sub: cada aba subscreve os tópicos que
 * lhe interessam em vez de o app.js ter de conhecer todas as abas. O estado
 * em si são colecções simples (Map/Array), espelhando o que o servidor manda;
 * não há reconciliação complexa porque o servidor já entrega a forma final.
 */
'use strict';

import { setSnrLevels } from './utils.js';

class Store extends EventTarget {
  constructor() {
    super();
    this.nodes = new Map();          // id_string -> registo (forma estável do servidor)
    this.counts = { total: 0, contacted: 0, online: 0 };
    this.channels = [];              // [{index, name, has_psk}]
    this.localNode = {};             // {long_name, short_name, node_id, gps_enabled, has_position}
    this.localPosition = {};         // {lat, lon, alt, found}
    this.connection = { connected: false, target: {}, bridge: {}, node_ready: false };
    this.reconnect = { attempt: 0, delay_s: 0 };
    this.packetStats = { snr: null, packets_per_min: 0 };
    this.metricSections = [];        // [{key, label}]
    this.favorites = new Set();
    this.configLock = { held_by: null };

    // Mensagens: conversations[key] = [entry, ...], chave "ch:<n>" ou "dm:<id>"
    this.conversations = new Map();
    this.unread = new Map();
    this.dmPartners = [];

    this.selectedNodeId = null;      // selecção partilhada entre Nós/Mapa/Navegação
    this.searchText = '';
  }

  emit(topic, detail) {
    this.dispatchEvent(new CustomEvent(topic, { detail }));
  }

  on(topic, handler) {
    this.addEventListener(topic, (event) => handler(event.detail));
  }

  /* ── aplicação do snapshot inicial ───────────────────────────────────── */
  applySnapshot(snapshot) {
    this.nodes.clear();
    (snapshot.nodes || []).forEach((node) => this.nodes.set(node.id_string, node));
    this.counts = snapshot.counts || this.counts;
    this.channels = snapshot.channels || [];
    this.localNode = snapshot.local_node || {};
    this.localPosition = snapshot.local_position || {};
    this.connection = snapshot.connection || this.connection;
    this.reconnect = snapshot.reconnect || this.reconnect;
    this.packetStats = snapshot.packet_stats || this.packetStats;
    this.metricSections = snapshot.metric_sections || [];
    this.favorites = new Set(snapshot.favorites || []);
    this.configLock = snapshot.config_lock || this.configLock;
    setSnrLevels(snapshot.snr_levels);

    this.conversations.clear();
    const conv = (snapshot.messages && snapshot.messages.conversations) || {};
    Object.entries(conv).forEach(([key, list]) => this.conversations.set(key, list));
    this.unread = new Map(Object.entries((snapshot.messages && snapshot.messages.unread) || {}));
    this.dmPartners = (snapshot.messages && snapshot.messages.dm_partners) || [];

    this.emit('snapshot', snapshot);
    this.emit('nodes-changed');
    this.emit('channels-changed');
    this.emit('connection-changed');
    this.emit('messages-changed');
    this.emit('local-node-changed');
    this.emit('local-position-changed');
  }

  /* ── eventos incrementais do socket ──────────────────────────────────── */
  applySnrLevels(levels) {
    setSnrLevels(levels);
    this.emit('nodes-changed');          // redesenha as cores do SNR
  }

  applyNodes(payload) {
    (payload.nodes || []).forEach((node) => this.nodes.set(node.id_string, node));
    if (payload.counts) this.counts = payload.counts;
    this.emit('nodes-changed');
  }

  applyNodedbReset(payload) {
    this.nodes.clear();
    if (payload.counts) this.counts = payload.counts;
    this.emit('nodes-changed');
  }

  applyConnection(payload) {
    this.connection = payload;
    this.emit('connection-changed');
  }

  applyReconnect(payload) {
    this.reconnect = payload;
    this.emit('connection-changed');
  }

  applyLocalNode(payload) {
    this.localNode = payload;
    this.emit('local-node-changed');
  }

  applyLocalPosition(payload) {
    this.localPosition = payload;
    this.emit('local-position-changed');
  }

  applyChannels(payload) {
    this.channels = payload.channels || [];
    this.emit('channels-changed');
  }

  applyMessage(payload) {
    const list = this.conversations.get(payload.key) || [];
    list.push(payload.message);
    this.conversations.set(payload.key, list);
    if (payload.unread) this.unread = new Map(Object.entries(payload.unread));
    this.emit('messages-changed', { key: payload.key });
  }

  applyMessageStatus(payload) {
    if (!payload.message) return;
    for (const list of this.conversations.values()) {
      const idx = list.findIndex((m) => m.packet_id === payload.packet_id && m.packet_id);
      if (idx !== -1) { list[idx] = payload.message; break; }
    }
    this.emit('messages-changed', {});
  }

  applyUnread(payload) {
    this.unread = new Map(Object.entries(payload.unread || {}));
    this.emit('messages-changed', {});
  }

  markRead(key) {
    this.unread.delete(key);
    this.emit('messages-changed', { key });
  }

  totalUnread() {
    let total = 0;
    for (const n of this.unread.values()) total += n;
    return total;
  }

  applyNeighbors(payload) {
    this.emit('neighbors', payload);
  }

  applyTraceroute(payload) {
    this.emit('traceroute', payload);
  }

  applyPacket(payload) {
    this.emit('packet', payload);
  }

  applyPacketStats(payload) {
    this.packetStats = payload;
    this.emit('packet-stats-changed');
  }

  applyNodeReady(payload) {
    this.favorites = new Set(payload.favorites || []);
    this.connection.node_ready = true;
    this.emit('connection-changed');
    this.emit('nodes-changed');
  }

  applyFavorites(payload) {
    this.favorites = new Set(payload.favorites || []);
    this.emit('nodes-changed');
  }

  /* Resposta imediata ao clique; o evento favorites do servidor confirma
     (ou corrige) logo a seguir. */
  toggleFavoriteLocal(nodeId) {
    if (this.favorites.has(nodeId)) this.favorites.delete(nodeId);
    else this.favorites.add(nodeId);
    this.emit('nodes-changed');
  }

  applyConfigLock(payload) {
    this.configLock = payload;
    this.emit('config-lock-changed');
  }

  setSelectedNode(nodeId) {
    this.selectedNodeId = nodeId;
    this.emit('selection-changed', nodeId);
  }

  setSearchText(text) {
    this.searchText = (text || '').toLowerCase();
    this.emit('search-changed', this.searchText);
  }

  nodeList() {
    return [...this.nodes.values()];
  }

  /* Critério da pesquisa global (ID, nome longo ou curto), partilhado pelas
     abas que listam nós. */
  matchesSearch(node) {
    const q = this.searchText;
    if (!q) return true;
    return (node.id_string || '').toLowerCase().includes(q)
      || (node.long_name || '').toLowerCase().includes(q)
      || (node.short_name || '').toLowerCase().includes(q);
  }

  /* O nó local não entra: já está no cabeçalho, ao lado da ligação. */
  visibleNodes() {
    return this.nodeList().filter((n) => {
      if (n.is_local) return false;
      if (n.last_heard == null) return false;
      return this.matchesSearch(n);
    });
  }
}

export const store = new Store();
