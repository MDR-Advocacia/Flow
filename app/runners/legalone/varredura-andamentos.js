/**
 * Runner: varredura de andamentos no Legal One (Novajus).
 *
 * Pra cada lawsuit_id no input, navega DetailsAndamentos do processo
 * (renderOnlySection=True), raspa a tabela de andamentos, filtra os
 * ultimos `windowDays` dias, e devolve { lawsuitId, andamentos: [...] }
 * via arquivo de status.
 *
 * Reusa o fluxo de login OnePass do cancel-legacy-task.js (mesmas
 * env vars: LEGALONE_WEB_USERNAME / LEGALONE_WEB_PASSWORD /
 * LEGALONE_WEB_KEY_LABEL).
 *
 * Status incremental no output.json:
 *   {
 *     "generatedAt": "...",
 *     "state": "starting" | "running" | "completed" | "failed",
 *     "totalItems": N,
 *     "processedItems": N,
 *     "items": [
 *       { "processadoId": 1, "lawsuitId": 123, "status": "ok",
 *         "andamentos": [...] },
 *       { "processadoId": 2, "lawsuitId": 456, "status": "error",
 *         "error": "..." }
 *     ]
 *   }
 *
 * Exit 0 em sucesso (mesmo com items individuais em erro), 1 em falha
 * fatal (login impossivel, browser crash).
 */

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const BASE_URL =
  process.env.LEGALONE_WEB_BASE_URL || 'https://mdradvocacia.novajus.com.br';

const RUNNER_STATUS_OK = 'ok';
const RUNNER_STATUS_ERROR = 'error';
const RUNNER_STATUS_PENDING = 'pending';

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const cur = argv[i];
    if (!cur.startsWith('--')) continue;
    const key = cur.slice(2);
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
    const value = process.env[name];
    if (value) return value;
  }
  throw new Error(`Missing required env var. Tried: ${names.join(', ')}`);
}

function readJsonFile(filePath, fallback = null) {
  try {
    const raw = fs.readFileSync(filePath, 'utf8').replace(/^﻿/, '');
    return JSON.parse(raw);
  } catch (_) {
    return fallback;
  }
}

function writeJsonFile(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2));
}

async function waitForPageSettle(page, delayMs = 0) {
  await page
    .waitForLoadState('domcontentloaded', { timeout: 120000 })
    .catch(() => {});
  if (delayMs > 0) {
    await page.waitForTimeout(delayMs);
  }
  await page
    .waitForLoadState('networkidle', { timeout: 30000 })
    .catch(() => {});
}

async function firstExistingSelector(page, selectors) {
  for (const selector of selectors) {
    const handle = await page.$(selector).catch(() => null);
    if (handle) return selector;
  }
  return null;
}

async function clickFirstAvailable(page, selectors) {
  const selector = await firstExistingSelector(page, selectors);
  if (!selector) return false;
  await page.click(selector, { timeout: 30000 });
  return true;
}

async function fillFirstAvailable(page, selectors, value) {
  const selector = await firstExistingSelector(page, selectors);
  if (!selector) return false;
  await page.fill(selector, value, { timeout: 30000 });
  return true;
}

async function completeKeySelectionIfPresent(page, keyLabel) {
  const body = await page.locator('body').innerText().catch(() => '');
  if (
    !body ||
    (!/Selecione uma chave de registro/i.test(body) && !body.includes(keyLabel))
  ) {
    return false;
  }
  await page
    .getByText(keyLabel, { exact: false })
    .first()
    .click({ timeout: 30000 });
  await page
    .getByRole('button', { name: /Continuar/i })
    .click({ timeout: 30000 });
  return true;
}

function isAuthenticationContext(text) {
  const lower = String(text || '').toLowerCase();
  return (
    lower.includes('signon.thomsonreuters.com') ||
    lower.includes('auth.thomsonreuters.com') ||
    lower.includes('novajus.com.br/conta/login') ||
    lower.includes('loginonepass') ||
    lower.includes('onepass') ||
    lower.includes('selecione uma chave de registro')
  );
}

async function capturePageContext(page) {
  return page
    .evaluate(() => {
      const bodyText = document.body ? document.body.innerText || '' : '';
      return {
        url: window.location.href,
        title: document.title || '',
        bodyStart: bodyText.slice(0, 1500),
      };
    })
    .catch(() => ({ url: page.url(), title: '', bodyStart: '' }));
}

