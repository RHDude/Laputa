// Themes. The mode (dark or light) sets the state colours, the theme sets surfaces and accent.
// Monet: wallpaper colours (Android 12+); the shell sends the palette in env.monet.

export const THEMES = [
  ['system', null, ['#0d1430', '#6ea8ea', '#9fbeff']],
  ['dark', 'dark', ['#0d1430', '#2f4a8e', '#9fbeff']],
  ['light', 'light', ['#6ea8ea', '#f4f7fc', '#2c5fd6']],
  ['dusk', 'dark', ['#1a1033', '#8a3f6b', '#f0a6c8']],
  ['ocean', 'dark', ['#031a24', '#0d6b7a', '#6fd3e0']],
  ['amoled', 'dark', ['#000000', '#141a33', '#9fbeff']],
  ['dawn', 'light', ['#f6a57a', '#fff8f3', '#d9622b']],
  ['mint', 'light', ['#5cbfa0', '#f3faf7', '#1f8a6a']],
  ['monet-dark', 'dark', null],
  ['monet-light', 'light', null],
];

const VARS = ['--sky-top', '--sky-mid', '--sky-low', '--sky-ink', '--sky-dim', '--ground', '--raise', '--raise-2', '--line', '--ink', '--dim',
  '--accent', '--accent-ink', '--accent-soft', '--isl-rock', '--isl-rock-dead', '--isl-house'];

function rgba(hex, a) {
  const n = parseInt(String(hex || '#000000').slice(1), 16);
  return 'rgba(' + ((n >> 16) & 255) + ', ' + ((n >> 8) & 255) + ', ' + (n & 255) + ', ' + a + ')';
}

export function monetVars(m, mode) {
  const a = m.accent1 || {};
  const n1 = m.neutral1 || {};
  const n2 = m.neutral2 || {};
  if (mode === 'dark') {
    return {
      '--sky-top': n2[900], '--sky-mid': a[800], '--sky-low': a[600], '--sky-ink': n1[50], '--sky-dim': rgba(n1[100], 0.75),
      '--ground': n1[900], '--raise': n2[800], '--raise-2': n2[700], '--line': rgba(n2[300], 0.16),
      '--ink': n1[50], '--dim': n2[300], '--accent': a[200], '--accent-ink': a[800], '--accent-soft': rgba(a[200], 0.15),
      '--isl-rock': n2[500], '--isl-rock-dead': n2[700], '--isl-house': a[50],
    };
  }
  return {
    '--sky-top': a[300], '--sky-mid': a[100], '--sky-low': a[50], '--sky-ink': a[900], '--sky-dim': rgba(a[900], 0.7),
    '--ground': n1[50], '--raise': n1[10] || '#ffffff', '--raise-2': n2[100], '--line': rgba(n2[600], 0.14),
    '--ink': n1[900], '--dim': n2[600], '--accent': a[600], '--accent-ink': '#ffffff', '--accent-soft': rgba(a[600], 0.1),
    '--isl-rock': n2[400], '--isl-rock-dead': n2[200], '--isl-house': '#ffffff',
  };
}

export function monetSwatch(m, mode) {
  if (!m) return null;
  const v = monetVars(m, mode || 'dark');
  return [v['--sky-top'], v['--ground'], v['--accent']];
}

// The actual theme and mode: System and Monet depend on night mode and on whether a palette exists.
export function resolveTheme(pref, night, monet) {
  let name = pref || 'system';
  if (name.startsWith('monet') && !monet) name = name === 'monet-light' ? 'light' : name === 'monet-dark' ? 'dark' : 'system';
  if (name === 'system') name = night ? 'dark' : 'light';
  if (name === 'monet') name = night ? 'monet-dark' : 'monet-light';
  const t = THEMES.find((x) => x[0] === name) || THEMES[1];
  return { name, mode: t[1] || 'dark', monet: name.startsWith('monet') };
}

export function applyTheme(th, monet) {
  const el = document.documentElement;
  el.dataset.theme = th.name;
  el.dataset.mode = th.mode;
  if (th.monet && monet) {
    const v = monetVars(monet, th.mode);
    Object.keys(v).forEach((k) => { if (v[k]) el.style.setProperty(k, v[k]); });
  } else {
    VARS.forEach((k) => el.style.removeProperty(k));
  }
  const cs = getComputedStyle(el);
  return { skyTop: cs.getPropertyValue('--sky-top').trim(), ground: cs.getPropertyValue('--ground').trim() };
}

// The last applied theme: index.html applies it before the first paint on the next start.
export function rememberTheme() {
  const el = document.documentElement;
  try { localStorage.setItem('laputa.theme', JSON.stringify({ t: el.dataset.theme, m: el.dataset.mode, s: el.getAttribute('style') || '' })); } catch (e) { /* no storage: start dark */ }
}
