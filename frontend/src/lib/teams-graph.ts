// Aquisição de token do Microsoft Graph (delegado, no nome da operadora logada)
// pra mandar a DM de alerta no Teams. Single-tenant MDR/Duna — clientId/tenantId
// são identificadores PÚBLICOS de SPA (não são segredo); o secret NÃO é usado
// neste fluxo delegado. Override por env (VITE_ENTRA_*) se um dia mudar.

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
const SCOPES = ["Chat.Create", "ChatMessage.Send", "User.Read.All"];

// Canal aba-mãe ↔ popup de retorno (mesma origem). O popup troca o código por
// token e manda o resultado por aqui — porque o COOP das páginas de login da
// Microsoft corta o window.opener e o monitoramento interno do MSAL não fecha.
export const MSAL_TEAMS_CHANNEL = "flow-msal-teams-auth";

// Config compartilhado app ↔ página de retorno (msal-redirect.ts).
// - redirectUri: 2ª entry do Vite (página que RODA o MSAL e completa o fluxo).
// - cache em localStorage (INCLUSIVE o temporário): o code_verifier do PKCE
//   precisa ser visível pro popup — sessionStorage é por-janela e deixava o
//   handleRedirectPromise do popup sem estado pra trocar o código por token.
// Precisa estar registrada como redirect URI SPA no Entra
// (…/msal-redirect.html nos dois domínios flow.*).
export function msalConfig() {
  return {
    auth: {
      clientId: ENTRA_CLIENT_ID,
      authority: `https://login.microsoftonline.com/${ENTRA_TENANT_ID}`,
      redirectUri: `${window.location.origin}/msal-redirect.html`,
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

/**
 * Popup + escuta do canal: resolve com o PRIMEIRO que entregar o token —
 * o fluxo interno do MSAL (quando o opener sobrevive) OU o BroadcastChannel
 * (quando o COOP corta o opener e é o popup que completa a troca).
 * A rejeição do caminho interno NÃO derruba a espera (o canal ainda pode
 * entregar); só o timeout encerra de vez.
 */
function tokenViaPopup(msal: PublicClientApplication, loginHint: string): Promise<string> {
  return new Promise((resolve, reject) => {
    let done = false;
    const bc = new BroadcastChannel(MSAL_TEAMS_CHANNEL);
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
    msal
      .acquireTokenPopup({ scopes: SCOPES, loginHint })
      .then((r) => finish(() => resolve(r.accessToken)))
      .catch((e) => {
        // Ex.: "user_cancelled" fantasma quando o COOP faz o popup parecer
        // fechado. O popup real segue vivo e entrega pelo canal — só loga.
        // eslint-disable-next-line no-console
        console.warn("[teams-graph] fluxo interno do popup falhou; aguardando o canal:", e);
      });
  });
}

/**
 * Token do Graph pra agir COMO a operadora (loginHint = e-mail dela).
 * Tenta silencioso; cai pra popup só se precisar de interação/consentimento.
 */
export async function getGraphTokenForTeams(loginHint: string): Promise<string> {
  const msal = await getMsal();
  const accounts = msal.getAllAccounts();
  const account =
    accounts.find((a) => a.username?.toLowerCase() === loginHint.toLowerCase()) ||
    accounts[0];

  try {
    if (account) {
      const r = await msal.acquireTokenSilent({ scopes: SCOPES, account });
      return r.accessToken;
    }
    const r = await msal.ssoSilent({ scopes: SCOPES, loginHint });
    return r.accessToken;
  } catch (e) {
    if (e instanceof InteractionRequiredAuthError || !account) {
      return tokenViaPopup(msal, loginHint);
    }
    throw e;
  }
}
