"""Conexao com o SQL Server riscogc.

Equivale ao `ADODB.Connection` com `Provider=SQLOLEDB; Integrated Security=SSPI`
usado pelas macros, mas com parametros vinculados no lugar da concatenacao de
string e uma conexao por thread (o lote roda em paralelo).
"""

from __future__ import annotations

import logging
import os
import threading
import warnings

import pandas as pd
import pyodbc

log = logging.getLogger(__name__)

# Producao: srvSqlPrd01 (10.101.1.18), banco riscogc -- decisao da area em
# 05/10/2026. O codigo original apontava para a homologacao (10.201.1.6 /
# riscogchml), unico acesso que o gestor tinha. As variaveis permitem apontar
# para outro ambiente sem mexer no codigo.
SERVIDOR = os.getenv("LIQUIDEZ_SERVIDOR", "10.101.1.18,1433")
BANCO = os.getenv("LIQUIDEZ_BANCO", "riscogc")

# Ordem de preferencia; nesta maquina so existe o driver legado "SQL Server".
DRIVERS_PREFERIDOS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
)

_local = threading.local()

# Quanto uma consulta pode demorar antes de desistir. O driver legado nem sempre
# honra este atributo -- por isso a rede caindo ainda pode pendurar a chamada --
# mas onde ele vale, falhar em minutos e melhor que pendurar por uma hora.
TIMEOUT_CONSULTA = 120
TIMEOUT_LOGIN = 20

# SQLSTATEs que significam "a conexao morreu", nao "a consulta esta errada".
# Nesses casos vale derrubar a conexao da thread e tentar de novo: o cache de
# conexao por thread transforma uma queda momentanea em falha permanente ate o
# fim do processo, porque toda consulta seguinte reusa o socket morto.
# 01000 saiu: e um SQLSTATE de aviso generico, nao de conexao perdida.
ESTADOS_CONEXAO = ("08S01", "08001", "08003", "HYT00", "HYT01")


class ErroConexao(RuntimeError):
    pass


def _e_erro_de_conexao(exc: Exception) -> bool:
    estado = exc.args[0] if exc.args else ""
    return isinstance(estado, str) and estado in ESTADOS_CONEXAO


def melhor_driver() -> str:
    disponiveis = set(pyodbc.drivers())
    for driver in DRIVERS_PREFERIDOS:
        if driver in disponiveis:
            return driver
    raise ErroConexao(f"Nenhum driver ODBC de SQL Server encontrado. Disponiveis: {sorted(disponiveis)}")


def string_conexao(servidor: str = SERVIDOR, banco: str = BANCO) -> str:
    driver = melhor_driver()
    partes = [
        f"DRIVER={{{driver}}}",
        f"SERVER={servidor}",
        f"DATABASE={banco}",
        "Trusted_Connection=yes",
    ]
    # Os drivers 17/18 exigem decisao explicita sobre TLS; o legado nao entende.
    if driver.startswith("ODBC Driver"):
        partes.append("TrustServerCertificate=yes")
    if driver == "ODBC Driver 18 for SQL Server":
        partes.append("Encrypt=no")
    return ";".join(partes) + ";"


# SQL_SS_TIMESTAMPOFFSET: o pyodbc nao le datetimeoffset sem conversor e um
# SELECT * em tabela com essa coluna derruba a consulta ("ODBC SQL type -155 is
# not yet supported"). Caso real: HOD_MCT_REU_MOVIMENTACAO_PROCESSADA
# .data_processamento (05/10/2026). Mesmo conversor do projeto Datalake.
SQL_DATETIMEOFFSET = -155


def ler_datetimeoffset(bruto: bytes | None):
    """Bytes do driver (ano, mes, dia, h, min, s, fracao em ns, fuso h, fuso min)
    -> datetime com fuso."""
    import struct
    from datetime import datetime, timedelta, timezone

    if bruto is None:
        return None
    ano, mes, dia, hora, minuto, segundo, ns, fuso_h, fuso_m = struct.unpack("<6hI2h", bruto)
    return datetime(ano, mes, dia, hora, minuto, segundo, ns // 1000,
                    tzinfo=timezone(timedelta(hours=fuso_h, minutes=fuso_m)))


def conectar(servidor: str = SERVIDOR, banco: str = BANCO) -> pyodbc.Connection:
    """Devolve a conexao da thread atual, criando-a se necessario."""
    conexao = getattr(_local, "conexao", None)
    if conexao is None:
        conexao = pyodbc.connect(string_conexao(servidor, banco), timeout=TIMEOUT_LOGIN)
        conexao.timeout = TIMEOUT_CONSULTA
        conexao.add_output_converter(SQL_DATETIMEOFFSET, ler_datetimeoffset)
        _local.conexao = conexao
    return conexao


def fechar() -> None:
    conexao = getattr(_local, "conexao", None)
    if conexao is not None:
        try:
            conexao.close()
        except pyodbc.Error:
            # Conexao ja morta: fechar falha e nao ha o que fazer a respeito.
            pass
        _local.conexao = None


def _executar(sql: str, params: tuple) -> pd.DataFrame:
    conexao = conectar()
    with warnings.catch_warnings():
        # pandas avisa que so "suporta" SQLAlchemy; com pyodbc funciona normalmente.
        warnings.simplefilter("ignore", UserWarning)
        return pd.read_sql(sql, conexao, params=params)


def consultar(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Executa a consulta e devolve um DataFrame.

    Uma queda de rede derruba a conexao em cache da thread, e como ela e
    reaproveitada, TODAS as consultas seguintes falhariam ate o fim do processo.
    Por isso a segunda tentativa: descarta a conexao morta e reconecta uma vez.
    Vale so para erro de conexao -- consulta malformada nao melhora repetindo.
    """
    try:
        return _executar(sql, params)
    except pyodbc.Error as exc:
        if not _e_erro_de_conexao(exc):
            raise
        log.warning("Conexao com %s caiu (%s); reconectando e tentando de novo.",
                    SERVIDOR, exc.args[0] if exc.args else exc)
        fechar()
        return _executar(sql, params)


def testar_conexao() -> dict:
    """Confere driver, acesso ao banco e devolve um resumo para o CLI."""
    driver = melhor_driver()
    conexao = pyodbc.connect(string_conexao(), timeout=15)
    try:
        cursor = conexao.cursor()
        cursor.execute("SELECT @@VERSION, DB_NAME(), SUSER_SNAME()")
        versao, banco, usuario = cursor.fetchone()
        return {
            "driver": driver,
            "servidor": SERVIDOR,
            "banco": banco,
            "usuario": usuario,
            "versao": versao.splitlines()[0].strip(),
        }
    finally:
        conexao.close()
