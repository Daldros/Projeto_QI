"""Passivo lido direto da MUL_GLD_PASSIVO_CONSOLIDADO do datalake (de onde o Datalake
monta a HOD_PAS_*). Sem banco: confere o SQL montado e o `atualidade`."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez import queries as q  # noqa: E402

SCRIPTS_PASSIVO = ("analise_passivo.py", "grafo_fundo.py", "grafo_passivo.py", "passivo_gestor.py")


def test_db_passivo_le_a_consolidada_com_uma_linha_por_nota():
    sql, params = q.db_passivo("23.216.398/0001-01", "2026-10-20")
    assert "datalake.dbo.MUL_GLD_PASSIVO_CONSOLIDADO" in sql and "HOD_PAS_" not in sql
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


def test_hod_pas_nao_e_mais_lida_em_lugar_nenhum():
    for arquivo in [RAIZ / "liquidez" / "queries.py"] + [RAIZ / a for a in SCRIPTS_PASSIVO]:
        texto = arquivo.read_text(encoding="utf-8")
        assert "dbo.HOD_PAS_" not in texto, arquivo.name
    for arquivo in SCRIPTS_PASSIVO:
        assert "FROM {PASSIVO_NOTAS} AS p" in (RAIZ / arquivo).read_text(encoding="utf-8"), arquivo


def test_atualidade_mostra_cada_base_e_nao_para_em_falha(monkeypatch, capsys):
    import datetime as dt

    from liquidez import cli, db

    def falso(sql, params=()):
        if "datalake." in sql:
            raise RuntimeError("The SELECT permission was denied on the object\nmais")
        if "TB_TAXAS" in sql:
            return pd.DataFrame({"u": [None]})
        return pd.DataFrame({"u": [dt.date(2026, 10, 6)]})

    monkeypatch.setattr(db, "consultar", falso)
    assert cli.main(["atualidade"]) == 0
    saida = capsys.readouterr().out
    no_datalake = sum("datalake." in sql for _, sql in q.SQL_ATUALIDADE)
    assert no_datalake == 4                     # carteira, passivo, cotas (MEC) e estoque
    assert saida.count("2026-10-06") == len(q.SQL_ATUALIDADE) - no_datalake - 1
    assert saida.count("(falhou: The SELECT permission was denied on the object)") == no_datalake
    assert f"{'CDI (TB_TAXAS)':<28} (vazia)" in saida
    assert "HOD_PAS_" not in " ".join(sql for _, sql in q.SQL_ATUALIDADE)
