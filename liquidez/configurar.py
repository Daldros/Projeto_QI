"""Monta a configuracao do projeto a partir do riscogc.

A pasta `config/` nao veio no pacote original (decisao de 05/10/2026: o que for
necessario e reconstruido do banco). Este modulo gera:

  config/fundos/<cnpj>.json      fichas, de HOD_TB_CADASTRO_FUNDOS (Situacao NORMAL)
  config/calendario/feriados.csv de AUD_DIM_FERIADOS
  config/isin_cotas.csv          delegado a `cotas.baixar` (ja existia)
  config/prazos_ativos.csv       prazos fixos + tabela levantada pelo gestor + cotas de fundo na carteira
  config/regras_vencidos.json    copia de config/parametros/regras_vencidos.json
  config/ativos_excluidos.json   copia de config/parametros/ativos_excluidos.json
  config/indicadores.json        copia de config/parametros/indicadores.json
  config/produtos.json           copia de config/parametros/produtos.json (de-para Base_Origem)

O que e decisao da area (limites, regras, prazos levantados regulamento por
regulamento) fica versionado em `config/parametros/` e VENCE o banco:
`fundos_overrides.csv` traz pz_resg/cc/codinome por CNPJ e
`prazos_ativos_gestor.csv` e a tabela de prazos por ativo que o gestor mantinha
a mao (pacote de 21/10/2026, 289 ativos).

O que o pacote do gestor traz e nao pode ir para o git (cronogramas por
operacao, cadastros manuais, documentos de CRA) entra na `config/` local por
`importar_pacote` -- ver `python -m liquidez importar-pacote`.

As funcoes `montar_*` sao puras (recebem DataFrames) e as `consultar_*` fazem
o SQL -- assim os testes rodam sem banco.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .cadastro import Cadastro, Fundo
from .cotas import SEM_PRAZO, ler_prazo
from .cvm import formatar_cnpj, so_digitos
from .prazos import Prazo, TabelaPrazos
from .tabelas import CARTEIRA

log = logging.getLogger(__name__)

DIR_CONFIG = Path("config")
DIR_PARAMETROS = DIR_CONFIG / "parametros"
ARQ_OVERRIDES = DIR_PARAMETROS / "fundos_overrides.csv"
ARQ_PRAZOS_FIXOS = DIR_PARAMETROS / "prazos_fixos.csv"
# Tabela de prazos do gestor (config/prazos_ativos.csv do pacote de 21/10/2026),
# revisada ativo a ativo por ele -- e onde estao os prazos das cotas de outros
# fundos que o nome abreviado do administrador nao deixa casar com o cadastro.
ARQ_PRAZOS_GESTOR = DIR_PARAMETROS / "prazos_ativos_gestor.csv"
COPIAS = ("regras_vencidos.json", "ativos_excluidos.json", "indicadores.json", "produtos.json")

# Base_Origem da carteira que e cota de fundo: a mesma lista de grafo_fundo.BASES_COTA
# (duplicada aqui para o pacote nao depender do script da raiz).
BASES_COTA = (
    "Outros Fundos",
    "Fundos-Fundos",
    "Fundos-Fundos_a_Mercado",
    "COTAS DE FUNDOS EXTERNOS",
    "Carteira de Fundos de Investimentos",
    "PORTFOLIO_INVESTIDO",
    "COTASAPLICADAS",
    "Cotas de Investimento",
    "COTAS FUNDOS DE INVESTIMENTOS",
)

# --- SQL (so SELECT; parametros com ?) ----------------------------------------

SQL_CADASTRO = (
    "SELECT CNPJFundo, idFundoInterno, idFundoAdm, nmFundoInterno, nmCotaInterno, "
    "       TipoFundo, Condominio, Situacao, LiquidacaoResgate, CarenciaResgate, "
    "       Administrador, CNPJAdministrador, Gestor, CNPJGestor, RazaoSocial, "
    "       DataPrimeiroAporte, RegistroCVM "
    "FROM dbo.HOD_TB_CADASTRO_FUNDOS WHERE Situacao = ?"
)
SQL_FUNDOS_CARTEIRA = (
    f"SELECT DISTINCT docFundo AS CPF_CNPJ FROM {CARTEIRA} "
    f"WHERE dtReferencia = (SELECT MAX(dtReferencia) FROM {CARTEIRA})"
)
SQL_DEPARA_BEYOND = "SELECT dsNomeFundoAdm, nmFundoByd, flAtivo FROM dbo.AUD_DIM_DEPARA_FUNDOS"
SQL_FERIADOS = "SELECT dtFeriado, dsFeriado FROM dbo.AUD_DIM_FERIADOS ORDER BY dtFeriado"
SQL_COTAS_CARTEIRA = (
    "SELECT DISTINCT cdOp AS Codigo_Op, cdAtivo AS Codigo_Ativo, dsNomeAtivo AS Nome_Ativo"
    f" FROM {CARTEIRA} "
    f"WHERE dtReferencia = (SELECT MAX(dtReferencia) FROM {CARTEIRA}) "
    "  AND dsBaseOrigem IN (" + ",".join("?" * len(BASES_COTA)) + ")"
)
# Mesma consulta numa data dada: a posicao mais recente do banco pode estar
# incompleta (02/10/2026 tinha 475 linhas em 16 fundos; o MULTIPLICA FIDC
# sozinho tinha 453 em 30/09) e a lista de cotas sai pela metade.
SQL_COTAS_CARTEIRA_DATA = (
    "SELECT DISTINCT cdOp AS Codigo_Op, cdAtivo AS Codigo_Ativo, dsNomeAtivo AS Nome_Ativo"
    f" FROM {CARTEIRA} "
    "WHERE dtReferencia = ? AND dsBaseOrigem IN (" + ",".join("?" * len(BASES_COTA)) + ")"
)
ARQ_COTAS_DEPARA = DIR_PARAMETROS / "cotas_depara.csv"


@dataclass
class Relatorio:
    fichas_novas: int = 0
    fichas_mantidas: int = 0
    fichas_sem_prazo: int = 0
    overrides_aplicados: int = 0
    sem_codinome: list[str] = field(default_factory=list)
    feriados: int = 0
    prazos: int = 0
    prazos_sem_prazo: int = 0
    prazos_do_gestor: int = 0
    arquivos: list[Path] = field(default_factory=list)


# --- overrides (parametros da area) --------------------------------------------

def carregar_overrides(caminho: Path = ARQ_OVERRIDES) -> dict[str, dict]:
    """cnpj(14 digitos) -> {pz_resg, cc, codinome_beyond, nome_produto_mov, ativo, observacao}."""
    caminho = Path(caminho)
    if not caminho.exists():
        return {}
    saida: dict[str, dict] = {}
    with caminho.open(encoding="utf-8-sig", newline="") as arq:
        for linha in csv.DictReader(arq, delimiter=";"):
            cnpj = so_digitos(linha.get("cnpj"))
            if not cnpj:
                continue
            reg = {}
            for campo in ("pz_resg", "cc"):
                v = (linha.get(campo) or "").strip()
                if v:
                    reg[campo] = int(float(v))
            for campo in ("codinome_beyond", "nome_produto_mov", "observacao"):
                v = (linha.get(campo) or "").strip()
                if v:
                    reg[campo] = v
            v = (linha.get("ativo") or "").strip().lower()
            if v in ("0", "nao", "não", "false", "n"):
                reg["ativo"] = False
            saida[cnpj] = reg
    return saida


# --- fichas --------------------------------------------------------------------

def _prazo_do_cadastro(linhas: pd.DataFrame) -> int | None:
    """Maior prazo entre as linhas (ORIGINAL e classes) do CNPJ; FECHADO = 999."""
    prazos = [ler_prazo(l.get("LiquidacaoResgate"), l.get("Condominio")) for _, l in linhas.iterrows()]
    prazos = [p for p in prazos if p is not None]
    return max(prazos) if prazos else None


def _codinome(nome_interno: str, depara: pd.DataFrame) -> str | None:
    if depara is None or depara.empty:
        return None
    alvo = str(nome_interno or "").strip().upper()
    for _, l in depara.iterrows():
        if str(l.get("dsNomeFundoAdm") or "").strip().upper() == alvo and l.get("flAtivo", True):
            return str(l.get("nmFundoByd") or "").strip() or None
    return None


def montar_fichas(
    cadastro_df: pd.DataFrame,
    fundos_carteira: set[str] | None,
    depara: pd.DataFrame,
    overrides: dict[str, dict],
    existentes: Cadastro | None = None,
    hoje: dt.date | None = None,
) -> tuple[list[Fundo], Relatorio]:
    """Uma ficha por CNPJ do cadastro (Situacao NORMAL).

    `fundos_carteira` (CNPJs com posicao na carteira mais recente) restringe o
    universo quando informado. Ficha existente mantem os blocos `liquidez` e
    `integracao` que ja tiver; o override vence tudo.
    """
    rel = Relatorio()
    hoje = hoje or dt.date.today()
    fichas: list[Fundo] = []
    carteira = {so_digitos(c) for c in (fundos_carteira or set())}
    ja = (existentes.fundos if existentes else {})

    for cnpj_bruto, linhas in cadastro_df.groupby("CNPJFundo", sort=True):
        cnpj = so_digitos(cnpj_bruto)
        if len(cnpj) != 14 or (carteira and cnpj not in carteira):
            continue
        orig = linhas[linhas["nmCotaInterno"].fillna("").str.upper() == "ORIGINAL"]
        base = (orig if not orig.empty else linhas).iloc[0]
        nome = str(base.get("nmFundoInterno") or "").strip()
        ov = overrides.get(cnpj, {})
        anterior = ja.get(cnpj)

        liquidez = dict(anterior.liquidez) if anterior else {}
        integracao = dict(anterior.integracao) if anterior else {}
        controle = dict(anterior.controle) if anterior else {}

        if "pz_resg" not in liquidez:
            liquidez["pz_resg"] = _prazo_do_cadastro(linhas)
        if "cc" not in liquidez:
            liquidez["cc"] = liquidez["pz_resg"]       # cotizacao: sem coluna no banco; padrao = pz_resg
        if "codinome_beyond" not in integracao:
            integracao["codinome_beyond"] = _codinome(nome, depara)
        if ov:
            rel.overrides_aplicados += 1
            for campo in ("pz_resg", "cc"):
                if campo in ov:
                    liquidez[campo] = ov[campo]
            for campo in ("codinome_beyond", "nome_produto_mov"):
                if campo in ov:
                    integracao[campo] = ov[campo]
            if "ativo" in ov:
                controle["ativo"] = ov["ativo"]
            if "observacoes" not in controle and ov.get("observacao"):
                controle["observacoes"] = ov["observacao"]
        controle.setdefault("ativo", True)
        controle.setdefault("incluir_no_lote", True)
        controle["atualizado_em"] = hoje.isoformat()
        controle["origem"] = "riscogc.HOD_TB_CADASTRO_FUNDOS"

        # bloco cvm: o sync-cvm (web) e a fonte oficial; o banco entra como ponto de partida
        cvm = dict(anterior.cvm) if anterior and anterior.cvm else {
            "administrador": {"nome": str(base.get("Administrador") or "").strip() or None,
                              "cnpj": formatar_cnpj(base.get("CNPJAdministrador"))},
            "gestor": {"nome": str(base.get("Gestor") or "").strip() or None,
                       "cnpj": formatar_cnpj(base.get("CNPJGestor"))},
            "tipo_fundo": str(base.get("TipoFundo") or "").strip() or None,
            "razao_social": str(base.get("RazaoSocial") or "").strip() or None,
            "fonte": "riscogc (provisorio; rodar fundos sync-cvm)",
        }

        if liquidez.get("pz_resg") is None:
            rel.fichas_sem_prazo += 1
        if not integracao.get("codinome_beyond"):
            rel.sem_codinome.append(nome)
        if anterior:
            rel.fichas_mantidas += 1
        else:
            rel.fichas_novas += 1
        fichas.append(Fundo(cnpj=formatar_cnpj(cnpj) or cnpj, nome_interno=nome, cvm=cvm,
                            liquidez=liquidez, integracao=integracao, controle=controle))
    return fichas, rel


# --- feriados ------------------------------------------------------------------

def montar_feriados(df: pd.DataFrame) -> list[tuple[dt.date, str]]:
    saida = []
    for _, l in df.iterrows():
        d = l.get("dtFeriado")
        if d is None or pd.isna(d):
            continue
        d = d.date() if hasattr(d, "date") and not isinstance(d, dt.date) else d
        if isinstance(d, dt.datetime):
            d = d.date()
        saida.append((d, str(l.get("dsFeriado") or "").strip()))
    return sorted(set(saida))


def gravar_feriados(feriados: list[tuple[dt.date, str]], caminho: Path) -> Path:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    linhas = ["data;descricao"] + [f"{d.isoformat()};{desc}" for d, desc in feriados]
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return caminho


# --- prazos dos ativos -----------------------------------------------------------

def carregar_prazos_fixos(caminho: Path = ARQ_PRAZOS_FIXOS) -> list[Prazo]:
    caminho = Path(caminho)
    if not caminho.exists():
        return []
    with caminho.open(encoding="utf-8-sig", newline="") as arq:
        return [Prazo.from_row(l) for l in csv.DictReader(arq, delimiter=";")]


def carregar_cotas_depara(caminho: Path = ARQ_COTAS_DEPARA) -> dict[str, str]:
    """codigo_ativo (cota na carteira) -> CNPJ do fundo investido, decidido a mao.

    E a saida para a cota cujo nome abreviado do administrador nao casa com o
    cadastro ("APOLLO II FIDCNP MZ1", "ECOAGMUL MEZ A"). Arquivo opcional.
    """
    caminho = Path(caminho)
    if not caminho.exists():
        return {}
    df = pd.read_csv(caminho, sep=";", dtype=str, encoding="utf-8-sig").fillna("")
    return {str(r["codigo_ativo"]).strip().upper(): so_digitos(r["cnpj"])
            for _, r in df.iterrows() if str(r.get("codigo_ativo", "")).strip() and str(r.get("cnpj", "")).strip()}


def montar_prazos(cotas_carteira: pd.DataFrame, fichas: list[Fundo], fixos: list[Prazo],
                  depara_cotas: dict[str, str] | None = None,
                  levantados: list[Prazo] | None = None) -> TabelaPrazos:
    """Cota de fundo na carteira -> prazo do fundo investido, quando o nome do
    ativo casa com o nome interno de um fundo do cadastro (ou quando o de-para
    manual aponta o CNPJ); senao fica sem prazo (AVISO no validar). Entradas
    fixas (CPR, tesouraria...) entram antes.

    `levantados` e a tabela do gestor, e entra logo depois das fixas: o
    `Codigo_Ativo` que ela ja resolveu nao passa pelo casamento por nome. Linha
    sem prazo nela fica de fora, para o banco ainda poder resolver. Linha de
    origem `cadastro` com CNPJ segue a ficha desse fundo quando ela tem
    `pz_resg` (o mesmo que o `prazos sync-cadastro` do gestor fazia); sem ficha,
    vale o prazo que o gestor anotou.

    `fichas` deve ser o cadastro INTEIRO (todos os fundos NORMAL), nao so os que
    tem carteira: o fundo investido raramente tem carteira propria no riscogc.
    Sem prazo a cota entra como liquidez zero -- e a premissa conservadora e a
    que vale ate alguem decidir (decisao da area em 05/10/2026).
    """
    depara_cotas = depara_cotas or {}
    por_nome = {f.nome_interno.strip().upper(): f for f in fichas}
    por_cnpj = {so_digitos(f.cnpj): f for f in fichas}
    registros = list(fixos)
    vistos = {r.codigo_ativo.strip().upper() for r in registros}
    for r in levantados or []:
        chave = r.codigo_ativo.strip().upper()
        if not chave or chave in vistos or r.prazo is None:
            continue
        vistos.add(chave)
        fundo = por_cnpj.get(so_digitos(r.cnpj)) if r.origem_prazo == "cadastro" and r.cnpj else None
        if fundo is not None and fundo.pz_resg is not None:
            r = Prazo(codigo_op=r.codigo_op, codigo_ativo=r.codigo_ativo, cnpj=r.cnpj,
                      prazo=fundo.pz_resg, origem_prazo="cadastro", observacao=r.observacao)
        registros.append(r)
    for _, l in cotas_carteira.iterrows():
        codigo = str(l.get("Codigo_Ativo") or "").strip()
        if not codigo or codigo.upper() in vistos:
            continue
        vistos.add(codigo.upper())
        nome = str(l.get("Nome_Ativo") or "").strip().upper()
        fundo = por_cnpj.get(depara_cotas.get(codigo.upper(), ""))
        if fundo is None:
            fundo = por_nome.get(nome) or next((f for n, f in por_nome.items() if n and n in nome), None)
        if fundo is not None and fundo.pz_resg is not None:
            registros.append(Prazo(codigo_op=str(l.get("Codigo_Op") or ""), codigo_ativo=codigo,
                                   cnpj=fundo.cnpj, prazo=fundo.pz_resg, origem_prazo="cadastro",
                                   observacao=f"ficha {fundo.nome_interno}"))
        else:
            registros.append(Prazo(codigo_op=str(l.get("Codigo_Op") or ""), codigo_ativo=codigo,
                                   observacao="sem fundo da casa correspondente: definir com 'prazos set'"))
    return TabelaPrazos(registros)


# --- copias de parametros ----------------------------------------------------------

def copiar_parametros(origem: Path = DIR_PARAMETROS, destino: Path = DIR_CONFIG) -> list[Path]:
    from .regras_vencidos import CatalogoRegras

    gravados = []
    for nome in COPIAS:
        src = Path(origem) / nome
        if not src.exists():
            raise FileNotFoundError(f"{src} nao existe: parametro da area obrigatorio.")
        json.loads(src.read_text(encoding="utf-8"))           # valida o JSON
        dst = Path(destino) / nome
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        gravados.append(dst)
    CatalogoRegras.carregar(Path(destino) / "regras_vencidos.json")   # valida as regras
    ind = json.loads((Path(destino) / "indicadores.json").read_text(encoding="utf-8"))
    for chave in ("limites", "fator_gdp", "anbima_horizontes_dias", "anbima_percentil_95", "resgate"):
        if chave not in ind:
            raise KeyError(f"indicadores.json sem a chave {chave!r}")
    if len(ind["anbima_horizontes_dias"]) != len(ind["anbima_percentil_95"]):
        raise ValueError("indicadores.json: horizontes e percentis com tamanhos diferentes")
    return gravados


# --- pacote do gestor (so config local) -------------------------------------------

# O que o pacote do gestor (21/10/2026) traz e so pode morar na config/ LOCAL:
# dado por operacao -- cronograma da planilha de conciliacao, cadastro manual,
# Termo de CRA -- nao vai para o git, mesma regra do bi/ e da carga. Caminhos
# relativos a config/ do pacote. Fica de fora o que o projeto gera do riscogc
# de producao (fichas, feriados, prazos, isin_cotas, cronograma/beyond) e os
# backups (`*_antes_*`, `_anterior/`, `processados/`): copiar o do gestor
# misturaria a foto do HML com a de producao.
PACOTE_PADROES = (
    "cronograma/*.json",
    "cronograma/manuais/*.json",
    "cronograma/fichas/*.json",
    "cra/**/*",
    "curvas/*",
    "custo_de_carrego.json",
)
PACOTE_FORA = ("cronograma/bullets.json",)


@dataclass
class RelatorioPacote:
    copiados: list[Path] = field(default_factory=list)
    iguais: list[Path] = field(default_factory=list)
    # Ja existem na config local com outro conteudo: so com `sobrescrever`.
    divergentes: list[Path] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)


def _raiz_do_pacote(pacote: Path) -> Path:
    """Aceita a pasta do pacote ou a config/ dele."""
    pacote = Path(pacote)
    raiz = pacote / "config" if (pacote / "config").is_dir() else pacote
    if not (raiz / "cronograma").is_dir():
        raise FileNotFoundError(f"{pacote} nao parece o pacote do gestor (sem config/cronograma).")
    return raiz


def _mesclar_bullets(origem: Path, destino: Path, aplicar: bool, sobrescrever: bool) -> list[str]:
    """Traz so as DECISOES da fila de bullets (confirmado / planilha_pendente).

    O resto do registro (estoque, pendente) e estado da rodada do gestor e se
    refaz sozinho aqui. Decisao local ja tomada vence, salvo `sobrescrever`.
    """
    from . import bullets as bl

    decididos = (bl.CONFIRMADO, bl.PLANILHA_PENDENTE)
    deles = bl.carregar(origem)["operacoes"]
    registro = bl.carregar(destino)
    trazidas = []
    for codigo, op in sorted(deles.items()):
        if op.get("estado") not in decididos:
            continue
        atual = registro["operacoes"].get(codigo)
        if atual and atual.get("estado") in decididos and not sobrescrever:
            continue
        registro["operacoes"][codigo] = {**(atual or {}), **op}
        trazidas.append(codigo)
    if aplicar and trazidas:
        bl.salvar(registro, destino)
    return trazidas


def importar_pacote(pacote: Path, raiz: Path = DIR_CONFIG, aplicar: bool = False,
                    sobrescrever: bool = False) -> RelatorioPacote:
    """Copia para a config local o que o pacote do gestor tem e o banco nao da.

    Nada de conexao: e so arquivo. Arquivo que ja existe aqui com conteudo
    diferente e mantido (pode ser uma reimportacao mais nova da planilha) e
    aparece em `divergentes`; `sobrescrever` troca pelo do gestor.
    """
    origem = _raiz_do_pacote(pacote)
    raiz = Path(raiz)
    rel = RelatorioPacote()
    vistos: set[Path] = set()
    for padrao in PACOTE_PADROES:
        for src in sorted(origem.glob(padrao)):
            rel_path = src.relative_to(origem)
            if (not src.is_file() or rel_path in vistos or rel_path.as_posix() in PACOTE_FORA
                    or "processados" in rel_path.parts):
                continue
            vistos.add(rel_path)
            dst = raiz / rel_path
            if dst.exists() and dst.read_bytes() == src.read_bytes():
                rel.iguais.append(dst)
                continue
            if dst.exists() and not sobrescrever:
                rel.divergentes.append(dst)
                continue
            if aplicar:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
            rel.copiados.append(dst)
    bullets_origem = origem / "cronograma" / "bullets.json"
    if bullets_origem.exists():
        rel.bullets = _mesclar_bullets(bullets_origem, raiz / "cronograma" / "bullets.json",
                                       aplicar, sobrescrever)
    return rel


# --- consultas (banco) --------------------------------------------------------------

def consultar_tudo(data: str | None = None) -> dict[str, pd.DataFrame]:
    from . import db

    cotas = (db.consultar(SQL_COTAS_CARTEIRA_DATA, (data, *BASES_COTA)) if data
             else db.consultar(SQL_COTAS_CARTEIRA, BASES_COTA))
    return {
        "cadastro": db.consultar(SQL_CADASTRO, ("NORMAL",)),
        "fundos_carteira": db.consultar(SQL_FUNDOS_CARTEIRA),
        "depara": db.consultar(SQL_DEPARA_BEYOND),
        "feriados": db.consultar(SQL_FERIADOS),
        "cotas_carteira": cotas,
    }


def executar(dados: dict[str, pd.DataFrame], raiz: Path = DIR_CONFIG, parametros: Path = DIR_PARAMETROS,
             so_carteira: bool = True, aplicar: bool = False, hoje: dt.date | None = None) -> Relatorio:
    """Monta tudo; com aplicar=False so relata (nada gravado)."""
    raiz = Path(raiz)
    overrides = carregar_overrides(Path(parametros) / "fundos_overrides.csv")
    existentes = Cadastro(raiz / "fundos").carregar()
    carteira = {so_digitos(v) for v in dados["fundos_carteira"]["CPF_CNPJ"]} if so_carteira else None
    fichas, rel = montar_fichas(dados["cadastro"], carteira, dados["depara"], overrides, existentes, hoje)
    feriados = montar_feriados(dados["feriados"])
    rel.feriados = len(feriados)
    # Para o prazo das cotas vale o cadastro inteiro: o fundo investido quase
    # nunca esta entre os que tem carteira.
    todos = fichas if carteira is None else montar_fichas(
        dados["cadastro"], None, dados["depara"], overrides, None, hoje)[0]
    fixos = carregar_prazos_fixos(Path(parametros) / "prazos_fixos.csv")
    levantados = carregar_prazos_fixos(Path(parametros) / ARQ_PRAZOS_GESTOR.name)
    prazos = montar_prazos(dados["cotas_carteira"], todos, fixos,
                           carregar_cotas_depara(Path(parametros) / "cotas_depara.csv"),
                           levantados)
    rel.prazos = len(prazos.registros)
    rel.prazos_do_gestor = len(
        {r.codigo_ativo.strip().upper() for r in levantados if r.prazo is not None} - {""}
        - {r.codigo_ativo.strip().upper() for r in fixos})
    rel.prazos_sem_prazo = sum(1 for r in prazos.registros if r.prazo is None)
    if not aplicar:
        return rel
    cad = Cadastro(raiz / "fundos")
    for f in fichas:
        rel.arquivos.append(cad.gravar(f))
    rel.arquivos.append(gravar_feriados(feriados, raiz / "calendario" / "feriados.csv"))
    rel.arquivos.append(prazos.gravar(raiz / "prazos_ativos.csv"))
    rel.arquivos += copiar_parametros(Path(parametros), raiz)
    return rel
