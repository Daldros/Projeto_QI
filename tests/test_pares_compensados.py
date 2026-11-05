"""Pares de DC que se anulam (AMADEUS FIDC, 30/09/2026) saem das faixas e do fluxo."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez import tratamentos as tr  # noqa: E402

LINHAS = [
    # Codigo_Op, Codigo_Ativo, Emissor, Valor_Liquido, Qtde_Dias
    ("C430524", "MEZAN", "SINGCTVM", 15665529.41, 200),
    ("C430522", "MEZAN", "SINGCTVM", -15665529.42, 200),
    ("C430526", "SRP", "SINGCTVM", 43073816.19, 204),
    ("C430525", "SRP", "SINGCTVM", -43073816.19, 204),
    ("C450944", "MEZAN", "SINGCTVM", 20437546.04, 204),
    ("C450963", "MEZAN", " singctvm", -20437546.04, 204),
    ("C451447", "PDDNC", "CBLINDAD", -3868505.66, 8000),     # provisao: sem par, fica
    ("C322637", "NCPX", "CBLINDAD", 3748022.91, 193),        # NC: sem par (outro ativo), fica
    ("C327909", "NCPX", "OUTRO", 41613241.60, 26),
]


def _carteira():
    df = pd.DataFrame(LINHAS, columns=["Codigo_Op", "Codigo_Ativo", "Emissor", "Valor_Liquido",
                                       tr.COL_QTDE_DIAS])
    df[tr.COL_BASE_ORIGEM] = "DC"
    return df


def test_pares_saem_do_dc_e_ficam_sem_prazo():
    out = tr.separar_pares_compensados(_carteira()).set_index("Codigo_Op")
    comp = out[out[tr.COL_BASE_ORIGEM] == tr.PRODUTO_COMPENSADO]
    assert sorted(comp.index) == ["C430522", "C430524", "C430525", "C430526", "C450944", "C450963"]
    assert comp[tr.COL_QTDE_DIAS].isna().all()
    assert abs(comp["Valor_Liquido"].sum() + 0.01) < 1e-6        # o saldo continua no PL
    dc = out[out[tr.COL_BASE_ORIGEM] == "DC"]
    assert sorted(dc.index) == ["C322637", "C327909", "C451447"]
    assert dc[tr.COL_QTDE_DIAS].notna().all()


def test_sem_par_nada_muda_e_par_fora_da_tolerancia_fica():
    df = _carteira()
    df.loc[1, "Valor_Liquido"] = -15665000.00                    # nao se anula
    out = tr.separar_pares_compensados(df).set_index("Codigo_Op")
    assert out.loc["C430524", tr.COL_BASE_ORIGEM] == "DC"
    assert out.loc["C430522", tr.COL_BASE_ORIGEM] == "DC"
    so_um = df[df["Codigo_Op"] == "C327909"]
    assert tr.separar_pares_compensados(so_um).equals(so_um)


def test_fluxo_e_matriz_nao_veem_o_par():
    from liquidez import fluxo as fx
    from liquidez.analise import analisar_carteira
    from liquidez.regras_adm import obter as obter_regra

    df = tr.separar_pares_compensados(_carteira())
    df["Data_Posicao"] = "2026-09-30"
    df[tr.COL_ATIVO_VENCIDO] = tr.NAO
    df[tr.COL_ATIVOS_EXCLUIDOS] = tr.NAO
    pos = fx.posicoes_do_produto(df, "DC")
    assert set(pos["codigo_op"].astype(str)) == {"C322637", "C327909", "C451447"}
    m = analisar_carteira(df, 29, obter_regra("SINGULARE"))
    assert abs(m.loc["Exposição Total", tr.PRODUTO_COMPENSADO] + 0.01) < 1e-6
    faixas = m.drop(index=["Exposição Total"])[tr.PRODUTO_COMPENSADO]
    assert (faixas == 0).all()
