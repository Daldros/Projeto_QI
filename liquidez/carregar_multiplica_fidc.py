"""Carrega as operações Beyond do Multiplica FIDC em um DataFrame pandas.

No banco, o fundo aparece dividido nos codinomes ``Multiplica LP`` e
``Multiplica LT``. Por padrão, a carga usa a data mais recente disponível para
os dois codinomes e mantém a coluna ``Fundo`` para distingui-los.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd

from . import db


CODINOMES = ("Multiplica LP", "Multiplica LT")
PASTA_DADOS = Path(__file__).with_name("dados")


def data_mais_recente() -> dt.date:
    resultado = db.consultar(
        """
        SELECT MAX(Data) AS Data
        FROM dbo.TB_LIQUIDEZ_OPERACOES_4
        WHERE Fundo IN (?, ?)
        """,
        CODINOMES,
    )
    data = resultado.iloc[0]["Data"]
    if pd.isna(data):
        raise RuntimeError("Não há registros para o Multiplica FIDC.")
    return pd.Timestamp(data).date()


def carregar(data: dt.date | str | None = None) -> pd.DataFrame:
    """Retorna as operações de LP e LT na data escolhida."""
    data_consulta = pd.Timestamp(data).date() if data else data_mais_recente()
    return db.consultar(
        """
        SELECT *
        FROM dbo.TB_LIQUIDEZ_OPERACOES_4
        WHERE Fundo IN (?, ?) AND Data = ?
        ORDER BY Fundo, Operacao, Titulo
        """,
        (*CODINOMES, data_consulta.isoformat()),
    )


def salvar(df: pd.DataFrame) -> Path:
    if df.empty:
        raise RuntimeError("A consulta não retornou registros.")
    PASTA_DADOS.mkdir(exist_ok=True)
    data = pd.Timestamp(df["Data"].iloc[0]).date().isoformat()
    destino = PASTA_DADOS / f"multiplica_fidc_{data}.parquet"
    df.to_parquet(destino, index=False, compression="zstd")
    return destino


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", help="Data da posição no formato AAAA-MM-DD")
    args = parser.parse_args()

    global df
    df = carregar(args.data)
    destino = salvar(df)
    print(f"Data: {pd.Timestamp(df['Data'].iloc[0]).date()}")
    print(f"Linhas: {len(df):,}")
    print(f"Colunas: {len(df.columns)}")
    print("Registros por codinome:")
    print(df.groupby("Fundo", dropna=False).size().to_string())
    print(f"Arquivo: {destino.resolve()}")
    print(f"Memória do DataFrame: {df.memory_usage(deep=True).sum() / 1024**2:.1f} MB")


if __name__ == "__main__":
    main()
