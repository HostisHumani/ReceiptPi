// Documentation screenshots of the demo instance (demo_server.py), see
// run.sh. Usage: node shoot.js <lang> <outdir> <picture>
// PLAYWRIGHT_MODULE: path of the playwright package if it isn't
// resolvable from here; SYSLIBS: extra library dir for Chromium.
// Phone width (390 CSS px, 2x), Warm theme, light, glass on. Writes
// <outdir>/<section>/<name>.png for "en" and <name>.de.png for "de".
const path = require('path');
const fs = require('fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const [lang, outdir, picture] = process.argv.slice(2);
const DE = lang === 'de';
const BASE = 'http://receiptpi.local:5000';
const MAX_H = 1800;

const t = (en, de) => (DE ? de : en);

async function shot(page, section, name, opts = {}) {
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(opts.wait ?? 400);
  const dir = path.join(outdir, section);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${name}${DE ? '.de' : ''}.png`);
  if (opts.full === false) {
    await page.screenshot({ path: file });
  } else {
    // A taller viewport instead of fullPage: the glass background is a
    // position:fixed layer, which a fullPage capture only paints for the
    // original viewport height. Cut just below the footer.
    const h = await page.evaluate(() => {
      const f = document.querySelector('footer');
      return Math.ceil((f ? f.getBoundingClientRect().bottom + window.scrollY : document.documentElement.scrollHeight) + 24);
    });
    await page.setViewportSize({ width: 390, height: Math.min(Math.max(h, 400), MAX_H) });
    await page.waitForTimeout(300);
    await page.screenshot({ path: file });
    await page.setViewportSize({ width: 390, height: 844 });
  }
  console.log('shot', file);
}

async function go(page, url) {
  await page.goto(BASE + url, { waitUntil: 'networkidle' });
}

(async () => {
  const browser = await chromium.launch({
    env: { ...process.env, LD_LIBRARY_PATH: [process.env.SYSLIBS, process.env.LD_LIBRARY_PATH].filter(Boolean).join(':') },
    args: ['--host-resolver-rules=MAP receiptpi.local 127.0.0.1', '--font-render-hinting=none'],
  });
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    colorScheme: 'light', locale: DE ? 'de-DE' : 'en-US',
  });
  const page = await ctx.newPage();

  // Phase A: no admin yet
  await go(page, '/');
  await shot(page, 'getting-started', 'home-no-admin', { full: false, wait: 1200 });
  await go(page, '/setup');
  await page.fill('#name', 'alex').catch(() => {});
  await shot(page, 'getting-started', 'setup');

  await go(page, '/__demo/seed');

  // Login
  await go(page, '/login');
  await page.fill('input[name="name"]', 'alex');
  await page.fill('input[name="password"]', 'demo-password-1');
  await shot(page, 'getting-started', 'login');
  await page.check('input[name="remember"]').catch(() => {});
  await Promise.all([page.waitForNavigation(), page.click('form button[type="submit"]')]);

  // Home and menu
  await go(page, '/');
  await shot(page, 'getting-started', 'home', { wait: 1200 });
  await page.click('details.burger-menu summary');
  await shot(page, 'getting-started', 'menu', { full: false });

  // Printing
  await go(page, '/lists/shopping');
  await shot(page, 'modules', 'lists-shopping');
  await go(page, '/lists/task');
  await page.fill('#title', t('Fix garage shelf', 'Garagenregal reparieren'));
  await page.fill('#description', t('Screws are in the red box.', 'Schrauben liegen in der roten Kiste.'));
  await page.fill('#items', t('Buy wall plugs\nDrill holes\nMount shelf', 'Dübel kaufen\nLöcher bohren\nRegal montieren'));
  await page.selectOption('#priority', 'medium');
  await shot(page, 'modules', 'lists-task');

  await go(page, '/message');
  await page.fill('#title', t('Reminder', 'Erinnerung'));
  await page.fill('#text', t('Water the plants\nbefore you leave.', 'Blumen gießen,\nbevor du gehst.'));
  await shot(page, 'modules', 'message');

  await go(page, '/images');
  await page.setInputFiles('#image', picture);
  await page.fill('#caption', t('Happy birthday!\nSee you on Sunday.', 'Alles Gute zum Geburtstag!\nBis Sonntag.'));
  await page.click('#image_preview_btn');
  await page.waitForSelector('#image_preview_result:not([hidden])', { timeout: 20000 });
  await shot(page, 'modules', 'images', { wait: 800 });

  await go(page, '/weather');
  await shot(page, 'modules', 'weather');
  await go(page, '/settings/weather');
  await shot(page, 'modules', 'weather-settings');

  await go(page, '/settings/recipes');
  await page.selectOption('select[name="provider"]', 'mealie').catch(() => {});
  await page.fill('input[name="base_url"]', 'http://192.0.2.30:9000').catch(() => {});
  await shot(page, 'modules', 'recipes-settings');

  await go(page, '/wifi');
  await shot(page, 'modules', 'wifi');

  await go(page, '/settings/system-report');
  await shot(page, 'modules', 'system-settings');

  await go(page, '/games');
  await shot(page, 'modules', 'games');
  await go(page, '/games/sudoku');
  await shot(page, 'modules', 'games-sudoku');

  await go(page, '/history');
  await shot(page, 'modules', 'history');
  await go(page, '/pending');
  await page.check('input[name="ids"] >> nth=0').catch(() => {});
  await shot(page, 'modules', 'pending');

  // Settings
  await go(page, '/settings');
  await shot(page, 'settings', 'overview');
  await go(page, '/settings/language');
  await shot(page, 'settings', 'language');
  await go(page, '/settings/design');
  await shot(page, 'settings', 'design');
  await go(page, '/settings/modules');
  await shot(page, 'settings', 'modules');
  await go(page, '/settings/print-rules');
  await shot(page, 'settings', 'print-rules');
  await go(page, '/settings/logos');
  await shot(page, 'settings', 'logos');
  await go(page, '/settings/backup');
  await shot(page, 'settings', 'backup');
  await go(page, '/settings/users');
  await shot(page, 'settings', 'users');
  await go(page, '/account');
  await shot(page, 'settings', 'account');
  await go(page, '/preferences');
  await shot(page, 'settings', 'my-settings');
  await go(page, '/preferences/home');
  await shot(page, 'settings', 'customize-home');

  // Integrations
  await go(page, '/settings/mqtt');
  await page.check('input[name="enabled"]').catch(() => {});
  await page.fill('input[name="host"]', 'broker.example').catch(() => {});
  await shot(page, 'integrations', 'mqtt');
  await go(page, '/settings/github-watch');
  await shot(page, 'integrations', 'github-watch');

  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
