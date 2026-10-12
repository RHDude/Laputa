import { html } from 'htm/preact';
import { native } from './api.js';
import { Icon, Island } from './icons.js';
import { t, dur, ago, list } from './i18n.js';
import { pct, round } from './fmt.js';

// ---------- overall state ----------

export function diskMax(n) {
  let m = 0;
  ((n.m && n.m.disks) || []).forEach((d) => { m = Math.max(m, pct(d.used, d.total)); });
  return m;
}

export function isMuted(n, now) { return !!(n.muted_until && n.muted_until > now); }

// Reboot from the panel: the watchman learns about it from the agent; until then the app remembers sending it.
const sentReboot = {};
export function markReboot(id) { sentReboot[id] = Date.now() / 1000; }
export function rebootAt(n, now) {
  if (n.rebooting) return n.rebooting;
  const t = sentReboot[n.id];
  return t && now - t < 60 && n.status !== 'down' ? t : 0;
}

// The node's services in trouble: failed or not up after boot.
export function badServices(n) {
  if (n.status === 'down' || n.rebooting) return [];
  const bm = n.boot_missing || [];
  return n.services.filter((s) => s.state === 'failed' || s.state === 'missing' || bm.includes(s.unit));
}

export function isProblem(n, now) {
  if (n.status === 'stale' || (n.status === 'down' && !isMuted(n, now))) return true;
  return n.status === 'up' && (badServices(n).length > 0 || diskMax(n) >= 90 || (n.backup_alarm || []).length > 0);
}

export function health(snap, link, now) {
  if (!snap) {
    return link.state === 'lost' ? { sev: 'lost', title: t('h.lost'), sub: t('h.lost.sub') } : { sev: 'wait', title: t('h.connecting'), sub: '' };
  }
  const nodes = snap.nodes;
  const down = nodes.filter((n) => n.status === 'down' && n.confirmed !== false && !isMuted(n, now));
  const cut = nodes.filter((n) => n.status === 'down' && n.confirmed === false && !isMuted(n, now));
  const boot = nodes.filter((n) => rebootAt(n, now));
  const stale = nodes.filter((n) => n.status === 'stale' && !rebootAt(n, now));
  const failed = [];
  nodes.forEach((n) => badServices(n).forEach((s) => failed.push([n, s])));
  const disk = nodes.filter((n) => n.status === 'up' && diskMax(n) >= 90);
  const late = [];
  nodes.forEach((n) => { if (n.status === 'up') (n.backups || []).forEach((b) => { if ((n.backup_alarm || []).includes(b.path)) late.push([n, b]); }); });
  const extra = [];
  if (failed.length) extra.push(failed.length === 1 ? t('h.extra.svc', { label: failed[0][1].label }) : t('h.extra.svcs', { n: failed.length }));
  if (disk.length) extra.push(t('h.extra.disk', { name: disk[0].name }));

  if (link.state === 'lost') return { sev: 'lost', title: t('h.lost'), sub: t('h.lost.data', { ago: ago(snap.ts, now) }) };
  if (down.length) {
    const places = {};
    down.forEach((n) => { if (n.place) (places[n.place] = places[n.place] || []).push(n); });
    const place = Object.keys(places).find((p) => places[p].length >= 2 && places[p].length === down.length);
    let title;
    let sub;
    if (place) {
      title = t('h.place_down', { place });
      sub = t('h.place_down.sub', { names: list(down.map((n) => n.name)) });
    } else {
      title = down.length === 1 ? t('h.down1', { name: down[0].name }) : t('h.downN', { n: down.length });
      sub = down.length === 1 ? t('h.down1.sub', { d: dur(now - down[0].since) }) : list(down.map((n) => n.name)) + '.';
    }
    if (extra.length) sub += ' ' + t('h.also', { what: list(extra) });
    return { sev: 'crit', title, sub };
  }
  if (failed.length) {
    const [n, s] = failed[0];
    const title = failed.length === 1 ? t('h.svc1', { label: s.label }) : t('h.svcN', { n: failed.length });
    let sub = failed.length === 1 ? n.name + (s.since ? ', ' + ago(s.since, now) : '') + '.' : list(failed.map((f) => f[1].label)) + '.';
    if (disk.length) sub += ' ' + t('h.also', { what: t('h.extra.disk', { name: disk[0].name }) });
    return { sev: 'crit', title, sub };
  }
  if (boot.length) return { sev: 'ok', title: boot.length === 1 ? t('h.reboot1', { name: boot[0].name }) : t('h.rebootN', { n: boot.length }), sub: t('h.reboot.sub') };
  if (cut.length) return { sev: 'warn', title: t('h.cut', { hub: snap.hub.name, name: cut[0].name }), sub: t('h.cut.sub') };
  if (stale.length) return { sev: 'warn', title: t('h.stale', { name: stale[0].name }), sub: t('h.stale.sub', { ago: ago(stale[0].last_seen, now) }) };
  if (disk.length) return { sev: 'warn', title: t('h.disk', { name: disk[0].name }), sub: t('h.disk.sub', { p: round(diskMax(disk[0])) }) };
  if (late.length) return { sev: 'warn', title: t('h.backup', { label: late[0][1].label, name: late[0][0].name }), sub: late[0][1].ts ? t('h.backup.sub', { ago: ago(late[0][1].ts, now) }) : '' };
  const n = nodes.length;
  if (!n) return { sev: 'wait', title: t('h.empty'), sub: t('h.empty.sub') };
  return { sev: 'ok', title: n === 1 ? t('h.ok1') : t('h.okN', { n }), sub: '' };
}

