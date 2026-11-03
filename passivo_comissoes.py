"""Base de passivo do mes para a apuracao de comissoes: posicao diaria por cotista.

    python passivo_comissoes.py --mes AAAA-MM [--todos] [--fundos CSV] [--saida XLSX]

Mesma fonte e mesmas regras do grafo do passivo (`grafo_passivo.py`): a
MUL_GLD_PASSIVO_CONSOLIDADO do datalake, uma linha por nota (vence a
alteracao mais recente), so SINGULARE e PLANNER, classe ligada ao fundo pelo
cadastro do riscogc e distribuidor pelo documento do cotista. A diferenca e o
recorte: em vez de uma data, TODAS as datas do mes que tem posicao, e so as
classes pedidas para a comissao (`config/parametros/comissoes_fundos.csv`,
a lista do e-mail do RI de 03/11/2026). `--todos` troca a lista por todas as
classes do cadastro.

Saida: uma planilha .xlsx (padrao `saidas/passivo_comissoes_AAAA-MM.xlsx`,
fora do git) com CPF/CNPJ e nome de cotista -- uso interno da apuracao. O que
vai para a tela e so a conferencia (fundo, classe, datas e totais), sem
cotista, e pode ser colado de volta.

Saldo = soma do vlCorrigido das notas do cotista na classe, na data: e o "PL
da posicao" que o sistema da gestora usava. O saldo medio do mes divide a soma
das posicoes diarias pelo numero de datas em que a CLASSE tem base -- o
cotista que entrou no meio do mes conta zero nos dias anteriores.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI))

from liquidez.queries import (  # noqa: E402
    PASSIVO_ADMINISTRADORES, PASSIVO_DISTRIBUIDORES, PASSIVO_JOIN_DISTRIBUIDOR,
    PASSIVO_JOIN_FUNDO, PASSIVO_NOTAS,
)
from liquidez.tabelas import PASSIVO  # noqa: E402

ALVOS_PADRAO = AQUI / "config" / "parametros" / "comissoes_fundos.csv"
LIMITE_EXCEL = 1_048_000          # linhas por aba (o Excel para em 1.048.576)

SQL_CADASTRO = (
    "SELECT idFundoInterno, idFundoAdm, CNPJFundo, nmFundoInterno, nmCotaInterno"
    " FROM dbo.HOD_TB_CADASTRO_FUNDOS"
    " WHERE Situacao = 'NORMAL' AND nmCotaInterno <> 'ORIGINAL'"
)

SQL_DATAS = (
    f"SELECT DISTINCT CAST(dtReferencia AS DATE) AS data FROM {PASSIVO}"
    " WHERE dtReferencia >= ? AND dtReferencia < ?"
    " AND UPPER(LTRIM(RTRIM(dsAdministrador))) IN ("
    + ", ".join(f"'{a}'" for a in PASSIVO_ADMINISTRADORES) + ")"
    " ORDER BY data"
)

# Uma data por consulta: e a base de notas do grafo, sem mexer nela. O filtro
# de classe entra depois do join, por idFundoInterno.
SQL_POSICOES = f"""
SELECT p.dataPosicao AS data, p.administrador, p.cdClasseAdm AS cd_classe_adm,
       f.idFundoInterno AS id_classe, f.CNPJFundo AS cnpj_fundo,
       f.nmFundoInterno AS fundo, f.nmCotaInterno AS classe,
       p.nome AS cotista, p.doc AS doc_cotista,
       d.nmDistribuidora AS distribuidor,
       COUNT(*) AS notas, SUM(CAST(p.vlCorrigido AS FLOAT)) AS saldo
FROM {PASSIVO_NOTAS} AS p
INNER JOIN dbo.HOD_TB_CADASTRO_FUNDOS AS f ON {PASSIVO_JOIN_FUNDO}
LEFT JOIN {PASSIVO_DISTRIBUIDORES} AS d ON {PASSIVO_JOIN_DISTRIBUIDOR}
WHERE f.Situacao = 'NORMAL' AND f.nmCotaInterno <> 'ORIGINAL'{{filtro}}
GROUP BY p.dataPosicao, p.administrador, p.cdClasseAdm, f.idFundoInterno,
         f.CNPJFundo, f.nmFundoInterno, f.nmCotaInterno, p.nome, p.doc,
         d.nmDistribuidora
