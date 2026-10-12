import { useState, useEffect } from 'preact/hooks';
import { html } from 'htm/preact';
import { native, hubReq, parseKey } from './api.js';
import { Icon } from './icons.js';
import { t, dur, ago, bytes, clock, list } from './i18n.js';
import { unitName, guessKind, kindName, KINDS } from './fmt.js';
import { reasonText, impactText, useImpact, runAction } from './node.js';
import { markReboot } from './home.js';

function Head({ title, sub, close }) {
  return html`<div class="sheet-head">
    <div><h2 class="disp">${title}</h2>${sub && html`<p class="dim">${sub}</p>`}</div>
    <button class="icon-btn" onClick=${close} aria-label=${t('close')}><${Icon} name="close" /></button>
  </div>`;
}

function KindPicker({ value, onChange }) {
  return html`<div class="kinds">${KINDS.map((k) => html`<button type="button" key=${k}
    class=${'kind-btn' + (value === k ? ' on' : '')} onClick=${() => onChange(k)}>
    <span class=${'svc-ic kind-' + k}><${Icon} name=${k} size=${20} /></span>${kindName(k)}</button>`)}</div>`;
}

function findNode(ctx, id) { return ctx.snap && ctx.snap.nodes.find((n) => n.id === id); }

function Impact({ imp }) {
  if (imp === undefined) return html`<p class="imp dim">${t('imp.checking')}</p>`;
  if (imp === null) return null;
  return html`<p class=${'imp' + (imp.total ? ' warn-t' : ' dim')}>${impactText(imp)}</p>`;
}

// ---------- service ----------

