// Aquisição de token do Microsoft Graph (delegado, no nome da operadora logada)
// pra mandar a DM de alerta no Teams. Single-tenant MDR/Duna — clientId/tenantId
// são identificadores PÚBLICOS de SPA (não são segredo); o secret NÃO é usado
// neste fluxo delegado. Override por env (VITE_ENTRA_*) se um dia mudar.

import {
  InteractionRequiredAuthError,
  PublicClientApplication,
} from "@azure/msal-browser";

const CLIENT_ID =
  (import.meta.env.VITE_ENTRA_CLIENT_ID as string | undefined) ||
  "5a322b91-a2f9-4d47-9e0e-02db86755fc3";
const TENANT_ID =
  (import.meta.env.VITE_ENTRA_TENANT_ID as string | undefined) ||
  "384ac778-bc7e-48ef-963c-d545a96997b8";

// Permissões consentidas no app do Entra (delegadas).
const SCOPES = ["Chat.Create", "ChatMessage.Send", "User.Read.All"];

let _msal: PublicClientApplication | null = null;

async function getMsal(): Promise<PublicClientApplication> {
  if (!_msal) {
    _msal = new PublicClientApplication({
      auth: {
        clientId: CLIENT_ID,
        authority: `https://login.microsoftonline.com/${TENANT_ID}`,
        // Página ESTÁTICA em branco (public/msal-redirect.html): se o retorno
        // cair na raiz, o app React boota dentro do popup e o router reescreve
        // a URL antes do MSAL ler o código — o popup fica preso na "tela do
        // Flow sem acesso". Precisa estar registrada como redirect URI SPA no
        // Entra (https://flow.mdradvocacia.com/msal-redirect.html e a variante
        // .dunatecnologia.com).
        redirectUri: `${window.location.origin}/msal-redirect.html`,
      },
      cache: { cacheLocation: "sessionStorage" },
    });
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
