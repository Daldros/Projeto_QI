"""Gera o documento tecnico dos tratamentos, em HTML.

O documento e montado a partir do proprio codigo e dos arquivos de
configuracao -- registro de regras por administrador, listas de ativos
excluidos, tabela de prazos e cadastro de fundos. Assim ele nao sai de
sincronia: mudou a regra, o documento muda junto.

    python -m liquidez documentar
"""

from __future__ import annotations

import datetime as dt
import html
import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

log = logging.getLogger(__name__)

ARQUIVO_PADRAO = Path("docs") / "tratamentos.html"


@dataclass
class Etapa:
    """Uma etapa do tratamento, na ordem em que o `GeraRel` do VBA chamava."""

    ordem: int
    nome: str
    origem_vba: str
    implementado: bool
    resumo: str
    entrada: str
    saida: str
    regra: list[str] = field(default_factory=list)
    por_administrador: dict[str, str] = field(default_factory=dict)
    diferencas: list[str] = field(default_factory=list)
    funcao: str = ""


ETAPAS: tuple[Etapa, ...] = (
    Etapa(
        ordem=1,
        nome="Separar ativos vencidos da carteira",
        origem_vba="Org_dados.SepararAtivosVencidos(ADM)",
        implementado=True,
        funcao="tratamentos.separar_ativos_vencidos",
        resumo="Levanta quais emissores da carteira estão com ativos vencidos. O resultado "
               "alimenta a marcação de cada linha na etapa 3.",
        entrada="Base <code>carteira</code> (MUL_GLD_CARTEIRA_UNIFICADA, banco datalake)",
        saida="Lista de emissores — era a coluna A da aba <code>Vencidos</code>",
        regra=[
            "Seleciona as linhas da carteira conforme as condições do administrador, definidas em "
            "<code>config/regras_vencidos.json</code> (ver a seção “Identificação dos ativos "
            "vencidos”).",
            "Do conjunto selecionado, extrai os valores distintos da coluna de emissor.",
        ],
        diferencas=[
            "No VBA, administrador sem tratamento não aplicava filtro algum e copiava a coluna "
            "inteira, marcando <b>todos</b> os emissores como vencidos. Isso é um defeito, não "
            "um comportamento a reproduzir: aqui a lista fica vazia e é registrado um aviso.",
            "A lista em si é opcional: a marcação da etapa 3 é feita direto por agrupamento, sem "
            "precisar do intermediário que a planilha guardava na aba <code>Vencidos</code>. Ela "
            "continua sendo produzida como saída de auditoria.",
        ],
    ),
    Etapa(
        ordem=2,
        nome="Separar sacados vencidos do estoque",
        origem_vba="Org_dados.SepararAtivosVencidosBD",
        implementado=True,
        funcao="tratamentos.separar_ativos_vencidos_bd",
        resumo="Levanta quais sacados têm algum recebível vencido. Serve para identificar, na "
               "etapa 4, o estoque com carrego.",
        entrada="Base <code>bd</code> (MUL_GLD_ESTOQUE, banco datalake)",
        saida="Lista de CNPJ de sacados — era a coluna E da aba <code>Vencidos</code>",
        regra=[
            "No estoque a coluna <code>Status_Recebivel</code> vem preenchida corretamente e é o "
            "critério direto — diferente da carteira, onde a identificação depende do código do "
            "ativo.",
            "Extrai os valores distintos da coluna de sacado do conjunto selecionado.",
        ],
        por_administrador={"Todos": "regra única, não depende do administrador"},
    ),
    Etapa(
        ordem=3,
        nome="Calcular prazo e marcar vencidos na carteira",
        origem_vba="Org_dados.CalcularDias(ADM)",
        implementado=True,
        funcao="tratamentos.calcular_dias",
        resumo="Gera as três colunas calculadas da carteira, que são a base de todo o corte por "
               "faixa de liquidez.",
        entrada="Base <code>carteira</code> + tabela de prazos + resultado da etapa 1",
        saida="Colunas <code>Qtde_Dias</code>, <code>Ativo Vencido</code> e "
              "<code>Ativos Excluidos</code> — eram as colunas X, Y e Z da aba Carteira",
        regra=[
            "<b>Qtde_Dias</b>: se o <code>Codigo_Ativo</code> está na tabela de prazos, usa o "
            "prazo de lá; senão, usa a diferença em dias corridos entre <code>Data_Posicao</code> "
            "e <code>Data_Vencimento</code>.",
            "<b>Ativo Vencido</b>: <code>SIM</code> quando o emissor da linha tem <i>algum</i> "
            "ativo vencido <b>no mesmo fundo</b>.",
            "<b>Ativos Excluidos</b>: <code>SIM</code> quando o <code>Codigo_Ativo</code> está na "
            "lista de exclusão do administrador.",
            "<b>Ação e ETF</b>: as linhas cuja <code>Base_Origem</code> é de bolsa recebem "
            "<code>Qtde_Dias = 2</code> por cima dos dois caminhos acima — é o ciclo de "
            "liquidação D+2 do instrumento, e ação não tem vencimento para o <i>DateDiff</i> "
            "nem entrada na tabela de prazos, que é de cotas de fundo.",
        ],
        por_administrador={
            "QI": "exclusão exige, além do código estar na lista, <code>Qtde_Dias &le; 0</code>",
            "PLANNER": "exclusão considera apenas o código estar na lista; "
                       "<code>Base_Origem = 'Carteira de Ações'</code> liquida em "
                       "<code>D+2</code>",
        },
        diferencas=[
            "A busca do prazo casa <b>somente</b> pelo <code>Codigo_Ativo</code>, como o "
            "<code>Match</code> do VBA. Existe a opção de casar também pelo "
            "<code>Codigo_Op</code>, mas ela muda o resultado: a coluna de código de operação tem "
            "valores genéricos como <code>A</code>, que casariam indevidamente linhas de CPR, CC, "
            "Tesouraria e PDD com prazo 0.",
            "<b>O agrupamento inclui o fundo.</b> Um mesmo emissor pode estar vencido em um fundo "
            "e adimplente em outro, e a marca de um não pode vazar para o outro. Na planilha o "
            "risco não existia porque só um fundo ficava carregado por vez; aqui as bases têm "
            "todos os fundos juntos. Na carga de 02/07/2026, 13 dos 24 emissores presentes em mais "
            "de um fundo estavam vencidos em uns e não em outros — sem a chave do fundo, 34 linhas "
            "seriam marcadas erradamente.",
        ],
    ),
    Etapa(
        ordem=4,
        nome="Marcar carrego no estoque",
        origem_vba="Org_dados.VencidosBD",
        implementado=True,
        funcao="tratamentos.marcar_vencidos_bd",
        resumo="Marca cada recebível do estoque conforme o sacado tenha ou não algum título "
               "vencido — é o que separa a exposição “com carrego”.",
        entrada="Base <code>bd</code> + resultado da etapa 2",
        saida="Coluna <code>Vencidos</code> — era a coluna AD da aba BD",
        regra=[
            "<code>SIM</code> quando o <code>CNPJ_Sacado</code> da linha está na lista da etapa 2; "
            "caso contrário <code>NAO</code>.",
        ],
        por_administrador={"Todos": "regra única, não depende do administrador"},
    ),
    Etapa(
        ordem=5,
        nome="Calcular dias do fluxo",
        origem_vba="Org_dados.CalcDiasFluxos",
        implementado=False,
        resumo="Calcula o prazo de cada parcela do fluxo de recebíveis e traz a marca de vencido "
               "da carteira pela operação.",
        entrada="Cronograma das operações + carteira tratada",
        saida="Colunas <code>Preço DC</code> e <code>Vencido</code> do fluxo",
        regra=[
            "<code>Preço DC</code> = <code>dtVencimento</code> − data-base, em dias corridos.",
            "<code>Vencido</code> = a marca <code>Ativo Vencido</code> da carteira, casando "
            "<code>fluxo.cdOperacao</code> com <code>carteira.Codigo_Op</code>.",
        ],
        diferencas=[
            "<b>A origem foi identificada.</b> Nenhuma macro carregava a aba <code>fluxo</code>; "
            "os dados vêm da planilha de conciliação do fundo, uma aba por operação, cujo código "
            "casa com o <code>Codigo_Op</code> da carteira. O cadastro contratual está em "
            "<code>config/cronograma/</code> (<code>cronograma.py</code>) e a projeção do fluxo "
            "futuro em <code>fluxo.py</code>, disponível pelo comando "
            "<code>python -m liquidez fluxo</code>.",
            "<b>A projeção não recalcula o passado.</b> O valor de partida é o "
            "<code>Valor_Liquido</code> da carteira na data-base, que já vem pelo acrual e portanto "
            "é o saldo devedor com os juros incorridos dentro. Dali para frente projeta-se pela "
            "curva futura do CDI e pela taxa da operação. A soma das amortizações projetadas fecha "
            "exatamente com o valor da carteira.",
            "<b>Pendência:</b> falta ligar a projeção à distribuição por faixa de prazo da etapa 6. "
            "Hoje o fluxo é gerado como saída própria e não altera a matriz — as regras das etapas "
            "3 e 6 seguem como estão.",
        ],
    ),
    Etapa(
        ordem=6,
        nome="Montar a análise da carteira",
        origem_vba="Org_dados.OrgCarteira(ADM)",
        implementado=True,
        funcao="analise.analisar_carteira",
        resumo="Distribui a exposição da carteira por produto e por faixa de prazo, do dia 0 até "
               "o prazo de resgate do fundo.",
        entrada="Carteira tratada + prazo de resgate do fundo (<code>pz_resg</code>)",
        saida="Matriz produto × faixa de prazo (era a aba <code>Analise carteira</code>)",
        regra=[
            "Uma coluna por valor distinto de <code>Base_Origem</code>.",
            "<b>Base fora do PL</b>: a <code>Base_Origem</code> que não é posição do fundo sai "
            "antes de tudo — não entra na exposição total nem em faixa de prazo.",
            "Linha <code>Exposição Total</code> por produto: soma de <code>Valor_Liquido</code>.",
            "Uma linha por faixa de prazo (0 até <code>pz_resg</code>), somando o "
            "<code>Valor_Liquido</code> das linhas com aquele <code>Qtde_Dias</code>, não vencidas "
            "e não excluídas.",
            "Linha <code>&gt;pz_resg</code>: mesmas condições, com prazo acima do resgate.",
            "Linha <code>Vencidos</code>: linhas com <code>Ativo Vencido = SIM</code> e não "
            "excluídas, de qualquer prazo.",
        ],
        por_administrador={
            "QI": "exposição total soma todas as linhas do produto",
            "PLANNER": "exposição total desconsidera as linhas marcadas como excluídas",
            "DAYCOVAL": "<code>Base_Origem = 'PL COTAS'</code> fica fora do PL — é a "
                        "subordinação (sênior + mezanino) escrita na carteira com sinal "
                        "negativo, não uma posição. Somá-la devolvia o patrimônio da júnior "
                        "no lugar do PL do fundo",
        },
        diferencas=[
            "<b>Produto DC ainda sem a abertura do fluxo.</b> No VBA o DC era distribuído pela "
            "abertura de parcelas da aba <code>fluxo</code>. A origem desses dados já foi "
            "identificada e a projeção existe (etapa 5), mas ainda não alimenta esta matriz: por "
            "ora o DC entra pela própria carteira, somado por faixa de prazo como qualquer outro "
            "produto. Quando a ligação for feita, apenas o DC muda — a exposição total dele não se "
            "altera, só a forma como se distribui pelas faixas.",
            "Validado contra a aba <code>Analise carteira</code> da planilha para o AVANTI "
            "(02/07/2026): exposição por produto e distribuição por faixa idênticas ao centavo "
            "(192 células conferidas, nenhuma divergência).",
        ],
    ),
    Etapa(
        ordem=7,
        nome="Separar as cotas de outros fundos",
        origem_vba="Org_dados.FiltrarCopiarOutrosFundos(ADM)",
        implementado=True,
        funcao="analise.outros_fundos",
        resumo="Lista as posições em cotas de outros fundos, que entram no relatório uma a uma em "
               "vez de agregadas por produto. É a segunda das três tabelas da aba "
               "<code>Analise carteira</code>.",
        entrada="Carteira tratada",
        saida="Lista <code>cod_fundo</code> / <code>fundo</code> / <code>exposicao</code>",
        regra=[
            "Filtra a carteira por <code>Base_Origem</code> conforme o administrador.",
            "Uma linha por posição (sem agregar): <code>Codigo_Op</code>, "
            "<code>Codigo_Ativo</code> e <code>Valor_Liquido</code>.",
            "A soma desta tabela é igual à coluna <code>Outros Fundos</code> da tabela 1.",
        ],
        por_administrador={
            "QI": "<code>Base_Origem = 'Outros Fundos'</code>",
            "PLANNER": "<code>Base_Origem</code> em <code>FUNDOS-FUNDOS</code> ou "
                       "<code>PORTFOLIO_INVESTIDO</code>, excluindo <code>Codigo_Ativo = 'PDD'</code>",
        },
        diferencas=[
            "Validado contra a planilha para o AVANTI (02/07/2026): 5 posições idênticas ao "
            "centavo, somando os R$ 69.902.684,42 da coluna Outros Fundos da tabela 1.",
        ],
    ),
    Etapa(
        ordem=8,
        nome="Calcular a exposição do estoque",
        origem_vba="Org_dados.CalculoExpBD",
        implementado=True,
        funcao="analise.exposicao_estoque",
        resumo="Distribui a exposição do estoque de recebíveis entre vencido e a vencer, com e sem "
               "carrego, por faixa de prazo. É a terceira tabela da aba <code>Analise carteira</code>.",
        entrada="Estoque tratado (etapa 4) + prazo de resgate do fundo",
        saida="Tabela com quatro colunas (Vencidos, A Vencer, Vencidos C/ Carrego, "
              "A Vencer C/ Carrego) por faixa de prazo",
        regra=[
            "Considera apenas recebíveis com <code>FAIXA_PDD</code> preenchida.",
            "<b>Vencidos / A Vencer</b>: corte por <code>Status_Recebivel</code>.",
            "<b>Vencidos C/ Carrego / A Vencer C/ Carrego</b>: corte pela marca de carrego "
            "(coluna <code>Vencidos</code> da etapa 4).",
            "Só as colunas “A Vencer” são distribuídas por <code>Prazo_Atual_Dias_Corridos</code> "
            "(dia 0 até o prazo de resgate): o que está vencido não tem prazo futuro e aparece "
            "apenas no total.",
        ],
        por_administrador={"Todos": "regra única, não depende do administrador"},
        diferencas=[
            "A comparação de <code>Status_Recebivel</code> ignora maiúsculas, como o Excel: a base "
            "tem “A vencer” e “A Vencer”, e o <code>SumIfs</code> pegava as duas formas.",
            "Validado contra a planilha para o AVANTI (02/07/2026): 128 células, todas idênticas. "
            "A única diferença aparente é uma célula onde a planilha tinha um <code>@</code> "
            "digitado por cima da fórmula — ali o valor correto (780,13) é o do Python.",
        ],
    ),
    Etapa(
        ordem=9,
        nome="Montar o relatório de liquidez",
        origem_vba="Org_dados.MontarRelatorioLiquidez",
        implementado=True,
        funcao="analise.montar_relatorio",
        resumo="Consolida as três tabelas no formato final do relatório: por ativo, a exposição e "
               "o valor líquido disponível dentro do prazo de resgate.",
        entrada="Tabelas das etapas 6, 7 e 8 + tabela de prazos",
        saida="Bloco Código Ativo / Exposição / Liq. Duration / Valor Líquido, mais o PL (R$ mil)",
        regra=[
            "Um registro por produto (etapa 6), exceto as cotas de outros fundos.",
            "<code>CPR</code> e <code>Tesouraria</code> entram pelo valor cheio da exposição.",
            "Os demais produtos entram pela soma das faixas até o prazo de resgate.",
            "Cotas de outros fundos (etapa 7) entram uma a uma: cota “A Vencer” usa a soma da "
            "coluna “A Vencer C/ Carrego” do estoque; as demais têm o prazo vindo da tabela de "
            "prazos e entram pela exposição cheia <b>somente se houver prazo e ele couber no prazo "
            "de cotização</b> — prazo maior, ou ausente, zera o Valor Líquido, porque não se pode "
            "afirmar que a cota resgata dentro da janela.",
            "Cotas marcadas como vencidas são ignoradas.",
            "PL (R$ mil) = exposição total geral da carteira ÷ 1000.",
        ],
        diferencas=[
            "<b>Cota fora da janela não conta como líquida.</b> Antes o Valor Líquido de uma cota "
            "de outro fundo era a exposição cheia, qualquer que fosse o prazo — a coluna dizia que "
            "uma cota de 180 dias estava disponível em 29, contradizendo o próprio Caixa "
            "Potencial, que já aplicava esse corte. Agora só conta quem tem prazo e cabe na "
            "janela.",
            "<b>Cota sem prazo na tabela também zera</b> (decisão de 03/08/2026): sem o prazo não "
            "se pode afirmar que a cota resgata dentro da janela. É o critério mais severo, e o "
            "que mais move números — na carga de 28/07/2026 os dois cortes juntos alcançam 97 das "
            "265 cotas e R$ 3,90 bilhões, dos quais R$ 3,29 bilhões vêm justamente das 58 cotas "
            "sem prazo. Preencher a tabela de prazos devolve esse valor; os ativos nessa situação "
            "são apontados pelo <code>prazos validar</code>.",
            "Em <code>CPR</code> e <code>Tesouraria</code>, o VBA usava <code>Val()</code> sobre a "
            "célula formatada com vírgula decimal e acabava truncando os centavos "
            "(−987.673 em vez de −987.673,13). O Python mantém o valor cheio, que é o correto.",
            "Validado contra o bloco <code>Relatorio_Liquidez!B16:E24</code> da planilha para o "
            "AVANTI (02/07/2026): 9 linhas idênticas, incluindo o PL de R$ 377.578,04 mil.",
        ],
    ),
)