export function ServiceSheet({ ctx, nodeId, unit, close }) {
  const { now, cmd, say, setSheet } = ctx;
  const node = findNode(ctx, nodeId);
  const s = node && node.services.find((x) => x.unit === unit);
  const [busy, setBusy] = useState('');
  const [edit, setEdit] = useState(null);
  if (!node || !s) return html`<${Head} title=${t('svc.one')} close=${close} /><p class="empty">${t('svc.gone')}</p>`;
  const down = node.status === 'down';
  const docker = s.unit.startsWith('docker:');
  const back = { type: 'service', node: nodeId, unit };
  const act = async (type, doing, done) => {
    setBusy(doing);
    try { await cmd(node.id, type, { unit: s.unit }, 75000); say(done); } catch (e) { say(e.message, 'bad'); }
    setBusy('');
  };
  const restart = async () => {
    if (s.state !== 'active') { act('svc.restart', t('svc.restarting'), t('svc.restarted', { label: s.label })); return; }
    setBusy(t('imp.checking.short'));
    let imp = null;
    try { imp = (await cmd(node.id, 'impact', { unit: s.unit }, 75000)).impact; } catch (e) { /* agent 1.0 can't, restart as before */ }
    setBusy('');
    if (imp && imp.total > 0) {
      setSheet({ type: 'confirm', title: t('svc.restart.q', { label: s.label }), text: impactText(imp), action: t('svc.restart'), danger: true, back,
        run: async () => { await cmd(node.id, 'svc.restart', { unit: s.unit }, 75000); say(t('svc.restarted', { label: s.label })); } });
    } else act('svc.restart', t('svc.restarting'), t('svc.restarted', { label: s.label }));
  };
  const stop = () => setSheet({
    type: 'confirm', title: t('svc.stop.q', { label: s.label }), text: t('svc.stop.text'), action: t('svc.stop'), danger: true, back,
    impact: { node: nodeId, unit: s.unit },
    run: async () => { await cmd(node.id, 'svc.stop', { unit: s.unit }, 75000); say(t('svc.stopped', { label: s.label })); },
  });
  const remove = () => setSheet({
    type: 'confirm', title: t('svc.remove.q', { label: s.label }), text: t('svc.remove.text'), action: t('svc.remove'), danger: true,
    run: async () => { await cmd(node.id, 'svc.remove', { unit: s.unit }); say(t('svc.removed')); },
  });
  const logs = () => setSheet({
    type: 'log', title: t('svc.log.title', { label: s.label }), back,
    load: async () => (await cmd(node.id, 'svc.logs', { unit: s.unit, lines: 80 })).output || t('svc.log.empty'),
  });
  const save = async (e) => {
    e.preventDefault();
    setBusy(t('saving'));
    try { await cmd(node.id, 'svc.edit', { unit: s.unit, label: edit.label.trim() || s.label, kind: edit.kind, link: edit.link.trim() }); setEdit(null); say(t('saved')); } catch (er) { say(er.message, 'bad'); }
    setBusy('');
  };

  if (edit) {
    return html`<${Head} title=${t('svc.label.title')} sub=${s.unit} close=${() => setEdit(null)} />
      <form class="form" onSubmit=${save}>
        <label>${t('f.name')}<input value=${edit.label} onInput=${(e) => setEdit({ ...edit, label: e.target.value })} maxlength="40" /></label>
        <label>${t('f.kind')}</label><${KindPicker} value=${edit.kind} onChange=${(k) => setEdit({ ...edit, kind: k })} />
        <label>${t('f.link')}<input value=${edit.link} onInput=${(e) => setEdit({ ...edit, link: e.target.value })}
          placeholder=${t('f.link.ph')} inputmode="url" autocapitalize="off" spellcheck=${false} /></label>
        <button class="btn primary" disabled=${!!busy}>${busy || t('save')}</button>
      </form>`;
  }

  const state = s.state;
  const bootMiss = (node.boot_missing || []).includes(s.unit) && state !== 'active';
  const stateKey = { active: 'svc.st.active', failed: 'svc.st.failed', inactive: 'svc.st.inactive', activating: 'svc.st.activating', deactivating: 'svc.st.deactivating', missing: 'svc.st.missing' }[state] || 'svc.unknown';
  const acts = (node.actions || []).filter((a) => a.service === s.unit);
  return html`<${Head} title=${s.label} sub=${t('svc.on', { kind: kindName(s.kind), node: node.name })} close=${close} />
    <div class=${'svc-state sv-' + (state === 'active' ? 'ok' : state === 'failed' || state === 'missing' || bootMiss ? 'crit' : 'off')}>
      <b>${down ? t('svc.node_down') : t(stateKey)}</b>
      <span>${down ? t('svc.node_down.sub') : bootMiss ? t('svc.boot_missing') : s.since ? (state === 'active' ? t('svc.for', { d: dur(now - s.since) }) : ago(s.since, now)) : ''}</span>
    </div>
    <dl class="facts">
      <div><dt>${docker ? t('svc.container') : t('svc.unit')}</dt><dd class="mono sel">${docker ? s.unit.slice(7) : s.unit}</dd></div>
      ${s.mem != null && html`<div><dt>${t('m.mem')}</dt><dd>${bytes(s.mem)}</dd></div>`}
      ${s.restarts > 0 && html`<div><dt>${docker ? t('svc.restarts.docker') : t('svc.restarts')}</dt><dd>${s.restarts}</dd></div>`}
    </dl>
    <div class="sheet-actions">
      ${state === 'active' || state === 'failed' || state === 'activating'
        ? html`<button class="btn primary" disabled=${!!busy || down} onClick=${restart}><${Icon} name="restart" />${busy || t('svc.restart')}</button>`
        : html`<button class="btn primary" disabled=${!!busy || down} onClick=${() => act('svc.start', t('svc.starting'), t('svc.started', { label: s.label }))}><${Icon} name="play" />${busy || t('svc.start')}</button>`}
      <div class="pair">
        <button class="btn" disabled=${down} onClick=${logs}><${Icon} name="log" />${t('svc.log')}</button>
        ${state === 'active' || state === 'activating'
          ? html`<button class="btn" disabled=${!!busy || down} onClick=${stop}><${Icon} name="stop" />${t('svc.stop')}</button>`
          : html`<button class="btn" disabled=${!!busy || down || state === 'inactive'} onClick=${() => act('svc.start', t('svc.starting'), t('svc.started', { label: s.label }))}><${Icon} name="play" />${t('svc.start')}</button>`}
      </div>
      ${acts.map((a) => html`<button class="btn" key=${a.id} disabled=${down} onClick=${() => runAction(ctx, node, a)}><${Icon} name="bolt" />${a.name}</button>`)}
      ${s.link && html`<button class="btn" onClick=${() => native('open', { url: /^[a-z]+:/i.test(s.link) ? s.link : 'https://' + s.link })}><${Icon} name="link" />${t(s.kind === 'bot' ? 'svc.open.bot' : s.kind === 'panel' ? 'svc.open.panel' : 'svc.open')}</button>`}
    </div>
    <div class="sheet-foot">
      <button class="link" disabled=${down} onClick=${() => setEdit({ label: s.label, kind: s.kind, link: s.link || '' })}>${t('svc.edit')}</button>
      <button class="link crit" disabled=${down} onClick=${remove}>${t('svc.remove.long')}</button>
    </div>`;
}

