// Times (setores/supervisões) do Minha Equipe.
//
// A fonte da verdade é a tabela `perf_equipe` no backend (CRUD no Painel
// Administrativo → Equipes). Este módulo mantém um cache em memória do
// catálogo e uma lista estática de FALLBACK, usada no primeiro paint (antes da
// resposta chegar) e se a API falhar — sem ela a sidebar piscaria vazia.
//
// `teamLabel` continua SÍNCRONO de propósito: é chamado direto no render de
// vários componentes. Quem precisa re-renderizar quando o catálogo chega usa o
// hook `useTeams()`.

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api-client";

export const GRUPO_PASSIVO = "Contencioso Passivo";
export const GRUPO_CREDITO = "Recuperação de Crédito";
// A Equipe Mista atende Réu E Autor da mesma parte (processos vinculados),
// então tem grupo próprio em vez de forçar um dos dois lados.
export const GRUPO_ESPECIALIZADA = "Especializada";

export interface Team {
  key: string;
  label: string;
  grupo: string;
}

// Espelha o seed da migration perf012. NÃO é a fonte da verdade — equipe criada
// pelo admin aparece aqui só depois que o catálogo carrega.
const FALLBACK: Team[] = [
  { key: "bb-reu", label: "BB Réu", grupo: GRUPO_PASSIVO },
  { key: "bb-execucao", label: "BB Execução & Encerramento", grupo: GRUPO_PASSIVO },
  { key: "bb-acordos", label: "BB Acordos", grupo: GRUPO_PASSIVO },
  { key: "bb-estrategico", label: "BB Estratégico", grupo: GRUPO_PASSIVO },
  { key: "master-reu", label: "Master Réu", grupo: GRUPO_PASSIVO },
  { key: "ativos-reu", label: "Ativos Réu", grupo: GRUPO_PASSIVO },
  { key: "trabalhista", label: "Trabalhista", grupo: GRUPO_PASSIVO },
  { key: "bb-autor-processual", label: "BB Autor — Processual", grupo: GRUPO_CREDITO },
  { key: "ativos-autor", label: "Ativos Autor", grupo: GRUPO_CREDITO },
  { key: "autor-recursal", label: "Autor — Recursal", grupo: GRUPO_CREDITO },
  { key: "ajuizamento", label: "Ajuizamento", grupo: GRUPO_CREDITO },
  { key: "estrategico-autor", label: "Estratégico Autor", grupo: GRUPO_CREDITO },
  { key: "cobranca", label: "Cobrança", grupo: GRUPO_CREDITO },
  { key: "equipe-mista", label: "Equipe Mista", grupo: GRUPO_ESPECIALIZADA },
  // Controladoria — sucede o antigo "BB Cadastro" (perfil extinto em 2026-07-20).
  // Key preservada de propósito: as permissões já concedidas apontam pra ela.
  { key: "bb-cadastro", label: "Controladoria", grupo: GRUPO_ESPECIALIZADA },
];

// Ordem canônica dos grupos no menu; grupo novo (criado pelo admin) entra no fim.
const ORDEM_GRUPOS = [GRUPO_PASSIVO, GRUPO_CREDITO, GRUPO_ESPECIALIZADA];

let _teams: Team[] = FALLBACK;
let _carregando: Promise<void> | null = null;
let _carregado = false;
const _subs = new Set<() => void>();

function _notificar() {
  _subs.forEach((fn) => fn());
}

/** Busca o catálogo no backend (uma vez por sessão, salvo `force`). */
export async function carregarEquipes(force = false): Promise<void> {
  if (_carregado && !force) return;
  if (_carregando && !force) return _carregando;
  _carregando = (async () => {
    try {
      const res = await apiFetch("/api/v1/equipes");
      if (!res.ok) return; // mantém o fallback — melhor lista velha que vazia
      const body = await res.json();
      const lista = (body?.equipes ?? []) as Team[];
      if (Array.isArray(lista) && lista.length) {
        _teams = lista;
        _carregado = true;
        _notificar();
      }
    } catch {
      /* offline/erro: segue com o que já tem */
    } finally {
      _carregando = null;
    }
  })();
  return _carregando;
}

/** Lista atual (fallback até o catálogo chegar). */
export function getTeams(): Team[] {
  return _teams;
}

export const TEAMS = FALLBACK;

export function teamKeys(): string[] {
  return _teams.map((t) => t.key);
}

export function teamLabel(key: string): string {
  return _teams.find((t) => t.key === key)?.label ?? key;
}

export function isValidTeam(key: string | undefined): boolean {
  return !!key && _teams.some((t) => t.key === key);
}

/** Grupos na ordem canônica, com os criados pelo admin no fim. */
export function gruposDeEquipes(teams: Team[] = _teams): string[] {
  const vistos = Array.from(new Set(teams.map((t) => t.grupo)));
  const conhecidos = ORDEM_GRUPOS.filter((g) => vistos.includes(g));
  const novos = vistos.filter((g) => !ORDEM_GRUPOS.includes(g)).sort();
  return [...conhecidos, ...novos];
}

/** Assina o catálogo e dispara o carregamento — re-renderiza quando chega. */
export function useTeams(): Team[] {
  const [, forcar] = useState(0);
  useEffect(() => {
    const fn = () => forcar((v) => v + 1);
    _subs.add(fn);
    void carregarEquipes();
    return () => {
      _subs.delete(fn);
    };
  }, []);
  return _teams;
}
