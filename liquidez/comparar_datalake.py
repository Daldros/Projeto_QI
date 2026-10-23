"""Confere as candidatas do datalake contra o que a liquidez le hoje.

`python -m liquidez datalake comparar --data AAAA-MM-DD`. So SELECT e so
agregados: nenhum nome ou documento de cotista ou de sacado sai na tela, so
fundo, classe, contagens, somas e datas.

  1. estoque: HOD_TB_ESTOQUE_UNIFICADO (riscogc) x MUL_GLD_ESTOQUE (datalake,
     a fonte nova), fundo a fundo do cadastro local;
  2. passivo: classes que o filtro do cadastro de fundos (Situacao NORMAL e
     nmCotaInterno <> 'ORIGINAL') tira da consulta, e quanto saldo fica fora;
  3. cotas: DLK_DEPARA_FUNDOS (CNPJ e cota) x cadastro de fundos, e os fundos
     do cadastro local com e sem serie na MEC;
  4. feriados: AUD_DIM_FERIADOS x DLK_CALENDARIO_B3;
  5. CDI: a forma da CDH_GLD_INDICES_MERCADO e o CDI dela x TB_TAXAS;
  6. movimentacao: o que o RCO usa hoje (HOD) e as fontes por administrador
     que o datalake tem (nenhuma MUL).

Cada parte que falhar vira uma linha "(falhou: ...)" e a comparacao segue.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Iterable
from pathlib import Path

import pandas as pd

from . import db, queries
from .mapa_datalake import (SQL_CADASTRO_ADM, _digitos, _int, _nulo, _primeira_linha, _q,
                            LIMITE_VARREDURA, escolher_data)
from .tabelas import DEPARA_FUNDOS, ESTOQUE, PASSIVO, no_datalake

log = logging.getLogger(__name__)

CALENDARIO = no_datalake("DLK_CALENDARIO_B3")
INDICES = "CDH_GLD_INDICES_MERCADO"
MOV_SINGULARE = no_datalake("QIT_GLD_MOVIMENTACAO_FUNDOS_SINGULARE")
OUTRAS_MOV = ("QTK_GLD_MOVIMENTACAO_FUNDOS_QITECH", "PLA_GLD_MOV_ANALITICO_FUNDO_JCOT",
              "BTG_GLD_RTA_MOVIMENTACOES_BTG", "PLA_GLD_MOVIMENTACAO_MINICOM")
# Coluna que diz o tipo do movimento, na ordem em que se procura.
COLUNAS_TIPO_MOV = ("dsDescricaoMovimento", "dsTipoOperacao", "dsDescricaoTipo", "dsTipoEvento",
                    "dsTipo", "dsReferencia", "dsTipoMovimento")
MAX_LINHAS = 40


def _mil(v) -> str:
    return f"{(0.0 if _nulo(v) else float(v)) / 1000:,.2f}"


def _valor(df: pd.DataFrame, coluna: str) -> pd.Series:
    if coluna not in df:
        return pd.Series(0.0, index=df.index)
    return pd.to_numeric(df[coluna], errors="coerce").fillna(0.0)


def _texto(df: pd.DataFrame, coluna: str) -> pd.Series:
    if coluna not in df:
        return pd.Series("", index=df.index)
    return df[coluna].fillna("").astype(str).str.strip()


# --- 1. estoque ---------------------------------------------------------------

SQL_ESTOQUE_BRUTO = f"""
SELECT COUNT(*) AS linhas,
       COUNT(DISTINCT CONCAT(e.dsSeuNumero, '_', e.dsNuDocumento)) AS chaves,
       SUM(CASE WHEN e.dtFundo <> e.dtReferencia THEN 1 ELSE 0 END) AS dt_fundo_diferente