// ---------- adding services: suggestions with reasons ----------

export function AddServiceSheet({ ctx, nodeId, close }) {
  const { cmd, say } = ctx;
  const node = findNode(ctx, nodeId);
  const [units, setUnits] = useState(null);
  const [sel, setSel] = useState(new Set());
  const [err, setErr] = useState('');
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let dead = false;
    (async () => {
      let u;
      try { u = (await cmd(nodeId, 'svc.suggest', {}, 75000)).units || []; } catch (e) {
        try { u = ((await cmd(nodeId, 'svc.discover', {})).units || []).map((x) => ({ ...x, reasons: [], ports: [], recommended: false, monitored: false })); } catch (e2) { if (!dead) setErr(e2.message); return; }
      }
      if (dead) return;
      setUnits(u);
      setSel(new Set(u.filter((x) => x.recommended && !x.monitored).map((x) => x.unit)));
    })();
    return () => { dead = true; };
  }, [nodeId]);
  if (!node) return html`<${Head} title=${t('pick.title')} close=${close} />`;
  const have = new Set(node.services.map((s) => s.unit));
  const needle = q.trim().toLowerCase();
  const all = (units || []).filter((u) => !u.monitored && !have.has(u.unit) && (!needle || (u.unit + ' ' + (u.desc || '') + ' ' + unitName(u.unit)).toLowerCase().includes(needle)));
  const rec = all.filter((u) => u.recommended);
  const rest = all.filter((u) => !u.recommended);
  const toggle = (u) => { const n = new Set(sel); if (n.has(u)) n.delete(u); else n.add(u); setSel(n); };
  const add = async () => {
    const items = (units || []).filter((u) => sel.has(u.unit)).map((u) => ({ unit: u.unit, label: unitName(u.unit), kind: guessKind(u.unit, u.desc) }));
    setBusy(true);
    try {
      const r = await cmd(nodeId, 'svc.add_many', { items }, 75000);
      if (r.errors && r.errors.length) say(t('pick.partial', { n: r.added, err: r.errors[0].error }), 'bad');
      else say(t('pick.done', { n: r.added }));
      close();
    } catch (e) { say(e.message, 'bad'); setBusy(false); }
  };
  const row = (u) => html`<button class=${'row pick-row' + (sel.has(u.unit) ? ' on' : '')} key=${u.unit} onClick=${() => toggle(u.unit)} role="checkbox" aria-checked=${sel.has(u.unit)}>
    <i class="check"><${Icon} name="check" size=${16} /></i>
    <span class="svc-main"><strong>${unitName(u.unit)}</strong>
      <small class="dim"><span class="mono">${u.unit.replace(/\.service$/, '')}</span>${(u.reasons || []).length ? ' · ' + list(u.reasons.map((r) => reasonText(r, u.ports || []))) : u.desc ? ' · ' + u.desc : ''}</small></span>
    ${u.state !== 'active' && html`<span class=${u.state === 'failed' ? 'crit small' : 'dim small'}>${t(u.state === 'failed' ? 'svc.failed' : 'svc.inactive')}</span>`}
  </button>`;
  return html`<${Head} title=${t('pick.title')} sub=${t('pick.sub', { node: node.name })} close=${close} />
    <label class="search"><${Icon} name="search" size=${20} />
      <input value=${q} onInput=${(e) => setQ(e.target.value)} placeholder=${t('pick.search')} autocapitalize="off" autocomplete="off" spellcheck=${false} /></label>
    ${err && html`<p class="crit">${err}</p>`}
    ${!units && !err && html`<p class="empty">${t('pick.loading')}</p>`}
    ${units && all.length === 0 && html`<p class="empty">${needle ? t('pick.nothing') : t('pick.all')}</p>`}
    <div class="list scroll">
      ${rec.length > 0 && html`<p class="list-cap">${t('pick.rec')}</p>`}
      ${rec.map(row)}
      ${rest.length > 0 && html`<p class="list-cap">${rec.length ? t('pick.rest') : t('pick.found')}</p>`}
      ${rest.map(row)}
    </div>
    ${units && html`<button class="btn primary" disabled=${busy || !sel.size} onClick=${add}>${busy ? t('adding') : sel.size ? t('pick.add', { n: sel.size }) : t('pick.choose')}</button>`}`;
}

