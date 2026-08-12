const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const l1session = require('./l1-session');

const PUBLICATION_TREATMENT_ENDPOINT =
  'https://legalone-prod-webapp-eastus2-api.azure-api.net/prod//webapi/api/internal/publications/SetPublicationTreatStatus/';

const TARGET_STATUS_TO_TREAT_CODE = {
  TRATADA: 1,
  SEM_PROVIDENCIAS: 2,
};

const RUNNER_STATUS_PENDING = 'pending';
const RUNNER_STATUS_TREATED = 'treated';
const RUNNER_STATUS_WITHOUT_PROVIDENCE = 'without_providence';
const RUNNER_STATUS_SCHEDULED_RETRY = 'scheduled_retry';
const RUNNER_STATUS_ERROR = 'error';

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const current = argv[index];
    if (!current.startsWith('--')) continue;
    const key = current.slice(2);
    const next = argv[index + 1];
    if (!next || next.startsWith('--')) {
      args[key] = true;
      continue;
    }
    args[key] = next;
    index += 1;
  }
  return args;
}

function requireEnv(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required env var: ${name}`);
  }
  return value;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readJsonFile(filePath, fallback = null) {
  try {
    const raw = fs.readFileSync(filePath, 'utf8').replace(/^\uFEFF/, '');
    return JSON.parse(raw);
  } catch (error) {
    return fallback;
  }
}

function writeJsonFile(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2));
}

function readControlSignal(controlFilePath) {
  if (!controlFilePath || !fs.existsSync(controlFilePath)) {
    return 'run';
  }
  const signal = fs.readFileSync(controlFilePath, 'utf8').trim().toLowerCase();
  if (!signal || signal === 'run' || signal === 'resume') {
    return 'run';
  }
  if (signal === 'pause') {
    return 'pause';
  }
  if (signal === 'stop') {
    return 'stop';
  }
  return 'run';
}

function sanitizeFileSegment(value) {
  const normalized = String(value ?? '')
    .normalize('NFKD')
    .replace(/[^\w.-]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 80);
  return normalized || 'item';
}

function normalizeSequenceNumber(value, fallback = 1) {
  const numeric = Number(value || fallback);
  return String(Number.isFinite(numeric) ? numeric : fallback).padStart(4, '0');
}

function targetStatusToSuccessStatus(targetStatus) {
  return targetStatus === 'SEM_PROVIDENCIAS' ? 'without_providence' : 'treated';
}

function isRetryableError(error) {
  const text = String(error?.message || error || '').toLowerCase();
  return (
    text.includes('timeout') ||
    text.includes('econnreset') ||
    text.includes('socket') ||
    text.includes('401') ||
    text.includes('403') ||
    text.includes('unauthorized') ||
    text.includes('forbidden') ||
    text.includes('subscription') ||
    text.includes('authentication')
  );
}

async function waitForPageSettle(page, delayMs = 0) {
  await page.waitForLoadState('domcontentloaded', { timeout: 120000 }).catch(() => {});
  if (delayMs > 0) {
    await page.waitForTimeout(delayMs);
  }
  await page.waitForLoadState('networkidle', { timeout: 45000 }).catch(() => {});
}

async function firstExistingSelector(page, selectors) {
  for (const selector of selectors) {
    const handle = await page.$(selector).catch(() => null);
    if (handle) {
      return selector;
    }
  }
  return null;
}

async function clickFirstAvailable(page, selectors) {
  const selector = await firstExistingSelector(page, selectors);
  if (!selector) {
    return false;
  }
  await page.click(selector, { timeout: 30000 });
  return true;
}

async function fillFirstAvailable(page, selectors, value) {
  const selector = await firstExistingSelector(page, selectors);
  if (!selector) {
    return false;
  }
  await page.fill(selector, value, { timeout: 30000 });
  return true;
}

function isAuthenticationPage(context) {
  const text = `${context.url}\n${context.title}\n${context.bodyStart}`.toLowerCase();
  return (
    text.includes('signon.thomsonreuters.com') ||
    text.includes('auth.thomsonreuters.com') ||
    text.includes('novajus.com.br/conta/login') ||
    text.includes('loginonepass') ||
    text.includes('onepass') ||
    text.includes('username') ||
    text.includes('password') ||
    text.includes('entrar') ||
    text.includes('autentica')
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
  // Start from the destination page itself so Legal One/Novajus keeps the
  // current returnUrl through the Thomson Reuters OIDC handoff.
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

    // Thomson Reuters — fluxo novo de 2 etapas. Os checks por URL DEVEM vir
    // ANTES do fallback com #Username/#SignIn, senão o branch single-page
    // rouba a tela de identifier-only (que também tem #Username e #SignIn,
    // mas com botão disabled) e trava esperando o click aceitar.
    if (page.url().includes('/u/login/password')) {
      const filled = await fillFirstAvailable(
        page,
        ['#password', 'input[name="password"]', '#Password'],
        password,
      );
      if (filled) {
        await clickFirstAvailable(page, ['button[name="action"]', 'button[type="submit"]', '#SignIn']);
        await waitForPageSettle(page, 6000);
        continue;
      }
    }

    if (page.url().includes('/u/login/identifier')) {
      const filled = await fillFirstAvailable(
        page,
        ['input[name="username"]', 'input[name="email"]', 'input[type="email"]', '#Username'],
        username,
      );
      if (filled) {
        await clickFirstAvailable(page, ['button[name="action"]', 'button[type="submit"]', '#SignIn']);
        await waitForPageSettle(page, 4000);
        continue;
      }
    }

    // Novajus antigo single-page: tela com #Username + #Password + #SignIn
    // juntos. Hoje alguns tenants usam OnePass/SSO: ao preencher o username,
    // o front detecta conta SSO e redireciona sozinho pra Thomson Reuters
    // (auth.thomsonreuters.com/u/login/password) ANTES de habilitar o #SignIn.
    // Isso fazia page.click('#SignIn') travar com "element is not enabled"
    // seguido de "element was detached from the DOM" (TimeoutError em prod).
    //
    // Estratégia: preenche o username, observa se a URL muda em 3s. Se mudou,
    // foi auto-redirect — aborta o ramo e deixa o proximo iteration do loop
    // resolver pelo branch /u/login/password. Se nao mudou, eh Novajus legado
    // puro — completa o fluxo single-page normal.
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

      if (await firstExistingSelector(page, ['#Password'])) {
        await page.fill('#Password', password, { timeout: 30000 });
        await page.click('#SignIn', { timeout: 30000 });
      }
      await waitForPageSettle(page, 4000);
      continue;
    }

    const context = await capturePageContext(page);
    if (!isAuthenticationPage(context)) {
      return;
    }
  }

  const finalContext = await capturePageContext(page);
  if (isAuthenticationPage(finalContext)) {
    throw new Error(
      `Authentication flow did not finish | url=${finalContext.url} | title=${finalContext.title || ''} | body=${(finalContext.bodyStart || '').slice(0, 400)}`,
    );
  }
}

async function capturePageContext(page) {
  return page
    .evaluate(() => {
      const bodyText = document.body ? document.body.innerText || '' : '';
      return {
        url: window.location.href,
        title: document.title || '',
        bodyStart: bodyText.slice(0, 1200),
      };
    })
    .catch(() => ({
      url: page.url(),
      title: '',
      bodyStart: '',
    }));
}

async function writeDiagnosticArtifact(page, item, attemptNumber, artifactsDir, payload) {
  if (!artifactsDir) {
    return {};
  }

  fs.mkdirSync(artifactsDir, { recursive: true });
  const sequenceNumber = normalizeSequenceNumber(item.sequenceNumber || item.index);
  const baseName = `${sequenceNumber}-${sanitizeFileSegment(item.cnj || item.publicationId)}-attempt-${attemptNumber}`;
  const jsonPath = path.join(artifactsDir, `${baseName}.json`);
  const screenshotPath = path.join(artifactsDir, `${baseName}.png`);

  const diagnostic = {
    capturedAt: new Date().toISOString(),
    item,
    attemptNumber,
    payload,
  };

  try {
    diagnostic.page = await capturePageContext(page);
  } catch (error) {
    diagnostic.pageError = error instanceof Error ? error.message : String(error);
  }

  try {
    if (page && !page.isClosed()) {
      await page.screenshot({ path: screenshotPath, fullPage: true, timeout: 30000 });
      diagnostic.screenshotPath = screenshotPath;
    }
  } catch (error) {
    diagnostic.screenshotError = error instanceof Error ? error.message : String(error);
  }

  writeJsonFile(jsonPath, diagnostic);
  return {
    diagnosticJsonPath: jsonPath,
    diagnosticScreenshotPath: diagnostic.screenshotPath || null,
  };
}

async function createLoggedInSession(loginConfig, firstPublicationId) {
  // Use Playwright's bundled Chromium so the runner works in the slim
  // API container (no Google Chrome installed there). Locally, you can
  // force "chrome" via PLAYWRIGHT_CHANNEL=chrome if desired.
  const launchOptions = { headless: true };
  if (process.env.PLAYWRIGHT_CHANNEL) {
    launchOptions.channel = process.env.PLAYWRIGHT_CHANNEL;
  }
  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext();
  await context.route('**/*', (route) => {
    const resourceType = route.request().resourceType();
    if (resourceType === 'image' || resourceType === 'media' || resourceType === 'font') {
      return route.abort();
    }
    return route.continue();
  });

  // Sessão compartilhada: com cookies válidos o login() vira só uma validação
  // (navega, vê a home logada e retorna) — sem derrubar os outros robôs.
  const sharedCookies = l1session.loadSharedCookies();
  if (sharedCookies) {
    await l1session.injectSharedCookies(context, loginConfig.returnUrl, sharedCookies);
    console.error('[session] cookies compartilhados injetados — tentando reusar a sessão.');
  }

  const page = await context.newPage();
  await login(page, loginConfig);
  // Regrava o cache (sessão validada agora — herda pra todo o sistema).
  const saved = await l1session.persistSharedCookies(null, context, loginConfig.returnUrl);
  console.error(`[session] sessão ativa ${saved ? 'sincronizada no' : 'NÃO sincronizada (sem .ASPXAUTH) no'} cache compartilhado.`);
  const apiHeaders = await captureApiHeaders(page, firstPublicationId);

  return { browser, context, page, apiHeaders };
}

async function closeSession(session) {
  if (!session) return;
  await session.browser?.close().catch(() => {});
}

async function captureApiHeaders(page, publicationId) {
  const captured = {};
  const handler = (request) => {
    const url = request.url();
    if (!url.includes('/webapi/api/internal/publications')) {
      return;
    }
    const headers = request.headers();
    for (const key of [
      'authorization',
      'ocp-apim-subscription-key',
      'authenticationmethod',
      'distribution',
      'tenancy',
    ]) {
      if (headers[key]) {
        captured[key] = headers[key];
      }
    }
  };

  page.on('request', handler);
  try {
    const targetUrl = `https://firm.legalone.com.br/publications?publicationId=${publicationId}&treatStatus=3`;
    for (let attempt = 1; attempt <= 4; attempt += 1) {
      await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 120000 });
      await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});
      await page.waitForTimeout(4000);
      if (captured.authorization && captured['ocp-apim-subscription-key']) {
        break;
      }
    }
  } finally {
    page.off('request', handler);
  }

  if (!captured.authorization || !captured['ocp-apim-subscription-key']) {
    throw new Error('Nao foi possivel capturar os headers internos do modulo de publicacoes.');
  }

  return {
    authorization: captured.authorization,
    'ocp-apim-subscription-key': captured['ocp-apim-subscription-key'],
    authenticationmethod: captured.authenticationmethod || 'ASYMMETRIC_JWT_TOKEN',
    distribution: captured.distribution || 'FirmsBrazil',
    tenancy: captured.tenancy,
    origin: 'https://firm.legalone.com.br',
    referer: 'https://firm.legalone.com.br/',
    accept: 'application/json, text/plain, */*',
    'content-type': 'application/json;charset=UTF-8',
  };
}

