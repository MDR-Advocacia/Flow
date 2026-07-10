// Runner Playwright: loga no LegalOne novo (firm.legalone.com.br) via OnePass e
// CAPTURA o Bearer JWT + a Ocp-Apim-Subscription-Key que o SPA manda pro
// gateway interno (azure-api.net). Devolve isso pro Python fazer o import da
// planilha por HTTP puro (mesmo padrão do legacy_task_http, mas app novo).
//
// Uso: node capture-l1-token.js [--base-url https://firm.legalone.com.br]
// Saída (stdout, última linha): JSON { ok, token, subscriptionKey, tenancy, source }
//
// Login/SSO espelhado do generate-report.js (OnePass + key selection).

const { chromium } = require('playwright');

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const c = argv[i];
    if (!c.startsWith('--')) continue;
    const key = c.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith('--')) { args[key] = true; continue; }
    args[key] = next;
    i += 1;
  }
  return args;
}

function requireEnvAny(names) {
  for (const name of names) {
    if (process.env[name]) return process.env[name];
  }
  throw new Error(`Missing env var. Tried: ${names.join(', ')}`);
}

async function waitForPageSettle(page, delayMs = 0) {
  await page.waitForLoadState('domcontentloaded', { timeout: 120000 }).catch(() => {});
  if (delayMs > 0) await page.waitForTimeout(delayMs);
  await page.waitForLoadState('networkidle', { timeout: 45000 }).catch(() => {});
}

async function firstExistingSelector(page, selectors) {
  for (const s of selectors) {
    const h = await page.$(s).catch(() => null);
    if (h) return s;
  }
  return null;
}

async function clickFirstAvailable(page, selectors) {
  const s = await firstExistingSelector(page, selectors);
  if (!s) return false;
  await page.click(s, { timeout: 30000 });
  return true;
}

async function fillFirstAvailable(page, selectors, value) {
  const s = await firstExistingSelector(page, selectors);
  if (!s) return false;
  await page.fill(s, value, { timeout: 30000 });
  return true;
}

function capturePageContext(page) {
  return page
    .evaluate(() => ({
      url: window.location.href,
      title: document.title || '',
      bodyStart: (document.body ? document.body.innerText || '' : '').slice(0, 1500),
    }))
    .catch(() => ({ url: page.url(), title: '', bodyStart: '' }));
}

function isAuthenticationPage(ctx) {
  const t = `${ctx.url}\n${ctx.title}\n${ctx.bodyStart}`.toLowerCase();
  return (
    t.includes('signon.thomsonreuters.com') ||
    t.includes('auth.thomsonreuters.com') ||
    t.includes('novajus.com.br/conta/login') ||
    t.includes('loginonepass') ||
    t.includes('onepass') ||
    t.includes('username') ||
    t.includes('password') ||
    t.includes('autentica')
  );
}

async function completeKeySelectionIfPresent(page, keyLabel) {
  const body = await page.locator('body').innerText().catch(() => '');
  if (!body || (!/Selecione uma chave de registro/i.test(body) && !body.includes(keyLabel))) {
    return false;
  }
  await page.getByText(keyLabel, { exact: false }).first().click({ timeout: 30000 });
  await page.getByRole('button', { name: /Continuar/i }).click({ timeout: 30000 });
  return true;
}

