// frontend/src/App.tsx (versão final, refatorada)

import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, useEffect, useContext } from 'react';

// Componentes de Rota e Layout
import ProtectedRoute from './components/ProtectedRoute';
import { ChangePasswordDialog } from './components/ChangePasswordDialog';
import AdminNoticeBar from './components/AdminNoticeBar';
import FeedbackButton from './components/FeedbackButton';

// Páginas
import AdminPage from './pages/AdminPage';
import CancelamentoDuplicadasPage from './pages/CancelamentoDuplicadasPage';
import NotFound from './pages/NotFound';
import LandingPage from './pages/LandingPage';
import PublicationsDashboardPage from './pages/PublicationsDashboardPage';
import CreateTaskByTemplatePage from './pages/CreateTaskByTemplatePage';
import LoginPage from './pages/LoginPage';
import CreateTaskFromSpreadsheetPage from './pages/CreateTaskFromSpreadsheetPage';
import PublicationsPage from './pages/PublicationsPage';
import TaskTemplatesPage from './pages/TaskTemplatesPage';
import TemplateReviewPage from './pages/TemplateReviewPage';
import OfficePoloScopePage from './pages/OfficePoloScopePage';
import AutomationsPage from './pages/AutomationsPage';
import ProfilePage from './pages/ProfilePage';
import BatchExecutionsPage from './pages/BatchExecutionsPage';
import PublicationTreatmentPage from './pages/PublicationTreatmentPage';
import CitacoesBMPage from './pages/CitacoesBMPage';
import LookupByCnjPage from './pages/LookupByCnjPage';
import PrazosIniciaisPage from './pages/PrazosIniciaisPage';
import PrazosIniciaisTreatmentPage from './pages/PrazosIniciaisTreatmentPage';
import PrazosIniciaisTreatmentPageOperator from './pages/PrazosIniciaisTreatmentPageOperator';
import PrazosIniciaisTemplatesAdminPage from './pages/PrazosIniciaisTemplatesAdminPage';
import AnaliseRecursalPage from './pages/AnaliseRecursalPage';
import PatrocinioRelatorioPage from './pages/PatrocinioRelatorioPage';
import AjusPage from './pages/AjusPage';
import ClassificadorPage from './pages/ClassificadorPage';
import GedLegalOnePage from './pages/GedLegalOnePage';
import ContatosLegalOnePage from './pages/ContatosLegalOnePage';
import OnerequestPage from './pages/OnerequestPage';
import OnerequestDashboardPage from './pages/OnerequestDashboardPage';
import DistribuidosBBPage from './pages/DistribuidosBBPage';
import DistribuidosBBDashboardPage from './pages/DistribuidosBBDashboardPage';
import DistribuidosBBConfigPage from './pages/DistribuidosBBConfigPage';
import MinhaEquipePage from './pages/MinhaEquipePage';

// Contexto de Autenticação
import { AuthProvider, AuthContext } from './contexts/AuthContext';
import { Toaster } from '@/components/ui/toaster';

const queryClient = new QueryClient();

function AppContent() {
  const authContext = useContext(AuthContext);
  const [showChangePasswordDialog, setShowChangePasswordDialog] = useState(false);

  useEffect(() => {
    if (authContext?.mustChangePassword && authContext.isAuthenticated) {
      setShowChangePasswordDialog(true);
    }
  }, [authContext?.mustChangePassword, authContext?.isAuthenticated]);

  return (
    <>
      <Router>
        <AdminNoticeBar />
        <Routes>
          {/* Rota Pública: Login */}
          <Route path="/login" element={<LoginPage />} />

          {/* Rotas Protegidas */}
          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<LandingPage />} />
            <Route path="/admin" element={<AdminPage />} />
            <Route path="/me" element={<ProfilePage />} />
            <Route path="/tasks/template-batch" element={<CreateTaskByTemplatePage />} />
            <Route path="/tasks/spreadsheet-batch" element={<CreateTaskFromSpreadsheetPage />} />
            <Route path="/cancelamento-duplicadas" element={<CancelamentoDuplicadasPage />} />
            <Route path="/publications" element={<PublicationsPage />} />
            <Route path="/publications/dashboard" element={<PublicationsDashboardPage />} />
            <Route path="/publications/lookup" element={<LookupByCnjPage />} />
            <Route path="/publications/treatment" element={<PublicationTreatmentPage />} />
            <Route path="/publications/citacoes-bm" element={<CitacoesBMPage />} />
            <Route path="/publications/templates" element={<TaskTemplatesPage />} />
            <Route
              path="/publications/templates/review-pending"
              element={<TemplateReviewPage />}
            />
            <Route
              path="/admin/offices/polo-scope"
              element={<OfficePoloScopePage />}
            />
            <Route path="/automations" element={<AutomationsPage />} />
            <Route path="/batches" element={<BatchExecutionsPage />} />
            <Route path="/prazos-iniciais" element={<PrazosIniciaisPage />} />
            <Route path="/prazos-iniciais/treatment" element={<PrazosIniciaisTreatmentPageOperator />} />
            <Route path="/prazos-iniciais/treatment/detalhes" element={<PrazosIniciaisTreatmentPage />} />
            <Route
              path="/prazos-iniciais/templates"
              element={<PrazosIniciaisTemplatesAdminPage />}
            />
            <Route
              path="/prazos-iniciais/analise-recursal"
              element={<AnaliseRecursalPage />}
            />
            <Route
              path="/prazos-iniciais/patrocinio/relatorio"
              element={<PatrocinioRelatorioPage />}
            />
            <Route path="/ajus" element={<AjusPage />} />
            <Route path="/classificador" element={<ClassificadorPage />} />
            <Route path="/ged-legalone" element={<GedLegalOnePage />} />
            <Route path="/contatos-legalone" element={<ContatosLegalOnePage />} />
            <Route path="/onerequest" element={<OnerequestPage />} />
            <Route path="/onerequest/dashboard" element={<OnerequestDashboardPage />} />
            <Route path="/distribuidos-bb" element={<DistribuidosBBPage />} />
            <Route path="/distribuidos-bb/dashboard" element={<DistribuidosBBDashboardPage />} />
            <Route path="/distribuidos-bb/config" element={<DistribuidosBBConfigPage />} />
            <Route path="/minha-equipe" element={<MinhaEquipePage />} />
            <Route path="/minha-equipe/:team" element={<MinhaEquipePage />} />
          </Route>

          {/* Rota para página não encontrada */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Router>

      {/*
        Botao flutuante de feedback (canto inferior direito). Persiste
        em todas as paginas autenticadas. Componente decide internamente
        se renderiza (esconde no /login). Fora do <Router> proposital —
        nao depende de rota, deve flutuar sempre.
      */}
      <FeedbackButton />

      <Toaster />

      <ChangePasswordDialog
        isOpen={showChangePasswordDialog}
        isMandatory={authContext?.mustChangePassword ?? false}
        onPasswordChanged={() => {
          setShowChangePasswordDialog(false);
          // O JWT atual ainda carrega must_change_password=true.
          // Força logout para que o próximo login emita um token com a claim atualizada.
          if (authContext?.mustChangePassword) {
            authContext?.logout();
          } else {
            authContext?.refreshMe();
          }
        }}
      />
    </>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <AppContent />
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
