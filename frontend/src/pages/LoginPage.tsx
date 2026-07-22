import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { DunaFlowMark } from '@/components/DunaFlowMark';

// Base do oauth2-proxy (Microsoft Entra ID). Em produção fica em
// auth.dunatecnologia.com; sobrescrevível por env pra outros ambientes.
const SSO_AUTHORIZE_BASE =
  import.meta.env.VITE_SSO_AUTHORIZE_BASE || 'https://auth.dunatecnologia.com';

// Logo oficial da Microsoft (4 quadrados) — lucide não tem.
const MicrosoftIcon = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 21 21" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect x="1" y="1" width="9" height="9" fill="#F25022" />
    <rect x="11" y="1" width="9" height="9" fill="#7FBA00" />
    <rect x="1" y="11" width="9" height="9" fill="#00A4EF" />
    <rect x="11" y="11" width="9" height="9" fill="#FFB900" />
  </svg>
);

// Login SÓ via SSO Microsoft (decisão do operador 2026-07-22): o formulário de
// e-mail/senha saiu da tela. O endpoint /auth/login continua existindo no
// backend (integrações/emergência), mas o caminho do usuário é o Entra ID.
const LoginPage = () => {
  // Inicia o fluxo SSO: redireciona pro oauth2-proxy/Entra e volta pra cá com
  // a sessão (cookie .dunatecnologia.com). No retorno, o AuthContext chama
  // /api/v1/auth/sso/session e loga automaticamente.
  const handleMicrosoftLogin = () => {
    const rd = `${window.location.origin}/`;
    window.location.href = `${SSO_AUTHORIZE_BASE}/oauth2/start?rd=${encodeURIComponent(rd)}`;
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-background px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex flex-col items-center gap-2">
          <DunaFlowMark size="lg" className="text-[hsl(var(--dunatech-navy))]" />
          <p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">
            by DUNATECH
          </p>
        </div>
        <Card className="w-full">
          <CardHeader>
            <CardTitle className="text-2xl text-center">Entrar</CardTitle>
            <CardDescription className="text-center">
              Acesse com sua conta corporativa Microsoft
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button type="button" className="w-full" onClick={handleMicrosoftLogin}>
              <MicrosoftIcon className="mr-2 h-4 w-4" />
              Entrar com Microsoft
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default LoginPage;
