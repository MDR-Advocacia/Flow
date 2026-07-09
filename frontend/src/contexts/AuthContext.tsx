// frontend/src/contexts/AuthContext.tsx

import { createContext, useState, ReactNode, useEffect } from 'react';

interface User {
  id: number;
  external_id?: number;
  name: string;
  email: string;
  role?: string;
  can_schedule_batch?: boolean;
  can_use_publications?: boolean;
  can_use_prazos_iniciais?: boolean;
  can_use_onerequest?: boolean;
  can_use_minha_equipe?: boolean;
  minha_equipe_equipes?: string[];
  can_manage_distribuidos_bb?: boolean;
  must_change_password?: boolean;
  is_active?: boolean;
}

interface TokenData {
  sub: string;
  role: string;
  can_schedule_batch: boolean;
  can_use_publications: boolean;
  can_use_prazos_iniciais?: boolean;
  can_use_onerequest?: boolean;
  can_manage_distribuidos_bb?: boolean;
  must_change_password: boolean;
  exp: number;
}

interface AuthContextType {
  isAuthenticated: boolean;
  user: User | null;
  token: string | null;
  tokenData: TokenData | null;
  mustChangePassword: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  isLoading: boolean;
  canScheduleBatch: boolean;
  canUsePublications: boolean;
  canUsePrazosIniciais: boolean;
  canUseOnerequest: boolean;
  canUseMinhaEquipe: boolean;
  minhaEquipeEquipes: string[];
  canManageDistribuidosBB: boolean;
  isAdmin: boolean;
  refreshMe: () => Promise<void>;
}

// Exporta o contexto para que o hook externo possa usá-lo
export const AuthContext = createContext<AuthContextType | undefined>(undefined);

// Helper to decode JWT
function decodeToken(token: string): TokenData | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const decoded = JSON.parse(atob(parts[1]));
    return decoded;
  } catch (e) {
    return null;
  }
}

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [tokenData, setTokenData] = useState<TokenData | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refreshMe = async () => {
    const storedToken = localStorage.getItem('authToken');
    if (storedToken) {
      try {
        const userResponse = await fetch('/api/v1/me', {
          headers: {
            Authorization: `Bearer ${storedToken}`,
          },
        });
        if (!userResponse.ok) {
          throw new Error('Falha ao atualizar dados do usuário');
        }
        const userData: User = await userResponse.json();
        setUser(userData);
      } catch (error) {
        console.error("Erro ao atualizar usuário:", error);
      }
    }
  };

  useEffect(() => {
    const bootstrap = async () => {
      // Aplica um token: valida no /me e popula o estado. Retorna true se ok.
      const applyToken = async (tok: string): Promise<boolean> => {
        try {
          const resp = await fetch('/api/v1/me', {
            headers: { Authorization: `Bearer ${tok}` },
          });
          if (!resp.ok) return false;
          const userData: User = await resp.json();
          setUser(userData);
          setToken(tok);
          setTokenData(decodeToken(tok));
          localStorage.setItem('authToken', tok);
          return true;
        } catch {
          return false;
        }
      };

      // 1) Token salvo (sessão anterior).
      const stored = localStorage.getItem('authToken');
      if (stored && (await applyToken(stored))) {
        setIsLoading(false);
        return;
      }

      // 2) SSO: o proxy reverso (oauth2-proxy + Entra) injeta a identidade.
      //    Em produção, atrás do proxy, isto loga o usuário automaticamente.
      try {
        const sso = await fetch('/api/v1/auth/sso/session');
        if (sso.ok) {
          const { access_token } = await sso.json();
          if (access_token && (await applyToken(access_token))) {
            setIsLoading(false);
            return;
          }
        }
      } catch {
        // SSO indisponível (ex.: dev local sem proxy) — cai no login por senha.
      }

      // 3) Sem token e sem SSO → deslogado (login por senha / break-glass).
      localStorage.removeItem('authToken');
      setUser(null);
      setToken(null);
      setTokenData(null);
      setIsLoading(false);
    };
    bootstrap();
  }, []);

  const login = async (email: string, password: string) => {
    setIsLoading(true);
    const formData = new URLSearchParams();
    formData.append('username', email);
    formData.append('password', password);
    try {
      const response = await fetch('/api/v1/auth/token', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: formData.toString(),
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Falha na autenticação');
      }
      const data = await response.json();
      const receivedToken = data.access_token;
      const decoded = decodeToken(receivedToken);
      setToken(receivedToken);
      setTokenData(decoded);
      localStorage.setItem('authToken', receivedToken);
      const userResponse = await fetch('/api/v1/me', {
        headers: {
          Authorization: `Bearer ${receivedToken}`,
        },
      });
      if (!userResponse.ok) {
        throw new Error('Não foi possível buscar os dados do usuário.');
      }
      const userData: User = await userResponse.json();
      setUser(userData);
    } catch (error) {
      logout();
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const logout = () => {
    setUser(null);
    setToken(null);
    setTokenData(null);
    localStorage.removeItem('authToken');
  };

  const value = {
    isAuthenticated: !!token,
    user,
    token,
    tokenData,
    // Usa o estado do /me (banco) como fonte de verdade — o JWT pode estar
    // defasado se a senha foi trocada sem reemissão de token.
    mustChangePassword: user?.must_change_password ?? tokenData?.must_change_password ?? false,
    login,
    logout,
    isLoading,
    // Fonte de verdade = /me (banco). O JWT é só fallback no boot, pois é um
    // snapshot de até 24h: sem isto, liberar permissão no admin só "pega" quando
    // o token expira — e a usuária fica presa na tela de espera nesse meio tempo.
    canScheduleBatch: user?.can_schedule_batch ?? tokenData?.can_schedule_batch ?? false,
    // Default FALSE: 1º acesso entra sem permissão (vê a tela de boas-vindas).
    // Usa o /me (banco) como fonte de verdade pra refletir liberação sem re-login.
    canUsePublications: user?.can_use_publications ?? tokenData?.can_use_publications ?? false,
    // Nova permissão — default false pra JWTs antigos que não carregam a claim.
    // Usa o /me (banco) como fallback pra não exigir re-login após toggle no admin.
    canUsePrazosIniciais:
      user?.can_use_prazos_iniciais ?? tokenData?.can_use_prazos_iniciais ?? false,
    canUseOnerequest:
      user?.can_use_onerequest ?? tokenData?.can_use_onerequest ?? false,
    canUseMinhaEquipe: user?.can_use_minha_equipe ?? false,
    minhaEquipeEquipes: user?.minha_equipe_equipes ?? [],
    canManageDistribuidosBB:
      user?.can_manage_distribuidos_bb ?? tokenData?.can_manage_distribuidos_bb ?? false,
    isAdmin: (user?.role ?? tokenData?.role) === 'admin',
    refreshMe,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

// A função useAuth foi MOVIDA para /hooks/useAuth.ts