// Página de RETORNO do login Microsoft (popup/iframe do MSAL).
//
// O COOP das páginas de login da Microsoft corta o window.opener — o
// monitoramento interno do MSAL na aba-mãe não enxerga este popup. Então é
// ESTA página que completa o fluxo: handleRedirectPromise troca o código por
// token (o code_verifier do PKCE está no localStorage COMPARTILHADO — ver
// msalConfig) e o resultado volta pra aba-mãe por BroadcastChannel. Depois,
// fecha a janela.
//
// Num IFRAME (ssoSilent), o MSAL ignora handleRedirectPromise por padrão — o
// fluxo silencioso continua sendo monitorado pela aba-mãe, sem conflito.
//
// É uma 2ª entry do Vite (bundlada, sem CDN → passa em qualquer CSP). O config
// é o MESMO do app (importado de teams-graph pra não divergir).

import { PublicClientApplication } from "@azure/msal-browser";

import { MSAL_TEAMS_CHANNEL, msalConfig } from "@/lib/teams-graph";

function setMsg(texto: string) {
  const el = document.getElementById("msg");
  if (el) el.textContent = texto;
}

async function run() {
  const bc = new BroadcastChannel(MSAL_TEAMS_CHANNEL);
  try {
    const msal = new PublicClientApplication(msalConfig());
    await msal.initialize();
    const result = await msal.handleRedirectPromise();
    if (result?.accessToken) {
      bc.postMessage({ accessToken: result.accessToken });
      setMsg("Autenticado! Fechando…");
      setTimeout(() => window.close(), 250);
      return;
    }
    // Sem resposta de auth na URL (página aberta direto, ou iframe silencioso).
    setMsg("Nada pra processar aqui — pode fechar esta janela.");
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error("[msal-redirect] falha ao processar o retorno:", e);
    bc.postMessage({ error: `Falha na autenticação Microsoft: ${String((e as Error)?.message || e).slice(0, 200)}` });
    setMsg("Não consegui concluir a autenticação. Feche esta janela e tente de novo.");
  } finally {
    setTimeout(() => bc.close(), 1000);
  }
}

run();
