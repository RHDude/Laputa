import { useState, useEffect, useRef } from 'preact/hooks';
import { html } from 'htm/preact';
import { native, hubReq, parseKey } from './api.js';
import { Icon, Island } from './icons.js';
import { t, clock, day, evText, LANGS } from './i18n.js';
import { kindName, KINDS, EV_KINDS, evKind } from './fmt.js';
import { THEMES, monetSwatch } from './theme.js';
import { Needs } from './home.js';
import { ServiceRow, svcSev, SvcFolds } from './node.js';

// ---------- services ----------

export function ServicesTab({ ctx }) {
  const { snap, now, setSheet } = ctx;
  const [q, setQ] = useState('');
  const all = [];
  (snap ? snap.nodes : []).forEach((n) => n.services.forEach((s) => all.push({ n, s })));
  const needle = q.trim().toLowerCase();
  const shown = needle
    ? all.filter(({ n, s }) => (s.label + ' ' + s.unit + ' ' + n.name + ' ' + kindName(s.kind)).toLowerCase().includes(needle))
    : all;
  const bad = shown.filter(({ n, s }) => n.status !== 'down' && svcSev(s, n) === 'crit');
  const groups = KINDS.map((k) => [k, shown.filter(({ s }) => s.kind === k && !bad.some((b) => b.s === s))]).filter((g) => g[1].length);
  const row = ({ n, s }) => html`<${ServiceRow} key=${n.id + s.unit} s=${n.status === 'down' ? { ...s, state: 'unknown' } : s} node=${n} now=${now} showNode
    onClick=${() => setSheet({ type: 'service', node: n.id, unit: s.unit })} />`;
  return html`<div class="page">
    <div class="bar"><h1 class="disp">${t('tab.services')}</h1></div>
    <label class="search"><${Icon} name="search" size=${20} />
      <input value=${q} onInput=${(e) => setQ(e.target.value)} placeholder=${t('svcs.search')} autocapitalize="off" autocomplete="off" spellcheck=${false} /></label>
    ${all.length === 0 && html`<p class="empty">${t('svcs.none')}</p>`}
    ${all.length > 0 && shown.length === 0 && html`<p class="empty">${t('svcs.nothing', { q })}</p>`}
    ${bad.length > 0 && html`<section class="block"><div class="block-head"><h2 class="crit">${t('svcs.bad')}</h2></div><div class="list">${bad.map(row)}</div></section>`}
    ${groups.length > 0 && html`<section class="block"><${SvcFolds} ctx=${ctx} scope="all" showNode flatUpTo=${needle ? 999 : 0}
      items=${groups.reduce((a, g) => a.concat(g[1]), [])} /></section>`}
  </div>`;
}

// ---------- events ----------

