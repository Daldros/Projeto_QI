"""Estoque por fundo numa data: Valor Presente, PDD e taxa media dos recebiveis.

    python analise_estoque_taxa.py --data 2026-10-30 [--saida saidas\\estoque_taxa_2026-10-30.xlsx]

Pedido da area em 26/11/2026. Le `datalake.dbo.MUL_GLD_ESTOQUE` na data e, por
fundo (CNPJ):

  - Valor Presente (`vlPresente`): o valor amortizado do recebivel na data;
  - VL PDD (`vlPdd`): soma da PDD marcada;
  - Taxa media (`txRecebivel`): media PONDERADA PELO VALOR PRESENTE -- os
    recebiveis tem valores muito diferentes, e a media simples daria o mesmo
    peso a um titulo de R$ 100 e a um de R$ 1 milhao;
  - e, para conferencia, Valor de Aquisicao (`vlAquisicao`, quanto o fundo
    pagou), Valor Nominal (`vlNominal`, quanto o sacado vai pagar) e a
    quantidade de recebiveis.

Uma linha por recebivel: a tabela repete o mesmo recebivel (seu numero +
documento) quando ele e corrigido, e vale a alteracao mais recente -- a mesma
regra da carga do estoque (`usp_Carregar_HOD_TB_ESTOQUE_UNIFICADO`). Sem essa
regra, o recebivel corrigido seria somado duas vezes.

Sem posicao na data pedida, usa a ultima data ate ela e avisa. So SELECT; a
planilha so tem totais por fundo (nada de sacado ou cedente).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI))

from liquidez.tabelas import no_datalake  # noqa: E402

ESTOQUE = no_datalake("MUL_GLD_ESTOQUE")

SQL_DATA = f"SELECT MAX(dtReferencia) AS d FROM {ESTOQUE} WITH (NOLOCK) WHERE dtReferencia <= ?"

# Uma linha por recebivel na data (vence a alteracao mais recente) e o total por
# fundo no proprio banco: sao milhoes de linhas, e so os totais saem.
SQL_FUNDOS = f"""
WITH b AS (
    -- o CNPJ do fundo vem com e sem mascara na tabela: so os digitos agrupam
    SELECT e.*, REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(e.noDocFundo)), '.', ''), '/', ''), '-', '') AS doc
    FROM {ESTOQUE} AS e WITH (NOLOCK)
    WHERE e.dtReferencia = ?
), r AS (
    SELECT b.doc, b.dsNomeFundo, b.vlAquisicao, b.vlPresente, b.vlNominal, b.vlPdd, b.txRecebivel, b.dsFaixaPdd,
           ROW_NUMBER() OVER (PARTITION BY b.doc, b.dsSeuNumero, b.dsNuDocumento
                              ORDER BY b.dhAlteracao DESC, b.id DESC) AS rn
    FROM b
)
SELECT doc AS cnpj,
       MAX(dsNomeFundo) AS fundo,
       COUNT(*) AS recebiveis,
       SUM(CAST(vlAquisicao AS FLOAT)) AS valor_aquisicao,
       SUM(CAST(vlPresente AS FLOAT)) AS valor_presente,
       SUM(CAST(vlNominal AS FLOAT)) AS valor_nominal,
       SUM(CAST(vlPdd AS FLOAT)) AS vl_pdd,
       SUM(CAST(txRecebivel AS FLOAT) * CAST(vlPresente AS FLOAT)) AS taxa_x_vp,
       SUM(CASE WHEN txRecebivel IS NOT NULL THEN CAST(vlPresente AS FLOAT) ELSE 0 END) AS vp_com_taxa,
       AVG(CAST(txRecebivel AS FLOAT)) AS taxa_simples,
       SUM(CASE WHEN txRecebivel IS NULL THEN 1 ELSE 0 END) AS sem_taxa,
       SUM(CASE WHEN UPPER(LTRIM(RTRIM(dsFaixaPdd))) IN ('WOM', 'WOP') THEN 1 ELSE 0 END) AS em_write_off,
       MIN(CAST(txRecebivel AS FLOAT)) AS taxa_min,
       MAX(CAST(txRecebivel AS FLOAT)) AS taxa_max