async function login(page, { username, password, keyLabel, returnUrl }) {
  await page.goto(returnUrl, {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  });
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
      const filled = await fillFirstAvailable(
        page,
        ['input[name="username"]', 'input[name="email"]', 'input[type="email"]'],
        username,
      );
      if (filled) {
        await clickFirstAvailable(page, [
          'button[name="action"]',
          'button[type="submit"]',
        ]);
        await waitForPageSettle(page, 4000);
        continue;
      }
    }

    if (page.url().includes('/u/login/password')) {
      const filled = await fillFirstAvailable(
        page,
        ['#password', 'input[name="password"]', '#Password'],
        password,
      );
      if (filled) {
        await clickFirstAvailable(page, [
          'button[name="action"]',
          'button[type="submit"]',
          '#SignIn',
        ]);
        await waitForPageSettle(page, 6000);
        continue;
      }
    }

    if (
      (await firstExistingSelector(page, ['#Username'])) &&
      (await firstExistingSelector(page, ['#Password']))
    ) {
      const initialUrl = page.url();
      await page.fill('#Username', username, { timeout: 30000 });
      await page.locator('#Username').blur().catch(() => {});
      const redirected = await page
        .waitForURL((u) => u !== initialUrl, { timeout: 5000 })
        .then(() => true)
        .catch(() => false);
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
    const blob = `${ctx.url}\n${ctx.title}\n${ctx.bodyStart}`;
    if (!isAuthenticationContext(blob)) {
      return;
    }
  }

  const ctx = await capturePageContext(page);
  throw new Error(
    `Login OnePass nao finalizou. url=${ctx.url} title=${ctx.title}`,
  );
}

async function dismissCookieBanner(page) {
  try {
    const accept = await page
      .$('#cookie-policy-accept, text=/Aceito esta pol[ií]tica/i')
      .catch(() => null);
    if (accept) {
      await accept.click({ timeout: 2000 }).catch(() => {});
    }
  } catch (_) {}
  try {
    await page.evaluate(() => {
      const id = '__rpa_kill_cookie_banner__';
      if (!document.getElementById(id)) {
        const style = document.createElement('style');
        style.id = id;
        style.textContent =
          'body > div.cookie-policy, div.cookie-policy { display:none !important; pointer-events:none !important; visibility:hidden !important; }';
        document.head.appendChild(style);
      }
    });
  } catch (_) {}
}


// ── Guarda de vazamento de browser ─────────────────────────────────────────
// O Chromium do Playwright não morre junto com o node: se o runner terminar
// (ou estourar) sem fechar o browser, sobra processo órfão no container — são
// ~7 por vazamento (pai + zygote + gpu + renderers + crashpad). Em 12/08/2026
// o container da API acumulou 112 chrome-headless assim, e como o servidor é
// compartilhado (o OneLog roda no mesmo host) isso derrubou o desempenho de
// tudo. Mesma família do bug corrigido no cancel-legacy-task.js em 05/08.
let browserAtivo = null;

async function fecharBrowserAtivo() {
  const b = browserAtivo;
  browserAtivo = null;
  if (b) {
    await b.close().catch(() => {});
  }
}

// Rede de segurança: mesmo fechando o browser, o node às vezes fica pendurado
// em handle aberto. Sem isso o processo vira zumbi do lado Python.
const EXIT_WATCHDOG_MS_GUARDA = 5000;
function armarWatchdogDeSaidaGuarda() {
  setTimeout(() => process.exit(), EXIT_WATCHDOG_MS_GUARDA).unref();
}