async function login(page, { username, password, keyLabel, returnUrl }) {
  await page.goto(returnUrl, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await waitForPageSettle(page, 4000);
  for (let attempt = 1; attempt <= 8; attempt += 1) {
    if (await completeKeySelectionIfPresent(page, keyLabel)) {
      await waitForPageSettle(page, 8000);
      continue;
    }
    if (await firstExistingSelector(page, ['#btn-login-onepass'])) {
      await clickFirstAvailable(page, ['#btn-login-onepass']);
      await waitForPageSettle(page, 4000);
      continue;
    }
    if (page.url().includes('/u/login/identifier')) {
      if (await fillFirstAvailable(page, ['input[name="username"]', 'input[name="email"]', 'input[type="email"]'], username)) {
        await clickFirstAvailable(page, ['button[name="action"]', 'button[type="submit"]']);
        await waitForPageSettle(page, 4000);
        continue;
      }
    }
    if (page.url().includes('/u/login/password')) {
      if (await fillFirstAvailable(page, ['#password', 'input[name="password"]', '#Password'], password)) {
        await clickFirstAvailable(page, ['button[name="action"]', 'button[type="submit"]', '#SignIn']);
        await waitForPageSettle(page, 6000);
        continue;
      }
    }
    if ((await firstExistingSelector(page, ['#Username'])) && (await firstExistingSelector(page, ['#Password']))) {
      const initialUrl = page.url();
      await page.fill('#Username', username, { timeout: 30000 });
      await page.locator('#Username').blur().catch(() => {});
      const redirected = await page.waitForURL((u) => u !== initialUrl, { timeout: 5000 }).then(() => true).catch(() => false);
      if (redirected) { await waitForPageSettle(page, 4000); continue; }
      await page.fill('#Password', password, { timeout: 30000 });
      await page.click('#SignIn', { timeout: 30000 });
      await waitForPageSettle(page, 4000);
      continue;
    }
    const ctx = await capturePageContext(page);
    if (!isAuthenticationPage(ctx)) return;
  }
  const finalCtx = await capturePageContext(page);
  if (isAuthenticationPage(finalCtx)) {
    throw new Error(`Login não finalizou | url=${finalCtx.url} | title=${finalCtx.title}`);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  // Login pelo novajus (fluxo OnePass provado); captura no app novo (firm).
  const loginBase = (args['login-url'] || process.env.LEGAL_ONE_WEB_URL || 'https://mdradvocacia.novajus.com.br').replace(/\/$/, '');
  const appBase = (args['base-url'] || process.env.LEGAL_ONE_APP_URL || 'https://firm.legalone.com.br').replace(/\/$/, '');
  const loginConfig = {
    username: requireEnvAny(['LEGALONE_WEB_USERNAME', 'LEGAL_ONE_WEB_USERNAME']),
    password: requireEnvAny(['LEGALONE_WEB_PASSWORD', 'LEGAL_ONE_WEB_PASSWORD']),
    keyLabel: requireEnvAny(['LEGALONE_WEB_KEY_LABEL', 'LEGAL_ONE_WEB_KEY_LABEL']),
    returnUrl: `${loginBase}/home`,
  };

  const launchOptions = { headless: true };
  if (process.env.PLAYWRIGHT_CHANNEL) launchOptions.channel = process.env.PLAYWRIGHT_CHANNEL;
  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext();
  await context.route('**/*', (route) => {
    const rt = route.request().resourceType();
    if (rt === 'image' || rt === 'media' || rt === 'font') return route.abort();
    return route.continue();
  });
  const page = await context.newPage();

  // Sniffer: primeiro request ao gateway interno com Bearer → captura token+key.
  const captured = { token: null, subscriptionKey: null, tenancy: null, source: null };
  page.on('request', (req) => {
    try {
      if (!req.url().includes('azure-api.net')) return;
      const h = req.headers();
      const auth = h['authorization'];
      const sub = h['ocp-apim-subscription-key'];
      if (auth && /^bearer\s+/i.test(auth) && !captured.token) {
        captured.token = auth.replace(/^bearer\s+/i, '').trim();
        captured.source = 'request-intercept';
      }
      if (sub && !captured.subscriptionKey) captured.subscriptionKey = sub;
      if (h['tenancy'] && !captured.tenancy) captured.tenancy = h['tenancy'];
    } catch (_) { /* ignore */ }
  });

  try {
    await login(page, loginConfig);
    // Já autenticado no OnePass — pula pro app novo, que deve entrar via SSO
    // silencioso e disparar as chamadas ao gateway (o Bearer sai nelas).
    await page.goto(`${appBase}/`, { waitUntil: 'domcontentloaded', timeout: 120000 }).catch(() => {});
    await waitForPageSettle(page, 3000);
    for (let i = 0; i < 45 && !captured.token; i += 1) {
      await page.waitForTimeout(1000);
    }
    // Fallback: varre o storage por um JWT (caso o intercept não pegue).
    if (!captured.token) {
      const fromStorage = await page.evaluate(() => {
        const found = [];
        for (const store of [window.localStorage, window.sessionStorage]) {
          for (let i = 0; i < store.length; i += 1) {
            const v = store.getItem(store.key(i)) || '';
            const m = v.match(/eyJ[\w-]+\.[\w-]+\.[\w-]+/);
            if (m) found.push(m[0]);
          }
        }
        return found;
      }).catch(() => []);
      if (fromStorage.length) {
        captured.token = fromStorage[0];
        captured.source = 'storage-scan';
      }
    }

    const ctx = await capturePageContext(page);
    console.error(`[cap] token=${captured.token ? 'SIM(' + captured.token.length + ')' : 'NAO'} key=${captured.subscriptionKey ? 'SIM' : 'NAO'} src=${captured.source} url=${ctx.url.slice(0, 60)}`);
    console.log(JSON.stringify({
      ok: !!captured.token,
      token: captured.token,
      subscriptionKey: captured.subscriptionKey,
      tenancy: captured.tenancy || 'mdradvocacia',
      source: captured.source,
    }));
    process.exitCode = captured.token ? 0 : 1;
  } catch (err) {
    console.log(JSON.stringify({ ok: false, error: String((err && err.message) || err) }));
    process.exitCode = 1;
  } finally {
    await browser.close().catch(() => {});
  }
}

main();
