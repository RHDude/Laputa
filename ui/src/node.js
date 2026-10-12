import { useState, useEffect } from 'preact/hooks';
import { html } from 'htm/preact';
import { Icon } from './icons.js';
import { t, dur, ago, bytes, rate, clock, list, num } from './i18n.js';
import { pct, round, unitName, guessKind, KINDS, kindName } from './fmt.js';
import { isMuted, badServices, Meter, rebootAt } from './home.js';
import { NotifyRows, Group } from './tabs.js';

// ---------- charts ----------

export function Spark({ data, kind, h = 44 }) {
  if (!data || data.length < 2) return html`<div class="spark spark-empty" style=${'height:' + h + 'px'} />`;
  const w = 300;
  const pts = [];
  const n = data.length;
  let started = false;
  let d = '';
  for (let i = 0; i < n; i++) {
    const v = data[i];
    if (v == null) { started = false; continue; }
    const x = (i / (n - 1)) * w;
    const y = h - 2 - (Math.max(0, Math.min(100, v)) / 100) * (h - 6);
    d += (started ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1);
    started = true;
    pts.push([x, y]);
  }
  if (!pts.length) return html`<div class="spark spark-empty" style=${'height:' + h + 'px'} />`;
  const area = d + 'L' + pts[pts.length - 1][0].toFixed(1) + ' ' + h + 'L' + pts[0][0].toFixed(1) + ' ' + h + 'Z';
  return html`<svg class=${'spark spark-' + kind} style=${'height:' + h + 'px'} viewBox=${'0 0 ' + w + ' ' + h} preserveAspectRatio="none" aria-hidden="true">
    <path class="spark-area" d=${area} /><path class="spark-line" d=${d} />
  </svg>`;
}

// ---------- services ----------

export function svcText(s, node, now) {
  if (node && (node.boot_missing || []).includes(s.unit) && s.state !== 'active') return t('svc.boot_missing');
  switch (s.state) {
    case 'active': return s.since ? t('svc.active.for', { d: dur(now - s.since, true) }) : t('svc.active');
    case 'failed': return s.since ? t('svc.failed.ago', { ago: ago(s.since, now) }) : t('svc.failed');
    case 'inactive': return t('svc.inactive');
    case 'activating': return t('svc.activating');
    case 'deactivating': return t('svc.deactivating');
    case 'missing': return t('svc.missing');
    default: return t('svc.unknown');
  }
}

export function svcSev(s, node) {
  if (node && (node.boot_missing || []).includes(s.unit) && s.state !== 'active') return 'crit';
  if (s.state === 'active') return 'ok';
  if (s.state === 'failed' || s.state === 'missing') return 'crit';
  if (s.state === 'activating' || s.state === 'deactivating') return 'warn';
  return 'off';
}

export function ServiceRow({ s, node, now, showNode, onClick }) {
  const sev = svcSev(s, node);
  return html`<button class=${'row svc-row sv-' + sev} onClick=${onClick}>
    <span class=${'svc-ic kind-' + s.kind}><${Icon} name=${s.unit.startsWith('docker:') && s.kind === 'other' ? 'box' : s.kind} /></span>
    <span class="svc-main">
      <strong>${s.label}</strong>
      <small class=${sev === 'crit' ? 'crit' : sev === 'warn' ? 'warn-t' : 'dim'}>${svcText(s, node, now)}</small>
    </span>
    ${showNode && html`<span class="svc-node">${node.name}</span>`}
    <${Icon} name="chevron" size=${18} cls="dim" />
  </button>`;
}

