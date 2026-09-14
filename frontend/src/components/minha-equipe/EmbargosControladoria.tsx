// Aba "Embargos à Execução" da Controladoria: o Controle (visão única das
// filas) e o Monitor do tribunal. `?visao=monitor` abre direto no monitor.

import { useState } from "react";
import { Gavel, ListChecks } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import ControleEmbargosView from "@/components/minha-equipe/ControleEmbargosView";
import EmbargosExecucaoTab from "@/components/minha-equipe/EmbargosExecucaoTab";

export default function EmbargosControladoria({ team }: { team: string }) {
  const [visao, setVisao] = useState<"controle" | "monitor">(() =>
    new URLSearchParams(window.location.search).get("visao") === "monitor" ? "monitor" : "controle",
  );

  return (
    <div className="space-y-3">
      <Tabs value={visao} onValueChange={(v) => setVisao(v as "controle" | "monitor")}>
        <TabsList>
          <TabsTrigger value="controle">
            <ListChecks className="mr-1.5 h-4 w-4" /> Controle de Embargos
          </TabsTrigger>
          <TabsTrigger value="monitor">
            <Gavel className="mr-1.5 h-4 w-4" /> Monitor do tribunal
          </TabsTrigger>
        </TabsList>
      </Tabs>

      {visao === "controle" ? (
        <>
          <p className="text-sm text-muted-foreground">
            Todos os embargos à execução do BB Autor num lugar só — venham do <strong>monitor do tribunal</strong>
            {" "}(advogado fora do processo) ou das <strong>Publicações</strong> (com e sem pasta). O caso só sai da
            pendência quando a <strong>pasta dos embargos existe no Legal One</strong>.
          </p>
          <ControleEmbargosView team={team} />
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            Execuções do BB Autor com a <strong>inicial protocolada</strong>. Passada a janela de dias úteis, o
            Flow consulta o tribunal a cada intervalo até achar <strong>embargos à execução</strong> ligados a
            elas — aí para, avisa e mostra aqui para a conferência do vínculo.
          </p>
          <EmbargosExecucaoTab team={team} />
        </>
      )}
    </div>
  );
}
