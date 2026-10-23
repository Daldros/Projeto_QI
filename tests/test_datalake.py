"""Leitura direta do banco datalake (sem banco): nomes das tabelas, serie de
cotas pela MEC e o `datalake mapear`."""

from __future__ import annotations

import importlib
import logging
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez import cli, db, queries as q, varcota as vc  # noqa: E402
from liquidez import mapa_datalake as md  # noqa: E402

SCRIPTS_RAIZ = ("analise_passivo.py", "grafo_fundo.py", "grafo_passivo.py", "passivo_gestor.py")


def test_tabelas_padrao_estao_no_banco_datalake(monkeypatch):
    monkeypatch.delenv("LIQUIDEZ_BANCO_DATALAKE", raising=False)
    tb = importlib.reload(importlib.import_module("liquidez.tabelas"))
    assert tb.CARTEIRA == "datalake.dbo.MUL_GLD_CARTEIRA_UNIFICADA"
    assert tb.PASSIVO == "datalake.dbo.MUL_GLD_PASSIVO_CONSOLIDADO"
    assert tb.MEC == "datalake.dbo.MUL_GLD_MEC_CONSOLIDADA"
    assert tb.DEPARA_FUNDOS == "datalake.dbo.DLK_DEPARA_FUNDOS"


def test_nome_do_banco_vem_do_ambiente_e_e_validado(monkeypatch):
    modulo = importlib.import_module("liquidez.tabelas")
    try:
        monkeypatch.setenv("LIQUIDEZ_BANCO_DATALAKE", "datalake_hml")
        assert importlib.reload(modulo).CARTEIRA == "datalake_hml.dbo.MUL_GLD_CARTEIRA_UNIFICADA"
        # entra no texto do SQL: nada alem de identificador simples
        monkeypatch.setenv("LIQUIDEZ_BANCO_DATALAKE", "datalake; DROP TABLE x")
        with pytest.raises(ValueError):
            importlib.reload(modulo)
    finally:
        monkeypatch.delenv("LIQUIDEZ_BANCO_DATALAKE", raising=False)
        importlib.reload(modulo)


def test_nada_le_mais_os_clones_do_riscogc_nem_a_hod_de_cotas():
    arquivos = sorted((RAIZ / "liquidez").glob("*.py")) + [RAIZ / a for a in SCRIPTS_RAIZ]
    for arquivo in arquivos:
        texto = arquivo.read_text(encoding="utf-8")
        # toda MUL_ passa por liquidez/tabelas.py (datalake.dbo.*)
        assert not re.search(r"(?<!datalake\.)dbo\.MUL_", texto), arquivo.name
    for sql in (vc.CONSULTA, vc.CONSULTA_SEM_DEPARA, vc.CONSULTA_CADASTRO,
                *(s for _, s in q.SQL_ATUALIDADE)):
        assert "HOD_TB_COTAS_FUNDOS" not in sql


def test_carteira_vem_do_datalake_sem_a_coluna_do_clone():
    for sql, _ in (q.carteira_unificada("x", "2026-10-21"), q.db_ativo("x", "2026-10-21")):
        assert "FROM datalake.dbo.MUL_GLD_CARTEIRA_UNIFICADA AS cu" in sql
        assert "dtCarga" not in sql          # so existe no clone do riscogc


# --- serie de cotas pela MEC ----------------------------------------------------

CADASTRO = pd.DataFrame(
    [("SIN_MULT FIDC_SEN", "ORIGINAL", "23.216.398/0001-01"),
     ("SIN_MULT FIDC_SEN_GER", "SENIOR", "23.216.398/0001-01"),
     ("SIN_MULT FIDC_SUB", "Subordinada", "23.216.398/0001-01"),
     ("PLA_ADV FIM_UNICA", "UNICA", "11.111.111/0001-11"),
     ("PLA_ADV FIM_UNICA_IND", "UNICA OLD", None),
     ("PLA_SERRA FII", "ORIGINAL", "22.222.222/0001-22"),
     ("SIN_SERRA FII", "ORIGINAL", "22.222.222/0001-22")],
    columns=["idFundoInterno", "nmCotaInterno", "CNPJFundo"])