FROM {ESTOQUE} AS e
WHERE e.dtReferencia = ? AND e.noDocFundo IN (?, ?)
"""


def _por(df: pd.DataFrame, coluna: str) -> str:
    """'A=n (R$ mil v), B=...' por valor da coluna, maiores primeiro."""
    if df.empty or coluna not in df:
        return "-"
    g = (df.assign(_v=_valor(df, "Valor_Presente"), _k=_texto(df, coluna).replace("", "(vazio)"))
           .groupby("_k")["_v"].agg(["size", "sum"]).sort_values("sum", ascending=False))
    return ", ".join(f"{k}={int(n):,} (R$ mil {_mil(v)})" for k, (n, v) in g.iterrows())


def comparar_estoque_fundo(nome: str, cnpj: str, data, hod: pd.DataFrame, mul: pd.DataFrame,
                           bruto: dict) -> list[str]:
    """Bloco de um fundo: as duas fontes lado a lado, so com agregados."""
    def vp_por_id(df):
        if df.empty or "ID_Recebivel" not in df:
            return pd.Series(dtype=float)
        return _valor(df, "Valor_Presente").groupby(_texto(df, "ID_Recebivel")).sum()

    def status(df):
        s = _texto(df, "Status_Recebivel").str.upper()
        v = _valor(df, "Valor_Presente")
        return {k: float(v[s == k].sum()) for k in ("VENCIDO", "A VENCER")} | {
            "outro": float(v[~s.isin(["VENCIDO", "A VENCER"])].sum())}

    h, m = vp_por_id(hod), vp_por_id(mul)
    comuns = h.index.intersection(m.index)
    so_h, so_m = h.index.difference(m.index), m.index.difference(h.index)
    sh, sm = status(hod), status(mul)
    linhas = [f"{nome} ({cnpj}) em {data}",
              f"   {'':<24}{'HOD (riscogc)':>20}{'MUL (datalake)':>20}",
              f"   {'linhas':<24}{len(hod):>20,}{len(mul):>20,}",
              f"   {'valor presente R$ mil':<24}{_mil(_valor(hod, 'Valor_Presente').sum()):>20}"
              f"{_mil(_valor(mul, 'Valor_Presente').sum()):>20}"]
    for k in ("VENCIDO", "A VENCER", "outro"):
        linhas.append(f"   {'  ' + k:<24}{_mil(sh[k]):>20}{_mil(sm[k]):>20}")
    dif = float((h[comuns] - m[comuns]).abs().sum()) if len(comuns) else 0.0
    linhas.append(f"   recebiveis: nos dois {len(comuns):,} (diferenca de VP R$ mil {_mil(dif)});"
                  f" so na HOD {len(so_h):,} (R$ mil {_mil(h[so_h].sum())});"
                  f" so na MUL {len(so_m):,} (R$ mil {_mil(m[so_m].sum())})")
    linhas.append(f"   status HOD: {_por(hod, 'Status_Recebivel')}")
    linhas.append(f"   status MUL: {_por(mul, 'Status_Recebivel')}")
    linhas.append(f"   HOD por origem: {_por(hod, 'Origem_Importacao')}")
    linhas.append(f"   MUL por sistema: {_por(mul, 'Sistema_Origem')}")
    linhas.append(f"   MUL bruta: {_int(bruto.get('linhas')):,} linha(s), {_int(bruto.get('chaves')):,} chave(s)"
                  f" ({_int(bruto.get('linhas')) - _int(bruto.get('chaves')):,} repetida(s), fica a mais recente);"
                  f" dtFundo diferente de dtReferencia em {_int(bruto.get('dt_fundo_diferente')):,}")
    return linhas


def parte_estoque(consultar: Callable, fichas: list, data) -> list[str]:
    if not fichas:
        return ["(cadastro local vazio: rode `configurar --aplicar` para ter as fichas)"]
    linhas: list[str] = []
    for f in fichas:
        try:
            hod = consultar(*queries.estoque_unificado_hod(f.cnpj, data))
            mul = consultar(*queries.estoque_unificado(f.cnpj, data))
            b = consultar(SQL_ESTOQUE_BRUTO, (str(data), *queries.cnpj_nos_dois_formatos(f.cnpj)))
            linhas += comparar_estoque_fundo(f.nome_interno, f.cnpj, data, hod, mul,
                                             b.iloc[0].to_dict() if not b.empty else {})
        except Exception as exc:  # noqa: BLE001 - um fundo nao derruba a parte
            linhas.append(f"{f.nome_interno} ({f.cnpj}): (falhou: {_primeira_linha(exc)})")
    return linhas


# --- 2. passivo x filtro do cadastro ----------------------------------------------

SQL_PASSIVO_POR_CLASSE = f"""
SELECT x.cdFundo, x.administrador, MAX(x.docFundo) AS docFundo, MAX(x.dsNomeFundo) AS dsNomeFundo,
       MAX(x.dsClasse) AS dsClasse, MAX(x.dtReferencia) AS data, COUNT(*) AS notas,
       SUM(x.vlCorrigido) AS saldo