export function EventsTab({ ctx }) {
  const { snap, now, setRoute, cfg, saveCfg } = ctx;
  const [q, setQ] = useState('');
  const f = cfg.settings.evFilter || {};
  const hideK = f.kinds || [];
  const hideN = f.nodes || [];
  const setF = (patch) => saveCfg({ ...cfg, settings: { ...cfg.settings, evFilter: { kinds: hideK, nodes: hideN, ...patch } } });
  const toggle = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const nodes = snap ? snap.nodes : [];
  const all = snap ? snap.events.slice().sort((a, b) => b.seq - a.seq).map((e) => ({ e, x: evText(e) })) : [];
  const needle = q.trim().toLowerCase();
  const shown = all.filter(({ e, x }) => !hideK.includes(evKind(e.type)) && !(e.node && hideN.includes(e.node))
    && (!needle || (x.title + ' ' + x.text).toLowerCase().includes(needle)));
  const hidden = all.length - shown.length;
  const days = [];
  shown.forEach((it) => {
    const d = day(it.e.ts, now);
    if (!days.length || days[days.length - 1][0] !== d) days.push([d, []]);
    days[days.length - 1][1].push(it);
  });
  return html`<div class="page">
    <div class="bar"><h1 class="disp">${t('tab.events')}</h1></div>
    ${all.length > 0 && html`<div class="evf">
      <label class="search"><${Icon} name="search" size=${20} />
        <input value=${q} onInput=${(e) => setQ(e.target.value)} placeholder=${t('evf.search')} autocapitalize="off" autocomplete="off" spellcheck=${false} /></label>
      <div class="chips-row">${EV_KINDS.map((k) => html`<button key=${k} class=${'fchip' + (hideK.includes(k) ? '' : ' on')} onClick=${() => setF({ kinds: toggle(hideK, k) })}>${t('evk.' + k)}</button>`)}</div>
      ${nodes.length > 1 && html`<div class="chips-row">${nodes.map((n) => html`<button key=${n.id} class=${'fchip' + (hideN.includes(n.id) ? '' : ' on')} onClick=${() => setF({ nodes: toggle(hideN, n.id) })}>${n.name}</button>`)}</div>`}
      ${hidden > 0 && html`<p class="dim small evf-hidden">${t('evf.hidden', { n: hidden })}
        ${(hideK.length > 0 || hideN.length > 0) && html` · <button class="link" onClick=${() => { setQ(''); setF({ kinds: [], nodes: [] }); }}>${t('evf.reset')}</button>`}</p>`}
    </div>`}
    ${all.length === 0 && html`<p class="empty">${t('ev.none')}</p>`}
    ${all.length > 0 && shown.length === 0 && html`<p class="empty">${t('evf.none')}</p>`}
    ${days.map(([d, items]) => html`<section class="block" key=${d}>
      <div class="block-head"><h2>${d}</h2></div>
      <ol class="timeline">
        ${items.map(({ e, x }) => html`<li key=${e.seq} class=${'ev ev-' + e.sev} onClick=${() => e.node && setRoute({ tab: 'nodes', node: e.node })}>
          <i class="ev-mark" /><time>${clock(e.ts)}</time>
          <div><b>${x.title}</b>${x.text && html`<p>${x.text}</p>`}</div>
        </li>`)}
      </ol>
    </section>`)}
  </div>`;
}

// Grouped settings: a card of 'name — value ›' rows, choices in a sheet.
export function Group({ title, foot, children }) {
  return html`<section class="block">
    ${title && html`<div class="block-head"><h2>${title}</h2></div>`}
    <div class="card">${children}</div>
    ${foot && html`<p class="dim small card-foot">${foot}</p>`}
  </section>`;
}

export function Row({ label, sub, value, onClick, cls, lead, chevron = true }) {
  return html`<button class=${'srow ' + (cls || '')} onClick=${onClick}>
    ${lead}
    <span class="srow-main"><span>${label}</span>${sub && html`<small class="dim">${sub}</small>`}</span>
    ${value != null && html`<span class="srow-val">${value}</span>`}
    ${chevron && html`<${Icon} name="chevron" size=${18} cls="dim" />`}
  </button>`;
}

export function pick(setSheet, title, options, value, onPick) {
  setSheet({ type: 'choice', title, options, value, onPick });
}

// What to notify about: general and per node. Stored in the app; the watchman does not know about it.
export function NotifyRows({ levels, onChange, inherit, base, setSheet }) {
  return EV_KINDS.map((k) => {
    const cur = levels[k] || (inherit ? 'inherit' : 'sound');
    const baseLvl = (base && base[k]) || 'sound';
    const opts = (inherit ? [{ k: 'inherit', title: t('lvl.inherit'), text: t('lvl.inherit.text', { v: t('lvl.' + baseLvl) }) }] : [])
      .concat(['sound', 'quiet', 'off'].map((o) => ({ k: o, title: t('lvl.' + o), text: t('lvl.' + o + '.text') })));
    const shown = cur === 'inherit' ? t('lvl.inherit') : t('lvl.' + cur);
    return html`<${Row} key=${k} label=${t('evk.' + k)} value=${shown} cls=${cur === 'off' ? 'val-off' : cur === 'quiet' ? 'val-quiet' : ''}
      onClick=${() => pick(setSheet, t('evk.' + k), opts, cur, (o) => { const nx = { ...levels }; if (o === 'inherit') delete nx[k]; else nx[k] = o; onChange(nx); })} />`;
  });
}