async function submitTreatment(session, item) {
  const treatCode = TARGET_STATUS_TO_TREAT_CODE[item.targetStatus];
  if (!treatCode) {
    throw new Error(`Target status invalido: ${item.targetStatus}`);
  }

  const response = await session.context.request.post(PUBLICATION_TREATMENT_ENDPOINT, {
    headers: session.apiHeaders,
    data: {
      publicationId: Number(item.publicationId),
      treatStatus: treatCode,
      type: '[Publication] setPublicationTreatedStatus',
    },
    timeout: 60000,
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = { raw: await response.text().catch(() => null) };
  }

  if (!response.ok) {
    throw new Error(`HTTP ${response.status} ao tratar publicação ${item.publicationId}`);
  }
  if (!payload || payload.success !== true) {
    throw new Error(payload?.message || `Falha sem detalhe ao tratar publicação ${item.publicationId}`);
  }

  return {
    status: targetStatusToSuccessStatus(item.targetStatus),
    response: payload,
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const inputPath = args.input;
  if (!inputPath) {
    throw new Error('Use --input <json>');
  }

  const items = readJsonFile(inputPath, []);
  if (!Array.isArray(items)) {
    throw new Error('Arquivo de entrada invalido.');
  }

  const username = requireEnv('LEGALONE_WEB_USERNAME');
  const password = requireEnv('LEGALONE_WEB_PASSWORD');
  const keyLabel = requireEnv('LEGALONE_WEB_KEY_LABEL');

  const batchSize = Math.max(1, Number(args['batch-size'] || '20'));
  const maxAttempts = Math.max(1, Number(args['max-attempts'] || '3'));
  const pauseBetweenBatchesSeconds = Math.max(0, Number(args['pause-between-batches-seconds'] || '5'));
  const pauseBetweenBatchesMs = pauseBetweenBatchesSeconds * 1000;
  const outputPath =
    args.output || path.join(path.dirname(inputPath), `publication-treatment-${Date.now()}.json`);
  const controlFilePath =
    args['control-file'] || path.join(path.dirname(outputPath), 'publication-treatment.control');
  const artifactsDir = args['artifacts-dir'] || null;

  const loginConfig = {
    username,
    password,
    keyLabel,
    returnUrl: 'https://mdradvocacia.novajus.com.br/home',
  };

  const resultsMap = new Map();
  const resultOrder = [];
  const upsertResult = (payload) => {
    const sequenceNumber = normalizeSequenceNumber(payload.sequenceNumber || payload.index, resultOrder.length + 1);
    const key = String(payload.queueItemId || sequenceNumber);
    const normalized = {
      ...payload,
      sequenceNumber,
    };
    if (!resultsMap.has(key)) {
      resultOrder.push(key);
    }
    resultsMap.set(key, normalized);
    return normalized;
  };

  for (const item of items) {
    upsertResult({
      ...item,
      status: RUNNER_STATUS_PENDING,
      attempts: 0,
      retryPending: false,
      startedAt: null,
      finishedAt: null,
      error: null,
      response: null,
    });
  }

  let activeQueueType = 'primary';
  let activeQueueProcessed = 0;
  let activeQueueTotal = items.length;
  let retryPass = 0;

  const getResults = () => resultOrder.map((key) => resultsMap.get(key));
  const buildPayload = (state, extra = {}) => {
    const results = getResults();
    const successCount = results.filter((item) => ['treated', 'without_providence'].includes(item.status)).length;
    const failedCount = results.filter((item) => item.status === 'error').length;
    const retryPendingCount = results.filter((item) => item.status === 'scheduled_retry' || item.retryPending).length;
    const processedItems = results.filter((item) => item.finishedAt).length;
    return {
      generatedAt: new Date().toISOString(),
      state,
      batchSize,
      totalBatches: activeQueueTotal ? Math.max(1, Math.ceil(activeQueueTotal / batchSize)) : 1,
      currentBatch: activeQueueTotal ? Math.min(Math.ceil(activeQueueTotal / batchSize), Math.floor(activeQueueProcessed / batchSize) + 1) : 1,
      controlFile: controlFilePath,
      totalItems: items.length,
      processedItems,
      successCount,
      failedCount,
      retryPendingCount,
      remainingItems: Math.max(0, items.length - successCount - failedCount),
      activeQueueType,
      retryPass,
      maxAttempts,
      items: results,
      ...extra,
    };
  };
  const persistPayload = (state, extra = {}) => {
    writeJsonFile(outputPath, buildPayload(state, extra));
  };

  const waitForRunSignal = async () => {
    while (true) {
      const signal = readControlSignal(controlFilePath);
      if (signal === 'stop') {
        persistPayload('stopped');
        return 'stop';
      }
      if (signal === 'pause') {
        persistPayload('paused');
        await sleep(2000);
        continue;
      }
      return 'run';
    }
  };

  const waitBetweenBatches = async () => {
    if (!pauseBetweenBatchesMs) {
      return 'run';
    }
    let remainingMs = pauseBetweenBatchesMs;
    while (remainingMs > 0) {
      const signal = readControlSignal(controlFilePath);
      if (signal === 'stop') {
        persistPayload('stopped');
        return 'stop';
      }
      if (signal === 'pause') {
        persistPayload('paused');
        await sleep(2000);
        continue;
      }
      const sleepWindow = Math.min(1000, remainingMs);
      persistPayload('sleeping', { sleepUntil: new Date(Date.now() + remainingMs).toISOString() });
      await sleep(sleepWindow);
      remainingMs -= sleepWindow;
    }
    return 'run';
  };

  persistPayload('starting');
  fs.mkdirSync(path.dirname(controlFilePath), { recursive: true });
  fs.writeFileSync(controlFilePath, 'run');

  let session = null;
  if (items.length > 0) {
    session = await createLoggedInSession(loginConfig, items[0].publicationId);
  }

  let pendingItems = items.map((item) => ({
    ...item,
    sequenceNumber: normalizeSequenceNumber(item.sequenceNumber || item.index, item.index),
    attemptNumber: 1,
  }));
  let deferredRetryItems = [];
  let stopRequested = false;

  while (pendingItems.length || deferredRetryItems.length) {
    if (!pendingItems.length) {
      pendingItems = [...deferredRetryItems];
      deferredRetryItems = [];
      if (!pendingItems.length) {
        break;
      }
      activeQueueType = 'retry';
      retryPass += 1;
      activeQueueProcessed = 0;
      activeQueueTotal = pendingItems.length;
      persistPayload('running');
    }

    for (let index = 0; index < pendingItems.length; index += 1) {
      const signal = await waitForRunSignal();
      if (signal === 'stop') {
        stopRequested = true;
        break;
      }

      const item = pendingItems[index];
      const attemptNumber = Number(item.attemptNumber || 1);
      const startedAt = new Date().toISOString();
      let finalResult = null;
      let shouldRestartSession = false;

      try {
        finalResult = await submitTreatment(session, item);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const shouldRetry = attemptNumber < maxAttempts && isRetryableError(error);
        const diagnostics = await writeDiagnosticArtifact(session?.page, item, attemptNumber, artifactsDir, {
          error: message,
        });

        finalResult = {
          status: shouldRetry ? 'scheduled_retry' : 'error',
          response: null,
          error: message,
          retryPending: shouldRetry,
          ...diagnostics,
        };

        if (shouldRetry) {
          deferredRetryItems.push({
            ...item,
            attemptNumber: attemptNumber + 1,
          });
        }
        shouldRestartSession = shouldRetry;
      }

      if (finalResult && finalResult.status !== 'scheduled_retry') {
        finalResult.retryPending = false;
      }

      const result = upsertResult({
        ...item,
        status: finalResult.status,
        finalStatus: finalResult.status,
        attempts: attemptNumber,
        retryPending: Boolean(finalResult.retryPending),
        startedAt,
        finishedAt: new Date().toISOString(),
        response: finalResult.response || null,
        error: finalResult.error || null,
        diagnosticJsonPath: finalResult.diagnosticJsonPath || null,
        diagnosticScreenshotPath: finalResult.diagnosticScreenshotPath || null,
      });

      activeQueueProcessed += 1;
      persistPayload('running');
      console.log(JSON.stringify(result));

      if (shouldRestartSession) {
        await closeSession(session);
        session = await createLoggedInSession(loginConfig, item.publicationId);
      }

      const isBatchBoundary = (index + 1) % batchSize === 0;
      const hasMoreItems = index + 1 < pendingItems.length;
      if (isBatchBoundary && hasMoreItems) {
        const batchSignal = await waitBetweenBatches();
        if (batchSignal === 'stop') {
          stopRequested = true;
          break;
        }
      }
    }

    if (stopRequested) {
      break;
    }

    pendingItems = [];
  }

  const finalPayload = buildPayload('running');
  const remainingItems = finalPayload.remainingItems;
  persistPayload(remainingItems > 0 ? 'stopped' : 'completed');
  await closeSession(session);
}

main().catch((error) => {
  console.error(error);
  // Try to record a terminal failure state in the status file so the Python
  // side stops treating a crashed runner as if it were still "starting/running".
  try {
    const args = parseArgs(process.argv.slice(2));
    const outputPath = args.output;
    if (outputPath) {
      let existing = readJsonFile(outputPath, null) || {};
      existing.generatedAt = new Date().toISOString();
      existing.state = 'failed';
      existing.errorMessage = error instanceof Error ? error.message : String(error);
      writeJsonFile(outputPath, existing);
    }
  } catch (persistError) {
    console.error('Failed to persist runner failure state:', persistError);
  }
  process.exitCode = 1;
});