// ---------- game console ----------

export function GameSetupSheet({ ctx, nodeId, close }) {
  const { cmd, say } = ctx;
  const node = findNode(ctx, nodeId);
  const g = node && node.game;
  const cur = (g && g.cfg) || {};
  const [f, setF] = useState({ type: (g && g.type) || 'minecraft', host: cur.host || '127.0.0.1', port: String(cur.port || 25575), password: '' });
  const [busy, setBusy] = useState(false);
  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await cmd(nodeId, 'game.setup', { type: f.type, host: f.host.trim(), port: +f.port || 25575, password: f.password }, 40000);
      say(t('game.connected')); close();
    } catch (er) { say(er.message, 'bad'); setBusy(false); }
  };
  const off = async () => {
    setBusy(true);
    try { await cmd(nodeId, 'game.disable', {}); say(t('game.disabled')); close(); } catch (er) { say(er.message, 'bad'); setBusy(false); }
  };
  return html`<${Head} title=${t('game.head')} sub=${node ? t('game.rcon_on', { node: node.name }) : ''} close=${close} />
    <form class="form" onSubmit=${save}>
      <div class="seg two">
        ${[['minecraft', 'Minecraft'], ['rcon', t('game.other')]].map(([k, v]) => html`<button type="button" key=${k} class=${f.type === k ? 'on' : ''} onClick=${() => setF({ ...f, type: k })}>${v}</button>`)}
      </div>
      ${f.type === 'rcon' && html`<p class="dim small">${t('game.other.note')}</p>`}
      <div class="pair">
        <label>${t('game.host')}<input value=${f.host} onInput=${(e) => setF({ ...f, host: e.target.value })} autocapitalize="off" spellcheck=${false} /></label>
        <label>${t('game.port')}<input value=${f.port} onInput=${(e) => setF({ ...f, port: e.target.value })} inputmode="numeric" /></label>
      </div>
      <label>${t('game.pass')}<input type="password" value=${f.password} onInput=${(e) => setF({ ...f, password: e.target.value })}
        placeholder=${g ? t('game.pass.keep') : f.type === 'minecraft' ? t('game.pass.mc') : ''} autocomplete="off" /></label>
      <p class="dim small">${t('game.pass.note')}</p>
      <button class="btn primary" disabled=${busy}>${busy ? t('checking') : t('game.save')}</button>
      ${g && html`<button type="button" class="link crit" disabled=${busy} onClick=${off}>${t('game.disable')}</button>`}
    </form>`;
}