"""


# --- casamento lista do e-mail -> cadastro ----------------------------------

def normalizar(texto) -> str:
    """Maiusculas, sem acento, so letras e numeros separados por um espaco."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).upper()
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    t = re.sub(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])", " ", t)   # SENIOR1 -> SENIOR 1
    return " ".join(t.split())


# Palavras que variam entre a lista do RI e o cadastro sem mudar o fundo.
_RUIDO_FUNDO = {"FIDC", "NP", "FIC", "FI", "FUNDO", "DE", "DO", "DA", "EM",
                "INVESTIMENTO", "INVESTIMENTOS", "DIREITOS", "CREDITORIOS",
                "COTAS", "RESPONSABILIDADE", "LIMITADA", "LTDA"}


def chave_fundo(nome) -> str:
    return normalizar(nome)


def chave_fundo_solta(nome) -> str:
    return " ".join(p for p in normalizar(nome).split() if p not in _RUIDO_FUNDO)


_SINONIMO_CLASSE = {"SR": "SENIOR", "SEN": "SENIOR", "MEZ": "MEZANINO",
                    "MZ": "MEZANINO", "SUB": "SUBORDINADA", "JR": "SUBORDINADA"}
_ROMANO = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6"}
_RUIDO_CLASSE = {"CLASSE", "COTA", "COTAS", "DE", "SUBCLASSE"}


def chave_classe(nome) -> str:
    partes = []
    for p in normalizar(nome).split():
        if p in _RUIDO_CLASSE:
            continue
        p = _SINONIMO_CLASSE.get(p, p)
        if partes and p in _ROMANO:          # SENIOR II -> SENIOR 2
            p = _ROMANO[p]
        partes.append(p)
    return " ".join(partes)


