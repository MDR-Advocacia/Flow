"""Pesquisa de VÍNCULOS no portal do BB — processos em comum por parte envolvida.

No ato da distribuição/cadastro, pra cada parte do processo capturado (exceto o
próprio Banco do Brasil) a gente pergunta ao portal: essa pessoa é parte em
OUTRAS ações? Quais estão ativas e conduzidas pelo NOSSO escritório (MDR)?

Decodificado dos HARs reais (2026-07-20). São endpoints JSON limpos do PAJ — não
precisa de Playwright; reusamos a sessão autenticada do OneLog num requests.Session.

Fluxo (3 chamadas):
  1) doc → numeroPessoa:
       GET  {base}/resources/app/v2/portal/cadastro/processo/pessoas/
            pesquisa-avancada/{cpf | cnpj-alfanumerico}/{doc}?inicioBusca=0&somente=paj
  2) processos da pessoa:
       POST {base}/resources/app/v1/processo/consulta/consulta-parte-envolvida
            body {tipoEnvolvimento:"P", numeroPessoaParte, ajuizado:"T",
                  estadoNPJ:"T", tipoVariacao:"T", inicioPesquisa:1}
  3) polo (só dos vínculos confirmados):
       GET  {base}/resources/app/v1/processo/consulta/{numeroProcesso}
            → indicadorPoloBanco ('A'=Ativo/Autor lado banco, 'P'=Passivo/Réu)

Regra do vínculo (campos decodificados, sem heurística):
  - ATIVO   = indicadorProcessoAtivo == 'A'  (cancelados vêm 'I')
  - NOSSO   = numeroAdvogadoProcesso == ADVOGADO_MDR  (outros vêm 'Não Cadastrado'/0)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

import requests

from app.core.config import settings

logger = logging.getLogger("distribuidos_bb.vinculos")


class VinculoAcessoNegado(RuntimeError):
    """O portal recusou a consulta (403/401/HTML de erro) — NÃO é 'sem vínculo'.

    Existe para separar dois estados que o código antigo confundia: "pesquisei e
    a parte não tem outra ação nossa" (resultado legítimo, vazio) de "não
    consegui nem pesquisar" (acesso caiu). Sem essa distinção o motor devolve
    zero em silêncio e a operação lê isso como se fosse resposta do portal.
    """


def _checar_resposta(r: Any, etapa: str) -> None:
    """Levanta VinculoAcessoNegado quando a resposta não é o JSON esperado."""
    ct = (r.headers.get("content-type") or "").lower()
    if r.status_code in (401, 403) or (r.status_code == 200 and "json" not in ct and r.text.strip().startswith("<")):
        raise VinculoAcessoNegado(
            f"{etapa}: portal recusou a consulta (HTTP {r.status_code}, content-type={ct or '?'}). "
            "Sessão do BB sem permissão para a API do PAJ ou endpoint alterado."
        )

# Código do advogado do BB que identifica o nosso escritório (MARCOS DELLI
# RIBEIRO) na lista de partes. Editável via config bbd_config `vinculo_advogado_mdr`.
ADVOGADO_MDR_DEFAULT = 8706512
# CNPJ do próprio Banco do Brasil — nunca pesquisar como "parte" (é o cliente).
CNPJ_BB = "00000000000191"

# Situações que NÃO contam como vínculo: a pasta existe no portal mas o processo
# ainda não foi distribuído pra gente (montagem de dossiê = provável recuperação
# de crédito futura). Casa por substring, minúsculo e sem acento.
# Editável via config `vinculo_situacoes_excluidas` (lista separada por vírgula).
SITUACOES_EXCLUIDAS_DEFAULT = "montagem"

_BASE_DEFAULT = "https://juridico.bb.com.br/paj"
# Página da SPA de consulta — é o Referer que o portal envia em toda chamada
# da API. Sem ele a borda devolve 403 em HTML.
REFERER_SPA = (
    "https://juridico.bb.com.br/paj/app/paj-cadastro/spas/processo/consulta/"
    "processo-consulta.app.html"
)


def _origem(base_url: Optional[str] = None) -> str:
    """Só o esquema+host do portal (o Origin que o navegador manda no POST)."""
    b = _base(base_url)
    partes = b.split("/")
    return "//".join([partes[0], partes[2]]) if len(partes) > 2 else b


def apenas_digitos(v: Optional[str]) -> str:
    return re.sub(r"\D", "", v or "")


def _base(base_url: Optional[str] = None) -> str:
    return (base_url or getattr(settings, "distribuidos_bb_paj_base", _BASE_DEFAULT)).rstrip("/")


def montar_sessao(cookies_onelog: list[dict[str, Any]], user_agent: str) -> requests.Session:
    """requests.Session com os cookies autenticados do OneLog (mesma sessão do RPA)."""
    sess = requests.Session()
    for c in cookies_onelog or []:
        nome = c.get("name")
        valor = c.get("value")
        if not nome:
            continue
        sess.cookies.set(
            nome, valor,
            domain=c.get("domain") or "juridico.bb.com.br",
            path=c.get("path") or "/",
        )
    # Headers copiados do HAR real do portal (31/08/2026). Não é enfeite: a
    # borda do BB recusava com 403 (HTML de erro, não JSON) o conjunto que
    # usávamos antes. Duas diferenças mataram a chamada:
    #   - mandávamos `X-Requested-With: XMLHttpRequest`, que o portal NÃO manda;
    #   - não mandávamos `Referer`, que o portal manda em toda chamada da SPA.
    # O 403 era indistinguível de "parte sem vínculo" até a exceção criada aqui.
    sess.headers.update({
        "User-Agent": user_agent or "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": REFERER_SPA,
        "Origin": _origem(),
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    })
    return sess


def _resolver_numero_pessoa(sess: requests.Session, doc: str, *, base_url: str,
                            timeout: int = 30) -> Optional[int]:
    """Passo 1: documento (CPF/CNPJ) → numeroPessoa do cadastro do BB."""
    digs = apenas_digitos(doc)
    if len(digs) == 14:
        rota = f"cnpj-alfanumerico/{digs}"
    elif len(digs) == 11:
        rota = f"cpf/{digs}"
    else:
        return None
    url = f"{base_url}/resources/app/v2/portal/cadastro/processo/pessoas/pesquisa-avancada/{rota}"
    r = sess.get(url, params={"inicioBusca": 0, "somente": "paj"}, timeout=timeout)
    _checar_resposta(r, "pesquisa da pessoa")
    if r.status_code != 200 or not r.text.strip():
        logger.warning("Vínculos: pesquisa da pessoa devolveu HTTP %s (doc %s).", r.status_code, digs[:6] + "…")
        return None
    lista = (r.json().get("data") or {}).get("listaOcorrencia") or []
    if not lista:
        return None
    return lista[0].get("numeroPessoa")


def _listar_processos_da_parte(sess: requests.Session, numero_pessoa: int, *, base_url: str,
                               timeout: int = 30) -> list[dict[str, Any]]:
    """Passo 2: todos os processos em que a pessoa é parte envolvida."""
    url = f"{base_url}/resources/app/v1/processo/consulta/consulta-parte-envolvida"
    body = {
        "tipoEnvolvimento": "P",
        "numeroPessoaParte": numero_pessoa,
        "ajuizado": "T",
        "estadoNPJ": "T",
        "tipoVariacao": "T",
        "inicioPesquisa": 1,
    }
    r = sess.post(url, json=body, timeout=timeout)
    _checar_resposta(r, "processos da parte")
    if r.status_code != 200:
        logger.warning("Vínculos: lista de processos da parte devolveu HTTP %s.", r.status_code)
        return []
    return (r.json().get("data") or {}).get("listaOcorrencia") or []


def _consultar_polo(sess: requests.Session, numero_processo: int, *, base_url: str,
                    timeout: int = 30) -> Optional[str]:
    """Passo 3: polo do banco no processo. 'A'=Ativo (Autor) / 'P'=Passivo (Réu)."""
    url = f"{base_url}/resources/app/v1/processo/consulta/{numero_processo}"
    r = sess.get(url, timeout=timeout)
    if r.status_code != 200:
        # Tolerado de propósito: sem o polo o vínculo ainda é válido e útil.
        logger.info("Vínculos: polo indisponível pro processo %s (HTTP %s).", numero_processo, r.status_code)
        return None
    return (r.json().get("data") or {}).get("indicadorPoloBanco")


def _fmt_npj(numero_processo: Any) -> str:
    """20260034965 → '2026/0034965-000' (máscara NPJ da casa)."""
    d = apenas_digitos(str(numero_processo))
    if len(d) >= 11:
        return f"{d[:4]}/{d[4:11]}-000"
    return str(numero_processo)


def _sem_acento(v: Optional[str]) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", str(v or "").strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def situacao_excluida(situacao: Optional[str], excluidas: str = SITUACOES_EXCLUIDAS_DEFAULT) -> bool:
    """True quando a situação indica que o processo NÃO foi distribuído pra nós."""
    s = _sem_acento(situacao)
    if not s:
        return False
    return any(t.strip() and _sem_acento(t) in s for t in (excluidas or "").split(","))


def _polo_texto(indicador: Optional[str]) -> Optional[str]:
    return {"A": "Ativo", "P": "Passivo"}.get((indicador or "").strip().upper())


def _posicao_texto(indicador: Optional[str]) -> Optional[str]:
    # Lado do BANCO: Ativo → BB é Autor; Passivo → BB é Réu.
    return {"A": "Autor", "P": "Réu"}.get((indicador or "").strip().upper())


def pesquisar_vinculos_parte(
    sess: requests.Session,
    doc: str,
    *,
    advogado_mdr: int = ADVOGADO_MDR_DEFAULT,
    base_url: Optional[str] = None,
    incluir_polo: bool = True,
    situacoes_excluidas: str = SITUACOES_EXCLUIDAS_DEFAULT,
) -> dict[str, Any]:
    """Pesquisa os processos da parte e devolve os VÍNCULOS ativos-nossos.

    Devolve {numero_pessoa, total, ativos_mdr:[...], todos:[...]}. Cada item de
    `ativos_mdr` tem npj, cnj, cliente, advogado_bb, situacao, natureza, polo,
    posicao_banco. `todos` é a lista bruta (útil pra auditoria).
    """
    base = _base(base_url)
    digs = apenas_digitos(doc)
    if not digs or digs == CNPJ_BB:
        return {"numero_pessoa": None, "total": 0, "ativos_mdr": [], "todos": []}

    numero_pessoa = _resolver_numero_pessoa(sess, digs, base_url=base)
    if not numero_pessoa:
        return {"numero_pessoa": None, "total": 0, "ativos_mdr": [], "todos": []}

    ocorrencias = _listar_processos_da_parte(sess, numero_pessoa, base_url=base)
    ativos_mdr: list[dict[str, Any]] = []
    for o in ocorrencias:
        ativo = (o.get("indicadorProcessoAtivo") or "").strip().upper() == "A"
        nosso = int(o.get("numeroAdvogadoProcesso") or 0) == int(advogado_mdr)
        if not (ativo and nosso):
            continue
        # Ainda não distribuído pra nós (montagem de dossiê) → não é vínculo.
        if situacao_excluida(o.get("textoEstadoProcesso"), situacoes_excluidas):
            continue
        numero_proc = o.get("numeroProcesso")
        cnj = apenas_digitos(o.get("textoNumeroInventario")) or None
        polo = _consultar_polo(sess, numero_proc, base_url=base) if incluir_polo else None
        ativos_mdr.append({
            "npj": _fmt_npj(numero_proc),
            "numero_processo": numero_proc,
            "cnj": cnj,
            "cliente": o.get("nomeContrarioPrincipal"),
            "advogado_bb": o.get("nomeAdvogadoProcesso"),
            "numero_advogado": o.get("numeroAdvogadoProcesso"),
            "situacao": o.get("textoEstadoProcesso"),
            "natureza": o.get("textoNaturezaProcesso"),
            "uja": o.get("codigoPrefixoDependencia"),
            "polo": _polo_texto(polo),
            "posicao_banco": _posicao_texto(polo),
        })

    return {
        "numero_pessoa": numero_pessoa,
        "total": len(ocorrencias),
        "ativos_mdr": ativos_mdr,
        "todos": ocorrencias,
    }
