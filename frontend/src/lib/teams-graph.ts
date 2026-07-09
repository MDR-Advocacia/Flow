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

// Config compartilhado app ↔ página de retorno (msal-redirect.ts). O
// redirectUri aponta pra 2ª entry do Vite: uma página que RODA o MSAL e chama
// handleRedirectPromise. Não dá pra depender do window.opener (as páginas de
// login da Microsoft mandam COOP e cortam o vínculo com a aba-mãe). Precisa
// estar registrada como redirect URI SPA no Entra (…/msal-redirect.html nos
// dois domínios flow.*).
export function msalConfig() {
  return {
    auth: {
      clientId: ENTRA_CLIENT_ID,
      authority: `https://login.microsoftonline.com/${ENTRA_TENANT_ID}`,
      redirectUri: `${window.location.origin}/msal-redirect.html`,
    },
    cache: { cacheLocation: "sessionStorage" as const },
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
      const r = await msal.acquireTokenPopup({ scopes: SCOPES, loginHint });
      return r.accessToken;
    }
    throw e;
  }
}
