// Сквозная проверка интерфейса против живого стенда (test/bench/run.py serve).
import { chromium } from '/opt/npm-tools/node_modules/playwright/index.mjs';
import fs from 'node:fs';

const BENCH = process.env.BENCH_DIR;
const SHOTS = new URL('./shots/', import.meta.url).pathname;
const URL0 = 'http://127.0.0.1:8090/index.html?hub=https://127.0.0.1:18743&token=app-summit-secret&hub=https://127.0.0.1:18744&token=app-aurora-secret';
const results = [];
const check = (name, ok, extra) => { results.push([name, !!ok]); console.log((ok ? '  PASS  ' : '  FAIL  ') + name + (!ok && extra ? '   ' + extra : '')); };
const setUnit = (agent, unit, patch) => {
  const p = `${BENCH}/agent-${agent}/units.json`;
  const u = JSON.parse(fs.readFileSync(p, 'utf8'));
  Object.assign(u[unit], patch, { since: Date.now() / 1000 });
  fs.writeFileSync(p, JSON.stringify(u));
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 2, ignoreHTTPSErrors: true, isMobile: true, hasTouch: true, locale: 'ru-RU' });
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push('console: ' + m.text()); });
const text = (sel) => page.locator(sel).first().innerText({ timeout: 8000 }).catch(() => '');
const has = (t, timeout = 12000) => page.getByText(t, { exact: false }).first().waitFor({ timeout }).then(() => true).catch(() => false);
const shot = (n) => page.screenshot({ path: SHOTS + n + '.png' });