def _mec():
    linhas = []
    for dia in ("2026-10-20", "2026-10-21"):
        linhas += [
            (dia, "SIN_MULT FIDC_SEN", 1000.0, 1.5, 1500.0),       # ORIGINAL -> classe da _GER
            (dia, "SIN_MULT FIDC_SUB", 10.0, 2.0, 20.0),
            (dia, "PLA_ADV FIM_UNICA_IND", 5.0, 100.0, 500.0),     # de-para na variante
            (dia, "PLA_SERRA FII", 4.0, 25.0, 100.0),              # mesmo fundo em dois idFundo
            (dia, "SIN_SERRA FII", 4.0, 25.0, 100.0),
            (dia, "XXX_ORFAO", 1.0, 1.0, 1.0),                     # sem cadastro
        ]
    linhas.append(("2026-10-21", "SIN_MULT FIDC_SUB_GER", 10.0, 2.0, 999.0))  # nao fecha com o PL
    return pd.DataFrame(linhas, columns=["Data", "idFundo", "qtCotas", "valorCota", "valorPL"])


@pytest.fixture
def mec_falsa(monkeypatch):
    chamadas = []

    def falso(sql, params=()):
        chamadas.append((sql, params))
        if sql is vc.CONSULTA:
            return _mec()
        if sql is vc.CONSULTA_SEM_DEPARA:
            return pd.DataFrame({"sem_depara": [3]})
        if sql is vc.CONSULTA_CADASTRO:
            return CADASTRO.copy()
        raise AssertionError(f"consulta inesperada: {sql[:60]}")

    monkeypatch.setattr(db, "consultar", falso)
    return chamadas


def test_serie_de_cotas_vem_da_mec_pelo_de_para(mec_falsa, caplog):
    with caplog.at_level(logging.INFO, logger="liquidez.varcota"):
        s = vc.extrair("2026-10-21", dias=30)
    # janela nas duas consultas do datalake
    assert mec_falsa[0][1] == ("2026-09-21", "2026-10-21") == mec_falsa[1][1]
    assert "datalake.dbo.MUL_GLD_MEC_CONSOLIDADA" in vc.CONSULTA
    assert "datalake.dbo.DLK_DEPARA_FUNDOS" in vc.CONSULTA
    assert "ORDER BY m.dhAlteracao DESC, m.id DESC" in vc.CONSULTA     # como a carga da HOD

    classes = s.groupby("cnpj_fundo")["classe"].agg(lambda c: sorted(set(c))).to_dict()
    assert classes == {"23216398000101": ["SENIOR", "SUBORDINADA"],
                       # a variante _IND herda a classe da base, nao o "OLD" dela
                       "11111111000111": ["UNICA"],
                       "22222222000122": ["ORIGINAL"]}
    # SERRA: dois idFundo na mesma classe e data -> um so (PL nao dobra)
    serra = s[s["cnpj_fundo"] == "22222222000122"]
    assert len(serra) == 2 and serra["valor_pl"].sum() == 200.0
    # a linha que nao fecha qt x cota = PL saiu; o orfao tambem
    assert len(s) == 8 and set(s["fonte_cota"]) == {"PATRIMONIAL"}
    texto = caplog.text
    assert "3 carteira(s) da MEC" in texto and "XXX_ORFAO" in texto
    assert "nao fecham qtCotas x cota = PL" in texto
    assert "sem cota indexada" in texto


def test_classe_da_base_segue_a_ordem_do_cadastro():
    rotulos = {"A": "ORIGINAL", "A_GER": "SENIOR 2", "A_IND": "SENIOR 2 OLD",
               "B": "ORIGINAL", "B_IND": "MEZANINO", "C": "ORIGINAL"}
    assert vc._classe_da_base("A_IND", rotulos) == "SENIOR 2"
    assert vc._classe_da_base("B", rotulos) == "MEZANINO"
    assert vc._classe_da_base("X FIDC_SUB2", {}) == "SUBORDINADA 2"     # sufixo do idFundo
    assert vc._classe_da_base("C", rotulos) == "ORIGINAL"               # fica visivel, nao inventa