// Services folded by kind. Up to 6: a plain list, one-row groups only get in the way.
// A group in trouble is always open; the phone remembers what is open.
export function SvcFolds({ ctx, scope, items, showNode, flatUpTo = 6 }) {
  const { cfg, saveCfg, setSheet, now } = ctx;
  const row = ({ n, s }) => html`<${ServiceRow} key=${n.id + s.unit} s=${n.status === 'down' ? { ...s, state: 'unknown' } : s} node=${n} now=${now} showNode=${showNode}
    onClick=${() => setSheet({ type: 'service', node: n.id, unit: s.unit })} />`;
  if (items.length <= flatUpTo) return html`<div class="list">${items.map(row)}</div>`;
  const open = cfg.settings.fold || {};
  const groups = KINDS.map((k) => [k, items.filter(({ s }) => (KINDS.includes(s.kind) ? s.kind : 'other') === k)]).filter((g) => g[1].length);
  return html`<div class="folds">${groups.map(([k, list]) => {
    const bad = list.filter(({ n, s }) => n.status !== 'down' && svcSev(s, n) === 'crit').length;
    const unknown = list.filter(({ n }) => n.status === 'down').length;   // node is silent: service states unknown
    const off = list.filter(({ n, s }) => n.status !== 'down' && svcSev(s, n) === 'off').length;
    const summary = bad ? t('fold.bad', { n: bad }) : unknown === list.length ? t('svc.unknown')
      : [off && t('fold.off', { n: off }), unknown && t('fold.unknown', { n: unknown })].filter(Boolean).join(', ') || t('fold.ok');
    const key = scope + ':' + k;
    const isOpen = bad > 0 || !!open[key];
    const toggle = () => saveCfg({ ...cfg, settings: { ...cfg.settings, fold: { ...open, [key]: !open[key] } } });
    return html`<div class=${'fold' + (isOpen ? ' open' : '')} key=${k}>
      <button class="fold-head" onClick=${toggle} aria-expanded=${isOpen} disabled=${bad > 0}>
        <span class=${'svc-ic kind-' + k}><${Icon} name=${k} size=${20} /></span>
        <span class="svc-main"><strong>${kindName(k, true)} <span class="dim">${list.length}</span></strong>
          <small class=${bad ? 'crit' : 'dim'}>${summary}</small></span>
        ${!bad && html`<${Icon} name="chevron" size=${18} cls="dim fold-chev" />`}
      </button>
      ${isOpen && html`<div class="list fold-body">${list.map(row)}</div>`}
    </div>`;
  })}</div>`;
}

export function reasonText(r, ports) {
  return r === 'port' ? t('why.port', { ports: ports.join(', ') }) : t('why.' + r);
}

function Candidates({ ctx, node }) {
  const { cmd, say } = ctx;
  const [busy, setBusy] = useState('');
  const items = node.candidates || [];
  if (!items.length || node.status === 'down') return null;
  const add = async (c) => {
    setBusy(c.unit);
    try { await cmd(node.id, 'svc.add', { unit: c.unit, label: unitName(c.unit), kind: guessKind(c.unit, c.desc) }); say(t('cand.added', { label: unitName(c.unit) })); } catch (e) { say(e.message, 'bad'); }
    setBusy('');
  };
  const dismiss = async (c) => {
    setBusy(c.unit);
    try { await cmd(node.id, 'svc.dismiss', { unit: c.unit }); } catch (e) { say(e.message, 'bad'); }
    setBusy('');
  };
  return html`<div class="cands">
    <p class="dim small">${t('cand.head')}</p>
    ${items.map((c) => html`<div class="cand" key=${c.unit}>
      <span class="svc-main"><strong>${unitName(c.unit)}</strong>
        <small class="dim">${list(c.reasons.map((r) => reasonText(r, c.ports || [])))}</small></span>
      <button class="pill accent" disabled=${!!busy} onClick=${() => add(c)}>${t('cand.watch')}</button>
      <button class="icon-btn sm" disabled=${!!busy} onClick=${() => dismiss(c)} aria-label=${t('cand.dismiss')}><${Icon} name="close" size=${18} /></button>
    </div>`)}
  </div>`;
}

// ---------- node actions ----------

