"""Separa o que a carteira junta: principal e juros corridos.

O `Valor_Liquido` e preco sujo -- principal mais o juro acumulado desde o ultimo
evento de pagamento. O `fluxo.py` trata os 100% como principal e os amortiza
pelos pesos do cronograma, mas o acrual nao e principal: ele sai inteiro no
PROXIMO pagamento. O efeito e de data, nao de valor -- o dinheiro aparece sempre
mais tarde do que entra, e a faixa curta, que e o produto do relatorio de
liquidez, e a que mais perde.

Medido na carteira de 31/07/2026: R$ 7,70 milhoes de acrual, dos quais R$ 7,27
milhoes pertencem a ate 90 dias, com R$ 6,4 milhoes exibidos hoje acima de 6
meses. Ver `saidas/acrual_referencia.csv`.

A separacao nao precisa do principal por fora -- ele sai da divisao:

    Valor_Liquido = P x F        ->    P = Valor_Liquido / F
    acrual        = Valor_Liquido - P

`F` e o fator do periodo entre o ultimo evento e a data-base. Para papel CDI+ e
o produtorio do CDI realizado (`TB_Taxas`) vezes o spread; para pre, so o spread.

Num papel com correcao cambial a variacao NAO entra em `F`: ela corrige o
principal e os juros na mesma proporcao, entao cancela na divisao. Basta o fator
de juros -- o que dispensa a PTAX realizada do periodo.

Este modulo nao projeta nada e nao altera o `fluxo.py`. So decompoe.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import calendario

log = logging.getLogger(__name__)

# Uma serie por (inicio, fim) durante o processo: a decomposicao roda por
# operacao e a carteira tem centenas.
_CACHE_CDI: dict[tuple[dt.date, dt.date], dict[dt.date, float]] = {}


class ErroAcrual(RuntimeError):
    pass


@dataclass
class Decomposicao:
    """O que estava junto no `Valor_Liquido`, agora separado."""

    codigo_op: str
    data_base: dt.date
    ultimo_evento: dt.date
    origem_evento: str          # "cronograma" ou "emissao"
    valor_liquido: float
    principal: float
    acrual: float
    fator: float
    qt_dias_uteis: int
    qt_dias_corridos: int
    indexado: bool
    base_taxa: int
    # Dias uteis do periodo sem cotacao de CDI na TB_Taxas. Lista vazia e o
    # normal; qualquer item aqui significa que o fator saiu subestimado.
    dias_sem_cdi: list[dt.date] = field(default_factory=list)

    @property
    def confiavel(self) -> bool:
        return not self.dias_sem_cdi

    @property
    def proporcao(self) -> float:
        """Acrual como fracao do valor -- util para ordenar por relevancia."""
        return self.acrual / self.valor_liquido if self.valor_liquido else 0.0


# --- CDI realizado ----------------------------------------------------------

def serie_cdi(inicio: dt.date, fim: dt.date, usar_cache: bool = True) -> dict:
    """CDI_Over diario da `TB_Taxas`, por data.

    `Valor` e varchar na tabela e guarda ruido de float em texto
    (`4.7199999999999998`), por isso o CAST no proprio SQL.
    """
    from . import db

    chave = (inicio, fim)
    if usar_cache and chave in _CACHE_CDI:
        return _CACHE_CDI[chave]

    df = db.consultar(
        "SELECT Data, CAST(Valor AS FLOAT) AS taxa FROM dbo.TB_Taxas "
        "WHERE Ativo = 'CDI_Over' AND Data BETWEEN ? AND ? ORDER BY Data",
        (inicio.isoformat(), fim.isoformat()),
    )
    serie = {
        (d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])): float(t)
        for d, t in zip(df["Data"], df["taxa"])
    }
    if usar_cache:
        _CACHE_CDI[chave] = serie
    return serie


def limpar_cache() -> None:
    _CACHE_CDI.clear()


def fator_cdi(
    inicio: dt.date,
    fim: dt.date,
    serie: dict[dt.date, float],
) -> tuple[float, int, list[dt.date]]:
    """Produtorio de (1 + i/100)^(1/252) nos dias UTEIS de (inicio, fim].

    Devolve (fator, dias contados, dias uteis sem cotacao). O dia faltante e
    DEVOLVIDO, nao engolido: pular em silencio subestima o fator e o erro some
    no meio de centenas de operacoes. Foi assim que 05/08/2026 passou
    despercebido ate alguem cruzar a serie com o calendario.
    """
    if fim <= inicio:
        return 1.0, 0, []
    fator, contados, faltando = 1.0, 0, []
    data = inicio + dt.timedelta(days=1)
    while data <= fim:
        if calendario.e_dia_util(data):
            taxa = serie.get(data)
            if taxa is None:
                faltando.append(data)
            else:
                fator *= (1 + taxa / 100) ** (1 / 252)
                contados += 1
        data += dt.timedelta(days=1)
    return fator, contados, faltando


# --- Decomposicao -----------------------------------------------------------

def _data(valor) -> dt.date | None:
    if valor is None:
        return None
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    try:
        return dt.date.fromisoformat(str(valor)[:10])
    except (ValueError, TypeError):
        return None


def ultimo_evento(
    parcelas: pd.DataFrame | list,
    data_base: dt.date,
    operacao=None,
) -> tuple[dt.date | None, str]:
    """Data a partir da qual o juro corrente esta correndo.

    E a ultima parcela ANTERIOR a data-base. Papel que ainda nao pagou nada usa
    a emissao -- o acrual corre desde ela, e ignorar isso deixaria de fora
    justamente o papel novo, que costuma ter o acrual mais gordo.
    """
    datas = []
    if isinstance(parcelas, pd.DataFrame):
        origem = parcelas["dt_vencimento"] if "dt_vencimento" in parcelas else []
    else:
        origem = [p if not isinstance(p, dict) else p.get("dt_vencimento")
                  for p in (parcelas or [])]
    for bruto in origem:
        data = _data(bruto)
        if data is not None and data < data_base:
            datas.append(data)
    if datas:
        return max(datas), "cronograma"

    if operacao is not None:
        for campo in ("dt_operacao", "dt_emissao"):
            data = _data(operacao.get(campo) if hasattr(operacao, "get")
                         else getattr(operacao, campo, None))
            if data is not None and data < data_base:
                return data, "emissao"
    return None, ""


def decompor(
    codigo_op: str,
    valor_liquido: float,
    data_base: dt.date,
    operacao,
    parcelas,
    serie: dict[dt.date, float] | None = None,
) -> Decomposicao | None:
    """Separa `valor_liquido` em principal e acrual. None quando nao da.

    Nao da em tres casos, todos legitimos: sem data de inicio do acrual, sem
    taxa cadastrada, ou valor nao positivo. Devolver None e melhor que arriscar
    um palpite -- quem chama decide o que fazer com a operacao que sobrou.
    """
    if valor_liquido <= 0:
        return None
    taxa_ad = operacao.get("taxa_ad_efetiva") if hasattr(operacao, "get") else None
    if taxa_ad is None or (isinstance(taxa_ad, float) and pd.isna(taxa_ad)):
        return None

    inicio, origem = ultimo_evento(parcelas, data_base, operacao)
    if inicio is None:
        return None

    base_taxa = int(operacao.get("base_taxa") or 360)
    indexado = bool(operacao.get("indexado"))
    dias_corridos = (data_base - inicio).days

    if indexado:
        if serie is None:
            serie = serie_cdi(inicio, data_base)
        f_indice, dias_uteis, faltando = fator_cdi(inicio, data_base, serie)
    else:
        f_indice, faltando = 1.0, []
        dias_uteis = calendario.dias_uteis(inicio, data_base)

    expoente = dias_uteis if base_taxa == 252 else dias_corridos
    fator = f_indice * (1 + float(taxa_ad)) ** expoente
    if fator <= 1.0:
        return None

    principal = valor_liquido / fator
    return Decomposicao(
        codigo_op=codigo_op,
        data_base=data_base,
        ultimo_evento=inicio,
        origem_evento=origem,
        valor_liquido=float(valor_liquido),
        principal=principal,
        acrual=float(valor_liquido) - principal,
        fator=fator,
        qt_dias_uteis=dias_uteis,
        qt_dias_corridos=dias_corridos,
        indexado=indexado,
        base_taxa=base_taxa,
        dias_sem_cdi=faltando,
    )


def decompor_carteira(
    posicoes: pd.DataFrame,
    cronograma,
    data_base: dt.date,
) -> tuple[list[Decomposicao], dict[str, int]]:
    """Decompoe cada posicao que tenha cronograma.

    Devolve (decomposicoes, motivos por que as demais ficaram de fora). Uma
    carga so de CDI cobre todas as operacoes -- o intervalo maximo e o de quem
    tem o ultimo evento mais antigo.
    """
    resultados: list[Decomposicao] = []
    motivos: dict[str, int] = {}

    def anotar(motivo: str) -> None:
        motivos[motivo] = motivos.get(motivo, 0) + 1

    # Uma unica ida ao banco: o intervalo que cobre todo mundo.
    mais_antigo = data_base
    pendentes = []
    for _, posicao in posicoes.iterrows():
        codigo = str(posicao["codigo_op"])
        operacao = cronograma.operacao(codigo)
        if operacao is None:
            anotar("sem cronograma")
            continue
        parcelas = cronograma.parcelas_de(codigo)
        inicio, _ = ultimo_evento(parcelas, data_base, operacao)
        if inicio is None:
            anotar("sem inicio de acrual")
            continue
        mais_antigo = min(mais_antigo, inicio)
        pendentes.append((codigo, posicao, operacao, parcelas))

    serie = serie_cdi(mais_antigo, data_base) if pendentes else {}

    for codigo, posicao, operacao, parcelas in pendentes:
        d = decompor(codigo, float(posicao["valor_base"] or 0.0), data_base,
                     operacao, parcelas, serie)
        if d is None:
            anotar("sem taxa ou sem acrual")
            continue
        if not d.confiavel:
            log.warning("%s: %d dia(s) util(eis) sem CDI (%s) -- fator subestimado.",
                        codigo, len(d.dias_sem_cdi),
                        ", ".join(f"{x:%d/%m/%Y}" for x in d.dias_sem_cdi[:3]))
        resultados.append(d)
    return resultados, motivos


def para_dataframe(decomposicoes: list[Decomposicao]) -> pd.DataFrame:
    """Tabela das decomposicoes, para conferencia e gravacao."""
    return pd.DataFrame([{
        "codigo_op": d.codigo_op,
        "data_base": d.data_base,
        "ultimo_evento": d.ultimo_evento,
        "origem_evento": d.origem_evento,
        "qt_dias_uteis": d.qt_dias_uteis,
        "qt_dias_corridos": d.qt_dias_corridos,
        "indexado": d.indexado,
        "base_taxa": d.base_taxa,
        "fator": d.fator,
        "valor_liquido": d.valor_liquido,
        "principal": d.principal,
        "acrual": d.acrual,
        "proporcao": d.proporcao,
        "confiavel": d.confiavel,
    } for d in decomposicoes])
