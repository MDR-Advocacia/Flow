// frontend/src/pages/AdminPage.tsx
//
// Só o esqueleto: cabeçalho + abas. Cada aba é um componente em
// components/admin/ (antes tudo morava aqui, em 1.548 linhas).

import { useAuth } from "@/hooks/useAuth";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AlertCircle, ShieldCheck } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { BaseProcessualPage } from "@/pages/BaseProcessualPage";
import TaxonomiaAdminTab from "@/components/TaxonomiaAdminTab";
import { AdminNoticesManager } from "@/components/AdminNoticesManager";
import { UserFeedbackManager } from "@/components/UserFeedbackManager";
import EquipesManager from "@/components/admin/EquipesManager";
import CargosManager from "@/components/admin/CargosManager";
import SquadsManager from "@/components/admin/SquadsManager";
import SyncManager from "@/components/admin/SyncManager";
import UsersAndPermissions from "@/components/admin/UsersAndPermissions";
import SsoUnifyManager from "@/components/admin/SsoUnifyManager";
import UtilizacaoManager from "@/components/admin/UtilizacaoManager";
import { MOSTRAR_LEGADO } from "@/components/admin/types";

// --- Componente Principal da Página (Renderizando todos) ---
const AdminPage = () => {
    const { isAdmin } = useAuth();

    if (!isAdmin) {
        return (
            <div className="space-y-6">
                <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>Acesso Negado</AlertTitle>
                    <AlertDescription>Você não tem permissão para acessar esta página.</AlertDescription>
                </Alert>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
                        <ShieldCheck className="h-6 w-6" />
                        Painel Administrativo
                    </h1>
                    <p className="text-muted-foreground">
                        Gerencie as configurações e associações do sistema.
                    </p>
                </div>
            </div>

            <Tabs defaultValue="sync" className="w-full">
                <TabsList>
                    <TabsTrigger value="sync">Sincronização</TabsTrigger>
                    <TabsTrigger value="squads">Squads</TabsTrigger>
                    <TabsTrigger value="equipes">Equipes</TabsTrigger>
                    {MOSTRAR_LEGADO && <TabsTrigger value="taxonomy">Taxonomia</TabsTrigger>}
                    <TabsTrigger value="cargos">Cargos</TabsTrigger>
                    <TabsTrigger value="users">Usuários & Permissões</TabsTrigger>
                    <TabsTrigger value="sso">Contas SSO</TabsTrigger>
                    <TabsTrigger value="notices">Avisos</TabsTrigger>
                    <TabsTrigger value="utilizacao">Utilização</TabsTrigger>
                    <TabsTrigger value="feedback">Feedback</TabsTrigger>
                    <TabsTrigger value="base-processual">Base Banco Master</TabsTrigger>
                </TabsList>
                <TabsContent value="sync" className="space-y-6">
                    <SyncManager />
                </TabsContent>
                <TabsContent value="squads" className="space-y-6">
                    <SquadsManager />
                </TabsContent>
                <TabsContent value="equipes" className="space-y-6">
                    <EquipesManager />
                </TabsContent>
                {MOSTRAR_LEGADO && (
                <TabsContent value="taxonomy" className="space-y-6">
                    <TaxonomiaAdminTab />
                </TabsContent>
                )}
                <TabsContent value="cargos" className="space-y-6">
                    <CargosManager />
                </TabsContent>
                <TabsContent value="users" className="space-y-6">
                    <UsersAndPermissions />
                </TabsContent>
                <TabsContent value="sso" className="space-y-6">
                    <SsoUnifyManager />
                </TabsContent>
                <TabsContent value="notices" className="space-y-6">
                    <AdminNoticesManager />
                </TabsContent>
                <TabsContent value="utilizacao" className="space-y-6">
                    <UtilizacaoManager />
                </TabsContent>
                <TabsContent value="feedback" className="space-y-6">
                    <UserFeedbackManager />
                </TabsContent>
            <TabsContent value="base-processual" className="space-y-6">
                    <BaseProcessualPage />
                </TabsContent>
            </Tabs>
        </div>
    )
}

export default AdminPage;