def casar_alvos(alvos: pd.DataFrame, cadastro: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por (alvo, classe do cadastro); alvo sem par sai com status.

    Fundo: nome igual depois de normalizar; se nao houver, igual sem as
    palavras de ruido (FIDC, NP...), desde que aponte para UM fundo so.
    Classe: igual depois de normalizar (SR = SENIOR, II = 2...). Nada de
    aproximacao alem disso: classe errada e comissao paga sobre o PL errado.
    """
    cad = cadastro.copy()
    cad["_f"] = cad["nmFundoInterno"].map(chave_fundo)
    cad["_fs"] = cad["nmFundoInterno"].map(chave_fundo_solta)
    cad["_c"] = cad["nmCotaInterno"].map(chave_classe)

    linhas = []
    for i, a in alvos.reset_index(drop=True).iterrows():
        base = {"ordem": i + 1, "fundo_pedido": a["fundo"], "classe_pedida": a["classe"]}
        do_fundo = cad[cad["_f"] == chave_fundo(a["fundo"])]
        if do_fundo.empty:
            solto = cad[cad["_fs"] == chave_fundo_solta(a["fundo"])]
            if solto["nmFundoInterno"].nunique() == 1:
                do_fundo = solto
            elif solto["nmFundoInterno"].nunique() > 1:
                linhas.append({**base, "status": "FUNDO AMBIGUO",
                               "candidatos": " | ".join(sorted(solto["nmFundoInterno"].astype(str).unique()))})
                continue
        if do_fundo.empty:
            linhas.append({**base, "status": "FUNDO NAO ENCONTRADO", "candidatos": ""})
            continue
        par = do_fundo[do_fundo["_c"] == chave_classe(a["classe"])]
        if par.empty:
            linhas.append({**base, "status": "CLASSE NAO ENCONTRADA",
                           "candidatos": " | ".join(sorted(do_fundo["nmCotaInterno"].astype(str).str.strip().unique()))})
            continue
        for _, c in par.iterrows():
            linhas.append({**base, "status": "OK", "candidatos": "",
                           "id_classe": c["idFundoInterno"], "id_fundo_adm": c["idFundoAdm"],
                           "cnpj_fundo": c["CNPJFundo"],
                           "fundo": str(c["nmFundoInterno"]).strip(),
                           "classe": str(c["nmCotaInterno"]).strip()})
    cols = ["ordem", "fundo_pedido", "classe_pedida", "status", "candidatos",
            "id_classe", "id_fundo_adm", "cnpj_fundo", "fundo", "classe"]
    return pd.DataFrame(linhas).reindex(columns=cols)


def todas_as_classes(cadastro: pd.DataFrame) -> pd.DataFrame:
    """`--todos`: o cadastro inteiro vira a lista pedida."""
    c = cadastro.sort_values(["nmFundoInterno", "nmCotaInterno"]).reset_index(drop=True)
    return pd.DataFrame({
        "ordem": range(1, len(c) + 1),
        "fundo_pedido": c["nmFundoInterno"].astype(str).str.strip(),
        "classe_pedida": c["nmCotaInterno"].astype(str).str.strip(),
        "status": "OK", "candidatos": "",
        "id_classe": c["idFundoInterno"], "id_fundo_adm": c["idFundoAdm"],
        "cnpj_fundo": c["CNPJFundo"],
        "fundo": c["nmFundoInterno"].astype(str).str.strip(),
        "classe": c["nmCotaInterno"].astype(str).str.strip()})


# --- montagem das abas -------------------------------------------------------

def _dig(serie: pd.Series) -> pd.Series:
    return serie.astype(str).str.replace(r"\D", "", regex=True)


def tipo_cotista(doc_digitos: str, nome: str, cnpjs_casa: set[str]) -> str:
    if doc_digitos in cnpjs_casa:
        return "FUNDO DA CASA"
    if re.search(r"\b(FIDC|FIC|FIM|FIP|FII|FIAGRO|FI|FUNDO DE INVESTIMENTO|MULTIMERCADO)\b",
                 normalizar(nome)):
        return "FUNDO DE TERCEIRO"
    if len(doc_digitos) == 11:
        return "PF"
    if len(doc_digitos) == 14:
        return "PJ"
    return "SEM DOCUMENTO"


def preparar_posicoes(bruto: pd.DataFrame, cnpjs_casa: set[str]) -> pd.DataFrame:
    p = bruto.copy()
    p["data"] = pd.to_datetime(p["data"]).dt.normalize()
    for c in ("fundo", "classe", "cotista", "doc_cotista", "distribuidor", "administrador"):
        p[c] = p[c].fillna("").astype(str).str.strip()
    p["doc_digitos"] = _dig(p["doc_cotista"])
    p["tipo_cotista"] = [tipo_cotista(d, n, cnpjs_casa)
                         for d, n in zip(p["doc_digitos"], p["cotista"])]
    p["saldo"] = p["saldo"].astype(float)
    return p


def _chave_cotista(p: pd.DataFrame) -> pd.Series:
    """Mesmo cotista = mesmo documento; sem documento, o nome."""
    return p["doc_digitos"].where(p["doc_digitos"].str.strip("0") != "",
                                  "SEMDOC-" + p["cotista"])


def saldo_medio(p: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por (classe, cotista) com medio, inicial, final e maximo."""
    if p.empty:
        return pd.DataFrame()
    p = p.assign(_cot=_chave_cotista(p))
    datas_classe = p.groupby("id_classe")["data"].nunique().rename("datas_classe")
    primeira = p.groupby("id_classe")["data"].min().rename("_d0")
    ultima = p.groupby("id_classe")["data"].max().rename("_d1")
    dia = (p.groupby(["id_classe", "_cot", "data"], as_index=False)["saldo"].sum())
    g = (dia.groupby(["id_classe", "_cot"])
         .agg(soma=("saldo", "sum"), saldo_maximo=("saldo", "max"),
              dias_com_posicao=("data", "nunique"))
         .join(datas_classe, on="id_classe").join(primeira, on="id_classe")
         .join(ultima, on="id_classe").reset_index())
    inicio = dia.merge(g[["id_classe", "_cot", "_d0"]], on=["id_classe", "_cot"])
    inicio = inicio[inicio["data"] == inicio["_d0"]].set_index(["id_classe", "_cot"])["saldo"]
    fim = dia.merge(g[["id_classe", "_cot", "_d1"]], on=["id_classe", "_cot"])
    fim = fim[fim["data"] == fim["_d1"]].set_index(["id_classe", "_cot"])["saldo"]
    g = g.set_index(["id_classe", "_cot"])
    g["saldo_inicial"] = inicio.reindex(g.index).fillna(0.0)
    g["saldo_final"] = fim.reindex(g.index).fillna(0.0)
    g = g.reset_index()
    g["saldo_medio"] = g["soma"] / g["datas_classe"]

    # atributos descritivos: os da ultima data em que o cotista aparece
    desc = (p.sort_values("data")
            .groupby(["id_classe", "_cot"])
            .agg(administrador=("administrador", "last"), cnpj_fundo=("cnpj_fundo", "last"),
                 fundo=("fundo", "last"), classe=("classe", "last"),
                 cotista=("cotista", "last"), doc_cotista=("doc_cotista", "last"),
                 tipo_cotista=("tipo_cotista", "last"),
                 distribuidor=("distribuidor", "last")))
    g = g.join(desc, on=["id_classe", "_cot"])
    tot = g.groupby("id_classe")["saldo_medio"].transform("sum")
    g["participacao_classe"] = (g["saldo_medio"] / tot).where(tot > 0, 0.0)
    g = g.sort_values(["fundo", "classe", "saldo_medio"], ascending=[True, True, False])
    g["ranking_classe"] = g.groupby("id_classe").cumcount() + 1
    return g[["fundo", "classe", "cnpj_fundo", "administrador", "ranking_classe",
              "cotista", "doc_cotista", "tipo_cotista", "distribuidor",
              "saldo_medio", "participacao_classe", "saldo_inicial", "saldo_final",
              "saldo_maximo", "dias_com_posicao", "datas_classe"]].reset_index(drop=True)


def investidores(medio: pd.DataFrame) -> pd.DataFrame:
    """Principais investidores somando todas as classes da lista."""
    if medio.empty:
        return pd.DataFrame()
    m = medio.assign(_cot=_chave_cotista(medio.assign(
        doc_digitos=_dig(medio["doc_cotista"]))))
    g = (m.groupby("_cot")
         .agg(cotista=("cotista", "first"), doc_cotista=("doc_cotista", "first"),
              tipo_cotista=("tipo_cotista", "first"),
              distribuidor=("distribuidor", lambda s: " | ".join(sorted({x for x in s if x}))),
              classes=("classe", "size"),
              saldo_medio=("saldo_medio", "sum"), saldo_final=("saldo_final", "sum"))
         .reset_index(drop=False))
    nomes = (m.assign(fc=m["fundo"] + " / " + m["classe"])
             .groupby("_cot")["fc"].apply(lambda s: " | ".join(s)))
    g["fundos_classes"] = g["_cot"].map(nomes)
    total = g["saldo_medio"].sum()
    g["participacao_total"] = g["saldo_medio"] / total if total else 0.0
    g = g.sort_values("saldo_medio", ascending=False).reset_index(drop=True)
    g.insert(0, "ranking", range(1, len(g) + 1))
    return g.drop(columns="_cot")


def pl_diario(p: pd.DataFrame) -> pd.DataFrame:
    """Classe x data: o PL somado das posicoes (confere com o administrador)."""
    if p.empty:
        return pd.DataFrame()
    t = (p.groupby(["fundo", "classe", "data"])["saldo"].sum().unstack("data")
         .sort_index())
    t.columns = [c.strftime("%d/%m") for c in t.columns]
    return t.reset_index()


def datas_uteis(inicio: dt.date, fim: dt.date) -> tuple[list[dt.date], str]:
    """Dias uteis do mes pelo calendario ANBIMA; sem ele, seg-sex."""
    dias = [inicio + dt.timedelta(n) for n in range((fim - inicio).days + 1)]
    try:
        from liquidez import calendario
        return [d for d in dias if calendario.e_dia_util(d)], "ANBIMA"
    except Exception:                                    # noqa: BLE001
        return [d for d in dias if d.weekday() < 5], "seg-sex (sem feriados)"


def conferencia_classes(casados: pd.DataFrame, p: pd.DataFrame) -> pd.DataFrame:
    """A lista pedida com o que veio: datas, cotistas e PL medio/final."""
    c = casados.copy()
    if p.empty:
        for col in ("datas_com_base", "cotistas", "pl_medio", "pl_final", "data_final"):
            c[col] = None
        return c
    pl = p.groupby(["id_classe", "data"])["saldo"].sum().reset_index()
    resumo = pl.groupby("id_classe").agg(datas_com_base=("data", "nunique"),
                                         pl_medio=("saldo", "mean"),
                                         data_final=("data", "max"))
    fim = pl.merge(resumo["data_final"].reset_index(), on="id_classe")
    fim = fim[fim["data"] == fim["data_final"]].set_index("id_classe")["saldo"].rename("pl_final")
    cot = p.assign(_cot=_chave_cotista(p)).groupby("id_classe")["_cot"].nunique().rename("cotistas")
    adm = p.groupby("id_classe")["administrador"].agg(lambda s: " | ".join(sorted(set(s))))
    c = (c.join(resumo, on="id_classe").join(fim, on="id_classe")
          .join(cot, on="id_classe").join(adm.rename("administrador"), on="id_classe"))
    sem_base = (c["status"] == "OK") & c["datas_com_base"].isna()
    c.loc[sem_base, "status"] = "SEM POSICAO NO MES"
    return c


def conflitos_de_classe(p: pd.DataFrame) -> pd.DataFrame:
    """Codigo de classe do administrador ligado a mais de uma classe do cadastro
    na mesma data: a nota seria contada duas vezes."""
    if p.empty:
        return pd.DataFrame()
    g = p.groupby(["data", "administrador", "cd_classe_adm"])["id_classe"].nunique()
    return g[g > 1].reset_index()


# --- banco -------------------------------------------------------------------

def mes_limites(mes: str) -> tuple[dt.date, dt.date]:
    ano, m = (int(x) for x in mes.split("-"))
    inicio = dt.date(ano, m, 1)
    prox = dt.date(ano + (m == 12), m % 12 + 1, 1)
    return inicio, prox - dt.timedelta(days=1)


def carregar(mes: str, alvos_csv: Path | None, todos: bool):
    from liquidez import db

    inicio, fim = mes_limites(mes)
    cadastro = db.consultar(SQL_CADASTRO)
    if todos:
        casados = todas_as_classes(cadastro)
    else:
        alvos = pd.read_csv(alvos_csv, sep=";", dtype=str, encoding="utf-8-sig").fillna("")
        alvos = alvos[alvos["fundo"].str.strip() != ""]
        casados = casar_alvos(alvos, cadastro)

    ids = [i for i in casados.loc[casados["status"] == "OK", "id_classe"].dropna().unique()]
    datas = db.consultar(SQL_DATAS, (inicio, fim + dt.timedelta(days=1)))
    datas = [pd.Timestamp(d).date() for d in datas["data"]]

    partes = []
    if ids:
        filtro = "" if todos else f" AND f.idFundoInterno IN ({', '.join('?' * len(ids))})"
        sql = SQL_POSICOES.format(filtro=filtro)
        for d in datas:
            print(f"  lendo {d:%d/%m/%Y} ...", flush=True)
            partes.append(db.consultar(sql, (d, *([] if todos else ids))))
    bruto = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(
        columns=["data", "administrador", "cd_classe_adm", "id_classe", "cnpj_fundo",
                 "fundo", "classe", "cotista", "doc_cotista", "distribuidor", "notas", "saldo"])
    cnpjs_casa = set(_dig(cadastro["CNPJFundo"].dropna()))
    return inicio, fim, datas, casados, preparar_posicoes(bruto, cnpjs_casa)


# --- planilha ------------------------------------------------------------------

NAVY = "002346"
FORMATO_MOEDA = '#,##0.00;[Red]-#,##0.00'
COLUNAS_MOEDA = {"saldo", "saldo_medio", "saldo_inicial", "saldo_final", "saldo_maximo",
                 "pl_medio", "pl_final"}
COLUNAS_PCT = {"participacao_classe", "participacao_total"}


def escrever(caminho: Path, abas: dict[str, pd.DataFrame]) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    caminho.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(caminho, engine="openpyxl") as w:
        for nome, df in abas.items():
            df.to_excel(w, sheet_name=nome, index=False)
            ws = w.sheets[nome]
            if nome == "leia-me":
                ws.column_dimensions["A"].width = 28
                ws.column_dimensions["B"].width = 110
                for linha in ws.iter_rows(min_row=2):
                    linha[1].alignment = Alignment(wrap_text=True, vertical="top")
                    linha[0].font = Font(bold=True)
            cab = PatternFill("solid", fgColor=NAVY)
            for cel in ws[1]:
                cel.fill, cel.font = cab, Font(bold=True, color="FFFFFF")
            ws.freeze_panes = "A2"
            if ws.max_row > 1 and nome != "leia-me":
                ws.auto_filter.ref = ws.dimensions
            for j, col in enumerate(df.columns, start=1):
                letra = get_column_letter(j)
                nome_col = str(col)
                moeda = nome_col in COLUNAS_MOEDA or (nome == "pl_diario" and j > 2)
                if nome != "leia-me":
                    amostra = df[col].astype(str).head(500).str.len()
                    ws.column_dimensions[letra].width = min(
                        max(len(nome_col), int(amostra.max() if len(amostra) else 0)) + 2, 60)
                if moeda or nome_col in COLUNAS_PCT or nome_col == "data" or nome_col == "data_final":
                    fmt = (FORMATO_MOEDA if moeda else "0.00%" if nome_col in COLUNAS_PCT
                           else "dd/mm/yyyy")
                    for (cel,) in ws.iter_rows(min_row=2, min_col=j, max_col=j):
                        cel.number_format = fmt
                    if moeda:
                        ws.column_dimensions[letra].width = max(ws.column_dimensions[letra].width, 18)


def leia_me(inicio, fim, datas, uteis, fonte_cal, todos, alvos_csv, p, avisos):
    faltam = [d for d in uteis if d not in set(datas)]
    linhas = [
        ("Base", f"Passivo de {inicio:%m/%Y} para a apuracao de comissoes"),
        ("Gerado em", dt.datetime.now().strftime("%d/%m/%Y %H:%M")),
        ("Fonte", "datalake.dbo.MUL_GLD_PASSIVO_CONSOLIDADO (mesma base do grafo do passivo); "
                  "classe -> fundo pelo HOD_TB_CADASTRO_FUNDOS (riscogc); distribuidor pela "
                  "TB_PASSIVO_DISTRIBUIDORES, pelo documento do cotista."),
        ("Administradores", ", ".join(PASSIVO_ADMINISTRADORES)),
        ("Recorte", "todas as classes do cadastro (--todos)" if todos
                    else f"classes de {Path(alvos_csv).name} (lista do e-mail do RI de 03/11/2026)"),
        ("Regras", "uma linha por nota (vence a alteracao mais recente); saldo = soma do "
                   "vlCorrigido das notas do cotista na classe e na data (PL da posicao)."),
        ("Saldo medio", "soma das posicoes diarias do cotista / numero de datas em que a classe "
                        "tem base no mes. Dia sem posicao do cotista conta zero."),
        ("Datas com base", f"{len(datas)}: " + ", ".join(f"{d:%d/%m}" for d in datas)),
        (f"Dias uteis ({fonte_cal})", str(len(uteis))),
        ("Dias uteis sem base", ", ".join(f"{d:%d/%m}" for d in faltam) or "nenhum"),
        ("Linhas de posicao", f"{len(p):,}".replace(",", ".")),
        ("Avisos", " | ".join(avisos) or "nenhum"),
        ("Sigilo", "Contem CPF/CNPJ e nome de cotista: uso interno da apuracao. Nao versionar."),
        ("Abas", "classes: lista pedida x cadastro; principais_investidores: ranking somando as "
                 "classes; saldo_medio: cotista por classe; posicoes_diarias: base completa; "
                 "pl_diario: PL por classe e data para conferir com o administrador."),
    ]
    return pd.DataFrame(linhas, columns=["item", "valor"])


def _brl(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mes", required=True, help="AAAA-MM, ex.: 2026-10")
    ap.add_argument("--fundos", default=str(ALVOS_PADRAO),
                    help="CSV fundo;classe (padrao: config/parametros/comissoes_fundos.csv)")
    ap.add_argument("--todos", action="store_true", help="todas as classes do cadastro")
    ap.add_argument("--saida", default=None,
                    help="xlsx de saida (padrao: saidas/passivo_comissoes_AAAA-MM.xlsx)")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"\d{4}-\d{2}", a.mes):
        ap.error("--mes no formato AAAA-MM")

    sufixo = "_todos" if a.todos else ""
    saida = Path(a.saida) if a.saida else AQUI / "saidas" / f"passivo_comissoes_{a.mes}{sufixo}.xlsx"

    print(f"Passivo {a.mes} -- {'todas as classes' if a.todos else a.fundos}")
    inicio, fim, datas, casados, p = carregar(a.mes, Path(a.fundos), a.todos)
    uteis, fonte_cal = datas_uteis(inicio, fim)

    classes = conferencia_classes(casados, p)
    avisos = []
    conf = conflitos_de_classe(p)
    if not conf.empty:
        avisos.append(f"{len(conf)} (data, codigo de classe) ligados a mais de uma classe do cadastro")
    nao_ok = classes[classes["status"] != "OK"]
    if len(nao_ok):
        avisos.append(f"{len(nao_ok)} classe(s) da lista sem posicao: ver aba classes")

    medio = saldo_medio(p)
    abas = {
        "leia-me": leia_me(inicio, fim, datas, uteis, fonte_cal, a.todos, a.fundos, p, avisos),
        "classes": classes,
        "principais_investidores": investidores(medio),
        "saldo_medio": medio,
        "posicoes_diarias": p[["data", "administrador", "cnpj_fundo", "fundo", "classe",
                               "cotista", "doc_cotista", "tipo_cotista", "distribuidor",
                               "notas", "saldo"]].sort_values(
                                   ["fundo", "classe", "data", "saldo"],
                                   ascending=[True, True, True, False]),
        "pl_diario": pl_diario(p),
    }
    if len(abas["posicoes_diarias"]) > LIMITE_EXCEL:
        csv = saida.with_suffix(".posicoes_diarias.csv")
        csv.parent.mkdir(parents=True, exist_ok=True)
        abas["posicoes_diarias"].to_csv(csv, sep=";", decimal=",", index=False, encoding="utf-8-sig")
        abas["posicoes_diarias"] = pd.DataFrame({"aviso": [f"passou de {LIMITE_EXCEL} linhas: "
                                                           f"base completa em {csv.name}"]})
        avisos.append(f"posicoes_diarias em {csv.name}")
    escrever(saida, abas)

    # --- conferencia na tela: sem cotista --------------------------------------
    print()
    print(f"Datas com base ({len(datas)}): " + ", ".join(f"{d:%d/%m}" for d in datas))
    faltam = [d for d in uteis if d not in set(datas)]
    print(f"Dias uteis {fonte_cal}: {len(uteis)} | sem base: "
          + (", ".join(f"{d:%d/%m}" for d in faltam) or "nenhum"))
    print()
    print(f"{'#':>3}  {'STATUS':<22} {'FUNDO / CLASSE PEDIDA':<45} {'NO CADASTRO':<45} "
          f"{'ADM':<10} {'DATAS':>5} {'COTIST':>6} {'PL MEDIO':>18} {'PL FINAL':>18}")
    for _, c in classes.iterrows():
        pedido = f"{c['fundo_pedido']} / {c['classe_pedida']}"
        cad = (f"{c['fundo']} / {c['classe']}" if isinstance(c.get("fundo"), str)
               else (c["candidatos"] and f"existe: {c['candidatos']}") or "-")
        print(f"{int(c['ordem']):>3}  {c['status']:<22} {pedido[:45]:<45} {cad[:45]:<45} "
              f"{(c['administrador'] if pd.notna(c.get('administrador')) else '-')[:10]:<10} "
              f"{int(c['datas_com_base']) if pd.notna(c.get('datas_com_base')) else 0:>5} "
              f"{int(c['cotistas']) if pd.notna(c.get('cotistas')) else 0:>6} "
              f"{_brl(c.get('pl_medio')):>18} {_brl(c.get('pl_final')):>18}")
        if c["status"] != "OK" and c["candidatos"] and len(c["candidatos"]) > 34:
            print(f"     classes/fundos existentes: {c['candidatos']}")
    print()
    print(f"Linhas de posicao: {len(p)} | cotistas distintos: "
          f"{_chave_cotista(p).nunique() if len(p) else 0} | "
          f"saldo medio somado: {_brl(medio['saldo_medio'].sum() if len(medio) else 0)}")
    for av in avisos:
        print(f"AVISO: {av}")
    if not conf.empty:
        print(conf.to_string(index=False))
    print(f"Planilha: {saida.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
