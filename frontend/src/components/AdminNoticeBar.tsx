import { useEffect, useState, useCallback, useContext, useRef } from "react";
import { AlertTriangle, Info, Megaphone, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { AuthContext } from "@/contexts/AuthContext";
import {
  dismissAdminNotice,
  fetchActiveAdminNotices,
  markAdminNoticesSeen,
} from "@/services/api";
import type { AdminNoticeActive, AdminNoticeSeverity } from "@/types/api";

const POLL_INTERVAL_MS = 30_000;

const SEVERITY_STYLES: Record<AdminNoticeSeverity, {
  container: string;
  icon: string;
  accent: string;
  Icon: React.ComponentType<{ className?: string }>;
}> = {
  info: {
    container: "bg-blue-50 border-blue-200 text-blue-900",
    icon: "text-blue-600",
    accent: "bg-blue-600",
    Icon: Info,
  },
  warning: {
    container: "bg-amber-50 border-amber-300 text-amber-900",
    icon: "text-amber-700",
    accent: "bg-amber-500",
    Icon: AlertTriangle,
  },
  danger: {
    container: "bg-red-50 border-red-300 text-red-900",
    icon: "text-red-700",
    accent: "bg-red-600",
    Icon: Megaphone,
  },
};

function formatEnds(ends_at: string | null): string | null {
  if (!ends_at) return null;
  return new Date(ends_at).toLocaleString("pt-BR", {
    timeZone: "America/Fortaleza",
    dateStyle: "short",
    timeStyle: "short",
  });
}

/**
 * Avisos broadcast emitidos pelo admin. Dois formatos, decididos pelo flag
 * `require_ack` de cada aviso:
 *
 *  - require_ack=false -> BANNER discreto, empilhado no topo (sticky). O
 *    usuario fecha no X quando quiser. Comportamento historico.
 *  - require_ack=true  -> POP-UP bloqueante (modal central). Aparece na hora,
 *    cobre a tela com overlay e so some quando o usuario clica "Ciente".
 *    Mostrado um de cada vez (fila) pra forcar leitura de cada aviso.
 *
 * Polling de 30s no GET /admin/notices/active — o backend ja' filtra (a)
 * janela starts_at..ends_at e (b) dismissals do usuario, entao a UI so
 * renderiza o que esta' ativo e nao foi confirmado.
 *
 * Impressao: toda vez que um aviso novo aparece, reportamos POST /seen
 * (best-effort, idempotente) pra alimentar o "Visto por N" do painel admin.
 * Confirmar ("Ciente"/X) faz POST /dismiss — some pra sempre pra esse user.
 */
export function AdminNoticeBar() {
  const auth = useContext(AuthContext);
  const isAuthenticated = auth?.isAuthenticated ?? false;
  const [notices, setNotices] = useState<AdminNoticeActive[]>([]);
  const [dismissingIds, setDismissingIds] = useState<Set<number>>(new Set());
  // Ids cuja impressao ja' foi reportada nesta sessao — evita re-POST a cada
  // poll de 30s. (O backend so atualiza last_seen_at; reportar 1x basta.)
  const reportedRef = useRef<Set<number>>(new Set());

  const load = useCallback(async () => {
    try {
      const data = await fetchActiveAdminNotices();
      setNotices(data);
      // Reporta impressao dos ids ainda nao vistos nesta sessao.
      const fresh = data
        .map((n) => n.id)
        .filter((id) => !reportedRef.current.has(id));
      if (fresh.length) {
        fresh.forEach((id) => reportedRef.current.add(id));
        void markAdminNoticesSeen(fresh);
      }
    } catch (err) {
      // Silencioso — falhas de rede sao normais (laptop dormindo, VPN
      // caindo). O proximo tick reten ta'.
      console.warn("AdminNoticeBar: falha ao carregar avisos:", err);
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated) {
      setNotices([]);
      reportedRef.current = new Set();
      return;
    }
    // Carrega imediatamente + a cada 30s.
    load();
    const id = setInterval(load, POLL_INTERVAL_MS);
    // Refresh imediato quando a aba volta a ficar visivel (admin que
    // acabou de criar aviso em outra aba ve' o banner sem esperar 30s)
    // e quando a janela reganha foco (alt-tab voltando do navegador).
    const onVisibility = () => {
      if (document.visibilityState === "visible") load();
    };
    window.addEventListener("focus", load);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", load);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [isAuthenticated, load]);

  const handleDismiss = async (noticeId: number) => {
    setDismissingIds((s) => new Set(s).add(noticeId));
    // Otimistico: tira da UI antes da chamada terminar.
    setNotices((prev) => prev.filter((n) => n.id !== noticeId));
    try {
      await dismissAdminNotice(noticeId);
    } catch (err) {
      console.warn("AdminNoticeBar: falha ao dispensar aviso:", err);
      // Reload pra pegar estado real do servidor.
      load();
    } finally {
      setDismissingIds((s) => {
        const next = new Set(s);
        next.delete(noticeId);
        return next;
      });
    }
  };

  if (!isAuthenticated || notices.length === 0) return null;

  const banners = notices.filter((n) => !n.require_ack);
  // Pop-ups exibidos um de cada vez (fila) — o primeiro da lista bloqueia
  // a tela ate' o "Ciente"; ao confirmar, o proximo aparece.
  const modalNotice = notices.find((n) => n.require_ack) ?? null;

  return (
    <>
      {banners.length > 0 ? (
        <div className="sticky top-0 z-50 flex flex-col gap-px">
          {banners.map((n) => {
            const style = SEVERITY_STYLES[n.severity] || SEVERITY_STYLES.info;
            const { Icon } = style;
            const isDismissing = dismissingIds.has(n.id);
            const ends = formatEnds(n.ends_at);
            return (
              <div
                key={n.id}
                className={`border-b px-5 py-4 sm:px-8 sm:py-5 ${style.container}`}
                role="alert"
              >
                <div className="flex items-start gap-3.5">
                  <Icon className={`mt-0.5 h-6 w-6 shrink-0 ${style.icon}`} />
                  <div className="flex-1 min-w-0">
                    <div className="text-base font-semibold leading-tight">{n.title}</div>
                    <div className="mt-1 space-y-1.5 text-sm leading-relaxed">
                      {String(n.message ?? "")
                        .split(/\n\s*\n/)
                        .filter((par) => par.trim())
                        .map((par, i) => (
                          <p key={i} className="whitespace-pre-wrap">
                            {par.trim()}
                          </p>
                        ))}
                    </div>
                    {ends ? (
                      <div className="text-xs mt-1 opacity-70">Ate {ends}</div>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    onClick={() => handleDismiss(n.id)}
                    disabled={isDismissing}
                    className="shrink-0 rounded-md p-1.5 hover:bg-black/5 disabled:opacity-50"
                    title="Dispensar este aviso (nao volta a aparecer pra voce)"
                  >
                    <X className="h-5 w-5" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      {modalNotice ? (() => {
        const style = SEVERITY_STYLES[modalNotice.severity] || SEVERITY_STYLES.info;
        const { Icon } = style;
        const isDismissing = dismissingIds.has(modalNotice.id);
        const ends = formatEnds(modalNotice.ends_at);
        return (
          <div
            className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="notice-modal-title"
          >
            {/* max-w-2xl: com `max-w-lg` (512px) qualquer aviso de mais de dois
                parágrafos virava um paredão de linhas curtas quebradas no meio
                da frase — o texto do recorte de Publicações (05/08/2026) ficou
                ilegível assim. `max-h` + rolagem interna evitam que um aviso
                comprido empurre o botão "Ciente" pra fora da tela em notebook. */}
            <div className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
              <div className={`h-1.5 w-full shrink-0 ${style.accent}`} />
              <div className="flex min-h-0 flex-1 flex-col p-6">
                <div className="flex min-h-0 flex-1 items-start gap-3.5 overflow-y-auto">
                  <Icon className={`mt-0.5 h-7 w-7 shrink-0 ${style.icon}`} />
                  <div className="flex-1 min-w-0">
                    <h2
                      id="notice-modal-title"
                      className="text-lg font-semibold leading-tight text-slate-900"
                    >
                      {modalNotice.title}
                    </h2>
                    {/* `leading-relaxed` + espaço entre parágrafos: a mensagem
                        vem como texto puro com quebras (whitespace-pre-wrap), e
                        sem entrelinha os parágrafos colam num bloco só. */}
                    <div className="mt-3 space-y-3 text-[15px] leading-relaxed text-slate-700">
                      {String(modalNotice.message ?? "")
                        .split(/\n\s*\n/)
                        .filter((par) => par.trim())
                        .map((par, i) => (
                          <p key={i} className="whitespace-pre-wrap">
                            {par.trim()}
                          </p>
                        ))}
                    </div>
                    {ends ? (
                      <p className="mt-3 text-xs text-slate-400">Aviso valido ate {ends}</p>
                    ) : null}
                  </div>
                </div>
                {/* Fora da área de rolagem: o botão fica sempre visível, mesmo
                    com aviso longo. */}
                <div className="mt-5 flex shrink-0 justify-end border-t pt-4">
                  <Button
                    onClick={() => handleDismiss(modalNotice.id)}
                    disabled={isDismissing}
                  >
                    Ciente
                  </Button>
                </div>
              </div>
            </div>
          </div>
        );
      })() : null}
    </>
  );
}

export default AdminNoticeBar;
