"""Semeia config/parametros/fundos_overrides.csv com pz_resg/cc do bi/dim_fundo
gerado pelo gestor (a config verdadeira, levantada regulamento por regulamento).

    python ferramentas/semear_overrides.py <pasta bi>   # ex.: caminho de bi/dim_fundo

Le bi/dim_fundo/dados.parquet e grava um override por fundo. O parquet NAO fica
no repositorio; o CSV resultante fica (sao so prazos por CNPJ).
"""
import csv
import sys
from pathlib import Path

import pandas as pd

bi = Path(sys.argv[1] if len(sys.argv) > 1 else "bi")
df = pd.read_parquet(bi / "dim_fundo" / "dados.parquet")
saida = Path("config/parametros/fundos_overrides.csv")
with saida.open("w", encoding="utf-8-sig", newline="") as arq:
    w = csv.writer(arq, delimiter=";")
    w.writerow(["cnpj", "pz_resg", "cc", "codinome_beyond", "nome_produto_mov", "ativo", "observacao"])
    for _, l in df.sort_values("cnpj_fundo").iterrows():
        pz = "" if pd.isna(l.get("pz_resg")) else int(l["pz_resg"])
        cc = "" if pd.isna(l.get("cc")) else int(l["cc"])
        w.writerow([l["cnpj_fundo"], pz, cc, "", "", "", "bi/dim_fundo do gestor"])
print(saida, len(df))
