// O texto da publicação — que é o trabalho de verdade do operador.
//
// Três decisões de leitura, todas contra fadiga em turno de ~50 publicações:
//
// 1. MEDIDA DE LINHA. O texto para em ~72 caracteres por linha em vez de se
//    esticar pela largura do card. Linha de 130 caracteres faz o olho perder
//    o começo da linha seguinte a cada retorno; é o que mais cansa em leitura
//    corrida, mais do que o tamanho da fonte.
// 2. FUNDO QUENTE, NÃO ESCURO. Inverter para claro-sobre-escuro piora texto
//    longo (o texto claro sangra no fundo escuro, e piora com astigmatismo).
//    O que cansa é o brilho do branco puro, então o papel é levemente creme,
//    com contraste alto mas não máximo.
// 3. GRIFO DO TRECHO DECISIVO. A IA já devolve a justificativa e a
//    fundamentação do prazo; o que faltava era mostrá-las NO texto. Com o
//    trecho grifado, conferir a classificação passa de ler três parágrafos a
//    bater o olho — e o erro da IA fica visível em vez de plausível.

import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Highlighter, Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Passos de corpo do texto; o escolhido persiste entre publicações. */
const TAMANHOS = [16, 17.5, 19, 21] as const;
const CHAVE_TAMANHO = "pub-triagem:corpo-texto";

function tamanhoSalvo(): number {
  try {
    const v = Number(localStorage.getItem(CHAVE_TAMANHO));
    return TAMANHOS.includes(v as any) ? v : 17.5;
  } catch {
    return 17.5;
  }
}

