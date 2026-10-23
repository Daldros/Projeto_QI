"""Analise da carteira por produto e faixa de prazo -- etapa 6 (`OrgCarteira`).

Distribui a exposicao da carteira tratada numa matriz produto x faixa de prazo,
do dia 0 ate o prazo de cotizacao do fundo (cc), mais as linhas ">prazo" e "vencidos".
Equivale a aba `Analise carteira` da planilha.

Diferencas conscientes em relacao ao VBA:

- O produto DC nao usa mais a aba `fluxo` (que nao esta disponivel): entra pela
  propria carteira, somado por faixa de prazo como qualquer outro produto.
  Quando a abertura do fluxo existir, so o DC muda -- o resto continua igual.
- Onde o VBA fazia loops de SumIfs celula a celula, aqui e um pivot.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .regras_adm import PRAZO_LIQUIDACAO_BOLSA, RegraADM, normalizar
from .tratamentos import (
    BASES_TITULO_PUBLICO,
    COL_ATIVO_VENCIDO,
    COL_ATIVOS_EXCLUIDOS,
    COL_BASE_ORIGEM,
    COL_QTDE_DIAS,
    NAO,
    PRAZO_TITULO_PUBLICO,
    SIM,
)

log = logging.getLogger(__name__)

COL_PRODUTO = COL_BASE_ORIGEM
COL_VALOR = "Valor_Liquido"

LINHA_EXPOSICAO = "Exposição Total"
LINHA_VENCIDOS = "Vencidos"


def _rotulo_faixa(prazo_cotizacao: int) -> str:
    return f">{prazo_cotizacao}"


def analisar_carteira(
    carteira: pd.DataFrame,
    prazo_cotizacao: int,
    regra: RegraADM,
) -> pd.DataFrame:
    """Matriz produto (colunas) x faixa de prazo (linhas) com a exposicao.

    Linhas, na ordem: `Exposição Total`, 0, 1, ..., prazo_cotizacao, `>prazo_cotizacao`,
    `Vencidos`. Uma coluna por produto (`Base_Origem`) mais `Exposição Total`
    com o total da linha.

    Regras (do `OrgCarteira`):
    - `Base_Origem` em `regra.bases_fora_do_pl` nao entra em nada: nao e posicao
      do fundo. E o `PL COTAS` do DAYCOVAL, a subordinacao escrita na carteira.
    - Exposição Total por produto = soma de `Valor_Liquido`. Para a PLANNER,
      desconsidera as linhas marcadas como excluidas.
    - Cada faixa j (0..prazo_cotizacao) = soma das linhas com `Qtde_Dias == j`, nao
      vencidas e nao excluidas.
    - `>prazo_cotizacao` = idem com `Qtde_Dias > prazo_cotizacao`.
    - `Vencidos` = linhas com `Ativo Vencido = SIM` e nao excluidas, qualquer prazo.
    """
    faixa_alta = _rotulo_faixa(prazo_cotizacao)
    ordem_linhas = [LINHA_EXPOSICAO] + list(range(prazo_cotizacao + 1)) + [faixa_alta, LINHA_VENCIDOS]

    if carteira.empty:
        return pd.DataFrame(index=ordem_linhas)

    df = carteira.copy()
    df[COL_PRODUTO] = df[COL_PRODUTO].fillna("").astype(str).str.strip()
    df = df[df[COL_PRODUTO] != ""]

    # Base que nao e posicao do fundo sai antes de tudo -- da Exposicao Total,
    # que e o PL, e das faixas. O caso e o `PL COTAS` do DAYCOVAL: uma linha
    # negativa com o patrimonio das classes senior e mezanino, que e a
    # subordinacao escrita dentro da carteira, e nao um ativo. Somando-a, o PL
    # do ECOAGRO MULTIPLICA saia R$ 14,5 mi -- o patrimonio da JUNIOR -- e o
    # Caixa Potencial, que conta ativo, virava 283,55% de um denominador de
    # equity. Fora a linha, o PL e o ativo (R$ 106,3 mi) e a razao da 38,61%.
    if regra.bases_fora_do_pl:
        alvos = {normalizar(b) for b in regra.bases_fora_do_pl}
        fora = df[COL_PRODUTO].map(normalizar).isin(alvos)
        if fora.any():
            log.info("Fora do PL: %d linha(s) em %s, somando %s.",
                     int(fora.sum()), ", ".join(regra.bases_fora_do_pl),
                     f"{df.loc[fora, COL_VALOR].sum():,.2f}")
            df = df[~fora]
    df[COL_VALOR] = pd.to_numeric(df[COL_VALOR], errors="coerce").fillna(0.0)
    dias = pd.to_numeric(df[COL_QTDE_DIAS], errors="coerce")
    vencido = df[COL_ATIVO_VENCIDO].astype(str).str.upper() == SIM
    excluido = df[COL_ATIVOS_EXCLUIDOS].astype(str).str.upper() == SIM

    produtos = sorted(df[COL_PRODUTO].unique())
    matriz = pd.DataFrame(0.0, index=ordem_linhas, columns=produtos)

    # --- Exposição Total por produto -------------------------------------
    total_base = df if not regra.exclui_do_total else df[~excluido]
    exposicao = total_base.groupby(COL_PRODUTO)[COL_VALOR].sum()
    matriz.loc[LINHA_EXPOSICAO, exposicao.index] = exposicao.values

    # --- Distribuição por faixa (não vencidos, não excluídos) ------------
    # Linhas sem prazo (Qtde_Dias nulo) nao entram em nenhuma faixa -- ficam
    # apenas na Exposição Total, como no VBA (celula X vazia).
    validos_mask = (~vencido) & (~excluido) & dias.notna()
    validos = df[validos_mask].copy()
    dias_validos = dias[validos_mask]
    if not validos.empty:
        acima = dias_validos > prazo_cotizacao
        faixa = dias_validos.clip(lower=0).round().astype(int).astype(object)
        faixa[acima] = faixa_alta
        validos = validos.assign(_faixa=faixa.values)
        pivo = validos.pivot_table(
            index="_faixa", columns=COL_PRODUTO, values=COL_VALOR, aggfunc="sum"
        )
        for faixa_rotulo in pivo.index:
            for produto in pivo.columns:
                valor = pivo.loc[faixa_rotulo, produto]
                if pd.notna(valor) and faixa_rotulo in matriz.index:
                    matriz.loc[faixa_rotulo, produto] = valor

    # --- Vencidos (não excluídos), qualquer prazo ------------------------
    venc = df[vencido & ~excluido]
    if not venc.empty:
        soma_venc = venc.groupby(COL_PRODUTO)[COL_VALOR].sum()
        matriz.loc[LINHA_VENCIDOS, soma_venc.index] = soma_venc.values

    # --- Coluna de total da linha ----------------------------------------
    matriz[LINHA_EXPOSICAO] = matriz[produtos].sum(axis=1)
    return matriz


# ---------------------------------------------------------------------------
# Etapa 7 -- FiltrarCopiarOutrosFundos: cotas de outros fundos, uma a uma
# ---------------------------------------------------------------------------

COL_COD_OP = "Codigo_Op"
COL_COD_ATIVO = "Codigo_Ativo"

COLUNAS_OUTROS_FUNDOS = ("cnpj_fundo", "nome_fundo", "cod_fundo", "fundo", "exposicao")


def outros_fundos(carteira: pd.DataFrame, fundo, regra: RegraADM) -> pd.DataFrame:
    """Lista as posicoes em cotas de outros fundos, uma linha por posicao.

    Corresponde a tabela 2 da aba `Analise carteira`. Filtra a carteira por
    `Base_Origem` conforme o administrador; a PLANNER ainda exclui
    `Codigo_Ativo = 'PDD'`. Sem agregacao: reproduz a copia linha a linha do VBA.
    """
    vazio = pd.DataFrame(columns=list(COLUNAS_OUTROS_FUNDOS))
    if carteira.empty or not regra.filtro_outros_fundos:
        return vazio

    produto = carteira[COL_PRODUTO].fillna("").astype(str).str.strip().str.upper()
    alvos = {p.upper() for p in regra.filtro_outros_fundos}
    selecao = produto.isin(alvos)
    if regra.outros_fundos_exclui_pdd:
        cod = carteira[COL_COD_ATIVO].fillna("").astype(str).str.strip().str.upper()
        selecao &= cod != "PDD"

    sub = carteira[selecao]
    if sub.empty:
        return vazio

    out = pd.DataFrame(
        {
            "cnpj_fundo": fundo.cnpj,
            "nome_fundo": fundo.nome_interno,
            "cod_fundo": sub[COL_COD_OP].fillna("").astype(str).str.strip(),
            "fundo": sub[COL_COD_ATIVO].fillna("").astype(str).str.strip(),
            "exposicao": pd.to_numeric(sub[COL_VALOR], errors="coerce").fillna(0.0),
        }
    )
    return out[list(COLUNAS_OUTROS_FUNDOS)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Etapa 8 -- CalculoExpBD: exposicao do estoque de recebiveis por faixa
# ---------------------------------------------------------------------------

COL_STATUS = "Status_Recebivel"
COL_PRAZO_BD = "Prazo_Atual_Dias_Corridos"
COL_VALOR_BD = "Valor_Presente"
COL_FAIXA_PDD = "FAIXA_PDD"
COL_CARREGO = "Vencidos"  # marca da etapa 4 (VencidosBD)

COLS_ESTOQUE = ("Vencidos", "A Vencer", "Vencidos C/ Carrego", "A Vencer C/ Carrego")


COL_ID_RECEBIVEL = "ID_Recebivel"


def _estoque_proprio(bd_tratado: pd.DataFrame, carteira: pd.DataFrame) -> pd.Series:
    """Linhas do BD que sao o DC do proprio fundo, ja contado na etapa 6.

    A consulta do estoque traz duas coisas sob o mesmo CNPJ: o estoque da
    carteira investida -- que e o que esta etapa mede -- e o recebivel proprio
    do fundo, que a carteira ja apresenta como DC. Conferido no GAD FIDC NP:
    141 linhas de cada lado, o `Codigo_Op` da carteira E o `ID_Recebivel` do BD,
    e os 116 valores distintos casam um a um.

    Ate 10/08/2026 essas linhas saiam por acidente: nenhuma delas tem FAIXA_PDD
    preenchida, e o filtro de PDD as descartava. Ao remover aquele filtro, a
    exclusao passa a ser explicita -- senao o estoque do GAD saltaria de
    R$ 14,7 mi para R$ 119,9 mi.
    """
    vazio = pd.Series(False, index=bd_tratado.index)
    if carteira is None or carteira.empty:
        return vazio
    if COL_ID_RECEBIVEL not in bd_tratado.columns or COL_COD_OP not in carteira.columns:
        return vazio
    dc = carteira[COL_PRODUTO].fillna("").astype(str).str.strip().str.upper() == "DC"
    codigos = set(carteira.loc[dc, COL_COD_OP].astype(str).str.strip())
    if not codigos:
        return vazio
    return bd_tratado[COL_ID_RECEBIVEL].astype(str).str.strip().isin(codigos)


def exposicao_estoque(
    bd_tratado: pd.DataFrame,
    prazo_cotizacao: int,
    carteira: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Distribui a exposicao do estoque por faixa de prazo (tabela 3).

    Considera apenas recebiveis com `FAIXA_PDD` preenchida. Quatro colunas:

    - Vencidos / A Vencer: cortadas por `Status_Recebivel` (comparacao sem
      diferenciar maiusculas, como o Excel: ha "A vencer" e "A Vencer" na base).
    - Vencidos C/ Carrego / A Vencer C/ Carrego: cortadas pela marca de carrego
      (coluna `Vencidos` da etapa 4): SIM tem sacado com titulo vencido, NAO nao.

    So "A Vencer" e "A Vencer C/ Carrego" sao distribuidas por faixa -- o que
    esta vencido nao tem prazo futuro e aparece apenas no total (linha
    `Exposição`), com as celulas das faixas vazias.
    """
    faixa_alta = _rotulo_faixa(prazo_cotizacao)
    ordem = ["Exposição"] + list(range(prazo_cotizacao + 1)) + [faixa_alta]
    tabela = pd.DataFrame(index=ordem, columns=list(COLS_ESTOQUE), dtype="object")

    if bd_tratado.empty:
        return tabela

    df = bd_tratado
    # O FAIXA_PDD deixou de ser filtro de entrada (10/08/2026). Ele so e
    # preenchido quando ha provisao, entao descartava recebivel a vencer e
    # adimplente -- no GAD eram 33 duplicatas e contratos em dia, R$ 5,53 mi,
    # dos quais R$ 2,12 mi vencendo dentro da janela. Com a remocao, o "A
    # Vencer" do estoque passa a fechar com a cota da carteira.
    #
    # O que sai agora e o estoque PROPRIO do fundo, por criterio explicito.
    proprio = _estoque_proprio(df, carteira)
    if proprio.any():
        df = df[~proprio]
    valor = pd.to_numeric(df[COL_VALOR_BD], errors="coerce").fillna(0.0)
    status = df[COL_STATUS].fillna("").astype(str).str.strip().str.upper()
    carrego = df[COL_CARREGO].astype(str).str.upper() if COL_CARREGO in df.columns else pd.Series("", index=df.index)
    dias = pd.to_numeric(df[COL_PRAZO_BD], errors="coerce")

    eh_vencido = status == "VENCIDO"
    eh_a_vencer = status == "A VENCER"
    eh_carrego_sim = carrego == SIM
    eh_carrego_nao = carrego == NAO

    # Linha de total (Exposição)
    tabela.loc["Exposição", "Vencidos"] = valor[eh_vencido].sum()
    tabela.loc["Exposição", "A Vencer"] = valor[eh_a_vencer].sum()
    tabela.loc["Exposição", "Vencidos C/ Carrego"] = valor[eh_carrego_sim].sum()
    tabela.loc["Exposição", "A Vencer C/ Carrego"] = valor[eh_carrego_nao].sum()

    # Distribuicao por faixa apenas das colunas "A Vencer" e "A Vencer C/ Carrego"
    def por_faixa(mask: pd.Series) -> pd.Series:
        sel = mask & dias.notna()
        faixas = dias[sel].clip(lower=0).round().astype(int)
        faixas = faixas.where(dias[sel] <= prazo_cotizacao, other=-1)  # -1 marca o >prazo
        soma = valor[sel].groupby(faixas).sum()
        resultado = {}
        for f, v in soma.items():
            resultado[faixa_alta if f == -1 else int(f)] = v
        return resultado

    for faixa, v in por_faixa(eh_a_vencer).items():
        if faixa in tabela.index:
            tabela.loc[faixa, "A Vencer"] = v
    for faixa, v in por_faixa(eh_carrego_nao).items():
        if faixa in tabela.index:
            tabela.loc[faixa, "A Vencer C/ Carrego"] = v

    return tabela


