"""Leitura do Beyond por fundo e diagnostico (`cronograma beyond-diagnostico`), sem banco."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez import beyond_cronograma as bc  # noqa: E402
from liquidez import cli, db  # noqa: E402

AMADEUS = SimpleNamespace(cnpj="49.815.454/0001-00", nome_interno="AMADEUS FIDC", cc=29,
                          codinome_beyond="Amadeus")


class _Cadastro:
    def __init__(self, *_a, **_k):
        pass

    def carregar(self):
        return self

    def obter(self, _chave):
        return AMADEUS

    def filtrar(self, **_k):
        return [AMADEUS]


def test_resolver_leitura(monkeypatch):
    chamadas = []

    def falso(sql, params=()):
        chamadas.append((sql, params))
        if "WHERE Fundo = ?" in sql:
            return pd.DataFrame({"d": [dt.date(2026, 9, 24)]})
        return pd.DataFrame({"d": [dt.date(2026, 10, 30)]})

    monkeypatch.setattr(db, "consultar", falso)
    assert bc.resolver_leitura("atual", "Amadeus", "2026-09-30") == dt.date(2026, 10, 30)
    assert bc.resolver_leitura("fundo", "Amadeus", "2026-09-30") == dt.date(2026, 9, 24)
    assert chamadas[-1][1] == ("Amadeus", "2026-09-30")          # nao passa da posicao
    assert bc.resolver_leitura("2026-09-17", "Amadeus", "x") == dt.date(2026, 9, 17)


def test_leitura_do_fundo_sem_leitura(monkeypatch):
    monkeypatch.setattr(db, "consultar", lambda sql, params=(): pd.DataFrame({"d": [None]}))
    assert bc.leitura_do_fundo("Amadeus", "2026-09-30") is None


def test_diagnostico_acha_o_nome_que_o_beyond_usa(monkeypatch, capsys):
    vistos = []

    def falso(sql, params=()):
        vistos.append((sql, params))
        if "GROUP BY Data ORDER BY Data DESC" in sql:
            return pd.DataFrame({"Data": [dt.date(2026, 9, 24)], "fundos": [30], "linhas": [9]})
        if "SELECT MAX(Data) AS d FROM dbo.TB_LIQUIDEZ_OPERACOES_4" == sql:
            return pd.DataFrame({"d": [dt.date(2026, 10, 30)]})
        if "GROUP BY Fundo" in sql:
            assert params[1] == "%AMADEUS%" and params[2] == "Amadeus"
            return pd.DataFrame({"Fundo": ["AMADEUS FIDC NP"], "leituras": [8],
                                 "primeira": [dt.date(2026, 8, 6)], "ultima": [dt.date(2026, 10, 1)],
                                 "ultima_ate_posicao": [dt.date(2026, 9, 24)], "linhas": [400]})
        if "GROUP BY Grupo_Produto, Situacao_Titulo" in sql:
            assert params == ("AMADEUS FIDC NP", "2026-09-24")
            return pd.DataFrame({"Grupo_Produto": ["CCB/NC", "CCB/NC"],
                                 "Situacao_Titulo": ["A Vencer", "Vencido"],
                                 "operacoes": [3, 1], "titulos": [30, 2], "aplicado": [1e6, 5e4],
                                 "venc_min": ["2026-10-01", "2026-09-01"],
                                 "venc_max": ["2027-09-01", "2026-09-10"]})
        if "HOD_TB_ESTOQUE_UNIFICADO" in sql and "MAX(" in sql:
            return pd.DataFrame({"d": [dt.date(2026, 9, 29)]})
        if "HOD_TB_ESTOQUE_UNIFICADO" in sql:
            return pd.DataFrame({"Status_Recebivel": ["A Vencer"], "recebiveis": [10],
                                 "valor_presente": [1234.5]})
        if "MAX(dtReferencia)" in sql:
            return pd.DataFrame({"d": [dt.date(2026, 9, 30)]})
        raise RuntimeError("carteira fora do ar")                 # a secao falha, as outras seguem

    monkeypatch.setattr(db, "consultar", falso)
    monkeypatch.setattr(cli, "Cadastro", _Cadastro)
    rc = cli.main(["cronograma", "beyond-diagnostico", "--fundo", "AMADEUS FIDC",
                   "--data", "2026-09-30"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "usado: Fundo = 'AMADEUS FIDC NP'" in out and "NAO casa" in out
    assert "ultima leitura ate 2026-09-30: 2026-09-24" in out
    assert "falhou: carteira fora do ar" in out
    # estoque: a data pedida e a ultima disponivel antes dela
    assert "estoque em 2026-09-30" in out and "estoque em 2026-09-29" in out


def test_captura_usa_a_leitura_do_fundo(monkeypatch, capsys, tmp_path):
    from liquidez import cronograma as cr

    usadas = []

    def falso(sql, params=()):
        if "WHERE Fundo = ? AND Data <= ?" in sql:
            return pd.DataFrame({"d": [dt.date(2026, 9, 24)]})
        if "NCPX" in sql:
            return pd.DataFrame({"Codigo_Op": ["C1"], "Emissor": ["X"],
                                 "Data_Aquisicao": [dt.date(2026, 9, 1)], "Custo_Total": [10.0]})
        raise AssertionError(sql)

    monkeypatch.setattr(db, "consultar", falso)
    monkeypatch.setattr(cli, "Cadastro", _Cadastro)
    monkeypatch.setattr(cr.Cronograma, "carregar_se_houver", staticmethod(lambda _c: None))
    monkeypatch.setattr(cr, "normalizar_codigo", lambda c: c)
    monkeypatch.setattr(bc, "montar", lambda *a, **k: (usadas.append(a[3]), (None, "sem par"))[1])
    monkeypatch.setattr(bc, "snapshot_atual", lambda: (_ for _ in ()).throw(AssertionError("global")))
    rc = cli.main(["cronograma", "beyond", "--fundo", "AMADEUS FIDC", "--data", "2026-09-30",
                   "--leitura", "fundo", "--saida", str(tmp_path)])
    assert rc == 2 and usadas == [dt.date(2026, 9, 24)]
    assert "leitura 2026-09-24" in capsys.readouterr().out
