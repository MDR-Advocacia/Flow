// Runner Playwright: dispara a geração de um relatório do L1 (modelo do
// "Relatório Agenda") clicando "Gerar" num browser REAL — assim o SignalR que
// dirige a geração (e que o POST headless não consegue completar) roda de
// verdade. Mantém o browser vivo até detectar a conclusão (ou timeout).
//
// Uso: node generate-report.js --id 627 [--timeout-min 15] [--base-url ...]
// Saída (stdout): JSON { status: 'ok'|'error', completed, model_id, signals, ... }
//
// Login/SSO espelhado do cancel-legacy-task.js (OnePass + key selection).

const { chromium } = require('playwright');
const l1session = require('./l1-session');

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const c = argv[i];
    if (!c.startsWith('--')) continue;
    const key = c.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith('--')) {
      args[key] = true;
      continue;
    }
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
      if (redirected) {
        await waitForPageSettle(page, 4000);
        continue;
      }
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

async function dismissOverlays(page) {
  // Cookie banner + Pendo (interceptam cliques no "Gerar").
  try {
    await page.evaluate(() => {
      for (const id of ['__rpa_kill_cookie__', '__rpa_kill_pendo__']) {
        if (!document.getElementById(id)) {
          const st = document.createElement('style');
          st.id = id;
          st.textContent =
            id.includes('pendo')
              ? '#pendo-base, [id^="pendo-"], [class*="_pendo-"] { display:none !important; pointer-events:none !important; }'
              : 'div.cookie-policy { display:none !important; pointer-events:none !important; visibility:hidden !important; }';
          document.head.appendChild(st);
        }
      }
    });
  } catch (_) {}
}

async function createLoggedInSession(loginConfig) {
  const launchOptions = { headless: true };
  if (process.env.PLAYWRIGHT_CHANNEL) launchOptions.channel = process.env.PLAYWRIGHT_CHANNEL;
  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext();
  await context.route('**/*', (route) => {
    const rt = route.request().resourceType();
    if (rt === 'image' || rt === 'media' || rt === 'font') return route.abort();
    return route.continue();
  });
  // Sessão compartilhada: com cookies válidos o login() vira só uma validação
  // (navega, vê a página logada e retorna) — sem derrubar os outros robôs.
  const sharedCookies = l1session.loadSharedCookies();
  if (sharedCookies) {
    await l1session.injectSharedCookies(context, loginConfig.returnUrl, sharedCookies);
    console.error('[session] cookies compartilhados injetados — tentando reusar a sessão.');
  }
  const page = await context.newPage();
  await login(page, loginConfig);
  const saved = await l1session.persistSharedCookies(null, context, loginConfig.returnUrl);
  console.error(`[session] sessão ativa ${saved ? 'sincronizada no' : 'NÃO sincronizada no'} cache compartilhado.`);
  return { browser, context, page };
}

async function gerarRelatorio(page, { baseUrl, modelId, timeoutMs }) {
  const url = `${baseUrl}/agenda/GenericReport/?id=${modelId}`;
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await dismissOverlays(page);
  await waitForPageSettle(page, 2000);
  const ctx = await capturePageContext(page);
  if (isAuthenticationPage(ctx)) {
    throw new Error('Caiu em página de auth ao abrir o relatório.');
  }

  // Clica "Gerar" (botão submit do form). value="0" = gerar.
  const clicked = await clickFirstAvailable(page, [
    'button[name="ButtonSave"][value="0"]',
    'button[name="ButtonSave"]',
    'button:has-text("Gerar")',
  ]);
  if (!clicked) throw new Error('Botão "Gerar" não encontrado.');
  console.error('[gen] cliquei Gerar; aguardando o submit registrar o job…');

  // VALIDADO: o clique dispara um job SERVER-SIDE (o submit navega pra
  // /agenda/ReportAgenda/Search com o novo relatório em "Buscando dados") e a
  // geração CONCLUI sozinha (~45s) mesmo após o browser fechar. Então basta
  // confirmar que o disparo aconteceu — o Python faz o poll/download/ingest.
  let triggered = false;
  try {
    await page.waitForURL(/ReportAgenda\/Search/i, { timeout: Math.min(timeoutMs, 90000) });
    triggered = true;
  } catch (_) {
    const c = await capturePageContext(page);
    triggered = /relat[oó]rios gerados|buscando dados/i.test(`${c.title}\n${c.bodyStart}`);
  }
  await page.waitForTimeout(3000); // folga pro server registrar o job antes de fechar
  const final = await capturePageContext(page);
  console.error(`[gen] triggered=${triggered} | url=${final.url.slice(-50)}`);
  return { triggered, final };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const modelId = args.id || '627';
  const timeoutMs = (Number(args['timeout-min']) || 15) * 60 * 1000;
  const baseUrl = (args['base-url'] || process.env.LEGAL_ONE_WEB_URL || 'https://mdradvocacia.novajus.com.br').replace(/\/$/, '');

  const loginConfig = {
    username: requireEnvAny(['LEGALONE_WEB_USERNAME', 'LEGAL_ONE_WEB_USERNAME']),
    password: requireEnvAny(['LEGALONE_WEB_PASSWORD', 'LEGAL_ONE_WEB_PASSWORD']),
    keyLabel: requireEnvAny(['LEGALONE_WEB_KEY_LABEL', 'LEGAL_ONE_WEB_KEY_LABEL']),
    returnUrl: `${baseUrl}/agenda/GenericReport/?id=${modelId}`,
  };

  let session;
  try {
    session = await createLoggedInSession(loginConfig);
    const res = await gerarRelatorio(session.page, { baseUrl, modelId, timeoutMs });
    console.log(JSON.stringify({ status: res.triggered ? 'ok' : 'error', model_id: modelId, ...res }));
    process.exitCode = res.triggered ? 0 : 1;
  } catch (err) {
    console.log(JSON.stringify({ status: 'error', model_id: modelId, error: String(err && err.message || err) }));
    process.exitCode = 1;
  } finally {
    if (session) await session.browser?.close().catch(() => {});
  }
}

main();
