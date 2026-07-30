// frontend/src/components/ProtectedRoute.tsx

import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth'
import Layout from './Layout';
import WelcomeScreen from './WelcomeScreen';
import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useToast } from '@/hooks/use-toast';

const ProtectedRoute = () => {
  const {
    isAuthenticated,
    isLoading,
    canScheduleBatch,
    canUsePublications,
    canUsePrazosIniciais,
    canUseOnerequest,
    canUseEncerramentos,
    isAdmin,
  } = useAuth();
  const { pathname } = useLocation();
  const { toast } = useToast();
  const [permissionDenied, setPermissionDenied] = useState(false);

  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      // Check route permissions
      if (pathname.startsWith('/tasks/') && !canScheduleBatch) {
        toast({
          title: 'Acesso Negado',
          description: 'Você não tem permissão para acessar agendamento em lote.',
          variant: 'destructive',
        });
        setPermissionDenied(true);
      } else if (pathname.startsWith('/publications') && !canUsePublications) {
        toast({
          title: 'Acesso Negado',
          description: 'Você não tem permissão para acessar publicações.',
          variant: 'destructive',
        });
        setPermissionDenied(true);
      } else if (pathname.startsWith('/automations') && !canScheduleBatch) {
        toast({
          title: 'Acesso Negado',
          description: 'Você não tem permissão para acessar agendamentos.',
          variant: 'destructive',
        });
        setPermissionDenied(true);
      } else if (pathname.startsWith('/prazos-iniciais') && !canUsePrazosIniciais && !isAdmin) {
        toast({
          title: 'Acesso Negado',
          description: 'Você não tem permissão para acessar Prazos Iniciais.',
          variant: 'destructive',
        });
        setPermissionDenied(true);
      } else if (pathname.startsWith('/onerequest') && !canUseOnerequest && !isAdmin) {
        toast({
          title: 'Acesso Negado',
          description: 'Você não tem permissão para acessar o OneRequest.',
          variant: 'destructive',
        });
        setPermissionDenied(true);
      } else if (pathname.startsWith('/encerramentos-legalone') && !canUseEncerramentos && !isAdmin) {
        toast({
          title: 'Acesso Negado',
          description: 'Você não tem permissão para acessar o módulo Encerramentos.',
          variant: 'destructive',
        });
        setPermissionDenied(true);
      } else if (pathname.startsWith('/admin') && !isAdmin) {
        toast({
          title: 'Acesso Negado',
          description: 'Você não tem permissão para acessar o painel administrativo.',
          variant: 'destructive',
        });
        setPermissionDenied(true);
      } else {
        setPermissionDenied(false);
      }
    }
  }, [pathname, isLoading, isAuthenticated, canScheduleBatch, canUsePublications, canUsePrazosIniciais, canUseOnerequest, canUseEncerramentos, isAdmin, toast]);

  // Se ainda estivermos verificando a autenticação (ex: ao recarregar a página),
  // mostramos um indicador de carregamento.
  if (isLoading) {
    return (
      <div className="flex h-screen w-full items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  // Conta nova/pendente: autenticado mas SEM nenhuma permissão e não-admin →
  // tela de boas-vindas (aguardando liberação do admin). Cobre todo 1º acesso
  // via Entra ID, que agora entra sem permissão por padrão.
  if (
    isAuthenticated &&
    !isAdmin &&
    !canScheduleBatch &&
    !canUsePublications &&
    !canUsePrazosIniciais &&
    !canUseOnerequest &&
    !canUseEncerramentos
  ) {
    return <WelcomeScreen />;
  }

  // Se permissão negada, redireciona para home
  if (permissionDenied) {
    return <Navigate to="/" replace />;
  }

  // Se o usuário estiver autenticado, renderizamos o Layout principal
  // e o conteúdo da rota filha através do <Outlet />.
  if (isAuthenticated) {
    return (
      <Layout>
        <Outlet />
      </Layout>
    );
  }

  // Se não estiver autenticado, redirecionamos para a página de login.
  return <Navigate to="/login" replace />;
};

export default ProtectedRoute;