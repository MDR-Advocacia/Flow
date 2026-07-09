// Página do POPUP de login Microsoft (2ª entry do Vite → msal-redirect.html).
//
// O COOP da Microsoft corta o window.opener, então é ESTA janela que roda o
// fluxo REDIRECT inteiro:
//   1ª carga (?flow=teams): acquireTokenRedirect → navega pra Microsoft.
//   volta (com #code): handleRedirectPromise troca o código por token (o
//   code_verifier do PKCE está no localStorage compartilhado) e devolve pra
//   aba-mãe por BroadcastChannel; depois fecha.
//
// Dentro de IFRAME (o ssoSilent da aba-mãe usa a mesma redirectUri): NÃO faz
// nada — deixa o MSAL da aba-mãe monitorar o iframe, sem conflito.
//
// Bundlada (sem CDN) → passa em qualquer CSP. Config é o MESMO do app.

import { PublicClientApplication } from "@azure/msal-browser";

import { MSAL_TEAMS_CHANNEL, TEAMS_SCOPES, msalConfig } from "@/lib/teams-graph";

function setMsg(texto: string) {
  const el = document.getElementById("msg");
  if (el) el.textContent = texto;
}

async function run() {
  // Iframe (ssoSilent da aba-mãe) — não interferir.
  if (window.top !== window.self) return;

  const bc = new BroadcastChannel(MSAL_TEAMS_CHANNEL);
  try {
    const msal = new PublicClientApplication(msalConfig());
    await msal.initialize();

    // Voltando da Microsoft? Conclui a troca código→token.
    const result = await msal.handleRedirectPromise();
    if (result?.accessToken) {
      bc.postMessage({ accessToken: result.accessToken });
      setMsg("Autenticado! Fechando…");
      setTimeout(() => window.close(), 250);
      return;
    }

    // 1ª carga do popup: inicia o login (navega pra Microsoft).
    const params = new URLSearchParams(window.location.search);
    if (params.get("flow") === "teams") {
      setMsg("Redirecionando para a Microsoft…");
      await msal.acquireTokenRedirect({
        scopes: TEAMS_SCOPES,
        loginHint: params.get("login_hint") || undefined,
      });
      return; // a página navega embora aqui
    }

    setMsg("Nada pra processar aqui — pode fechar esta janela.");
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error("[msal-redirect] falha no login:", e);
    bc.postMessage({
      error: `Falha na autenticação Microsoft: ${String((e as Error)?.message || e).slice(0, 200)}`,
    });
    setMsg("Não consegui concluir a autenticação. Feche esta janela e tente de novo.");
    setTimeout(() => bc.close(), 1000);
  }
}

run();