# Codigo usado na tabela de prazos para "sem prazo de resgate" (fundo fechado).
SEM_PRAZO_TXT = "999"


ESTILO = """
:root {
  --fundo: #ffffff; --fundo2: #f6f7f9; --texto: #1b1d21; --suave: #5c6169;
  --borda: #e2e5ea; --acento: #1f5fa9; --ok: #1a7f4b; --pendente: #a35c00;
  --codigo-fundo: #f0f2f5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fundo: #16181c; --fundo2: #1e2126; --texto: #e6e8ec; --suave: #9aa1ab;
    --borda: #2e333b; --acento: #74a9e8; --ok: #4cc38a; --pendente: #e0a458;
    --codigo-fundo: #23272e;
  }
}
:root[data-theme="dark"] {
  --fundo: #16181c; --fundo2: #1e2126; --texto: #e6e8ec; --suave: #9aa1ab;
  --borda: #2e333b; --acento: #74a9e8; --ok: #4cc38a; --pendente: #e0a458;
  --codigo-fundo: #23272e;
}
:root[data-theme="light"] {
  --fundo: #ffffff; --fundo2: #f6f7f9; --texto: #1b1d21; --suave: #5c6169;
  --borda: #e2e5ea; --acento: #1f5fa9; --ok: #1a7f4b; --pendente: #a35c00;
  --codigo-fundo: #f0f2f5;
}
body {
  background: var(--fundo); color: var(--texto); margin: 0;
  font: 16px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.pagina { max-width: 60rem; margin: 0 auto; padding: 2.5rem 1.25rem 5rem; }
h1 { font-size: 1.9rem; margin: 0 0 .3rem; letter-spacing: -.01em; }
h2 { font-size: 1.3rem; margin: 3rem 0 1rem; padding-bottom: .4rem;
     border-bottom: 2px solid var(--borda); }
h3 { font-size: 1.05rem; margin: 0 0 .2rem; }
p { margin: .6rem 0; }
.sub { color: var(--suave); margin: 0 0 2rem; font-size: .95rem; }
code { background: var(--codigo-fundo); padding: .1em .35em; border-radius: 4px;
       font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: .87em; }
.cartoes { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
           margin: 1.5rem 0; }
.cartao { background: var(--fundo2); border: 1px solid var(--borda); border-radius: 10px;
          padding: .85rem 1rem; }
.cartao .n { font-size: 1.5rem; font-weight: 650; letter-spacing: -.02em; }
.cartao .r { color: var(--suave); font-size: .8rem; text-transform: uppercase;
             letter-spacing: .04em; }
.etapa { background: var(--fundo2); border: 1px solid var(--borda); border-left: 4px solid var(--acento);
         border-radius: 10px; padding: 1.1rem 1.25rem; margin: 1rem 0; }
.etapa.pendente { border-left-color: var(--pendente); }
.cabeca { display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap; }
.ordem { color: var(--suave); font-variant-numeric: tabular-nums; font-weight: 650; }
.marca { font-size: .72rem; font-weight: 650; text-transform: uppercase; letter-spacing: .05em;
         padding: .15rem .5rem; border-radius: 20px; border: 1px solid currentColor; }
.marca.ok { color: var(--ok); }
.marca.pendente { color: var(--pendente); }
.origem { color: var(--suave); font-size: .85rem; margin: .1rem 0 .8rem; }
.grade { display: grid; gap: .5rem 1.25rem; grid-template-columns: max-content 1fr;
         margin: .8rem 0; font-size: .93rem; }
.grade dt { color: var(--suave); }
.grade dd { margin: 0; }
ul { margin: .5rem 0; padding-left: 1.2rem; }
li { margin: .25rem 0; }
.nota { border-left: 3px solid var(--pendente); background: var(--fundo);
        padding: .6rem .9rem; border-radius: 0 8px 8px 0; margin: .8rem 0 0; font-size: .93rem; }
.rolagem { overflow-x: auto; margin: 1rem 0; }
table { border-collapse: collapse; width: 100%; font-size: .92rem; }
th, td { text-align: left; padding: .5rem .7rem; border-bottom: 1px solid var(--borda);
         vertical-align: top; }
th { color: var(--suave); font-size: .78rem; text-transform: uppercase; letter-spacing: .04em;
     font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.rodape { margin-top: 3.5rem; padding-top: 1rem; border-top: 1px solid var(--borda);
          color: var(--suave); font-size: .85rem; }
"""


