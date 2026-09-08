/**
 * OfficeTemplatesView — view "Por escritório" da pagina de Templates.
 *
 * Junta:
 *  - Seletor de escritorio (Combobox cmdk + path completo)
 *  - Header com info do escritorio (polo, link pra editar polo)
 *  - OfficeTemplateTree (arvore com cobertura visual)
 *  - TemplateInlineModal (criar / editar)
 *  - TemplateReviewModal (migrar v1 → v2)
 *
 * Persiste o ultimo escritorio selecionado em localStorage pra restaurar
 * ao reabrir a pagina (operador costuma trabalhar num escritorio so).
 * Inicializa com `default_office_id` do usuario quando nao ha selecao
 * previa.
 */
import { useEffect, useState } from "react";
import { Building2, ChevronsUpDown, Check, ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  OfficeTemplateTree,
  type CoverageTemplateInfo,
} from "@/components/OfficeTemplateTree";
import { TemplateInlineModal } from "@/components/TemplateInlineModal";
import {
  TemplateReviewModal,
  type TemplateForReview,
} from "@/components/TemplateReviewModal";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "@/lib/api-client";

interface Office {
  id: number;
  external_id: number;
  name: string;
  path: string;
  polo_scope?: string;
}

interface MeResponse {
  default_office_id?: number | null;
}

const LS_KEY = "templates-page:last-office";

