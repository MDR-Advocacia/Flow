// Página de RETORNO do login Microsoft (popup/iframe do MSAL).
//
// Roda o MSAL e chama handleRedirectPromise: MSAL detecta que está dentro do
// popup que a aba-mãe abriu e devolve o resultado por BroadcastChannel, então
// a aba-mãe resolve o acquireTokenPopup e o popup fecha. NÃO dá pra depender do
// window.opener (as páginas de login da Microsoft mandam header COOP, que corta
// o vínculo do popup com a aba-mãe — por isso a janela ficava aberta com o
// ?code na URL sem ninguém pra ler).
//
// É uma 2ª entry do Vite (bundlada, sem CDN → passa em qualquer CSP). O config
// é o MESMO do app (mesmo clientId/authority/redirectUri), importado de
// teams-graph pra não divergir.

import { PublicClientApplication } from "@azure/msal-browser";

import { msalConfig } from "@/lib/teams-graph";

async function run() {
  try {
    const msal = new PublicClientApplication(msalConfig());
    await msal.initialize();
    // Processa a resposta (code/token). MSAL cuida de notificar a aba-mãe e
    // fechar este popup. Se não for um retorno de auth, resolve com null.
    await msal.handleRedirectPromise();
  } catch (e) {
    // Deixa visível caso algo falhe (não deveria) — evita "janela morta muda".
    // eslint-disable-next-line no-console
    console.error("[msal-redirect] falha ao processar o retorno:", e);
    const el = document.getElementById("msg");
    if (el) el.textContent = "Não consegui concluir a autenticação. Feche esta janela e tente de novo.";
  }
}

run();
