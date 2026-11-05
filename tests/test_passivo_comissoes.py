"""Base de passivo para comissoes (`passivo_comissoes.py`), sem banco."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import passivo_comissoes as pc  # noqa: E402

CADASTRO = pd.DataFrame([
    # idFundoInterno, idFundoAdm, CNPJ, fundo, classe
    (1, "101", "11.111.111/0001-11", "MULTIPLICA FIDC", "SENIOR"),
    (2, "102", "11.111.111/0001-11", "MULTIPLICA FIDC", "MEZANINO A"),
    (3, "103", "11.111.111/0001-11", "MULTIPLICA FIDC", "MEZANINO B"),
    (4, "201", "22.222.222/0001-22", "LIMAJ FIDC", "SÊNIOR I"),
    (5, "301", "33.333.333/0001-33", "EXPLORER FIDC", "SENIOR 2"),
    (6, "401", "44.444.444/0001-44", "PETTRA FIDC", "SUBORDINADA"),
], columns=["idFundoInterno", "idFundoAdm", "CNPJFundo", "nmFundoInterno", "nmCotaInterno"])


def test_normalizar_e_chaves():
    assert pc.normalizar("  Sênior-1 ") == "SENIOR 1"
    assert pc.chave_classe("SENIOR1") == pc.chave_classe("Sênior I") == "SENIOR 1"
    assert pc.chave_classe("SR 2") == "SENIOR 2"
    assert pc.chave_classe("Classe Mezanino A") == "MEZANINO A"
    assert pc.chave_fundo_solta("EXPLORER FIDC NP") == pc.chave_fundo_solta("EXPLORER FIDC") == "EXPLORER"


def test_casar_alvos_acha_par_e_explica_o_que_falta():
    alvos = pd.DataFrame({"fundo": ["MULTIPLICA FIDC", "LIMAJ FIDC", "EXPLORER FIDC NP",
                                    "MULTIPLICA FIDC", "PETTRA FIDC", "NAO EXISTE FIDC"],
                          "classe": ["MEZANINO A", "SENIOR 1", "SENIOR 2",
                                     "MEZANINO D", "MEZANINO", "SENIOR"]})
    c = pc.casar_alvos(alvos, CADASTRO)
    assert list(c["status"]) == ["OK", "OK", "OK", "CLASSE NAO ENCONTRADA",
                                 "CLASSE NAO ENCONTRADA", "FUNDO NAO ENCONTRADO"]
    assert list(c["id_classe"].iloc[:3]) == [2, 4, 5]
    # classe errada nao vira a mais parecida: mostra as que existem
    assert c["candidatos"].iloc[3] == "MEZANINO A | MEZANINO B | SENIOR"
    assert c["candidatos"].iloc[4] == "SUBORDINADA"


def test_lista_do_email_tem_as_21_classes():
    alvos = pd.read_csv(pc.ALVOS_PADRAO, sep=";", dtype=str, encoding="utf-8-sig")
    assert len(alvos) == 21 and not alvos.duplicated(["fundo", "classe"]).any()


def _bruto():
    d1, d2, d3 = (pd.Timestamp(f"2026-10-{d:02d}") for d in (1, 2, 5))
    linhas = [
        # data, adm, cd, id, cnpj, fundo, classe, cotista, doc, distr, notas, saldo
        (d1, "SINGULARE", "102", 2, "11.111.111/0001-11", "MULTIPLICA FIDC", "MEZANINO A",
         "JOAO", "123.456.789-01", None, 2, 100.0),
        (d2, "SINGULARE", "102", 2, "11.111.111/0001-11", "MULTIPLICA FIDC", "MEZANINO A",
         "JOAO", "123.456.789-01", None, 2, 110.0),
        (d3, "SINGULARE", "102", 2, "11.111.111/0001-11", "MULTIPLICA FIDC", "MEZANINO A",
         "JOAO", "123.456.789-01", None, 2, 120.0),
        # entra no meio do mes: conta zero no dia 01
        (d2, "SINGULARE", "102", 2, "11.111.111/0001-11", "MULTIPLICA FIDC", "MEZANINO A",
         "LIMAJ FIDC", "22.222.222/0001-22", None, 1, 300.0),
        (d3, "SINGULARE", "102", 2, "11.111.111/0001-11", "MULTIPLICA FIDC", "MEZANINO A",
         "LIMAJ FIDC", "22.222.222/0001-22", None, 1, 300.0),
        (d1, "PLANNER", "201", 4, "22.222.222/0001-22", "LIMAJ FIDC", "SÊNIOR I",
         "JOAO", "12345678901", "XP", 1, 50.0),
    ]
    return pd.DataFrame(linhas, columns=["data", "administrador", "cd_classe_adm", "id_classe",
                                         "cnpj_fundo", "fundo", "classe", "cotista",
                                         "doc_cotista", "distribuidor", "notas", "saldo"])


def test_saldo_medio_divide_pelas_datas_da_classe():
    p = pc.preparar_posicoes(_bruto(), {"11111111000111", "22222222000122"})
    assert set(p["tipo_cotista"]) == {"PF", "FUNDO DA CASA"}
    m = pc.saldo_medio(p).set_index(["classe", "cotista"])
    joao = m.loc[("MEZANINO A", "JOAO")]
    assert joao["saldo_medio"] == pytest.approx(110.0)
    assert (joao["saldo_inicial"], joao["saldo_final"], joao["saldo_maximo"]) == (100.0, 120.0, 120.0)
    limaj = m.loc[("MEZANINO A", "LIMAJ FIDC")]
    assert limaj["saldo_medio"] == pytest.approx(200.0)          # 600 / 3 datas
    assert limaj["saldo_inicial"] == 0.0 and limaj["ranking_classe"] == 1
    assert m.loc[("SÊNIOR I", "JOAO"), "datas_classe"] == 1

    # o mesmo CPF com e sem pontuacao e um investidor so
    inv = pc.investidores(pc.saldo_medio(p))
    joao_tot = inv[inv["cotista"] == "JOAO"].iloc[0]
    assert joao_tot["classes"] == 2 and joao_tot["saldo_medio"] == pytest.approx(160.0)
    assert inv["ranking"].tolist() == [1, 2] and inv["cotista"].iloc[0] == "LIMAJ FIDC"

    pl = pc.pl_diario(p).set_index(["fundo", "classe"])
    assert pl.loc[("MULTIPLICA FIDC", "MEZANINO A"), "02/10"] == 410.0


def test_main_ponta_a_ponta_com_banco_falso(tmp_path, monkeypatch, capsys):
    from liquidez import db

    alvos = tmp_path / "alvos.csv"
    alvos.write_text("fundo;classe\nMULTIPLICA FIDC;MEZANINO A\nLIMAJ FIDC;SENIOR 1\n"
                     "MULTIPLICA FIDC;MEZANINO D\nEXPLORER FIDC NP;SENIOR 2\n", encoding="utf-8")
    bruto = _bruto()
    chamadas = []

    def falso(sql, params=()):
        chamadas.append((sql, params))
        if "HOD_TB_CADASTRO_FUNDOS" in sql and "PASSIVO" not in sql:
            return CADASTRO
        if "SELECT DISTINCT" in sql:
            assert params == (dt.date(2026, 10, 1), dt.date(2026, 11, 1))
            return pd.DataFrame({"data": [dt.date(2026, 10, 1), dt.date(2026, 10, 2),
                                          dt.date(2026, 10, 5)]})
        assert "datalake.dbo.MUL_GLD_PASSIVO_CONSOLIDADO" in sql and "x.rn = 1" in sql
        assert "f.idFundoInterno IN (?, ?, ?)" in sql
        data, *ids = params
        assert sorted(ids) == [2, 4, 5]
        return bruto[bruto["data"] == pd.Timestamp(data)]

    monkeypatch.setattr(db, "consultar", falso)
    saida = tmp_path / "out.xlsx"
    assert pc.main(["--mes", "2026-10", "--fundos", str(alvos), "--saida", str(saida)]) == 0

    abas = pd.read_excel(saida, sheet_name=None)
    assert list(abas) == ["leia-me", "classes", "principais_investidores", "saldo_medio",
                          "posicoes_diarias", "pl_diario"]
    assert len(abas["posicoes_diarias"]) == 6
    st = abas["classes"].set_index("classe_pedida")["status"]
    assert st["MEZANINO A"] == "OK" and st["MEZANINO D"] == "CLASSE NAO ENCONTRADA"
    assert st["SENIOR 2"] == "SEM POSICAO NO MES"

    # a tela e para colar de volta: nada de cotista nem documento
    tela = capsys.readouterr().out
    assert "JOAO" not in tela and "123.456" not in tela and "12345678901" not in tela
    assert "CLASSE NAO ENCONTRADA" in tela and "existe: MEZANINO A | MEZANINO B | SENIOR" in tela