export function QuickAddSheet({ ctx, nodeId, close }) {
  const { cmd, say } = ctx;
  const [f, setF] = useState({ label: '', command: '' });
  const [busy, setBusy] = useState(false);
  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try { await cmd(nodeId, 'game.quick', { op: 'add', label: f.label.trim(), command: f.command.trim() }); say(t('saved')); close(); } catch (er) { say(er.message, 'bad'); setBusy(false); }
  };
  return html`<${Head} title=${t('game.q.title')} sub=${t('game.q.sub')} close=${close} />
    <form class="form" onSubmit=${save}>
      <label>${t('game.q.label')}<input value=${f.label} onInput=${(e) => setF({ ...f, label: e.target.value })} maxlength="30" placeholder=${t('game.q.label.ph')} /></label>
      <label>${t('game.q.cmd')}<input value=${f.command} onInput=${(e) => setF({ ...f, command: e.target.value })} maxlength="256"
        placeholder="time set night" autocapitalize="off" spellcheck=${false} class="mono" /></label>
      <button class="btn primary" disabled=${busy || !f.command.trim()}>${t('save')}</button>
    </form>`;
}

// ---------- reboot ----------

export function RebootSheet({ ctx, nodeId, close }) {
  const { cmd, say } = ctx;
  const node = findNode(ctx, nodeId);
  const imp = useImpact(ctx, nodeId, null, true);
  const mc = node && node.game && node.game.type === 'minecraft' && node.game.online;
  const [mode, setMode] = useState('now');
  const [hours, setHours] = useState(3);
  const [say5, setSay5] = useState(t('rb.say5'));
  const [say1, setSay1] = useState(t('rb.say1'));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  if (!node) return html`<${Head} title=${t('rb.title')} close=${close} />`;
  const go = async () => {
    setBusy(true); setErr('');
    try {
      await cmd(nodeId, 'node.reboot', { mode, hours, say5, say1 });
      if (mode === 'now') markReboot(nodeId);
      say(mode === 'now' ? t('rb.now.done', { name: node.name }) : mode === 'warn' ? t('rb.warn.done') : t('rb.empty.done'));
      close();
    } catch (e) { setErr(e.message); setBusy(false); }
  };
  const opt = (k, title, text) => html`<button class=${'choice' + (mode === k ? ' on' : '')} onClick=${() => setMode(k)} role="radio" aria-checked=${mode === k}>
    <i class="radio" /><span><b>${title}</b><small>${text}</small></span></button>`;
  return html`<${Head} title=${t('rb.title.q', { name: node.name })} close=${close} />
    <${Impact} imp=${imp} />
    <div>
      ${opt('now', t('rb.now'), t('rb.now.text'))}
      ${mc && opt('warn', t('rb.warn'), t('rb.warn.text'))}
      ${opt('empty', t('rb.empty'), t('rb.empty.text'))}
    </div>
    ${mode === 'warn' && html`<div class="form">
      <label>${t('rb.say5.label')}<input value=${say5} onInput=${(e) => setSay5(e.target.value)} maxlength="200" /></label>
      <label>${t('rb.say1.label')}<input value=${say1} onInput=${(e) => setSay1(e.target.value)} maxlength="200" /></label>
    </div>`}
    ${mode === 'empty' && html`<div><p class="dim small">${t('rb.hours')}</p><div class="seg five">
      ${[1, 3, 6, 12, 24].map((h) => html`<button key=${h} class=${hours === h ? 'on' : ''} onClick=${() => setHours(h)}>${t('u.h', { n: h })}</button>`)}
    </div></div>`}
    ${err && html`<p class="crit">${err}</p>`}
    <div class="pair">
      <button class="btn" disabled=${busy} onClick=${close}>${t('cancel')}</button>
      <button class="btn danger solid" disabled=${busy} onClick=${go}>${busy ? t('doing') : mode === 'now' ? t('rb.go') : t('rb.plan')}</button>
    </div>`;
}

// ---------- backups ----------

