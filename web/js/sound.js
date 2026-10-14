/*
 * web/js/sound.js — Aviso sonoro de mensagem recebida.
 *
 * O som é sintetizado com Web Audio (sem ficheiros): duas notas curtas e
 * suaves, mais agudas numa DM do que num canal. A preferência é por
 * dispositivo (localStorage), porque o mesmo servidor pode estar aberto num
 * telemóvel que deve tocar e num portátil que não.
 *
 * O Safari iOS só deixa tocar áudio depois de um gesto do utilizador: o
 * AudioContext é criado/retomado no primeiro toque ou tecla na página.
 */
'use strict';

const STORAGE_KEY = 'meshdeck.sound';
const MIN_GAP_MS = 1500;   // rajada de mensagens: um aviso, não dez

let ctx = null;
let lastPlayed = 0;
let enabled = readPreference();

function readPreference() {
  try {
    return localStorage.getItem(STORAGE_KEY) !== 'off';
  } catch {
    return true;
  }
}

export function isSoundEnabled() {
  return enabled;
}

export function setSoundEnabled(on) {
  enabled = !!on;
  try { localStorage.setItem(STORAGE_KEY, enabled ? 'on' : 'off'); } catch { /* sem storage */ }
}

function context() {
  if (ctx) return ctx;
  const Ctor = window.AudioContext || window.webkitAudioContext;
  if (!Ctor) return null;
  ctx = new Ctor();
  return ctx;
}

/* Chamado uma vez no arranque: o primeiro gesto desbloqueia o áudio. */
export function initSound() {
  const unlock = () => {
    const c = context();
    if (!c) return;
    if (c.state === 'suspended') c.resume();
    // Um buffer vazio dentro do gesto é o que o iOS exige para "abrir" o áudio.
    const source = c.createBufferSource();
    source.buffer = c.createBuffer(1, 1, 22050);
    source.connect(c.destination);
    source.start(0);
    ['touchend', 'click', 'keydown'].forEach((e) => document.removeEventListener(e, unlock, true));
  };
  ['touchend', 'click', 'keydown'].forEach((e) => document.addEventListener(e, unlock, true));
}

function tone(c, freq, start, duration, peak) {
  const osc = c.createOscillator();
  const gain = c.createGain();
  osc.type = 'sine';
  osc.frequency.setValueAtTime(freq, start);
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(peak, start + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  osc.connect(gain).connect(c.destination);
  osc.start(start);
  osc.stop(start + duration + 0.02);
}

/* kind: 'dm' ou 'channel'. force ignora a preferência e o intervalo mínimo
   (pré-escuta ao ligar o som no menu). */
export function playMessageSound(kind = 'channel', { force = false } = {}) {
  if (!force && !enabled) return;
  const now = Date.now();
  if (!force && now - lastPlayed < MIN_GAP_MS) return;
  const c = context();
  if (!c) return;
  if (c.state === 'suspended') c.resume();
  if (c.state !== 'running' && !force) return;   // ainda sem gesto do utilizador
  lastPlayed = now;

  const t0 = c.currentTime + 0.01;
  if (kind === 'dm') {
    tone(c, 880, t0, 0.18, 0.18);          // A5
    tone(c, 1318.5, t0 + 0.12, 0.28, 0.16); // E6
  } else {
    tone(c, 659.3, t0, 0.16, 0.14);         // E5
    tone(c, 987.8, t0 + 0.11, 0.24, 0.12);  // B5
  }
}
