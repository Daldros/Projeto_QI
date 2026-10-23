"""Regra de vencidos: criterio do administrador OU data de vencimento no passado."""
import json
from pathlib import Path

import pandas as pd
import pytest

from liquidez.regras_vencidos import CatalogoRegras, Condicao, ErroRegra, RegraVencidos
from liquidez.tratamentos import marcar_ativo_vencido

RAIZ = Path(__file__).resolve().parents[1]

VC = Condicao("Codigo_Ativo", "comeca_com", "VC")
QTD = Condicao("Qtd_Total", "maior_que", 0)
DATA = Condicao("Data_Vencimento", "data_anterior_a", "Data_Posicao")


def _carteira():
    return pd.DataFrame({
        "cnpj_fundo": ["F"] * 5,
        "Emissor": ["A", "A", "B", "C", "D"],
        "Codigo_Ativo": ["VCDEB", "NCPX", "NCPX", "NCPX", "TESOU"],
        "Qtd_Total": [10, 10, 10, 10, 0],
        "Data_Posicao": ["2026-10-01"] * 5,
        # A: reclassificado (VC). B: venceu ontem, ainda sem VC. C: a vencer. D: sem data.
        "Data_Vencimento": ["2026-09-01", "2027-01-01", "2026-09-30", "2026-10-15", None],
    })


def test_operador_data_anterior_a_compara_as_duas_colunas():
    assert DATA.avaliar(_carteira()).tolist() == [True, False, True, False, False]


def test_data_ausente_nao_marca():
    assert not DATA.avaliar(_carteira()).iloc[4]


def test_coluna_de_referencia_ausente_ignora_a_condicao():
    df = _carteira().drop(columns=["Data_Posicao"])
    assert not DATA.avaliar(df).any()


def test_so_prefixo_deixa_passar_o_vencido_por_data():
    regra = RegraVencidos("QI", "", "Emissor", (VC, QTD))
    assert regra.selecionar(_carteira()).tolist() == [True, False, False, False, False]


def test_alternativa_por_data_soma_ao_prefixo():
    regra = RegraVencidos("QI", "", "Emissor", (VC, QTD), alternativas=((DATA,),))
    assert regra.selecionar(_carteira()).tolist() == [True, False, True, False, False]


def test_carrego_por_emissor_alcanca_as_linhas_a_vencer_do_mesmo_emissor():
    regra = RegraVencidos("QI", "", "Emissor", (VC, QTD), alternativas=((DATA,),))
    marca = marcar_ativo_vencido(_carteira(), regra)
    # A inteiro (reclassificado), B inteiro (venceu por data); C e D ficam.
    assert marca.tolist() == ["SIM", "SIM", "SIM", "NAO", "NAO"]


def test_descrever_mostra_os_grupos_com_ou():
    regra = RegraVencidos("QI", "", "Emissor", (VC, QTD), alternativas=((DATA,),))
    texto = regra.descrever()
    assert "ou" in texto and any("Data_Posicao" in t for t in texto)


def test_regra_so_com_alternativas_e_definida():
    assert RegraVencidos("X", "", "Emissor", (), alternativas=((DATA,),)).definida


def test_catalogo_versionado_carrega_a_alternativa_da_qi():
    cat = CatalogoRegras.carregar(RAIZ / "config" / "parametros" / "regras_vencidos.json")
    qi = cat.obter("QI CORRETORA DE TITULOS E VALORES MOBILIARIOS")
    assert qi.alternativas and qi.alternativas[0][0].operador == "data_anterior_a"
    assert cat.obter("PLANNER").alternativas == ()


def test_grupo_alternativo_vazio_e_erro(tmp_path):
    cfg = {"X": {"coluna_emissor": "Emissor",
                 "condicoes": [{"coluna": "Codigo_Ativo", "operador": "comeca_com", "valor": "VC"}],
                 "alternativas": [[]]}}
    caminho = tmp_path / "r.json"
    caminho.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ErroRegra):
        CatalogoRegras.carregar(caminho)
