"""De-para de Base_Origem: a 'Renda Fixa' do riscogc vira o 'DC' que o pacote procura."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez.tratamentos import (  # noqa: E402
    COL_BASE_ORIGEM,
    COL_BASE_ORIGEM_FONTE,
    aplicar_depara_produtos,
    carregar_depara_produtos,
)

REGRAS_PADRAO = json.loads((RAIZ / "config" / "parametros" / "produtos.json").read_text("utf-8"))["depara_base_origem"]


def _carteira():
    return pd.DataFrame({
        "Base_Origem": ["Renda Fixa", "Renda Fixa", "Renda Fixa", "Outros Fundos", "TESOURARIA"],
        "Codigo_Op": ["C417693", "C427010", "LFT2027", "APLOIIMZ", "A"],
        "Codigo_Ativo": ["MEZAN", "PDDNC", "LFT", "APOLLO II FIDCNP MZ1", "Saldo Tesouraria"],
    })


def test_renda_fixa_com_codigo_de_operacao_vira_dc():
    df = aplicar_depara_produtos(_carteira(), REGRAS_PADRAO)
    assert df[COL_BASE_ORIGEM].tolist() == ["DC", "DC", "Renda Fixa", "Outros Fundos", "TESOURARIA"]
    # o rotulo de origem fica guardado e o DataFrame de entrada nao e alterado
    assert df[COL_BASE_ORIGEM_FONTE].tolist()[:2] == ["Renda Fixa", "Renda Fixa"]
    assert _carteira()[COL_BASE_ORIGEM].tolist()[0] == "Renda Fixa"


def test_sem_regras_ou_sem_coluna_nao_mexe():
    df = _carteira()
    assert aplicar_depara_produtos(df, []) is df
    sem_base = df.drop(columns=["Base_Origem"])
    assert aplicar_depara_produtos(sem_base, REGRAS_PADRAO) is sem_base


def test_regra_sem_regex_troca_o_rotulo_inteiro():
    df = aplicar_depara_produtos(_carteira(), [{"de": "tesouraria", "para": "Caixa"}])
    assert df[COL_BASE_ORIGEM].tolist()[-1] == "Caixa"


def test_carregar_cai_no_parametro_versionado(tmp_path):
    # sem config/produtos.json gerado, le a copia de config/parametros/
    regras = carregar_depara_produtos(tmp_path / "nao_existe.json")
    assert regras == REGRAS_PADRAO
    (tmp_path / "p.json").write_text(json.dumps({"depara_base_origem": [{"de": "X", "para": "Y"}]}), "utf-8")
    assert carregar_depara_produtos(tmp_path / "p.json") == [{"de": "X", "para": "Y"}]


def test_titulo_cdb_e_imovel_ficam_fora_do_dc():
    df = pd.DataFrame({
        "Base_Origem": ["Renda Fixa"] * 5,
        "Codigo_Op": ["C474875", "C474877", "C400001", "C400002", "C417693"],
        "Codigo_Ativo": ["LFTO", "NTN O", "CDBDI", "IMOVE", "NCPX"],
    })
    saida = aplicar_depara_produtos(df, REGRAS_PADRAO)
    assert saida[COL_BASE_ORIGEM].tolist() == ["Renda Fixa"] * 4 + ["DC"]


def test_familias_sem_conferencia():
    from liquidez.bullets import e_familia_bullet

    assert all(e_familia_bullet(c) for c in ("CCEXP", "PDDNC", "PDCCE", "PDCEX", "PDD_L", "MEZAN", "SRP"))
    assert not any(e_familia_bullet(c) for c in ("NCPX", "DIRPX", "CRDC", "CONFD", "VCNC", None))
