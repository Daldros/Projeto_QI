"""Calendario de dias uteis, a partir do arquivo de feriados da ANBIMA.

Dia util e a unidade de conta de quase tudo aqui: a base 252 dos papeis CDI+,
o acrual entre cupons, o NDU da curva. Contar so excluindo sabado e domingo
erra por 9 a 12 dias por ano -- em 2026 sao 12 feriados em dia de semana --, e o
erro entra direto no fator de juros.

A ANBIMA e a fonte de mercado. O arquivo dela cobre 2001 a 2099 e traz apenas os
feriados em que nao ha sensibilizacao de Reservas Bancarias, que e exatamente o
recorte que interessa: nao inclui feriado municipal nem eleicao.

O `.xls` original fica guardado ao lado do CSV. O CSV e o que o programa le --
texto, inspecionavel e versionavel, no mesmo espirito do cadastro de fundos e da
tabela de prazos. Baixar de novo so quando a ANBIMA publicar revisao; o
calendario muda pouco e nao pode mudar sozinho no meio de uma apuracao.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DIR = Path("config") / "calendario"
ARQUIVO_XLS = DIR / "feriados_nacionais.xls"
ARQUIVO_CSV = DIR / "feriados.csv"
URL_ANBIMA = "https://www.anbima.com.br/feriados/arqs/feriados_nacionais.xls"

# Colunas do .xls da ANBIMA: Data | Dia da Semana | Feriado.
COL_DATA, COL_DESCRICAO = 0, 2

_CACHE: set[dt.date] | None = None


class ErroCalendario(RuntimeError):
    pass


# --- Carga ------------------------------------------------------------------

def baixar(destino: Path = ARQUIVO_XLS, url: str = URL_ANBIMA) -> Path:
    """Busca o .xls na ANBIMA. Chamada explicita -- nao roda sozinha."""
    import ssl
    import urllib.request

    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    contexto = ssl.create_default_context()
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE
    pedido = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    with urllib.request.urlopen(pedido, timeout=60, context=contexto) as resposta:
        dados = resposta.read()
    if not dados.startswith(b"\xd0\xcf\x11\xe0"):
        raise ErroCalendario(
            f"{url} nao devolveu um .xls (primeiros bytes: {dados[:16]!r}). "
            f"A ANBIMA pode ter mudado o endereco."
        )
    destino.write_bytes(dados)
    log.info("Calendario baixado: %s (%d bytes)", destino, len(dados))
    return destino


def _texto(bruto: str) -> str:
    """Conserta o acento do .xls da ANBIMA.

    O arquivo guarda o texto em UTF-8, mas se declara em cp1252; o `xlrd` honra
    a declaracao e devolve `ConfraternizaÃ§Ã£o`. Reverter o passo errado
    recupera o original. Se nao for esse o caso, o texto volta intacto -- o
    `except` cobre quem ja esta certo.
    """
    try:
        return bruto.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return bruto


def converter(origem: Path = ARQUIVO_XLS, destino: Path = ARQUIVO_CSV) -> Path:
    """Converte o .xls da ANBIMA no CSV que o programa le."""
    import xlrd

    origem, destino = Path(origem), Path(destino)
    if not origem.exists():
        raise ErroCalendario(
            f"{origem} nao existe. Rode 'python -m liquidez.calendario atualizar'."
        )
    livro = xlrd.open_workbook(origem)
    aba = livro.sheet_by_index(0)

    feriados: dict[dt.date, str] = {}
    for i in range(aba.nrows):
        celula = aba.cell(i, COL_DATA)
        if celula.ctype != xlrd.XL_CELL_DATE:
            continue
        ano, mes, dia, *_ = xlrd.xldate_as_tuple(celula.value, livro.datemode)
        descricao = ""
        if aba.ncols > COL_DESCRICAO:
            descricao = _texto(str(aba.cell_value(i, COL_DESCRICAO)).strip())
        feriados[dt.date(ano, mes, dia)] = descricao

    destino.parent.mkdir(parents=True, exist_ok=True)
    linhas = ["data;descricao"]
    linhas += [f"{d.isoformat()};{feriados[d]}" for d in sorted(feriados)]
    destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    log.info("Calendario convertido: %d feriados de %s a %s -> %s",
             len(feriados), min(feriados), max(feriados), destino)
    return destino


def atualizar() -> Path:
    """Baixa e converte, nesta ordem."""
    baixar()
    return converter()


def carregar(caminho: Path = ARQUIVO_CSV, recarregar: bool = False) -> set[dt.date]:
    """O conjunto de feriados. Fica em cache -- e lido em laco apertado."""
    global _CACHE
    if _CACHE is not None and not recarregar:
        return _CACHE
    caminho = Path(caminho)
    if not caminho.exists():
        raise ErroCalendario(
            f"{caminho} nao existe. Rode 'python -m liquidez.calendario atualizar'."
        )
    feriados = set()
    for linha in caminho.read_text(encoding="utf-8").splitlines()[1:]:
        texto = linha.split(";")[0].strip()
        if texto:
            feriados.add(dt.date.fromisoformat(texto))
    _CACHE = feriados
    return feriados


def limpar_cache() -> None:
    global _CACHE
    _CACHE = None


# --- Consulta ---------------------------------------------------------------

def e_dia_util(data: dt.date) -> bool:
    return data.weekday() < 5 and data not in carregar()


def dias_uteis(inicio: dt.date, fim: dt.date) -> int:
    """Dias uteis em (inicio, fim] -- abre exclusivo, fecha inclusivo.

    E a convencao do acrual: o dia do evento anterior nao conta de novo, o dia
    do evento seguinte conta. Mesma contagem de `cra.contar_dias_uteis`.
    """
    if fim <= inicio:
        return 0
    feriados = carregar()
    total, data = 0, inicio + dt.timedelta(days=1)
    while data <= fim:
        if data.weekday() < 5 and data not in feriados:
            total += 1
        data += dt.timedelta(days=1)
    return total


def proximo_dia_util(data: dt.date) -> dt.date:
    while not e_dia_util(data):
        data += dt.timedelta(days=1)
    return data


def dia_util_anterior(data: dt.date) -> dt.date:
    while not e_dia_util(data):
        data -= dt.timedelta(days=1)
    return data


def cobertura() -> tuple[dt.date, dt.date]:
    """Primeiro e ultimo feriado do arquivo -- ate onde o calendario vale."""
    feriados = carregar()
    return min(feriados), max(feriados)


def _main(argv: list[str] | None = None) -> int:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = sys.argv[1:] if argv is None else argv
    comando = args[0] if args else "resumo"

    if comando == "atualizar":
        print(atualizar())
        comando = "resumo"

    if comando == "resumo":
        feriados = carregar()
        de, ate = cobertura()
        print(f"{len(feriados)} feriados de {de:%d/%m/%Y} a {ate:%d/%m/%Y}")
        ano = dt.date.today().year
        print(f"\n{'ano':<6} {'feriados':>9} {'dias uteis':>11}")
        for a in range(ano - 1, ano + 4):
            n = sum(1 for d in feriados if d.year == a and d.weekday() < 5)
            print(f"{a:<6} {n:>9} "
                  f"{dias_uteis(dt.date(a - 1, 12, 31), dt.date(a, 12, 31)):>11}")
        return 0

    print(f"Comando desconhecido: {comando}. Use 'atualizar' ou 'resumo'.")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