FROM (SELECT LTRIM(RTRIM(m.cdFundo)) AS cdFundo, UPPER(LTRIM(RTRIM(m.dsAdministrador))) AS administrador,
             m.docFundo, m.dsNomeFundo, m.dsClasse, m.dtReferencia, m.vlCorrigido,
             ROW_NUMBER() OVER (PARTITION BY UPPER(LTRIM(RTRIM(m.dsAdministrador))), LTRIM(RTRIM(m.cdFundo)),
                                LTRIM(RTRIM(m.cdCotista)), LTRIM(RTRIM(m.nrNota))
                                ORDER BY m.dhAlteracao DESC, m.id DESC) AS rn
      FROM {PASSIVO} AS m
      WHERE m.dtReferencia = (SELECT MAX(dtReferencia) FROM {PASSIVO} WHERE dtReferencia <= ?)
        AND m.cdFundo IS NOT NULL AND m.cdCotista IS NOT NULL AND m.nrNota IS NOT NULL) AS x
WHERE x.rn = 1
GROUP BY x.cdFundo, x.administrador
"""


def situacao_no_cadastro(linhas_cadastro: pd.DataFrame) -> str:
    """Se a classe entra na consulta do passivo, pelo filtro de hoje, e por que nao."""
    if linhas_cadastro.empty:
        return "sem cadastro"
    situacao = _texto(linhas_cadastro, "Situacao").str.upper()
    cota = linhas_cadastro["nmCotaInterno"]
    rotulo_ok = cota.notna() & (cota.astype(str).str.strip().str.upper() != "ORIGINAL")
    if ((situacao == "NORMAL") & rotulo_ok).any():
        return "entra"
    if (situacao == "NORMAL").any():
        return "rotulo ORIGINAL"
    return "situacao " + "/".join(sorted(set(situacao) - {""})) if (situacao != "").any() else "situacao vazia"


def parte_passivo(consultar: Callable, fichas: list, data) -> list[str]:
    from .queries import PASSIVO_ADMINISTRADORES

    p = consultar(SQL_PASSIVO_POR_CLASSE, (str(data),))
    if p.empty:
        return ["(passivo vazio ate a data)"]
    cad = consultar(SQL_CADASTRO_ADM)
    cad["idFundoAdm"] = _texto(cad, "idFundoAdm")
    p = p[p["administrador"].isin(PASSIVO_ADMINISTRADORES)].copy()
    p["cdFundo"] = _texto(p, "cdFundo")
    p["motivo"] = [situacao_no_cadastro(cad[cad["idFundoAdm"] == c]) for c in p["cdFundo"]]
    p["saldo_v"] = _valor(p, "saldo")
    nossos = {f.cnpj_numerico for f in fichas}
    p["ficha"] = p["docFundo"].map(_digitos).isin(nossos)

    resumo = p.groupby("motivo")["saldo_v"].agg(["size", "sum"]).sort_values("sum", ascending=False)
    linhas = [f"Passivo de {str(p['data'].max())[:10]}, {len(p)} classe(s) de "
              f"{'/'.join(PASSIVO_ADMINISTRADORES)}: o que o filtro do cadastro faz com elas (R$ mil):"]
    for motivo, (n, v) in resumo.iterrows():
        linhas.append(f"   {motivo:<28} {int(n):>4} classe(s)  {_mil(v):>18}")
    fora = p[p["motivo"] != "entra"].sort_values("saldo_v", ascending=False)
    linhas.append(f"Fora da consulta ({len(fora)}; * = fundo do cadastro local):")
    for _, r in fora.head(MAX_LINHAS).iterrows():
        linhas.append(f"   {'*' if r['ficha'] else ' '} cdFundo {r['cdFundo']:<10} {str(r['dsNomeFundo'])[:34]:<34}"
                      f" {str(r['dsClasse'])[:18]:<18} {_mil(r['saldo_v']):>16}  {r['motivo']}")
    if len(fora) > MAX_LINHAS:
        linhas.append(f"   (+{len(fora) - MAX_LINHAS} classe(s))")
    return linhas


# --- 3. cotas: de-para do datalake x cadastro ---------------------------------------

SQL_DEPARA = (f"SELECT cdCarteira, noCnpjDigitos, dsNomeFundo, dsCota, dsAdministrador, dsSituacao,"
              f" cdFundoInterno FROM {DEPARA_FUNDOS}")


def parte_cotas(consultar: Callable, fichas: list, data) -> list[str]:
    from . import varcota as vc

    dep = consultar(SQL_DEPARA)
    cad = consultar(vc.CONSULTA_CADASTRO)
    cad["idFundoInterno"] = _texto(cad, "idFundoInterno")
    rotulos = dict(zip(cad["idFundoInterno"], _texto(cad, "nmCotaInterno").str.upper()))
    cnpj_cad = {i: _digitos(c) for i, c in zip(cad["idFundoInterno"], cad["CNPJFundo"]) if _digitos(c)}

    dep["id"] = _texto(dep, "cdFundoInterno")
    dep["cnpj_dlk"] = dep["noCnpjDigitos"].map(_digitos)
    com_id = dep[dep["id"] != ""].copy()
    com_id["cnpj_cad"] = [cnpj_cad.get(i) or cnpj_cad.get(vc.id_base(i)) for i in com_id["id"]]
    sem_cad = com_id["cnpj_cad"].isna()
    igual = (~sem_cad) & (com_id["cnpj_cad"] == com_id["cnpj_dlk"])
    linhas = [f"DLK_DEPARA_FUNDOS: {len(dep)} linha(s), {len(com_id)} com cdFundoInterno,"
              f" {int((dep['cnpj_dlk'] != '').sum())} com CNPJ.",
              f"CNPJ do de-para x cadastro: igual em {int(igual.sum())}, diferente em"
              f" {int((~sem_cad & ~igual).sum())}, sem linha no cadastro em {int(sem_cad.sum())}.",
              "dsSituacao: " + ", ".join(f"{k or '(vazio)'}={n}" for k, n in
                                         _texto(dep, "dsSituacao").value_counts().items())]
    com_id["classe_cad"] = [vc._classe_da_base(i, rotulos) for i in com_id["id"]]
    com_id["cota_dlk"] = _texto(com_id, "dsCota").str.upper().replace("", "(vazio)")
    pares = com_id.groupby(["classe_cad", "cota_dlk"]).size().sort_values(ascending=False)
    linhas.append(f"Classe pelo cadastro (como a serie usa hoje) x dsCota do de-para ({len(pares)} par(es)):")
    for (classe, cota), n in list(pares.items())[:MAX_LINHAS]:
        linhas.append(f"   {str(classe)[:28]:<28} {str(cota)[:40]:<40} {n:>4}")
    if len(pares) > MAX_LINHAS:
        linhas.append(f"   (+{len(pares) - MAX_LINHAS} par(es))")

    if not fichas:
        linhas.append("(cadastro local vazio: sem cobertura por fundo)")
        return linhas
    serie = vc.extrair(data)
    com_serie = set(serie["cnpj_fundo"]) if not serie.empty else set()
    ganha = set(com_id.loc[sem_cad, "cnpj_dlk"])
    linhas.append(f"Fundos do cadastro local na serie da MEC ate {data}:")
    for f in sorted(fichas, key=lambda f: f.nome_interno):
        if f.cnpj_numerico in com_serie:
            s = serie[serie["cnpj_fundo"] == f.cnpj_numerico]
            estado = (f"COM serie, {s['data'].nunique()} dia(s) de {s['data'].min():%d/%m/%Y} a"
                      f" {s['data'].max():%d/%m/%Y}, classes {', '.join(sorted(s['classe'].astype(str).unique()))}")
        elif f.cnpj_numerico in ganha:
            estado = "SEM serie hoje; entra se o CNPJ vier do de-para do datalake"
        else:
            estado = "SEM serie (a MEC nao tem a carteira, ou falta de-para)"
        linhas.append(f"   {f.nome_interno[:40]:<40} {f.cnpj}  {estado}")
    return linhas


# --- 4. feriados -----------------------------------------------------------------

SQL_FERIADOS_AUD = "SELECT dtFeriado, dsFeriado FROM dbo.AUD_DIM_FERIADOS"
SQL_CALENDARIO = (f"SELECT dtData, dsFeriado, flDiaUtil, flDiaUtilB3 FROM {CALENDARIO}"
                  " WHERE dtData BETWEEN ? AND ?")
FAIXA_FERIADOS = ("2024-01-01", "2030-12-31")


def _zero(serie: pd.Series) -> pd.Series:
    return serie.astype(str).str.strip().str.lower().isin(["0", "false"])


def parte_feriados(consultar: Callable) -> list[str]:
    de, ate = (pd.Timestamp(d) for d in FAIXA_FERIADOS)
    aud = consultar(SQL_FERIADOS_AUD)
    aud["d"] = pd.to_datetime(aud["dtFeriado"], errors="coerce")
    aud = aud[aud["d"].between(de, ate) & (aud["d"].dt.day_of_week < 5)]
    cal = consultar(SQL_CALENDARIO, FAIXA_FERIADOS)
    cal["d"] = pd.to_datetime(cal["dtData"], errors="coerce")
    cal = cal[cal["d"].dt.day_of_week < 5]
    nomes = dict(zip(cal["d"], _texto(cal, "dsFeriado"))) | dict(zip(aud["d"], _texto(aud, "dsFeriado")))
    a = set(aud["d"])
    linhas = [f"Dias de semana sem expediente de {FAIXA_FERIADOS[0][:4]} a {FAIXA_FERIADOS[1][:4]}:"
              f" AUD_DIM_FERIADOS {len(a)}"]
    for rotulo, coluna in (("flDiaUtil = 0", "flDiaUtil"), ("flDiaUtilB3 = 0", "flDiaUtilB3")):
        c = set(cal.loc[_zero(cal[coluna]), "d"])
        linhas.append(f"   DLK_CALENDARIO_B3 com {rotulo}: {len(c)};"
                      f" so na AUD {len(a - c)}, so no calendario {len(c - a)}")
        for titulo, datas in (("so na AUD", a - c), ("so no calendario", c - a)):
            for d in sorted(datas)[:15]:
                linhas.append(f"      {titulo:<16} {d:%d/%m/%Y} {nomes.get(d, '')[:40]}")
    return linhas


# --- 5. CDI ------------------------------------------------------------------------

SQL_COLUNAS_DE = """
SELECT c.name AS coluna, t.name AS tipo
FROM {dl}.sys.columns AS c
JOIN {dl}.sys.objects AS o ON o.object_id = c.object_id
JOIN {dl}.sys.types AS t ON t.user_type_id = c.user_type_id
WHERE o.name = ? ORDER BY c.column_id
"""
SQL_LINHAS_DE = """
SELECT SUM(p.rows) AS linhas
FROM {dl}.sys.partitions AS p JOIN {dl}.sys.objects AS o ON o.object_id = p.object_id
WHERE o.name = ? AND p.index_id IN (0, 1)
"""
SQL_CDI_RISCOGC = "SELECT TOP 5 Data, Valor FROM dbo.TB_TAXAS WHERE Ativo = 'CDI_Over' ORDER BY Data DESC"


def _forma(consultar: Callable, nome: str) -> tuple[pd.DataFrame, int]:
    from .tabelas import BANCO_DATALAKE as dl

    cols = consultar(SQL_COLUNAS_DE.format(dl=dl), (nome,))
    n = consultar(SQL_LINHAS_DE.format(dl=dl), (nome,))
    return cols, _int(n["linhas"].iloc[0]) if not n.empty else 0


def parte_cdi(consultar: Callable) -> list[str]:
    cols, n = _forma(consultar, INDICES)
    if cols.empty:
        return [f"({INDICES} nao visivel)"]
    tabela = no_datalake(INDICES)
    linhas = [f"{INDICES}: {n:,} linha(s); colunas: " + ", ".join(f"{c} {t}" for c, t in cols.itertuples(index=False))]
    data = escolher_data(cols)
    if "cdIndice" not in set(cols["coluna"]) or data is None:
        return linhas + ["(sem cdIndice ou coluna de data: nada a agrupar)"]
    if n > LIMITE_VARREDURA:
        return linhas + [f"(mais de {LIMITE_VARREDURA:,} linhas: agrupamento pulado)"]
    g = consultar(f"SELECT cdIndice, COUNT(*) AS linhas, MIN({_q(data)}) AS de, MAX({_q(data)}) AS ate"
                  f" FROM {tabela} WITH (NOLOCK) GROUP BY cdIndice ORDER BY cdIndice")
    linhas.append(f"cdIndice ({len(g)}): " + "; ".join(
        f"{r.cdIndice} {int(r.linhas):,} de {str(r.de)[:10]} a {str(r.ate)[:10]}" for r in g.itertuples()))
    for indice in [i for i in g["cdIndice"].astype(str) if "CDI" in i.upper()][:3]:
        u = consultar(f"SELECT TOP 5 * FROM {tabela} WITH (NOLOCK) WHERE cdIndice = ?"
                      f" ORDER BY {_q(data)} DESC", (indice,))
        linhas.append(f"Ultimas linhas de {indice}:")
        linhas += ["   " + " | ".join(f"{k}={v}" for k, v in r.items()) for r in u.to_dict("records")]
    r = consultar(SQL_CDI_RISCOGC)
    linhas.append("TB_TAXAS CDI_Over (riscogc), ultimas: " + "; ".join(
        f"{str(x.Data)[:10]} {x.Valor}" for x in r.itertuples()))
    return linhas


# --- 6. movimentacao -------------------------------------------------------------------

SQL_PASSIVO_MESES = (f"SELECT CONVERT(CHAR(7), dtReferencia, 126) AS mes, COUNT(DISTINCT dtReferencia) AS datas"
                     f" FROM {PASSIVO} GROUP BY CONVERT(CHAR(7), dtReferencia, 126) ORDER BY mes")
SQL_MOV_SINGULARE_TIPOS = (f"SELECT dsDescricaoMovimento AS tipo, COUNT(*) AS n, SUM(vlBruto) AS bruto,"
                           f" MIN(dtMovimento) AS de, MAX(dtMovimento) AS ate FROM {MOV_SINGULARE}"
                           f" GROUP BY dsDescricaoMovimento ORDER BY n DESC")
SQL_MOV_SINGULARE_DIA = f"""
SELECT REPLACE(REPLACE(REPLACE(dsDocumentoFundo, '.', ''), '/', ''), '-', '') AS cnpj,
       dtMovimento AS dia, dsDescricaoMovimento AS tipo, SUM(vlBruto) AS bruto,
       SUM(vlBrutoResgate) AS bruto_resgate, COUNT(*) AS n
