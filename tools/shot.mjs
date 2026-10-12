// Скриншоты интерфейса в Chromium: node shot.mjs <url> <out.png> [--light] [--full] [--click=selector]... [--wait=ms]
import { chromium } from '/opt/npm-tools/node_modules/playwright/index.mjs';
const [url, out, ...rest] = process.argv.slice(2);
const opt = (k) => rest.find((a) => a.startsWith('--' + k));
const browser = await chromium.launch({ executablePath: process.env.PW_CHROME || undefined });
const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 2, ignoreHTTPSErrors: true, isMobile: true, hasTouch: true, locale: 'ru-RU' });
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
page.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
await page.goto(url);
await page.waitForTimeout(900);
for (const a of rest.filter((x) => x.startsWith('--click='))) {
  await page.click(a.slice(8), { timeout: 5000 });
  await page.waitForTimeout(700);
}
for (const a of rest.filter((x) => x.startsWith('--fill='))) {
  const [sel, val] = a.slice(7).split('::');
  await page.fill(sel, val);
  await page.waitForTimeout(300);
}
const w = opt('wait'); if (w) await page.waitForTimeout(+w.split('=')[1]);
await page.screenshot({ path: out, fullPage: !!opt('full') });
if (errs.length) console.log(errs.join('\n'));
await browser.close();
