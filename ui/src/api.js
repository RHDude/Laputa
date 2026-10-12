// Bridge between the UI, the native shell and the watchmen.
import { mockState, mockHistory, mockCmd } from './mock.js';
import { t } from './i18n.js';

const N = typeof window !== 'undefined' ? window.LaputaNative : null;
const Q = new URLSearchParams(typeof location !== 'undefined' ? location.search : '');
// __DEV__ is set by the build: the production bundle drops the mock entirely.
export const MOCK = __DEV__ && !N && Q.has('mock') ? (Q.get('mock') || 'ok') : null;
const DEV_HUBS = !N && Q.get('hub')
  ? Q.getAll('hub').map((u, i) => ({ url: u, pin: '', token: Q.getAll('token')[i] || Q.get('token') || '', name: '' }))
  : null;

let seq = 0;
const pending = new Map();
const listeners = new Set();

if (typeof window !== 'undefined') {
  window.__laputa = {
    resolve(id, ok, payload) {
      const p = pending.get(id);
      if (!p) return;
      pending.delete(id);
      if (ok) p.res(payload); else p.rej(new Error(typeof payload === 'string' ? payload : 'native error'));
    },
    emit(evt) { listeners.forEach((fn) => { try { fn(evt); } catch (e) { /* ignore */ } }); },
    back() { let handled = false; listeners.forEach((fn) => { if (fn({ type: 'back' }) === true) handled = true; }); return handled; },
  };
}

export function onNative(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function native(method, args) {
  if (!N) return devNative(method, args || {});
  return new Promise((res, rej) => {
    const id = ++seq;
    pending.set(id, { res, rej });
    try { N.post(JSON.stringify({ id, method, args: args || {} })); } catch (e) { pending.delete(id); rej(e); }
  });
}

// ---------- running without the shell (browser, mock, bench) ----------

function lsGet(key, def) {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : def; } catch (e) { return def; }
}
function lsSet(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) { /* ignore */ }
}

// Monet-like palette for browser testing: one hue at different lightness.
function devMonet(hue) {
  const L = { 0: 100, 10: 99, 50: 95, 100: 90, 200: 80, 300: 70, 400: 60, 500: 50, 600: 40, 700: 30, 800: 20, 900: 10, 1000: 0 };
  const hex = (h, sat, l) => {
    const a = (sat / 100) * Math.min(l / 100, 1 - l / 100);
    const f = (n) => { const k = (n + h / 30) % 12; const c = l / 100 - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)); return Math.round(255 * c).toString(16).padStart(2, '0'); };
    return '#' + f(0) + f(8) + f(4);
  };
  const pal = (sat, dh) => { const o = {}; Object.keys(L).forEach((k) => { o[k] = hex(hue + dh, sat, L[k]); }); return o; };
  return { accent1: pal(55, 0), accent2: pal(25, 0), accent3: pal(40, 60), neutral1: pal(6, 0), neutral2: pal(12, 0) };
}

let devStore = null;
async function devNative(method, a) {
  switch (method) {
    case 'env':
      return { version: 'dev', night: Q.get('theme') !== 'light', notif: Q.get('perm') !== '0', battery: Q.get('perm') !== '0', sdk: 0,
        lang: Q.get('lang') || undefined, monet: Q.has('monet') ? devMonet(+Q.get('monet') || 150) : undefined };
    case 'store.get': {
      if (!devStore) {
        devStore = __DEV__ && MOCK ? { hubs: Q.has('fresh') ? [] : [{ url: 'https://192.0.2.10:8743', pin: 'aa', token: 't', name: '' }, { url: 'https://192.0.2.20:8743', pin: 'bb', token: 't', name: '' }], settings: {} }
          : DEV_HUBS ? { hubs: DEV_HUBS, settings: {} } : lsGet('laputa', { hubs: [], settings: {} });
      }
      return devStore;
    }
    case 'store.set': devStore = a; if (!(__DEV__ && MOCK) && !DEV_HUBS) lsSet('laputa', a); return true;
    case 'http': {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), a.timeout || 15000);
      try {
        const r = await fetch(a.url, { method: a.method || 'GET', body: a.body || undefined, signal: ctl.signal,
          headers: { Authorization: 'Bearer ' + a.token, ...(a.body ? { 'Content-Type': 'application/json' } : {}) } });
        return { status: r.status, body: await r.text() };
      } finally { clearTimeout(t); }
    }
    case 'clipboard.read':
      try { return await navigator.clipboard.readText(); } catch (e) { return ''; }
    case 'clipboard.write':
      try { await navigator.clipboard.writeText(a.text); } catch (e) { /* ignore */ }
      return true;
    case 'open': window.open(a.url, '_blank'); return true;
    case 'log.get': return t('log.browser');
    default: return true;
  }
}

// ---------- watchmen ----------

export class HubError extends Error {
  constructor(msg, status) { super(msg); this.status = status || 0; }
}

let mockVer = 1;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function mockReq(method, path, body) {
  if (path.startsWith('/v1/state')) {
    const since = +(/since=(\d+)/.exec(path) || [0, 0])[1];
    if (since >= mockVer) { await sleep(4000); mockVer++; }
    const s = mockState(MOCK); s.v = mockVer; return s;
  }
  if (path.startsWith('/v1/history')) return mockHistory((/node=([\w-]+)/.exec(path) || [0, 'x'])[1]);
  if (path === '/v1/cmd') {
    await sleep(600);
    return mockCmd(body.type, body.args || {});
  }
  if (path === '/v1/ping') return { ok: true, name: 'Summit', id: 'summit' };
  return { ok: true };
}

export async function hubReq(hub, method, path, body, timeout) {
  if (__DEV__ && MOCK) return mockReq(method, path, body);
  let r;
  try {
    r = await native('http', { url: hub.url.replace(/\/+$/, '') + path, pin: hub.pin, token: hub.token, method,
      body: body ? JSON.stringify(body) : '', timeout: timeout || 15000 });
  } catch (e) {
    throw new HubError(String((e && e.message) || e), 0);
  }
  if (r.status === 401 || r.status === 403 || r.status === 404) throw new HubError(t('err.hub_key'), r.status);
  if (r.status !== 200) throw new HubError(t('err.hub_status', { status: r.status }), r.status);
  try { return JSON.parse(r.body); } catch (e) { throw new HubError(t('err.hub_garbage'), 200); }
}

// Watchman key: 'laputa1:' + base64url(JSON {url, pin, token, name}).
export function parseKey(text) {
  const m = /laputa1:([A-Za-z0-9_\-=]+)/.exec((text || '').trim());
  if (!m) return null;
  try {
    let b = m[1].replace(/-/g, '+').replace(/_/g, '/');
    while (b.length % 4) b += '=';
    const raw = atob(b);
    const bytes = Uint8Array.from(raw, (c) => c.charCodeAt(0));
    const o = JSON.parse(new TextDecoder().decode(bytes));
    if (!o.url || !o.token) return null;
    return { url: String(o.url), pin: String(o.pin || ''), token: String(o.token), name: String(o.name || '') };
  } catch (e) { return null; }
}