export function runAction(ctx, node, a) {
  const { setSheet, say } = ctx;
  const exec = async () => {
    const r = await ctx.cmdRaw(node.id, 'action.run', { id: a.id }, 640000);
    const out = (r.output || '').trim() || t('act.no_output');
    setSheet({ type: 'log', title: a.name, text: (r.ok ? '' : t('act.failed', { rc: r.rc }) + '\n\n') + out });
    if (r.ok) say(t('act.done', { name: a.name }));
  };
  if (a.confirm) {
    setSheet({ type: 'confirm', title: t('act.confirm', { name: a.name }), text: t('act.confirm.text', { node: node.name }), code: a.command,
      action: t('act.run'), run: exec, keepOpen: true });
  } else {
    say(t('act.running', { name: a.name }));
    exec().catch((e) => say(e.message, 'bad'));
  }
}

function Actions({ ctx, node }) {
  const acts = node.actions || [];
  if (!acts.length) return null;
  const down = node.status === 'down';
  return html`<section class="block">
    <div class="block-head"><h2>${t('act.head')}</h2></div>
    <div class="act-grid">
      ${acts.map((a) => html`<button class="btn" key=${a.id} disabled=${down} onClick=${() => runAction(ctx, node, a)}><${Icon} name="bolt" />${a.name}</button>`)}
    </div>
  </section>`;
}

// ---------- game console ----------

function Game({ ctx, node }) {
  const { cmd, say, setSheet } = ctx;
  const g = node.game;
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState([]);
  const [editQ, setEditQ] = useState(false);
  const down = node.status === 'down';
  const hasGame = node.services.some((s) => s.kind === 'game');
  if (!g) {
    if (!hasGame || down) return null;
    return html`<section class="block">
      <div class="block-head"><h2>${t('game.head')}</h2></div>
      <p class="empty left">${t('game.offer')}</p>
      <button class="btn" onClick=${() => setSheet({ type: 'gameSetup', node: node.id })}><${Icon} name="game" />${t('game.connect')}</button>
    </section>`;
  }
  const mc = g.type === 'minecraft';
  const run = async (command) => {
    const c = (command || text).trim();
    if (!c || busy) return;
    setBusy(true);
    try {
      const r = await cmd(node.id, 'game.cmd', { command: c });
      setOut((o) => [{ c, r: r.output || t('game.silent') }, ...o].slice(0, 6));
      if (!command) setText('');
    } catch (e) { say(e.message, 'bad'); }
    setBusy(false);
  };
  const removeQ = async (i) => {
    try { await cmd(node.id, 'game.quick', { op: 'remove', index: i }); } catch (e) { say(e.message, 'bad'); }
  };
  const quick = g.quick || [];
  const builtin = mc ? [{ label: t('game.mc.list'), command: 'list' }, { label: t('game.mc.save'), command: 'save-all' }] : [];
  return html`<section class="block">
    <div class="block-head"><h2>${mc ? 'Minecraft' : t('game.head')}</h2>
      <button class="link" onClick=${() => setSheet({ type: 'gameSetup', node: node.id })}>${t('game.setup')}</button></div>
    ${g.error ? html`<p class="row-note crit">${t('err.rcon_auth')}</p>` : html`
      <p class="mc-line">${!g.online ? t('game.off') : mc && g.players ? (g.players.length ? t('game.players', { n: g.players.length, max: g.max }) : t('game.nobody')) : t('game.on')}</p>
      ${g.players && g.players.length > 0 && html`<div class="chips">${g.players.map((p) => html`<span class="chip" key=${p}>${p}</span>`)}</div>`}`}
    <form class="console" onSubmit=${(e) => { e.preventDefault(); run(); }}>
      <input value=${text} onInput=${(e) => setText(e.target.value)} placeholder=${mc ? t('game.ph.mc') : t('game.ph')}
        autocapitalize="off" autocomplete="off" spellcheck=${false} enterkeyhint="send" disabled=${down} />
      <button class="icon-btn" disabled=${busy || !text.trim() || down} aria-label=${t('game.send')}><${Icon} name="send" /></button>
    </form>
    <div class="quick">
      ${builtin.map((q) => html`<button class="pill" key=${q.command} disabled=${busy || down} onClick=${() => run(q.command)}>${q.label}</button>`)}
      ${quick.map((q, i) => html`<button class=${'pill' + (editQ ? ' editing' : '')} key=${i + q.command} disabled=${busy || down}
        onClick=${() => (editQ ? removeQ(i) : run(q.command))} title=${q.command}>${editQ && html`<${Icon} name="close" size=${14} />`}${q.label}</button>`)}
      ${!down && html`<button class="pill ghost" onClick=${() => setSheet({ type: 'quickAdd', node: node.id })} aria-label=${t('game.q.add')}><${Icon} name="plus" size=${16} /></button>`}
      ${quick.length > 0 && !down && html`<button class="pill ghost" onClick=${() => setEditQ(!editQ)}>${editQ ? t('game.q.done') : t('game.q.edit')}</button>`}
    </div>
    ${out.map((o, i) => html`<div class="console-out" key=${i + o.c}><b>${o.c}</b><pre>${o.r}</pre></div>`)}
  </section>`;
}

