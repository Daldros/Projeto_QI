"""Extracao das bases por fundo e em lote.

Substitui `CarregarAtivosPassivos` (que carregava as 5 bases de um fundo so) e
as macros `Consultar_*`.

Layout da saida: cada carga gera 6 arquivos, um por base, com todos os fundos e
as colunas `cnpj_fundo` / `nome_fundo` / `data_posicao_carga` na frente. Uma
carga substitui a anterior -- o banco continua sendo a fonte para reprocessar
qualquer data.

Durante o lote a extracao grava um arquivo por fundo em `_tmp/`, o que preserva
a retomada se a execucao cair no meio; ao final tudo e consolidado e o `_tmp/`
e apagado.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from . import db, queries
from .cadastro import Fundo

log = logging.getLogger(__name__)

BASES = ("bd", "carteira", "db_ativo", "db_passivo", "mov", "beyond")

COMPRESSAO = "zstd"

# Nome da aba correspondente na planilha, usado nas mensagens e na conferencia.
ABA_DE = {
    "bd": "BD",
    "carteira": "Carteira",
    "db_ativo": "DB_Ativo",
    "db_passivo": "DB_Passivo",
    "mov": "MOV",
    "beyond": "Beyond",
}

COLUNAS_IDENTIFICACAO = ("cnpj_fundo", "nome_fundo", "data_posicao_carga")


def _consulta_da_base(base: str, fundo: Fundo, data: dt.date | str):
    """Monta (sql, params) da base para o fundo, ou None se nao for aplicavel."""
    if base == "bd":
        return queries.estoque_unificado(fundo.cnpj, data)
    if base == "carteira":
        return queries.carteira_unificada(fundo.cnpj, data)
    if base == "db_ativo":
        return queries.db_ativo(fundo.cnpj, data)
    if base == "db_passivo":
        return queries.db_passivo(fundo.cnpj, data)
    if base == "mov":
        return queries.movimentacao(fundo.nome_produto_mov)
    if base == "beyond":
        if not fundo.codinome_beyond:
            return None
        return queries.beyond(fundo.codinome_beyond, data)
    raise ValueError(f"Base desconhecida: {base}")


def extrair_fundo(
    fundo: Fundo,
    data: dt.date | str,
    bases: tuple[str, ...] = BASES,
) -> dict[str, pd.DataFrame]:
    """Executa as consultas de um fundo e devolve {base: DataFrame}.

    A base `beyond` fica ausente quando o fundo nao tem codinome cadastrado --
    e aviso, nao erro: hoje so 3 fundos tem codinome.
    """
    resultado: dict[str, pd.DataFrame] = {}
    for base in bases:
        consulta = _consulta_da_base(base, fundo, data)
        if consulta is None:
            log.warning("%s: sem codinome_beyond, base 'beyond' pulada", fundo.nome_interno)
            continue
        sql, params = consulta
        inicio = time.perf_counter()
        df = db.consultar(sql, params)
        log.info(
            "%s | %-10s %6d linhas em %5.1fs",
            fundo.nome_interno, base, len(df), time.perf_counter() - inicio,
        )
        resultado[base] = df
    return resultado


# ---------------------------------------------------------------------------
# Gravacao
# ---------------------------------------------------------------------------

def _extensao(formato: str) -> str:
    return ".parquet" if formato == "parquet" else ".csv"


def _homogeneizar_objetos(df: pd.DataFrame) -> pd.DataFrame:
    """Colunas `object` com tipos misturados viram texto.

    A matriz de faixas tem rotulos (">cc", "Vencidos") e dias (0, 1, 2...) na
    mesma coluna; o parquet exige um tipo so e falhava com "Expected bytes, got
    a 'int' object" (05/10/2026, MULTIPLICA FIDC). Numeros puros e texto puro
    ficam como estao.
    """
    saida = df
    for coluna in df.columns:
        serie = df[coluna]
        if serie.dtype != object:
            continue
        tipos = {type(v) for v in serie.dropna()}
        if len(tipos) > 1 and not tipos <= {str}:
            if saida is df:
                saida = df.copy()
            saida[coluna] = serie.map(lambda v: None if pd.isna(v) else str(v))
    return saida


def _gravar(df: pd.DataFrame, destino_sem_extensao: Path, formato: str) -> Path:
    destino_sem_extensao.parent.mkdir(parents=True, exist_ok=True)
    caminho = destino_sem_extensao.with_suffix(_extensao(formato))
    if formato == "parquet":
        _homogeneizar_objetos(df).to_parquet(caminho, index=False, compression=COMPRESSAO)
    else:
        df.to_csv(caminho, index=False, sep=";", encoding="utf-8-sig")
    return caminho


def identificar(df: pd.DataFrame, fundo: Fundo, data: str) -> pd.DataFrame:
    """Insere as colunas de identificacao do fundo no inicio do DataFrame."""
    df = df.copy()
    for coluna, valor in zip(
        reversed(COLUNAS_IDENTIFICACAO), (data, fundo.nome_interno, fundo.cnpj)
    ):
        if coluna in df.columns:
            df = df.drop(columns=[coluna])
        df.insert(0, coluna, valor)
    return df


def _ler(caminho: Path) -> pd.DataFrame:
    if caminho.suffix == ".parquet":
        return pd.read_parquet(caminho)
    return pd.read_csv(caminho, sep=";", encoding="utf-8-sig")


def consolidar(
    saida: Path,
    temporarios: Path,
    bases: tuple[str, ...],
    formato: str,
) -> dict[str, dict]:
    """Junta os arquivos por fundo em um arquivo por base, na raiz da saida."""
    resumo: dict[str, dict] = {}
    for base in bases:
        arquivos = sorted(temporarios.glob(f"*/{base}{_extensao(formato)}"))
        partes = []
        for arquivo in arquivos:
            df = _ler(arquivo)
            if not df.empty:
                partes.append(df)
        destino = saida / base
        if partes:
            junto = pd.concat(partes, ignore_index=True)
        else:
            # Sem dados: grava vazio so com as colunas de identificacao, para o
            # arquivo da carga anterior nao ficar para tras.
            junto = pd.DataFrame(columns=list(COLUNAS_IDENTIFICACAO))
        caminho = _gravar(junto, destino, formato)
        resumo[base] = {
            "arquivo": caminho.name,
            "linhas": int(len(junto)),
            "fundos": int(junto["cnpj_fundo"].nunique()) if "cnpj_fundo" in junto else 0,
            "kb": round(caminho.stat().st_size / 1024, 1),
        }
        log.info(
            "consolidado %-11s %7d linhas de %3d fundo(s) -> %s (%.0f KB)",
            base, resumo[base]["linhas"], resumo[base]["fundos"],
            caminho.name, resumo[base]["kb"],
        )
    return resumo


def _limpar_carga_anterior(saida: Path, bases: tuple[str, ...], formato: str) -> None:
    """Remove arquivos de bases que nao serao regeradas nesta carga."""
    for base in BASES:
        if base in bases:
            continue
        for extensao in (".parquet", ".csv"):
            antigo = saida / f"{base}{extensao}"
            if antigo.exists():
                antigo.unlink()
                log.info("carga anterior: removido %s", antigo.name)


# ---------------------------------------------------------------------------
# Lote
# ---------------------------------------------------------------------------

def extrair_lote(
    fundos: list[Fundo],
    data: dt.date | str,
    saida: Path,
    bases: tuple[str, ...] = BASES,
    workers: int = 4,
    formato: str = "parquet",
    forcar: bool = False,
) -> tuple[list[dict], dict[str, dict]]:
    """Extrai varios fundos em paralelo e consolida a carga.

    Devolve (resumo por fundo/base, resumo por base consolidada).
    """
    data_str = queries._data(data)
    saida = Path(saida)
    temporarios = saida / "_tmp"
    if forcar and temporarios.exists():
        shutil.rmtree(temporarios, ignore_errors=True)
    resumo: list[dict] = []

    def processar(fundo: Fundo) -> list[dict]:
        linhas: list[dict] = []
        for base in bases:
            destino = temporarios / fundo.cnpj_numerico / f"{base}{_extensao(formato)}"
            if destino.exists():
                linhas.append({
                    "fundo": fundo.nome_interno, "cnpj": fundo.cnpj, "base": base,
                    "linhas": "", "segundos": "", "status": "pulado", "erro": "",
                })
                continue
            consulta = _consulta_da_base(base, fundo, data)
            if consulta is None:
                linhas.append({
                    "fundo": fundo.nome_interno, "cnpj": fundo.cnpj, "base": base,
                    "linhas": 0, "segundos": 0, "status": "sem_codinome", "erro": "",
                })
                continue
            sql, params = consulta
            inicio = time.perf_counter()
            try:
                df = db.consultar(sql, params)
                _gravar(identificar(df, fundo, data_str), destino.with_suffix(""), formato)
                linhas.append({
                    "fundo": fundo.nome_interno, "cnpj": fundo.cnpj, "base": base,
                    "linhas": len(df), "segundos": round(time.perf_counter() - inicio, 1),
                    "status": "ok", "erro": "",
                })
                del df
            except Exception as exc:  # noqa: BLE001 - um fundo com erro nao derruba o lote
                log.error("%s | %s falhou: %s", fundo.nome_interno, base, exc)
                linhas.append({
                    "fundo": fundo.nome_interno, "cnpj": fundo.cnpj, "base": base,
                    "linhas": 0, "segundos": round(time.perf_counter() - inicio, 1),
                    "status": "erro", "erro": str(exc)[:300],
                })
        return linhas

    log.info("Lote: %d fundos x %d bases, %d threads", len(fundos), len(bases), workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futuros = {executor.submit(processar, f): f for f in fundos}
        concluidos = 0
        for futuro in as_completed(futuros):
            fundo = futuros[futuro]
            concluidos += 1
            try:
                resumo.extend(futuro.result())
            except Exception as exc:  # noqa: BLE001
                log.error("%s falhou por completo: %s", fundo.nome_interno, exc)
                resumo.append({
                    "fundo": fundo.nome_interno, "cnpj": fundo.cnpj, "base": "*",
                    "linhas": 0, "segundos": 0, "status": "erro", "erro": str(exc)[:300],
                })
            log.info("Progresso: %d/%d fundos", concluidos, len(fundos))

    erros = [r for r in resumo if r["status"] == "erro"]
    if erros:
        log.warning(
            "%d consulta(s) com erro: os temporarios em %s foram mantidos para retomada.",
            len(erros), temporarios,
        )

    saida.mkdir(parents=True, exist_ok=True)
    _limpar_carga_anterior(saida, bases, formato)
    por_base = consolidar(saida, temporarios, bases, formato)

    if not erros:
        shutil.rmtree(temporarios, ignore_errors=True)

    (saida / "_carga.json").write_text(
        json.dumps(
            {
                "data_posicao": data_str,
                "gerado_em": dt.datetime.now().isoformat(timespec="seconds"),
                "fundos_solicitados": len(fundos),
                "formato": formato,
                "compressao": COMPRESSAO if formato == "parquet" else None,
                "bases": por_base,
                "erros": len(erros),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    caminho_resumo = saida / "_resumo.csv"
    with caminho_resumo.open("w", newline="", encoding="utf-8-sig") as arq:
        escritor = csv.DictWriter(
            arq, fieldnames=["fundo", "cnpj", "base", "linhas", "segundos", "status", "erro"], delimiter=";"
        )
        escritor.writeheader()
        escritor.writerows(sorted(resumo, key=lambda r: (r["fundo"], r["base"])))
    log.info("Resumo gravado em %s", caminho_resumo)
    return resumo, por_base


def carregar_base(saida: Path, base: str, cnpj: str | None = None) -> pd.DataFrame:
    """Le uma base consolidada, opcionalmente filtrando um fundo."""
    saida = Path(saida)
    for extensao in (".parquet", ".csv"):
        caminho = saida / f"{base}{extensao}"
        if caminho.exists():
            df = _ler(caminho)
            if cnpj:
                from .cvm import so_digitos

                alvo = so_digitos(cnpj)
                df = df[df["cnpj_fundo"].map(so_digitos) == alvo]
            return df.reset_index(drop=True)
    raise FileNotFoundError(f"Base '{base}' nao encontrada em {saida}")