// ---------- sky ----------

function isleStatus(n, now) {
  if (rebootAt(n, now)) return 'reboot';
  if (n.status === 'down') return isMuted(n, now) ? 'muted' : n.confirmed === false ? 'stale' : 'down';
  if (n.status === 'up' && badServices(n).length) return 'sick';
  return n.status;
}

// Up to 8 nodes: one row; up to 16: two tiers; more: one island per place, troubled nodes separate.
export function skyLayout(nodes, now) {
  let items = nodes.map((n) => ({ key: n.id, node: n }));
  if (nodes.length > 16) {
    const groups = new Map();
    const out = [];
    nodes.forEach((n) => {
      if (isProblem(n, now)) { out.push({ key: n.id, node: n }); return; }
      const p = n.place || '—';
      if (!groups.has(p)) { const g = { key: 'place:' + p, place: p, nodes: [] }; groups.set(p, g); out.push(g); }
      groups.get(p).nodes.push(n);
    });
    items = out.map((x) => (x.nodes && x.nodes.length === 1 ? { key: x.nodes[0].id, node: x.nodes[0] } : x));
  }
  if (items.length <= 8) return [items];
  const half = Math.ceil(items.length / 2);
  return [items.slice(0, half), items.slice(half)];
}

function Sky({ ctx, h }) {
  const { snap, now, link, cfg, setRoute } = ctx;
  const nodes = snap ? snap.nodes : [];
  const tiers = skyLayout(nodes, now);
  const open = (it) => {
    if (it.node) { setRoute({ tab: 'nodes', node: it.node.id }); return; }
    const el = document.getElementById('row-' + it.nodes[0].id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  let i = 0;
  return html`<header class=${'sky sky-' + h.sev}>
    <div class="sky-stars" />
    <div class="sky-text">
      <h1>${h.title}</h1>
      ${h.sub && html`<p>${h.sub}</p>`}
    </div>
    <div class=${'sky-isles' + (tiers.length > 1 ? ' tiers' : '')}>
      ${tiers.map((row, ti) => html`<div class=${'tier tier-' + ti} key=${ti} style=${'--n:' + Math.max(row.length, 1)}>
        ${row.map((it) => {
          const k = i++;
          const st = it.node ? isleStatus(it.node, now) : 'up';
          const label = it.node ? it.node.name : it.place;
          return html`<button key=${it.key} class=${'isle isle-' + st} style=${'--i:' + k + ';--dy:' + [0, -10, 5, -5, 8, -2][k % 6] + 'px'}
            onClick=${() => open(it)} aria-label=${it.node ? label : t('sky.place', { place: label, n: it.nodes.length })}>
            <${Island} status=${st} towers=${it.node ? Math.max(1, Math.min(3, it.node.services.length || 1)) : 3} seed=${k} />
            ${!it.node && html`<b class="isle-count">${it.nodes.length}</b>`}
            <span>${label}</span>
          </button>`;
        })}
      </div>`)}
    </div>
    ${snap && link.state === 'ok' && html`<div class="sky-foot">${t('sky.foot', { hub: snap.hub.name, ago: ago(snap.ts, now) })}${cfg.hubs.length < 2 ? t('sky.one') : ''}</div>`}
  </header>`;
}

// ---------- permissions alerts depend on ----------

export function Needs({ ctx }) {
  const { env, setEnv } = ctx;
  if (env.notif !== false && env.battery !== false) return null;
  const refresh = () => native('env').then((e) => setEnv((x) => ({ ...x, ...e })));
  const ask = (what) => native(what).then(() => { setTimeout(refresh, 700); setTimeout(refresh, 4000); });
  return html`<div class="needs">
    ${env.notif === false && html`<div class="need"><p>${t('need.notif')}</p>
      <button class="btn" onClick=${() => ask('perm.notifications')}>${t('need.notif.btn')}</button></div>`}
    ${env.battery === false && html`<div class="need"><p>${t('need.battery')}</p>
      <button class="btn" onClick=${() => ask('perm.battery')}>${t('need.battery.btn')}</button></div>`}
  </div>`;
}

// ---------- nodes ----------

export function Meter({ label, value, text, hot = 85, crit = 93 }) {
  const lvl = value >= crit ? 'crit' : value >= hot ? 'warn' : 'ok';
  return html`<div class=${'meter meter-' + lvl}>
    <div class="meter-top"><span>${label}</span><b>${text != null ? text : round(value) + ' %'}</b></div>
    <div class="meter-bar"><i style=${'width:' + Math.max(2, Math.min(100, value)) + '%'} /></div>
  </div>`;
}

function NodeRow({ n, now, onClick }) {
  const muted = isMuted(n, now);
  const bad = badServices(n);
  const m = n.m || {};
  const cut = n.status === 'down' && n.confirmed === false;
  const boot = rebootAt(n, now);
  let line;
  if (boot) line = html`<p class="row-note accent-t">${t('row.reboot', { d: dur(now - boot) })}</p>`;
  else if (cut) line = html`<p class="row-note warn-t">${t('row.cut', { d: dur(now - n.since) })}</p>`;
  else if (n.status === 'down') line = html`<p class="row-note crit">${t('row.down', { d: dur(now - n.since) })}${muted ? t('row.muted') : ''}</p>`;
  else if (n.status === 'new') line = html`<p class="row-note">${t('row.new')}</p>`;
  else {
    line = html`<div class="meters">
      <${Meter} label=${t('m.cpu')} value=${m.cpu || 0} />
      <${Meter} label=${t('m.mem')} value=${pct((m.mem || [])[0], (m.mem || [])[1])} hot=${90} crit=${97} />
      <${Meter} label=${t('m.disk')} value=${diskMax(n)} hot=${80} crit=${90} />
    </div>`;
  }
  const late = (n.backups || []).filter((b) => (n.backup_alarm || []).includes(b.path));
  return html`<button id=${'row-' + n.id} class=${'row node-row st-' + (cut ? 'stale' : n.status)} onClick=${onClick}>
    <div class="row-head">
      <i class=${'lamp lamp-' + (boot ? 'reboot' : cut ? 'stale' : n.status)} />
      <strong class="disp">${n.name}</strong>
      <span class="place">${n.place}</span>
      <span class="grow" />
      ${n.status === 'up' && !n.pending && !boot && html`<span class="dim">${t('row.up', { d: dur(m.uptime || 0, true) })}</span>`}
      ${n.status === 'up' && n.pending && !boot && html`<span class="warn-t">${t('row.pending')}</span>`}
      ${n.status === 'stale' && !boot && html`<span class="warn-t">${t('row.stale')}</span>`}
    </div>
    ${line}
    ${bad.length > 0 && html`<p class="row-note crit"><${Icon} name="warn" size=${16} />
      ${bad.length === 1 ? t('row.svc1', { label: bad[0].label }) : t('row.svcN', { labels: list(bad.map((s) => s.label)) })}</p>`}
    ${late.length > 0 && n.status === 'up' && html`<p class="row-note warn-t">${t('row.backup', { label: late[0].label })}</p>`}
    ${n.status === 'up' && (n.candidates || []).length > 0 && html`<p class="row-note">${t('row.cand', { n: n.candidates.length })}</p>`}
  </button>`;
}

export function NodesTab({ ctx }) {
  const { snap, link, now, setRoute } = ctx;
  const h = health(snap, link, now);
  return html`<div class="page page-home">
    <${Sky} ctx=${ctx} h=${h} />
    <div class="ground">
      <${Needs} ctx=${ctx} />
      ${snap && snap.nodes.map((n) => html`<${NodeRow} key=${n.id} n=${n} now=${now} onClick=${() => setRoute({ tab: 'nodes', node: n.id })} />`)}
      ${!snap && link.state !== 'lost' && html`<p class="empty">${t('home.loading')}</p>`}
      ${!snap && link.state === 'lost' && html`<p class="empty">${t('home.lost')}</p>`}
    </div>
  </div>`;
}