# ---------------------------------------------------------------------------
# Etapa 9 -- MontarRelatorioLiquidez: consolidacao final
# ---------------------------------------------------------------------------

COLUNAS_RELATORIO = (
    "cnpj_fundo", "nome_fundo", "codigo_ativo",
    "exposicao", "liq_duration", "valor_liquido", "origem",
)

# Produtos que nao entram como linha propria no relatorio (vao pelo laco 2).
# Mantido so como piso: o conjunto de verdade vem do administrador do fundo,
# via `_produtos_de_cotas`. Deixar fixo aqui foi o que quase produziu contagem
# em dobro quando novos administradores entraram -- a linha ia para o laco 2
# como cota E continuava no laco 1 como produto.
PRODUTOS_OUTROS = {"OUTROS FUNDOS", "FUNDOS-FUNDOS"}


def _produtos_de_cotas(fundo) -> set[str]:
    """Rotulos que representam cota de fundo para o administrador deste fundo.

    Sai da MESMA fonte que alimenta a etapa 7 (`regras_adm`), para que o laco 1
    pule exatamente o que o laco 2 recolhe. Se as duas listas divergirem, o
    valor aparece duas vezes no relatorio.
    """
    from .regras_adm import obter as regra_de

    regra = regra_de(getattr(fundo, "administrador", None))
    return PRODUTOS_OUTROS | {p.strip().upper() for p in regra.filtro_outros_fundos}


