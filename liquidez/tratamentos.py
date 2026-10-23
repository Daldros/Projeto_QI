"""Tratamentos dos dados extraidos -- equivalente ao modulo VBA `Org_dados`.

Cobre por enquanto o bloco da aba `Vencidos` e as colunas calculadas da
Carteira e do BD, na mesma ordem em que `GeraRel` chama:

    SepararAtivosVencidos    -> Vencidos!A  (emissores vencidos da Carteira)
    SepararAtivosVencidosBD  -> Vencidos!E  (CNPJ dos sacados vencidos no BD)
    CalcularDias             -> Carteira!X, !Y, !Z
    VencidosBD               -> BD!AD

Nada e gravado em planilha: cada etapa devolve DataFrame/conjunto, para o
proximo passo (montagem da Analise carteira e do Relatorio_Liquidez) consumir.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .prazos import TabelaPrazos
from .regras_adm import PRAZO_LIQUIDACAO_BOLSA, RegraADM, normalizar
from .regras_adm import obter as obter_regra
from .regras_vencidos import CatalogoRegras, RegraVencidos

log = logging.getLogger(__name__)

ARQUIVO_EXCLUIDOS = Path("config") / "ativos_excluidos.json"
ARQUIVO_PRODUTOS = Path("config") / "produtos.json"
ARQUIVO_PRODUTOS_PARAMETROS = Path("config") / "parametros" / "produtos.json"
COL_BASE_ORIGEM_FONTE = "Base_Origem_Fonte"
COL_COD_OP = "Codigo_Op"            # mesmo nome que analise.COL_COD_OP

SIM, NAO = "SIM", "NAO"

# A coluna de produto da Carteira. `analise` a chama de COL_PRODUTO e importa
# daqui: dois nomes para a mesma coluna ja bastam, duas definicoes seriam o
# comeco de duas grafias.
COL_BASE_ORIGEM = "Base_Origem"

# Titulo publico liquida no MESMO DIA. Decisao da area em 25/08/2026, e vale
# para TODOS os fundos -- nao e regra de administrador, e caracteristica do
# papel: LFT, LTN e NTN tem mercado secundario diario e o Tesouro recompra.
#
# O vencimento do titulo nao descreve a liquidez dele. Sem esta regra, uma NTN-B
# 2060 entrava com 12 mil dias de prazo e ficava fora de qualquer janela, como
# se fosse ilíquida -- quando e o ativo mais liquido que existe na carteira.
PRAZO_TITULO_PUBLICO = 0

# Identificacao pelo TICKER, e nao so pelo rotulo da base: em 31/07/2026, 111
# das 123 linhas de titulo publico estavam sob rotulos genericos como "Renda
# Fixa", "Carteira de Renda Fixa" e "COMPROMISSADA OVER". Olhar so a base
# deixaria de fora justamente as que mais precisam da regra.
#
# Comparacao por PREFIXO, no `Codigo_Ativo` ou no `Nome_Ativo`, porque cada
# administrador escreve de um jeito: "LFT", "LFT01032027 -LFT", "LTNO-2026",
# "NTN-B-2055", "NTNB15052035-NTN-B - 15/05/2035". Ha ainda quem ponha
# categoria no codigo e o papel no nome (LFT000029348 sob o codigo 19408), e por
# isso os dois campos entram na busca.
#
# Conferido linha a linha em 31/07/2026: as 123 casadas sao todas titulo
# publico, sem falso positivo.
TICKERS_TITULO_PUBLICO = ("LFT", "LTN", "NTN", "BTN")

# Excecoes: parecem titulo publico pelo prefixo, mas sao COMPROMISSADA -- o
# papel e o lastro, nao a posicao. Decisao da area em 25/08/2026.
#
# Elas ja estao em `config/ativos_excluidos.json`, e e la que a regra do
# administrador decide o que fazer com elas. Carimbar D+0 aqui atropelaria
# aquela decisao por um caminho lateral: em 31/07/2026 o LTNO some 19 linhas e
# R$ 32,76 mi em 15 fundos, todos da QI, cuja regra exige `Qtde_Dias <= 0` para
# excluir -- ou seja, o D+0 mudaria o resultado do teste de exclusao.
#
# O sufixo "O" e o que marca a operacao: LTNO e compromissada lastreada em LTN,
# LFTO em LFT e NTN O em NTN-B. O prazo que vale e o do VENCIMENTO DA OPERACAO,
# que a carteira ja traz -- e e ele que decide se o saldo entra na liquidez ou
# nao. Em 31/07/2026 o LTNO e o NTN O vencem em 03/08, tres dias, e por isso
# entram; uma operacao mais longa nao entraria, e esse e o comportamento certo.
#
# "NTNO" sem espaco entra por precaucao: o rotulo varia entre administradores e
# o prefixo nao colide com NTNB nem com NTN-B.
#
# Testado ANTES dos tickers, senao "LTNO-2026" casaria por "LTN".
NAO_TITULO_PUBLICO = ("LTNO", "LFTO", "NTN O", "NTNO")

# Base cujas linhas sao OPERACAO compromissada, e nao o titulo em si. O prazo
# delas e o da operacao, que a carteira grava em `Data_Vencimento` -- e ele pode
# ser longo: compromissada nao e so overnight, ha operacao a termo com anos pela
# frente. Foi por isso que a regra de exclusao do VBA exigiu `Qtde_Dias <= 0`:
# so sai a que ja venceu, nao a que tem prazo.
#
# Carimbar D+0 aqui trocava o prazo real por liquidez imediata -- no MHX MULTI
# transformava uma compromissada de 1.749 dias em caixa do dia, R$ 4,4 mi que o
# fundo nao tem disponivel. Comparacao por SUBSTRING: o rotulo varia
# ("COMPROMISSADA OVER" hoje, mas nao ha garantia de que seja o unico).
BASES_COMPROMISSADA = ("COMPROMISSADA",)

# Bases cujo rotulo ja diz que a linha inteira e titulo publico. Servem ao
# `analise`, que precisa saber quando o PRODUTO todo e publico -- "Renda Fixa"
# tem titulo publico dentro, mas nao e um.
BASES_TITULO_PUBLICO = ("TITULOS_PUBLICOS", "RENDA FIXA - TÍTS. PÚBLICOS")


def e_titulo_publico(codigo_ativo, nome_ativo=None) -> bool:
    """O papel e titulo publico, pelo ticker no codigo ou no nome.

    Compromissada nao entra, mesmo lastreada em titulo publico: o que a carteira
    tem ali e a operacao, e ela tem regra propria. Ver `NAO_TITULO_PUBLICO`.
    """
    textos = [str(v or "").strip().upper() for v in (codigo_ativo, nome_ativo)]
    if any(t.startswith(NAO_TITULO_PUBLICO) for t in textos):
        return False
    return any(t.startswith(TICKERS_TITULO_PUBLICO) for t in textos)

# Colunas calculadas, com o nome que tinham na planilha.
COL_QTDE_DIAS = "Qtde_Dias"          # Carteira!X
COL_ATIVO_VENCIDO = "Ativo Vencido"  # Carteira!Y
COL_ATIVOS_EXCLUIDOS = "Ativos Excluidos"  # Carteira!Z
COL_VENCIDOS_BD = "Vencidos"         # BD!AD


def carregar_depara_produtos(caminho: Path = ARQUIVO_PRODUTOS) -> list[dict]:
    """Le o de-para de Base_Origem (config/produtos.json).

    Cai na copia versionada em config/parametros/ quando o `configurar` ainda
    nao rodou, e em lista vazia quando nao ha arquivo nenhum -- sem de-para a
    carteira segue como veio, que e o comportamento do codigo original.
    """
    for candidato in (Path(caminho), ARQUIVO_PRODUTOS_PARAMETROS):
        if candidato.exists():
            dados = json.loads(candidato.read_text(encoding="utf-8"))
            regras = dados.get("depara_base_origem", [])
            for regra in regras:
                if "de" not in regra or "para" not in regra:
                    raise ValueError(f"{candidato}: regra sem 'de'/'para': {regra}")
            return regras
    return []


def aplicar_depara_produtos(carteira: pd.DataFrame, regras: list[dict]) -> pd.DataFrame:
    """Troca o rotulo de Base_Origem conforme o de-para; o original vai para
    `Base_Origem_Fonte`.

    O riscogc grava as operacoes de direitos creditorios como "Renda Fixa", e o
    resto do pacote (fluxo, bullets, analise, vencidos) as procura como "DC".
    A troca acontece aqui, antes de qualquer tratamento, para que TODOS os
    consumidores vejam o mesmo rotulo. Com `codigo_op_regex` a regra so pega a
    linha cujo Codigo_Op casa -- e o que separa a NC (C417693) do titulo
    publico que venha sob o mesmo rotulo.
    """
    if carteira.empty or not regras or COL_BASE_ORIGEM not in carteira.columns:
        return carteira
    df = carteira.copy()
    if COL_BASE_ORIGEM_FONTE not in df.columns:
        df[COL_BASE_ORIGEM_FONTE] = df[COL_BASE_ORIGEM]
    base = _texto(df[COL_BASE_ORIGEM]).str.upper()
    for regra in regras:
        selecao = base == str(regra["de"]).strip().upper()
        padrao = regra.get("codigo_op_regex")
        if padrao:
            if COL_COD_OP not in df.columns:
                log.warning("De-para de produto com codigo_op_regex, mas a carteira nao tem %s.", COL_COD_OP)
                continue
            selecao &= _texto(df[COL_COD_OP]).str.upper().str.match(padrao)
        excluir = regra.get("excluir_codigo_ativo_regex")
        if excluir and "Codigo_Ativo" in df.columns:
            # Titulo publico, compromissada, CDB e imovel tambem vem com Codigo_Op
            # C###### sob "Renda Fixa", mas nao sao direito creditorio: ficam
            # com o rotulo original e com as regras de ticker que ja os tratam.
            selecao &= ~_texto(df["Codigo_Ativo"]).str.upper().str.match(excluir)
        trocadas = int(selecao.sum())
        if trocadas:
            df.loc[selecao, COL_BASE_ORIGEM] = regra["para"]
            log.info("Base_Origem '%s' -> '%s' em %d linha(s).", regra["de"], regra["para"], trocadas)
    return df


def carregar_excluidos(caminho: Path = ARQUIVO_EXCLUIDOS) -> dict[str, list[str]]:
    """Le as listas de ativos excluidos (colunas C e G da aba Vencidos)."""
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    return {k: v for k, v in dados.items() if not k.startswith("_")}


def excluidos_do_adm(administrador: str | None, listas: dict[str, list[str]]) -> set[str]:
    """Lista de excluidos do administrador; cai no 'padrao' se nao houver."""
    alvo = normalizar(administrador)
    for chave, valores in listas.items():
        if chave == "padrao":
            continue
        if normalizar(chave) and normalizar(chave) in alvo:
            return {str(v).strip().upper() for v in valores}
    return {str(v).strip().upper() for v in listas.get("padrao", [])}


def _texto(serie: pd.Series) -> pd.Series:
    return serie.fillna("").astype(str).str.strip()


# ---------------------------------------------------------------------------
# 1. SepararAtivosVencidos -> Vencidos!A
# ---------------------------------------------------------------------------

def selecao_vencidos_carteira(carteira: pd.DataFrame, regra: RegraVencidos) -> pd.Series:
    """Mascara das linhas da Carteira que representam ativos vencidos.

    A regra vem de `config/regras_vencidos.json`. Administrador sem regra
    definida nao seleciona nada. O VBA, nesse caso, nao aplicava filtro e
    copiava a coluna inteira -- marcando TODOS os emissores como vencidos. E um
    defeito do VBA, nao um comportamento a reproduzir.
    """
    if carteira.empty:
        return pd.Series(dtype=bool)
    if not regra.definida:
        log.warning(
            "Sem regra de identificacao de vencidos para este administrador: "
            "nenhum ativo sera marcado como vencido."
        )
        return pd.Series(False, index=carteira.index)
    return regra.selecionar(carteira)


def separar_ativos_vencidos(carteira: pd.DataFrame, regra: RegraVencidos) -> set[str]:
    """Emissores dos ativos vencidos da Carteira.

    Equivale a coluna A da aba `Vencidos`. Nao e necessaria para marcar as
    linhas -- `marcar_ativo_vencido` faz isso direto --, mas continua util como
    saida de auditoria.
    """
    if carteira.empty:
        return set()
    emissores = _texto(carteira.loc[selecao_vencidos_carteira(carteira, regra), regra.coluna_emissor])
    return {e for e in emissores if e}


def marcar_ativo_vencido(
    carteira: pd.DataFrame,
    regra: RegraVencidos,
    chave_fundo: str | None = "cnpj_fundo",
) -> pd.Series:
    """SIM/NAO por linha: o emissor da linha tem algum ativo vencido?

    Dispensa a lista intermediaria que a planilha precisava manter na aba
    `Vencidos`: o `groupby(...).transform("any")` responde a pergunta ja
    alinhada a cada linha.

    `chave_fundo` entra no agrupamento para que a marca de um fundo nao vaze
    para outro quando a base tem varios fundos -- situacao que na planilha nao
    existia, porque so um fundo ficava carregado por vez.
    """
    if carteira.empty:
        return pd.Series(dtype=object)
    if regra.coluna_emissor not in carteira.columns:
        log.warning("Coluna %s ausente na carteira.", regra.coluna_emissor)
        return pd.Series(NAO, index=carteira.index)

    vencido = selecao_vencidos_carteira(carteira, regra)
    chaves = [_texto(carteira[regra.coluna_emissor]).str.upper()]
    if chave_fundo and chave_fundo in carteira.columns:
        chaves.insert(0, _texto(carteira[chave_fundo]))
    tem_vencido = vencido.groupby(chaves, sort=False).transform("any")
    return pd.Series(np.where(tem_vencido, SIM, NAO), index=carteira.index)


# ---------------------------------------------------------------------------
# 2. SepararAtivosVencidosBD -> Vencidos!E
# ---------------------------------------------------------------------------

def separar_ativos_vencidos_bd(bd: pd.DataFrame, regra: RegraVencidos) -> set[str]:
    """CNPJ dos sacados inadimplentes no estoque (coluna E da aba Vencidos).

    O criterio vem de `_estoque` em `config/regras_vencidos.json`: no BD a
    coluna `Status_Recebivel` vem preenchida corretamente e e o criterio direto,
    diferente da carteira, onde a identificacao depende do codigo do ativo.
    """
    if bd.empty or not regra.definida:
        return set()
    sacados = _texto(bd.loc[regra.selecionar(bd), regra.coluna_emissor])
    return {s for s in sacados if s}


# ---------------------------------------------------------------------------
# 3. CalcularDias -> Carteira!X, !Y, !Z
# ---------------------------------------------------------------------------

def calcular_dias(
    carteira: pd.DataFrame,
    tabela_prazos: TabelaPrazos,
    regra_vencidos: RegraVencidos,
    codigos_excluidos: set[str],
    regra: RegraADM,
    chave_fundo: str | None = "cnpj_fundo",
) -> pd.DataFrame:
    """Acrescenta Qtde_Dias, Ativo Vencido e Ativos Excluidos a Carteira.

    - `Qtde_Dias`: prazo da tabela quando o `Codigo_Ativo` esta la; senao a
      diferenca em dias corridos entre `Data_Posicao` e `Data_Vencimento`. As
      linhas cujo `Base_Origem` esta em `regra.bases_bolsa` sao acao/ETF e
      recebem `PRAZO_LIQUIDACAO_BOLSA` por cima dos dois -- e o ciclo de
      liquidacao do instrumento, e nao ha vencimento nem cadastro de onde tira-lo.
      Por ultimo, titulo publico (`e_titulo_publico`) recebe
      `PRAZO_TITULO_PUBLICO` sobre tudo, para todo administrador: o vencimento
      do papel nao descreve a liquidez dele.
    - `Ativo Vencido`: SIM quando o `Emissor` esta na lista de vencidos.
    - `Ativos Excluidos`: SIM quando o `Codigo_Ativo` esta na lista de
      excluidos do administrador. Para os administradores que usavam a coluna C
      da aba Vencidos (todos menos a PLANNER) o VBA exigia tambem `Qtde_Dias <= 0`.
    """
    if carteira.empty:
        return carteira.assign(
            **{COL_QTDE_DIAS: pd.Series(dtype="Float64"),
               COL_ATIVO_VENCIDO: pd.Series(dtype="object"),
               COL_ATIVOS_EXCLUIDOS: pd.Series(dtype="object")}
        )

    df = carteira.copy()
    codigo = _texto(df["Codigo_Ativo"]).str.upper()

    # Qtde_Dias: tabela de prazos primeiro, DateDiff como fallback.
    prazo = tabela_prazos.aplicar(df)
    inicio = pd.to_datetime(df["Data_Posicao"], errors="coerce")
    fim = pd.to_datetime(df["Data_Vencimento"], errors="coerce")
    dias = (fim - inicio).dt.days.astype("Float64")
    df[COL_QTDE_DIAS] = prazo.where(prazo.notna(), dias)

    # O prazo CADASTRAL, antes das regras de instrumento abaixo. Ele existe para
    # o teste de `Ativos Excluidos`, que pergunta se o prazo do ativo ja venceu
    # (`Qtde_Dias <= 0`) -- e essa e uma pergunta sobre o cadastro, nao sobre a
    # liquidez do papel.
    #
    # Sem essa separacao, pôr titulo publico em D+0 fazia LTNO e LFTO -- que
    # estao na lista de excluidos -- passarem no teste e sumirem das faixas. Em
    # 31/07/2026 isso tirava R$ 30,4 mi do Caixa Potencial de cinco fundos, no
    # sentido oposto ao da regra que acabara de os declarar liquidos.
    dias_cadastrais = df[COL_QTDE_DIAS].copy()

    # Acao e ETF: D+2 por ciclo de liquidacao da bolsa. Vem DEPOIS dos dois
    # caminhos anteriores porque nenhum deles alcanca esses papeis -- acao nao
    # tem vencimento para o DateDiff nem entrada na tabela de prazos, que e de
    # cotas de fundo. Sem esta regra a linha fica sem faixa e o ativo mais
    # liquido da carteira e contado como se nunca virasse caixa.
    if regra.bases_bolsa and COL_BASE_ORIGEM in df.columns:
        alvos = {normalizar(b) for b in regra.bases_bolsa}
        de_bolsa = _texto(df[COL_BASE_ORIGEM]).map(normalizar).isin(alvos)
        if de_bolsa.any():
            # Prazo ja definido e diferente do ciclo da bolsa nao existe hoje --
            # mas se um dia existir, e sinal de que a tabela de prazos ganhou um
            # ticker, e a sobreposicao silenciosa seria a pior forma de descobrir.
            tinha = df.loc[de_bolsa, COL_QTDE_DIAS]
            conflito = tinha.notna() & (tinha != PRAZO_LIQUIDACAO_BOLSA)
            if conflito.any():
                log.warning(
                    "%d linha(s) de %s ja tinham prazo proprio e foram "
                    "sobrescritas com D+%d (valores: %s).",
                    int(conflito.sum()), ", ".join(regra.bases_bolsa),
                    PRAZO_LIQUIDACAO_BOLSA,
                    ", ".join(str(v) for v in sorted(set(tinha[conflito]))[:5]),
                )
            df.loc[de_bolsa, COL_QTDE_DIAS] = PRAZO_LIQUIDACAO_BOLSA
            log.info("%d linha(s) de bolsa com prazo D+%d.",
                     int(de_bolsa.sum()), PRAZO_LIQUIDACAO_BOLSA)

    # Titulo publico por ultimo, e para TODOS os administradores: liquida no
    # mesmo dia, independentemente do vencimento do papel e de quem escreve a
    # carteira. Vem depois da tabela de prazos porque a sobrepoe -- ela dava 3
    # dias para LTNO e 1.493 para uma compromissada over lastreada em LFT.
    publico = df["Codigo_Ativo"].combine(
        df["Nome_Ativo"] if "Nome_Ativo" in df.columns else df["Codigo_Ativo"],
        lambda c, n: e_titulo_publico(c, n),
    ).astype(bool)
    if COL_BASE_ORIGEM in df.columns:
        base_norm = _texto(df[COL_BASE_ORIGEM]).map(normalizar)
        alvos_pub = {normalizar(b) for b in BASES_TITULO_PUBLICO}
        publico |= base_norm.isin(alvos_pub)
        # Compromissada fica de fora mesmo lastreada em titulo publico: o prazo
        # e o da OPERACAO, que a carteira ja traz, e ele pode ser longo.
        e_compromissada = base_norm.str.contains(
            "|".join(normalizar(b) for b in BASES_COMPROMISSADA),
            na=False, regex=True)
        if e_compromissada.any():
            publico &= ~e_compromissada
    if publico.any():
        tinha = df.loc[publico, COL_QTDE_DIAS]
        mudou = tinha.notna() & (tinha != PRAZO_TITULO_PUBLICO)
        if mudou.any():
            log.info(
                "Titulo publico: %d linha(s) tiveram o prazo trocado por D+%d "
                "(vinham de %s).", int(mudou.sum()), PRAZO_TITULO_PUBLICO,
                ", ".join(f"{v:.0f}" for v in sorted(set(tinha[mudou]))[:6]),
            )
        df.loc[publico, COL_QTDE_DIAS] = PRAZO_TITULO_PUBLICO
        log.info("%d linha(s) de titulo publico com prazo D+%d.",
                 int(publico.sum()), PRAZO_TITULO_PUBLICO)

    # Ativo Vencido: o emissor da linha tem algum ativo vencido?
    df[COL_ATIVO_VENCIDO] = marcar_ativo_vencido(df, regra_vencidos, chave_fundo)

    # Ativos Excluidos: codigo na lista do administrador.
    na_lista = codigo.isin(codigos_excluidos)
    if regra.coluna_excluidos_vencidos != "G":
        # Coluna C da aba Vencidos: o VBA tambem exigia Qtde_Dias <= 0. O teste
        # usa o prazo CADASTRAL, e nao o carimbado pelas regras de instrumento:
        # ele pergunta se o papel venceu, e titulo publico em D+0 nao venceu.
        na_lista &= dias_cadastrais.fillna(1) <= 0
    df[COL_ATIVOS_EXCLUIDOS] = pd.Series(
        [SIM if v else NAO for v in na_lista], index=df.index
    )
    return df


# ---------------------------------------------------------------------------
# 4. VencidosBD -> BD!AD
# ---------------------------------------------------------------------------

def marcar_vencidos_bd(
    bd: pd.DataFrame,
    regra: RegraVencidos,
    chave_fundo: str | None = "cnpj_fundo",
) -> pd.DataFrame:
    """Marca no BD os recebiveis cujo sacado tem algum titulo vencido (carrego).

    Mesma tecnica usada na carteira: em vez de montar a lista intermediaria que
    a planilha guardava na coluna E da aba `Vencidos`, o
    `groupby(...).transform("any")` responde direto por linha.

    `chave_fundo` no agrupamento e essencial: um sacado pode estar vencido em um
    fundo e adimplente em outro, e a marca de um nao pode vazar para o outro.
    """
    if bd.empty:
        return bd.assign(**{COL_VENCIDOS_BD: pd.Series(dtype="object")})
    if not regra.definida:
        log.warning("Sem regra de identificacao de vencidos no estoque.")
        return bd.assign(**{COL_VENCIDOS_BD: NAO})

    df = bd.copy()
    vencido = regra.selecionar(df)
    chaves = [_texto(df[regra.coluna_emissor]).str.upper()]
    if chave_fundo and chave_fundo in df.columns:
        chaves.insert(0, _texto(df[chave_fundo]))
    tem_vencido = vencido.groupby(chaves, sort=False).transform("any")
    df[COL_VENCIDOS_BD] = pd.Series(np.where(tem_vencido, SIM, NAO), index=df.index)
    return df


# ---------------------------------------------------------------------------
# Tabela `vencidos` -- materializa as colunas A e E da aba Vencidos
# ---------------------------------------------------------------------------

COLUNAS_VENCIDOS = (
    "cnpj_fundo", "nome_fundo", "data_posicao", "tipo",
    "codigo", "descricao", "linhas", "valor",
)

TIPO_EMISSOR = "emissor_vencido"      # coluna A da aba
TIPO_SACADO = "sacado_vencido_bd"     # coluna E da aba


def _agrupar(base: pd.DataFrame, coluna_codigo: str, coluna_descricao: str, coluna_valor: str):
    """Agrupa por codigo devolvendo (codigo, descricao, linhas, valor)."""
    if base.empty:
        return []
    aux = pd.DataFrame(
        {
            "codigo": _texto(base[coluna_codigo]),
            "descricao": _texto(base[coluna_descricao]) if coluna_descricao in base.columns else "",
            "valor": pd.to_numeric(base[coluna_valor], errors="coerce").fillna(0.0)
            if coluna_valor in base.columns else 0.0,
        }
    )
    aux = aux[aux["codigo"] != ""]
    if aux.empty:
        return []
    agrupado = aux.groupby("codigo", as_index=False).agg(
        descricao=("descricao", "first"), linhas=("codigo", "size"), valor=("valor", "sum")
    )
    return agrupado.to_dict("records")


def montar_tabela_vencidos(
    fundo,
    data_posicao: str,
    carteira: pd.DataFrame,
    bd: pd.DataFrame,
    regra: RegraVencidos,
    regra_estoque: RegraVencidos,
) -> pd.DataFrame:
    """Tabela com as duas listas calculadas da aba Vencidos.

    Alem dos codigos que a planilha guardava, traz quantas linhas e quanto
    valor originaram cada codigo -- o que permite auditar de onde veio a marca.
    """
    linhas: list[dict] = []

    if not carteira.empty:
        selecao = selecao_vencidos_carteira(carteira, regra)
        for registro in _agrupar(
            carteira.loc[selecao], regra.coluna_emissor, "Nome_Ativo", "Valor_Liquido"
        ):
            linhas.append({"tipo": TIPO_EMISSOR, **registro})

    if not bd.empty and regra_estoque.definida:
        selecao = regra_estoque.selecionar(bd)
        for registro in _agrupar(
            bd.loc[selecao], regra_estoque.coluna_emissor, "Nome_Sacado", "Valor_Presente"
        ):
            linhas.append({"tipo": TIPO_SACADO, **registro})

    if not linhas:
        return pd.DataFrame(columns=list(COLUNAS_VENCIDOS))

    df = pd.DataFrame(linhas)
    df.insert(0, "data_posicao", data_posicao)
    df.insert(0, "nome_fundo", fundo.nome_interno)
    df.insert(0, "cnpj_fundo", fundo.cnpj)
    return df[list(COLUNAS_VENCIDOS)].sort_values(["tipo", "codigo"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------

@dataclass
class ResultadoTratamento:
    """Saida dos tratamentos de um fundo."""

    fundo: str
    cnpj: str
    administrador: str | None
    regra: RegraADM
    regra_vencidos: RegraVencidos
    carteira: pd.DataFrame
    bd: pd.DataFrame
    vencidos: pd.DataFrame = field(default_factory=pd.DataFrame)
    emissores_vencidos: set[str] = field(default_factory=set)
    sacados_vencidos: set[str] = field(default_factory=set)

    def resumo(self) -> dict:
        def contar(df, coluna, valor):
            return int((df[coluna] == valor).sum()) if coluna in df.columns and not df.empty else 0

        return {
            "carteira_linhas": len(self.carteira),
            "bd_linhas": len(self.bd),
            "emissores_vencidos": len(self.emissores_vencidos),
            "sacados_vencidos": len(self.sacados_vencidos),
            "carteira_ativo_vencido_sim": contar(self.carteira, COL_ATIVO_VENCIDO, SIM),
            "carteira_excluidos_sim": contar(self.carteira, COL_ATIVOS_EXCLUIDOS, SIM),
            "carteira_prazo_da_tabela": int(self.carteira[COL_QTDE_DIAS].notna().sum())
            if COL_QTDE_DIAS in self.carteira.columns and not self.carteira.empty else 0,
            "bd_vencidos_sim": contar(self.bd, COL_VENCIDOS_BD, SIM),
        }


def _data_da_carga(carteira: pd.DataFrame, bd: pd.DataFrame) -> str:
    for df in (carteira, bd):
        if not df.empty and "data_posicao_carga" in df.columns:
            valor = df["data_posicao_carga"].iloc[0]
            if valor:
                return str(valor)
    return ""


def tratar_fundo(
    fundo,
    carteira: pd.DataFrame,
    bd: pd.DataFrame,
    tabela_prazos: TabelaPrazos | None = None,
    listas_excluidos: dict[str, list[str]] | None = None,
    data_posicao: str | None = None,
    catalogo: CatalogoRegras | None = None,
    depara_produtos: list[dict] | None = None,
) -> ResultadoTratamento:
    """Roda os tratamentos de um fundo, na ordem do `GeraRel`.

    Antes de tudo, o de-para de produto (`config/produtos.json`): e ele que faz
    a "Renda Fixa" do riscogc virar o "DC" que o resto do pacote procura.
    """
    depara_produtos = depara_produtos if depara_produtos is not None else carregar_depara_produtos()
    carteira = aplicar_depara_produtos(carteira, depara_produtos)
    tabela_prazos = tabela_prazos or TabelaPrazos.carregar()
    listas_excluidos = listas_excluidos if listas_excluidos is not None else carregar_excluidos()
    catalogo = catalogo or CatalogoRegras.carregar()

    regra = obter_regra(fundo.administrador)
    regra_vencidos = catalogo.obter(fundo.administrador)
    excluidos = excluidos_do_adm(fundo.administrador, listas_excluidos)
    data = data_posicao or _data_da_carga(carteira, bd)

    emissores = separar_ativos_vencidos(carteira, regra_vencidos)
    sacados = separar_ativos_vencidos_bd(bd, catalogo.estoque)
    carteira_tratada = calcular_dias(
        carteira, tabela_prazos, regra_vencidos, excluidos, regra
    )
    bd_tratado = marcar_vencidos_bd(bd, catalogo.estoque)
    vencidos = montar_tabela_vencidos(
        fundo, data, carteira, bd, regra_vencidos, catalogo.estoque
    )

    return ResultadoTratamento(
        fundo=fundo.nome_interno,
        cnpj=fundo.cnpj,
        administrador=fundo.administrador,
        regra=regra,
        regra_vencidos=regra_vencidos,
        carteira=carteira_tratada,
        bd=bd_tratado,
        vencidos=vencidos,
        emissores_vencidos=emissores,
        sacados_vencidos=sacados,
    )
