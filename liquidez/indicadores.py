"""Indicadores de liquidez do relatorio -- bloco G/J/O do `Relatorio_Liquidez`.

Fontes (confirmadas nas formulas da planilha):
  - Ponta ativa (Caixa Potencial, PL): tabela por ativo da etapa 9.
  - Ponta passiva (cotistas, distribuidores, GDP): base `DB_Passivo`.
  - RCO (resgate maximo): base `MOV` (a aba `Resg` era uma dinamica do MOV).

O corte do Caixa Potencial usa o PRAZO DE COTIZACAO (cc), nao o PzResg --
decisao de projeto confirmada com a area de risco.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

ARQUIVO_CONFIG = Path("config") / "indicadores.json"

# Colunas do DB_Passivo usadas nos indicadores.
COL_SALDO = "vlSaldoTotal"
COL_DISTRIBUIDOR = "nmDistribuidor"

# Colunas do MOV usadas no RCO.
COL_TIPO_MOV = "tipo_movimentacao"
COL_VALOR_MOV = "valor_movimentacao"
COL_DATA_MOV = "data_movimentacao"


def carregar_config(caminho: Path = ARQUIVO_CONFIG) -> dict:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def _enquadrado(valor: float | None, limite: float) -> bool | None:
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    return valor > limite


def _razao(numerador: float, denominador: float) -> float | None:
    """Divisao protegida: devolve None quando o denominador e zero.

    No VBA isso virava #DIV/0! (ex.: fundo sem o distribuidor BTG/ATIVA).
    """
    if not denominador:
        return None
    return numerador / denominador


@dataclass
class Indicadores:
    fundo: str
    cnpj: str
    prazo_cotizacao: int

    # Ponta ativa
    pl_mil: float = 0.0
    caixa_potencial_mil: float = 0.0
    caixa_pl: float | None = None

    # Cotistas
    maior_cotista_mil: float = 0.0
    tres_maiores_cotistas_mil: float = 0.0
    n_cotistas: int = 0
    cx_cotista: float | None = None
    cx_cotista3: float | None = None
    enq_cotista: bool | None = None

    # GDP (Grau de Dispersao do Passivo)
    media_mil: float = 0.0
    desvio_mil: float = 0.0
    gdp_mil: float = 0.0
    cx_gdp: float | None = None
    enq_gdp: bool | None = None

    # Distribuidores
    maior_distribuidor_mil: float = 0.0
    tres_maiores_distribuidores_mil: float = 0.0
    cx_distribuidor: float | None = None
    cx_distribuidor3: float | None = None
    enq_distribuidor: bool | None = None

    # RCO (Resgate em Condicoes Ordinarias)
    resgate_max_mil: float = 0.0
    caixa_resgate: float | None = None
    enq_rco: bool | None = None

    # ANBIMA: lista de (horizonte_dias, percentil_95, enquadrado)
    anbima: list[tuple[int, float, bool]] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "anbima"}
        d["anbima"] = [
            {"horizonte_dias": h, "percentil_95": p, "enquadrado": e}
            for h, p, e in self.anbima
        ]
        return d


def caixa_potencial(relatorio: pd.DataFrame, prazo_cotizacao: int) -> float:
    """Soma o Valor Liquido dos ativos com Liq. Duration <= prazo de cotizacao, em R$ mil.

    Reproduz `O15 = SUMIFS(E; D <= O17)/1000`, com O17 = prazo de cotizacao.
    """
    if relatorio.empty:
        return 0.0
    dur = pd.to_numeric(relatorio["liq_duration"], errors="coerce")
    valor = pd.to_numeric(relatorio["valor_liquido"], errors="coerce").fillna(0.0)
    dentro = dur <= prazo_cotizacao
    return float(valor[dentro.fillna(False)].sum()) / 1000


def _saldos(db_passivo: pd.DataFrame) -> pd.Series:
    if db_passivo.empty or COL_SALDO not in db_passivo.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(db_passivo[COL_SALDO], errors="coerce").dropna()


def calcular(
    fundo,
    relatorio: pd.DataFrame,
    pl_mil: float,
    db_passivo: pd.DataFrame,
    mov: pd.DataFrame,
    prazo_cotizacao: int,
    config: dict | None = None,
) -> Indicadores:
    """Calcula todos os indicadores de liquidez do fundo."""
    config = config or carregar_config()
    limites = config["limites"]

    ind = Indicadores(
        fundo=fundo.nome_interno, cnpj=fundo.cnpj, prazo_cotizacao=prazo_cotizacao,
        pl_mil=float(pl_mil),
    )

    # --- Ponta ativa -----------------------------------------------------
    ind.caixa_potencial_mil = caixa_potencial(relatorio, prazo_cotizacao)
    ind.caixa_pl = _razao(ind.caixa_potencial_mil, ind.pl_mil)
    caixa = ind.caixa_potencial_mil

    # --- Cotistas (DB_Passivo!G) -----------------------------------------
    saldos = _saldos(db_passivo).sort_values(ascending=False)
    ind.n_cotistas = int(len(saldos))
    if not saldos.empty:
        ind.maior_cotista_mil = float(saldos.iloc[0]) / 1000
        ind.tres_maiores_cotistas_mil = float(saldos.iloc[:3].sum()) / 1000
        ind.media_mil = float(saldos.mean()) / 1000
        ind.desvio_mil = float(saldos.std(ddof=1)) / 1000 if len(saldos) > 1 else 0.0
    ind.cx_cotista = _razao(caixa, ind.maior_cotista_mil)
    ind.cx_cotista3 = _razao(caixa, ind.tres_maiores_cotistas_mil)
    ind.enq_cotista = _enquadrado(ind.cx_cotista, limites["cx_cotista"])

    # --- GDP = media + fator * desvio ------------------------------------
    ind.gdp_mil = ind.media_mil + config["fator_gdp"] * ind.desvio_mil
    ind.cx_gdp = _razao(caixa, ind.gdp_mil)
    ind.enq_gdp = _enquadrado(ind.cx_gdp, limites["cx_gdp"])

    # --- Distribuidores (DB_Passivo!F/G) ---------------------------------
    # A planilha somava so "BTG" e "ATIVA" (hardcoded). Aqui pegamos os
    # maiores distribuidores de fato -- mais geral e correto.
    if not db_passivo.empty and COL_DISTRIBUIDOR in db_passivo.columns:
        por_dist = (
            db_passivo.assign(_s=pd.to_numeric(db_passivo[COL_SALDO], errors="coerce"))
            .dropna(subset=["_s"])
            .groupby(db_passivo[COL_DISTRIBUIDOR].fillna("").astype(str).str.strip())["_s"]
            .sum()
            .sort_values(ascending=False)
        )
        por_dist = por_dist[por_dist.index != ""]
        if not por_dist.empty:
            ind.maior_distribuidor_mil = float(por_dist.iloc[0]) / 1000
            ind.tres_maiores_distribuidores_mil = float(por_dist.iloc[:3].sum()) / 1000
    ind.cx_distribuidor = _razao(caixa, ind.maior_distribuidor_mil)
    ind.cx_distribuidor3 = _razao(caixa, ind.tres_maiores_distribuidores_mil)
    ind.enq_distribuidor = _enquadrado(ind.cx_distribuidor, limites["cx_distribuidor"])

    # --- RCO: maior resgate diario (MOV) ---------------------------------
    ind.resgate_max_mil = resgate_maximo(mov, config) / 1000
    ind.caixa_resgate = _razao(caixa, ind.resgate_max_mil)
    ind.enq_rco = _enquadrado(ind.caixa_resgate, limites["caixa_resgate"])

    # --- ANBIMA: Caixa/PL vs percentil 95% por horizonte -----------------
    horizontes = config["anbima_horizontes_dias"]
    percentis = config["anbima_percentil_95"]
    if ind.caixa_pl is not None:
        ind.anbima = [
            (int(h), float(p), ind.caixa_pl > p)
            for h, p in zip(horizontes, percentis)
        ]

    return ind


def resgate_maximo(mov: pd.DataFrame, config: dict | None = None) -> float:
    """Maior resgate em um unico dia, em reais (base para o RCO)."""
    config = config or carregar_config()
    if mov.empty or COL_VALOR_MOV not in mov.columns:
        return 0.0
    tipos = {t.upper() for t in config["resgate"]["tipos"]}
    tipo = mov[COL_TIPO_MOV].fillna("").astype(str).str.strip().str.upper()
    valor = pd.to_numeric(mov[COL_VALOR_MOV], errors="coerce").fillna(0.0)
    resgates = mov[tipo.isin(tipos)].assign(_v=valor[tipo.isin(tipos)])
    if resgates.empty:
        return 0.0
    por_dia = resgates.groupby(resgates[COL_DATA_MOV].astype(str))["_v"].sum()
    return float(por_dia.max())