export function OfficeTemplatesView() {
  const { toast } = useToast();
  const [offices, setOffices] = useState<Office[]>([]);
  const [selectedOfficeId, setSelectedOfficeId] = useState<number | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  // Modal state
  const [inlineOpen, setInlineOpen] = useState(false);
  const [inlineMode, setInlineMode] = useState<"create" | "edit">("create");
  const [inlineCategory, setInlineCategory] = useState("");
  const [inlineSubcategory, setInlineSubcategory] = useState<string | null>(null);
  const [inlineTemplateId, setInlineTemplateId] = useState<number | undefined>();

  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewing, setReviewing] = useState<TemplateForReview | null>(null);

  // Carrega escritorios (uma vez)
  useEffect(() => {
    apiFetch("/api/v1/offices?include_virtual=true")
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: Office[]) => {
        const sorted = [...(rows || [])].sort((a, b) =>
          (a.path || a.name).localeCompare(b.path || b.name),
        );
        setOffices(sorted);
      })
      .catch((err) =>
        toast({
          title: "Falha carregando escritórios",
          description: String(err?.message ?? err),
          variant: "destructive",
        }),
      );
  }, [toast]);

  // Resolve escritorio default: localStorage → /me.default_office_id → primeiro
  useEffect(() => {
    if (selectedOfficeId !== null) return;
    if (offices.length === 0) return;

    const fromLs = localStorage.getItem(LS_KEY);
    if (fromLs) {
      const id = Number(fromLs);
      if (offices.some((o) => o.external_id === id)) {
        setSelectedOfficeId(id);
        return;
      }
    }

    apiFetch("/api/v1/me")
      .then((r) => (r.ok ? r.json() : null))
      .then((me: MeResponse | null) => {
        if (me?.default_office_id) {
          setSelectedOfficeId(me.default_office_id);
        } else {
          // Default extremo: primeiro da lista
          setSelectedOfficeId(offices[0].external_id);
        }
      })
      .catch(() => {
        if (offices[0]) setSelectedOfficeId(offices[0].external_id);
      });
  }, [offices, selectedOfficeId]);

  // Persistir ultima selecao
  useEffect(() => {
    if (selectedOfficeId !== null) {
      localStorage.setItem(LS_KEY, String(selectedOfficeId));
    }
  }, [selectedOfficeId]);

  const selectedOffice =
    offices.find((o) => o.external_id === selectedOfficeId) ?? null;

  // Handlers do tree
  const handleAddTemplate = (
    category: string,
    subcategory: string | null,
    officeId: number,
  ) => {
    setInlineMode("create");
    setInlineCategory(category);
    setInlineSubcategory(subcategory);
    setInlineTemplateId(undefined);
    setInlineOpen(true);
  };

  const handleEditTemplate = (t: CoverageTemplateInfo) => {
    setInlineMode("edit");
    setInlineCategory(t.category);
    setInlineSubcategory(t.subcategory);
    setInlineTemplateId(t.id);
    setInlineOpen(true);
  };

  const handleMigrateTemplate = (t: CoverageTemplateInfo) => {
    setReviewing({
      id: t.id,
      name: t.name,
      category: t.category,
      subcategory: t.subcategory,
      legacy_label: t.legacy_label,
      office_polo_scope: selectedOffice?.polo_scope ?? null,
      office_name: selectedOffice?.path ?? selectedOffice?.name ?? null,
    });
    setReviewOpen(true);
  };

  const handleSaved = () => {
    setReloadKey((k) => k + 1);
  };

  return (
    <div className="space-y-4">
      {/* Header: seletor de escritorio + info */}
      <Card>
        <CardContent className="pt-4 pb-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[280px]">
              <label className="block text-xs font-medium mb-1">
                Escritório responsável
              </label>
              <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
                <PopoverTrigger asChild>
                  <Button
                    type="button"
                    variant="outline"
                    role="combobox"
                    aria-expanded={pickerOpen}
                    className="w-full justify-between font-normal"
                  >
                    {selectedOffice ? (
                      <span className="flex items-center gap-2 truncate">
                        <Building2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                        <span className="truncate">
                          {selectedOffice.path || selectedOffice.name}
                        </span>
                      </span>
                    ) : (
                      <span className="text-muted-foreground">
                        Selecione o escritório
                      </span>
                    )}
                    <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent
                  className="w-[--radix-popover-trigger-width] p-0"
                  align="start"
                >
                  <Command
                    filter={(value, search) => {
                      const norm = (s: string) =>
                        s
                          .toLowerCase()
                          .normalize("NFD")
                          .replace(/[̀-ͯ]/g, "");
                      return norm(value).includes(norm(search)) ? 1 : 0;
                    }}
                  >
                    <CommandInput placeholder="Buscar escritório..." />
                    <CommandList className="max-h-80">
                      <CommandEmpty>Nenhum escritório encontrado.</CommandEmpty>
                      <CommandGroup>
                        {offices.map((o) => {
                          const isSelected = o.external_id === selectedOfficeId;
                          const value = `${o.path || o.name}::${o.external_id}`;
                          return (
                            <CommandItem
                              key={o.external_id}
                              value={value}
                              onSelect={() => {
                                setSelectedOfficeId(o.external_id);
                                setPickerOpen(false);
                              }}
                            >
                              <Check
                                className={`mr-2 h-4 w-4 ${
                                  isSelected ? "opacity-100" : "opacity-0"
                                }`}
                              />
                              <span className="truncate">
                                {o.path || o.name}
                              </span>
                              {o.polo_scope && o.polo_scope !== "ambos" && (
                                <Badge
                                  variant="secondary"
                                  className="ml-auto text-xs"
                                >
                                  {o.polo_scope}
                                </Badge>
                              )}
                            </CommandItem>
                          );
                        })}
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </PopoverContent>
              </Popover>
            </div>

            {/* Info do escritorio */}
            {selectedOffice && (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">Polo:</span>
                <Badge variant="secondary">
                  {selectedOffice.polo_scope || "ambos"}
                </Badge>
                <Button asChild variant="ghost" size="sm" className="h-7 px-2 text-xs">
                  <Link to="/admin/offices/polo-scope">
                    <ExternalLink className="h-3 w-3 mr-1" />
                    editar polo
                  </Link>
                </Button>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Arvore por escritorio */}
      {selectedOffice ? (
        <OfficeTemplateTree
          officeExternalId={selectedOffice.external_id}
          onAddTemplate={handleAddTemplate}
          onEditTemplate={handleEditTemplate}
          onMigrateTemplate={handleMigrateTemplate}
          reloadKey={reloadKey}
        />
      ) : (
        <Card>
          <CardContent className="text-center text-sm text-muted-foreground py-12">
            Selecione um escritório acima pra ver e configurar suas classificações.
          </CardContent>
        </Card>
      )}

      {/* Modais */}
      {selectedOffice && (
        <TemplateInlineModal
          open={inlineOpen}
          onOpenChange={setInlineOpen}
          mode={inlineMode}
          officeExternalId={selectedOffice.external_id}
          category={inlineCategory}
          subcategory={inlineSubcategory}
          templateId={inlineTemplateId}
          onSaved={handleSaved}
        />
      )}
      <TemplateReviewModal
        open={reviewOpen}
        onOpenChange={setReviewOpen}
        template={reviewing}
        onMigrated={handleSaved}
      />
    </div>
  );
}

export default OfficeTemplatesView;
