"""Passivo lido direto da MUL_GLD_PASSIVO_CONSOLIDADO (de onde o Datalake monta a HOD_PAS_*).
Sem banco: confere o SQL montado e o resumo usado no `passivo comparar`."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez import queries as q  # noqa: E402
from liquidez.diagnostico import formatar_comparacao_passivo, resumir_passivo  # noqa: E402

SCRIPTS_PASSIVO = ("analise_passivo.py", "grafo_fundo.py", "grafo_passivo.py", "passivo_gestor.py")


def test_db_passivo_le_a_consolidada_com_uma_linha_por_nota():
    sql, params = q.db_passivo("23.216.398/0001-01", "2026-10-20")
    assert "dbo.MUL_GLD_PASSIVO_CONSOLIDADO" in sql and "HOD_PAS_" not in sql
    # nota repetida na consolidada: vence a alteracao mais recente, como na carga da HOD
    assert "ROW_NUMBER() OVER (PARTITION BY m.dtReferencia" in sql and "x.rn = 1" in sql
    assert "ORDER BY m.dhAlteracao DESC, m.id DESC" in sql
    assert "IN ('SINGULARE', 'PLANNER')" in sql
    # a data vai no FROM (dentro da base de notas), o fundo no WHERE
    assert sql.count("?") == 2 and params == ("2026-10-20", "23.216.398/0001-01")
    assert sql.index("m.dtReferencia = ?") < sql.index("f.CNPJFundo = ?")
    # colunas que indicadores e apresentacao leem
    for coluna in ("dataPosicao", "nmFundoInterno", "CNPJFundo", "nmCotista", "docCotista",
                   "nmDistribuidor", "vlSaldoTotal"):
        assert coluna in sql


def test_distribuidor_entra_uma_vez_por_documento():
    assert "GROUP BY REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(DocumentoDistribuidor))" in q.PASSIVO_DISTRIBUIDORES
    sql, _ = q.db_passivo("x", "2026-10-20")
    assert "LEFT JOIN dbo.TB_PASSIVO_DISTRIBUIDORES" not in sql      # o join direto duplicava a nota


def test_hod_pas_so_sobra_na_consulta_de_comparacao():
    fonte = (RAIZ / "liquidez" / "queries.py").read_text(encoding="utf-8")
    legado = inspect.getsource(q.db_passivo_hod)
    diagnostico = sum(sql.count("dbo.HOD_PAS_") for _, sql in q.SQL_ATUALIDADE)
    # so na consulta antiga do `passivo comparar` e na ultima data do `atualidade`
    assert legado.count("dbo.HOD_PAS_") == 2 and diagnostico == 1
    assert fonte.count("dbo.HOD_PAS_") == 3
    for arquivo in SCRIPTS_PASSIVO:
        texto = (RAIZ / arquivo).read_text(encoding="utf-8")
        assert "dbo.HOD_PAS_" not in texto, arquivo
        assert "FROM {PASSIVO_NOTAS} AS p" in texto, arquivo


def test_resumo_do_passivo_nao_leva_cotista():
    df = pd.DataFrame({"nmCotista": ["A", "B", "C", "D"], "docCotista": ["1", "2", "3", "4"],
                       "nmDistribuidor": [None, "XP", None, "BTG"],
                       "vlSaldoTotal": [100.0, 400.0, 250.0, 50.0]})
    r = resumir_passivo(df)
    assert r == {"linhas": 4, "total": 800.0, "maior": 400.0, "tres_maiores": 750.0, "com_distribuidor": 2}
    assert resumir_passivo(pd.DataFrame())["linhas"] == 0
    texto = formatar_comparacao_passivo("FUNDO X", "00.000.000/0001-00", r, resumir_passivo(None),
                                        {"linhas": 5, "notas": 4})
    assert "1 repetida(s) descartada(s)" in texto and "0.40" in texto


def test_atualidade_mostra_cada_base_e_nao_para_em_falha(monkeypatch, capsys):
    import datetime as dt

    from liquidez import cli, db

    def falso(sql, params=()):
        if "datalake." in sql:
            raise RuntimeError("The SELECT permission was denied on the object\nmais")
        if "HOD_PAS_POSICAO_NOTA" in sql:
            return pd.DataFrame({"u": [None]})
        return pd.DataFrame({"u": [dt.date(2026, 10, 6)]})

    monkeypatch.setattr(db, "consultar", falso)
    assert cli.main(["atualidade"]) == 0
    saida = capsys.readouterr().out
    assert saida.count("2026-10-06") == len(q.SQL_ATUALIDADE) - 3
    assert saida.count("(falhou: The SELECT permission was denied on the object)") == 2
    assert "passivo HOD (antigo)         (vazia)" in saida