// ---------- backups ----------

function Backups({ ctx, node }) {
  const { now, setSheet } = ctx;
  const items = node.backups || [];
  const down = node.status === 'down';
  if (!items.length) return null;
  const alarm = node.backup_alarm || [];
  return html`<section class="block">
    <div class="block-head"><h2>${t('bk.head')}</h2>
      ${!down && html`<button class="link" onClick=${() => setSheet({ type: 'backupAdd', node: node.id })}>${t('add')}</button>`}</div>
    <div class="list">
      ${items.map((b) => { const late = alarm.includes(b.path); return html`<button class=${'row svc-row' + (late ? ' sv-warn' : '')} key=${b.path}
        onClick=${() => setSheet({ type: 'backup', node: node.id, path: b.path })}>
        <span class="svc-ic"><${Icon} name="archive" /></span>
        <span class="svc-main"><strong>${b.label}</strong>
          <small class=${late ? 'warn-t' : 'dim'}>${b.error ? t('bk.missing') : !b.ts ? t('bk.empty')
            : t('bk.last', { ago: ago(b.ts, now) }) + (b.every ? t('bk.every', { d: dur(b.every, true) }) : '') + (late ? t('bk.late') : '')}</small></span>
        <span class="dim small">${b.size != null && b.ts ? bytes(b.size) : ''}</span>
      </button>`; })}
    </div>
  </section>`;
}

// ---------- maintenance ----------

function Maint({ ctx, node, reboot }) {
  const mt = node.maint;
  if (!mt || node.status === 'down' || (!mt.updates && !mt.reboot)) return null;
  return html`<section class="block">
    <div class="block-head"><h2>${t('mt.head')}</h2></div>
    ${mt.updates > 0 && html`<p>${t('mt.updates', { n: mt.updates })}${mt.security ? t('mt.security', { n: mt.security }) : ''}</p>`}
    ${mt.reboot && html`<div class="mt-reboot"><p>${t('mt.reboot')}<span class="dim">${t('maint.why.' + (mt.why || 'file'))}${(mt.pkgs || []).length ? ': ' + list(mt.pkgs.slice(0, 3)) : ''}</span></p>
      ${!node.pending && html`<button class="link" onClick=${reboot}>${t('mt.reboot.btn')}</button>`}</div>`}
  </section>`;
}

// ---------- crash analysis ----------