FROM {MOV_SINGULARE}
GROUP BY REPLACE(REPLACE(REPLACE(dsDocumentoFundo, '.', ''), '/', ''), '-', ''), dtMovimento, dsDescricaoMovimento
"""


def _maior_dia(df: pd.DataFrame, dia: str, valor: str) -> tuple[float, str]:
    if df.empty:
        return 0.0, "-"
    por_dia = _valor(df, valor).groupby(df[dia].astype(str).str[:10]).sum()
    return float(por_dia.max()), str(por_dia.idxmax())


def _tipos_de_resgate() -> set[str]:
    """Os tipos que o RCO soma: da config local ou, sem ela, da copia versionada."""
    from .indicadores import ARQUIVO_CONFIG, carregar_config

    for caminho in (ARQUIVO_CONFIG, Path("config") / "parametros" / "indicadores.json"):
        if Path(caminho).exists():
            return {t.upper() for t in carregar_config(caminho)["resgate"]["tipos"]}
    return set()


def parte_movimentacao(consultar: Callable, fichas: list) -> list[str]:
    from .indicadores import COL_DATA_MOV, COL_TIPO_MOV, COL_VALOR_MOV

    tipos = _tipos_de_resgate()
    linhas = ["Passivo do datalake, datas por mes (a movimentacao da HOD e calculada dele):"]
    meses = consultar(SQL_PASSIVO_MESES)
    linhas.append("   " + ", ".join(f"{r.mes} {int(r.datas)}" for r in meses.itertuples()))

    t = consultar(SQL_MOV_SINGULARE_TIPOS)
    linhas.append(f"QIT_GLD_MOVIMENTACAO_FUNDOS_SINGULARE por tipo ({len(t)}):")
    for r in t.head(MAX_LINHAS).itertuples():
        linhas.append(f"   {str(r.tipo)[:40]:<40} {int(r.n):>7,}  R$ mil {_mil(r.bruto):>16}"
                      f"  {str(r.de)[:10]} a {str(r.ate)[:10]}")
    dia = consultar(SQL_MOV_SINGULARE_DIA)
    dia["cnpj"] = dia["cnpj"].map(_digitos)
    resgate = dia[_texto(dia, "tipo").str.upper().str.contains("RESG")]

    linhas.append("RCO por fundo do cadastro local: maior resgate de um dia, R$ mil (data). Na Singulare,"
                  " pelo vlBruto e pelo vlBrutoResgate das linhas com RESG no tipo:")
    linhas.append(f"   {'fundo':<40} {'HOD (hoje)':>30} {'Singulare vlBruto':>30} {'Singulare vlBrutoResgate':>30}")
    for f in sorted(fichas, key=lambda f: f.nome_interno):
        try:
            mov = consultar(*queries.movimentacao(f.nome_produto_mov))
            eh = _texto(mov, COL_TIPO_MOV).str.upper().isin(tipos)
            v_hod, d_hod = _maior_dia(mov[eh], COL_DATA_MOV, COL_VALOR_MOV)
            do_fundo = resgate[resgate["cnpj"] == f.cnpj_numerico]
            v_b, d_b = _maior_dia(do_fundo, "dia", "bruto")
            v_r, d_r = _maior_dia(do_fundo, "dia", "bruto_resgate")
            linhas.append(f"   {f.nome_interno[:40]:<40} {_mil(v_hod):>18} ({d_hod:<10})"
                          f" {_mil(v_b):>18} ({d_b:<10}) {_mil(v_r):>18} ({d_r:<10})")
        except Exception as exc:  # noqa: BLE001
            linhas.append(f"   {f.nome_interno[:40]:<40} (falhou: {_primeira_linha(exc)})")

    linhas.append("Outras fontes de movimentacao por administrador:")
    for nome in OUTRAS_MOV:
        try:
            cols, n = _forma(consultar, nome)
            if cols.empty:
                linhas.append(f"   {nome}: (nao visivel)")
                continue
            data = escolher_data(cols)
            tipo = next((c for c in COLUNAS_TIPO_MOV if c in set(cols["coluna"])), None)
            tabela = no_datalake(nome)
            texto = f"   {nome}: {n:,} linha(s)"
            if data:
                r = consultar(f"SELECT MIN({_q(data)}) AS de, MAX({_q(data)}) AS ate FROM {tabela} WITH (NOLOCK)").iloc[0]
                texto += f", {data} de {str(r['de'])[:10]} a {str(r['ate'])[:10]}"
            linhas.append(texto)
            if tipo and n <= LIMITE_VARREDURA:
                g = consultar(f"SELECT TOP 10 {_q(tipo)} AS tipo, COUNT(*) AS n FROM {tabela} WITH (NOLOCK)"
                              f" GROUP BY {_q(tipo)} ORDER BY n DESC")
                linhas.append(f"      {tipo}: " + ", ".join(f"{r.tipo}={int(r.n):,}" for r in g.itertuples()))
        except Exception as exc:  # noqa: BLE001
            linhas.append(f"   {nome}: (falhou: {_primeira_linha(exc)})")
    return linhas


# --- execucao ---------------------------------------------------------------------

def comparar(fichas: Iterable = (), data=None) -> list[str]:
    """Roda as seis partes e devolve o texto, linha a linha."""
    consultar = db.consultar
    data = data or dt.date.today().isoformat()
    fichas = sorted(fichas, key=lambda f: f.nome_interno)
    saida: list[str] = []

    def parte(numero: int, titulo: str, funcao: Callable[[], list[str]]) -> None:
        saida.append("")
        saida.append(f"== {numero}. {titulo}")
        try:
            saida.extend(funcao())
        except Exception as exc:  # noqa: BLE001 - uma parte nao derruba a comparacao
            log.debug("parte %d falhou", numero, exc_info=True)
            saida.append(f"(falhou: {_primeira_linha(exc)})")

    parte(1, f"estoque em {data}: HOD (riscogc) x MUL_GLD_ESTOQUE (datalake)",
          lambda: parte_estoque(consultar, fichas, data))
    parte(2, "passivo: classes que o filtro do cadastro de fundos tira da consulta",
          lambda: parte_passivo(consultar, fichas, data))
    parte(3, "cotas: DLK_DEPARA_FUNDOS x cadastro de fundos",
          lambda: parte_cotas(consultar, fichas, data))
    parte(4, "feriados: AUD_DIM_FERIADOS x DLK_CALENDARIO_B3", lambda: parte_feriados(consultar))
    parte(5, f"CDI: {INDICES} x TB_TAXAS", lambda: parte_cdi(consultar))
    parte(6, "movimentacao: HOD de hoje x fontes do datalake", lambda: parte_movimentacao(consultar, fichas))
    return saida