def _produtos_de_bolsa(fundo) -> set[str]:
    """Rotulos de `Base_Origem` que sao acao/ETF para este administrador.

    Mesma fonte que `calcular_dias` usa para carimbar o D+2 na carteira. A
    duplicidade seria o problema: com a matriz distribuindo o valor na faixa 2 e
    a tabela do relatorio dizendo "Liq. Duration = 999", a mesma pagina afirmaria
    que o papel liquida em dois dias e que nao liquida nunca.
    """
    from .regras_adm import obter as regra_de

    regra = regra_de(getattr(fundo, "administrador", None))
    return {normalizar(b) for b in regra.bases_bolsa}

VALOR_CHEIO = {"CPR", "TESOURARIA"}
COL_A_VENCER_CARREGO = "A Vencer C/ Carrego"

# Origem da linha do relatorio. A de estoque tem tratamento proprio na hora de
# somar linhas repetidas -- o `valor_liquido` dela e do fundo, nao da posicao.
ORIGEM_A_VENCER = "outro_fundo_a_vencer"

# ---------------------------------------------------------------------------
# O Valor Liquido do produto projetado (ver `_liquido_do_fluxo`)
# ---------------------------------------------------------------------------

# Produto cujo Valor Liquido sai do fluxo projetado em vez da matriz. E o mesmo
# produto que `fluxo.posicoes_do_produto` projeta (`bullets.PRODUTO_BULLET`).
PRODUTO_DO_FLUXO = "DC"