def _tabela(cabecalho: list[str], linhas: list[list[str]], numericas: set[int] | None = None) -> str:
    numericas = numericas or set()
    ths = "".join(f"<th>{html.escape(c)}</th>" for c in cabecalho)
    trs = []
    for linha in linhas:
        tds = "".join(
            f'<td class="num">{c}</td>' if i in numericas else f"<td>{c}</td>"
            for i, c in enumerate(linha)
        )
        trs.append(f"<tr>{tds}</tr>")
    return (
        '<div class="rolagem"><table><thead><tr>'
        + ths
        + "</tr></thead><tbody>"
        + "".join(trs)
        + "</tbody></table></div>"
    )


def _casa(chave: str, administrador: str | None) -> bool:
    from .regras_adm import normalizar

    alvo = normalizar(administrador)
    return bool(alvo) and normalizar(chave) in alvo


def _cartao(numero: str, rotulo: str) -> str:
    return f'<div class="cartao"><div class="n">{numero}</div><div class="r">{html.escape(rotulo)}</div></div>'


def _bloco_etapa(etapa: Etapa) -> str:
    classe = "etapa" if etapa.implementado else "etapa pendente"
    marca = (
        '<span class="marca ok">implementado</span>'
        if etapa.implementado
        else '<span class="marca pendente">pendente</span>'
    )
    partes = [
        f'<section class="{classe}">',
        '<div class="cabeca">',
        f'<span class="ordem">{etapa.ordem}</span>',
        f"<h3>{html.escape(etapa.nome)}</h3>",
        marca,
        "</div>",
        f'<p class="origem">Origem: <code>{html.escape(etapa.origem_vba)}</code>'
        + (f" &nbsp;·&nbsp; Implementação: <code>{html.escape(etapa.funcao)}</code>" if etapa.funcao else "")
        + "</p>",
        f"<p>{etapa.resumo}</p>",
        '<dl class="grade">',
        f"<dt>Entrada</dt><dd>{etapa.entrada}</dd>",
        f"<dt>Saída</dt><dd>{etapa.saida}</dd>",
        "</dl>",
    ]
    if etapa.regra:
        partes.append("<p><b>Regra</b></p><ul>" + "".join(f"<li>{r}</li>" for r in etapa.regra) + "</ul>")
    if etapa.por_administrador:
        partes.append("<p><b>Por administrador</b></p>")
        partes.append(
            _tabela(
                ["Administrador", "Diferença na regra"],
                [[html.escape(k), v] for k, v in etapa.por_administrador.items()],
            )
        )
    for diferenca in etapa.diferencas:
        partes.append(f'<div class="nota">{diferenca}</div>')
    partes.append("</section>")
    return "".join(partes)


