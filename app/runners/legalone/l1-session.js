// Sessão web COMPARTILHADA do Legal One entre todos os runners Playwright.
//
// O L1 é single-session por usuário: cada login novo derruba a sessão dos
// outros robôs (legacy_task_http, captura de token do import, tratamento de
// publicações, relatórios…). Este helper faz os runners REUSAREM a sessão do
// cache `/app/data/legacy_task_http_session.json` (o mesmo do legacy_task_http):
//
//   1. `loadSharedCookies(file)` — lê o cache (name→value; exige .ASPXAUTH).
//   2. `injectSharedCookies(context, originUrl, map)` — injeta no contexto ANTES
//      do login(). Como todo login() dos runners começa navegando pro returnUrl
//      e SAI CEDO se a página não for de auth, cookies válidos transformam o
//      login numa validação sem credencial (ninguém é derrubado).
//   3. `persistSharedCookies(file, context, originUrl)` — depois do login(),
//      regrava o cache (tmp + rename atômico) pra TODO o sistema herdar a
//      sessão corrente em vez de re-logar e derrubá-la.
//
// O lado Python lê `{"cookies": {...}, "obtained_at": iso}` (TTL próprio) —
// `obtained_at` sai com offset +00:00 (datetime.fromisoformat do Python 3.10
// não aceita sufixo 'Z'). Tudo best-effort: falha aqui NUNCA quebra o runner.

const fs = require('fs');
const path = require('path');

const DEFAULT_SESSION_FILE =
  process.env.L1_SHARED_SESSION_FILE || '/app/data/legacy_task_http_session.json';

function loadSharedCookies(file) {
  const target = file || DEFAULT_SESSION_FILE;
  try {
    if (!fs.existsSync(target)) return null;
    const raw = JSON.parse(fs.readFileSync(target, 'utf-8'));
    const jar = raw && raw.cookies ? raw.cookies : null;
    if (!jar || !jar['.ASPXAUTH']) return null;
    return jar;
  } catch (_) {
    return null;
  }
}

async function injectSharedCookies(context, originUrl, cookieMap) {
  if (!cookieMap) return false;
  try {
    const origin = new URL(originUrl).origin;
    await context.addCookies(
      Object.entries(cookieMap).map(([name, value]) => ({
        name,
        value: String(value),
        url: `${origin}/`,
      })),
    );
    return true;
  } catch (_) {
    return false;
  }
}

async function persistSharedCookies(file, context, originUrl) {
  const target = file || DEFAULT_SESSION_FILE;
  try {
    const origin = new URL(originUrl).origin;
    const jar = await context.cookies(`${origin}/`);
    const cookies = {};
    for (const c of jar) cookies[c.name] = c.value;
    if (!cookies['.ASPXAUTH']) return false;
    const payload = JSON.stringify({
      cookies,
      obtained_at: new Date().toISOString().replace('Z', '+00:00'),
    });
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const tmp = `${target}.tmp-${process.pid}`;
    fs.writeFileSync(tmp, payload, 'utf-8');
    fs.renameSync(tmp, target); // atômico: leitores nunca veem JSON pela metade
    return true;
  } catch (_) {
    return false;
  }
}

module.exports = {
  DEFAULT_SESSION_FILE,
  loadSharedCookies,
  injectSharedCookies,
  persistSharedCookies,
};
