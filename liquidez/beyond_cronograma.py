"""Cronograma de NC/CCB deduzido do Beyond, para operacao nova sem planilha.

A carteira do administrador nao traz o fluxo futuro de NC e CCB: entrega uma
linha por operacao, com valor de hoje e data do ultimo vencimento. Quem tem o
fluxo e o cedente, na `TB_LIQUIDEZ_OPERACOES_4` -- uma linha por parcela, com
data e valor aplicado. Este modulo liga as duas pontas e monta o cronograma.

HIERARQUIA. A planilha do fundo continua soberana; este cronograma e PREMISSA,
e fica abaixo dela e acima do bullet. Operacao que ja tenha cronograma
cadastrado nao passa por aqui. Quando a planilha chegar depois, ela sobrescreve
-- e por isso a operacao NAO sai da fila de conferencia do `bullets.py`: o que
muda e a projecao, nao o controle.

ALCANCE. O Beyond so conhece o que nasce de CESSAO DE RECEBIVEL. Isso cobre
quase todo o universo da QI/Singulare -- 14 dos 33 fundos ativos tem NC/CCB e
casam com codinome, R$ 3,54 bi em 10/09/2026 -- e quase nada da PLANNER, onde a
carteira e feita de acao judicial, precatorio, confissao de divida e imovel em
garantia, que nao passam por cessao. Dos 45 fundos PLANNER com posicao, 5 casam
alguma operacao e so o G VALUE casa bem (33 operacoes). Nao adianta procurar
codinome para ARTEB, MSB ou OLIVENZA: o ativo deles nao nasce la.

SO OPERACAO NOVA. Nao ha reconstrucao de historico. O casamento roda sobre o
que entrou na carteira desde a posicao anterior, e nessa janela a ambiguidade
praticamente desaparece: medido em 52 operacoes novas de 70 dias (jul a set de
2026, sete fundos), 29 tinham candidata unica na data e 21 das 23 restantes
foram resolvidas pelo valor -- 50 de 52. Casar estoque historico e outra
conversa: ali centenas de operacoes dividem as mesmas datas e o valor deixa de
desempatar.

A CADENCIA. A `TB_LIQUIDEZ_OPERACOES_4` e semanal, nao diaria: das 75 leituras
ate 15/09/2026, 56 caem em quinta-feira, com 7 dias de intervalo em 46 delas; o
maior buraco foi de 15 dias. Desde 14/08/2026 ha cargas extras em outros dias,
mas o padrao segue sendo quinta e fechamento de mes. Por isso nao se procura a
leitura da data da posicao -- procura-se a mais recente que existir.

CAPTURA UNICA. O Beyond e consultado UMA vez por operacao, na primeira leitura
em que ela aparece. Dali em diante o cronograma e cadastro: o `fluxo.py` le do
arquivo, como ja faz com o cronograma de planilha. Isso tambem protege o
relatorio enquanto o formato de atualizacao da tabela nao esta estavel. Tres
consequencias:

  - A data do snapshot deixa de ser critica. Consulta-se SEMPRE a leitura mais
    recente (`snapshot_atual`), nunca a data da posicao: a tabela e semanal, e
    procurar pela data-base devolve vazio quase sempre. Operacao que entra num
    dia sem leitura fica em bullet e e capturada na leitura seguinte.
  - Reprocessar data passada nao depende do Beyond, e o numero nao muda entre
    duas rodadas da mesma data. Se o fluxo fosse buscado a cada execucao, mudaria:
    o Beyond reprojeta com a curva dele.
  - Correcao posterior no Beyond (renegociacao, mudanca de cronograma) NAO chega
    sozinha. E o mesmo comportamento do cadastro por planilha, e recapturar uma
    operacao e acao manual explicita -- nunca efeito colateral do rodar diario.

A CHAVE. Nao existe identificador comum: a carteira numera `C456501` e o Beyond
`153010`, em sistemas independentes. O que liga e

    carteira.Data_Aquisicao == beyond.Data_Operacao          (sempre)
    carteira.Custo_Total    == soma do Valor_Aplicado         (desempate)

O valor confirma, nao identifica -- ha operacao adquirida com desagio (uma em
240 medidas) em que ele nao bate. Nesse caso, havendo candidata unica na data,
o casamento vale; havendo mais de uma, a operacao vai para a fila.

A TAXA. O Beyond guarda em `Taxa_Final` a taxa MENSAL total projetada -- CDI
estimado mais spread. A planilha do fundo guarda o spread. Validado em 155
operacoes que tem os dois: nas prefixadas as duas taxas coincidem ate o quarto
decimal; nas de CDI o Beyond fica ~15 p.p. acima, que e exatamente o CDI que
ele projetou. Por isso o spread sai por divisao:

    spread = (1 + Taxa_Final) ** 12 / (1 + cdi_na_data) - 1

e o CDI volta na projecao pela curva da casa, como o `fluxo.py` ja faz para
cronograma cadastrado. As colunas de valor do Beyond -- `Aberto`,
`Valor_Vencimento`, `Taxa_Efetiva` -- nao entram: sao projecao dele com a curva
dele, e duas delas vem nulas na maioria das linhas.

PARCELA VENCIDA NAO ENTRA. O Beyond ainda carrega a parcela que venceu no dia
anterior enquanto a carteira ja a baixou para um ativo `VCNC`. Sem o filtro, o
cronograma nasce com uma parcela a mais -- foi a diferenca que apareceu em
CAPRETZ, SORGATTO e TERRAFER na validacao contra a planilha.
"""

from __future__ import annotations

import datetime as dt
import logging
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

ORIGEM = "beyond"

# Base da planilha: 12 meses de 30 dias. Conferida contra as celulas de um
# cronograma importado -- (1+am)**12-1 e (1+aa)**(1/360)-1 batem exatamente.
BASE_TAXA = 360
MESES_ANO = 12

# Um arquivo por fundo, com o mesmo desenho do cronograma vindo de planilha.
# Pasta propria porque o `cronograma importar` reescreve os arquivos da planilha
# a partir dela -- e apagaria o que foi capturado aqui, como ja acontece com os
# cadastros manuais.
DIR_BEYOND = Path("config") / "cronograma" / "beyond"

# Como o Beyond nomeia o indexador, e o que isso vira no cronograma.
INDEXADORES = {
    "100% CDI": ("CDI", True),
    "100% SELIC": ("SELIC", True),
    "PRE": (None, False),
}

# Tolerancia do desempate por valor. Um real cobre arredondamento de centavos
# sem deixar passar operacao diferente -- a menor distancia entre operacoes
# concorrentes na amostra foi de milhares de reais.
TOLERANCIA_VALOR = 1.00

# Meses por ano, para anualizar a Taxa_Final mensal.
MESES = 12


def _data(valor) -> dt.date:
    """Normaliza o que vem do banco para `date`.

    O driver devolve `date`, `datetime` ou texto conforme a coluna e o tipo, e
    o cronograma precisa de um so formato para serializar.
    """
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    return dt.date.fromisoformat(str(valor)[:10])


@dataclass
class Parcela:
    dt_vencimento: dt.date
    vl_amortizacao: float

    def to_dict(self) -> dict:
        return {"dt_vencimento": self.dt_vencimento.isoformat(),
                "vl_amortizacao": round(self.vl_amortizacao, 2)}


@dataclass
class CronogramaBeyond:
    """O cronograma no formato que o `fluxo.py` consome, mais a procedencia."""

    cnpj_fundo: str
    codigo_op: str
    operacao: int
    emissor: str
    tp_instrumento: str
    vl_principal: float
    dt_operacao: dt.date
    dt_vencimento: dt.date
    indexado: bool
    indexador: str | None
    taxa_aa: float | None
    taxa_am_total: float
    cdi_na_data: float | None
    n_parcelas: int
    parcelas: list[Parcela] = field(default_factory=list)
    criterio: str = ""
    observacao: str = ""

    def to_operacao(self) -> dict:
        """A operacao no mesmo formato que a planilha grava.

        Os campos e a ordem sao os de `cronograma.COLUNAS_OPERACOES`, para que o
        carregador do Beyond seja um gemeo do da planilha e o `fluxo.py` receba
        exatamente a mesma coisa. O que nao existe aqui fica None, como no
        cadastro manual: o Beyond nao tem numero de contrato nem papel na B3.

        As taxas seguem a convencao da planilha, conferida contra as celulas:
        `taxa_aa = (1+taxa_am)**12 - 1` e `taxa_ad = (1+taxa_aa)**(1/360) - 1`
        (base 360 = 12 meses de 30 dias), com igualdade exata.
        """
        taxa_am, taxa_ad = _taxas_derivadas(self.taxa_aa)
        return {
            "cnpj_fundo": self.cnpj_fundo,
            "codigo_op": self.codigo_op,
            "aba": f"(beyond: operacao {self.operacao})",
            "emissor": self.emissor,
            "tp_instrumento": self.tp_instrumento,
            "nr_contrato": None,
            "cd_papel_b3": None,
            "vl_principal": round(self.vl_principal, 2),
            "dt_emissao": self.dt_operacao.isoformat(),
            "dt_vencimento": self.dt_vencimento.isoformat(),
            "dt_inicio_adm": self.dt_operacao.isoformat(),
            "dt_operacao": self.dt_operacao.isoformat(),
            "nm_adm": None,
            "indexado": self.indexado,
            "indexador": self.indexador,
            "taxa_aa": self.taxa_aa,
            "taxa_am": taxa_am,
            "taxa_ad": taxa_ad,
            "taxa_pre_ad": taxa_ad,
            "taxa_ad_efetiva": taxa_ad,
            "base_taxa": BASE_TAXA,
            "n_parcelas": self.n_parcelas,
            "vl_amortizacao_total": round(
                sum(p.vl_amortizacao for p in self.parcelas), 2),
            "parcelas": [p.to_dict() for p in self.parcelas],
            # --- procedencia, que a planilha nao tem ---
            "origem": ORIGEM,
            "beyond_operacao": self.operacao,
            "beyond_taxa_am_total": self.taxa_am_total,
            "beyond_cdi_na_data": self.cdi_na_data,
            "criterio_casamento": self.criterio,
            "observacao": self.observacao,
        }

    def to_dict(self) -> dict:
        return {
            "cnpj_fundo": self.cnpj_fundo,
            "codigo_op": self.codigo_op,
            "emissor": self.emissor,
            "tp_instrumento": self.tp_instrumento,
            "vl_principal": round(self.vl_principal, 2),
            "dt_operacao": self.dt_operacao.isoformat(),
            "dt_vencimento": self.dt_vencimento.isoformat(),
            "indexado": self.indexado,
            "indexador": self.indexador,
            "taxa_aa": self.taxa_aa,
            "n_parcelas": self.n_parcelas,
            "parcelas": [p.to_dict() for p in self.parcelas],
            "origem": ORIGEM,
            "beyond_operacao": self.operacao,
            "beyond_taxa_am": self.taxa_am_total,
            "beyond_cdi_na_data": self.cdi_na_data,
            "criterio_casamento": self.criterio,
            "observacao": self.observacao,
        }


# --- consultas ---------------------------------------------------------------

def snapshot_atual() -> dt.date | None:
    """A leitura mais recente do Beyond. E sempre ela que se consulta.

    A `TB_LIQUIDEZ_OPERACOES_4` nao e diaria: 56 das 75 datas ate 15/09/2026 sao
    quinta-feira, com 7 dias de intervalo em 46 delas, e o maior buraco
    observado foi de 15 dias. Procurar pela data da posicao devolve vazio na
    maior parte dos dias -- foi o que esvaziou a extracao de 31/08/2026.

    Nao se tenta casar snapshot com data-base, porque nao e disso que se trata:
    o Beyond descreve a operacao, nao a posicao. A leitura corrente e a melhor
    descricao disponivel, e depois de capturada o cronograma congela (ver
    CAPTURA UNICA no cabecalho) -- o que tambem protege o relatorio enquanto o
    formato de atualizacao da tabela nao estabiliza.
    """
    from . import db

    df = db.consultar("SELECT MAX(Data) AS d FROM dbo.TB_LIQUIDEZ_OPERACOES_4")
    if df.empty or df.iloc[0]["d"] is None:
        return None
    return _data(df.iloc[0]["d"])


def _operacoes_do_dia(codinome: str, data: dt.date | str, dt_operacao) -> list[dict]:
    """Operacoes CCB/NC do Beyond originadas em `dt_operacao`.

    Agrupa por titulo antes de somar: a mesma linha aparece em varias datas de
    posicao, e somar direto multiplicaria o valor pelo numero de leituras.
    """
    from . import db

    sql = (
        "SELECT Operacao, MAX(cedente) AS cedente, SUM(aplicado) AS aplicado,"
        " COUNT(*) AS titulos, MAX(indice) AS indice, MAX(taxa) AS taxa,"
        " MAX(produto) AS produto"
        " FROM (SELECT Operacao, Titulo, MAX(Valor_Aplicado) AS aplicado,"
        "              MAX(Cedente) AS cedente, MAX(Indice) AS indice,"
        "              MAX(Taxa_Final) AS taxa, MAX(Produto) AS produto"
        "       FROM dbo.TB_LIQUIDEZ_OPERACOES_4"
        "       WHERE Fundo = ? AND Data = ? AND Grupo_Produto LIKE 'CCB/NC%'"
        "         AND Data_Operacao = ?"
        "       GROUP BY Operacao, Titulo) t"
        " GROUP BY Operacao"
    )
    df = db.consultar(sql, (codinome, str(data), str(dt_operacao)))
    return df.to_dict("records")


def _titulos(codinome: str, data: dt.date | str, operacao: int) -> list[dict]:
    """Parcelas vivas da operacao, da data-base para frente.

    `Situacao_Titulo = 'A Vencer'` e o corte: vencido e pago parcial ja viraram
    ativo proprio na carteira e nao tem fluxo a projetar aqui.
    """
    from . import db

    sql = (
        "SELECT Titulo, Documento, Data_Vencimento, Valor_Aplicado"
        " FROM dbo.TB_LIQUIDEZ_OPERACOES_4"
        " WHERE Fundo = ? AND Data = ? AND Operacao = ?"
        "   AND Situacao_Titulo = 'A Vencer' AND Data_Vencimento > ?"
        " ORDER BY Data_Vencimento"
    )
    return db.consultar(sql, (codinome, str(data), int(operacao), str(data))).to_dict("records")


def _cdi_na_data(quando: dt.date | str) -> float | None:
    """CDI over da data da operacao, em fracao ao ano.

    Serve para tirar o CDI de dentro da `Taxa_Final` e devolver o spread. Sem a
    taxa do dia nao ha como decompor, e a operacao vai para a fila em vez de
    entrar com spread errado.
    """
    from . import db

    df = db.consultar(
        "SELECT TOP 1 Valor FROM dbo.TB_TAXAS WHERE Ativo = 'CDI_Over'"
        " AND Data <= ? ORDER BY Data DESC", (str(quando),))
    if df.empty:
        return None
    try:
        return float(df.iloc[0]["Valor"]) / 100
    except (TypeError, ValueError):
        return None


# --- casamento ---------------------------------------------------------------

def _assinatura(codinome: str, data: dt.date | str, operacao) -> tuple:
    """As parcelas vivas da operacao, como chave comparavel."""
    return tuple(sorted((str(_data(t["Data_Vencimento"])),
                         round(float(t["Valor_Aplicado"] or 0), 2))
                        for t in _titulos(codinome, data, operacao)))


def casar(codinome: str, data: dt.date | str, dt_aquisicao,
          custo_total: float) -> tuple[dict | None, str]:
    """Acha a operacao do Beyond que corresponde a linha da carteira.

    Devolve `(operacao, criterio)`. O criterio registra COMO casou, porque a
    confianca e diferente: `data+valor` e identificacao, `data unica` e
    inferencia aceitavel, e o resto e ambiguidade que a fila resolve.
    """
    candidatas = _operacoes_do_dia(codinome, data, dt_aquisicao)
    if not candidatas:
        return None, "sem candidata"

    exatas = [c for c in candidatas
              if abs(float(c["aplicado"] or 0) - custo_total) <= TOLERANCIA_VALOR]
    if len(exatas) == 1:
        return exatas[0], "data+valor"
    if len(exatas) > 1:
        # Gemeas -- mesmo dia, mesmo valor. Um cedente recorrente costuma emitir
        # varias NC iguais no mesmo dia (RAVIBRAS, SO LEITE), e o Beyond as
        # numera em operacoes separadas. Se todas trazem o MESMO cronograma, a
        # escolha nao muda o fluxo e nao ha chute: qualquer uma serve. So e
        # ambiguidade de verdade quando os cronogramas divergem.
        if len({_assinatura(codinome, data, c["Operacao"]) for c in exatas}) == 1:
            return exatas[0], f"gemeas identicas ({len(exatas)})"
        return None, f"ambiguo: {len(exatas)} candidatas com o mesmo valor"
    if len(candidatas) == 1:
        return candidatas[0], "data unica"
    return None, f"ambiguo: {len(candidatas)} candidatas na data, nenhuma casa em valor"


def montar(cnpj_fundo: str, codigo_op: str, codinome: str, data: dt.date | str | None,
           dt_aquisicao, custo_total: float,
           emissor: str = "") -> tuple[CronogramaBeyond | None, str]:
    """Monta o cronograma da operacao, ou explica por que nao deu.

    `data` e a leitura do Beyond a consultar. Passar None -- o caso normal --
    usa o snapshot corrente, que e sempre a melhor descricao disponivel da
    operacao (ver `snapshot_atual`). So se informa uma data para reproduzir uma
    captura antiga.

    O motivo devolvido e o que vai para a fila -- ele precisa dizer o suficiente
    para alguem decidir sem reabrir a consulta.
    """
    if data is None:
        data = snapshot_atual()
        if data is None:
            return None, "Beyond sem nenhuma leitura disponivel"

    operacao, criterio = casar(codinome, data, dt_aquisicao, custo_total)
    if operacao is None:
        return None, criterio

    titulos = _titulos(codinome, data, operacao["Operacao"])
    if not titulos:
        return None, "operacao sem parcela a vencer no Beyond"

    indice = str(operacao.get("indice") or "").strip().upper()
    indexador, indexado = INDEXADORES.get(indice, (None, False))
    if indice and indice not in INDEXADORES:
        log.warning("%s: indice desconhecido no Beyond (%r); tratado como pre",
                    codigo_op, operacao.get("indice"))

    taxa_am = float(operacao.get("taxa") or 0)
    taxa_aa_total = (1 + taxa_am) ** MESES - 1
    cdi = _cdi_na_data(dt_aquisicao) if indexado else None
    if indexado:
        if cdi is None:
            return None, "sem CDI na data da operacao para decompor a taxa"
        taxa_aa = (1 + taxa_aa_total) / (1 + cdi) - 1
    else:
        taxa_aa = taxa_aa_total

    parcelas = [Parcela(dt_vencimento=_data(t["Data_Vencimento"]),
                        vl_amortizacao=float(t["Valor_Aplicado"] or 0))
                for t in titulos]

    nota = (f"Cronograma deduzido da operacao {operacao['Operacao']} do Beyond "
            f"({criterio}). Premissa, nao leitura: a planilha do fundo sobrescreve "
            f"quando chegar. Taxa do Beyond {taxa_am:.6f} a.m. "
            f"({taxa_aa_total * 100:.4f}% a.a. total)")
    if indexado:
        nota += f"; CDI de {cdi * 100:.4f}% na data deixa spread de {taxa_aa * 100:.4f}% a.a."
    nota += (f". {len(parcelas)} parcela(s) a vencer; parcela vencida fica de fora "
             f"porque a carteira ja a baixou para ativo proprio.")

    crono = CronogramaBeyond(
        cnpj_fundo=cnpj_fundo,
        codigo_op=codigo_op,
        operacao=int(operacao["Operacao"]),
        emissor=str(operacao.get("cedente") or emissor or "").strip(),
        tp_instrumento=str(operacao.get("produto") or "NC").strip() or "NC",
        vl_principal=float(operacao["aplicado"] or 0),
        dt_operacao=_data(dt_aquisicao),
        dt_vencimento=parcelas[-1].dt_vencimento,
        indexado=indexado,
        indexador=indexador,
        taxa_aa=taxa_aa,
        taxa_am_total=taxa_am,
        cdi_na_data=cdi,
        n_parcelas=len(parcelas),
        parcelas=parcelas,
        criterio=criterio,
        observacao=nota,
    )
    return crono, criterio


# --- operacoes novas ---------------------------------------------------------

def novas_na_carteira(cnpj_fundo: str, data: dt.date | str,
                      data_anterior: dt.date | str) -> list[dict]:
    """Linhas de NC/CCB que aparecem em `data` e nao existiam em `data_anterior`.

    `Custo_Total > 0` exclui a linha zerada que o administrador deixa para tras
    quando move a operacao para um ativo vencido.
    """
    from . import db

    sql = (
        "SELECT cdOp AS Codigo_Op, cdAtivo AS Codigo_Ativo, dsEmissor AS Emissor,"
        " dtAquisicao AS Data_Aquisicao, dtVencimento AS Data_Vencimento,"
        " vlCustoTotal AS Custo_Total, vlLiquido AS Valor_Liquido"
        " FROM dbo.MUL_GLD_CARTEIRA_UNIFICADA"
        " WHERE docFundo = ? AND dtReferencia = ? AND vlCustoTotal > 0"
        "   AND (cdAtivo LIKE 'NCPX%' OR cdAtivo LIKE 'CCBPX%')"
        "   AND cdOp NOT IN ("
        "       SELECT cdOp FROM dbo.MUL_GLD_CARTEIRA_UNIFICADA"
        "       WHERE docFundo = ? AND dtReferencia = ?)"
        " ORDER BY vlCustoTotal DESC"
    )
    return db.consultar(sql, (cnpj_fundo, str(data), cnpj_fundo, str(data_anterior))).to_dict("records")


# --- persistencia ------------------------------------------------------------

def _taxas_derivadas(taxa_aa: float | None) -> tuple[float | None, float | None]:
    """Mensal e diaria a partir da anual, na convencao da planilha."""
    if taxa_aa is None:
        return None, None
    taxa_am = (1 + taxa_aa) ** (1 / MESES_ANO) - 1
    taxa_ad = (1 + taxa_aa) ** (1 / BASE_TAXA) - 1
    return taxa_am, taxa_ad


def caminho_do_fundo(cnpj_fundo: str, diretorio: Path = DIR_BEYOND) -> Path:
    """Um arquivo por fundo, nomeado pelo CNPJ -- igual ao da planilha."""
    return Path(diretorio) / (re.sub(r"\D", "", str(cnpj_fundo)) + ".json")


def carregar(cnpj_fundo: str, diretorio: Path = DIR_BEYOND) -> dict:
    """O que ja foi capturado para o fundo. Ausente e o mesmo que vazio."""
    caminho = caminho_do_fundo(cnpj_fundo, diretorio)
    if not caminho.exists():
        return {}
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Cronograma do Beyond %s ignorado: %s", caminho.name, exc)
        return {}
    return {o["codigo_op"]: o for o in dados.get("operacoes", []) if o.get("codigo_op")}


def gravar(cnpj_fundo: str, cronogramas: list[CronogramaBeyond],
           snapshot: dt.date | str | None = None,
           diretorio: Path = DIR_BEYOND) -> Path:
    """Acrescenta as capturas ao arquivo do fundo, sem tocar no que ja existe.

    CAPTURA UNICA: operacao ja gravada NAO e reescrita, mesmo que o Beyond tenha
    mudado. Recapturar e acao manual -- apagar a operacao do arquivo e rodar de
    novo. Sem isso, uma correcao no Beyond entraria sozinha no relatorio e o
    numero de uma data passada mudaria entre duas rodadas.
    """
    caminho = caminho_do_fundo(cnpj_fundo, diretorio)
    caminho.parent.mkdir(parents=True, exist_ok=True)

    existentes = carregar(cnpj_fundo, diretorio)
    novas = [c for c in cronogramas if c.codigo_op not in existentes]
    if not novas and existentes:
        log.info("%s: nada novo a capturar (%d operacao(oes) ja gravadas).",
                 cnpj_fundo, len(existentes))

    operacoes = list(existentes.values()) + [c.to_operacao() for c in novas]
    operacoes.sort(key=lambda o: str(o.get("codigo_op")))

    dados = {
        "cnpj_fundo": cnpj_fundo,
        "capturado_em": dt.datetime.now().replace(microsecond=0).isoformat(),
        "beyond": {
            "tabela": "TB_LIQUIDEZ_OPERACOES_4",
            "snapshot": str(snapshot) if snapshot else None,
            "origem": ORIGEM,
        },
        "total_operacoes": len(operacoes),
        "total_parcelas": sum(len(o.get("parcelas") or []) for o in operacoes),
        "operacoes": operacoes,
    }
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    log.info("%s: %d nova(s), %d no total -> %s",
             cnpj_fundo, len(novas), len(operacoes), caminho)
    return caminho
