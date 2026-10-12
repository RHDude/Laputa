// UI languages. Dictionaries live in ui/i18n/*.json; the native side reads them for notifications.
import ru from '../i18n/ru.json';
import en from '../i18n/en.json';
import uk from '../i18n/uk.json';
import de from '../i18n/de.json';
import es from '../i18n/es.json';
import zh from '../i18n/zh.json';

const DICTS = { ru, en, uk, de, es, zh };
export const LANGS = [['ru', 'Русский'], ['en', 'English'], ['uk', 'Українська'], ['de', 'Deutsch'], ['es', 'Español'], ['zh', '中文']];

let lang = 'ru';
let D = ru;
let PR = new Intl.PluralRules('ru');
const NF = {};

export function pickLang(pref, sys) {
  if (pref && DICTS[pref]) return pref;
  let s = String(sys || (typeof navigator !== 'undefined' ? navigator.language : '') || '').toLowerCase().slice(0, 2);
  if (DICTS[s]) return s;
  return s === 'be' || s === 'kk' ? 'ru' : 'en';
}

export function setLang(l) {
  if (!DICTS[l]) return;
  if (typeof document !== 'undefined' && document.documentElement.lang !== l) document.documentElement.lang = l;
  if (l === lang) return;
  lang = l;
  D = DICTS[l];
  PR = new Intl.PluralRules(l);
}

export function getLang() { return lang; }

function raw(key) {
  let v = D[key];
  if (v === undefined) v = en[key];
  if (v === undefined) v = ru[key];
  return v;
}

export function has(key) { return raw(key) !== undefined; }

// t('h.down1', {name: 'X'}). An object value holds plural forms, picked by p.n.
export function t(key, p) {
  let v = raw(key);
  if (v === undefined) return key;
  if (typeof v === 'object') {
    const n = p && p.n != null ? +p.n : 0;
    v = v[PR.select(n)] ?? v.other ?? v.many ?? '';
  }
  return p ? v.replace(/\{(\w+)\}/g, (m, k) => (p[k] != null ? String(p[k]) : '')) : v;
}

export function num(x, digits) {
  const k = lang + (digits || 0);
  if (!NF[k]) NF[k] = new Intl.NumberFormat(lang, { maximumFractionDigits: digits || 0, minimumFractionDigits: digits || 0 });
  return NF[k].format(x);
}

// Duration: '40 s', '12 min', '3 h 5 min', '12 d 4 h'.
export function dur(sec, short) {
  sec = Math.max(0, Math.floor(sec));
  const j = t('u.join');
  if (sec < 60) return t('u.s', { n: sec });
  const m = Math.floor(sec / 60);
  if (m < 60) return t('u.min', { n: m });
  const h = Math.floor(m / 60);
  if (h < 24) {
    const mm = m % 60;
    return short || !mm ? t('u.h', { n: h }) : t('u.h', { n: h }) + j + t('u.min', { n: mm });
  }
  const d = Math.floor(h / 24);
  const hh = h % 24;
  return short || !hh ? t('u.d', { n: d }) : t('u.d', { n: d }) + j + t('u.h', { n: hh });
}

export function ago(ts, now) {
  const s = Math.max(0, Math.floor(now - ts));
  if (s < 5) return t('ago.now');
  return t('ago', { d: dur(s, true) });
}

export function bytes(n) {
  if (n == null || isNaN(n)) return '—';
  const U = t('u.bytes').split(' ');
  let i = 0;
  while (n >= 1024 && i < U.length - 1) { n /= 1024; i++; }
  return num(n, n >= 100 || i === 0 ? 0 : 1) + ' ' + U[i];
}

export function rate(bps) {
  if (bps == null) return '—';
  const U = t('u.bits').split(' ');
  const bits = bps * 8;
  if (bits < 1000) return num(bits) + ' ' + U[0];
  if (bits < 1e6) return num(bits / 1e3) + ' ' + U[1];
  if (bits < 1e9) return num(bits / 1e6, 1) + ' ' + U[2];
  return num(bits / 1e9, 2) + ' ' + U[3];
}

function pad(n) { return n < 10 ? '0' + n : '' + n; }
export function clock(ts) {
  const d = new Date(ts * 1000);
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}

export function day(ts, now) {
  const d = new Date(ts * 1000);
  const n = new Date(now * 1000);
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(n) - start(d)) / 86400000);
  if (diff === 0) return t('day.today');
  if (diff === 1) return t('day.yesterday');
  return new Intl.DateTimeFormat(lang, { day: 'numeric', month: 'long' }).format(d);
}

export function list(items) {
  return items.join(t('u.comma'));
}

// ---------- events: text built from type and params, the watchman's text is a fallback ----------

const VARIANT = {
  node_down: (p) => (p.group ? 'group' : p.both ? 'both' : p.gone != null ? 'gone' : ''),
  node_up: (p) => (p.why === 'boot' && p.cause ? 'bootc' : p.why || ''),
  svc_failed: (p) => (p.restarts ? 'r' : ''),
  ssh_login: (p) => (p.new ? 'new' : ''),
};

export function evParams(p) {
  const q = { ...p };
  ['gone', 'age', 'every'].forEach((k) => { if (typeof p[k] === 'number') q[k] = dur(p[k]); });
  ['names', 'labels'].forEach((k) => { if (Array.isArray(p[k])) q[k] = list(p[k]); });
  if (p.cause) q.cause = t('cause.' + p.cause);
  if (typeof p.why === 'string' && has('maint.why.' + p.why)) q.why = t('maint.why.' + p.why);
  q.n = p.n ?? p.restarts ?? p.hours;
  return q;
}

export function evText(e) {
  const p = e.p || {};
  const base = 'ev.' + e.type + '.';
  if (!has(base + 't')) return { title: e.title || '', text: e.text || '' };
  const v = VARIANT[e.type] ? VARIANT[e.type](p) : '';
  const k = (s) => (v && has(base + s + '.' + v) ? base + s + '.' + v : base + s);
  const q = evParams(p);
  return { title: t(k('t'), q), text: has(k('x')) ? t(k('x'), q) : '' };
}

// ---------- agent and watchman errors ----------

export function errText(r) {
  const old = r && !r.code && /не знает команду (\S+)/.exec(r.error || '');   // agent 1.0
  if (old) return t('err.unknown_cmd', { cmd: old[1] });
  if (r && r.code && has('err.' + r.code)) {
    const p = { ...(r.p || {}) };
    if (p.out) p.out = String(p.out).slice(-300);
    return t('err.' + r.code, p);
  }
  return (r && r.error) || t('err.generic');
}
