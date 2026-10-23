"""Testes de fumaca: o pacote importa, aponta para o banco certo e os scripts
da raiz so chamam funcoes que existem. Nao acessam banco nem rede."""

from __future__ import annotations

import ast
import importlib
import pkgutil
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import liquidez  # noqa: E402

MODULOS = sorted(m.name for m in pkgutil.iter_modules(liquidez.__path__) if m.name != "__main__")


@pytest.mark.parametrize("nome", MODULOS)
def test_modulo_importa(nome):
    importlib.import_module(f"liquidez.{nome}")


def test_banco_padrao_e_o_riscogc_de_producao(monkeypatch):
    monkeypatch.delenv("LIQUIDEZ_SERVIDOR", raising=False)
    monkeypatch.delenv("LIQUIDEZ_BANCO", raising=False)
    db = importlib.reload(importlib.import_module("liquidez.db"))
    assert db.SERVIDOR == "10.101.1.18,1433" and db.BANCO == "riscogc"
    assert "01000" not in db.ESTADOS_CONEXAO


def _chamadas_de(arquivo: Path, alias: str) -> set[str]:
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    return {n.attr for n in ast.walk(arvore)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == alias}


def _definidos_em(arquivo: Path) -> set[str]:
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    nomes = set()
    for n in arvore.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            nomes.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                alvos = t.elts if isinstance(t, ast.Tuple) else [t]
                nomes |= {a.id for a in alvos if isinstance(a, ast.Name)}
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            nomes |= {(a.asname or a.name).split(".")[0] for a in n.names}
    return nomes


def test_analise_passivo_so_usa_funcoes_que_existem_no_grafo_fundo():
    usados = _chamadas_de(RAIZ / "analise_passivo.py", "gf")
    faltando = usados - _definidos_em(RAIZ / "grafo_fundo.py")
    assert not faltando, f"analise_passivo.py chama gf.{sorted(faltando)} que nao existe(m)"


def test_conversor_datetimeoffset():
    """05/10: extrair parou em 'ODBC SQL type -155 is not yet supported' (movimentacao)."""
    import struct
    from datetime import timedelta

    from liquidez import db

    bruto = struct.pack("<6hI2h", 2026, 10, 2, 15, 41, 45, 123_000_000, -3, 0)
    valor = db.ler_datetimeoffset(bruto)
    assert (valor.year, valor.month, valor.day, valor.hour, valor.microsecond) == (2026, 10, 2, 15, 123_000)
    assert valor.utcoffset() == timedelta(hours=-3)
    assert db.ler_datetimeoffset(None) is None
    assert db.SQL_DATETIMEOFFSET == -155


def test_gravar_parquet_com_coluna_mista(tmp_path):
    """05/10: tratar --salvar falhava ao gravar a matriz de faixas (rotulos + dias na mesma coluna)."""
    import pandas as pd

    from liquidez.extrator import _gravar

    matriz = pd.DataFrame({"DC": [1.0, 2.0, 3.0]}, index=pd.Index([0, 1, ">cc"], name="Prazos"))
    caminho = _gravar(matriz.reset_index(names="Prazos"), tmp_path / "analise", "parquet")
    lido = pd.read_parquet(caminho)
    assert lido["Prazos"].tolist() == ["0", "1", ">cc"] and lido["DC"].tolist() == [1.0, 2.0, 3.0]
    # coluna numerica pura e texto puro nao mudam
    df = pd.DataFrame({"n": [1, 2], "t": ["a", "b"]})
    assert pd.read_parquet(_gravar(df, tmp_path / "simples", "parquet"))["n"].tolist() == [1, 2]


def test_escopo_bullet_aceita_os_nomes_do_cadastro_do_riscogc():
    """05/10: o cadastro do riscogc escreve SINGULARE/QI TECH; a CVM, QI CORRETORA."""
    from liquidez import bullets as bl

    for nome in ("SINGULARE", "QI TECH", "QI CORRETORA DE TITULOS E VALORES MOBILIARIOS"):
        assert bl.e_administrador_bullet(nome), nome
    assert not bl.e_administrador_bullet("PLANNER")