# Como medir a parcela que cabe na janela. Decisao do usuario em 29/09/2026: o
# Valor Liquido tem de considerar todo fluxo que vence antes da data de resgate.
# Resta escolher em que moeda.
#
#   "marcado" -- `vl_amortizacao + vl_acrual`, o valor de hoje. E a ancora que o
#       `fluxo` confere contra a carteira (cli.py:1152) e bate ao centavo. Casa
#       com a outra perna da mesma coluna, `A Vencer C/ Carrego` do estoque, que
#       tambem e marcada ate hoje.
#   "caixa"   -- `vl_parcela`, o dinheiro que entra na conta, com o juro do
#       trecho de hoje ate o vencimento. Responde "quanto recebo", nao "quanto
#       valho".
#
# "marcado" e o padrao para nao misturar duas convencoes na mesma coluna: pela
# parcela cheia o DC carregaria juro futuro e o estoque nao. Diferenca medida em
# 31/08/2026: R$ 224,9 mi contra R$ 240,2 mi nos 17 fundos com fluxo.
MOEDA_DO_FLUXO = "marcado"


def _liquido_do_fluxo(fluxo, prazo_cotizacao: int) -> float | None:
    """Quanto do fluxo projetado vence dentro da janela de resgate.

    Por que isto existe. A matriz data cada linha da carteira pelo vencimento
    FINAL da operacao (`tratamentos.calcular_dias`), entao o Valor Liquido lia o
    DC como se toda operacao fosse bullet: operacao que paga em parcelas mensais
    e vence depois da janela contava zero, mesmo recebendo dentro dela. No
    MULTIPLICA FIDC de 31/08/2026 eram R$ 5,8 mi invisiveis; no MULTIPLICA LONG
    TERM, R$ 23,6 mi.

    Duas exclusoes, ambas necessarias:

    * OPERACAO COM EMISSOR VENCIDO sai. O fluxo projeta operacao vencida porque o
      cronograma dela existe, mas a matriz manda essa linha para "Vencido", fora
      das faixas. Sem o filtro, R$ 101,3 mi de caixa de emissor inadimplente
      entrariam na janela dos 17 fundos -- e no grafico da escada apareceriam
      duas vezes, ja que o rodape tambem os soma em "Vencidos".
    * JURO A APROPRIAR sai por padrao -- ver `MOEDA_DO_FLUXO`.

    Devolve None quando nao ha fluxo utilizavel, e ai o chamador fica com a
    matriz. None e nao 0.0 de proposito: zero afirmaria que o produto nao tem
    liquidez, e a ausencia de fluxo nao afirma isso.
    """
    if fluxo is None or len(fluxo) == 0:
        return None
    f = fluxo
    if "qt_dias" not in f.columns:
        return None

    from .fluxo import COL_VENCIDO_FLUXO

    dias = pd.to_numeric(f["qt_dias"], errors="coerce")
    dentro = dias.notna() & (dias <= prazo_cotizacao)
    if COL_VENCIDO_FLUXO in f.columns:
        dentro &= ~f[COL_VENCIDO_FLUXO].fillna(False).astype(bool)
    else:
        # Particao antiga, gravada antes de 29/09/2026. Preferir a matriz a
        # devolver um numero que conta emissor inadimplente como caixa.
        log.warning(
            "fato_fluxo sem '%s': o Valor Liquido de %s segue pela matriz. "
            "Regere o fluxo (python -m liquidez fluxo --fatos).",
            COL_VENCIDO_FLUXO, PRODUTO_DO_FLUXO)
        return None

    if not dentro.any():
        return 0.0
    if MOEDA_DO_FLUXO == "caixa":
        return float(pd.to_numeric(f.loc[dentro, "vl_parcela"],
                                   errors="coerce").fillna(0.0).sum())
    amort = pd.to_numeric(f.loc[dentro, "vl_amortizacao"], errors="coerce").fillna(0.0)
    acrual = pd.to_numeric(f.loc[dentro, "vl_acrual"], errors="coerce").fillna(0.0)
    return float(amort.sum() + acrual.sum())