function Postmortem({ ctx, node }) {
  const { now, get, cfg, saveCfg } = ctx;
  const pm = node.pm;
  const seen = (cfg.settings.pmSeen || {})[node.id];
  const show = pm && pm.cause && pm.up_at && now - pm.up_at < 2 * 86400 && seen !== pm.up_at;
  const end = pm && (pm.last_ts || pm.down_at);
  const [hist, setHist] = useState(null);
  useEffect(() => {
    if (!show || !end) return undefined;
    let dead = false;
    get('/v1/history?node=' + encodeURIComponent(node.id) + '&from=' + (end - 1800) + '&to=' + end).then((h) => { if (!dead) setHist(h); }).catch(() => {});
    return () => { dead = true; };
  }, [show, end]);
  if (!show) return null;
  const hide = () => saveCfg({ ...cfg, settings: { ...cfg.settings, pmSeen: { ...(cfg.settings.pmSeen || {}), [node.id]: pm.up_at } } });
  const lines = pm.lines || [];
  const hasData = hist && hist.cpu.some((x) => x != null);
  return html`<section class="block pm">
    <div class="block-head"><h2 class=${pm.cause === 'clean' ? '' : 'crit'}>${t('pm.head')}</h2><button class="link" onClick=${hide}>${t('pm.hide')}</button></div>
    <p class="pm-cause">${t('cause.' + pm.cause)}</p>
    <p class="dim small">${pm.down_at ? t('pm.when', { at: clock(pm.down_at), back: clock(pm.up_at), d: dur(pm.up_at - pm.down_at) }) : t('pm.back', { ago: ago(pm.up_at, now) })}</p>
    ${hasData && html`<div class="pm-graphs">
      <div><span class="dim small">${t('m.cpu')}</span><${Spark} data=${hist.cpu} kind="cpu" h=${36} /></div>
      <div><span class="dim small">${t('m.mem')}</span><${Spark} data=${hist.mem} kind="mem" h=${36} /></div>
      <div><span class="dim small">${t('m.disk')}</span><${Spark} data=${hist.disk} kind="disk" h=${36} /></div>
      <p class="dim small">${t('pm.graphs')}</p>
    </div>`}
    ${lines.length > 0 && html`<details class="pm-log"><summary>${t('pm.log', { n: lines.length })}</summary>
      <pre class="log">${lines.map((l) => clock(l[0]) + '  ' + l[1]).join('\n')}</pre></details>`}
  </section>`;
}

// ---------- checking a silent node from neighbours ----------

export function diagVerdict(d) {
  const rs = (d && d.results ? d.results : []).filter((r) => r.ok);
  if (!rs.length) return null;
  if (rs.some((r) => r.tcp && r.tcp.ok)) return 'agent';
  if (rs.some((r) => r.ping && r.ping.recv > 0)) return 'hang';
  return 'dark';
}

function lastHop(r) {
  const hops = (r.trace && r.trace.hops) || [];
  const ok = hops.filter((h) => h.ip && h.ip !== '*');
  return ok.length ? ok[ok.length - 1].ip : null;
}

function tzName(ts) {
  const off = -new Date(ts * 1000).getTimezoneOffset();
  const h = Math.floor(Math.abs(off) / 60);
  const m = Math.abs(off) % 60;
  return 'UTC' + (off >= 0 ? '+' : '-') + h + (m ? ':' + (m < 10 ? '0' : '') + m : '');
}

export function hosterText(node, now) {
  const d = node.diag || {};
  const when = new Date(node.since * 1000);
  const stamp = when.getFullYear() + '-' + String(when.getMonth() + 1).padStart(2, '0') + '-' + String(when.getDate()).padStart(2, '0') + ' ' + clock(node.since) + ' ' + tzName(node.since);
  const out = [t('host.hello'), '', t('host.body', { ip: d.ip || node.addr || '?', at: stamp, d: dur(now - node.since) }), ''];
  const rs = (d.results || []).filter((r) => r.ok);
  if (rs.length) {
    out.push(t('host.checks'));
    rs.forEach((r) => {
      const parts = [];
      if (r.ping) parts.push(t('host.ping', { recv: r.ping.recv, sent: r.ping.sent }));
      if (r.tcp) parts.push(t(r.tcp.ok ? 'host.tcp.ok' : 'host.tcp.bad', { port: r.tcp.port }));
      const hop = lastHop(r);
      if (r.trace) parts.push(hop ? t('host.trace', { hop }) : t('host.trace.none'));
      out.push('— ' + list(parts) + '.');
    });
    out.push('');
  }
  out.push(t('host.ask'));
  const raw = rs.map((r) => (r.raw || '').trim()).filter(Boolean);
  if (raw.length) out.push('', t('host.raw'), '', raw[0]);
  return out.join('\n');
}

