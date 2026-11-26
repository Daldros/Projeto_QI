"""Estoque por fundo (MUL_GLD_ESTOQUE): Valor Presente, PDD e taxa media ponderada. Sem banco."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import analise_estoque_taxa as ae  # noqa: E402

BRUTO = pd.DataFrame({
    "cnpj": ["49815454000100", "23216398000101"],        # o SQL devolve so os digitos
    "fundo": ["AMADEUS FIDC", "MULTIPLICA FIDC"],
    "recebiveis": [3, 2],
    "valor_aquisicao": [90.0, 1800.0], "valor_presente": [100.0, 2000.0], "valor_nominal": [120.0, 2200.0],
    "vl_pdd": [-5.0, -40.0],
    # titulos de 10 (2%), 30 (3%) e 60 (1%) no AMADEUS: ponderada = (0,2+0,9+0,6)/100 = 1,7%
    "taxa_x_vp": [1.7, 40.0], "vp_com_taxa": [100.0, 2000.0],
    "taxa_simples": [2.0, 2.5], "taxa_min": [1.0, 1.5], "taxa_max": [3.0, 3.5],
    "sem_taxa": [0, 0], "em_write_off": [0, 1],
})


def test_taxa_ponderada_pelo_valor_presente_e_total():
    df = ae.montar(BRUTO)
    assert list(df["fundo"]) == ["MULTIPLICA FIDC", "AMADEUS FIDC"]       # maior VP primeiro
    am = df.set_index("fundo").loc["AMADEUS FIDC"]
    assert am["cnpj"] == "49.815.454/0001-00"                               # mascara de volta
    assert am["taxa_media"] == pytest.approx(0.017) and am["taxa_simples"] == 2.0
    t = ae.total(df)
    assert t["valor_presente"] == 2100 and t["vl_pdd"] == -45 and t["recebiveis"] == 5
    assert t["taxa_media"] == pytest.approx((0.017 * 100 + 0.02 * 2000) / 2100)


def test_sql_uma_linha_por_recebivel_na_data():
    sql = ae.SQL_FUNDOS
    assert "datalake.dbo.MUL_GLD_ESTOQUE" in sql and "e.dtReferencia = ?" in sql
    assert "PARTITION BY b.doc, b.dsSeuNumero, b.dsNuDocumento" in sql and "WHERE rn = 1" in sql
    assert "ORDER BY b.dhAlteracao DESC, b.id DESC" in sql and "GROUP BY doc" in sql


def test_main_com_banco_falso_grava_a_planilha(tmp_path, monkeypatch, capsys):
    from openpyxl import load_workbook

    from liquidez import db

    def falso(sql, params=()):
        if "MAX(dtReferencia)" in sql:
            assert params == ("2026-10-30",)
            return pd.DataFrame({"d": [pd.Timestamp("2026-10-29")]})
        assert params == ("2026-10-29",)
        return BRUTO

    monkeypatch.setattr(db, "consultar", falso)
    saida = tmp_path / "e.xlsx"
    assert ae.main(["--data", "2026-10-30", "--saida", str(saida)]) == 0
    out = capsys.readouterr().out
    assert "AVISO: sem posicao em 2026-10-30; usada 2026-10-29" in out and "TOTAL" in out
    wb = load_workbook(saida)
    ws = wb["Estoque por fundo"]
    assert [c.value for c in ws[4]] == ["Fundo", "CNPJ", "Valor Presente (R$)", "VL PDD (R$)", "Taxa média"]
    assert ws["A5"].value == "MULTIPLICA FIDC" and ws["A7"].value == "Total" and ws["C7"].value == 2100
    assert "Conferência" in wb.sheetnames