// ---------- settings ----------

export function hostOf(url) {
  try { return new URL(url).host; } catch (e) { return url; }
}

export function SettingsTab({ ctx }) {
  const { cfg, saveCfg, env, setSheet, say, link } = ctx;
  const [pings, setPings] = useState({});
  const s = cfg.settings;
  const mode = s.mode || 'live';
  const theme = s.theme || 'system';
  const lang = s.lang || 'system';

  useEffect(() => {
    let dead = false;
    cfg.hubs.forEach((h) => {
      const t0 = Date.now();
      hubReq(h, 'GET', '/v1/ping', null, 8000)
        .then((r) => { if (!dead) setPings((p) => ({ ...p, [h.url]: { ok: true, ms: Date.now() - t0, name: r.name } })); })
        .catch((e) => { if (!dead) setPings((p) => ({ ...p, [h.url]: { ok: false, err: e.message } })); });
    });
    return () => { dead = true; };
  }, [cfg.hubs.length, link.state]);

  const set = (patch) => saveCfg({ ...cfg, settings: { ...s, ...patch } });
  const removeHub = (h) => setSheet({
    type: 'confirm', title: t('hub.off.q', { name: h.name || hostOf(h.url) }),
    text: cfg.hubs.length === 1 ? t('hub.off.last') : t('hub.off.text'), action: t('hub.off'), danger: true,
    run: async () => saveCfg({ ...cfg, hubs: cfg.hubs.filter((x) => x !== h) }),
  });

  const langName = lang === 'system' ? t('set.lang.system') : (LANGS.find((x) => x[0] === lang) || [0, lang])[1];
  const themeName = t('set.theme.' + theme);
  return html`<div class="page">
    <div class="bar"><h1 class="disp">${t('tab.settings')}</h1></div>

    <${Group} title=${t('set.hubs')} foot=${t('set.hubs.text')}>
      ${cfg.hubs.map((h) => { const p = pings[h.url]; return html`<${Row} key=${h.url} onClick=${() => removeHub(h)} chevron=${false}
        lead=${html`<i class=${'lamp lamp-' + (p ? (p.ok ? 'up' : 'down') : 'new')} />`}
        label=${(p && p.name) || h.name || t('hub')} sub=${hostOf(h.url)}
        value=${html`<span class=${p && !p.ok ? 'crit' : 'dim'}>${p ? (p.ok ? p.ms + ' ' + t('u.ms') : t('hub.nolink')) : t('hub.checking')}</span>`} />`; })}
      <${Row} cls="srow-add" lead=${html`<${Icon} name="plus" size=${20} />`} label=${t('hub.add')} chevron=${false} onClick=${() => setSheet({ type: 'addHub' })} />
    </${Group}>

    <${Needs} ctx=${ctx} />
    <${Group} title=${t('set.alerts')} foot=${t('set.battery')}>
      <${Row} label=${t('set.when')} value=${mode === 'live' ? t('set.live') : t('set.eco')}
        onClick=${() => pick(setSheet, t('set.when'), [{ k: 'live', title: t('set.live'), text: t('set.live.text') }, { k: 'eco', title: t('set.eco'), text: t('set.eco.text') }], mode, (k) => set({ mode: k }))} />
      <${Row} label=${t('set.test')} chevron=${false} cls="srow-act" onClick=${() => native('alerts.test').then(() => say(t('set.test.sent')))} />
    </${Group}>

    <${Group} title=${t('set.notify')} foot=${t('set.notify.foot')}>
      <${NotifyRows} levels=${s.notify || {}} onChange=${(v) => set({ notify: v })} setSheet=${setSheet} />
    </${Group}>

    <${Group} title=${t('set.look')}>
      <${Row} label=${t('set.theme')} value=${themeName}
        onClick=${() => pick(setSheet, t('set.theme'), THEMES.filter(([k]) => env.monet || !k.startsWith('monet')).map(([k, mode, sw]) => ({ k, title: t('set.theme.' + k),
          text: k.startsWith('monet') ? t('set.theme.monet.text') : null, swatch: sw || monetSwatch(env.monet, mode || (env.night !== false ? 'dark' : 'light')) })), theme, (k) => set({ theme: k }))} />
      <${Row} label=${t('set.lang')} value=${langName}
        onClick=${() => pick(setSheet, t('set.lang'), [{ k: 'system', title: t('set.lang.system') }, ...LANGS.map(([k, v]) => ({ k, title: v }))], lang, (k) => set({ lang: k }))} />
    </${Group}>

    <${Group} title=${t('set.diag')}>
      <${Row} label=${t('set.log')} onClick=${() => setSheet({ type: 'log', title: t('set.log'), load: () => native('log.get') })} />
    </${Group}>
    <p class="dim small ver">Laputa ${env.version || ''}</p>
  </div>`;
}