function Diag({ ctx, node }) {
  const { post, say, setSheet, now } = ctx;
  if (node.status !== 'down') return null;
  const d = node.diag;
  const again = async () => {
    try { await post('/v1/diag', { node: node.id }); say(t('dg.started')); } catch (e) { say(e.message, 'bad'); }
  };
  const v = diagVerdict(d);
  const rs = d ? d.results || [] : [];
  return html`<section class="block">
    <div class="block-head"><h2>${t('dg.head')}</h2>
      ${!(d && d.running) && html`<button class="link" onClick=${again}>${d ? t('dg.again') : t('dg.run')}</button>`}</div>
    ${!d && html`<p class="empty left">${t('dg.none')}</p>`}
    ${d && d.running && !rs.length && html`<p class="dim">${t('dg.running')}</p>`}
    ${v && html`<p class="dg-verdict"><b>${t('dg.v.' + v)}</b></p>`}
    ${rs.map((r, i) => html`<div class="dg-row" key=${i}>
      <b>${t('dg.from', { name: r.from })}</b>
      ${!r.ok ? html`<span class="dim">${r.error || t('err.generic')}</span>` : html`<span class="dim">${[
        r.ping ? t('dg.ping', { recv: r.ping.recv, sent: r.ping.sent }) + (r.ping.avg != null ? ', ' + num(r.ping.avg, 1) + ' ' + t('u.ms') : '') : null,
        r.tcp ? t(r.tcp.ok ? 'dg.tcp.ok' : 'dg.tcp.bad', { port: r.tcp.port }) : null,
        r.trace ? (lastHop(r) ? t('dg.trace', { hop: lastHop(r) }) : t('dg.trace.none')) : null,
      ].filter(Boolean).join(' · ')}</span>`}
    </div>`)}
    ${d && d.running && rs.length > 0 && html`<p class="dim small">${t('dg.running')}</p>`}
    ${d && !d.running && html`<p class="dim small">${t('dg.at', { ago: ago(d.ts, now) })}</p>`}
    <button class="btn" onClick=${() => setSheet({ type: 'log', title: t('dg.hoster'), text: hosterText(node, now) })}><${Icon} name="mail" />${t('dg.hoster')}</button>
  </section>`;
}

// ---------- node screen ----------

