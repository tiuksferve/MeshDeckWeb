/*
 * web/js/api.js — Pedidos HTTP autenticados.
 *
 * Tudo o que é pedido pontualmente (login, esquema de configuração, HTML das
 * métricas, PSK dos canais, detalhe de um pacote) passa por aqui. O que é
 * contínuo vai por socket.js.
 */
'use strict';

export async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: options.body ? { 'Content-Type': 'application/json' } : {},
    ...options,
  });
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  return { ok: response.ok, status: response.status, data };
}

export const getJSON = (path) => api(path);
export const postJSON = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });
