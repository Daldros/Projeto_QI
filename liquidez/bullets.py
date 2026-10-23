"""Controle das operacoes projetadas como bullet a partir da carteira.

O administrador QI Tech entrega em `Base_Origem = DC` operacoes que ja vem
precificadas por accrual. A maioria e bullet -- principal e juros no vencimento
-- e para essas a carteira sozinha basta: valor de hoje, data de vencimento,
pronto. Nao ha cronograma a consultar porque nao ha o que agendar.

A premissa tem dois alcances. O primeiro e o do ADMINISTRADOR, acima. O segundo
e o da FAMILIA DO PAPEL (`FAMILIAS_PRESUMIDAS`): o CRA entra em qualquer fundo,
porque tambem para ele a carteira ja traz valor e vencimento, e sem isso o papel
ficaria fora do fluxo ate alguem ler o Termo de Securitizacao. Nos dois casos
vale a mesma hierarquia -- a premissa e o piso, o cronograma ganha quando existe
e a operacao entra na fila de conferencia enquanto nao for verificada.

Mas algumas TEM parcelas, e so a planilha do fundo revela quais. Como a planilha
nao chega todo dia, sobretudo para operacao nova, a projecao parte da premissa
bullet e sinaliza o que ainda nao foi conferido. Verificada a operacao, ela sai
da fila por um de dois caminhos: confirmada como bullet, ou identificada com
fluxo -- e ai a planilha assume.

O marco zero existe para a fila nascer trabalhavel. Sem ele, toda operacao viva
apareceria como novidade no primeiro dia e a lista seria grande demais para
alguem olhar. Com ele, o que ja existia vira estoque conhecido e o alerta
diario mostra so o que mudou.

Este modulo nao calcula nada -- so guarda quem e o que. A projecao esta em
`fluxo.projetar_bullet`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)

ARQUIVO = Path("config") / "cronograma" / "bullets.json"

# O recorte da premissa. E propriedade do ADMINISTRADOR, nao do fundo nem do
# papel: a convencao de entregar DC ja corrigido e da QI Tech. Comparado por
# prefixo normalizado -- a razao social muda de pontuacao com o tempo.
#
# A PLANNER foi avaliada em 21/08/2026 e ficou de FORA por ora. Nao por falta de
# dado -- 416 das 498 linhas de DC dela tem valor e vencimento na carteira, R$
# 1,74 bi -- mas porque a chave nao passa: o `Codigo_Op` dela e numero puro
# ("22740"), e `cronograma.PADRAO_CODIGO` exige o prefixo B/C da QI. Sem casar,
# `normalizar_codigo` devolve None e a linha some antes da projecao. Incluir a
# PLANNER aqui sem resolver a chave nao faz nada -- so cria premissa inerte.
# "SINGULARE" e o nome antigo da QI CORRETORA e e como o HOD_TB_CADASTRO_FUNDOS
# (fonte das fichas desde 05/10/2026) a registra; sem o alias o MULTIPLICA FIDC
# caia fora do escopo e o fluxo exigia planilha. Mesmo alias de regras_adm.py.
ADMINISTRADORES_BULLET = ("QI CORRETORA", "SINGULARE", "QI TECH")

PRODUTO_BULLET = "DC"

# Familias de papel que sao bullet POR CONSTRUCAO -- o tipo do instrumento
# responde a pergunta, entao nao ha o que conferir. Comparadas por prefixo do
# `Codigo_Ativo`, que e onde a carteira carrega o tipo.
#
# CCEXP e carta de credito de exportacao: pagamento unico no vencimento, prazo
# curto, e rola sem parar. Sem esta regra, cada rolagem devolveria as mesmas
# dezenas de operacoes para a fila toda semana -- uma pergunta cuja resposta ja
# se sabe, repetida ate a fila perder o sentido.
#
# So entra aqui familia cujo fluxo decorre do instrumento. NCPX, por exemplo,
# NAO entra: la ha operacao com parcelas, e a ausencia de aba e casual.
#
# PDD*/PDC* (PDDNC, PDCCE, PDCEX, PDD_L) e a provisao lancada pelo administrador
# como "operacao" propria, com valor NEGATIVO e vencimento placeholder (2049/2050):
# nao e papel, nao tem cronograma e so existe para abater a operacao a que se
# refere. MEZAN e SRP sao os pares de contrapartida da estrutura (uma linha
# positiva e uma negativa de mesmo valor, mesmo vencimento): no MULTIPLICA FIDC em
# 30/09/2026 eram 170 operacoes somando R$ 0,00. Nenhum deles pede conferencia
# -- sem esta regra eram 179 das 256 linhas da fila.
FAMILIAS_BULLET = ("CCEXP", "PDD", "PDC", "MEZAN", "SRP")

# Familias cuja linha na carteira e o AGREGADO de um pool de recebiveis, nao uma
# operacao com cronograma proprio. No MULTIPLICA LONG TERM e no MULTIAGRO
# (30/09/2026) a carteira inteira de DC e MEZAN/SRP -- R$ 391 mi e R$ 1,15 bi --
# e o Beyond nao tem o que casar (nenhum Codigo_Op NCPX/CCBPX). O fluxo delas
# vem do ESTOQUE do proprio fundo (HOD_TB_ESTOQUE_UNIFICADO): o valor da
# carteira e distribuido pelos vencimentos dos recebiveis a vencer, pro rata ao
# valor presente (`fluxo.pesos_do_estoque`). Sem estoque, cai na regra bullet.
FAMILIAS_ESTOQUE = ("MEZAN", "SRP")


def e_familia_estoque(codigo_ativo) -> bool:
    texto = _normalizar(codigo_ativo)
    return any(texto.startswith(_normalizar(f)) for f in FAMILIAS_ESTOQUE)


def e_familia_bullet(codigo_ativo) -> bool:
    texto = _normalizar(codigo_ativo)
    return any(texto.startswith(_normalizar(f)) for f in FAMILIAS_BULLET)


# Familias que entram na premissa bullet INDEPENDENTE do administrador e do
# produto -- o recorte de `ADMINISTRADORES_BULLET` nao se aplica a elas.
#
# Nao confundir com FAMILIAS_BULLET: aquelas sao bullet por construcao e nem
# chegam a fila. CRA amortiza com frequencia, entao continua pedindo
# conferencia como qualquer operacao presumida.
#
# O CRA entra porque a carteira sozinha ja da o que a premissa precisa -- valor
# e data de vencimento. Sem isso o papel fica fora da projecao ate alguem ler o
# Termo de Securitizacao, e um CRA de dezenas de milhoes ausente do fluxo e pior
# do que um CRA projetado sem os juros do periodo. Quando o cadastro chega, ele
# ganha: a premissa e o piso.
#
# Comparado por prefixo do `Codigo_Ativo`, como as demais familias. Se o
# administrador nomear o papel de outro jeito, a operacao cai em
# `sem_cronograma` e o relatorio mostra o `Codigo_Ativo` real -- e dali se
# corrige esta lista.
FAMILIAS_PRESUMIDAS = ("CRA",)


def e_familia_presumida(codigo_ativo) -> bool:
    """A operacao entra na premissa bullet mesmo fora do escopo do administrador."""
    texto = _normalizar(codigo_ativo)
    return any(texto.startswith(_normalizar(f)) for f in FAMILIAS_PRESUMIDAS)

# Estados possiveis. Ausencia do registro = operacao nunca vista, ou seja, nova.
ESTOQUE = "estoque"                      # existia no marco zero
PENDENTE = "pendente"                    # apareceu depois, aguarda conferencia
CONFIRMADO = "confirmado"                # conferida: e bullet mesmo
PLANILHA_PENDENTE = "planilha_pendente"  # conferida: TEM fluxo, planilha a caminho
POR_REGRA = "por_regra"                  # familia bullet: nem chega ao registro

# Estados que ainda pedem sua atencao.
NAO_CONFERIDOS = (ESTOQUE, PENDENTE)

CABECALHO = (
    "Controle das operacoes DC projetadas como bullet. 'estoque' e o que existia "
    "no marco zero; 'pendente' apareceu depois e aguarda conferencia; "
    "'confirmado' foi conferida e e bullet; 'planilha_pendente' foi conferida, "
    "TEM fluxo e espera a planilha -- segue projetada como bullet e marcada "
    "como incorreta. Editavel a mao ou por 'python -m liquidez bullet ...'."
)


def _normalizar(texto) -> str:
    """Maiusculas sem acento, para comparar razao social sem depender de grafia."""
    bruto = str(texto or "").strip().upper()
    return "".join(
        c for c in unicodedata.normalize("NFKD", bruto)
        if not unicodedata.combining(c)
    )


def e_administrador_bullet(administrador) -> bool:
    texto = _normalizar(administrador)
    return any(texto.startswith(_normalizar(a)) for a in ADMINISTRADORES_BULLET)


def fundos_no_escopo(fundos) -> list:
    """Os fundos onde a premissa bullet vale."""
    return [f for f in fundos if e_administrador_bullet(getattr(f, "administrador", None))]


# --- Persistencia -----------------------------------------------------------

def carregar(caminho: Path = ARQUIVO) -> dict:
    """O registro inteiro. Arquivo ausente e o mesmo que registro vazio."""
    caminho = Path(caminho)
    vazio = {"marco_zero": None, "operacoes": {}}
    if not caminho.exists():
        return vazio
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # Registro ilegivel nao pode derrubar a rodada. Sem ele toda operacao
        # volta a pedir conferencia, que e o lado seguro do erro -- avisar
        # demais e melhor que projetar errado em silencio.
        log.warning("Nao foi possivel ler %s (%s); nenhum estado aplicado.",
                    caminho, exc)
        return vazio
    operacoes = dados.get("operacoes")
    return {
        "marco_zero": dados.get("marco_zero"),
        "operacoes": operacoes if isinstance(operacoes, dict) else {},
    }


def salvar(registro: dict, caminho: Path = ARQUIVO) -> Path:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    corpo = {
        "descricao": CABECALHO,
        "marco_zero": registro.get("marco_zero"),
        "operacoes": dict(sorted(registro.get("operacoes", {}).items())),
    }
    caminho.write_text(
        json.dumps(corpo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return caminho


# --- Marco zero -------------------------------------------------------------

def gravar_marco(
    operacoes: list[tuple[str, str]],
    data_marco,
    caminho: Path = ARQUIVO,
    refazer: bool = False,
) -> dict:
    """Fotografa as operacoes vivas na data do marco como estoque conhecido.

    Roda uma vez. Refazer depois reclassificaria como estoque tudo que apareceu
    desde entao -- apagando exatamente a distincao que o marco existe para
    criar. Por isso `refazer` e explicito.

    `operacoes` e uma lista de (codigo_op, cnpj_fundo).
    """
    registro = carregar(caminho)
    if registro["marco_zero"] and not refazer:
        raise ValueError(
            f"Marco zero ja gravado em {registro['marco_zero']}. "
            f"Use --refazer para substituir (isso reclassifica como estoque "
            f"tudo que apareceu depois)."
        )

    data = _texto_data(data_marco)
    novas = {}
    for codigo, cnpj in operacoes:
        novas[codigo] = {
            "estado": ESTOQUE,
            "cnpj_fundo": cnpj,
            "visto_em": data,
            "conferido_em": None,
            "nota": "",
        }
    registro = {"marco_zero": data, "operacoes": novas}
    salvar(registro, caminho)
    return registro


def _texto_data(valor) -> str:
    if isinstance(valor, (dt.date, dt.datetime)):
        return valor.strftime("%Y-%m-%d")
    return str(valor)[:10]


# --- Movimento diario -------------------------------------------------------

def registrar_vistas(
    operacoes: list[tuple[str, str]],
    data_base,
    caminho: Path = ARQUIVO,
) -> list[str]:
    """Anota as operacoes que aparecem pela primeira vez e devolve quais sao.

    Sem esta gravacao a mesma operacao seria "nova" todo dia e o alerta nunca
    esvaziaria. Quem ja esta no registro nao e tocado -- reprocessar uma data
    antiga nao reescreve estado nem data de conferencia.
    """
    registro = carregar(caminho)
    conhecidas = registro["operacoes"]
    data = _texto_data(data_base)

    novas = []
    for codigo, cnpj in operacoes:
        if codigo in conhecidas:
            # Reprocessamento de data anterior: guarda a primeira aparicao.
            visto = conhecidas[codigo].get("visto_em")
            if visto and data < visto:
                conhecidas[codigo]["visto_em"] = data
            continue
        conhecidas[codigo] = {
            "estado": PENDENTE,
            "cnpj_fundo": cnpj,
            "visto_em": data,
            "conferido_em": None,
            "nota": "",
        }
        novas.append(codigo)

    if novas:
        salvar(registro, caminho)
    return novas


def estados_de(codigos, caminho: Path = ARQUIVO) -> dict[str, str]:
    """Estado de cada codigo. Desconhecido conta como pendente de conferencia."""
    conhecidas = carregar(caminho)["operacoes"]
    return {
        c: conhecidas.get(c, {}).get("estado", PENDENTE) for c in codigos
    }


# --- Conferencia ------------------------------------------------------------

def _marcar(codigo: str, estado: str, nota: str, caminho: Path) -> dict:
    from .cronograma import normalizar_codigo

    normalizado = normalizar_codigo(codigo)
    if not normalizado:
        raise ValueError(f"Codigo_Op invalido: {codigo!r}")

    registro = carregar(caminho)
    atual = registro["operacoes"].get(normalizado, {})
    atual.update({
        "estado": estado,
        "cnpj_fundo": atual.get("cnpj_fundo", ""),
        "visto_em": atual.get("visto_em"),
        "conferido_em": dt.date.today().isoformat(),
        "nota": nota,
    })
    registro["operacoes"][normalizado] = atual
    salvar(registro, caminho)
    return {normalizado: atual}


def confirmar(codigo: str, nota: str = "", caminho: Path = ARQUIVO) -> dict:
    """Conferida: e bullet mesmo. Sai da fila, segue projetada igual."""
    return _marcar(codigo, CONFIRMADO, nota, caminho)


def marcar_planilha_pendente(codigo: str, nota: str = "", caminho: Path = ARQUIVO) -> dict:
    """Conferida: TEM fluxo. Segue projetada como bullet, marcada como incorreta.

    Sai da fila porque nao precisa ser conferida de novo -- o que falta e a
    planilha, nao a conferencia. Mas continua aparecendo no relatorio, senao um
    erro conhecido sumiria de vista.
    """
    return _marcar(codigo, PLANILHA_PENDENTE, nota, caminho)


def confirmar_varias(
    codigos,
    nota: str = "",
    estado: str = CONFIRMADO,
    caminho: Path = ARQUIVO,
) -> list[str]:
    """Confirma um lote de uma vez, com uma unica gravacao.

    Familias inteiras de papel sao homogeneas -- CCEXP e carta de credito de
    exportacao, bullet por construcao. Responder 123 vezes ao mesmo fato seria
    trabalho sem informacao nova.

    Nao mexe em quem ja foi conferido: reconfirmar apagaria a nota e a data de
    quem foi olhado individualmente.
    """
    registro = carregar(caminho)
    hoje = dt.date.today().isoformat()
    mexidos = []
    for codigo in codigos:
        atual = registro["operacoes"].get(codigo, {})
        if atual.get("estado") in (CONFIRMADO, PLANILHA_PENDENTE):
            continue
        atual.update({
            "estado": estado,
            "cnpj_fundo": atual.get("cnpj_fundo", ""),
            "visto_em": atual.get("visto_em"),
            "conferido_em": hoje,
            "nota": nota,
        })
        registro["operacoes"][codigo] = atual
        mexidos.append(codigo)
    if mexidos:
        salvar(registro, caminho)
    return mexidos


def remover(codigo: str, caminho: Path = ARQUIVO) -> bool:
    """Apaga o registro: a operacao volta a ser tratada como nunca vista."""
    from .cronograma import normalizar_codigo

    normalizado = normalizar_codigo(codigo)
    registro = carregar(caminho)
    if normalizado not in registro["operacoes"]:
        return False
    del registro["operacoes"][normalizado]
    salvar(registro, caminho)
    return True


def limpar(codigos_vivos, caminho: Path = ARQUIVO) -> list[str]:
    """Tira do registro quem nao esta mais na carteira.

    Operacao rola (a velha e liquidada, uma nova assume o principal) e o
    registro acumularia entradas mortas. Aviso de operacao que nao existe mais
    nao e aviso -- e ruido que faz a lista parecer maior do que e.
    """
    registro = carregar(caminho)
    vivos = set(codigos_vivos)
    mortos = [c for c in registro["operacoes"] if c not in vivos]
    for c in mortos:
        del registro["operacoes"][c]
    if mortos:
        salvar(registro, caminho)
    return mortos
