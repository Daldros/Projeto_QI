"""`carteira resumo`: agrupa por Base_Origem x Modalidade e mostra exemplos."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez.diagnostico import distribuicao, formatar, resumir_carteira  # noqa: E402


def _carteira():
    return pd.DataFrame({
        "Base_Origem": ["Renda Fixa", "Renda Fixa", "Renda Fixa", "TESOURARIA"],
        "Modalidade": ["NC", "NC", "CCB", None],
        "Codigo_Op": ["C1", "C1", "C2", None],
        "Codigo_Ativo": ["NC001", "NC001", "CCB01", "CX"],
        "Nome_Ativo": ["MEZAN", "MEZAN", "SRP", "Saldo Tesouraria"],
        "Emissor": ["A", "A", "B", None],
        "Valor_Liquido": [100.0, 50.0, 30.0, 20.0],
        "Status": ["ATIVO", "ATIVO", "ATIVO", None],
    })


def test_resumo_agrupa_e_ordena_por_valor():
    resumo = resumir_carteira(_carteira())
    assert [(l["Base_Origem"], l["Modalidade"]) for l in resumo] == [
        ("Renda Fixa", "NC"), ("Renda Fixa", "CCB"), ("TESOURARIA", "(nulo)"),
    ]
    nc = resumo[0]
    assert nc["linhas"] == 2 and nc["operacoes"] == 1 and nc["valor"] == 150.0
    assert nc["ex_Codigo_Op"] == "C1" and nc["ex_Nome_Ativo"] == "MEZAN"
    assert resumo[2]["operacoes"] == 0 and resumo[2]["ex_Codigo_Op"] == "-"


def test_formatar_mostra_totais_e_distribuicoes():
    df = _carteira()
    texto = formatar(resumir_carteira(df), df)
    assert "Linhas ........ 4" in texto
    assert "Sem Codigo_Op . 1" in texto
    assert "Renda Fixa | NC" in texto
    assert "Status: ATIVO=3 | (nulo)=1" in texto
    assert distribuicao(df, "Inexistente") == []


def test_resumo_vazio():
    assert resumir_carteira(pd.DataFrame()) == []


def test_cobertura_estoque_casa_por_prefixo():
    from liquidez.diagnostico import cobertura_estoque, formatar_cobertura

    carteira = pd.DataFrame({
        "Base_Origem": ["Renda Fixa", "DC", "DC", "TESOURARIA"],   # crua: o de-para vira DC
        "Codigo_Op": ["C1000", "C1000", "C2000", "A"],
        "Codigo_Ativo": ["DIRPX", "PDDNC", "NCPX", "Saldo"],
        "Valor_Liquido": [80.0, -10.0, 50.0, 5.0],
    })
    bd = pd.DataFrame({
        "ID_Recebivel": ["C1000_1", "C1000_2", "C9999_1"],
        "Valor_Presente": [40.0, 35.0, 1.0],
        "Tipo_Recebivel": ["Duplicata", "Duplicata", "CCB"],
        "Status_Recebivel": ["A vencer", "Vencido", "A vencer"],
    })
    r = cobertura_estoque(carteira, bd)
    assert r["casamento"] == "prefixo antes de '_'"
    por_fam = {f["familia"]: f for f in r["familias"]}
    assert por_fam["DIRPX"]["casadas"] == 1 and por_fam["DIRPX"]["valor_presente_bd"] == 75.0
    assert por_fam["DIRPX"]["recebiveis"] == 2
    assert por_fam["NCPX"]["casadas"] == 0
    texto = formatar_cobertura(r)
    assert "TOTAL" in texto and "Duplicata=2" in texto and "C1000_1" in texto


def test_cobertura_estoque_sem_bd():
    from liquidez.diagnostico import cobertura_estoque

    carteira = pd.DataFrame({"Base_Origem": ["DC"], "Codigo_Op": ["C1"], "Codigo_Ativo": ["SRP"], "Valor_Liquido": [1.0]})
    r = cobertura_estoque(carteira, pd.DataFrame())
    assert r["casamento"] == "nenhum" and r["familias"][0]["casadas"] == 0
