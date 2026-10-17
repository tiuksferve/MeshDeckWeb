/*
 * web/js/i18n.js — Traduções da interface web.
 *
 * O catálogo vem de /api/i18n, gerado a partir de i18n.py: o servidor e o
 * browser partilham as traduções, não há um segundo ficheiro no frontend. Tal como
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

/* Cada dispositivo tem a sua língua: a que o utilizador escolheu no menu
   (guardada num cookie, que o servidor também lê para traduzir métricas,
   configuração e erros) ou, sem escolha, a do próprio dispositivo. */
const LANG_COOKIE = 'meshdeck_lang';

export function preferredLanguage() {
  const saved = document.cookie.split('; ').find((c) => c.startsWith(`${LANG_COOKIE}=`));
  const value = saved && saved.slice(LANG_COOKIE.length + 1);
  if (value === 'pt' || value === 'en') return value;
  const device = (navigator.languages && navigator.languages[0]) || navigator.language || '';
  return device.toLowerCase().startsWith('pt') ? 'pt' : 'en';
}

/* Formato de data e hora: o da região do dispositivo quando está na língua
   escolhida (en-GB → 17/10/2026 14:05, en-US → 10/17/2026 2:05 PM); senão o
   habitual da língua escolhida. */
export function dateLocale() {
  const lang = current || 'en';
  const device = (navigator.languages && navigator.languages[0]) || navigator.language || '';
  if (device.toLowerCase().split('-')[0] === lang) return device;
  return lang === 'pt' ? 'pt-PT' : 'en-US';
}

/* O servidor também escreve horas (métricas): recebe o formato num cookie,
   já decomposto em ordem da data e relógio de 12/24 h. */
function rememberDateStyle() {
  let locale = dateLocale();
  try { locale = Intl.getCanonicalLocales(locale)[0]; } catch { locale = current === 'pt' ? 'pt-PT' : 'en-US'; }
  const parts = new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', year: 'numeric' })
    .formatToParts(new Date(2026, 9, 17)).map((p) => p.type).filter((type) => type !== 'literal');
  const order = parts.map((type) => type[0]).join('');
  const h12 = new Intl.DateTimeFormat(locale, { hour: 'numeric' }).resolvedOptions().hour12;
  const style = `${locale}.${['dmy', 'mdy', 'ymd'].includes(order) ? order : 'dmy'}.${h12 ? 12 : 24}`;
  document.cookie = `meshdeck_dates=${style}; path=/; max-age=${400 * 86400}; SameSite=Lax`;
}

export function rememberLanguage(lang) {
  const value = lang === 'pt' ? 'pt' : 'en';
  document.cookie = `${LANG_COOKIE}=${value}; path=/; max-age=${400 * 86400}; SameSite=Lax`;
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
  rememberDateStyle();
  document.documentElement.lang = lang;
  translateDom();
  return true;
}