export function NodeScreen({ ctx, node, id }) {
  const { now, setRoute, setSheet, get, post, cmd, say, snap } = ctx;
  const [hist, setHist] = useState(null);
  useEffect(() => {
    let dead = false;
    get('/v1/history?node=' + encodeURIComponent(id) + '&hours=24').then((h) => { if (!dead) setHist(h); }).catch(() => {});
    return () => { dead = true; };
  }, [id]);

  const back = html`<button class="icon-btn" onClick=${() => setRoute({ tab: 'nodes', node: null })} aria-label=${t('back')}><${Icon} name="back" /></button>`;
  if (!node) {
    return html`<div class="page"><div class="bar">${back}<h1 class="disp">${t('node')}</h1></div>
      <p class="empty">${snap ? t('node.gone') : t('home.loading')}</p></div>`;
  }
  const m = node.m || {};
  const muted = isMuted(node, now);
  const down = node.status === 'down';
  const mem = m.mem || [0, 0];
  const swap = m.swap || [0, 0];
  const pend = node.pending;
  const boot = rebootAt(node, now);

  const mute = async (minutes) => {
    try { await post('/v1/mute', { node: node.id, minutes }); say(minutes ? t('mute.on', { name: node.name, d: dur(minutes * 60) }) : t('mute.off')); } catch (e) { say(e.message, 'bad'); }
  };
  const reboot = () => setSheet({ type: 'reboot', node: node.id });
  const cancel = async () => {
    try { await cmd(node.id, 'node.reboot_cancel', {}); say(t('rb.cancelled')); } catch (e) { say(e.message, 'bad'); }
  };
  const forget = () => setSheet({
    type: 'confirm', title: t('forget.q', { name: node.name }), text: t('forget.text'), action: t('forget.btn'), danger: true,
    run: async () => { await post('/v1/forget', { node: node.id }); setRoute({ tab: 'nodes', node: null }); say(t('forget.done')); },
  });

  return html`<div class="page page-node">
    <div class="bar">${back}
      <div class="bar-title"><h1 class="disp">${node.name}</h1><span class="place">${node.place}${node.hub ? t('node.is_hub') : ''}</span></div>
    </div>

    ${boot ? html`<div class="banner banner-reboot">
      <i class="lamp lamp-reboot" />
      <div><b>${t('node.reboot')}</b><span>${t('node.reboot.sub', { ago: ago(boot, now), at: clock(node.muted_until || boot + 600) })}</span></div>
    </div>` : html`<div class=${'banner banner-' + node.status}>
      <i class=${'lamp lamp-' + node.status} />
      <div>
        ${node.status === 'up' && html`<b>${t('node.up', { d: dur(m.uptime || 0) })}</b><span>${t('node.signal', { ago: ago(node.last_seen, now) })}</span>`}
        ${node.status === 'stale' && html`<b>${t('node.stale')}</b><span>${t('node.last', { ago: ago(node.last_seen, now) })}</span>`}
        ${node.status === 'new' && html`<b>${t('row.new')}</b>`}
        ${down && html`<b>${t('node.down', { d: dur(now - node.since) })}</b>
          <span>${t('node.last_at', { at: clock(node.last_seen) })} ${node.confirmed === true ? t('node.both') : node.confirmed === false ? t('node.cut') : ''}</span>`}
        ${muted && html`<span>${t('node.muted', { at: clock(node.muted_until) })}</span>`}
      </div>
    </div>`}
    ${pend && !down && !boot && html`<div class="banner banner-stale pend">
      <${Icon} name="power" />
      <div><b>${pend.mode === 'empty' ? t('rb.pend.empty') : t('rb.pend.warn', { at: clock(pend.at) })}</b>
        <span>${pend.mode === 'empty' ? (pend.left != null ? t('rb.pend.left', { n: pend.left }) + ' ' : '') + t('rb.pend.until', { at: clock(pend.until) }) : t('rb.pend.warned')}</span></div>
      <button class="link" onClick=${cancel}>${t('rb.cancel')}</button>
    </div>`}

    <${Postmortem} ctx=${ctx} node=${node} />
    <${Diag} ctx=${ctx} node=${node} />

    <section class=${'block' + (down ? ' stale-data' : '')}>
      ${down && html`<p class="dim small">${t('node.lastdata')}</p>`}
      <div class="big">
        <div class="big-head"><span>${t('m.cpu')}</span><b>${round(m.cpu || 0)} %</b></div>
        <${Spark} data=${hist && hist.cpu} kind="cpu" />
        <div class="big-foot dim"><span>${t('m.cores', { n: m.cores || '—' })}</span><span>${t('m.load')} ${(m.load || []).map((x) => num(x, 2)).join('  ')}</span></div>
      </div>
      <div class="big">
        <div class="big-head"><span>${t('m.mem')}</span><b>${round(pct(mem[0], mem[1]))} %</b></div>
        <${Spark} data=${hist && hist.mem} kind="mem" />
        <div class="big-foot dim"><span>${t('m.of', { a: bytes(mem[0]), b: bytes(mem[1]) })}</span>${swap[1] > 0 && html`<span>${t('m.swap')} ${t('m.of', { a: bytes(swap[0]), b: bytes(swap[1]) })}</span>`}</div>
      </div>
      <p class="dim small axis">${t('m.day')}</p>
      ${(m.disks || []).map((d) => html`<${Meter} key=${d.mount} label=${t('m.disk') + ' ' + d.mount} value=${pct(d.used, d.total)}
        text=${t('m.of', { a: bytes(d.used), b: bytes(d.total) })} hot=${80} crit=${90} />`)}
      <dl class="facts">
        ${m.temp != null && html`<div><dt>${t('m.temp')}</dt><dd>${round(m.temp)} °C</dd></div>`}
        ${m.net && html`<div><dt>${t('m.rx')}</dt><dd>${rate(m.net[0])}</dd></div>`}
        ${m.net && html`<div><dt>${t('m.tx')}</dt><dd>${rate(m.net[1])}</dd></div>`}
        ${m.os && html`<div><dt>${t('m.os')}</dt><dd>${m.os}</dd></div>`}
        ${m.kernel && html`<div><dt>${t('m.kernel')}</dt><dd>${m.kernel}</dd></div>`}
        ${node.addr && html`<div><dt>${t('m.addr')}</dt><dd class="mono sel">${node.addr}</dd></div>`}
      </dl>
    </section>

    <section class="block">
      <div class="block-head"><h2>${t('svc.head')}</h2>
        <button class="link" disabled=${down} onClick=${() => setSheet({ type: 'addService', node: node.id })}>${t('add')}</button></div>
      ${m.init === 'none' && node.services.length === 0 && html`<p class="empty left">${t('svc.noinit')}</p>`}
      ${m.init !== 'none' && node.services.length === 0 && html`<p class="empty left">${t('svc.none')}</p>`}
      <${SvcFolds} ctx=${ctx} scope=${node.id} items=${node.services.map((s) => ({ n: node, s }))} />
      <${Candidates} ctx=${ctx} node=${node} />
    </section>

    <${Actions} ctx=${ctx} node=${node} />
    <${Game} ctx=${ctx} node=${node} />
    <${Backups} ctx=${ctx} node=${node} />
    <${Maint} ctx=${ctx} node=${node} reboot=${reboot} />

    <${NodeNotify} ctx=${ctx} node=${node} />

    <section class="block actions">
      ${muted
        ? html`<button class="btn" onClick=${() => mute(0)}><${Icon} name="events" />${t('mute.unmute')}</button>`
        : html`<button class="btn" onClick=${() => mute(60)}><${Icon} name="mute" />${t('mute.hour')}</button>`}
      ${!down && !(node.backups || []).length && html`<button class="btn" onClick=${() => setSheet({ type: 'backupAdd', node: node.id })}><${Icon} name="archive" />${t('bk.watch')}</button>`}
      ${!down && !pend && !boot && html`<button class="btn danger" onClick=${reboot}><${Icon} name="power" />${t('rb.btn')}</button>`}
      ${down && html`<button class="btn danger" onClick=${forget}><${Icon} name="trash" />${t('forget.btn.long')}</button>`}
    </section>
    ${!down && !(node.actions || []).length && html`<p class="dim small hint">${t('act.hint')}</p>`}
  </div>`;
}