export function BackupAddSheet({ ctx, nodeId, close }) {
  const { cmd, say, now } = ctx;
  const node = findNode(ctx, nodeId);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState('');
  const [f, setF] = useState({ path: '', label: '' });
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let dead = false;
    cmd(nodeId, 'backup.suggest', {}, 75000).then((r) => { if (!dead) setRes(r); }).catch((e) => { if (!dead) setErr(e.message); });
    return () => { dead = true; };
  }, [nodeId]);
  const add = async (path, label) => {
    setBusy(true);
    try { await cmd(nodeId, 'backup.add', { path, label }); say(t('bk.added')); close(); } catch (e) { say(e.message, 'bad'); setBusy(false); }
  };
  return html`<${Head} title=${t('bk.add.title')} sub=${node ? t('bk.add.sub', { node: node.name }) : ''} close=${close} />
    ${!res && !err && html`<p class="empty">${t('bk.searching')}</p>`}
    ${err && html`<p class="crit">${err}</p>`}
    ${res && html`<div class="list scroll">
      ${res.dirs.length === 0 && html`<p class="dim">${t('bk.nothing')}</p>`}
      ${res.dirs.map((d) => html`<button class="row svc-row" key=${d.path} disabled=${busy} onClick=${() => add(d.path, d.label)}>
        <span class="svc-ic"><${Icon} name="archive" /></span>
        <span class="svc-main"><strong class="mono">${d.path}</strong>
          <small class="dim">${t('bk.found', { n: d.count, ago: d.ts ? ago(d.ts, now) : '—' })}${d.every ? t('bk.every', { d: dur(d.every, true) }) : ''}</small></span>
        <${Icon} name="plus" size=${18} cls="dim" />
      </button>`)}
      ${res.hints.length > 0 && html`<p class="list-cap">${t('bk.hints')}</p><pre class="log hints">${res.hints.join('\n')}</pre>`}
    </div>`}
    <form class="form" onSubmit=${(e) => { e.preventDefault(); add(f.path.trim(), f.label.trim()); }}>
      <label>${t('bk.path')}<input value=${f.path} onInput=${(e) => setF({ ...f, path: e.target.value })} placeholder="/var/backups/site" autocapitalize="off" spellcheck=${false} class="mono" /></label>
      <label>${t('f.name')}<input value=${f.label} onInput=${(e) => setF({ ...f, label: e.target.value })} maxlength="40" placeholder=${t('f.optional')} /></label>
      <button class="btn primary" disabled=${busy || !f.path.trim().startsWith('/')}>${t('bk.watch')}</button>
    </form>
    <p class="dim small">${t('bk.note')}</p>`;
}

export function BackupSheet({ ctx, nodeId, path, close }) {
  const { cmd, say, now, setSheet } = ctx;
  const node = findNode(ctx, nodeId);
  const b = node && (node.backups || []).find((x) => x.path === path);
  if (!b) return html`<${Head} title=${t('bk.head')} close=${close} /><p class="empty">${t('bk.gone')}</p>`;
  const late = (node.backup_alarm || []).includes(b.path);
  const remove = () => setSheet({
    type: 'confirm', title: t('bk.remove.q', { label: b.label }), text: t('bk.remove.text'), action: t('bk.remove'), danger: true,
    run: async () => { await cmd(nodeId, 'backup.remove', { path: b.path }); say(t('bk.removed')); },
  });
  return html`<${Head} title=${b.label} sub=${node.name} close=${close} />
    <div class=${'svc-state sv-' + (late ? 'crit' : 'ok')}>
      <b>${b.ts ? t('bk.last.cap', { ago: ago(b.ts, now) }) : t('bk.empty')}</b>
      <span>${b.every ? t('bk.usually', { d: dur(b.every) }) + (late ? t('bk.late') : '') : t('bk.learning')}</span>
    </div>
    <dl class="facts">
      <div><dt>${t('bk.path')}</dt><dd class="mono sel">${b.path}</dd></div>
      ${b.ts && html`<div><dt>${t('bk.when')}</dt><dd>${clock(b.ts)}</dd></div>`}
      ${b.size != null && b.ts && html`<div><dt>${t('bk.size')}</dt><dd>${bytes(b.size)}</dd></div>`}
      <div><dt>${t('bk.count')}</dt><dd>${b.count}</dd></div>
    </dl>
    <button class="btn danger" onClick=${remove}><${Icon} name="trash" />${t('bk.remove')}</button>`;
}

// ---------- confirmation ----------

