const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const RUNNER_STATUS_PENDING = 'pending';
const RUNNER_STATUS_CANCELLED = 'cancelled';
const RUNNER_STATUS_ALREADY_CANCELLED = 'already_cancelled';
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

function requireEnvAny(names) {
  for (const name of names) {
    const value = process.env[name];
    if (value) {
      return value;
    }
  }
  throw new Error(`Missing required env var. Tried: ${names.join(', ')}`);
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

function normalizeSequenceNumber(value, fallback = 1) {
  const numeric = Number(value || fallback);
  return String(Number.isFinite(numeric) ? numeric : fallback).padStart(4, '0');
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

function isRetryableError(error) {
  const text = String(error?.message || error || '').toLowerCase();
  return (
    text.includes('timeout') ||
    text.includes('navigation') ||
    text.includes('execution context was destroyed') ||
    text.includes('target page, context or browser has been closed') ||
    text.includes('401') ||
    text.includes('403') ||
    text.includes('unauthorized') ||
    text.includes('forbidden') ||
    text.includes('login') ||
    text.includes('signon')
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
  // Opening the destination page directly preserves the intended returnUrl
  // through the new Thomson Reuters + Novajus OnePass redirect chain.
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
      const filled = await fillFirstAvailable(
        page,
        ['input[name="username"]', 'input[name="email"]', 'input[type="email"]'],
        username,
      );
      if (filled) {
        await clickFirstAvailable(page, ['button[name="action"]', 'button[type="submit"]']);
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
        await clickFirstAvailable(page, ['button[name="action"]', 'button[type="submit"]', '#SignIn']);
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
        bodyStart: bodyText.slice(0, 1500),
      };
    })
    .catch(() => ({
      url: page.url(),
      title: '',
      bodyStart: '',
    }));
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

async function writeDiagnosticArtifact(page, item, attemptNumber, artifactsDir, payload) {
  if (!artifactsDir) {
    return {};
  }

  fs.mkdirSync(artifactsDir, { recursive: true });
  const sequenceNumber = normalizeSequenceNumber(item.sequenceNumber || item.index);
  const baseName = `${sequenceNumber}-${sanitizeFileSegment(item.cnj || item.taskId)}-attempt-${attemptNumber}`;
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

async function createLoggedInSession(loginConfig) {
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

  const page = await context.newPage();
  // Se `login` estoura, o `return` abaixo nunca acontece e quem chamou fica
  // com `session = null` — o `finally { closeSession(session) }` dele nao
  // fecha NADA e este browser vira orfao. Chromium aberto segura o event
  // loop do node pra sempre: o processo nao morre, so' e' morto no teto de
  // 180s do chamador, e ate' la' o lock da sessao web fica preso.
  // Aconteceu em 05/08/2026 08:44 — a propria pagina de login da novajus
  // devolveu erro de servidor ("Index was outside the bounds of the array"),
  // o login jogou "Authentication flow did not finish" e o node pendurou.
  try {
    await login(page, loginConfig);
  } catch (error) {
    await browser.close().catch(() => {});
    throw error;
  }
  return { browser, context, page };
}

async function closeSession(session) {
  if (!session) return;
  await session.browser?.close().catch(() => {});
}

async function dismissCookieBanner(page) {
  // Banner Thomson Reuters (body > div.cookie-policy). Estrategia dupla:
  //   1) Tenta clicar #cookie-policy-accept (id estavel, observado 2026-05-06).
  //   2) Independente do click ter funcionado, INJETA CSS que mata
  //      div.cookie-policy + pointer-events. Mesmo padrao do
  //      dismissPendoOverlay — garante que o banner nao volte a
  //      interferir mesmo que o L1 re-renderize.
  // Idempotente. No-op se nao tiver banner.
  try {
    for (const frame of page.frames()) {
      const accept = await frame
        .$('#cookie-policy-accept, text=/Aceito esta pol[ií]tica/i')
        .catch(() => null);
      if (accept) {
        await accept.click({ timeout: 2000 }).catch(() => {});
        await page.waitForTimeout(250);
        break;
      }
    }
  } catch (_) {}
  // CSS-kill defensivo (sempre roda, mesmo se click acima rodou). Evita
  // que o banner volte a interceptar pointer-events em frames ou em
  // re-render apos navegacao dentro do mesmo run.
  try {
    await page.evaluate(() => {
      const STYLE_ID = '__rpa_kill_cookie_banner__';
      if (!document.getElementById(STYLE_ID)) {
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent =
          'body > div.cookie-policy, div.cookie-policy { display: none !important; pointer-events: none !important; visibility: hidden !important; }';
        document.head.appendChild(style);
      }
      document.querySelectorAll('div.cookie-policy').forEach((el) => {
        el.style.display = 'none';
        el.style.pointerEvents = 'none';
      });
    });
  } catch (_) {
    // page pode nao estar pronta ainda — no-op.
  }
  return true;
}

async function dismissPendoOverlay(page) {
  // O L1 web exibe popups do Pendo (NPS, anuncios, tour) sobre o app.
  // Esses overlays interceptam pointer events e impedem clicks no
  // botao "Salvar" do modal de alteracao em lote. Em 2026-05-04 o popup
  // "Sua opiniao constroi o futuro do Legal One" derrubou o cancelamento
  // (RPA tentava clicar no botao por baixo, Pendo recebia o click).
  //
  // Estrategia: injeta CSS persistente que mata #pendo-base e qualquer
  // descendente "_pendo-*". Como o Pendo recria o overlay quando o user
  // dismissa via UI, o CSS garante que mesmo que volte a renderizar fica
  // escondido. Nao depende de seletor especifico de botao "X" (que muda
  // entre versoes do Pendo). Idempotente.
  try {
    await page.evaluate(() => {
      const STYLE_ID = '__rpa_kill_pendo__';
      if (!document.getElementById(STYLE_ID)) {
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent =
          '#pendo-base, [id^="pendo-"], [class*="_pendo-"] { display: none !important; pointer-events: none !important; }';
        document.head.appendChild(style);
      }
      document
        .querySelectorAll('#pendo-base, [id^="pendo-"]')
        .forEach((el) => {
          el.style.display = 'none';
          el.style.pointerEvents = 'none';
        });
    });
  } catch (_) {
    // page pode nao estar pronta ainda (navigation em andamento) — no-op.
  }
}

async function submitCancellationViaBatchModal(page, item, loginConfig) {
  // Fluxo:
  //   1. Goto detailsUrl (lista de tasks do processo)
  //   2. Marca checkbox da task alvo (#grid_check_{taskId})
  //   3. Clica na engrenagem do toolbar (.toolbar-default-action .popover-menu-button)
  //   4. Clica em "Alterar" (#toolbar-item a)
  //   5. No modal "Alterando compromisso(s) e tarefa(s)":
  //      a. Preenche o lookup "Campo" com "Status"
  //      b. Aguarda lookup "Para" (#LookupStatusLote) habilitar
  //      c. Preenche o lookup "Para" com "Cancelado"
  //   6. Clica em Salvar (button.toolbar-modal-submit)
  //   7. Aguarda fechar modal (toast/redirect/networkidle)

  const detailsUrl = item.detailsUrl;
  if (!detailsUrl) {
    throw new Error('detailsUrl ausente no item — nao consigo abrir lista de tasks.');
  }

  // 1) Goto + dispense cookie banner + autenticacao se necessario
  let loaded = false;
  for (let attempt = 1; attempt <= 3 && !loaded; attempt += 1) {
    await page.goto(detailsUrl, { waitUntil: 'domcontentloaded', timeout: 120000 });
    await dismissCookieBanner(page);
    await dismissPendoOverlay(page);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const ctx = await capturePageContext(page);
    if (isAuthenticationPage(ctx)) {
      await login(page, loginConfig);
      continue;
    }
    loaded = true;
  }
  if (!loaded) {
    throw new Error('Nao consegui abrir DetailsCompromissosTarefas (auth loop).');
  }

  // 2) Marca o checkbox da task. O <input> eh hidden via CSS, entao
  // page.click() (mesmo com force) recusa. Solucao: chamar el.click()
  // direto via evaluate — o metodo nativo do DOM dispara o handler
  // independente de visibilidade. Idempotente (so clica se nao marcado).
  const checkboxSelector = `input.grid_check[data-val="${item.taskId}"]`;
  await page.waitForSelector(checkboxSelector, {
    state: 'attached',
    timeout: 15000,
  });
  await page.evaluate((tid) => {
    const cb = document.querySelector(`input.grid_check[data-val="${tid}"]`);
    if (!cb) {
      throw new Error(`Checkbox grid_check[data-val="${tid}"] nao encontrado.`);
    }
    if (!cb.checked) {
      cb.click(); // dispara o handler nativo + change event
    }
  }, String(item.taskId));

  // 3) Clica na engrenagem do toolbar da grid (popover de acoes em lote).
  // O seletor PRECISA ser escopado a `table.webgrid thead` — existem
  // outros `.popover-button-wrapper.toolbar-default-action` em outros
  // lugares da pagina (sidebar, etc). Sem o escopo, o click vai pro
  // wrapper errado e o popover certo nunca abre.
  const gearSelector =
    'table.webgrid thead .popover-button-wrapper.toolbar-default-action .toolbar-action-right.popover-menu-button';
  await page.waitForSelector(gearSelector, { timeout: 8000 });

  let popoverOpen = false;
  for (let attempt = 1; attempt <= 3 && !popoverOpen; attempt += 1) {
    await page.click(gearSelector, { timeout: 5000 });
    try {
      // Espera o <ul> do popover (no thead) ficar visible
      await page.waitForFunction(
        () => {
          const ul = document.querySelector(
            'table.webgrid thead ul.action-right-list.popover-menu-list',
          );
          return ul && ul.offsetParent !== null;
        },
        null,
        { timeout: 2000 },
      );
      popoverOpen = true;
    } catch (_) {
      // tenta de novo
    }
  }
  if (!popoverOpen) {
    throw new Error('Engrenagem do toolbar nao abriu o popover apos 3 tentativas.');
  }

  // 4) Clica em "Alterar" dentro do popover aberto (escopado ao thead).
  // Aguarda o <li id="toolbar-item"> ficar VISIBLE — depende do <ul>
  // pai ter saido de display:none.
  await page.waitForSelector('#toolbar-item:visible', { timeout: 5000 });
  await page.click(
    'table.webgrid thead ul.action-right-list.popover-menu-list #toolbar-item a',
    { timeout: 5000 },
  );

  // 5) Modal monta — aguarda body > div.modal ficar visivel + perder
  // a classe widget-loading no .toolbar-modal-content + #LookupCampo
  // de fato montar dentro dele.
  //
  // CONFIRMADO 2026-05-06 (reprod ao vivo no Chrome via JS .click()):
  // o modal abre rapido (1s) mas o conteudo interno (lookup customizado
  // "Campo") fica em `.toolbar-modal-content widget-loading` por tempo
  // indeterminado — em 30s+ as vezes nao saiu. Antes tinha .catch(()=>{})
  // silencioso engolindo o timeout de 10s e seguindo direto pro click do
  // #LookupCampo .lookup-show, que obviamente nao existia ainda — bati
  // em "page.click: Timeout 5000ms" e marcava FAILED. Fix: timeout 30s
  // SEM catch + waitForSelector explicito do .lookup-show antes de clicar.
  await page.waitForSelector('body > div.modal', {
    state: 'visible',
    timeout: 15000,
  });
  await page.waitForFunction(
    () => {
      const content = document.querySelector('.toolbar-modal-content');
      return content && !content.classList.contains('widget-loading');
    },
    null,
    { timeout: 30000 },
  );
  // Espera o lookup "Campo" de fato existir no DOM antes de tentar clicar.
  // waitForFunction acima diz "loading saiu", mas o widget customizado
  // pode levar mais alguns ms pra montar os elementos internos.
  await page.waitForSelector('#LookupCampo .lookup-show', {
    state: 'visible',
    timeout: 15000,
  });

  //   5a) Campo = Status. Lookup customizado: NAO funciona via
  //   autocomplete sintetico (testado). Usar o botao .lookup-show
  //   que abre o dropdown como tabela, depois clicar na <tr> com
  //   data-val-id="0" (Status). IDs documentados pelo investigador:
  //   Status=0, Descricao=1, Local=2, Executante=3, Responsavel=4,
  //   Solicitante=5, Escritorio origem=6, Escritorio responsavel=7,
  //   Etiquetas=12.
  await page.click('#LookupCampo .lookup-show', { timeout: 5000 });
  await page.click(
    '.lookup-dropdown.lookup-inside-modal:visible tr[data-val-id="0"]',
    { timeout: 5000 },
  );

  //   5b) Aguarda o lookup "Para" (#LookupStatusLote) ficar VISIVEL —
  //   ele soh aparece quando "Campo" = Status.
  await page.waitForFunction(
    () => {
      const wrapper = document.getElementById('LookupStatusLote');
      return wrapper && wrapper.offsetParent !== null;
    },
    null,
    { timeout: 10000 },
  );

  //   5c) Para = Cancelado. Mesmo padrao: .lookup-show -> tr[data-val-id="3"].
  //   Status IDs: Pendente=0, Cumprido=1, Nao cumprido=2, Cancelado=3,
  //   Iniciado=4, Reagendado=5.
  // Espera o .lookup-show do StatusLote estar visible — mesmo motivo
  // do #LookupCampo (widget pode demorar ms a mais pra montar interno).
  await page.waitForSelector('#LookupStatusLote .lookup-show', {
    state: 'visible',
    timeout: 10000,
  });
  await page.click('#LookupStatusLote .lookup-show', { timeout: 5000 });
  await page.click(
    `.lookup-dropdown.lookup-inside-modal:visible tr[data-val-id="${item.targetStatusId}"]`,
    { timeout: 5000 },
  );

  // 6) Clica em Salvar — o botao submit aparece DUAS vezes no DOM
  // (um eh 0x0 invisivel). Filtra pelo visivel. Mata o Pendo overlay
  // imediatamente antes pra evitar pointer-events interception (Pendo
  // pode renderizar o NPS popup tardiamente, depois do modal abrir).
  await dismissPendoOverlay(page);
  await page.click('button.toolbar-modal-submit:visible', { timeout: 5000 });

  // 7) Aguarda o modal fechar (sumir do DOM ou virar invisible)
  await page
    .waitForFunction(
      () => {
        const modal = document.querySelector('.toolbar-modal-submit');
        if (!modal) return true;
        const visible = modal.offsetParent !== null;
        return !visible;
      },
      null,
      { timeout: 30000 },
    )
    .catch(() => {});
  // Aguarda networkidle pra dar tempo do servidor processar
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

  // 8) Re-acessa o detailsUrl pra forcar refresh — garante que a UI
  // exibe o estado atualizado da task (em caso de qualquer cache do
  // proprio Novajus) e da tempo do servidor terminar de processar.
  // O Python depois valida via API L1 GET /Tasks/{id}.
  try {
    await page.goto(detailsUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
  } catch (_) {
    // Refresh eh best-effort; nao impede o sucesso.
  }

  return {
    alreadyCancelled: false,
    finalUrl: page.url(),
    submitMethod: 'batch_modal',
  };
}

async function cancelTask(session, item, loginConfig) {
  // Cancelamento via tela de "Compromissos e tarefas" do processo
  // (DetailsCompromissosTarefas) usando o modal "Alterar em lote". Esse
  // caminho EVITA a tela de edit individual da task — logo NAO sofre
  // com custom fields obrigatorios, EnvolvidoEfetivoId, popup "data
  // anterior", validacao de Workflow, etc. Cancela direto.
  const response = await submitCancellationViaBatchModal(
    session.page,
    item,
    loginConfig,
  );

  if (response.alreadyCancelled) {
    return {
      status: RUNNER_STATUS_ALREADY_CANCELLED,
      response,
    };
  }

  // Se nao deu exception ate aqui, o submit do modal "Alterar em lote"
  // foi clicado sem erro. A verificacao AUTORITATIVA de cancelamento
  // acontece no Python (`get_task_by_id` via API L1) — esse runner so
  // reporta que o fluxo de UI executou. Se o save nao persistiu, o
  // Python detecta pelo statusId retornado pela API e marca FAILED.
  return {
    status: RUNNER_STATUS_CANCELLED,
    response,
  };
}

// --login-only: abre browser, faz login OnePass, exporta cookies do
// contexto autenticado pra um JSON e sai. Usado pelo service Python
// HTTP (LegacyTaskHttpCancellationService) pra obter o `.ASPXAUTH`
// inicial sem ter que reimplementar o fluxo de SSO (Thomson Reuters
// OnePass + key selection) em Python. Output: { mode, capturedAt,
// cookies: { name: value, ... } }. Exit 0 em sucesso, 1 em falha.
async function runLoginOnlyMode(args) {
  const outputPath = args.output;
  if (!outputPath) {
    throw new Error('Use --login-only --output <json>');
  }

  const username = requireEnvAny(['LEGALONE_WEB_USERNAME', 'LEGAL_ONE_WEB_USERNAME']);
  const password = requireEnvAny(['LEGALONE_WEB_PASSWORD', 'LEGAL_ONE_WEB_PASSWORD']);
  const keyLabel = requireEnvAny(['LEGALONE_WEB_KEY_LABEL', 'LEGAL_ONE_WEB_KEY_LABEL']);

  const loginConfig = {
    username,
    password,
    keyLabel,
    returnUrl: 'https://mdradvocacia.novajus.com.br/home',
  };

  let session = null;
  try {
    session = await createLoggedInSession(loginConfig);
    const cookies = await session.context.cookies();
    const cookieMap = {};
    for (const c of cookies) {
      cookieMap[c.name] = c.value;
    }
    if (!cookieMap['.ASPXAUTH']) {
      throw new Error(
        'Login completou mas .ASPXAUTH nao foi setado — verifique credenciais/SSO.',
      );
    }
    writeJsonFile(outputPath, {
      mode: 'login-only',
      capturedAt: new Date().toISOString(),
      cookieCount: Object.keys(cookieMap).length,
      cookies: cookieMap,
    });
    console.log(JSON.stringify({
      status: 'ok',
      cookieCount: Object.keys(cookieMap).length,
      output: outputPath,
    }));
  } finally {
    await closeSession(session);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args['login-only']) {
    return runLoginOnlyMode(args);
  }

  const inputPath = args.input;
  if (!inputPath) {
    throw new Error('Use --input <json> (ou --login-only --output <json>)');
  }

  const items = readJsonFile(inputPath, []);
  if (!Array.isArray(items)) {
    throw new Error('Arquivo de entrada invalido.');
  }

  const username = requireEnvAny(['LEGALONE_WEB_USERNAME', 'LEGAL_ONE_WEB_USERNAME']);
  const password = requireEnvAny(['LEGALONE_WEB_PASSWORD', 'LEGAL_ONE_WEB_PASSWORD']);
  const keyLabel = requireEnvAny(['LEGALONE_WEB_KEY_LABEL', 'LEGAL_ONE_WEB_KEY_LABEL']);
  const maxAttempts = Math.max(1, Number(args['max-attempts'] || '2'));
  const outputPath =
    args.output || path.join(path.dirname(inputPath), `legacy-task-cancellation-${Date.now()}.json`);
  const artifactsDir = args['artifacts-dir'] || null;

  const loginConfig = {
    username,
    password,
    keyLabel,
    returnUrl: items[0]?.editUrl || 'https://mdradvocacia.novajus.com.br/home',
  };

  const resultsMap = new Map();
  const resultOrder = [];
  const upsertResult = (payload) => {
    const sequenceNumber = normalizeSequenceNumber(payload.sequenceNumber || payload.index, resultOrder.length + 1);
    const key = String(payload.taskId || sequenceNumber);
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
      startedAt: null,
      finishedAt: null,
      error: null,
      response: null,
    });
  }

  const getResults = () => resultOrder.map((key) => resultsMap.get(key));
  const buildPayload = (state, extra = {}) => {
    const results = getResults();
    const successCount = results.filter((item) =>
      [RUNNER_STATUS_CANCELLED, RUNNER_STATUS_ALREADY_CANCELLED].includes(item.status),
    ).length;
    const failedCount = results.filter((item) => item.status === RUNNER_STATUS_ERROR).length;
    return {
      generatedAt: new Date().toISOString(),
      state,
      totalItems: items.length,
      processedItems: results.filter((item) => item.finishedAt).length,
      successCount,
      failedCount,
      remainingItems: Math.max(0, items.length - successCount - failedCount),
      maxAttempts,
      items: results,
      ...extra,
    };
  };
  const persistPayload = (state, extra = {}) => {
    writeJsonFile(outputPath, buildPayload(state, extra));
  };

  persistPayload('starting');
  let session = null;
  if (items.length > 0) {
    session = await createLoggedInSession(loginConfig);
  }

  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    let finalResult = null;

    for (let attemptNumber = 1; attemptNumber <= maxAttempts; attemptNumber += 1) {
      const startedAt = new Date().toISOString();

      try {
        finalResult = await cancelTask(session, item, loginConfig);
        finalResult.startedAt = startedAt;
        finalResult.finishedAt = new Date().toISOString();
        finalResult.attempts = attemptNumber;
        break;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const diagnostics = await writeDiagnosticArtifact(session?.page, item, attemptNumber, artifactsDir, {
          error: message,
        });
        finalResult = {
          status: RUNNER_STATUS_ERROR,
          response: null,
          error: message,
          startedAt,
          finishedAt: new Date().toISOString(),
          attempts: attemptNumber,
          ...diagnostics,
        };

        if (attemptNumber < maxAttempts && isRetryableError(error)) {
          await closeSession(session);
          session = await createLoggedInSession(loginConfig);
          continue;
        }
        break;
      }
    }

    const result = upsertResult({
      ...item,
      status: finalResult.status,
      attempts: finalResult.attempts || 1,
      startedAt: finalResult.startedAt,
      finishedAt: finalResult.finishedAt,
      response: finalResult.response || null,
      error: finalResult.error || null,
      diagnosticJsonPath: finalResult.diagnosticJsonPath || null,
      diagnosticScreenshotPath: finalResult.diagnosticScreenshotPath || null,
    });
    persistPayload('running');
    console.log(JSON.stringify(result));
  }

  const finalPayload = buildPayload('running');
  persistPayload(finalPayload.failedCount > 0 ? 'failed' : 'completed');
  await closeSession(session);
}

// `process.exitCode` so' vale quando o event loop drena. Qualquer handle
// vazado (chromium, socket, timer) mantem o node vivo indefinidamente com o
// codigo de saida ja' definido — foi assim que o login pendurado segurou o
// lock por 1h+ em 04/08/2026. Este watchdog e' `unref`ado: nao atrasa saida
// limpa nenhuma, so' dispara se o processo ainda estiver de pe' depois do
// prazo, e ai' sai a forca com o codigo que ja' estava setado.
const EXIT_WATCHDOG_MS = 5000;

function armarWatchdogDeSaida() {
  setTimeout(() => process.exit(), EXIT_WATCHDOG_MS).unref();
}

main().catch((error) => {
  console.error(error);
  try {
    const args = parseArgs(process.argv.slice(2));
    if (args.output) {
      const payload = readJsonFile(args.output, {}) || {};
      payload.generatedAt = new Date().toISOString();
      payload.state = 'failed';
      payload.errorMessage = error instanceof Error ? error.message : String(error);
      writeJsonFile(args.output, payload);
    }
  } catch (persistError) {
    console.error('Failed to persist runner failure state:', persistError);
  }
  process.exitCode = 1;
}).finally(armarWatchdogDeSaida);
