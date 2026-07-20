import { PropsWithChildren, useMemo, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import {
  Building2,
  CalendarClock,
  ChevronDown,
  CircleUser,
  Clock,
  Contact,
  CopyX,
  FileUp,
  Gavel,
  Gauge,
  BarChart3,
  Inbox,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Menu,
  Newspaper,
  Scale,
  ScanSearch,
  Settings,
  Upload,
  Users,
  Workflow,
} from "lucide-react";

import { useAuth } from "@/hooks/useAuth";
import { TEAMS } from "@/lib/teams";
import { Button } from "./ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "./ui/sheet";
import { DunaFlowMark } from "./DunaFlowMark";

type Permission = 'canScheduleBatch' | 'canUsePublications' | 'canUsePrazosIniciais' | 'canUseOnerequest' | 'canUseMinhaEquipe' | 'canManageDistribuidosBB' | 'isAdmin';

interface NavItem {
  to: string;
  icon: React.ElementType;
  label: string;
  requirePermission?: Permission;
  requireTeam?: string;
}

interface NavSubgroup {
  title: string;
  items: NavItem[];
}

interface NavSection {
  title?: string;
  items?: NavItem[];
  subgroups?: NavSubgroup[];
}

export default function Layout({ children }: PropsWithChildren) {
  const {
    user,
    logout,
    canScheduleBatch,
    canUsePublications,
    canUsePrazosIniciais,
    canUseOnerequest,
    canUseMinhaEquipe,
    minhaEquipeEquipes,
    canManageDistribuidosBB,
    isAdmin,
  } = useAuth();
  const navigate = useNavigate();

  // Seções recolhíveis da sidebar — estado por seção, persistido em localStorage
  // (cada usuário lembra o que deixou fechado). Default: tudo aberto.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(localStorage.getItem("flowSidebarCollapsed") || "{}");
    } catch {
      return {};
    }
  });
  const toggleSection = (key: string) => {
    setCollapsed((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      try {
        localStorage.setItem("flowSidebarCollapsed", JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  const hasPermission = (perm?: Permission) => {
    if (!perm) return true;
    if (perm === 'canScheduleBatch') return canScheduleBatch;
    if (perm === 'canUsePublications') return canUsePublications;
    // Admin vê o item mesmo sem a flag explícita (bypass alinhado com o backend).
    if (perm === 'canUsePrazosIniciais') return canUsePrazosIniciais || isAdmin;
    if (perm === 'canUseOnerequest') return canUseOnerequest || isAdmin;
    if (perm === 'canUseMinhaEquipe') return canUseMinhaEquipe || isAdmin;
    if (perm === 'canManageDistribuidosBB') return canManageDistribuidosBB || isAdmin;
    if (perm === 'isAdmin') return isAdmin;
    return false;
  };

  // Acesso por equipe: admin vê todas; demais precisam ter a chave liberada na árvore do admin.
  const hasTeam = (team?: string) => !team || isAdmin || minhaEquipeEquipes.includes(team);

  const baseSections: NavSection[] = [
    {
      title: "LegalOne",
      items: [
        { to: "/tasks/spreadsheet-batch", icon: FileUp, label: "Tarefas por Planilha", requirePermission: 'canScheduleBatch' },
        { to: "/ged-legalone", icon: Upload, label: "Envio em Lote ao GED", requirePermission: 'canScheduleBatch' },
        { to: "/contatos-legalone", icon: Contact, label: "Atualização de Contatos", requirePermission: 'canScheduleBatch' },
        { to: "/cancelamento-duplicadas", icon: CopyX, label: "Cancelar Duplicadas", requirePermission: 'isAdmin' },
        { to: "/distribuidos-bb/dashboard", icon: Building2, label: "Cadastro de Processo", requirePermission: 'canManageDistribuidosBB' },
      ],
    },
    {
      title: "Tratamento de Publicações",
      items: [
        { to: "/publications/dashboard", icon: LayoutDashboard, label: "Dashboard", requirePermission: 'canUsePublications' },
        { to: "/automations", icon: Clock, label: "Agendamentos", requirePermission: 'canUsePublications' },
        { to: "/publications", icon: Newspaper, label: "Publicações Legal One", requirePermission: 'canUsePublications' },
        { to: "/publications/treatment", icon: ListChecks, label: "Tratamento Web", requirePermission: 'canUsePublications' },
        { to: "/publications/citacoes-bm", icon: Gavel, label: "Citações BM", requirePermission: 'canUsePublications' },
        { to: "/publications/templates", icon: Settings, label: "Templates de Agendamento", requirePermission: 'canUsePublications' },
      ],
    },
    {
      title: "Prazos Processuais",
      items: [
        { to: "/prazos-iniciais", icon: CalendarClock, label: "Agendar Prazos Iniciais", requirePermission: 'canUsePrazosIniciais' },
        { to: "/prazos-iniciais/treatment", icon: ListChecks, label: "Tratamento Web Agendamentos Iniciais", requirePermission: 'canUsePrazosIniciais' },
        { to: "/prazos-iniciais/analise-recursal", icon: Scale, label: "Análise Recursal", requirePermission: 'canUsePrazosIniciais' },
        { to: "/prazos-iniciais/templates", icon: Settings, label: "Templates de Prazos Iniciais", requirePermission: 'isAdmin' },
        { to: "/ajus", icon: Workflow, label: "AJUS — Andamentos", requirePermission: 'canUsePrazosIniciais' },
      ],
    },
    {
      title: "Classificador",
      items: [
        { to: "/classificador", icon: ScanSearch, label: "Diagnostico de Carteira", requirePermission: 'canUsePrazosIniciais' },
      ],
    },
    {
      title: "OneRequest",
      items: [
        { to: "/onerequest", icon: Inbox, label: "DMIs Banco do Brasil", requirePermission: 'canUseOnerequest' },
        { to: "/onerequest/dashboard", icon: BarChart3, label: "DMIs — Dashboard", requirePermission: 'canUseOnerequest' },
      ],
    },
    {
      title: "Minha Equipe",
      subgroups: ["Contencioso Passivo", "Recuperação de Crédito", "Especializada"].map((grupo) => ({
        title: grupo,
        items: TEAMS.filter((t) => t.grupo === grupo).map((t) => ({
          to: `/minha-equipe/${t.key}`,
          icon: Gauge,
          label: t.label,
          requirePermission: 'canUseMinhaEquipe' as Permission,
          requireTeam: t.key,
        })),
      })),
    },
    {
      items: [
        { to: "/admin", icon: Users, label: "Administração", requirePermission: 'isAdmin' },
      ],
    },
  ];

  const visibleSections = useMemo(() => {
    const okItem = (it: NavItem) => hasPermission(it.requirePermission) && hasTeam(it.requireTeam);
    return baseSections
      .map((sec) => {
        if (sec.subgroups) {
          const subgroups = sec.subgroups
            .map((sg) => ({ ...sg, items: sg.items.filter(okItem) }))
            .filter((sg) => sg.items.length > 0);
          return { ...sec, subgroups, items: [] as NavItem[] };
        }
        return { ...sec, items: (sec.items ?? []).filter(okItem) };
      })
      .filter((sec) => (sec.subgroups ? sec.subgroups.length > 0 : (sec.items?.length ?? 0) > 0));
  }, [canScheduleBatch, canUsePublications, canUsePrazosIniciais, canUseOnerequest, canUseMinhaEquipe, minhaEquipeEquipes, isAdmin]);

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  const renderLink = ({ to, icon: Icon, label }: NavItem, indent = false) => (
    <NavLink
      key={to}
      to={to}
      className={({ isActive }) =>
        `flex items-center gap-3 rounded-lg py-2 text-muted-foreground transition-all hover:text-primary ${
          indent ? "pl-7 pr-3" : "px-3"
        } ${isActive ? "bg-muted !text-primary" : ""}`
      }
    >
      <Icon className="h-4 w-4" />
      {label}
    </NavLink>
  );

  const NavContent = () => (
    <nav className="grid items-start px-2 text-sm font-medium lg:px-4">
      {visibleSections.map((section, idx) => {
        const key = section.title ?? `sec-${idx}`;
        const isCollapsed = section.title ? !!collapsed[key] : false;
        return (
          <div key={key} className={idx > 0 ? "mt-2" : ""}>
            {section.title && (
              <button
                type="button"
                onClick={() => toggleSection(key)}
                aria-expanded={!isCollapsed}
                className="flex w-full items-center justify-between rounded-md px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/70 transition-colors hover:text-muted-foreground"
              >
                <span>{section.title}</span>
                <ChevronDown
                  className={`h-3.5 w-3.5 shrink-0 transition-transform ${isCollapsed ? "-rotate-90" : ""}`}
                />
              </button>
            )}
            {!isCollapsed &&
              section.subgroups?.map((sg) => {
                const sgKey = `${key}::${sg.title}`;
                const sgCollapsed = !!collapsed[sgKey];
                return (
                  <div key={sgKey} className="mt-0.5">
                    <button
                      type="button"
                      onClick={() => toggleSection(sgKey)}
                      aria-expanded={!sgCollapsed}
                      className="flex w-full items-center justify-between rounded-md py-1 pl-4 pr-3 text-[10px] font-medium uppercase tracking-wide text-muted-foreground/60 transition-colors hover:text-muted-foreground"
                    >
                      <span>{sg.title}</span>
                      <ChevronDown
                        className={`h-3 w-3 shrink-0 transition-transform ${sgCollapsed ? "-rotate-90" : ""}`}
                      />
                    </button>
                    {!sgCollapsed && sg.items.map((it) => renderLink(it, true))}
                  </div>
                );
              })}
            {!isCollapsed && section.items?.map((it) => renderLink(it, false))}
          </div>
        );
      })}
    </nav>
  );

  return (
    <div className="grid min-h-screen w-full md:grid-cols-[220px_1fr] lg:grid-cols-[280px_1fr]">
      <div className="hidden border-r bg-muted/40 md:block">
        <div className="sticky top-0 max-h-screen overflow-y-auto">
          <div className="flex h-14 items-center border-b px-4 lg:h-[60px] lg:px-6">
            <Link
              to="/"
              className="flex items-center w-full justify-center text-[hsl(var(--dunatech-navy))]"
              title="DunaFlow by DUNATECH"
            >
              <DunaFlowMark size="md" />
            </Link>
          </div>
          <NavContent />
          <div className="flex flex-col items-center gap-2 border-t px-4 py-4">
            <img
              src="/brand/dunaflow-logo.png"
              alt="DUNATECH"
              className="h-6 w-auto object-contain opacity-85"
            />
            <div className="text-[0.65rem] tracking-wider text-muted-foreground text-center">
              © 2026 Duna.Tech
            </div>
          </div>
        </div>
      </div>

      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-40 flex h-14 items-center gap-4 border-b bg-background px-4 lg:h-[60px] lg:px-6">
          <Sheet>
            <SheetTrigger asChild>
              <Button variant="outline" size="icon" className="shrink-0 md:hidden">
                <Menu className="h-5 w-5" />
                <span className="sr-only">Abrir menu de navegacao</span>
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="flex flex-col">
              <SheetTitle className="sr-only">Menu de navegação</SheetTitle>
              <div className="mb-4 flex items-center justify-center py-3 text-[hsl(var(--dunatech-navy))]">
                <DunaFlowMark size="md" />
              </div>
              <NavContent />
              <div className="mt-auto flex flex-col items-center gap-2 border-t pt-4">
                <img
                  src="/brand/dunaflow-logo.png"
                  alt="DUNATECH"
                  className="h-6 w-auto object-contain opacity-85"
                />
                <div className="text-[0.65rem] tracking-wider text-muted-foreground text-center">
                  © 2026 Duna.Tech
                </div>
              </div>
            </SheetContent>
          </Sheet>

          <div className="w-full flex-1" />

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="secondary" size="icon" className="rounded-full">
                <CircleUser className="h-5 w-5" />
                <span className="sr-only">Abrir menu do usuario</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>{user?.name || "Minha Conta"}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem asChild>
                <Link to="/me" className="cursor-pointer">
                  <CircleUser className="mr-2 h-4 w-4" />
                  <span>Meu Perfil</span>
                </Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={handleLogout}
                className="cursor-pointer text-red-600 focus:bg-red-50 focus:text-red-500"
              >
                <LogOut className="mr-2 h-4 w-4" />
                <span>Sair</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </header>

        <main className="flex flex-1 flex-col gap-4 overflow-auto p-4 lg:gap-6 lg:p-6">{children}</main>
      </div>
    </div>
  );
}
