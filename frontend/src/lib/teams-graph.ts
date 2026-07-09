// Aquisição de token do Microsoft Graph (delegado, no nome da operadora logada)
// pra mandar a DM de alerta no Teams. Single-tenant MDR/Duna — clientId/tenantId
// são identificadores PÚBLICOS de SPA (não são segredo); o secret NÃO é usado
// neste fluxo delegado. Override por env (VITE_ENTRA_*) se um dia mudar.
//
// ⚠️ COOP: as páginas de login da Microsoft mandam Cross-Origin-Opener-Policy
// e CORTAM o window.opener — a aba-mãe não consegue monitorar o popup. Por isso
// NÃO usamos acquireTokenPopup (que depende desse monitoramento e ainda mistura
// interação popup×redirect, dando `no_token_request_cache`). Em vez disso: o
// POPUP roda o fluxo REDIRECT inteiro (inicia + conclui na própria janela) e
// devolve o token por BroadcastChannel; a aba-mãe só abre e escuta.

import {
  InteractionRequiredAuthError,
  PublicClientApplication,
} from "@azure/msal-browser";

export const ENTRA_CLIENT_ID =
  (import.meta.env.VITE_ENTRA_CLIENT_ID as string | undefined) ||
  "5a322b91-a2f9-4d47-9e0e-02db86755fc3";
export const ENTRA_TENANT_ID =
  (import.meta.env.VITE_ENTRA_TENANT_ID as string | undefined) ||
  "384ac778-bc7e-48ef-963c-d545a96997b8";

// Permissões consentidas no app do Entra (delegadas).
export const TEAMS_SCOPES = ["Chat.Create", "ChatMessage.Send", "User.Read.All"];

// Canal aba-mãe ↔ popup (mesma origem) pra devolver o token — window.opener
// morre pelo COOP, mas BroadcastChannel entre janelas da mesma origem funciona.
export const MSAL_TEAMS_CHANNEL = "flow-msal-teams-auth";

// Config compartilhado app ↔ página de retorno (msal-redirect.ts).
// - redirectUri: a página estática/bundlada msal-redirect.html (2ª entry Vite),
//   registrada como redirect URI SPA no Entra nos dois domínios flow.*.
// - cache em localStorage (inclusive o temporário do PKCE): o popup recarrega ao
//   voltar da Microsoft; localStorage sobrevive à navegação e é visível à aba-mãe.
// - navigateToLoginRequestUrl=false: senão o MSAL re-navega pro ?flow=teams
//   depois de concluir e entra em loop.
export function msalConfig() {
  return {
    auth: {
      clientId: ENTRA_CLIENT_ID,
      authority: `https://login.microsoftonline.com/${ENTRA_TENANT_ID}`,
      redirectUri: `${window.location.origin}/msal-redirect.html`,
      navigateToLoginRequestUrl: false,
    },
    cache: {
      cacheLocation: "localStorage" as const,
      temporaryCacheLocation: "localStorage" as const,
    },
  };
}

let _msal: PublicClientApplication | null = null;

async function getMsal(): Promise<PublicClientApplication> {
  if (!_msal) {
    _msal = new PublicClientApplication(msalConfig());
    await _msal.initialize();
  }
  return _msal;
}

// Warm-up opcional (chamar no mount da página) — deixa o initialize pronto pra
// o clique do botão abrir o popup sem await longo antes (bloqueador de popup).
export function warmupTeamsAuth(): void {
  getMsal().catch(() => undefined);
}

// Abre o popup no fluxo REDIRECT e resolve com o token que ele devolver pelo
// canal. Aberto SÍNCRONO no gesto do clique (não pode vir depois de awaits
// longos, senão o navegador bloqueia).
function tokenViaPopup(loginHint: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const url =
      `${window.location.origin}/msal-redirect.html` +
      `?flow=teams&login_hint=${encodeURIComponent(loginHint || "")}`;
    const popup = window.open(url, "flow-teams-auth", "width=520,height=680");
    if (!popup) {
      reject(new Error("O navegador bloqueou o popup de login. Permita popups para este site e tente de novo."));
      return;
    }
    const bc = new BroadcastChannel(MSAL_TEAMS_CHANNEL);
    let done = false;
    const finish = (fn: () => void) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      bc.close();
      fn();
    };
    const timer = setTimeout(
      () => finish(() => reject(new Error("Tempo esgotado na autenticação com a Microsoft."))),
      120_000,
    );
    bc.onmessage = (ev) => {
      const d = ev.data as { accessToken?: string; error?: string } | undefined;
      if (d?.accessToken) finish(() => resolve(d.accessToken!));
      else if (d?.error) finish(() => reject(new Error(d.error)));
    };
  });
}

/**
 * Token do Graph pra agir COMO a operadora (loginHint = e-mail dela).
 * Silencioso quando há conta em cache (sem popup); popup só na 1ª vez / quando
 * o refresh token expira. Depois do 1º login o cache (localStorage) persiste.
 */
export async function getGraphTokenForTeams(loginHint: string): Promise<string> {
  const msal = await getMsal();
  const accounts = msal.getAllAccounts();
  const account =
    accounts.find((a) => a.username?.toLowerCase() === loginHint.toLowerCase()) ||
    accounts[0];

  // Sem conta em cache → precisa de interação: abre o popup direto (sem
  // ssoSilent, que é lento e faria o window.open cair no bloqueador).
  if (!account) {
    return tokenViaPopup(loginHint);
  }
  try {
    const r = await msal.acquireTokenSilent({ scopes: TEAMS_SCOPES, account });
    return r.accessToken;
  } catch (e) {
    if (e instanceof InteractionRequiredAuthError) {
      return tokenViaPopup(loginHint);
    }
    throw e;
  }
}
