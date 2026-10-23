"""Tabelas que a liquidez le direto do banco datalake.

Desde 23/10/2026 tudo o que tem tabela no datalake e lido de la, pelo nome em
tres partes (`datalake.dbo.TABELA`), na mesma conexao do riscogc: os dois
bancos estao no mesmo servidor (srvSqlPrd01) e o login le o datalake. Antes a
carteira e o passivo vinham dos CLONES que a rotina do projeto Datalake refaz
no riscogc, e a serie de cotas da HOD_TB_COTAS_FUNDOS, que a mesma rotina
recarrega a partir da MEC. Quando a rotina nao rodava, a liquidez parava junto
(logs com buracos de 10 a 14/10 e em 17 e 18/10/2026).

O que segue no riscogc e so o que ainda nao tem fonte conhecida no datalake:
estoque, cadastro de fundos e movimentacao (HOD_*), Beyond, curva, CDI,
distribuidores, feriados e o de-para do Beyond. `python -m liquidez datalake
mapear` procura as equivalentes.

O nome do banco vem de LIQUIDEZ_BANCO_DATALAKE (padrao `datalake`), como o
servidor e o banco principal em `db.py`.
"""

from __future__ import annotations

import os
import re

BANCO_DATALAKE = os.getenv("LIQUIDEZ_BANCO_DATALAKE", "datalake")

# Entra no texto do SQL (nome de objeto nao vai como parametro), entao so
# aceita identificador simples.
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", BANCO_DATALAKE):
    raise ValueError(f"LIQUIDEZ_BANCO_DATALAKE invalido: {BANCO_DATALAKE!r}")


def no_datalake(tabela: str) -> str:
    """Nome em tres partes de uma tabela do schema dbo do datalake."""
    return f"{BANCO_DATALAKE}.dbo.{tabela}"


CARTEIRA = no_datalake("MUL_GLD_CARTEIRA_UNIFICADA")
PASSIVO = no_datalake("MUL_GLD_PASSIVO_CONSOLIDADO")
# Cota e PL por carteira (`idCliente`). E a fonte da HOD_TB_COTAS_FUNDOS desde
# 10/09/2026 (usp_Carregar_HOD_TB_COTAS_FUNDOS, projeto Datalake).
MEC = no_datalake("MUL_GLD_MEC_CONSOLIDADA")
# De-para do proprio datalake: `cdCarteira` (= idCliente da MEC) ->
# `cdFundoInterno` (= idFundoInterno do cadastro de fundos).
DEPARA_FUNDOS = no_datalake("DLK_DEPARA_FUNDOS")