/** Forma comparável de um texto: sem acento, minúsculo, espaços colapsados. */
function normalizar(t: string): string {
  return t
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

/**
 * Indexa o texto na forma normalizada, guardando de onde veio cada caractere.
 *
 * O colapso de espaços precisa acontecer durante a varredura, não depois: se
 * normalizar caractere a caractere, dois espaços viram dois espaços e a string
 * normalizada deixa de bater com a que o `normalizar()` produz — o índice
 * encontrado escorrega e o grifo sai deslocado no meio da palavra.
 */
function indexar(texto: string): { norm: string; mapa: number[] } {
  let norm = "";
  const mapa: number[] = [];
  let ultimoFoiEspaco = true; // começa true para não abrir com espaço
  for (let i = 0; i < texto.length; i += 1) {
    const ch = texto[i];
    if (/\s/.test(ch)) {
      if (!ultimoFoiEspaco) {
        norm += " ";
        mapa.push(i);
        ultimoFoiEspaco = true;
      }
      continue;
    }
    const limpo = ch.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
    for (const c of limpo) {
      norm += c;
      mapa.push(i);
    }
    ultimoFoiEspaco = false;
  }
  return { norm, mapa };
}

/**
 * Acha onde cada trecho citado pela IA aparece no texto.
 *
 * Casa pela forma normalizada, porque a IA reescreve acento e espaçamento ao
 * citar. Trecho curto demais viraria grifo espalhado (um "prazo" solto pintaria
 * a publicação inteira), então exige 12 caracteres.
 */
function acharTrechos(texto: string, trechos: string[]): { ini: number; fim: number }[] {
  const { norm, mapa } = indexar(texto);
  const achados: { ini: number; fim: number }[] = [];

  for (const bruto of trechos) {
    const alvo = normalizar(bruto);
    if (alvo.length < 12) continue;
    let de = 0;
    for (;;) {
      const p = norm.indexOf(alvo, de);
      if (p < 0) break;
      const ini = mapa[p];
      // +1 porque `mapa` guarda o índice do caractere, e o fim é exclusivo.
      const fim = mapa[p + alvo.length - 1] + 1;
      if (ini != null && fim != null && fim > ini) achados.push({ ini, fim });
      de = p + alvo.length;
    }
  }

  // Une sobreposições para não abrir marca dentro de marca.
  achados.sort((a, b) => a.ini - b.ini);
  const unidos: { ini: number; fim: number }[] = [];
  for (const a of achados) {
    const ultimo = unidos[unidos.length - 1];
    if (ultimo && a.ini <= ultimo.fim) ultimo.fim = Math.max(ultimo.fim, a.fim);
    else unidos.push({ ...a });
  }
  return unidos;
}

interface Props {
  texto: string;
  /** Trechos que a IA citou ao classificar (justificativa, fundamentação). */
  trechos?: (string | null | undefined)[];
  /** Cabeçalho opcional à direita do controle de corpo. */
  extra?: React.ReactNode;
}

export function TextoPublicacao({ texto, trechos, extra }: Props) {
  const [corpo, setCorpo] = useState<number>(tamanhoSalvo);
  const [expandido, setExpandido] = useState(false);
  const [grifar, setGrifar] = useState(true);

  const limpos = useMemo(
    () => (trechos || []).filter((t): t is string => Boolean(t && t.trim())),
    [trechos],
  );
  const marcas = useMemo(
    () => (grifar && limpos.length ? acharTrechos(texto, limpos) : []),
    [texto, limpos, grifar],
  );

  const partes = useMemo(() => {
    if (!marcas.length) return [{ txt: texto, marcado: false }];
    const out: { txt: string; marcado: boolean }[] = [];
    let cursor = 0;
    for (const m of marcas) {
      if (m.ini > cursor) out.push({ txt: texto.slice(cursor, m.ini), marcado: false });
      out.push({ txt: texto.slice(m.ini, m.fim), marcado: true });
      cursor = m.fim;
    }
    if (cursor < texto.length) out.push({ txt: texto.slice(cursor), marcado: false });
    return out;
  }, [texto, marcas]);

  const mudarCorpo = (dir: 1 | -1) => {
    const i = TAMANHOS.indexOf(corpo as any);
    const novo = TAMANHOS[Math.min(TAMANHOS.length - 1, Math.max(0, i + dir))];
    setCorpo(novo);
    try { localStorage.setItem(CHAVE_TAMANHO, String(novo)); } catch { /* ok */ }
  };

  // Publicação curta não precisa de "ver íntegra": o recorte só atrapalha.
  const longo = texto.length > 1100;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {extra}
        <div className="ml-auto flex items-center gap-1">
          {limpos.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setGrifar((v) => !v)}
              className={cn("h-7 gap-1.5 text-[11.5px]", grifar ? "text-amber-700" : "text-muted-foreground")}
              title={grifar ? "Esconder o grifo do trecho decisivo" : "Grifar o trecho que a IA citou"}
            >
              <Highlighter className="h-3.5 w-3.5" />
              {marcas.length > 0 ? `${marcas.length} trecho(s)` : "sem trecho localizado"}
            </Button>
          )}
          <div className="flex items-center rounded-lg border bg-background">
            <button
              type="button"
              onClick={() => mudarCorpo(-1)}
              disabled={corpo === TAMANHOS[0]}
              className="px-1.5 py-1 text-muted-foreground disabled:opacity-30"
              title="Diminuir o texto"
            >
              <Minus className="h-3.5 w-3.5" />
            </button>
            <span className="px-1 text-[10px] font-bold text-muted-foreground">Aa</span>
            <button
              type="button"
              onClick={() => mudarCorpo(1)}
              disabled={corpo === TAMANHOS[TAMANHOS.length - 1]}
              className="px-1.5 py-1 text-muted-foreground disabled:opacity-30"
              title="Aumentar o texto"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>

      <div
        className={cn(
          "relative rounded-xl border px-6 py-5",
          // Papel levemente quente em vez de branco puro: menos brilho na
          // leitura longa, sem inverter o esquema.
          "bg-[hsl(40_38%_97%)] dark:bg-[hsl(220_28%_13%)]",
          longo && !expandido && "max-h-[420px] overflow-hidden",
        )}
      >
        <p
          className="whitespace-pre-wrap text-[hsl(220_45%_18%)] dark:text-[hsl(220_18%_88%)]"
          style={{
            fontSize: `${corpo}px`,
            lineHeight: 1.75,
            maxWidth: "72ch",
            hyphens: "auto",
          }}
        >
          {texto ? (
            partes.map((p, i) =>
              p.marcado ? (
                <mark
                  key={i}
                  className={cn(
                    // Vidro em vez de tinta: amarelo translúcido em gradiente,
                    // que marca sem apagar o texto nem virar bloco sólido.
                    //
                    // O fundo fica no PRÓPRIO <mark>, não num pseudo-elemento
                    // absoluto. Duas tentativas anteriores falharam e a razão
                    // de cada uma importa:
                    //   1. `backdrop-filter` no <mark> promove o elemento a
                    //      camada de composição e rasteriza o texto junto —
                    //      embaçava justamente o trecho a ler.
                    //   2. mover o filtro para `::before` com `inset-0`
                    //      resolveu o blur mas quebrou o grifo de trecho que
                    //      passa de uma linha: um inline fragmentado tem uma
                    //      caixa POR LINHA, e o pseudo-elemento absoluto
                    //      desenha um retângulo só — o meio do trecho ficava
                    //      sem marca (caso real: uma citação de 112 chars em
                    //      3 linhas aparecia como dois pedaços soltos).
                    // Sem filtro e com fundo próprio, o grifo acompanha cada
                    // fragmento; `box-decoration-break: clone` repete borda e
                    // cantos em toda linha em vez de só na primeira e na última.
                    "rounded-[3px] px-0.5 py-[1px] text-inherit",
                    "bg-gradient-to-b from-amber-200/55 to-amber-300/40",
                    "shadow-[inset_0_0_0_1px_hsl(43_90%_45%/0.28)]",
                    "[-webkit-box-decoration-break:clone] [box-decoration-break:clone]",
                  )}
                >
                  {p.txt}
                </mark>
              ) : (
                <span key={i}>{p.txt}</span>
              ),
            )
          ) : (
            <span className="text-muted-foreground">Sem texto capturado nesta publicação.</span>
          )}
        </p>
        {longo && !expandido && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-20 bg-gradient-to-b from-transparent to-[hsl(40_38%_97%)] dark:to-[hsl(220_28%_13%)]" />
        )}
      </div>

      {longo && (
        <Button
          variant="ghost"
          size="sm"
          className="mt-1 h-7 text-muted-foreground"
          onClick={() => setExpandido((v) => !v)}
        >
          {expandido
            ? <><ChevronUp className="mr-1 h-3.5 w-3.5" /> Recolher</>
            : <><ChevronDown className="mr-1 h-3.5 w-3.5" /> Ver íntegra</>}
        </Button>
      )}
    </div>
  );
}

export default TextoPublicacao;
