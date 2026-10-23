"""Fluxo do ativo objeto (lastro) de um CRA, a partir do Termo de Securitizacao.

Um CRA nao tem fluxo proprio: ele repassa o que o lastro paga. O lastro e o
titulo do devedor do agronegocio -- CDCA, CPR-F, NC, debenture -- que a
securitizadora comprou e vinculou a serie. Quem manda no caixa e esse titulo, e
e dele que se projeta a liquidez.

A leitura do Termo e trabalho de agente (`.claude/agents/cra-lastro.md`): o
documento e texto corrido em PDF e nao ha layout fixo. O agente le e preenche
uma FICHA -- um JSON com os parametros contratuais e a citacao de onde cada um
saiu. Este modulo pega a ficha e faz a conta, que e deterministica:

    ficha.json  --(este modulo)-->  config/cronograma/manuais/<codigo_op>.json
                                    saidas/cra/<codigo_op>_conferencia.xlsx

O JSON de saida entra direto no pipeline: e o mesmo formato que
`cronograma.carregar_manuais` ja le, o mesmo do CRA da ECOAGRO (B989830). Dali
`fluxo.projetar` usa as datas e os PESOS das amortizacoes -- o valor em reais
vem do `Valor_Liquido` da carteira na data-base, nao daqui.

O .xlsx e para conferir contra o documento antes de aceitar o cadastro: traz o
fluxo completo (saldo, juros, amortizacao, PMT) na taxa contratual, os
parametros com a pagina de onde vieram e as pendencias que a leitura deixou em
aberto. O motor de liquidez recalcula os juros com a curva do dia; os juros
daqui servem para bater com a tabela do Termo, nao para precificar.

Convencao das datas: o Termo costuma dizer "caso nao seja Dia Util, o pagamento
se da no Dia Util imediatamente seguinte". O ajuste e feito aqui, mas so conhece
sabado e domingo -- feriado bancario tem de vir na ficha (`feriados`), senao a
data cai em dia de feriado sem aviso.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import logging
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from . import cadastro as cadastro_fundos
from . import cronograma

log = logging.getLogger(__name__)

DIR_MANUAIS = Path("config") / "cronograma" / "manuais"
DIR_FICHAS = Path("config") / "cronograma" / "fichas"
DIR_CONFERENCIA = Path("saidas") / "cra"

# Pasta onde os documentos sao depositados, espelhando `config/planilhas/`:
# entrada na raiz, processados na subpasta. Ver `inventariar`.
DIR_DOCUMENTOS = Path("config") / "cra"
DIR_DOCUMENTOS_PROCESSADOS = DIR_DOCUMENTOS / "processados"
PADRAO_DOCUMENTO = ("*.pdf", "*.PDF")

# Meses entre pagamentos. O Termo escreve por extenso; a ficha guarda o mesmo
# rotulo para que a conferencia contra o documento seja direta.
MESES_DE = {
    "mensal": 1,
    "bimestral": 2,
    "trimestral": 3,
    "quadrimestral": 4,
    "semestral": 6,
    "anual": 12,
    "unica": 0,      # bullet: paga so no vencimento
    "bullet": 0,
}

BASES_VALIDAS = (252, 360)

# Tolerancia ao fechar a soma das amortizacoes contra o principal. Acima disso a
# leitura errou alguma parcela -- e melhor parar do que cadastrar torto.
TOLERANCIA_FECHAMENTO = 0.01


class ErroFicha(RuntimeError):
    """Ficha incompleta ou incoerente. Nao ha o que calcular."""


# --- Datas ------------------------------------------------------------------

def _data(valor: Any, campo: str = "") -> dt.date:
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    if isinstance(valor, str) and valor.strip():
        try:
            return dt.date.fromisoformat(valor.strip()[:10])
        except ValueError as exc:
            raise ErroFicha(f"{campo or 'data'} invalida: {valor!r} (use AAAA-MM-DD)") from exc
    raise ErroFicha(f"{campo or 'data'} ausente na ficha.")


def _data_ou_none(valor: Any, campo: str = "") -> dt.date | None:
    if valor in (None, "", "null"):
        return None
    return _data(valor, campo)


def somar_meses(base: dt.date, meses: int) -> dt.date:
    """Avanca meses preservando o dia; encurta quando o mes nao tem o dia.

    31/01 + 1 mes = 28/02 (ou 29 em bissexto). O deslocamento e sempre contado
    da data ancora, nunca da anterior -- assim 31/01 + 2 meses volta a ser 31/03
    em vez de ficar preso em 28.
    """
    total = base.month - 1 + meses
    ano = base.year + total // 12
    mes = total % 12 + 1
    return dt.date(ano, mes, min(base.day, calendar.monthrange(ano, mes)[1]))


def _feriados(ficha: dict) -> set[dt.date]:
    return {_data(d, "feriados") for d in ficha.get("feriados") or []}


def ajustar_dia_util(
    data: dt.date,
    feriados: set[dt.date],
    modo: str = "seguinte",
) -> dt.date:
    """Empurra a data para dia util. `modo`: seguinte, anterior ou nenhum."""
    if modo in (None, "nenhum", ""):
        return data
    passo = 1 if modo == "seguinte" else -1
    while data.weekday() >= 5 or data in feriados:
        data += dt.timedelta(days=passo)
    return data


def contar_dias_uteis(inicio: dt.date, fim: dt.date, feriados: set[dt.date]) -> int:
    """Dias uteis no intervalo (inicio, fim] -- a convencao da base 252."""
    if fim <= inicio:
        return 0
    dias = 0
    data = inicio + dt.timedelta(days=1)
    while data <= fim:
        if data.weekday() < 5 and data not in feriados:
            dias += 1
        data += dt.timedelta(days=1)
    return dias


def datas_periodicas(
    primeira: dt.date,
    ultima: dt.date,
    meses: int,
    quantidade: int | None = None,
) -> list[dt.date]:
    """Datas de `primeira` ate `ultima`, de `meses` em `meses`.

    Com `quantidade` a serie tem exatamente esse tamanho e a ultima data e
    forcada para `ultima` -- o Termo as vezes diz "24 parcelas mensais" com a
    ultima caindo no vencimento, que pode nao ser multiplo exato do passo.
    """
    if meses <= 0:
        return [ultima]
    if quantidade is not None and quantidade > 0:
        datas = [somar_meses(primeira, meses * i) for i in range(quantidade)]
        datas[-1] = ultima
        return datas
    datas, i = [], 0
    while True:
        data = somar_meses(primeira, meses * i)
        if data > ultima:
            break
        datas.append(data)
        i += 1
    if not datas or datas[-1] != ultima:
        datas.append(ultima)
    return datas


# --- Taxas ------------------------------------------------------------------

def taxa_diaria(taxa_aa: float, base: int) -> float:
    """Taxa anual efetiva -> diaria equivalente na base (252 uteis ou 360 corridos)."""
    if base not in BASES_VALIDAS:
        raise ErroFicha(f"base_taxa {base} nao suportada; use 252 ou 360.")
    return (1.0 + float(taxa_aa)) ** (1.0 / base) - 1.0


def _remuneracao(ficha: dict) -> dict:
    """Normaliza o bloco de remuneracao e devolve o que o cadastro precisa.

    Tres formas aparecem no Termo:
      - `spread`     CDI + 3,50% a.a.  -> taxa_ad_efetiva e so o spread; o CDI
                                          entra pela curva na projecao.
      - `percentual` 120% do CDI       -> o motor nao modela percentual do CDI;
                                          vira pendencia, nao chute.
      - `pre`        14,00% a.a.       -> taxa cheia, sem indexador.
    """
    rem = ficha.get("remuneracao") or {}
    forma = str(rem.get("forma") or "").lower().strip()
    indexador = (rem.get("indexador") or "").upper().strip() or None
    base = int(rem.get("base_taxa") or 252)
    taxa_aa = rem.get("taxa_aa")

    if forma not in ("spread", "percentual", "pre"):
        raise ErroFicha(
            "remuneracao.forma deve ser 'spread' (CDI + x), 'percentual' (x% do CDI) ou 'pre'."
        )
    if forma == "percentual":
        raise ErroFicha(
            f"Remuneracao em percentual do {indexador or 'indexador'} "
            f"({rem.get('percentual_indexador')}) nao e suportada pelo motor de "
            f"projecao, que so compoe indexador + spread. Registre como pendencia "
            f"e trate com o time de risco antes de cadastrar."
        )
    if taxa_aa is None:
        raise ErroFicha("remuneracao.taxa_aa ausente (use fracao: 3,50% a.a. -> 0.035).")
    taxa_aa = float(taxa_aa)
    if taxa_aa > 1.0:
        raise ErroFicha(
            f"remuneracao.taxa_aa = {taxa_aa}: use fracao, nao percentual "
            f"(3,50% a.a. -> 0.035)."
        )

    indexado = forma == "spread"
    if indexado and not indexador:
        raise ErroFicha("remuneracao.forma = 'spread' exige remuneracao.indexador.")
    if indexado and indexador not in ("CDI", "SELIC"):
        # IPCA+ existe em CRA, e comum, e o motor nao trata: a curva do fluxo e
        # a DI. Melhor barrar aqui do que projetar so o cupom real.
        raise ErroFicha(
            f"Indexador {indexador} nao e suportado: `fluxo.py` projeta com a curva "
            f"de DI (CDI) ou taxa pre. Um lastro {indexador}+ precisa de decisao de "
            f"risco sobre a projecao da inflacao antes de entrar no cadastro."
        )

    return {
        "indexado": indexado,
        "indexador": indexador if indexado else None,
        "base_taxa": base,
        "taxa_aa": taxa_aa,
        "taxa_ad_efetiva": taxa_diaria(taxa_aa, base),
        "forma": forma,
    }


# --- Cronograma de amortizacao ----------------------------------------------

def _pmt(principal: float, taxa: float, n: int) -> float:
    if n <= 0:
        raise ErroFicha("amortizacao Price exige n_parcelas > 0.")
    if abs(taxa) < 1e-12:
        return principal / n
    return principal * taxa / (1.0 - (1.0 + taxa) ** (-n))


def montar_amortizacoes(ficha: dict, rem: dict) -> list[tuple[dt.date, float]]:
    """(data, valor) de cada amortizacao, ja em reais e fechando no principal.

    Tipos aceitos, na ordem em que aparecem nos Termos:
      tabela           anexo com uma linha por data (valor ou % do principal)
      percentual_saldo anexo em % do SALDO DEVEDOR -- convertido para % do principal
      bullet           tudo no vencimento
      sac              parcelas iguais de principal
      price            PMT constante; a amortizacao cresce
    """
    amort = ficha.get("amortizacao") or {}
    tipo = str(amort.get("tipo") or "").lower().strip()
    principal = float(ficha.get("vl_principal") or 0.0)
    if principal <= 0:
        raise ErroFicha("vl_principal ausente ou nao positivo.")
    vencimento = _data(ficha.get("dt_vencimento"), "dt_vencimento")

    if tipo == "bullet":
        return [(vencimento, principal)]

    if tipo in ("tabela", "percentual_saldo"):
        linhas = amort.get("tabela") or []
        if not linhas:
            raise ErroFicha(f"amortizacao.tipo = '{tipo}' exige amortizacao.tabela preenchida.")
        datas = [_data(l.get("dt_vencimento"), "amortizacao.tabela.dt_vencimento") for l in linhas]
        if tipo == "tabela":
            valores = []
            for linha in linhas:
                if linha.get("vl_amortizacao") is not None:
                    valores.append(float(linha["vl_amortizacao"]))
                elif linha.get("pct_principal") is not None:
                    valores.append(float(linha["pct_principal"]) * principal)
                else:
                    raise ErroFicha(
                        "cada linha de amortizacao.tabela precisa de vl_amortizacao "
                        "ou pct_principal."
                    )
        else:
            # % do saldo devedor: 1/34, 1/33 ... 1/1 sao 34 parcelas iguais. A
            # conversao tem de ser feita percorrendo o saldo, nao multiplicando
            # o percentual pelo principal.
            valores, saldo = [], principal
            for linha in linhas:
                pct = linha.get("pct_saldo")
                if pct is None:
                    raise ErroFicha(
                        "amortizacao.tipo = 'percentual_saldo' exige pct_saldo em cada linha."
                    )
                valor = saldo * float(pct)
                valores.append(valor)
                saldo -= valor
        return list(zip(datas, valores))

    # Series geradas: precisam da data da primeira parcela e do passo.
    periodicidade = str(amort.get("periodicidade") or "").lower().strip()
    if periodicidade not in MESES_DE:
        raise ErroFicha(
            f"amortizacao.periodicidade {periodicidade!r} desconhecida; "
            f"use uma de {sorted(MESES_DE)}."
        )
    meses = MESES_DE[periodicidade]
    primeira = _data_ou_none(amort.get("dt_primeira"), "amortizacao.dt_primeira")
    n = amort.get("n_parcelas")
    n = int(n) if n else None
    if primeira is None:
        raise ErroFicha("amortizacao.dt_primeira ausente.")
    datas = datas_periodicas(primeira, vencimento, meses, n)

    if tipo == "sac":
        valor = principal / len(datas)
        return [(d, valor) for d in datas]

    if tipo == "price":
        # O Price precisa de uma taxa por periodo. Num lastro CDI+ ela nao
        # existe ex-ante: depende do CDI futuro. Usa-se a taxa da ficha
        # (`cdi_estimado_aa` + spread, ou so o spread) apenas para dar FORMATO a
        # amortizacao -- e o peso relativo das parcelas que vai para o pipeline.
        taxa_periodo = (1.0 + _taxa_nominal_aa(ficha, rem)) ** (meses / 12.0) - 1.0
        parcela = _pmt(principal, taxa_periodo, len(datas))
        saida, saldo = [], principal
        for data in datas:
            juros = saldo * taxa_periodo
            amortizacao = min(parcela - juros, saldo)
            saida.append((data, amortizacao))
            saldo -= amortizacao
        return saida

    raise ErroFicha(
        f"amortizacao.tipo {tipo!r} desconhecido; use tabela, percentual_saldo, "
        f"bullet, sac ou price."
    )


def _taxa_nominal_aa(ficha: dict, rem: dict) -> float:
    """Taxa cheia a.a. para a conferencia: spread composto com o CDI estimado.

    Sem `cdi_estimado_aa` na ficha, o fluxo de conferencia mostra so o spread --
    o que subestima os juros de um papel CDI+. A coluna do .xlsx diz qual foi
    usada, para a conferencia contra o Termo nao enganar.
    """
    if not rem["indexado"]:
        return rem["taxa_aa"]
    cdi = ficha.get("cdi_estimado_aa")
    if cdi is None:
        return rem["taxa_aa"]
    return (1.0 + float(cdi)) * (1.0 + rem["taxa_aa"]) - 1.0


# --- Parcelas (amortizacao + datas so de juros) ------------------------------

def montar_parcelas(ficha: dict, rem: dict) -> pd.DataFrame:
    """Uma linha por data de pagamento, com a amortizacao daquela data.

    As datas de juros que nao amortizam entram com `vl_amortizacao` = 0: o
    cronograma do pipeline e uma lista de datas de caixa, e a data que so paga
    juros tambem e caixa. Foi assim no CRA da ECOAGRO e na debenture ELFAMEDI.
    """
    feriados = _feriados(ficha)
    modo = str(ficha.get("ajuste_dia_util") or "seguinte").lower()
    vencimento = _data(ficha.get("dt_vencimento"), "dt_vencimento")
    principal = float(ficha["vl_principal"])

    amortizacoes = montar_amortizacoes(ficha, rem)

    juros = ficha.get("juros") or {}
    # O Anexo do Termo costuma listar as datas de cupom uma a uma, e elas nem
    # sempre seguem a periodicidade nominal: o Termo da Tec Plante diz "dia util
    # subsequente" mas o anexo antecipa (30/12/2023 sabado -> 29/12/2023). Quando
    # o documento lista as datas, elas mandam -- gerar por periodicidade
    # inventaria um calendario que o contrato nao tem.
    if juros.get("datas"):
        datas_juros = [_data(d, "juros.datas") for d in juros["datas"]]
    else:
        periodicidade = str(juros.get("periodicidade") or "unica").lower().strip()
        if periodicidade not in MESES_DE:
            raise ErroFicha(
                f"juros.periodicidade {periodicidade!r} desconhecida; use uma de {sorted(MESES_DE)}."
            )
        meses_juros = MESES_DE[periodicidade]
        primeiro_juros = _data_ou_none(
            juros.get("dt_primeiro_pagamento"), "juros.dt_primeiro_pagamento"
        )
        if meses_juros and primeiro_juros is None:
            raise ErroFicha(
                "juros.dt_primeiro_pagamento ausente -- informe as datas do anexo em "
                "`juros.datas` ou a data do primeiro cupom para gerar a serie."
            )
        datas_juros = (
            datas_periodicas(primeiro_juros, vencimento, meses_juros)
            if meses_juros
            else [vencimento]
        )

    # Uniao das datas, ja ajustadas para dia util. O ajuste vem antes da uniao:
    # duas datas diferentes podem cair no mesmo dia util e tem de virar uma so.
    por_data: dict[dt.date, float] = {}
    paga_juros: set[dt.date] = set()
    for data, valor in amortizacoes:
        d = ajustar_dia_util(data, feriados, modo)
        por_data[d] = por_data.get(d, 0.0) + valor
    for data in datas_juros:
        d = ajustar_dia_util(data, feriados, modo)
        por_data.setdefault(d, 0.0)
        paga_juros.add(d)

    datas = sorted(por_data)
    # Arredonda antes de fechar: arredondar depois reintroduziria os centavos
    # que o ajuste acabou de tirar.
    valores = [round(por_data[d], 2) for d in datas]

    # Fecha no principal: o residuo cai na ultima parcela QUE AMORTIZA (Price e
    # percentual de saldo nunca fecham exato). Jogar numa data so de juros
    # criaria uma amortizacao que o Termo nao tem.
    ultima_amortizacao = max(
        (i for i, v in enumerate(valores) if v > 0), default=len(valores) - 1
    )
    diferenca = round(principal - sum(valores), 2)
    if diferenca:
        log.info(
            "Residuo de %.2f absorvido na parcela %d para fechar o principal.",
            diferenca, ultima_amortizacao + 1,
        )
        valores[ultima_amortizacao] = round(valores[ultima_amortizacao] + diferenca, 2)

    return pd.DataFrame({
        "nr_parcela": range(1, len(datas) + 1),
        "dt_vencimento": datas,
        "vl_amortizacao": valores,
        "paga_juros": [d in paga_juros for d in datas],
    })


# --- Fluxo de conferencia ---------------------------------------------------

def projetar_conferencia(ficha: dict, rem: dict, parcelas: pd.DataFrame) -> pd.DataFrame:
    """Fluxo na taxa contratual, para bater com a tabela do Termo.

    Nao e a projecao do relatorio: la os juros vem da curva de DI da data-base e
    o principal vem da carteira. Aqui o principal e o de emissao e a taxa e a do
    contrato -- o que permite comparar linha a linha com o anexo do documento.

    Data que nao paga juros acumula: o juro corrido continua no saldo ate a
    proxima data de cupom, em vez de sumir.
    """
    feriados = _feriados(ficha)
    base = rem["base_taxa"]
    taxa_ad = taxa_diaria(_taxa_nominal_aa(ficha, rem), base)
    inicio = _data_ou_none(ficha.get("dt_inicio_juros")) or _data(
        ficha.get("dt_emissao") or ficha.get("dt_operacao"), "dt_emissao"
    )

    linhas = []
    saldo = float(ficha["vl_principal"])
    acumulado = 0.0
    anterior = inicio

    for linha in parcelas.itertuples(index=False):
        data = linha.dt_vencimento
        dias_corridos = (data - anterior).days
        dias = contar_dias_uteis(anterior, data, feriados) if base == 252 else dias_corridos
        juros_periodo = saldo * ((1.0 + taxa_ad) ** dias - 1.0)
        acumulado += juros_periodo
        juros_pagos = acumulado if linha.paga_juros else 0.0
        acumulado -= juros_pagos
        amortizacao = float(linha.vl_amortizacao)
        linhas.append({
            "nr_parcela": linha.nr_parcela,
            "dt_vencimento": data,
            "qt_dias_corridos": dias_corridos,
            "qt_dias_base": dias,
            "saldo_anterior": round(saldo, 2),
            "vl_juros": round(juros_pagos, 2),
            "vl_amortizacao": round(amortizacao, 2),
            "vl_parcela": round(juros_pagos + amortizacao, 2),
            "juros_acumulados": round(acumulado, 2),
            "saldo_final": round(saldo - amortizacao, 2),
        })
        saldo -= amortizacao
        anterior = data

    return pd.DataFrame(linhas)


# --- Conferencias -----------------------------------------------------------

def conferir(ficha: dict, rem: dict, parcelas: pd.DataFrame, fluxo: pd.DataFrame) -> list[str]:
    """Avisos que o operador precisa ler antes de aceitar o cadastro."""
    avisos: list[str] = []
    principal = float(ficha["vl_principal"])
    vencimento = _data(ficha["dt_vencimento"], "dt_vencimento")

    soma = float(parcelas["vl_amortizacao"].sum())
    if abs(soma - principal) > TOLERANCIA_FECHAMENTO:
        avisos.append(
            f"Amortizacoes somam {soma:,.2f} contra principal {principal:,.2f} "
            f"(diferenca {soma - principal:,.2f})."
        )
    if parcelas["dt_vencimento"].iloc[-1] < vencimento:
        avisos.append(
            f"Ultima parcela ({parcelas['dt_vencimento'].iloc[-1]}) e anterior ao "
            f"vencimento declarado ({vencimento})."
        )
    if (parcelas["vl_amortizacao"] < 0).any():
        avisos.append("Ha amortizacao negativa -- confira a tabela do Termo.")
    if not fluxo.empty and abs(float(fluxo["saldo_final"].iloc[-1])) > TOLERANCIA_FECHAMENTO:
        avisos.append(f"Saldo final {fluxo['saldo_final'].iloc[-1]:,.2f} nao zerou.")

    esperado = ficha.get("n_parcelas_declarado")
    if esperado and int(esperado) != len(parcelas):
        avisos.append(
            f"O Termo declara {int(esperado)} pagamentos; foram montados {len(parcelas)}."
        )
    if rem["indexado"] and ficha.get("cdi_estimado_aa") is None:
        avisos.append(
            f"Papel {rem['indexador']}+{rem['taxa_aa']:.2%}: o fluxo de conferencia usa "
            f"so o spread (sem CDI), entao os juros aqui ficam abaixo do Termo. "
            f"Informe `cdi_estimado_aa` na ficha para comparar com a tabela do anexo. "
            f"Isso NAO afeta o cadastro -- a projecao usa a curva de DI."
        )
    if not ficha.get("feriados"):
        avisos.append(
            "Sem lista de feriados: o ajuste de dia util so evitou sabado e domingo. "
            "Datas que caem em feriado bancario podem estar um dia adiantadas."
        )
    avisos.extend(str(p) for p in ficha.get("pendencias") or [])
    return avisos


# --- Saidas -----------------------------------------------------------------

def montar_cadastro_manual(ficha: dict, rem: dict, parcelas: pd.DataFrame) -> dict:
    """O JSON que `cronograma.carregar_manuais` le, no formato do B989830."""
    return {
        "cnpj_fundo": ficha["cnpj_fundo"],
        "codigo_op": ficha["codigo_op"],
        "emissor": ficha.get("emissor"),
        "tp_instrumento": ficha.get("tp_instrumento"),
        "nr_contrato": ficha.get("nr_contrato"),
        "cd_papel_b3": ficha.get("cd_papel_b3"),
        "vl_principal": float(ficha["vl_principal"]),
        "dt_operacao": ficha.get("dt_operacao"),
        "dt_emissao": ficha.get("dt_emissao"),
        "dt_vencimento": ficha["dt_vencimento"],
        "indexado": rem["indexado"],
        "indexador": rem["indexador"],
        "taxa_aa": rem["taxa_aa"],
        "taxa_ad_efetiva": rem["taxa_ad_efetiva"],
        "base_taxa": rem["base_taxa"],
        "observacao": ficha.get("observacao", ""),
        "parcelas": [
            {"dt_vencimento": linha.dt_vencimento.isoformat(),
             "vl_amortizacao": float(linha.vl_amortizacao)}
            for linha in parcelas.itertuples(index=False)
        ],
    }


def _resumo(ficha: dict, rem: dict, parcelas: pd.DataFrame) -> pd.DataFrame:
    """Parametros extraidos ao lado da fonte no documento, para conferencia."""
    fontes = ficha.get("fontes") or {}
    itens = [
        ("Fundo (CNPJ)", ficha.get("cnpj_fundo"), "cnpj_fundo"),
        ("Codigo da operacao", ficha.get("codigo_op"), "codigo_op"),
        ("CRA / serie", ficha.get("cra_serie"), "cra_serie"),
        ("Securitizadora", ficha.get("emissor"), "emissor"),
        ("Devedor do lastro", ficha.get("devedor"), "devedor"),
        ("Instrumento do lastro", ficha.get("tp_instrumento"), "tp_instrumento"),
        ("Numero do contrato", ficha.get("nr_contrato"), "nr_contrato"),
        ("Codigo B3", ficha.get("cd_papel_b3"), "cd_papel_b3"),
        ("Valor de principal", ficha.get("vl_principal"), "vl_principal"),
        ("Data de emissao", ficha.get("dt_emissao"), "dt_emissao"),
        ("Data de vencimento", ficha.get("dt_vencimento"), "dt_vencimento"),
        ("Remuneracao", _texto_remuneracao(rem), "remuneracao"),
        ("Base da taxa", rem["base_taxa"], "remuneracao"),
        ("Taxa diaria equivalente", rem["taxa_ad_efetiva"], "remuneracao"),
        ("Periodicidade dos juros", (ficha.get("juros") or {}).get("periodicidade"), "juros"),
        ("Tipo de amortizacao", (ficha.get("amortizacao") or {}).get("tipo"), "amortizacao"),
        ("Pagamentos montados", len(parcelas), ""),
        ("Datas que amortizam", int((parcelas["vl_amortizacao"] > 0).sum()), ""),
    ]
    return pd.DataFrame(
        [{"parametro": nome, "valor": valor, "fonte_no_documento": fontes.get(chave, "")}
         for nome, valor, chave in itens]
    )


def _texto_remuneracao(rem: dict) -> str:
    if rem["indexado"]:
        return f"{rem['indexador']} + {rem['taxa_aa']:.4%} a.a."
    return f"{rem['taxa_aa']:.4%} a.a. (pre)"


def gravar_conferencia(
    caminho: Path,
    ficha: dict,
    rem: dict,
    parcelas: pd.DataFrame,
    fluxo: pd.DataFrame,
    avisos: list[str],
) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(caminho, engine="openpyxl") as escritor:
        _resumo(ficha, rem, parcelas).to_excel(escritor, sheet_name="resumo", index=False)
        fluxo.to_excel(escritor, sheet_name="fluxo", index=False)
        pd.DataFrame({"aviso": avisos or ["sem pendencias"]}).to_excel(
            escritor, sheet_name="pendencias", index=False
        )
    return caminho


def processar(
    ficha: dict,
    dir_manuais: Path = DIR_MANUAIS,
    dir_conferencia: Path = DIR_CONFERENCIA,
    gravar_cadastro: bool = True,
) -> dict[str, Any]:
    """Ficha -> cadastro manual + planilha de conferencia. Devolve o que fez."""
    for campo in ("cnpj_fundo", "codigo_op", "vl_principal", "dt_vencimento"):
        if not ficha.get(campo):
            raise ErroFicha(f"Campo obrigatorio ausente na ficha: {campo}")

    rem = _remuneracao(ficha)
    parcelas = montar_parcelas(ficha, rem)
    fluxo = projetar_conferencia(ficha, rem, parcelas)
    avisos = conferir(ficha, rem, parcelas, fluxo)

    codigo = ficha["codigo_op"]
    resultado: dict[str, Any] = {
        "codigo_op": codigo,
        "parcelas": len(parcelas),
        "amortizacoes": int((parcelas["vl_amortizacao"] > 0).sum()),
        "soma_amortizacao": round(float(parcelas["vl_amortizacao"].sum()), 2),
        "primeira_data": parcelas["dt_vencimento"].iloc[0].isoformat(),
        "ultima_data": parcelas["dt_vencimento"].iloc[-1].isoformat(),
        "taxa_ad_efetiva": rem["taxa_ad_efetiva"],
        "avisos": avisos,
    }

    caminho_xlsx = Path(dir_conferencia) / f"{codigo}_conferencia.xlsx"
    gravar_conferencia(caminho_xlsx, ficha, rem, parcelas, fluxo, avisos)
    resultado["conferencia"] = str(caminho_xlsx)

    if gravar_cadastro:
        destino = Path(dir_manuais) / f"{codigo}.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        cadastro = montar_cadastro_manual(ficha, rem, parcelas)
        destino.write_text(
            json.dumps(cadastro, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        resultado["cadastro"] = str(destino)

    return resultado


# --- Pasta de entrada dos documentos ----------------------------------------
#
# O deposito segue a convencao que a importacao de planilhas ja usa: o arquivo
# entra na raiz de `config/cra/` e, depois de virar cadastro, e movido para
# `config/cra/processados/` com carimbo de data. O que muda e que aqui o mesmo
# arquivo carrega DOIS identificadores -- o fundo e a operacao --, porque um
# fundo tem varios CRA.
#
# Nome recomendado:  <cnpj>_<codigo_op>_<descricao livre>.pdf
#     32388135000162_B915955_TecPlante_204a.pdf
#
# O CNPJ tambem pode vir da pasta, o que permite organizar por fundo:
#     config/cra/32.388.135-0001-62/B915955_TecPlante.pdf
#     config/cra/QUATA FIC/B915955_TecPlante.pdf      (nome_interno do cadastro)

SEM_FUNDO = "sem_fundo"
SEM_CODIGO = "sem_codigo"
FUNDO_DESCONHECIDO = "fundo_desconhecido"
SEM_FICHA = "sem_ficha"
FICHA_PRONTA = "ficha_pronta"
CADASTRADO = "cadastrado"

EXPLICACAO = {
    SEM_FUNDO: "nao deu para identificar o fundo pelo nome do arquivo nem pela pasta",
    FUNDO_DESCONHECIDO: "CNPJ identificado, mas nao existe no cadastro de fundos",
    SEM_CODIGO: "falta o Codigo_Op (BXXXXXX/CXXXXXX) no nome do arquivo",
    SEM_FICHA: "documento ainda nao lido -- rode o agente cra-lastro",
    FICHA_PRONTA: "ficha preenchida, cadastro ainda nao gerado -- rode 'lote'",
    CADASTRADO: "cadastro gravado; o documento pode ser arquivado",
}


@dataclass
class Documento:
    """Um PDF na pasta de entrada, com o que foi possivel identificar dele."""

    caminho: Path
    cnpj_fundo: str | None = None
    nome_fundo: str | None = None
    origem_fundo: str = ""
    codigo_op: str | None = None
    origem_codigo: str = ""
    estado: str = SEM_FUNDO
    ficha: Path | None = None
    cadastro: Path | None = None
    # Operacao que o cronograma do fundo desconhece -- nem veio da planilha de
    # conciliacao, nem tem cadastro manual. E diferente de `sem_ficha`: ali o
    # documento so nao foi lido; aqui o pipeline inteiro ignora que o ativo
    # existe, e a projecao de liquidez do fundo esta sem ele.
    novo: bool = False

    @property
    def pendente(self) -> bool:
        return self.estado != CADASTRADO


def _nome_pasta(texto: str) -> str:
    """Nome utilizavel como pasta no Windows."""
    limpo = re.sub(r'[<>:"/\\|?*]', "-", (texto or "").strip())
    return limpo.rstrip(". ") or "_sem_nome"


def _so_alfanumerico(texto: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (texto or "").upper())


def _cnpj_no_texto(texto: str) -> str | None:
    """CNPJ (so digitos) achado em texto cru.

    Nao da para reusar `cronograma.cnpj_do_arquivo` em nome de PASTA: ele passa
    por `Path.stem`, que trata ponto como extensao e corta `32.388.135-0001-62`
    em `32.388`. Em nome de arquivo o `.pdf` protege; em pasta, nao.
    """
    achado = cronograma.PADRAO_CNPJ.search(texto or "")
    return "".join(achado.groups()) if achado else None


def documentos_na_entrada(diretorio: Path = DIR_DOCUMENTOS) -> list[Path]:
    """PDFs depositados, do mais antigo ao mais novo, incluindo subpastas.

    A subpasta `processados/` fica de fora -- o que ja foi arquivado nao volta
    para a fila.
    """
    base = Path(diretorio)
    if not base.exists():
        return []
    processados = (base / "processados").resolve()
    achados: list[Path] = []
    for padrao in PADRAO_DOCUMENTO:
        for caminho in base.rglob(padrao):
            if not caminho.is_file() or caminho.name.startswith("~$"):
                continue
            resolvido = caminho.resolve()
            if processados == resolvido or processados in resolvido.parents:
                continue
            achados.append(caminho)
    # rglob com dois padroes repete no Windows, que nao diferencia maiusculas.
    unicos = {c.resolve(): c for c in achados}
    return sorted(unicos.values(), key=lambda c: c.stat().st_mtime)


def _pastas_ate_raiz(caminho: Path, raiz: Path) -> list[Path]:
    """Pastas do arquivo ate a raiz da entrada, da mais proxima para a mais longe."""
    raiz = Path(raiz).resolve()
    atual = Path(caminho).resolve().parent
    saida: list[Path] = []
    while True:
        saida.append(atual)
        if atual == raiz or atual.parent == atual:
            break
        atual = atual.parent
    return saida


def identificar_fundo(
    caminho: Path,
    fundos: dict[str, Any],
    raiz: Path = DIR_DOCUMENTOS,
) -> tuple[str | None, str]:
    """CNPJ (so digitos) do fundo dono do documento, e de onde ele veio.

    Procura em cascata, do mais explicito para o mais frouxo: CNPJ no nome do
    arquivo, CNPJ na pasta, `nome_interno` do cadastro na pasta. Devolve
    (None, "") quando nada casa -- deduzir o fundo errado e pior do que parar.
    """
    achado = _cnpj_no_texto(Path(caminho).stem)
    if achado:
        return achado, "nome do arquivo"

    por_nome = {
        _so_alfanumerico(f.nome_interno): cnpj
        for cnpj, f in fundos.items()
        if f.nome_interno
    }
    for pasta in _pastas_ate_raiz(caminho, raiz):
        achado = _cnpj_no_texto(pasta.name)
        if achado:
            return achado, f"pasta '{pasta.name}'"
        cnpj = por_nome.get(_so_alfanumerico(pasta.name))
        if cnpj:
            return cnpj, f"pasta '{pasta.name}' (nome_interno)"
    return None, ""


def identificar_codigo(
    caminho: Path,
    raiz: Path = DIR_DOCUMENTOS,
) -> tuple[str | None, str]:
    """Codigo_Op do documento, e de onde ele veio.

    Mesma cascata do fundo: nome do arquivo primeiro, depois as pastas. E o que
    permite a pasta por operacao -- dentro de `C47862/` o Termo, o CDCA e os
    aditamentos herdam o codigo e podem ter nome livre. Sem isso, cada arquivo
    teria de repetir o codigo no nome.
    """
    achado = cronograma.codigo_da_aba(Path(caminho).stem)
    if achado:
        return achado, "nome do arquivo"
    for pasta in _pastas_ate_raiz(caminho, raiz):
        achado = cronograma.codigo_da_aba(pasta.name)
        if achado:
            return achado, f"pasta '{pasta.name}'"
    return None, ""


def inventariar(
    diretorio: Path = DIR_DOCUMENTOS,
    dir_fichas: Path = DIR_FICHAS,
    dir_manuais: Path = DIR_MANUAIS,
) -> list[Documento]:
    """Estado de cada documento da entrada: de quem e, e o que falta nele."""
    fundos = cadastro_fundos.Cadastro().carregar().fundos
    itens: list[Documento] = []
    # Um cronograma por fundo, nao por documento: `carregar` le o JSON da
    # planilha inteiro e um fundo costuma ter dezenas de CRA na fila.
    conhecidos: dict[str, set[str]] = {}

    def operacoes_do_fundo(cnpj: str) -> set[str]:
        if cnpj not in conhecidos:
            crono = cronograma.Cronograma.carregar(cnpj, exigir=False)
            coluna = crono.operacoes.get("codigo_op")
            conhecidos[cnpj] = set(coluna.dropna()) if coluna is not None else set()
        return conhecidos[cnpj]

    for caminho in documentos_na_entrada(diretorio):
        item = Documento(caminho=caminho)
        cnpj, origem = identificar_fundo(caminho, fundos, diretorio)
        item.cnpj_fundo, item.origem_fundo = cnpj, origem
        item.codigo_op, item.origem_codigo = identificar_codigo(caminho, diretorio)

        if cnpj is None:
            item.estado = SEM_FUNDO
        elif cnpj not in fundos:
            item.estado = FUNDO_DESCONHECIDO
        else:
            item.nome_fundo = fundos[cnpj].nome_interno
            if item.codigo_op is None:
                item.estado = SEM_CODIGO
            else:
                ficha = Path(dir_fichas) / f"{item.codigo_op}.json"
                manual = Path(dir_manuais) / f"{item.codigo_op}.json"
                item.ficha = ficha if ficha.exists() else None
                item.cadastro = manual if manual.exists() else None
                item.novo = item.codigo_op not in operacoes_do_fundo(cnpj)
                if manual.exists():
                    item.estado = CADASTRADO
                elif ficha.exists():
                    item.estado = FICHA_PRONTA
                else:
                    item.estado = SEM_FICHA
        itens.append(item)
    return itens


def organizar(
    diretorio: Path = DIR_DOCUMENTOS,
    dir_fichas: Path = DIR_FICHAS,
    dir_manuais: Path = DIR_MANUAIS,
) -> list[dict[str, Any]]:
    """Recolhe cada documento para `<entrada>/<fundo>/<codigo_op>/`.

    Uma operacao raramente tem um documento so -- vem o Termo de Securitizacao,
    o CDCA, o contrato de cessao fiduciaria, aditamentos. A pasta por codigo
    junta tudo o que fala do mesmo papel.

    Documento sem fundo ou sem codigo fica onde esta: mover para uma pasta
    adivinhada e pior do que deixar na raiz pedindo atencao.
    """
    resultados: list[dict[str, Any]] = []

    for item in inventariar(diretorio, dir_fichas, dir_manuais):
        linha: dict[str, Any] = {
            "arquivo": item.caminho.name,
            "fundo": item.nome_fundo or item.cnpj_fundo or "?",
            "codigo_op": item.codigo_op or "?",
            "estado": item.estado,
            "novo": item.novo,
        }
        if item.cnpj_fundo is None or item.codigo_op is None:
            linha["acao"] = "ignorado"
            resultados.append(linha)
            continue

        alvo = (Path(diretorio) / _nome_pasta(item.nome_fundo or item.cnpj_fundo)
                / _nome_pasta(item.codigo_op))
        if item.caminho.parent.resolve() == alvo.resolve():
            linha["acao"] = "ja_organizado"
            resultados.append(linha)
            continue

        alvo.mkdir(parents=True, exist_ok=True)
        destino = alvo / item.caminho.name
        if destino.exists():
            # Nao sobrepoe homonimo: pode ser um aditamento com o mesmo nome.
            carimbo = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            destino = alvo / f"{item.caminho.stem}_{carimbo}{item.caminho.suffix}"
        shutil.move(str(item.caminho), str(destino))
        linha["acao"] = "movido"
        linha["destino"] = str(destino)
        resultados.append(linha)

    return resultados


def _avisar_novos(itens: list[Documento]) -> None:
    """Destaca as operacoes que o cronograma do fundo ainda nao conhece."""
    novos = [i for i in itens if i.novo]
    if not novos:
        return
    print()
    print("!" * 70)
    print(f"!! {len(novos)} ATIVO(S) NOVO(S) -- fora do cronograma do fundo")
    print("!" * 70)
    for i in sorted(novos, key=lambda i: (i.nome_fundo or "", i.codigo_op or "")):
        print(f"   {i.nome_fundo or i.cnpj_fundo:<20} {i.codigo_op:<10} {i.caminho.name}")
    print()
    print("   Nao vieram da planilha de conciliacao nem tem cadastro manual:")
    print("   enquanto nao forem cadastrados, ficam de fora da projecao de liquidez.")


def processar_lote(
    diretorio: Path = DIR_DOCUMENTOS,
    dir_fichas: Path = DIR_FICHAS,
    dir_manuais: Path = DIR_MANUAIS,
    dir_conferencia: Path = DIR_CONFERENCIA,
    arquivar: bool = True,
) -> list[dict[str, Any]]:
    """Gera o cadastro de todo documento com ficha pronta e arquiva o que fechou.

    O documento so vai para `processados/` depois que o cadastro esta gravado --
    arquivar antes esconderia o PDF de quem ainda precisa le-lo.
    """
    resultados: list[dict[str, Any]] = []
    itens = inventariar(diretorio, dir_fichas, dir_manuais)

    for item in itens:
        linha: dict[str, Any] = {
            "arquivo": item.caminho.name,
            "fundo": item.nome_fundo or item.cnpj_fundo or "?",
            "codigo_op": item.codigo_op or "?",
            "estado": item.estado,
            "novo": item.novo,
        }

        if item.estado == FICHA_PRONTA:
            try:
                ficha = json.loads(item.ficha.read_text(encoding="utf-8"))
                saida = processar(
                    ficha,
                    dir_manuais=dir_manuais,
                    dir_conferencia=dir_conferencia,
                )
            except (OSError, json.JSONDecodeError, ErroFicha) as exc:
                linha.update(estado="erro", erro=str(exc)[:300])
                log.error("%s: %s", item.caminho.name, exc)
                resultados.append(linha)
                continue
            linha.update(
                estado=CADASTRADO,
                cadastro=saida["cadastro"],
                conferencia=saida["conferencia"],
                parcelas=saida["parcelas"],
                avisos=len(saida["avisos"]),
            )
            item.estado = CADASTRADO

        if item.estado == CADASTRADO and arquivar:
            # Derivado da entrada recebida, nao da constante: com `--entrada`
            # apontando para outro lugar, arquivar no padrao tiraria o
            # documento de uma pasta e o largaria em outra. E espelha
            # <fundo>/<codigo_op> dentro de processados, para o arquivo morto
            # ficar navegavel do mesmo jeito que a fila.
            destino = Path(diretorio) / "processados"
            if item.nome_fundo or item.cnpj_fundo:
                destino = destino / _nome_pasta(item.nome_fundo or item.cnpj_fundo)
            if item.codigo_op:
                destino = destino / _nome_pasta(item.codigo_op)
            alvo = cronograma.arquivar(item.caminho, destino)
            linha["arquivado_em"] = str(alvo)

        resultados.append(linha)

    _avisar_novos(itens)
    return resultados


def _imprimir_inventario(itens: list[Documento]) -> None:
    if not itens:
        print(f"Nenhum documento em {DIR_DOCUMENTOS}. Deposite os PDF e rode de novo.")
        return

    largura = max(len(i.caminho.name) for i in itens)
    print(f"{len(itens)} documento(s) em {DIR_DOCUMENTOS}\n")
    print(f"{'arquivo':<{largura}}  {'fundo':<22} {'codigo_op':<10} {'novo':<5} estado")
    print("-" * (largura + 51))
    for item in sorted(itens, key=lambda i: (i.estado, i.caminho.name)):
        fundo = (item.nome_fundo or item.cnpj_fundo or "-")[:22]
        print(f"{item.caminho.name:<{largura}}  {fundo:<22} "
              f"{item.codigo_op or '-':<10} {'SIM' if item.novo else '':<5} {item.estado}")

    print()
    for estado in (SEM_FUNDO, FUNDO_DESCONHECIDO, SEM_CODIGO, SEM_FICHA,
                   FICHA_PRONTA, CADASTRADO):
        quantos = sum(1 for i in itens if i.estado == estado)
        if quantos:
            print(f"  {quantos:3d} {estado:<20} {EXPLICACAO[estado]}")

    _avisar_novos(itens)


# --- Texto dos documentos ---------------------------------------------------

DIR_TEXTO = Path("saidas") / "cra" / "_texto"


def extrair_texto(pdf: Path, destino: Path | None = None) -> Path:
    """Extrai o texto do PDF para um .txt, com marcador de pagina.

    Existe porque o renderizador de PDF do editor depende do poppler, ausente
    no Windows. O `pypdf` e Python puro e resolve sem dependencia de sistema.

    Os marcadores `[[ pagina N ]]` preservam a referencia de pagina, que e o que
    a ficha cita em `fontes` -- sem eles, a citacao viraria numero de linha de um
    arquivo que so existe aqui.

    Termo escaneado (imagem, sem camada de texto) sai vazio ou quase. O aviso
    aparece no fim; nesse caso nao ha o que extrair sem OCR.
    """
    from pypdf import PdfReader

    pdf = Path(pdf)
    destino = Path(destino) if destino else DIR_TEXTO / f"{pdf.stem}.txt"
    destino.parent.mkdir(parents=True, exist_ok=True)

    leitor = PdfReader(str(pdf))
    partes, vazias = [], 0
    for numero, pagina in enumerate(leitor.pages, start=1):
        try:
            texto = pagina.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - pagina ruim nao derruba o resto
            log.warning("%s pagina %d: %s", pdf.name, numero, exc)
            texto = ""
        if not texto.strip():
            vazias += 1
        partes.append(f"\n[[ pagina {numero} ]]\n{texto}")

    destino.write_text("".join(partes), encoding="utf-8")
    log.info("%s: %d paginas, %d sem texto -> %s",
             pdf.name, len(leitor.pages), vazias, destino)
    if vazias == len(leitor.pages):
        log.warning("%s: nenhuma pagina tem camada de texto. Documento escaneado? "
                    "Sem OCR nao ha o que ler.", pdf.name)
    return destino


# --- Linha de comando -------------------------------------------------------

def _comando_ficha(args) -> int:
    try:
        ficha = json.loads(args.ficha.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Nao foi possivel ler a ficha {args.ficha}: {exc}", file=sys.stderr)
        return 2
    try:
        resultado = processar(
            ficha,
            dir_manuais=args.manuais,
            dir_conferencia=args.saida,
            gravar_cadastro=not args.so_conferencia,
        )
    except ErroFicha as exc:
        print(f"Ficha invalida: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0


def _comando_inventario(args) -> int:
    _imprimir_inventario(inventariar(args.entrada, args.fichas, args.manuais))
    return 0


def _comando_texto(args) -> int:
    alvos = []
    for caminho in args.pdf:
        caminho = Path(caminho)
        if caminho.is_dir():
            # dict por caminho resolvido: no Windows `*.pdf` e `*.PDF` casam o
            # mesmo arquivo, e sem isso cada PDF seria extraido duas vezes.
            achados = {c.resolve(): c
                       for p in PADRAO_DOCUMENTO for c in caminho.rglob(p)}
            alvos.extend(sorted(achados.values()))
        else:
            alvos.append(caminho)
    if not alvos:
        print("Nenhum PDF encontrado.", file=sys.stderr)
        return 2
    for caminho in alvos:
        print(extrair_texto(caminho, args.destino))
    return 0


def _comando_organizar(args) -> int:
    resultados = organizar(
        diretorio=args.entrada, dir_fichas=args.fichas, dir_manuais=args.manuais)
    print(json.dumps(resultados, ensure_ascii=False, indent=2))
    _avisar_novos(inventariar(args.entrada, args.fichas, args.manuais))
    return 0


def _comando_lote(args) -> int:
    resultados = processar_lote(
        diretorio=args.entrada,
        dir_fichas=args.fichas,
        dir_manuais=args.manuais,
        dir_conferencia=args.saida,
        arquivar=not args.nao_arquivar,
    )
    print(json.dumps(resultados, ensure_ascii=False, indent=2))
    return 1 if any(r["estado"] == "erro" for r in resultados) else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        prog="python -m liquidez.cra",
        description="Fluxo dos CRA: inventario da pasta de entrada e montagem "
                    "do cadastro a partir da ficha extraida do Termo.",
    )
    sub = parser.add_subparsers(dest="comando")

    def com_pastas(p, entrada=False, saida=False):
        p.add_argument("--manuais", type=Path, default=DIR_MANUAIS,
                       help=f"pasta do cadastro manual (padrao: {DIR_MANUAIS})")
        if entrada:
            p.add_argument("--entrada", type=Path, default=DIR_DOCUMENTOS,
                           help=f"pasta dos documentos (padrao: {DIR_DOCUMENTOS})")
            p.add_argument("--fichas", type=Path, default=DIR_FICHAS,
                           help=f"pasta das fichas (padrao: {DIR_FICHAS})")
        if saida:
            p.add_argument("--saida", type=Path, default=DIR_CONFERENCIA,
                           help=f"pasta da conferencia (padrao: {DIR_CONFERENCIA})")
        return p

    p_ficha = com_pastas(
        sub.add_parser("ficha", help="monta o cadastro de uma ficha"), saida=True)
    p_ficha.add_argument("ficha", type=Path, help="JSON com os parametros lidos do Termo")
    p_ficha.add_argument("--so-conferencia", action="store_true",
                         help="gera o .xlsx e nao grava o cadastro manual")
    p_ficha.set_defaults(funcao=_comando_ficha)

    com_pastas(
        sub.add_parser("inventario", help="lista os documentos da entrada e o estado de cada um"),
        entrada=True,
    ).set_defaults(funcao=_comando_inventario)

    p_texto = sub.add_parser(
        "texto", help="extrai o texto de um PDF (ou de uma pasta inteira) para .txt")
    p_texto.add_argument("pdf", type=Path, nargs="+",
                         help="arquivo(s) PDF ou pasta com PDF")
    p_texto.add_argument("--destino", type=Path, default=None,
                         help=f"arquivo de saida (padrao: {DIR_TEXTO}/<nome>.txt)")
    p_texto.set_defaults(funcao=_comando_texto)

    com_pastas(
        sub.add_parser("organizar",
                       help="recolhe os documentos para <fundo>/<codigo_op>/ e avisa dos novos"),
        entrada=True,
    ).set_defaults(funcao=_comando_organizar)

    p_lote = com_pastas(
        sub.add_parser("lote", help="processa as fichas prontas e arquiva os documentos"),
        entrada=True, saida=True)
    p_lote.add_argument("--nao-arquivar", action="store_true",
                        help="grava os cadastros mas deixa os documentos na entrada")
    p_lote.set_defaults(funcao=_comando_lote)

    # `python -m liquidez.cra caminho/ficha.json` continua valendo: o caminho de
    # uma ficha no lugar do subcomando vira `ficha <caminho>`.
    argumentos = list(sys.argv[1:] if argv is None else argv)
    if argumentos and argumentos[0] not in sub.choices and argumentos[0].endswith(".json"):
        argumentos.insert(0, "ficha")

    args = parser.parse_args(argumentos)
    if not getattr(args, "funcao", None):
        parser.print_help()
        return 2
    return args.funcao(args)


if __name__ == "__main__":
    raise SystemExit(main())
