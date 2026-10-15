/*
 * web/js/socket.js — Ligação Socket.IO e os comandos enviados ao servidor.
 *
 * Os nomes dos eventos espelham server/events.py; os dois lados têm de
 * concordar nesse ficheiro para o contrato não derivar silenciosamente.
 */
'use strict';

import { store } from './store.js';
import { playMessageSound } from './sound.js';

let socket = null;

export function connectSocket(onToast) {
  if (socket) {
    if (socket.connected) return socket;
    socket.disconnect();
  }
  socket = io({ withCredentials: true });

  socket.on('connect', () => console.info('[socket] ligado', socket.id));
  socket.on('connect_error', (err) => console.error('[socket] connect_error', err.message));
  socket.on('disconnect', (reason) => console.warn('[socket] desligado', reason));

  socket.on('snapshot', (payload) => store.applySnapshot(payload));
  socket.on('connection', (payload) => store.applyConnection(payload));
  socket.on('reconnect', (payload) => store.applyReconnect(payload));
  socket.on('nodes', (payload) => store.applyNodes(payload));
  socket.on('nodedb_reset', (payload) => store.applyNodedbReset(payload));
  socket.on('local_node', (payload) => store.applyLocalNode(payload));
  socket.on('local_position', (payload) => store.applyLocalPosition(payload));
  socket.on('my_node_id', () => {});   // já reflectido em local_node_ready
  socket.on('channels', (payload) => store.applyChannels(payload));
  socket.on('snr_levels', (payload) => store.applySnrLevels(payload));
  socket.on('message', (payload) => {
    store.applyMessage(payload);
    // 'message' só traz recebidas (as nossas chegam por 'message_sent').
    if (!payload?.message?.outgoing) {
      playMessageSound(String(payload?.key || '').startsWith('dm:') ? 'dm' : 'channel');
    }
  });
  socket.on('message_sent', (payload) => store.applyMessage(payload));
  socket.on('message_status', (payload) => store.applyMessageStatus(payload));
  socket.on('unread', (payload) => store.applyUnread(payload));
  socket.on('favorites', (payload) => store.applyFavorites(payload));
  socket.on('traceroute', (payload) => store.applyTraceroute(payload));
  socket.on('neighbors', (payload) => store.applyNeighbors(payload));
  socket.on('packet', (payload) => store.applyPacket(payload));
  socket.on('packet_stats', (payload) => store.applyPacketStats(payload));
  socket.on('node_ready', (payload) => store.applyNodeReady(payload));
  socket.on('config_lock', (payload) => store.applyConfigLock(payload));
  socket.on('position_sent', (payload) => { if (!payload.success) onToast?.(payload.message); });
  socket.on('error', (payload) => onToast?.(payload.message));
  socket.on('log', (payload) => store.emit('log-line', payload.line));

  return socket;
}

export function disconnectSocket() {
  socket?.disconnect();
  socket = null;
}

function emit(event, payload = {}) {
  if (!socket || !socket.connected) return;
  socket.emit(event, payload);
}

/* ── comandos: um por evento de server/events.py CMD_* ────────────────────── */
export const commands = {
  connectNode: (target) => emit('connect_node', target),
  disconnectNode: () => emit('disconnect_node', {}),
  sendChannelMessage: (channel, text, replyId = 0) =>
    emit('send_channel_message', { channel, text, reply_id: replyId }),
  sendDm: (nodeId, text, replyId = 0) => emit('send_dm', { node_id: nodeId, text, reply_id: replyId }),
  traceroute: (nodeId, hopLimit = 3) => emit('traceroute', { node_id: nodeId, hop_limit: hopLimit }),
  sendPosition: () => emit('send_position', {}),
  sendNodeInfo: () => emit('send_nodeinfo', {}),
  refreshLocalPosition: () => emit('refresh_local_position', {}),
  resetNodedb: () => emit('reset_nodedb', {}),
  toggleFavorite: (nodeId) => emit('toggle_favorite', { node_id: nodeId }),
  markRead: (key) => emit('mark_read', { key }),
  setLanguage: (language) => emit('set_language', { language }),
  requestSnapshot: () => emit('request_snapshot', {}),
  clearMetrics: () => emit('clear_metrics', {}),
};
