import { render } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import { html } from 'htm/preact';
import { native, onNative, hubReq, MOCK } from './api.js';
import { Icon } from './icons.js';
import { NodesTab, health } from './home.js';
import { NodeScreen } from './node.js';
import { ServicesTab, EventsTab, SettingsTab, Onboarding } from './tabs.js';
import { ServiceSheet, AddServiceSheet, GameSetupSheet, QuickAddSheet, RebootSheet, BackupAddSheet, BackupSheet, ConfirmSheet, AddHubSheet, LogSheet, ChoiceSheet } from './sheets.js';
import { t, setLang, pickLang, errText } from './i18n.js';
import { resolveTheme, applyTheme, rememberTheme } from './theme.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function App() {
  const [cfg, setCfg] = useState(null); // {hubs:[{url,pin,token,name}], settings:{mode,theme,seenSeq}}
  const [env, setEnv] = useState({ night: true, init: true }); // init: настоящие данные от системы ещё не пришли
  const [snap, setSnap] = useState(null);
  const [link, setLink] = useState({ state: 'connecting', hub: 0, at: 0 });
  const [route, setRoute] = useState({ tab: 'nodes', node: null });
  const [sheet, setSheet] = useState(null);
  // Пока открыта шторка, страница под ней стоит на месте: свайп по шторке не листает то, что за ней.
  const sheetOpen = !!sheet;
  useEffect(() => {
    if (!sheetOpen) return;
    const y = window.scrollY, b = document.body.style;
    b.position = 'fixed'; b.top = -y + 'px'; b.left = '0'; b.right = '0';
    return () => { b.position = b.top = b.left = b.right = ''; window.scrollTo(0, y); };
  }, [sheetOpen]);
  const [toast, setToast] = useState(null);
  const [now, setNow] = useState(Date.now() / 1000);
  const skew = useRef(0);
  const live = useRef({ paused: false, hub: 0, gen: 0 });
  const toastTimer = useRef(0);
  const stateRef = useRef({});
  stateRef.current = { route, sheet };

  // ---- запуск ----
  useEffect(() => {
    Promise.all([native('store.get'), native('env')]).then(([c, e]) => {
      setCfg({ hubs: (c && c.hubs) || [], settings: (c && c.settings) || {} });
      setEnv(e || {});
      native('ready');
    });
    const t = setInterval(() => setNow(Date.now() / 1000 + skew.current), 1000);
    const off = onNative((evt) => {
      if (evt.type === 'theme') setEnv((x) => ({ ...x, night: !!evt.night }));
      if (evt.type === 'env') setEnv((x) => ({ ...x, ...evt.env }));
      if (evt.type === 'pause') live.current.paused = true;
      if (evt.type === 'resume') { live.current.paused = false; native('env').then((e) => setEnv((x) => ({ ...x, ...e }))); }
      if (evt.type === 'open' && evt.node) { setSheet(null); setRoute({ tab: 'nodes', node: evt.node }); }
      if (evt.type === 'back') {
        const s = stateRef.current;
        if (s.sheet) { setSheet(null); return true; }
        if (s.route.node) { setRoute({ ...s.route, node: null }); return true; }
        if (s.route.tab !== 'nodes') { setRoute({ tab: 'nodes', node: null }); return true; }
        return false;
      }
      return undefined;
    });
    const vis = () => { live.current.paused = document.hidden; };
    document.addEventListener('visibilitychange', vis);
    return () => { clearInterval(t); off(); document.removeEventListener('visibilitychange', vis); };
  }, []);

  // ---- язык: выбранный в настройках или системный ----
  if (cfg) setLang(pickLang(cfg.settings.lang, env.lang));

  // ---- тема и цвета системных полос ----
  const th = resolveTheme(cfg && cfg.settings.theme, env.night !== false, env.monet);
  const night = th.mode === 'dark';
  const onSky = cfg && (!cfg.hubs.length || (route.tab === 'nodes' && !route.node));
  const sev = cfg && cfg.hubs.length ? health(snap, link, now).sev : 'wait';
  const monetKey = env.monet ? JSON.stringify(env.monet.accent1 || {}) : '';
  const themeReady = !!cfg && !env.init;
  useEffect(() => {
    // Пока не знаем настроек и системной темы, держим то, что index.html поднял из прошлого запуска, — без мигания.
    if (!themeReady) return;
    const c = applyTheme(th, env.monet);
    const skyTop = night ? ({ crit: '#120f2a', lost: '#171b29' }[sev] || c.skyTop) : ({ crit: '#8d8fc0', lost: '#9aa5b8' }[sev] || c.skyTop);
    native('chrome', { top: onSky ? skyTop : c.ground, bottom: c.ground, lightTop: !night, lightBottom: !night });
    rememberTheme();
  }, [themeReady, th.name, monetKey, onSky, sev]);

  // ---- опрос сторожей ----
  const hubsKey = cfg ? cfg.hubs.map((h) => h.url + h.token).join('|') : '';
  useEffect(() => {
    if (!cfg || !cfg.hubs.length) { setSnap(null); return undefined; }
    const hubs = cfg.hubs;
    const gen = ++live.current.gen;
    let idx = Math.min(live.current.hub, hubs.length - 1);
    let v = 0;
    let fails = 0;
    setLink({ state: 'connecting', hub: idx, at: Date.now() / 1000 });
    (async () => {
      while (live.current.gen === gen) {
        if (live.current.paused) { await sleep(400); continue; }
        try {
          const s = await hubReq(hubs[idx], 'GET', '/v1/state?since=' + v + '&wait=20', null, 30000);
          if (live.current.gen !== gen) return;
          if (s.ts) skew.current = s.ts - Date.now() / 1000;
          if (s.nodes) setSnap(s);
          if (typeof s.v === 'number') v = s.v;
          fails = 0;
          live.current.hub = idx;
          setLink((l) => (l.state === 'ok' && l.hub === idx ? l : { state: 'ok', hub: idx, at: Date.now() / 1000 }));
          if (__DEV__ && MOCK) await sleep(1500);
        } catch (e) {
          if (live.current.gen !== gen) return;
          fails++;
          idx = (idx + 1) % hubs.length;
          v = 0;
          if (fails >= hubs.length) {
            setLink((l) => (l.state === 'lost' ? l : { state: 'lost', hub: idx, at: Date.now() / 1000, error: String(e.message || e) }));
            await sleep(Math.min(12000, 1500 * fails));
          }
        }
      }
    })();
    return () => { live.current.gen++; };
  }, [hubsKey]);

  const say = useCallback((text, kind) => {
    clearTimeout(toastTimer.current);
    setToast({ text, kind: kind || 'info' });
    toastTimer.current = setTimeout(() => setToast(null), kind === 'bad' ? 5200 : 2600);
  }, []);

  const saveCfg = useCallback((next) => {
    setCfg(next);
    native('store.set', next).then(() => native('alerts.apply'));
  }, []);

  const activeHub = cfg && cfg.hubs[Math.min(live.current.hub, cfg.hubs.length - 1)];

  // Команда узлу через сторожа. cmdRaw отдаёт ответ узла как есть, cmd бросает ошибку с текстом на языке пользователя.
  const cmdRaw = useCallback(async (node, type, args, timeout) => {
    const hubs = cfg.hubs;
    let lastErr = null;
    for (let i = 0; i < hubs.length; i++) {
      const h = hubs[(live.current.hub + i) % hubs.length];
      try {
        const r = await hubReq(h, 'POST', '/v1/cmd', { node, type, args: args || {} }, timeout || 30000);
        // сторож не знает узел — возможно, знает второй
        if (!r.ok && r.code === 'hub_no_node' && i < hubs.length - 1) { lastErr = new Error(errText(r)); continue; }
        return r;
      } catch (e) {
        lastErr = e;
      }
    }
    throw lastErr || new Error(t('err.nohub'));
  }, [cfg]);
  const cmd = useCallback(async (node, type, args, timeout) => {
    const r = await cmdRaw(node, type, args, timeout);
    if (!r.ok) { const e = new Error(errText(r)); e.code = r.code; throw e; }
    return r;
  }, [cmdRaw]);

  const post = useCallback(async (path, body) => hubReq(activeHub, 'POST', path, body), [activeHub]);
  const get = useCallback(async (path) => hubReq(activeHub, 'GET', path), [activeHub]);

  if (!cfg) return html`<div class="boot" />`;

  const ctx = { cfg, saveCfg, env, setEnv, snap, link, now, route, setRoute, sheet, setSheet, say, cmd, cmdRaw, post, get, activeHub };

  if (!cfg.hubs.length) {
    return html`<${Onboarding} ctx=${ctx} /><${Toast} toast=${toast} />`;
  }

  const nodeObj = route.node && snap ? snap.nodes.find((n) => n.id === route.node) : null;
  const unseen = snap ? snap.events.filter((e) => e.seq > (cfg.settings.seenSeq || 0) && (e.sev === 'crit' || e.sev === 'warn')).length : 0;

  const go = (tab) => {
    if (tab === 'events' && snap && snap.events.length) {
      const top = Math.max(...snap.events.map((e) => e.seq));
      if (top > (cfg.settings.seenSeq || 0)) setTimeout(() => saveCfg({ ...cfg, settings: { ...cfg.settings, seenSeq: top } }), 1200);
    }
    setRoute({ tab, node: null });
  };

  let screen;
  if (route.node) screen = html`<${NodeScreen} ctx=${ctx} node=${nodeObj} id=${route.node} />`;
  else if (route.tab === 'services') screen = html`<${ServicesTab} ctx=${ctx} />`;
  else if (route.tab === 'events') screen = html`<${EventsTab} ctx=${ctx} />`;
  else if (route.tab === 'settings') screen = html`<${SettingsTab} ctx=${ctx} />`;
  else screen = html`<${NodesTab} ctx=${ctx} />`;

  return html`
    <div class="app">
      <main class="view" key=${route.node || route.tab}>${screen}</main>
      ${!route.node && html`<nav class="tabs">
        <${Tab} id="nodes" label=${t('tab.nodes')} icon="nodes" cur=${route.tab} go=${go} />
        <${Tab} id="services" label=${t('tab.services')} icon="services" cur=${route.tab} go=${go} />
        <${Tab} id="events" label=${t('tab.events')} icon="events" cur=${route.tab} go=${go} badge=${unseen} />
        <${Tab} id="settings" label=${t('tab.settings')} icon="settings" cur=${route.tab} go=${go} />
      </nav>`}
      <${Sheets} ctx=${ctx} />
      <${Toast} toast=${toast} />
    </div>`;
}

