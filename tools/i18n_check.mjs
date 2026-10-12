// Dictionary check: every key used in code exists in ru, all languages have the same keys, {params} and plural forms.
import fs from 'fs';
import path from 'path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const dir = path.join(ROOT, 'ui/i18n');
const langs = ['ru', 'en', 'uk', 'de', 'es', 'zh'];
const D = {};
for (const l of langs) {
  try { D[l] = JSON.parse(fs.readFileSync(path.join(dir, l + '.json'), 'utf8')); } catch (e) { console.log('missing or broken', l, e.message); process.exitCode = 1; }
}
const FORMS = { ru: ['one', 'few', 'many', 'other'], uk: ['one', 'few', 'many', 'other'], en: ['one', 'other'], de: ['one', 'other'], es: ['one', 'other'], zh: ['other'] };
const params = (v) => new Set((typeof v === 'object' ? Object.values(v).join(' ') : v).match(/\{\w+\}/g) || []);
let bad = 0;
const say = (...a) => { bad++; console.log(...a); };

// keys used by the UI
const src = path.join(ROOT, 'ui/src');
const used = new Set();
const prefixes = new Set();
for (const f of fs.readdirSync(src)) {
  const text = fs.readFileSync(path.join(src, f), 'utf8');
  for (const m of text.matchAll(/'([a-z][a-z0-9_]*(?:\.[a-z0-9_-]+)+\.?)'/g)) {
    const k = m[1];
    if (/\.(js|json|css|html|service|timer|ini|org|com|txt|sh|py)$/.test(k) || /^\d/.test(k)) continue;
    if (k.endsWith('.')) prefixes.add(k); else used.add(k);
  }
}
// keys used by the native side
const jdir = path.join(ROOT, 'android/src/app/laputa');
if (fs.existsSync(jdir)) {
  for (const f of fs.readdirSync(jdir)) {
    const text = fs.readFileSync(path.join(jdir, f), 'utf8');
    for (const m of text.matchAll(/(?:T\.t|tr)\(\s*"([a-z][a-z0-9_.-]+)"/g)) used.add(m[1]);
  }
}
const ru = D.ru || {};
for (const k of used) {
  if (!(k in ru) && ![...prefixes].some((p) => k.startsWith(p) && false)) {
    // strings that look like keys but are not
    if (/^(svc|game|node|backup|action|maint|diag|mc|impact)\.[a-z_]+$/.test(k) && !k.startsWith('svc.') && !k.startsWith('game.')) continue;
    if (['svc.restart', 'svc.start', 'svc.stop', 'svc.logs', 'svc.discover', 'svc.suggest', 'svc.add', 'svc.add_many', 'svc.edit', 'svc.remove', 'svc.dismiss',
      'game.setup', 'game.cmd', 'game.quick', 'game.disable', 'node.reboot', 'node.reboot_cancel', 'backup.add', 'backup.remove', 'backup.suggest', 'action.run',
      'store.get', 'store.set', 'clipboard.read', 'clipboard.write', 'log.get', 'log.put', 'alerts.apply', 'alerts.test', 'perm.notifications', 'perm.battery',
      'maint.check', 'diag.probe', 'mc.setup', 'mc.cmd', 'mc.disable', 'example.org', 'appassets.laputa', 'laputa.theme'].includes(k)) continue;
    say('missing in ru:', k);
  }
}
for (const p of prefixes) if (!Object.keys(ru).some((k) => k.startsWith(p))) say('no keys with prefix', p);
for (const l of langs) {
  if (!D[l]) continue;
  for (const k of Object.keys(ru)) {
    if (!(k in D[l])) { say(l, 'missing key', k); continue; }
    const a = params(ru[k]);
    const b = params(D[l][k]);
    const miss = [...a].filter((x) => !b.has(x));
    const extra = [...b].filter((x) => !a.has(x));
    if (miss.length || extra.length) say(l, k, 'params:', miss.length ? 'missing ' + miss.join(' ') : '', extra.length ? 'extra ' + extra.join(' ') : '');
    const v = D[l][k];
    if (typeof v === 'object') {
      for (const f of FORMS[l]) if (!(f in v)) say(l, k, 'missing plural form', f);
    } else if (typeof ru[k] === 'object' && l !== 'zh') say(l, k, 'needs plural forms');
  }
  for (const k of Object.keys(D[l])) if (!(k in ru)) say(l, 'extra key', k);
}
console.log(bad ? 'Errors: ' + bad : 'Dictionaries OK: ' + Object.keys(ru).length + ' keys x ' + langs.length + ' languages, ' + used.size + ' used in code');
process.exitCode = bad ? 1 : 0;