def montar_relatorio(
    matriz: pd.DataFrame,
    tabela_outros_fundos: pd.DataFrame,
    tabela_estoque: pd.DataFrame,
    fundo,
    prazo_cotizacao: int,
    tabela_prazos,
    resolvedor_cotas=None,
    fluxo: pd.DataFrame | None = None,
    liquido_fora_do_fluxo: float = 0.0,
) -> tuple[pd.DataFrame, float]:
    """Consolida as tres tabelas no formato final do relatorio.

    Devolve (DataFrame do relatorio, PL em R$ mil).

    Laco 1 -- produtos da matriz (etapa 6), exceto os de outros fundos:
      - Exposição = exposicao total do produto.
      - Liq. Duration = prazo de cotizacao do fundo (cc); nos produtos de bolsa
        (acao/ETF do administrador), o ciclo de liquidacao D+2 do instrumento.
      - Valor Líquido = valor cheio para CPR e Tesouraria; senao, soma das
        faixas 0..prazo_cotizacao (o que vence dentro do prazo de resgate).
        EXCECAO: `PRAZO_DO_FLUXO` -- ver `fluxo` abaixo.

    `fluxo` e o fluxo projetado do fundo (`fato_fluxo`). Quando vem, o Valor
    Líquido do produto `PRODUTO_DO_FLUXO` sai dele -- parcela a parcela, pela
    data em que cada uma vence -- em vez de sair da matriz, que data a operacao
    inteira pelo vencimento final e por isso zera operacao parcelada que recebe
    dentro da janela. `liquido_fora_do_fluxo` e o que aquele produto ainda tem na
    carteira e que o fluxo nao cobre (operacao sem cronograma que nao da bullet):
    o chamador o calcula porque so ele tem a carteira linha a linha. Ver
    `_liquido_do_fluxo`.

    Laco 2 -- cotas de outros fundos (etapa 7):
      - Cota "A Vencer" (codigo termina em AV, ou nome em "A VENCER"/"A VENC"):
        Valor Líquido = soma da coluna "A Vencer C/ Carrego" do estoque nas
        faixas 0..prazo_cotizacao; Duration = prazo de resgate.
      - Cota "Vencidos" (codigo termina em VEN, ou nome em "VENCIDOS"): ignorada.
      - Demais: Duration vem da tabela de prazos pelo nome; Valor Líquido =
        Exposição somente quando ha prazo e ele cabe no prazo de cotizacao.
        Prazo maior, ou ausente, zera o Valor Líquido.

    No fim, `_somar_repetidas` colapsa as linhas do mesmo (ativo, prazo, origem)
    numa so: a carteira traz uma linha por aplicacao, e o relatorio mostra a
    posicao. Nenhum total muda.
    """
    linhas: list[dict] = []

    # PL em R$ mil = total geral da matriz / 1000
    pl_mil = 0.0
    if not matriz.empty and LINHA_EXPOSICAO in matriz.index and LINHA_EXPOSICAO in matriz.columns:
        pl_mil = float(matriz.loc[LINHA_EXPOSICAO, LINHA_EXPOSICAO]) / 1000

    def add(codigo_ativo, exposicao, duration, valor_liquido, origem):
        linhas.append({
            "cnpj_fundo": fundo.cnpj,
            "nome_fundo": fundo.nome_interno,
            "codigo_ativo": codigo_ativo,
            "exposicao": float(exposicao),
            "liq_duration": None if duration is None else int(duration),
            "valor_liquido": float(valor_liquido),
            "origem": origem,
        })

    # --- Laco 1: produtos ------------------------------------------------
    de_cotas = _produtos_de_cotas(fundo)
    de_bolsa = _produtos_de_bolsa(fundo)
    # Global, e nao por administrador: titulo publico e titulo publico em
    # qualquer carteira.
    bases_publicas = {normalizar(b) for b in BASES_TITULO_PUBLICO}
    if not matriz.empty:
        produtos = [c for c in matriz.columns if c != LINHA_EXPOSICAO]
        faixas_prazo = [f for f in matriz.index if isinstance(f, int) and 0 <= f <= prazo_cotizacao]
        for produto in produtos:
            texto = str(produto).strip().upper()
            if not texto or texto in de_cotas:
                continue
            exposicao = float(matriz.loc[LINHA_EXPOSICAO, produto])
            if texto in VALOR_CHEIO:
                valor_liq = exposicao
            else:
                valor_liq = float(matriz.loc[faixas_prazo, produto].sum())
                if texto == PRODUTO_DO_FLUXO:
                    do_fluxo = _liquido_do_fluxo(fluxo, prazo_cotizacao)
                    if do_fluxo is not None:
                        log.info(
                            "%s: Valor Liquido de %s pelo fluxo projetado -- "
                            "R$ %.2f (matriz dava R$ %.2f; + R$ %.2f fora do fluxo).",
                            getattr(fundo, "nome_interno", ""), texto,
                            do_fluxo + liquido_fora_do_fluxo, valor_liq,
                            liquido_fora_do_fluxo)
                        valor_liq = do_fluxo + liquido_fora_do_fluxo
            # A duration do produto e o prazo de cotizacao do fundo (cc), como
            # no VBA -- a matriz e que carrega a abertura por dia. As excecoes
            # sao os produtos cujo prazo e do INSTRUMENTO e nao da janela do
            # fundo: num fundo fechado (cc=999) a coluna diria "999" para uma
            # acao que liquida em dois dias ou um titulo publico que liquida no
            # mesmo dia.
            #
            # So o produto INTEIRO conta: "Renda Fixa" tem titulo publico
            # dentro, mas nao e um -- ali a duration segue sendo a do fundo, e
            # quem carrega o D+0 daquelas linhas e a matriz, faixa a faixa.
            texto_norm = normalizar(produto)
            if texto_norm in de_bolsa:
                duracao = PRAZO_LIQUIDACAO_BOLSA
            elif texto_norm in bases_publicas:
                duracao = PRAZO_TITULO_PUBLICO
            else:
                duracao = prazo_cotizacao
            add(produto, exposicao, duracao, valor_liq, "produto")

    # --- Laco 2: cotas de outros fundos ----------------------------------
    soma_a_vencer_carrego = 0.0
    if not tabela_estoque.empty and COL_A_VENCER_CARREGO in tabela_estoque.columns:
        faixas = [f for f in tabela_estoque.index if isinstance(f, int) and 0 <= f <= prazo_cotizacao]
        soma_a_vencer_carrego = float(
            pd.to_numeric(tabela_estoque.loc[faixas, COL_A_VENCER_CARREGO], errors="coerce")
            .fillna(0.0).sum()
        )

    for _, linha in tabela_outros_fundos.iterrows():
        cod = str(linha["cod_fundo"]).strip().upper()
        nome = str(linha["fundo"]).strip().upper()
        exposicao = float(linha["exposicao"])

        # O nome vem truncado em ~20 caracteres, entao "VENCIDOS" chega como
        # "VENCIDO" ou "VENCID". Sem as variacoes, cota vencida passava por cota
        # normal -- foi o caso de PERSA FIDC NP VENCID.
        #
        # O teste e pelo NOME, nao pelo codigo: codigo de "a vencer" tambem
        # termina em VE (PLICA-AVE, LIMAJ-AVE), e olhar so o fim do codigo
        # classificaria R$ 689 mi de cota a vencer como vencida.
        #
        # A ordem tambem protege: `eh_a_vencer` e avaliado primeiro, entao
        # "SORANO FIDC A VENC" (codigo SORAVEN) cai no ramo certo.
        eh_a_vencer = cod.endswith("AV") or nome.endswith("A VENCER") or nome.endswith("A VENC")
        eh_vencido = (cod.endswith("VEN")
                      or nome.endswith(("VENCIDOS", "VENCIDO", "VENCID")))

        if eh_a_vencer:
            add(linha["fundo"], exposicao, prazo_cotizacao, soma_a_vencer_carrego,
                ORIGEM_A_VENCER)
        elif eh_vencido:
            continue  # cotas vencidas nao entram no relatorio
        else:
            # A cota so e liquida se resgatar dentro do prazo do proprio fundo.
            # Sem essa condicao a coluna dizia que uma cota de 180 dias estava
            # disponivel em 29 -- e contradizia o Caixa Potencial, que ja usa o
            # mesmo corte.
            #
            # Prazo desconhecido tambem nao conta (decisao do usuario em
            # 03/08/2026): sem prazo nao da para afirmar que a cota resgata
            # dentro da janela. Isso alcanca os ativos que o `prazos validar` ja
            # aponta como sem prazo -- preencher a tabela devolve o valor.
            #
            # A busca do prazo aceita ISIN aqui, e SO aqui: alguns
            # administradores identificam a cota pelo ISIN em vez do mnemonico.
            # No laco 1, dos produtos, isso nao vale -- um ISIN numa operacao de
            # DC e uma debenture, nao uma cota, e resolve-lo para o prazo de um
            # fundo daria liquidez a um papel que nao a tem.
            if resolvedor_cotas is not None:
                prazo = resolvedor_cotas.prazo(linha["fundo"])
            else:
                prazo = tabela_prazos.prazo_por_ativo(linha["fundo"])
            dentro_da_janela = prazo is not None and prazo <= prazo_cotizacao
            add(linha["fundo"], exposicao, prazo, exposicao if dentro_da_janela else 0.0,
                "outro_fundo")

    relatorio = pd.DataFrame(linhas, columns=list(COLUNAS_RELATORIO))
    return _somar_repetidas(relatorio), pl_mil


