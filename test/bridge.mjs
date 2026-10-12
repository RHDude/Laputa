// Проверка интерфейса через тот же мост, что в приложении: подставной window.LaputaNative
// отвечает в формате MainActivity (resolve(id, ok, json)), сеть идёт к живому стенду.
import { chromium } from '/opt/npm-tools/node_modules/playwright/index.mjs';
import { execSync } from 'node:child_process';

const SHOTS = new URL('./shots/', import.meta.url).pathname;
const results = [];
const check = (name, ok, extra) => { results.push([name, !!ok]); console.log((ok ? '  PASS  ' : '  FAIL  ') + name + (!ok && extra ? '   ' + extra : '')); };

// ключ приложения в том виде, в каком его печатает установщик на сервере
const key = execSync(`python3 -I -c "
import sys, json; sys.path.insert(0, 'server'); import laputa
h = json.load(open('${process.env.BENCH_DIR}/summit/etc/hub.json'))
import hashlib, ssl
pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(open('${process.env.BENCH_DIR}/summit/etc/tls.crt').read())).hexdigest()
print(laputa.make_key('laputa1', {'url': 'https://127.0.0.1:18743', 'pin': pin, 'token': h['app_token'], 'name': 'Сторож Казань'}))"`, { cwd: new URL('..', import.meta.url).pathname }).toString().trim();

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 2, ignoreHTTPSErrors: true, isMobile: true, hasTouch: true, locale: 'ru-RU' });
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push('console: ' + m.text()); });

await page.addInitScript((appKey) => {
  const calls = (window.__calls = []);
  let store = { hubs: [], settings: {} };
  const handlers = {
    env: async () => ({ version: '1.1.0', night: true, notif: true, battery: true, sdk: 35, lang: 'ru' }),
    'store.get': async () => store,
    'store.set': async (a) => { store = a; return true; },
    http: async (a) => {
      const r = await fetch(a.url, { method: a.method || 'GET', body: a.body || undefined,
        headers: { Authorization: 'Bearer ' + a.token, ...(a.body ? { 'Content-Type': 'application/json' } : {}) } });
      return { status: r.status, body: await r.text() };
    },
    'clipboard.read': async () => 'вот ключ: ' + appKey + '\n',
  };
  window.LaputaNative = {
    post(msg) {
      const m = JSON.parse(msg);
      calls.push(m.method + (m.method === 'chrome' ? ':' + m.args.top : ''));
      const h = handlers[m.method] || (async () => true);
      // как в MainActivity: ответ приходит позже и текстом JSON, который исполняется как выражение
      h(m.args).then((res) => setTimeout(() => (0, eval)('window.__laputa.resolve(' + m.id + ',true,' + JSON.stringify(res) + ')'), 5),
        (e) => setTimeout(() => (0, eval)('window.__laputa.resolve(' + m.id + ',false,' + JSON.stringify(String(e.message || e)) + ')'), 5));
    },
  };
}, key);

const has = (t, timeout = 12000) => page.getByText(t, { exact: false }).first().waitFor({ timeout }).then(() => true).catch(() => false);
const calls = () => page.evaluate(() => window.__calls);
try {
  await page.goto('http://127.0.0.1:8090/index.html');
  check('без сторожей показан экран подключения', await has('Подключите сторожа'));
  check('интерфейс сообщил оболочке, что готов', (await calls()).includes('ready'));
  await page.getByRole('button', { name: 'Вставить' }).click();
  await page.waitForFunction(() => (document.querySelector('.key') || {}).value?.includes('laputa1:'), null, { timeout: 5000 }).catch(() => {});
  check('ключ из буфера вставлен в поле', (await page.locator('.key').inputValue()).includes('laputa1:'));
  await page.getByRole('button', { name: 'Подключить' }).click();
  check('ключ с сервера разобран, сторож ответил, узлы на экране', await has('Все 5 в строю', 20000), await page.locator('.sky h1').innerText().catch(() => ''));
  check('имя сторожа с кириллицей не побилось', await has('Сторож Summit'));
  const c = await calls();
  check('настройки сохранены через мост и тревоги применены', c.includes('store.set') && c.includes('alerts.apply'), c.join(','));
  check('цвет системной полосы передан для неба', c.includes('chrome:#0d1430'), c.filter((x) => x.startsWith('chrome')).join(','));
  await page.screenshot({ path: SHOTS + 'bridge-home.png' });

  await page.evaluate(() => window.__laputa.emit({ type: 'open', node: 'meadow' }));
  check('переход по уведомлению открывает узел', await has('В строю') && (await page.locator('.bar-title h1').innerText()) === 'Meadow');
  check('на экране узла полоса перекрашена под фон', (await calls()).includes('chrome:#151e38'));
  check('кнопка «назад» закрывает узел', (await page.evaluate(() => window.__laputa.back())) === true && await has('Все 5 в строю'));
  check('на главном экране «назад» отдаётся системе', (await page.evaluate(() => window.__laputa.back())) === false);
  await page.evaluate(() => window.__laputa.emit({ type: 'theme', night: false }));
  await page.waitForTimeout(400);
  check('смена системной темы переключает оформление', (await page.evaluate(() => document.documentElement.dataset.theme)) === 'light' && (await calls()).includes('chrome:#6ea8ea'));
  await page.screenshot({ path: SHOTS + 'bridge-light.png' });
  await page.evaluate(() => window.__laputa.emit({ type: 'env', env: { notif: false } }));
  check('запрет уведомлений сразу виден на главном экране', await has('Уведомления запрещены'));
  await page.evaluate(() => window.__laputa.emit({ type: 'env', env: { lang: 'de' } }));
  check('язык системы из оболочки применяется, пока в настройках «как в системе»', await has('Benachrichtigungen sind blockiert') && await has('Alle 5 laufen'));
} catch (e) {
  check('сценарий дошёл до конца', false, e.message.split('\n')[0]);
}
check('ошибок в консоли страницы нет', errs.length === 0, errs.join(' | '));
await browser.close();
const bad = results.filter((r) => !r[1]);
console.log(`\nИтог: ${results.length} проверок, не прошло ${bad.length}`);
process.exit(bad.length ? 1 : 0);