# --- datalake mapear ------------------------------------------------------------

def test_tipo_e_coluna_de_data():
    assert md.tipo_coluna("varchar", 50, 0, 0) == "varchar(50)"
    assert md.tipo_coluna("nvarchar", -1, 0, 0) == "nvarchar(max)"
    assert md.tipo_coluna("nvarchar", 510, 0, 0) == "nvarchar(255)"
    assert md.tipo_coluna("decimal", 17, 38, 8) == "decimal(38,8)"
    assert md.tipo_coluna("datetime2", 8, 27, 7) == "datetime2(7)"
    cols = pd.DataFrame({"coluna": ["dhInsercao", "dtCarga", "dtVencimento", "dtReferencia"],
                         "tipo": ["datetime2", "datetime2", "date", "date"]})
    assert md.escolher_data(cols) == "dtReferencia"
    assert md.escolher_data(cols.iloc[:3]) == "dtVencimento"      # auditoria nao serve
    assert md.escolher_data(cols.iloc[:2]) is None


OBJETOS = pd.DataFrame(
    [("MUL_GLD_ESTOQUE", "dbo", "U ", "2026-10-01", "2026-10-22", 50_000_000, None),
     ("MUL_GLD_COTISTAS", "dbo", "U ", "2026-10-01", "2026-10-22", 8_000, None),
     ("MUL_GLD_COTISTAS_ADM", "dbo", "SN", "2026-10-01", "2026-10-01", None, "[dbo].[MUL_SLV_COTISTAS_ADM]"),
     ("MUL_GLD_ESTOQUE_BKP", "dbo", "U ", "2026-01-01", "2026-01-01", 10, None),
     ("QIT_GLD_MOVIMENTACAO_FUNDOS_SINGULARE", "dbo", "U ", "2026-01-01", "2026-10-22", 900, None)],
    columns=["objeto", "esquema", "tipo", "criado", "alterado", "linhas", "sinonimo_de"])
COLUNAS = pd.DataFrame(
    [("MUL_GLD_ESTOQUE", 1, "dtReferencia", "date", 3, 10, 0, 0),
     ("MUL_GLD_ESTOQUE", 2, "vlPresente", "decimal", 9, 18, 2, 1),
     ("MUL_GLD_ESTOQUE", 3, "cdRecebivel", "varchar", 50, 0, 0, 1),
     ("MUL_GLD_COTISTAS", 1, "dtReferencia", "date", 3, 10, 0, 0),
     ("MUL_GLD_COTISTAS", 2, "dsNomeCotista", "nvarchar", 510, 0, 0, 1),
     ("QIT_GLD_MOVIMENTACAO_FUNDOS_SINGULARE", 1, "vlResgate", "decimal", 9, 18, 2, 1)],
    columns=["objeto", "ordem", "coluna", "tipo", "tamanho", "precisao", "escala", "nula"])


def _catalogo_falso(datas_varridas):
    def falso(sql, params=()):
        if sql is md.SQL_CONEXAO:
            return pd.DataFrame([{"servidor": "SRV", "banco": "riscogc", "usuario": "u", "acesso_datalake": 1,
                                  "collation_banco": "Latin1_General_CS_AS",
                                  "collation_datalake": "SQL_Latin1_General_CP1_CI_AS"}])
        if sql is md.SQL_OBJETOS:
            return OBJETOS.copy()
        if sql is md.SQL_COLUNAS:
            return COLUNAS.copy()
        if sql is md.SQL_INDICES:
            return pd.DataFrame({"objeto": ["MUL_GLD_ESTOQUE"], "coluna": ["dtReferencia"]})
        if sql.startswith("SELECT MIN("):
            datas_varridas.append(sql)
            return pd.DataFrame([{"de": "2026-01-02", "ate": "2026-10-21", "datas": None, "ultimo": 61_000}])
        if sql is md.SQL_PASSIVO_CLASSES:
            raise RuntimeError("The SELECT permission was denied\nmais")
        raise AssertionError(f"consulta inesperada: {sql[:60]}")
    return falso


