"""Regras de tratamento de liquidez por administrador.

Espelha o `Select Case ADM` do modulo VBA `Org_dados`/`ResumoCartADM`. Na fase
de extracao o administrador ainda nao altera as consultas -- esta camada existe
para receber as regras do `Org_dados` (colunas, filtros de vencidos, faixas de
prazo) na proxima fase, e para avisar quando um fundo cai num administrador sem
tratamento definido (o `Case Else` do VBA, que abria um MsgBox).

Nota: a decisao de projeto e que o administrador vem da CVM. Os 39 fundos que a
planilha marcava como SINGULARE aparecem na CVM como QI CORRETORA; por isso a
regra e registrada sob QI, mantendo SINGULARE como alias historico. As regras em
si (colunas, filtros, criterios) sao as mesmas.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


def normalizar(nome: str | None) -> str:
    """Maiusculas, sem acento e com espacos colapsados, para casar VORTX/VORTX."""
    if not nome:
        return ""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento).strip().upper()


# Liquidacao de ativo negociado em bolsa -- acao e ETF -- no ciclo D+2 da B3.
#
# O prazo nao vem da tabela de prazos nem da diferenca de datas: acao nao tem
# vencimento, entao os dois caminhos do `calcular_dias` devolvem vazio e a linha
# fica FORA de todas as faixas da matriz. O efeito pratico era o pior possivel:
# o ativo mais liquido da carteira entrava com Valor Liquido zero, como se nao
# se convertesse em caixa nunca. E uma caracteristica do instrumento, nao do
# ativo individual, e por isso vive na regra do administrador -- quem sabe que
# aquele rotulo de `Base_Origem` traz papel de bolsa e o administrador.
PRAZO_LIQUIDACAO_BOLSA = 2


@dataclass(frozen=True)
class RegraADM:
    chave: str
    aliases: tuple[str, ...] = ()
    implementado: bool = False
    observacao: str = ""
    # Ganchos usados pela fase 2 (Org_dados). Valores extraidos do VBA.
    filtro_vencidos_carteira: str | None = None
    coluna_excluidos_vencidos: str | None = None
    filtro_outros_fundos: tuple[str, ...] = field(default_factory=tuple)
    # `Base_Origem` cujas linhas sao acoes/ETF negociados em bolsa: liquidam em
    # `PRAZO_LIQUIDACAO_BOLSA` dias. Comparacao por `normalizar`, entao
    # "Carteira de Ações" e "Carteira de Acoes" sao o mesmo rotulo.
    bases_bolsa: tuple[str, ...] = field(default_factory=tuple)
    # `Base_Origem` que nao e posicao do fundo e nao pode entrar na matriz da
    # etapa 6 -- nem no PL, nem em faixa de prazo. Comparacao por `normalizar`.
    bases_fora_do_pl: tuple[str, ...] = field(default_factory=tuple)
    # No OrgCarteira, a linha de Exposicao Total da PLANNER desconsidera as
    # linhas marcadas como excluidas; a QI soma tudo.
    exclui_do_total: bool = False
    # No FiltrarCopiarOutrosFundos, a PLANNER exclui Codigo_Ativo = "PDD".
    outros_fundos_exclui_pdd: bool = False

    def reconhece(self, nome: str | None) -> bool:
        alvo = normalizar(nome)
        if not alvo:
            return False
        for termo in (self.chave,) + self.aliases:
            if normalizar(termo) in alvo:
                return True
        return False


# Regras implementadas no VBA -------------------------------------------------

QI = RegraADM(
    chave="QI CORRETORA DE TITULOS E VALORES MOBILIARIOS",
    aliases=("QI CORRETORA", "SINGULARE"),
    implementado=True,
    observacao="Antiga SINGULARE. Regras do bloco SINGULARE do VBA.",
    filtro_vencidos_carteira="VC*",           # Carteira coluna I, com coluna Q > 0
    coluna_excluidos_vencidos="C",            # Vencidos!C (Ativos Excluidos)
    filtro_outros_fundos=("Outros Fundos",),  # Carteira coluna G
)

PLANNER = RegraADM(
    chave="PLANNER CORRETORA DE VALORES",
    aliases=("PLANNER",),
    implementado=True,
    observacao="Regras do bloco PLANNER do VBA.",
    filtro_vencidos_carteira="*VENC*",                     # Carteira coluna I
    coluna_excluidos_vencidos="G",                         # Vencidos!G
    # "Carteira de Fundos de Investimentos" entrou depois: nao vinha do VBA e
    # so apareceu quando se olhou o PRIME e o SALUS, que sao quase inteiramente
    # esse rotulo. Sem ele, as cotas contavam como produto comum -- e num fundo
    # de cc=999 isso significava o valor cheio entrando como liquido.
    filtro_outros_fundos=("FUNDOS-FUNDOS", "PORTFOLIO_INVESTIDO",
                          "Carteira de Fundos de Investimentos"),
    # Decisao da area de risco em 20/08/2026: o que chega em "Carteira de Ações"
    # e acao ou ETF negociado em bolsa, e liquida em D+2. Sao dois fundos hoje
    # (PRIME FIM CP e MULTIPLICA TOTAL RETURN), e nos dois a posicao entrava com
    # Valor Liquido zero por falta de prazo -- R$ 4,06 mi somados.
    bases_bolsa=("Carteira de Ações",),
    exclui_do_total=True,                                   # SumIfs(..., Z, "NAO")
    outros_fundos_exclui_pdd=True,                          # AutoFilter Field 9 <> "PDD"
)

# A VORTX nao vinha do VBA: entrou depois, quando se constatou que as cotas dela
# chegam com Base_Origem = COTASAPLICADAS. So o rotulo do produto muda -- o
# Codigo_Ativo segue o mesmo formato mnemonico da QI e da PLANNER, entao a busca
# do prazo do fundo investido funciona sem adaptacao.
VORTX = RegraADM(
    chave="VORTX DISTRIBUIDORA DE TITULOS E VALORES MOBILIARIOS",
    aliases=("VORTX",),
    implementado=True,
    observacao="Cotas de fundos vem em COTASAPLICADAS. Sem regra de vencidos "
               "na carteira: nao foi observado padrao equivalente ao VC*/*VENC*.",
    filtro_outros_fundos=("COTASAPLICADAS",),
)

# O BTG usa `PORTFOLIO_INVESTIDO`, o mesmo rotulo que a PLANNER ja tinha -- nao
# e coincidencia: os dois vem do mesmo sistema de carteira. Sem regra, as cotas
# dele nao chegavam a etapa 7 e ficavam invisiveis (R$ 722 mi em 6 fundos, dos
# quais o SUCELLUS e 100% cota).
BTG = RegraADM(
    chave="BTG PACTUAL SERVICOS FINANCEIROS",
    aliases=("BTG",),
    implementado=True,
    observacao="Cotas de fundos vem em PORTFOLIO_INVESTIDO. Sem regra de "
               "vencidos na carteira: nao foi observado padrao equivalente ao "
               "VC*/*VENC*. Acoes e ADR liquidam em D+2.",
    # Decisao da area em 25/08/2026, mesma logica da PLANNER: e papel negociado
    # em bolsa e liquida em D+2. Sao dois rotulos porque o BTG separa o mercado
    # local do offshore -- `ACOES` traz BBAS3, EGAF11, ONCO11 e `ADR E ACOES
    # OFFSHORE`, os tickers US.
    #
    # No `ACOES` a regra e quase inocua: os tres tickers ja estao na tabela de
    # prazos com 2 dias. O que ela destrava e o offshore, onde 4 das 6 linhas
    # nao tinham prazo nenhum -- e, por isso, ficavam fora de todas as faixas.
    bases_bolsa=("ACOES", "ADR E ACOES OFFSHORE"),
    filtro_outros_fundos=("PORTFOLIO_INVESTIDO",),
)

# A GENIAL escreve o nome do fundo por extenso e poe a serie depois de um hifen
# ("GAD FIDC NP-MEZ PREF"), ao contrario do BTG, que abrevia. Isso nao muda a
# regra -- o casamento continua sendo pelo Codigo_Ativo na tabela de prazos --
# mas explica por que os mnemonicos dela nao casavam com os dos outros.
GENIAL = RegraADM(
    chave="BANCO GENIAL",
    aliases=("GENIAL",),
    implementado=True,
    observacao="Cotas de fundos vem em 'Cotas de Investimento'. Sem regra de "
               "vencidos na carteira.",
    filtro_outros_fundos=("Cotas de Investimento",),
)

# A CAIXA usa "Outros Fundos", o mesmo rotulo da QI. Os mnemonicos dela ja
# estavam na tabela de prazos -- faltava so a regra que leva as linhas ate a
# etapa 7.
CAIXA = RegraADM(
    chave="CAIXA ECONOMICA FEDERAL",
    aliases=("CAIXA",),
    implementado=True,
    observacao="Cotas de fundos vem em 'Outros Fundos'. Sem regra de vencidos "
               "na carteira.",
    filtro_outros_fundos=("Outros Fundos",),
)

# Administradores presentes no cadastro mas sem tratamento (Case Else do VBA) --
#
# DAYCOVAL fica de fora por decisao da area: os dois fundos dele sao fechados,
# entao o Caixa Potencial nao e indicador que decida nada ali. Alem disso o
# Codigo_Ativo do DAYCOVAL guarda a CATEGORIA (15804, 15805, 16007), nao o
# ativo -- quem identifica e o Nome_Ativo. Ligar a regra sem tratar isso daria
# o mesmo prazo a fundos diferentes.
#
# `implementado=False` fala do tratamento de LIQUIDEZ. A regra de bases fora do
# PL e outra coisa e vale mesmo aqui: ela nao depende do tratamento, e sim de
# como o administrador escreve a carteira.
#
# Decisao da area de risco em 20/08/2026: o DAYCOVAL escreve na carteira uma
# linha `PL COTAS` NEGATIVA com o patrimonio das classes senior e mezanino. No
# ECOAGRO MULTIPLICA FIDC FIAGRO ela vale -91.861.057,83, que e exatamente
# SENIOR 60.442.909,06 + MEZANINO A 15.578.578,04 + MEZANINO B 15.839.570,73 --
# casa ao centavo com a serie de cotas.
#
# Aquilo nao e posicao do fundo: e a subordinacao escrita dentro da carteira.
# Somando-a, o PL do fundo saia R$ 14,5 mi -- o patrimonio da JUNIOR, e nao o
# do fundo. O Caixa Potencial, que conta ativo, ficava sobre um denominador de
# equity e devolvia Caixa/PL de 283,55%. Fora a linha, o PL passa a ser o ativo
# (R$ 106,3 mi) e a razao volta a 38,61%.
#
# Vale para o administrador e nao para o fundo porque a causa e a escrita dele.
# Hoje alcanca um fundo so -- o outro DAYCOVAL, ECOAGRO MULTI HY, nao tem a
# linha, e a regra passa por ele sem efeito.
DAYCOVAL = RegraADM(
    chave="BANCO DAYCOVAL",
    aliases=("DAYCOVAL",),
    observacao="Sem tratamento de liquidez. A linha `PL COTAS` da carteira e a "
               "subordinacao (senior + mezanino) com sinal negativo, nao uma "
               "posicao: fica fora do PL.",
    bases_fora_do_pl=("PL COTAS",),
)

SEM_TRATAMENTO = (DAYCOVAL,)

REGISTRO: tuple[RegraADM, ...] = (QI, PLANNER, VORTX, BTG, GENIAL, CAIXA) + SEM_TRATAMENTO

DESCONHECIDO = RegraADM(
    chave="(desconhecido)",
    implementado=False,
    observacao="Administrador nao cadastrado: verificar o tratamento de liquidez.",
)


def obter(administrador: str | None) -> RegraADM:
    """Devolve a regra do administrador; DESCONHECIDO quando nao ha correspondencia."""
    for regra in REGISTRO:
        if regra.reconhece(administrador):
            return regra
    return DESCONHECIDO