FROM r
WHERE rn = 1
GROUP BY doc
"""


def _cnpj(doc: str) -> str:
    d = "".join(ch for ch in str(doc) if ch.isdigit())
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else str(doc)


def montar(bruto: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por fundo, com a taxa ponderada pelo Valor Presente."""
    cols = ["fundo", "cnpj", "valor_presente", "vl_pdd", "taxa_media", "valor_aquisicao", "valor_nominal",
            "recebiveis", "taxa_simples", "taxa_min", "taxa_max", "sem_taxa", "em_write_off"]
    if bruto is None or bruto.empty:
        return pd.DataFrame(columns=cols)
    df = bruto.copy()
    for c in ("valor_aquisicao", "valor_presente", "valor_nominal", "vl_pdd", "taxa_x_vp", "vp_com_taxa",
              "taxa_simples", "taxa_min", "taxa_max"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["taxa_media"] = (df["taxa_x_vp"] / df["vp_com_taxa"]).where(df["vp_com_taxa"] > 0)
    df["fundo"] = df["fundo"].fillna("").astype(str).str.strip()
    df["cnpj"] = df["cnpj"].fillna("").astype(str).str.strip().map(_cnpj)
    return df.sort_values("valor_presente", ascending=False).reset_index(drop=True)[cols]


def total(df: pd.DataFrame) -> dict:
    vp = float(df["valor_presente"].sum())
    pond = (df["taxa_media"] * df["valor_presente"]).sum() if len(df) else 0.0
    return {"fundo": "Total", "cnpj": f"{len(df)} fundos", "valor_presente": vp,
            "vl_pdd": float(df["vl_pdd"].sum()),
            "taxa_media": float(pond / vp) if vp else None,
            "valor_aquisicao": float(df["valor_aquisicao"].sum()),
            "valor_nominal": float(df["valor_nominal"].sum()),
            "recebiveis": int(df["recebiveis"].sum())}


# --- planilha ------------------------------------------------------------------

NAVY, AZUL_CLARO, CINZA = "002346", "EAF3FB", "F5F7FA"
MOEDA = '#,##0.00;[Red]-#,##0.00'


def _tipo_taxa(df: pd.DataFrame) -> str:
    """A base guarda a taxa em % (2,1) ou em fracao (0,021)? Decide pela escala."""
    med = df["taxa_media"].dropna().median() if len(df) else None
    return "fracao" if med is not None and med < 0.5 else "percentual"


def escrever(df: pd.DataFrame, data_pedida: str, data_usada: str, caminho: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    escala = _tipo_taxa(df)
    fmt_taxa = "0.0000%" if escala == "fracao" else '0.0000" %"'
    wb = Workbook()
    ws = wb.active
    ws.title = "Estoque por fundo"
    fino = Side(style="thin", color="D1DCE8")

    ws["A1"] = "Estoque de recebíveis por fundo"
    ws["A1"].font = Font(bold=True, size=14, color=NAVY)
    sub = f"Data-base {dt.date.fromisoformat(data_usada):%d/%m/%Y} · fonte datalake.dbo.MUL_GLD_ESTOQUE"
    if data_usada != data_pedida:
        sub += f" (sem posição em {dt.date.fromisoformat(data_pedida):%d/%m/%Y}: usada a última até ela)"
    ws["A2"] = sub
    ws["A2"].font = Font(italic=True, color="597087")

    cab = ["Fundo", "CNPJ", "Valor Presente (R$)", "VL PDD (R$)", "Taxa média"]
    for j, t in enumerate(cab, start=1):
        c = ws.cell(row=4, column=j, value=t)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center")
    linha = 5
    for i, r in df.iterrows():
        valores = [r["fundo"], r["cnpj"], r["valor_presente"], r["vl_pdd"], r["taxa_media"]]
        for j, v in enumerate(valores, start=1):
            c = ws.cell(row=linha, column=j, value=None if pd.isna(v) else v)
            c.border = Border(bottom=fino)
            if i % 2:
                c.fill = PatternFill("solid", fgColor=CINZA)
        ws.cell(row=linha, column=3).number_format = MOEDA
        ws.cell(row=linha, column=4).number_format = MOEDA
        ws.cell(row=linha, column=5).number_format = fmt_taxa
        linha += 1
    t = total(df)
    for j, v in enumerate([t["fundo"], t["cnpj"], t["valor_presente"], t["vl_pdd"], t["taxa_media"]], start=1):
        c = ws.cell(row=linha, column=j, value=v)
        c.font = Font(bold=True, color=NAVY)
        c.fill = PatternFill("solid", fgColor=AZUL_CLARO)
        c.border = Border(top=Side(style="medium", color=NAVY))
    ws.cell(row=linha, column=3).number_format = MOEDA
    ws.cell(row=linha, column=4).number_format = MOEDA
    ws.cell(row=linha, column=5).number_format = fmt_taxa
    for j, w in enumerate((46, 22, 22, 20, 14), start=1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:E{linha - 1}"
    ws.cell(row=linha + 2, column=1,
            value="Taxa média = média da txRecebivel ponderada pelo Valor Presente de cada recebível.").font = \
        Font(italic=True, color="597087")

    # detalhe para conferencia
    d = wb.create_sheet("Conferência")
    cab2 = ["Fundo", "CNPJ", "Recebíveis", "Valor de Aquisição (R$)", "Valor Presente (R$)", "Valor Nominal (R$)",
            "VL PDD (R$)", "PDD / VP", "Taxa média (ponderada)", "Taxa média (simples)", "Taxa mínima",
            "Taxa máxima", "Sem taxa", "Em write-off (WOM/WOP)"]
    for j, tx in enumerate(cab2, start=1):
        c = d.cell(row=1, column=j, value=tx)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, r in df.iterrows():
        vals = [r["fundo"], r["cnpj"], int(r["recebiveis"]), r["valor_aquisicao"], r["valor_presente"],
                r["valor_nominal"], r["vl_pdd"],
                (r["vl_pdd"] / r["valor_presente"]) if r["valor_presente"] else None,
                r["taxa_media"], r["taxa_simples"], r["taxa_min"], r["taxa_max"], int(r["sem_taxa"]),
                int(r["em_write_off"])]
        for j, v in enumerate(vals, start=1):
            c = d.cell(row=i + 2, column=j, value=None if v is None or (isinstance(v, float) and pd.isna(v)) else v)
            if i % 2:
                c.fill = PatternFill("solid", fgColor=CINZA)
        for j in (4, 5, 6, 7):
            d.cell(row=i + 2, column=j).number_format = MOEDA
        d.cell(row=i + 2, column=8).number_format = "0.00%"
        for j in (9, 10, 11, 12):
            d.cell(row=i + 2, column=j).number_format = fmt_taxa
    for j, w in enumerate((46, 22, 12, 22, 22, 22, 20, 10, 14, 14, 12, 12, 10, 14), start=1):
        d.column_dimensions[get_column_letter(j)].width = w
    d.row_dimensions[1].height = 32
    d.freeze_panes = "C2"

    caminho.parent.mkdir(parents=True, exist_ok=True)
    wb.save(caminho)
    return caminho


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="AAAA-MM-DD (ex.: 2026-10-30)")
    ap.add_argument("--saida", help="xlsx de saida (padrao: saidas/estoque_taxa_<data>.xlsx)")
    a = ap.parse_args(argv)

    from liquidez import db

    d = db.consultar(SQL_DATA, (a.data,))
    usada = d.iloc[0, 0] if len(d) else None
    if usada is None or pd.isna(usada):
        print(f"MUL_GLD_ESTOQUE sem nenhuma posicao ate {a.data}.")
        return 1
    usada = str(pd.Timestamp(usada).date())
    if usada != a.data:
        print(f"AVISO: sem posicao em {a.data}; usada {usada}, a ultima ate ela.")
    df = montar(db.consultar(SQL_FUNDOS, (usada,)))
    saida = Path(a.saida) if a.saida else AQUI / "saidas" / f"estoque_taxa_{usada}.xlsx"
    escrever(df, a.data, usada, saida)

    t = total(df)
    escala = _tipo_taxa(df)
    fmt = (lambda v: "-" if v is None or pd.isna(v) else
           (f"{v * 100:.4f}%" if escala == "fracao" else f"{v:.4f} %"))
    print(f"Data-base {usada} | {len(df)} fundos | {t['recebiveis']} recebiveis | taxa em {escala} na base")
    print(f"{'FUNDO':<44} {'CNPJ':<20} {'VALOR PRESENTE':>20} {'VL PDD':>18} {'TAXA MEDIA':>12}")
    for _, r in df.iterrows():
        print(f"{r['fundo'][:44]:<44} {r['cnpj']:<20} {r['valor_presente']:>20,.2f} {r['vl_pdd']:>18,.2f} "
              f"{fmt(r['taxa_media']):>12}")
    print(f"{'TOTAL':<44} {t['cnpj']:<20} {t['valor_presente']:>20,.2f} {t['vl_pdd']:>18,.2f} "
          f"{fmt(t['taxa_media']):>12}")
    print(f"Planilha: {saida.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