def test_mapear_levanta_o_catalogo_e_nao_para_em_falha(monkeypatch):
    varridas: list[str] = []
    monkeypatch.setattr(db, "consultar", _catalogo_falso(varridas))
    texto = "\n".join(md.mapear(fichas=(), ate="2026-10-21"))

    for n in range(1, 9):
        assert f"\n== {n}. " in texto
    assert "acesso: SIM" in texto
    assert "MUL_GLD_COTISTAS_ADM" in texto and "[dbo].[MUL_SLV_COTISTAS_ADM]" in texto
    assert "dtReferencia date NOT NULL, vlPresente decimal(18,2), cdRecebivel varchar(50)" in texto
    # 50 mi de linhas com indice na data: le; sem COUNT(DISTINCT) (so em tabela pequena)
    assert any("datalake.[dbo].[MUL_GLD_ESTOQUE]" in s and "COUNT(DISTINCT" not in s for s in varridas)
    assert any("[MUL_GLD_COTISTAS]" in s and "COUNT(DISTINCT" in s for s in varridas)
    assert "61.000" in texto
    # candidatas: o backup fica fora; a movimentacao da Singulare aparece com colunas
    assert "MUL_GLD_ESTOQUE_BKP" not in texto.split("== 5.")[1]
    assert "QIT_GLD_MOVIMENTACAO_FUNDOS_SINGULARE [U] 900 linhas" in texto
    assert "PRESENTE    MUL_GLD_ESTOQUE.vlPresente" in texto
    assert "(cadastro local vazio" in texto
    assert "(falhou: The SELECT permission was denied)" in texto.split("== 8.")[1]


def test_passivo_do_mapa_nao_le_cotista():
    sql = md.SQL_PASSIVO_CLASSES
    assert "datalake.dbo.MUL_GLD_PASSIVO_CONSOLIDADO" in sql
    for coluna in ("dsNomeCotista", "docCotista", "cdCotista", "vlCorrigido"):
        assert coluna not in sql


def test_parte_passivo_compara_com_o_cadastro():
    passivo = pd.DataFrame(
        [("101", "SINGULARE", "23.216.398/0001-01", "23.216.398/0001-01", "F", "SENIOR", "SENIOR", "2026-10-21", 9),
         ("102", "SINGULARE", None, None, "F", "SUB", "SUB", "2026-10-21", 3),
         ("103", "PLANNER", "1", "99.999.999/0001-99", "G", "UNICA", "UNICA", "2026-10-21", 2),
         ("900", "BTG", "1", "1", "H", "X", "X", "2026-10-21", 1)],
        columns=["cdFundo", "administrador", "doc_min", "doc_max", "dsNomeFundo", "classe_min", "classe_max",
                 "data", "linhas"])
    cadastro = pd.DataFrame(
        [("101", "23216398000101", "MULTIPLICA FIDC", "ORIGINAL", "NORMAL"),
         ("103", "11.111.111/0001-11", "OUTRO FIDC", "UNICA", "NORMAL")],
        columns=["idFundoAdm", "CNPJFundo", "nmFundoInterno", "nmCotaInterno", "Situacao"])

    def falso(sql, params=()):
        return passivo.copy() if sql is md.SQL_PASSIVO_CLASSES else cadastro.copy()

    texto = "\n".join(md.parte_passivo(falso))
    assert "Classes (cdFundo x administrador): 4, das quais 3 de SINGULARE/PLANNER" in texto
    assert "com linha no cadastro 2; sem 1" in texto
    assert "docFundo: vazio em 1; muda dentro da classe em 1; igual ao CNPJFundo do cadastro em 1 de 2." in texto
    assert "cdFundo 103" in texto and "OUTRO FIDC" in texto
    assert "ORIGINAL" in texto and "SENIOR" in texto


def test_cli_troca_passivo_comparar_por_datalake_mapear():
    parser = cli.construir_parser()
    args = parser.parse_args(["datalake", "mapear", "--ate", "2026-10-21"])
    assert args.func is cli.cmd_datalake_mapear and args.ate == "2026-10-21"
    with pytest.raises(SystemExit):
        parser.parse_args(["passivo", "comparar", "--data", "2026-10-21"])