function NodeNotify({ ctx, node }) {
  const { cfg, saveCfg, setSheet } = ctx;
  const all = cfg.settings.notifyNode || {};
  const own = all[node.id] || {};
  const set = (v) => {
    const nx = { ...all };
    if (Object.keys(v).length) nx[node.id] = v; else delete nx[node.id];
    saveCfg({ ...cfg, settings: { ...cfg.settings, notifyNode: nx } });
  };
  return html`<${Group} title=${t('nn.head')} foot=${t('nn.text', { name: node.name })}>
    <${NotifyRows} levels=${own} onChange=${set} inherit base=${cfg.settings.notify || {}} setSheet=${setSheet} />
  </${Group}>`;
}

// ---------- who is affected ----------

export function impactText(imp) {
  if (!imp) return '';
  const parts = [];
  if (imp.players && imp.players.length) parts.push(t('imp.players', { n: imp.players.length, names: list(imp.players.slice(0, 5)) }));
  (imp.conns || []).forEach((c) => { if (c.n) parts.push(t('imp.conns', { n: c.n, label: c.label })); });
  (imp.tunnels || []).forEach((x) => { if (x.n) parts.push(t('imp.tunnel', { n: x.n, iface: x.iface })); });
  return parts.length ? t('imp.now', { what: list(parts) }) : t('imp.nobody');
}

export function useImpact(ctx, nodeId, unit, on) {
  const [imp, setImp] = useState(undefined);
  useEffect(() => {
    if (!on) return undefined;
    let dead = false;
    ctx.cmd(nodeId, 'impact', unit ? { unit } : {}, 75000).then((r) => { if (!dead) setImp(r.impact || null); }).catch(() => { if (!dead) setImp(null); });
    return () => { dead = true; };
  }, [nodeId, unit, on]);
  return imp;
}

