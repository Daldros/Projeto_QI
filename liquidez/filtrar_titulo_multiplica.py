"""Filtra um título do Multiplica FIDC na TB_LIQUIDEZ_OPERACOES_4."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import db


def carregar(titulo: int):
    return db.consultar(
        """
        SELECT *
        FROM dbo.TB_LIQUIDEZ_OPERACOES_4
        WHERE Fundo IN (?, ?) AND Titulo = ?
        ORDER BY Data DESC, Fundo, Operacao
        """,
        ("Multiplica LP", "Multiplica LT", titulo),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("titulo", type=int)
    args = parser.parse_args()
    df = carregar(args.titulo)

    pasta = Path(__file__).with_name("dados")
    pasta.mkdir(exist_ok=True)
    destino = pasta / f"multiplica_fidc_titulo_{args.titulo}.parquet"
    df.to_parquet(destino, index=False, compression="zstd")

    print(f"Linhas: {len(df):,}")
    print(f"Colunas: {len(df.columns)}")
    if not df.empty:
        print(f"Primeira data: {df['Data'].min()}")
        print(f"Última data: {df['Data'].max()}")
        print("Registros por fundo:")
        print(df.groupby("Fundo", dropna=False).size().to_string())
    print(f"Arquivo: {destino.resolve()}")


if __name__ == "__main__":
    main()
