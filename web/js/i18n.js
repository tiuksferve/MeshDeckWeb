/*
 * web/js/i18n.js — Traduções da interface web.
 *
 * O catálogo vem de /api/i18n, gerado a partir de i18n.py: a fonte é a mesma
 * do desktop, não há um segundo ficheiro de traduções no frontend. Tal como
 * no tr() do Python, a chave é o texto em PT e, se faltar, mostra-se a chave.
 *
 * O HTML estático marca-se com data-i18n (texto), data-i18n-placeholder e
 * data-i18n-title; o texto gerado em JS passa por t().
 */
'use strict';

let strings = {};
let current = null;
let requested = null;

export function getLanguage() {
  return current;
}

export function t(key, vars) {
  let text = strings[key] ?? key;
  if (vars) {
    text = text.replace(/\{(\w+)\}/g, (match, name) => (name in vars ? String(vars[name]) : match));
  }
  return text;
}

export function translateDom(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  root.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.dataset.i18nTitle); });
}

/* Carrega o catálogo e traduz a página. Devolve true se o idioma mudou, para
   quem chama saber se tem de redesenhar o conteúdo gerado em JS. */
export async function loadLanguage(lang) {
  lang = lang === 'pt' ? 'pt' : 'en';
  requested = lang;
  if (lang === current) return false;
  const response = await fetch(`/api/i18n?lang=${lang}`, { credentials: 'same-origin' });
  if (!response.ok) return false;
  const catalog = (await response.json()).strings || {};
  // Um pedido mais recente ganha: duas trocas rápidas não podem acabar com
  // o catálogo da primeira aplicado por cima da segunda.
  if (requested !== lang) return false;
  strings = catalog;
  current = lang;
  document.documentElement.lang = lang;
  translateDom();
  return true;
}