def _somar_repetidas(relatorio: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por (codigo_ativo, prazo, origem) -- a posicao, nao a operacao.

    A carteira traz uma linha por APLICACAO, e a etapa 7 copia cada uma, como o
    VBA fazia. No relatorio isso virava o mesmo ativo repetido dezenas de vezes
    com o mesmo prazo: 33 linhas de HODFIM no PRIME, 47 de TANZANITA no CARLOTA,
    128 de FGR no MULTIPLICA CAPITAL GLOBAL BONDS. Quem lia a tabela tinha de
    somar a olho para saber a posicao do fundo naquele ativo.

    Somar aqui, e nao na etapa 7: a tabela 2 continua sendo a copia linha a
    linha do VBA, e a soma dela segue batendo com a coluna de cotas da tabela 1.
    O que muda e so a apresentacao final.

    O prazo entra na chave por seguranca, nao por necessidade -- ele sai do
    codigo do ativo, entao e igual dentro do grupo (conferido em 31/07/2026:
    nenhum dos 11 grupos repetidos tinha prazo divergente). Se um dia divergir,
    o certo e a linha se dividir em duas, e nao um dos prazos desaparecer.

    Nenhum total se move: `caixa_potencial` soma `valor_liquido` por faixa de
    prazo, e agrupar por prazo preserva cada faixa.
    """
    if relatorio.empty:
        return relatorio

    chave = ["cnpj_fundo", "nome_fundo", "codigo_ativo", "liq_duration", "origem"]
    df = relatorio.assign(_ordem=range(len(relatorio)))

    # A cota "a vencer" carrega no `valor_liquido` a soma do estoque do FUNDO --
    # o mesmo numero em toda linha da origem, e nao uma parcela dela. Somar duas
    # dessas dobraria o estoque. Hoje ha uma so por fundo; o `first` e o que
    # garante que continue assim se aparecer a segunda.
    estoque = df["origem"] == ORIGEM_A_VENCER

    partes = []
    for mascara, como in ((~estoque, "sum"), (estoque, "first")):
        sub = df[mascara]
        if sub.empty:
            continue
        partes.append(
            # `dropna=False` porque prazo ausente E uma chave valida: sem ele o
            # pandas descartaria justamente as cotas sem prazo cadastrado.
            # `sort=False` + `_ordem` preservam a ordem de montagem -- produtos
            # primeiro, cotas depois.
            sub.groupby(chave, as_index=False, sort=False, dropna=False).agg(
                exposicao=("exposicao", "sum"),
                valor_liquido=("valor_liquido", como),
                _ordem=("_ordem", "min"),
            )
        )

    junto = pd.concat(partes, ignore_index=True).sort_values("_ordem")
    return junto[list(COLUNAS_RELATORIO)].reset_index(drop=True)


def resumo_analise(matriz: pd.DataFrame) -> dict:
    """Numeros de conferencia da matriz."""
    if matriz.empty:
        return {"produtos": 0, "exposicao_total": 0.0}
    produtos = [c for c in matriz.columns if c != LINHA_EXPOSICAO]
    return {
        "produtos": len(produtos),
        "exposicao_total": float(matriz.loc[LINHA_EXPOSICAO, LINHA_EXPOSICAO]),
        "por_produto": {
            p: float(matriz.loc[LINHA_EXPOSICAO, p]) for p in produtos
        },
    }
