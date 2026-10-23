"""Linha agregada (MEZAN/SRP) projetada pelo perfil de vencimento do estoque do fundo."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez import bullets as bl  # noqa: E402
from liquidez import fluxo as fx  # noqa: E402

BASE = dt.date(2026, 9, 30)


def _bd():
    return pd.DataFrame({
        "Prazo_Atual_Dias_Corridos": [10, 10, 40, 90, -5, 20],
        "Valor_Presente": [100.0, 50.0, 150.0, 200.0, 30.0, 70.0],
        "Status_Recebivel": ["A vencer", "A vencer", "A vencer", "A vencer", "Vencido", "Vencido"],
    })


def test_pesos_do_estoque_ignora_vencidos_e_agrupa_por_data():
    pesos = fx.pesos_do_estoque(_bd(), BASE)
    assert pesos["qt_dias"].tolist() == [10, 40, 90]
    assert pesos["dt_vencimento"].tolist() == [dt.date(2026, 10, 10), dt.date(2026, 11, 9), dt.date(2026, 12, 29)]
    assert [round(p, 3) for p in pesos["peso"]] == [0.3, 0.3, 0.4]
    assert fx.pesos_do_estoque(pd.DataFrame(), BASE).empty
    assert fx.pesos_do_estoque(_bd().drop(columns=["Valor_Presente"]), BASE).empty


def test_projetar_estoque_reparte_o_valor_da_carteira():
    pesos = fx.pesos_do_estoque(_bd(), BASE)
    f = fx.projetar_estoque("C1", 1000.0, pesos, "x")
    assert len(f) == 3 and round(f["vl_amortizacao"].sum(), 6) == 1000.0
    assert f["vl_amortizacao"].round(2).tolist() == [300.0, 300.0, 400.0]
    assert (f["tp_indice"] == "ESTOQUE").all() and (f["vl_juros"] == 0).all()


class _Crono:
    codigos: set = set()

    def operacao(self, codigo):
        return None


def test_projetar_usa_estoque_so_para_familia_agregada_com_valor():
    posicoes = pd.DataFrame({
        "codigo_op": ["C1", "C2", "C3"], "valor_base": [1000.0, 0.0, 500.0],
        "codigo_ativo": ["MEZAN", "SRP", "NCPX"],
        "dt_vencimento": [pd.Timestamp("2028-01-01")] * 3, "n_vencimentos": [1, 1, 1],
    })
    pesos = fx.pesos_do_estoque(_bd(), BASE)
    curva = fx.Curva(data_base=BASE, mercadoria="DI1", ndc=[1, 3650], fator_spot=[1.0, 2.0], ndu=[1, 2500])
    r = fx.projetar(posicoes, _Crono(), BASE, curva=curva, cnpj_fundo="x", bullet=True, pesos_estoque=pesos)
    assert [e["codigo_op"] for e in r.pelo_estoque] == ["C1"]           # MEZAN com valor: pelo estoque
    por = {b["codigo_op"]: b for b in r.bullets}
    assert por["C2"]["estado"] == bl.POR_REGRA                         # SRP zerado: bullet por regra, sem conferencia
    assert por["C3"]["estado"] == bl.PENDENTE                          # NCPX sem Beyond: bullet a conferir
    assert round(r.fluxo["vl_amortizacao"].sum(), 6) == 1500.0
    # sem estoque, MEZAN volta a ser bullet por regra
    r2 = fx.projetar(posicoes, _Crono(), BASE, curva=curva, cnpj_fundo="x", bullet=True, pesos_estoque=None)
    assert not r2.pelo_estoque and {b["codigo_op"] for b in r2.bullets} == {"C1", "C2", "C3"}


def test_par_de_contrapartida_se_anula_pelo_estoque():
    # MEZAN/SRP vem em pares (+/-). As duas pernas seguem o perfil do estoque:
    # se so a positiva fosse repartida, a janela ganharia caixa que nao existe.
    posicoes = pd.DataFrame({
        "codigo_op": ["C1", "C2", "C3"], "valor_base": [1000.0, -1000.0, 600.0],
        "codigo_ativo": ["MEZAN", "MEZAN", "SRP"],
        "dt_vencimento": [pd.Timestamp("2049-12-31")] * 3, "n_vencimentos": [1, 1, 1],
    })
    pesos = fx.pesos_do_estoque(_bd(), BASE)
    curva = fx.Curva(data_base=BASE, mercadoria="DI1", ndc=[1, 3650], fator_spot=[1.0, 2.0], ndu=[1, 2500])
    r = fx.projetar(posicoes, _Crono(), BASE, curva=curva, cnpj_fundo="x", bullet=True, pesos_estoque=pesos)
    assert {e["codigo_op"] for e in r.pelo_estoque} == {"C1", "C2", "C3"} and not r.bullets
    por_data = r.fluxo.groupby("dt_vencimento")["vl_amortizacao"].sum().round(2)
    assert por_data.tolist() == [180.0, 180.0, 240.0]                 # so o liquido (600) pelo perfil