function Tab({ id, label, icon, cur, go, badge }) {
  return html`<button class=${'tab' + (cur === id ? ' on' : '')} onClick=${() => go(id)} aria-current=${cur === id ? 'page' : null}>
    <span class="tab-ic"><${Icon} name=${icon} />${badge ? html`<i class="dot" />` : null}</span>
    <span>${label}</span>
  </button>`;
}

function Sheets({ ctx }) {
  const s = ctx.sheet;
  if (!s) return null;
  const close = () => ctx.setSheet(s.back || null);
  let body = null;
  if (s.type === 'service') body = html`<${ServiceSheet} ctx=${ctx} nodeId=${s.node} unit=${s.unit} close=${close} />`;
  else if (s.type === 'addService') body = html`<${AddServiceSheet} ctx=${ctx} nodeId=${s.node} close=${close} />`;
  else if (s.type === 'gameSetup') body = html`<${GameSetupSheet} ctx=${ctx} nodeId=${s.node} close=${close} />`;
  else if (s.type === 'quickAdd') body = html`<${QuickAddSheet} ctx=${ctx} nodeId=${s.node} close=${close} />`;
  else if (s.type === 'reboot') body = html`<${RebootSheet} ctx=${ctx} nodeId=${s.node} close=${close} />`;
  else if (s.type === 'backupAdd') body = html`<${BackupAddSheet} ctx=${ctx} nodeId=${s.node} close=${close} />`;
  else if (s.type === 'backup') body = html`<${BackupSheet} ctx=${ctx} nodeId=${s.node} path=${s.path} close=${close} />`;
  else if (s.type === 'confirm') body = html`<${ConfirmSheet} ctx=${ctx} ...${s} close=${close} />`;
  else if (s.type === 'addHub') body = html`<${AddHubSheet} ctx=${ctx} close=${close} />`;
  else if (s.type === 'choice') body = html`<${ChoiceSheet} ...${s} close=${close} />`;
  else if (s.type === 'log') body = html`<${LogSheet} title=${s.title} text=${s.text} load=${s.load} close=${close} />`;
  return html`<div class="scrim" onClick=${(e) => { if (e.target === e.currentTarget) close(); }}>
    <section class="sheet" role="dialog" aria-modal="true" key=${s.type + (s.unit || s.path || '')}>
      <div class="grip" />
      ${body}
    </section>
  </div>`;
}

function Toast({ toast }) {
  if (!toast) return null;
  return html`<div class=${'toast toast-' + toast.kind} role="status">${toast.text}</div>`;
}

render(html`<${App} />`, document.getElementById('root'));
