"""Escada com horizonte H e faixas escolhidas (`escada-html`), sem banco."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez import cli, escada_html as eh  # noqa: E402
from liquidez import fatos as ft  # noqa: E402
from liquidez.fluxo import COL_VENCIDO_FLUXO  # noqa: E402
from liquidez.regras_adm import obter  # noqa: E402

FUNDO = SimpleNamespace(cnpj="49.815.454/0001-00", nome_interno="AMADEUS FIDC", cc=29,
                        administrador="SINGULARE")
DATA = "2026-09-30"


def _carteira():
    lin = [
        # Base_Origem, Codigo_Op, Codigo_Ativo, Emissor, Valor, Qtde_Dias, vencido
        ("DC", "C327909", "NCPX", "X", 1000.0, 204, "NAO"),
        ("DC", "C999999", "NCPX", "Y", 50.0, 40, "NAO"),          # sem fluxo: entra pelo prazo
        ("DC", "C888888", "NCPX", "Z", 70.0, 10, "SIM"),          # vencido: so na coluna Vencidos
        ("DC COMPENSADO", "C1", "MEZAN", "S", 5.0, None, "NAO"),
        ("DC COMPENSADO", "C2", "MEZAN", "S", -5.01, None, "NAO"),
        ("CPR", "", "CPR", "", -30.0, None, "NAO"),
        ("TESOURARIA", "", "TES", "", 4.0, None, "NAO"),
        ("Renda Fixa", "", "LFT", "", 2.0, 0, "NAO"),
        ("Outros Fundos", "GAD SR", "GAD FIDC SENIOR", "", 80.0, 29, "NAO"),
        ("Outros Fundos", "SOCDILP", "QI CASH LEGACY RF DI", "", 14.0, 0, "NAO"),
        ("Outros Fundos", "SORAVEN", "SORANO FIDC A VENC", "", 175.0, 29, "NAO"),
        ("Outros Fundos", "SORVEN", "SORANO FIDC VENC", "", 1.0, 29, "NAO"),
    ]
    df = pd.DataFrame(lin, columns=["Base_Origem", "Codigo_Op", "Codigo_Ativo", "Emissor",
                                    "Valor_Liquido", "Qtde_Dias", "Ativo Vencido"])
    df["Ativos Excluidos"] = "NAO"
    df["Qtde_Dias"] = df["Qtde_Dias"].astype("Float64")
    return df


def _bd():
    return pd.DataFrame({
        "ID_Recebivel": ["r1", "r2", "r3", "r4", "r5"],
        "Status_Recebivel": ["A vencer", "A vencer", "A vencer", "Vencido", "A vencer"],
        "Prazo_Atual_Dias_Corridos": [5, 45, 120, -3, 20],
        "Valor_Presente": [10.0, 2.0, 1.0, 1.0, 160.0],
        "Vencidos": ["NAO", "NAO", "NAO", "SIM", "SIM"],      # r5: sacado com vencido
    })


def _dados(prazo):
    cart = _carteira()
    of = pd.DataFrame({"cod_fundo": ["GAD SR", "SOCDILP", "SORAVEN", "SORVEN"],
                       "fundo": ["GAD FIDC SENIOR", "QI CASH LEGACY RF DI", "SORANO FIDC A VENC",
                                 "SORANO FIDC VENC"],
                       "exposicao": [80.0, 14.0, 175.0, 1.0]})
    rel = pd.DataFrame({"codigo_ativo": ["DC", "GAD FIDC SENIOR"], "exposicao": [1120.0, 80.0],
                        "liq_duration": [prazo, 29], "valor_liquido": [0.0, 80.0 if prazo >= 29 else 0.0]})
    ind = SimpleNamespace(caixa_potencial_mil=0.1 if prazo == 21 else 0.18,
                          caixa_pl=0.07 if prazo == 21 else 0.12, cx_cotista=0.4, enq_cotista=True,
                          cx_gdp=0.3, enq_gdp=True, cx_distribuidor=None, enq_distribuidor=None,
                          caixa_resgate=0.05, enq_rco=False, resgate_max_mil=1.0)
    return {"tratamento": SimpleNamespace(carteira=cart, bd=_bd(), regra=obter("SINGULARE")),
            "outros_fundos": of, "relatorio": rel, "indicadores": ind, "pl_mil": 1.36599,
            "prazo_cotizacao": prazo, "data_posicao": DATA}


class _Prazos:
    def prazo_por_ativo(self, nome):
        return {"GAD FIDC SENIOR": 29, "QI CASH LEGACY RF DI": 0}.get(str(nome))


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    from liquidez import cotas, prazos

    vistos = []

    def processar(_cad, _fundo, _saida, _data, prazo_cot=None):
        vistos.append(prazo_cot)
        return _dados(prazo_cot)

    monkeypatch.setattr(cli, "_processar_fundo", processar)
    monkeypatch.setattr(prazos.TabelaPrazos, "carregar", classmethod(lambda cls, *a, **k: _Prazos()))
    monkeypatch.setattr(cotas.MapaCotas, "carregar", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(cotas.ResolvedorCotas, "__init__", lambda self, t, m, c: setattr(self, "t", t))
    monkeypatch.setattr(cotas.ResolvedorCotas, "prazo", lambda self, n: self.t.prazo_por_ativo(n))
    fluxo = pd.DataFrame({"cnpj_fundo": [FUNDO.cnpj] * 3, "codigo_op": ["C327909"] * 3,
                          "qt_dias": [26, 71, 204], "vl_amortizacao": [0.0, 300.0, 600.0],
                          "vl_acrual": [100.0, 0.0, 0.0], "vl_parcela": [110.0, 310.0, 620.0],
                          COL_VENCIDO_FLUXO: [False, False, False]})
    ft.gravar_tabela(fluxo, "fato_fluxo", DATA, tmp_path / "bi")
    return tmp_path, vistos


def test_escada_faixas_vencidos_e_conciliacao(ambiente):
    tmp, vistos = ambiente
    e = eh.calcular(None, FUNDO, tmp, DATA, 21, 90, (7, 15, 30, 60, 90), tmp / "bi")
    assert vistos == [21, 29]                                    # H e, ao lado, o cc
    assert e.rotulos() == ["0–7", "8–15", "16–30", "31–60", "61–90", ">90"]
    t = e.por_faixa(e.entradas, "categoria")
    assert t.loc[eh.CAT_DC, "16–30"] == 100.0 and t.loc[eh.CAT_DC, "61–90"] == 300.0
    assert t.loc[eh.CAT_DC, ">90"] == 600.0
    assert t.loc[eh.CAT_DC_SEM_FLUXO, "31–60"] == 50.0            # C999999, 40 dias
    assert t.loc[eh.CAT_CAIXA, "0–7"] == pytest.approx(-26.0)    # CPR + tesouraria no dia 0
    assert t.loc[eh.CAT_OUTROS, "0–7"] == 2.0
    assert t.loc[eh.CAT_COTAS, "16–30"] == 80.0 and t.loc[eh.CAT_COTAS, "0–7"] == 14.0
    assert t.loc[eh.CAT_ESTOQUE, "0–7"] == 10.0 and t.loc[eh.CAT_ESTOQUE, "31–60"] == 2.0
    assert t.loc[eh.CAT_ESTOQUE, ">90"] == 1.0
    v = e.vencidos.groupby("componente")["valor"].sum()
    assert v[eh.V_CARTEIRA] == 70.0 and v[eh.V_ESTOQUE] == 1.0 and v[eh.V_CARREGO] == 160.0
    assert e.fora["DC COMPENSADO (pares que se anulam)"] == pytest.approx(-0.01)
    assert e.cotas_substituidas == 176.0 and e.estoque_total == 174.0
    # tudo o que esta no PL aparece em algum lugar
    assert "Diferenca R$ 0,00" in eh.conciliacao(e, v.sum())
    assert e.ate(21) == pytest.approx(10.0 - 26.0 + 2.0 + 14.0)  # ate D+21: estoque 5d, caixa, LFT, QI CASH


def test_html_sem_cotista_e_com_as_faixas(ambiente):
    tmp, _ = ambiente
    caminho, e = eh.gerar(None, FUNDO, tmp, DATA, 21, 90, (7, 15, 30, 60, 90), tmp / "bi",
                          tmp / "escada.html")
    pagina = caminho.read_text(encoding="utf-8")
    assert pagina.startswith("<!doctype html>") and "<title>Escada de liquidez · AMADEUS FIDC</title>" in pagina
    for rot in ("0–7 d", "8–15 d", "16–30 d", "31–60 d", "61–90 d", "&gt;90 d", "Vencidos"):
        assert rot in pagina
    assert "H = D+21" in pagina and "Caixa Potencial em D+21" in pagina and "D+29" in pagina
    assert "prefers-color-scheme: dark" in pagina
    texto = eh.resumo_texto(e)
    assert "VENCIDOS (fora da escada)" in texto and "Diferenca R$ 0,00" in texto