try {
  await page.goto(URL0);
  check('главный экран: все узлы в строю', await has('Все 5 в строю', 20000), await text('.sky h1'));
  check('пять островов и пять строк', (await page.locator('.isle').count()) === 5 && (await page.locator('.node-row').count()) === 5);
  check('подпись называет сторожа', (await text('.sky-foot')).includes('Summit'));
  await shot('live-home');

  await page.locator('.node-row', { hasText: 'Aurora' }).click();
  check('экран узла открыт', await has('В строю'));
  check('узел помечен как сторож', (await text('.bar-title')).includes('сторож'));
  await page.getByRole('button', { name: 'Добавить' }).click();
  check('подбор служб: узел перечислил свои службы с причинами', await has('Milo bot') && await has('запускается с системой'));
  await page.locator('.sheet .pick-row', { hasText: 'Milo bot' }).click();
  await page.locator('.sheet .pick-row', { hasText: 'Mike bot' }).click();
  check('отмечены две службы и рекомендованная 3X-UI', (await page.locator('.sheet .pick-row.on').count()) === 3 && await has('Добавить: 3'));
  await shot('live-add-service');
  await page.getByRole('button', { name: 'Добавить: 3' }).click();
  check('обе службы на экране узла', await page.locator('.page-node .svc-row', { hasText: 'Mike bot' }).first().waitFor({ timeout: 12000 }).then(() => true).catch(() => false)
    && await page.locator('.page-node .svc-row', { hasText: 'Milo bot' }).count() === 1);

  await page.locator('.page-node .svc-row', { hasText: 'Milo bot' }).click();
  await page.getByRole('button', { name: 'Изменить ярлык' }).click();
  await page.locator('.form input').first().fill('Milo Bot');
  await page.locator('.form input').nth(1).fill('t.me/example_bot');
  await page.getByRole('button', { name: 'Сохранить' }).click();
  check('карточка службы: тип угадан как бот, есть ссылка', await has('Бот на Aurora') && await has('Открыть бота', 12000));
  await page.getByRole('button', { name: 'Перезапустить' }).click();
  check('перезапуск из карточки', await has('Milo Bot перезапущена'));
  await page.getByRole('button', { name: 'Журнал' }).click();
  check('журнал службы показан', await has('строка журнала'));
  await shot('live-log');
  await page.locator('.sheet .icon-btn').click();
  check('после журнала снова карточка службы', await has('Изменить ярлык'));
  await page.locator('.sheet .icon-btn').click();

  // игровая консоль на Meadow
  await page.locator('.bar .icon-btn').click();
  await page.locator('.node-row', { hasText: 'Meadow' }).click();
  await page.getByRole('button', { name: 'Добавить' }).click();
  check('Minecraft рекомендован и отмечен сам', await page.locator('.sheet .pick-row.on', { hasText: 'Minecraft' }).first().waitFor({ timeout: 12000 }).then(() => true).catch(() => false));
  await page.locator('.sheet .btn.primary').click();
  await page.getByRole('button', { name: 'Подключить консоль' }).click({ timeout: 12000 });
  const f = page.locator('.sheet .form input');
  await f.nth(1).fill('25599');
  await f.nth(2).fill('secret');
  await page.getByRole('button', { name: 'Сохранить и проверить' }).click();
  check('Minecraft подключён, игроки видны', await has('В игре 2 из 20', 15000) && await has('Alpha'));
  await page.locator('.console input').fill('say привет');
  await page.locator('.console .icon-btn').click();
  check('ответ консоли Minecraft', await has('Done: say привет'));
  await page.locator('.quick .pill.ghost').first().click();
  await page.locator('.sheet .form input').first().fill('Ночь');
  await page.locator('.sheet .form input').nth(1).fill('time set night');
  await page.locator('.sheet .btn.primary').click();
  check('своя кнопка консоли сохранилась на узле', await page.locator('.quick .pill', { hasText: 'Ночь' }).first().waitFor({ timeout: 12000 }).then(() => true).catch(() => false));
  await page.locator('.quick .pill', { hasText: 'Ночь' }).click();
  check('кнопка отправила команду', await has('Done: time set night'));
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await shot('live-minecraft');
  await page.getByRole('button', { name: 'Перезагрузить узел' }).click();
  check('перед перезагрузкой видно, кого заденет', await has('2 игрока (Alpha, Beta)', 15000));
  check('варианты: сейчас, с предупреждением, когда опустеет', await has('С предупреждением игрокам') && await has('Когда опустеет'));
  await shot('live-reboot');
  await page.locator('.sheet .btn', { hasText: 'Отмена' }).click();

  // беда: служба упала
  await page.locator('.bar .icon-btn').click();
  setUnit('aurora', 'milo-bot.service', { state: 'failed', restarts: 4 });
  check('главный экран сообщает о падении службы', await has('Не работает Milo Bot', 20000), await text('.sky h1'));
  check('небо перешло в тревожное', await page.locator('.sky.sky-crit').count() === 1);
  check('у вкладки событий появилась отметка', await page.locator('.tab .dot').count() === 1);
  await shot('live-failed');
  await page.locator('.tab', { hasText: 'Службы' }).click();
  check('упавшая служба вынесена наверх', (await text('.block h2')).includes('Требуют внимания'));
  await page.locator('.search input').fill('minec');
  check('поиск по названию', (await page.locator('.svc-row').count()) === 1 && await has('Minecraft'));
  await page.locator('.search input').fill('');
  await page.locator('.svc-row', { hasText: 'Milo Bot' }).click();
  await page.getByRole('button', { name: 'Перезапустить' }).click();
  check('перезапуск упавшей службы поднял её', await has('Milo Bot перезапущена'));
  await page.locator('.sheet .icon-btn').click();
  await page.locator('.tab', { hasText: 'События' }).click();
  check('в событиях есть падение и восстановление', await has('Упала служба Milo Bot') && await has('снова работает', 15000));
  await page.locator('.fchip', { hasText: 'Службы' }).click();
  check('фильтр событий: службы скрыты', !(await page.getByText('Упала служба Milo Bot').count()) && await has('Скрыто фильтром'));
  await page.locator('.evf-hidden .link').click();
  check('сброс фильтра возвращает всё', await has('Упала служба Milo Bot'));
  await shot('live-events');
  await page.locator('.tab', { hasText: 'Настройки' }).click();
  check('оба сторожа на связи и названы', await has('Summit') && await has('Aurora') && (await page.locator('.srow .lamp-up').count()) === 2);
  await page.locator('.srow', { hasText: 'Входы по SSH' }).click();
  await page.locator('.sheet .choice', { hasText: 'Не присылать' }).click();
  check('настройка «что присылать» выбирается в шторке и сохраняется', (await page.locator('.srow', { hasText: 'Входы по SSH' }).locator('.srow-val').innerText()) === 'Не присылать');
  await shot('live-settings');
  await page.locator('.srow', { hasText: 'Язык' }).click();
  await page.locator('.sheet .choice', { hasText: 'English' }).click();
  check('язык переключается: английский', await has('Watchmen') && await has('Language'));
  await page.locator('.tab').nth(2).click();
  check('события на английском из параметров', await has('Milo Bot failed'));
  await page.locator('.tab').nth(3).click();
  await page.locator('.srow', { hasText: 'Language' }).click();
  await page.locator('.sheet .choice', { hasText: 'Русский' }).click();
  await page.locator('.tab', { hasText: 'Узлы' }).click();
  check('после восстановления снова всё в строю', await has('Все 5 в строю', 15000), await text('.sky h1'));
} catch (e) {
  check('сценарий дошёл до конца', false, e.message.split('\n')[0]);
  await shot('live-error');
}
check('ошибок в консоли страницы нет', errs.length === 0, errs.join(' | '));
await browser.close();
const bad = results.filter((r) => !r[1]);
console.log(`\nИтог: ${results.length} проверок, не прошло ${bad.length}`);
process.exit(bad.length ? 1 : 0);