async function createLoggedInSession(loginConfig) {
  const launchOptions = { headless: true };
  if (process.env.PLAYWRIGHT_CHANNEL) {
    launchOptions.channel = process.env.PLAYWRIGHT_CHANNEL;
  }
  const browser = await chromium.launch(launchOptions);
  browserAtivo = browser;
  const context = await browser.newContext();
  // Bloqueia recursos pesados (imgs/fonts/media) — pagina interna do
  // L1 nao depende deles e isso acelera o load.
  await context.route('**/*', (route) => {
    const rt = route.request().resourceType();
    if (rt === 'image' || rt === 'media' || rt === 'font') {
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  await login(page, loginConfig);
  return { browser, context, page };
}

async function closeSession(session) {
  if (!session) return;
  await session.browser?.close().catch(() => {});
  if (browserAtivo === session.browser) browserAtivo = null;
}

function detailsUrl(lawsuitId) {
  // renderOnlySection=True devolve a tabela sem o chrome do app.
  return `${BASE_URL}/processos/processos/DetailsAndamentos/${lawsuitId}?renderOnlySection=True`;
}

/**
 * Raspa a tabela de andamentos. Estrutura confirmada por inspecao
 * (2026-05-19):
 *   <table class="webgrid grid-view-column-active">
 *     <tbody>
 *       <tr class="webgrid-row-style ...">
 *         <td>checkbox</td>
 *         <td>(vazio)</td>
 *         <td>icone</td>
 *         <td class="date-column">DATA dd/mm/yyyy</td>
 *         <td class="date-column">HORA HH:MM</td>
 *         <td>TIPO (Andamento|Publicacao|Intimacao Eletronica|...)</td>
 *         <td class="grid-column-pre">TEXTO COMPLETO | Movimentado por: X</td>
 *         ...
 *       </tr>
 *     </tbody>
 *   </table>
 */
async function scrapeAndamentos(page, lawsuitId) {
  await waitForPageSettle(page, 1500);

  const andamentos = await page.evaluate(() => {
    // Tabela com classe 'webgrid'. Fallback: qualquer tr com classe
    // 'webgrid-row-style' (caso o L1 mude o seletor da tabela).
    let trs = Array.from(
      document.querySelectorAll('table.webgrid tbody > tr.webgrid-row-style'),
    );
    if (trs.length === 0) {
      trs = Array.from(document.querySelectorAll('tr.webgrid-row-style'));
    }
    const out = [];
    const DATE_RE = /\d{2}\/\d{2}\/\d{4}/;

    for (const tr of trs) {
      const tds = Array.from(tr.querySelectorAll(':scope > td'));
      // Coletar date-columns (data + hora geralmente)
      const dateCells = tds.filter((td) =>
        td.classList.contains('date-column'),
      );
      if (dateCells.length === 0) continue;
      const dataStr = ((dateCells[0].innerText || '').trim()).match(DATE_RE);
      if (!dataStr) continue;
      const horaStr =
        dateCells.length > 1
          ? (dateCells[1].innerText || '').trim()
          : null;
      // Tipo: primeiro <td> apos a ultima date-column que NAO seja
      // grid-column-pre nem date-column.
      const lastDateIdx = tds.indexOf(dateCells[dateCells.length - 1]);
      let tipoStr = null;
      for (let i = lastDateIdx + 1; i < tds.length; i += 1) {
        if (
          !tds[i].classList.contains('grid-column-pre') &&
          !tds[i].classList.contains('date-column')
        ) {
          const t = (tds[i].innerText || '').trim();
          if (t.length > 0) {
            tipoStr = t;
            break;
          }
        }
      }
      // Texto: maior td.grid-column-pre com conteudo nao-vazio
      let texto = '';
      const preCells = tds.filter((td) =>
        td.classList.contains('grid-column-pre'),
      );
      for (const pre of preCells) {
        const t = (pre.innerText || '').trim();
        if (t.length > texto.length) texto = t;
      }
      if (!texto) continue;

      // movimentadoPor: tipicamente apos "| Movimentado por: X" no
      // proprio texto. Extrai e remove do texto pra deixar limpo.
      let movimentadoPor = null;
      const mov = texto.match(/Movimentado por[:\s]+([^\n\r|]+)/i);
      if (mov) {
        movimentadoPor = mov[1].trim();
      }

      out.push({
        data: dataStr[0],
        hora: horaStr,
        tipo: tipoStr,
        texto,
        movimentadoPor,
      });
    }
    return out;
  });

  return andamentos;
}

function filterByWindow(andamentos, windowDays) {
  if (!Number.isFinite(windowDays) || windowDays <= 0) {
    return andamentos;
  }
  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - windowDays);
  const out = [];
  for (const a of andamentos) {
    const m = (a.data || '').match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    if (!m) continue;
    const [_, dd, mm, yyyy] = m;
    const d = new Date(
      Number(yyyy),
      Number(mm) - 1,
      Number(dd),
      0,
      0,
      0,
      0,
    );
    if (d >= cutoff) out.push(a);
  }
  return out;
}

async function processItem(page, item, windowDays, loginConfig) {
  const url = detailsUrl(item.lawsuitId);
  // Goto + sanidade auth.
  let loaded = false;
  for (let attempt = 1; attempt <= 3 && !loaded; attempt += 1) {
    try {
      await page.goto(url, {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      });
    } catch (err) {
      if (attempt >= 3) throw err;
      continue;
    }
    await dismissCookieBanner(page);
    await page
      .waitForLoadState('networkidle', { timeout: 15000 })
      .catch(() => {});
    const ctx = await capturePageContext(page);
    const blob = `${ctx.url}\n${ctx.title}\n${ctx.bodyStart}`;
    if (isAuthenticationContext(blob)) {
      // Sessao expirou — relogga.
      await login(page, loginConfig);
      continue;
    }
    loaded = true;
  }
  if (!loaded) {
    throw new Error('Nao foi possivel abrir DetailsAndamentos (auth loop).');
  }

  const all = await scrapeAndamentos(page, item.lawsuitId);
  const filtered = filterByWindow(all, windowDays);
  return filtered;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const inputPath = args.input;
  if (!inputPath) {
    throw new Error('Use --input <json> --output <json>');
  }
  const outputPath =
    args.output ||
    path.join(path.dirname(inputPath), `varredura-${Date.now()}.json`);
  const maxAttempts = Math.max(1, Number(args['max-attempts'] || '2'));

  const inputData = readJsonFile(inputPath, null);
  if (!inputData || !Array.isArray(inputData.items)) {
    throw new Error('Input invalido — esperado { windowDays, items: [...] }');
  }
  const windowDays = Number(inputData.windowDays || 30);
  const items = inputData.items;

  const username = requireEnvAny([
    'LEGALONE_WEB_USERNAME',
    'LEGAL_ONE_WEB_USERNAME',
  ]);
  const password = requireEnvAny([
    'LEGALONE_WEB_PASSWORD',
    'LEGAL_ONE_WEB_PASSWORD',
  ]);
  const keyLabel = requireEnvAny([
    'LEGALONE_WEB_KEY_LABEL',
    'LEGAL_ONE_WEB_KEY_LABEL',
  ]);
  const loginConfig = {
    username,
    password,
    keyLabel,
    returnUrl: `${BASE_URL}/home`,
  };

  const results = items.map((it) => ({
    processadoId: it.processadoId,
    lawsuitId: it.lawsuitId,
    cnjNumber: it.cnjNumber || null,
    status: RUNNER_STATUS_PENDING,
    andamentos: [],
    error: null,
    attempts: 0,
    startedAt: null,
    finishedAt: null,
  }));

  const persist = (state) => {
    writeJsonFile(outputPath, {
      generatedAt: new Date().toISOString(),
      state,
      totalItems: items.length,
      processedItems: results.filter(
        (r) => r.status !== RUNNER_STATUS_PENDING,
      ).length,
      windowDays,
      items: results,
    });
  };

  persist('starting');

  let session = null;
  try {
    session = await createLoggedInSession(loginConfig);
    persist('running');

    for (let i = 0; i < items.length; i += 1) {
      const item = items[i];
      const result = results[i];
      result.startedAt = new Date().toISOString();
      for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        result.attempts = attempt;
        try {
          const andamentos = await processItem(
            session.page,
            item,
            windowDays,
            loginConfig,
          );
          result.andamentos = andamentos;
          result.status = RUNNER_STATUS_OK;
          result.error = null;
          break;
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          result.error = msg;
          if (attempt >= maxAttempts) {
            result.status = RUNNER_STATUS_ERROR;
            break;
          }
          // Tenta reciclar a sessao em erro auth/timeout.
          if (/auth|login|timeout|navigation|closed/i.test(msg)) {
            try {
              await closeSession(session);
            } catch (_) {}
            session = await createLoggedInSession(loginConfig);
          }
        }
      }
      result.finishedAt = new Date().toISOString();
      persist('running');
      console.log(
        JSON.stringify({
          lawsuitId: item.lawsuitId,
          status: result.status,
          andamentos: result.andamentos.length,
          error: result.error,
        }),
      );
    }

    persist('completed');
  } catch (fatal) {
    persist('failed');
    throw fatal;
  } finally {
    await closeSession(session);
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
}).finally(async () => {
  await fecharBrowserAtivo();
  armarWatchdogDeSaidaGuarda();
});
