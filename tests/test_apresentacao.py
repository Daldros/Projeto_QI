"""Apresentacao: escada por vertice em dias uteis, cobertura, concentracao, estresse e HTML."""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez import apresentacao as ap  # noqa: E402
from liquidez import calendario  # noqa: E402

BASE = dt.date(2026, 9, 30)  # quarta-feira


def test_data_do_vertice_pula_fim_de_semana_e_feriado(monkeypatch):
    monkeypatch.setattr(calendario, "_CACHE", {dt.date(2026, 10, 12)})
    assert ap.data_do_vertice(BASE, 1) == dt.date(2026, 10, 1)
    assert ap.data_do_vertice(BASE, 2) == dt.date(2026, 10, 2)
    assert ap.data_do_vertice(BASE, 3) == dt.date(2026, 10, 5)      # sabado/domingo pulados
    assert ap.data_do_vertice(BASE, 8) == dt.date(2026, 10, 13)     # 12/10 feriado


def test_escada_soma_fluxo_e_carteira_por_vertice(monkeypatch):
    monkeypatch.setattr(calendario, "_CACHE", set())
    carteira = pd.DataFrame({
        "Base_Origem": ["TESOURARIA", "DC", "Outros Fundos", "Renda Fixa", "DC", "Outros Fundos"],
        "Codigo_Op": ["A", "C1", "F1", "L1", "C9", "F2"],
        "Codigo_Ativo": ["Saldo", "NCPX", "COTA", "LFTO", "VCNC", "COTA2"],
        "Valor_Liquido": [100.0, 500.0, 1000.0, 50.0, 30.0, 200.0],
        "Qtde_Dias": [0, 90, None, 1, 0, 999],
        "Ativo Vencido": ["NAO", "NAO", "NAO", "NAO", "SIM", "NAO"],
        "Ativos Excluidos": ["NAO"] * 6,
    })
    fluxo = pd.DataFrame({
        "cnpj_fundo": ["x"] * 3, "codigo_op": ["C1", "C1", "C1"],
        "dt_vencimento": ["2026-10-02", "2026-10-30", "2027-01-15"],
        "vl_amortizacao": [100.0, 200.0, 200.0], "vl_acrual": [1.0, 0.0, 0.0],
    })
    esc = ap.escada_por_vertice(carteira, fluxo, BASE, (1, 2, 21, 63))
    por = {e["vertice"]: e for e in esc}
    assert por[1]["caixa"] == 150.0                       # tesouraria D+0 + LFTO D+1; cota sem prazo e 999 fora; vencido fora
    assert por[2]["caixa"] == 150.0 + 101.0               # parcela de 02/10 (amortizacao + acrual)
    assert por[21]["caixa"] == 150.0 + 101.0              # 21 du = 29/10: a parcela de 30/10 ainda nao entrou
    assert por[63]["caixa"] == 150.0 + 101.0 + 200.0      # 63 du = 29/12: entra 30/10, fica fora 15/01/2027


def test_cobertura_e_estados():
    escada = [{"vertice": 1, "data": BASE, "caixa": 100.0, "caixa_dc": 0, "caixa_outras": 100.0},
              {"vertice": 5, "data": BASE, "caixa": 100.0, "caixa_dc": 0, "caixa_outras": 100.0},
              {"vertice": 21, "data": BASE, "caixa": 100.0, "caixa_dc": 0, "caixa_outras": 100.0}]
    anb = pd.DataFrame({"horizonte_dias": [1, 5, 21], "percentil_95": [0.05, 0.11, 0.14], "enquadrado": [True, True, True]})
    cob = ap.cobertura_por_vertice(escada, ap.resgate_por_vertice(anb, 1000.0))
    assert [round(c["razao"], 2) for c in cob] == [2.0, 0.91, 0.71]
    assert [c["estado"] for c in cob] == ["ok", "atencao", "risco"]


def test_concentracao_e_estresse():
    passivo = pd.DataFrame({"vlSaldoTotal": [50.0, 30.0, 10.0, 10.0, 0.0, -5.0]})
    c = ap.concentracao(passivo)
    assert c["n"] == 4 and c["top1"] == 0.5 and c["top3"] == 0.9 and c["top5"] == 1.0
    assert round(c["hhi"], 4) == round(0.25 + 0.09 + 0.01 + 0.01, 4)
    est = ap.estresse(caixa_cc=556.7, fluxo_dc_ate_cc=108.4, maior_cotista=107.8, resgate_max=43.7, pl=2124.1)
    assert [round(e["sobra"], 1) for e in est] == [405.2, 448.3, 296.8]
    assert ap.estresse(10.0, 5.0, 8.0, 4.0, 100.0)[2]["sobra"] < 0


@dataclass
class _Fundo:
    cnpj: str = "23.216.398/0001-01"
    cnpj_numerico: str = "23216398000101"
    nome_interno: str = "MULTIPLICA FIDC"
    administrador: str = "SINGULARE"
    codinome_beyond: str = "Multiplica LP"
    cc: int = 29


def test_montar_html_com_e_sem_fatos(monkeypatch):
    monkeypatch.setattr(calendario, "_CACHE", set())
    ind = {"pl_mil": 2124084.95, "caixa_potencial_mil": 556675.37, "caixa_pl": 0.2621, "prazo_cotizacao": 29,
           "maior_cotista_mil": 107846.91, "cx_cotista": 5.16, "enq_cotista": True, "gdp_mil": 28300.0,
           "cx_gdp": 23.3, "enq_gdp": True, "resgate_max_mil": 43700.0, "caixa_resgate": 15.1, "enq_rco": True,
           "enq_distribuidor": True, "maior_distribuidor_mil": 0.0, "n_cotistas": 180}
    anb = pd.DataFrame({"horizonte_dias": [1, 21], "percentil_95": [0.0017, 0.1172], "enquadrado": [True, True]})
    rel = pd.DataFrame({"origem": ["produto", "produto"], "codigo_ativo": ["DC", "Outros Fundos"],
                        "exposicao": [812e6, 1203e6], "valor_liquido": [100e6, 0.0]})
    ctx = {"fundo": _Fundo(), "indicadores": ind, "anbima": anb, "relatorio": rel,
           "fluxo": pd.DataFrame(), "carteira": pd.DataFrame(), "passivo": pd.DataFrame(),
           "beyond": {"C1": {}}, "bullets": {"operacoes": {}}, "avisos": ["sem fato_fluxo"]}
    an = ap.analisar(ctx, BASE)
    vazio = {"fundo": _Fundo(cnpj="32.510.577/0001-30", cnpj_numerico="32510577000130", nome_interno="MULTIPLICA LONG TERM FIDC",
                             codinome_beyond="Multiplica LT", cc=999),
             "indicadores": {}, "anbima": pd.DataFrame(), "relatorio": pd.DataFrame(), "fluxo": pd.DataFrame(),
             "carteira": pd.DataFrame(), "passivo": pd.DataFrame(), "beyond": {}, "bullets": {}, "avisos": ["sem fato_indicadores"]}
    an2 = ap.analisar(vazio, BASE)
    html = ap.montar([(ctx, an), (vazio, an2)], BASE, {"fatos": "2026-09-30"})
    assert "MULTIPLICA FIDC" in html and "MULTIPLICA LONG TERM FIDC" in html
    assert "R$ 2.124,1 mi" in html and "26,2%" in html and "enquadrado nos 5 critérios" in html
    assert "sem fatos nesta data" in html and "sem fato_fluxo" in html
    assert html.count("<section class='pagina'") == 2 and "<script" in html