function Choice({ on, onClick, title, text }) {
  return html`<button class=${'choice' + (on ? ' on' : '')} onClick=${onClick} role="radio" aria-checked=${on}>
    <i class="radio" /><span><b>${title}</b><small>${text}</small></span>
  </button>`;
}

// ---------- first connection ----------

export function Onboarding({ ctx }) {
  const { cfg, saveCfg, say } = ctx;
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const ref = useRef(null);
  const paste = async () => { const v = await native('clipboard.read'); if (v) { setText(v); setErr(''); } else say(t('clip.empty')); };
  const connect = async () => {
    const hub = parseKey(text);
    if (!hub) { setErr(t('key.bad.long')); return; }
    setBusy(true); setErr('');
    try {
      const r = await hubReq(hub, 'GET', '/v1/ping', null, 10000);
      hub.name = r.name || hub.name;
      saveCfg({ ...cfg, hubs: [hub] });
    } catch (e) {
      setErr(e.status ? e.message + '.' : t('hub.silent.at', { host: hostOf(hub.url) }));
    }
    setBusy(false);
  };
  return html`<div class="page onboard">
    <header class="sky sky-wait"><div class="sky-stars" />
      <div class="sky-text"><h1>Laputa</h1><p>${t('ob.tag')}</p></div>
      <div class="sky-isles lone"><span class="isle isle-up" style="--i:0"><${Island} status="up" towers=${3} /></span></div>
    </header>
    <div class="ground">
      <h2>${t('ob.head')}</h2>
      <p class="dim">${t('ob.text')}</p>
      <textarea ref=${ref} class="key" rows="4" value=${text} onInput=${(e) => { setText(e.target.value); setErr(''); }}
        placeholder="laputa1:…" autocapitalize="off" autocomplete="off" spellcheck=${false} />
      ${err && html`<p class="crit small">${err}</p>`}
      <div class="pair">
        <button class="btn" onClick=${paste}><${Icon} name="paste" />${t('paste')}</button>
        <button class="btn primary" disabled=${busy || !text.trim()} onClick=${connect}>${busy ? t('checking') : t('connect')}</button>
      </div>
      <div class="langs small-langs">
        ${LANGS.map(([k, v]) => html`<button key=${k} class=${'lang' + ((cfg.settings.lang || '') === k ? ' on' : '')} onClick=${() => saveCfg({ ...cfg, settings: { ...cfg.settings, lang: k } })} lang=${k}>${v}</button>`)}
      </div>
    </div>
  </div>`;
}