export function ConfirmSheet({ ctx, title, text, code, action, danger, run, close, impact, keepOpen }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const imp = useImpact(ctx, impact && impact.node, impact && impact.unit, !!impact);
  const go = async () => {
    setBusy(true); setErr('');
    try { await run(); if (!keepOpen) close(); } catch (e) { setErr(e.message || String(e)); setBusy(false); }
  };
  return html`<${Head} title=${title} close=${close} />
    ${text && html`<p class="confirm-text">${text}</p>`}
    ${code && html`<pre class="log code">${code}</pre>`}
    ${impact && html`<${Impact} imp=${imp} />`}
    ${err && html`<p class="crit">${err}</p>`}
    <div class="pair">
      <button class="btn" disabled=${busy} onClick=${close}>${t('cancel')}</button>
      <button class=${'btn ' + (danger ? 'danger solid' : 'primary')} disabled=${busy} onClick=${go}>${busy ? t('doing') : action}</button>
    </div>`;
}

// ---------- new watchman ----------

export function AddHubSheet({ ctx, close }) {
  const { cfg, saveCfg, say } = ctx;
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const paste = async () => { const v = await native('clipboard.read'); if (v) { setText(v); setErr(''); } else say(t('clip.empty')); };
  const add = async () => {
    const hub = parseKey(text);
    if (!hub) { setErr(t('key.bad')); return; }
    if (cfg.hubs.some((h) => h.url === hub.url)) { setErr(t('key.dup')); return; }
    setBusy(true); setErr('');
    try {
      const r = await hubReq(hub, 'GET', '/v1/ping', null, 10000);
      hub.name = r.name || hub.name;
      saveCfg({ ...cfg, hubs: [...cfg.hubs, hub] });
      say(t('hub.added', { name: hub.name || '' })); close();
    } catch (e) { setErr(e.status ? e.message + '.' : t('hub.silent')); setBusy(false); }
  };
  return html`<${Head} title=${t('hub.new')} sub=${t('hub.new.sub')} close=${close} />
    <textarea class="key" rows="4" value=${text} onInput=${(e) => { setText(e.target.value); setErr(''); }} placeholder="laputa1:…"
      autocapitalize="off" autocomplete="off" spellcheck=${false} />
    ${err && html`<p class="crit small">${err}</p>`}
    <div class="pair">
      <button class="btn" onClick=${paste}><${Icon} name="paste" />${t('paste')}</button>
      <button class="btn primary" disabled=${busy || !text.trim()} onClick=${add}>${busy ? t('checking') : t('connect')}</button>
    </div>`;
}

// ---------- log and any long text ----------

export function LogSheet({ title, text, load, close }) {
  const [body, setBody] = useState(text != null ? text : null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let dead = false;
    if (load) load().then((v) => { if (!dead) setBody(String(v || '')); }).catch((e) => { if (!dead) setErr(e.message || String(e)); });
    return () => { dead = true; };
  }, []);
  return html`<${Head} title=${title} close=${close} />
    ${err && html`<p class="crit">${err}</p>`}
    ${body == null && !err && html`<p class="empty">${t('loading')}</p>`}
    ${body != null && html`<pre class="log">${body}</pre>`}
    ${body != null && html`<button class="btn" onClick=${() => native('clipboard.write', { text: body })}><${Icon} name="paste" />${t('copy')}</button>`}`;
}

// ---------- single choice ----------

export function ChoiceSheet({ title, options, value, onPick, close }) {
  return html`<${Head} title=${title} close=${close} />
    <div class="choices">${options.map((o) => html`<button key=${o.k} class=${'choice' + (value === o.k ? ' on' : '')} role="radio" aria-checked=${value === o.k}
      onClick=${() => { onPick(o.k); close(); }}>
      <i class="radio" /><span><b>${o.title}</b>${o.text && html`<small>${o.text}</small>`}</span>
      ${o.swatch && html`<span class="swatch">${o.swatch.map((c, i) => html`<i key=${i} style=${'background:' + c} />`)}</span>`}
    </button>`)}</div>`;
}