def _contexto() -> dict:
    """Coleta os números vivos do projeto para o documento."""
    from .cadastro import Cadastro
    from .prazos import SEM_PRAZO, TabelaPrazos
    from .regras_adm import REGISTRO
    from .tratamentos import carregar_excluidos

    contexto: dict = {"regras": REGISTRO}

    try:
        cadastro = Cadastro().carregar()
        contexto["fundos"] = list(cadastro.fundos.values())
    except Exception as exc:  # noqa: BLE001
        log.warning("Cadastro nao lido: %s", exc)
        contexto["fundos"] = []

    try:
        tabela = TabelaPrazos.carregar()
        contexto["prazos"] = tabela
        contexto["prazos_sem"] = sum(1 for r in tabela.registros if r.prazo is None)
        contexto["prazos_fechados"] = sum(1 for r in tabela.registros if r.prazo == SEM_PRAZO)
        contexto["prazos_do_cadastro"] = sum(
            1 for r in tabela.registros if r.origem_prazo == "cadastro"
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Tabela de prazos nao lida: %s", exc)
        contexto["prazos"] = None

    try:
        contexto["excluidos"] = carregar_excluidos()
    except Exception as exc:  # noqa: BLE001
        log.warning("Listas de excluidos nao lidas: %s", exc)
        contexto["excluidos"] = {}

    try:
        from .regras_vencidos import CatalogoRegras

        contexto["catalogo"] = CatalogoRegras.carregar()
    except Exception as exc:  # noqa: BLE001
        log.warning("Regras de vencidos nao lidas: %s", exc)
        contexto["catalogo"] = None

    try:
        from .indicadores import carregar_config

        contexto["config_indicadores"] = carregar_config()
    except Exception as exc:  # noqa: BLE001
        log.warning("Config de indicadores nao lida: %s", exc)
        contexto["config_indicadores"] = None

    return contexto


def gerar_html(destino: Path = ARQUIVO_PADRAO) -> Path:
    """Monta o documento e grava em disco. Devolve o caminho."""
    ctx = _contexto()
    agora = dt.datetime.now()
    fundos = ctx["fundos"]

    implementadas = sum(1 for e in ETAPAS if e.implementado)
    por_adm: dict[str, int] = {}
    for fundo in fundos:
        por_adm[fundo.administrador or "(sem administrador)"] = (
            por_adm.get(fundo.administrador or "(sem administrador)", 0) + 1
        )

    partes: list[str] = [
        '<div class="pagina">',
        "<h1>Tratamentos do Relatório de Liquidez</h1>",
        f'<p class="sub">Documento técnico gerado automaticamente a partir do código e dos '
        f"arquivos de configuração &nbsp;·&nbsp; {agora.strftime('%d/%m/%Y %H:%M')} "
        f"&nbsp;·&nbsp; versão {html.escape(__version__)}</p>",
        '<div class="cartoes">',
        _cartao(f"{implementadas}/{len(ETAPAS)}", "etapas implementadas"),
        _cartao(str(len(fundos)), "fundos no cadastro"),
        _cartao(
            str(len(ctx["prazos"].registros)) if ctx.get("prazos") else "—",
            "ativos com prazo",
        ),
        _cartao(str(sum(1 for r in ctx["regras"] if r.implementado)), "administradores tratados"),
        "</div>",
        "<p>Este documento descreve os tratamentos aplicados aos dados extraídos do SQL Server "
        "antes da montagem do relatório de liquidez. As regras vieram das macros VBA da planilha "
        "<code>Relatório de Liquidez_Multiplica_V2.xlsm</code> e são executadas na mesma ordem em "
        "que a rotina <code>GeraRel</code> as chamava.</p>",
        "<h2>Etapas</h2>",
    ]
    partes += [_bloco_etapa(etapa) for etapa in ETAPAS]

    # --- administradores ---
    partes.append("<h2>Regras por administrador</h2>")
    partes.append(
        "<p>O administrador de cada fundo vem do cadastro da CVM. Administrador sem tratamento "
        "definido gera aviso e não é processado — equivale ao <code>Case Else</code> do VBA, que "
        "exibia uma mensagem e interrompia.</p>"
    )
    linhas_adm = []
    for regra in ctx["regras"]:
        casados = sum(
            1 for f in fundos if regra.reconhece(f.administrador)
        )
        linhas_adm.append(
            [
                html.escape(regra.chave),
                "sim" if regra.implementado else "não",
                str(casados),
                html.escape(regra.filtro_vencidos_carteira or "—"),
                ", ".join(html.escape(f) for f in regra.filtro_outros_fundos) or "—",
            ]
        )
    partes.append(
        _tabela(
            ["Administrador", "Tratado", "Fundos", "Filtro de vencidos", "Filtro de outros fundos"],
            linhas_adm,
            numericas={2},
        )
    )
    if por_adm:
        partes.append("<p><b>Distribuição do cadastro</b></p>")
        partes.append(
            _tabela(
                ["Administrador (CVM)", "Fundos"],
                [[html.escape(k), str(v)] for k, v in sorted(por_adm.items(), key=lambda i: -i[1])],
                numericas={1},
            )
        )

    # --- indicadores ---
    partes.append("<h2>Indicadores de liquidez</h2>")
    partes.append(
        "<p>O bloco de indicadores do relatório (enquadramento) cruza a ponta ativa com a passiva. "
        "As fontes, confirmadas nas fórmulas da planilha:</p>"
    )
    partes.append(
        _tabela(
            ["Indicador", "Fórmula", "Fonte"],
            [
                ["Caixa Potencial", "soma do Valor Líquido dos ativos com Liq. Duration ≤ prazo de cotização, ÷ 1000", "etapas 6–9 (ponta ativa)"],
                ["Maior cotista / 3 maiores", "<code>LARGE(vlSaldoTotal, 1..3)</code> ÷ 1000", "DB_Passivo"],
                ["GDP", "média + 2,33 × desvio dos saldos dos cotistas", "DB_Passivo"],
                ["Maiores distribuidores", "soma de <code>vlSaldoTotal</code> por distribuidor", "DB_Passivo"],
                ["RCO (resgate máximo)", "maior resgate em um único dia", "MOV"],
                ["Caixa/PL vs ANBIMA", "Caixa/PL comparado ao percentil 95% por horizonte", "constantes ANBIMA"],
            ],
        )
    )
    partes.append(
        '<div class="nota"><b>Corte pelo prazo de cotização (cc), não pelo PzResg.</b> O Caixa '
        "Potencial e as faixas das etapas 6, 8 e 9 usam o prazo de cotização do fundo (coluna "
        "<code>cc</code> do cadastro), não o PzResg que a fórmula original da planilha usava. Nos "
        "14 fundos em que os dois diferem, isto muda o resultado — é uma correção, confirmada com "
        "a área de risco.</div>"
    )
    partes.append(
        '<div class="nota"><b>Distribuidores.</b> A planilha somava apenas “BTG” e “ATIVA” '
        "(fixos na fórmula), o que dava <code>#DIV/0!</code> quando o fundo não tinha esses "
        "distribuidores. Aqui somam-se os maiores distribuidores de fato; sem distribuidor "
        "cadastrado, o indicador fica em branco em vez de erro.</div>"
    )
    cfg = ctx.get("config_indicadores")
    if cfg:
        limites = ", ".join(f"{k} > {v:g}" for k, v in cfg["limites"].items())
        partes.append(f"<p><b>Limites de enquadramento:</b> {html.escape(limites)}.</p>")
    partes.append(
        '<div class="nota"><b>Validado contra a planilha</b> para o AVANTI (02/07/2026): PL, '
        "Caixa Potencial, Caixa/PL, maior cotista, 3 maiores, Cx/Cot, Cx/Cot 3, média, desvio, "
        "Cx/GDP, resgate máximo, Caixa/Resgate e o enquadramento dos 9 horizontes ANBIMA "
        "(5 enquadrados, 4 desenquadrados) — todos iguais aos blocos "
        "<code>G14:J36</code> e <code>N14:W20</code> da aba <code>Relatorio_Liquidez</code>. "
        "A única diferença é de R$ 0,08 no Caixa Potencial, que vem da truncagem do "
        "<code>Val()</code> do VBA em CPR e Tesouraria (etapa 9): o valor do Python é o correto. "
        "Os distribuidores, que na planilha davam <code>#DIV/0!</code>, ficam em branco.</div>"
    )

    # --- identificacao dos vencidos ---
    partes.append("<h2>Identificação dos ativos vencidos</h2>")
    catalogo = ctx.get("catalogo")
    if catalogo is None:
        partes.append("<p>Arquivo de regras não encontrado.</p>")
    else:
        partes.append(
            "<p>As regras ficam em <code>config/regras_vencidos.json</code>, de forma declarativa: "
            "incluir um administrador novo é alterar configuração, não código. Todas as condições "
            "de uma regra valem em conjunto. Do que for selecionado extrai-se o emissor, e depois "
            "<b>todas</b> as linhas daquele emissor <b>naquele fundo</b> são marcadas como "
            "vencidas.</p>"
        )
        linhas_regra = []
        for chave, regra in catalogo.regras.items():
            fundos_do_adm = sum(1 for f in fundos if regra.administrador and
                                _casa(regra.administrador, f.administrador))
            linhas_regra.append(
                [
                    html.escape(chave),
                    "<br>".join(f"<code>{html.escape(c)}</code>" for c in regra.descrever()),
                    html.escape(regra.coluna_emissor),
                    str(fundos_do_adm),
                ]
            )
        partes.append(
            _tabela(
                ["Administrador", "Condições (todas precisam valer)", "Coluna de emissor", "Fundos"],
                linhas_regra,
                numericas={3},
            )
        )
        for regra in catalogo.regras.values():
            if regra.descricao:
                partes.append(
                    f'<div class="nota"><b>{html.escape(regra.administrador)}:</b> '
                    f"{html.escape(regra.descricao)}</div>"
                )

        partes.append("<h3 style='margin-top:2rem'>Estoque de recebíveis</h3>")
        if catalogo.estoque.definida:
            partes.append(
                _tabela(
                    ["Condições", "Coluna de sacado"],
                    [[
                        "<br>".join(f"<code>{html.escape(c)}</code>" for c in catalogo.estoque.descrever()),
                        html.escape(catalogo.estoque.coluna_emissor),
                    ]],
                )
            )
            if catalogo.estoque.descricao:
                partes.append(f'<div class="nota">{html.escape(catalogo.estoque.descricao)}</div>')
        else:
            partes.append("<p>Sem regra definida para o estoque.</p>")

        if catalogo.pendentes:
            partes.append("<h3 style='margin-top:2rem'>Administradores sem critério definido</h3>")
            partes.append(
                "<p>Para estes, nenhum ativo vencido é identificado e o processamento registra "
                "aviso. O critério de cada um ainda precisa ser levantado no arquivo de origem.</p>"
            )
            partes.append(
                "<ul>" + "".join(f"<li>{html.escape(a)}</li>" for a in catalogo.pendentes) + "</ul>"
            )

    # --- ativos excluidos ---
    partes.append("<h2>Ativos excluídos</h2>")
    partes.append(
        "<p>Lista de códigos que não entram no cálculo de exposição. Era digitada à mão nas "
        "colunas C e G da aba <code>Vencidos</code>; hoje está em "
        "<code>config/ativos_excluidos.json</code>. A chave <code>padrão</code> corresponde à "
        "coluna C, que valia para todo administrador que não fosse a PLANNER.</p>"
    )
    if ctx["excluidos"]:
        partes.append(
            _tabela(
                ["Administrador", "Códigos", "Quantidade"],
                [
                    [
                        html.escape(chave),
                        ", ".join(f"<code>{html.escape(str(c))}</code>" for c in valores),
                        str(len(valores)),
                    ]
                    for chave, valores in ctx["excluidos"].items()
                ],
                numericas={2},
            )
        )

    # --- prazos ---
    partes.append("<h2>Tabela de prazos</h2>")
    if ctx.get("prazos"):
        tabela = ctx["prazos"]
        partes.append(
            f"<p>{len(tabela.registros)} ativos em <code>config/prazos_ativos.csv</code>, "
            f"consultados pelo <code>Codigo_Ativo</code>. Destes, "
            f"<b>{ctx['prazos_do_cadastro']}</b> têm o prazo vindo do cadastro do fundo, "
            f"<b>{ctx['prazos_fechados']}</b> são fundos fechados (código {SEM_PRAZO_TXT}) e "
            f"<b>{ctx['prazos_sem']}</b> estão sem prazo definido — estes últimos caem no cálculo "
            f"por data e são apontados pela validação.</p>"
        )
        partes.append(
            "<p>A aba <code>Fundos</code> da planilha tinha duas tabelas lado a lado. Foram "
            "unificadas em uma só, com a tabela <code>A:D</code> prevalecendo — a <code>K:L</code> "
            "não era lida por nenhuma macro. Onde as duas divergiam, o valor antigo ficou "
            "registrado na coluna <code>observacao</code>. Os códigos <code>999</code> e "
            "<code>9999</code>, que indicavam a mesma coisa, foram unificados em <code>999</code>.</p>"
        )
        sem_prazo = [r for r in tabela.registros if r.prazo is None]
        if sem_prazo:
            partes.append("<p><b>Ativos sem prazo definido</b></p>")
            partes.append(
                _tabela(
                    ["Código do ativo", "Código da operação", "CNPJ"],
                    [
                        [
                            html.escape(r.codigo_ativo),
                            html.escape(r.codigo_op),
                            html.escape(r.cnpj or "—"),
                        ]
                        for r in sorted(sem_prazo, key=lambda r: r.codigo_ativo)
                    ],
                )
            )
    else:
        partes.append("<p>Tabela de prazos não encontrada.</p>")

    partes.append(
        '<div class="rodape">Gerado por <code>python -m liquidez documentar</code>. '
        "Não editar à mão: o conteúdo é montado a partir do código e da configuração, e qualquer "
        "alteração manual se perde na próxima geração.</div>"
    )
    partes.append("</div>")

    documento = (
        "<!doctype html>\n<html lang=\"pt-BR\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Tratamentos do Relatório de Liquidez</title>\n"
        f"<style>{ESTILO}</style>\n</head>\n<body>\n"
        + "".join(partes)
        + "\n</body>\n</html>\n"
    )

    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(documento, encoding="utf-8")
    log.info("Documento gerado: %s", destino)
    return destino
