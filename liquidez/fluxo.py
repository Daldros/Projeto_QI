"""Projecao do fluxo futuro dos ativos -- insumo da etapa 5 (`CalcDiasFluxos`).

O valor de partida e o `Valor_Liquido` da carteira na data-base. Na carteira o
ativo ja vem precificado pelo acrual, entao esse valor E o saldo devedor
efetivo, com os juros ja incorridos dentro. Nao ha nada do passado a
reconstruir: o passado esta no numero da carteira.

Dali para frente projeta-se com a taxa da operacao e a curva futura do CDI, nas
datas de vencimento do cronograma contratual (`cronograma.py`):

    dias[i]     = (vencimento[i] - data_base) em dias corridos
    fator[i]    = Fator_Spot(dias[i]) * (1 + taxa_ad) ** dias[i]     (CDI + pre)
                = (1 + taxa_ad) ** dias[i]                           (so pre)
    juros[i]    = saldo * (fator[i] / fator[i-1] - 1)
    amort[i]    = valor_base * peso[i]
    parcela[i]  = juros[i] + amort[i]
    saldo      -= amort[i]

O peso de cada parcela vem da amortizacao contratual futura, normalizada. Assim
a soma das amortizacoes projetadas fecha exatamente com o valor da carteira --
nao se cria nem se destroi principal --, e o saldo termina em zero. Os juros
entram por cima: a soma das parcelas supera o valor da carteira, porque cada
parcela carrega o juro que ainda vai correr ate ela.

Parcela vencida nao entra: isto e fluxo futuro a receber. A parcela que venceu
ja virou ativo proprio na carteira (`VC*`), com valor proprio, e e contada la.
A parcela que vence na propria data-base ENTRA -- dinheiro que chega no dia e a
liquidez maxima, e ela ainda nao virou ativo vencido. Com `qt_dias = 0` o fator
e 1, entao ela contribui so com amortizacao, sem juros.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

MERCADORIA_PADRAO = "DI1"

# Base de contagem da taxa pre. 360 e a convencao das NC/CCB que vem da
# planilha; 252 (dias uteis) e usada por papeis como CRA.
BASE_PADRAO = 360

ORIGEM_CRONOGRAMA = "cronograma"
# Bullet porque a familia do papel e bullet por construcao (ver FAMILIAS_BULLET).
# Nao passa por conferencia: o tipo do instrumento ja responde.
ORIGEM_BULLET_REGRA = "bullet_regra"
ORIGEM_BULLET_CONFIRMADO = "bullet_confirmado"
ORIGEM_BULLET_PRESUMIDO = "bullet_presumido"
ORIGEM_ESTOQUE = "estoque"  # distribuido pelos recebiveis a vencer do proprio fundo
ORIGEM_BULLET_PLANILHA_PENDENTE = "bullet_planilha_pendente"

# O que `projetar_operacao` e `projetar_bullet` produzem: uma parcela por linha.
COLUNAS_PARCELA = [
    "cnpj_fundo", "codigo_op", "nr_parcela", "dt_vencimento", "qt_dias",
    # `vl_acrual` e o juro que JA estava corrido dentro do Valor_Liquido na
    # data-base e que sai no primeiro pagamento. Fica em coluna propria, e nao
    # somado aos juros, porque nao e projecao: e passado que a carteira ja
    # media. Zero quando a decomposicao esta desligada (ver `acrual.py`).
    "saldo_anterior", "vl_juros", "vl_acrual", "vl_amortizacao", "vl_parcela",
    "tp_indice",
    # De onde veio a linha. Fluxo presumido nao pode se passar por cadastrado --
    # quem le precisa saber o que foi lido da planilha e o que foi deduzido.
    "origem",
]

# A operacao esta marcada `Ativo Vencido = SIM` na carteira tratada? Vem da mesma
# propagacao por EMISSOR que a matriz usa para mandar a linha para "Vencido",
# fora das faixas de prazo.
#
# Fica fora de `COLUNAS_PARCELA` porque e atributo da OPERACAO, e os dois
# construtores de parcela nao tem como saber dele -- `projetar` carimba depois,
# de `posicoes_do_produto`.
#
# Sem esta coluna quem consome o fluxo nao consegue reproduzir aquele corte, e
# emissor inadimplente entra como caixa. Medido em 29/09/2026: R$ 101,3 mi de
# parcelas de emissor vencido caindo dentro da janela de resgate dos 17 fundos
# com fluxo -- mais que o dobro do efeito que a mudanca pretendia medir.
COL_VENCIDO_FLUXO = "ativo_vencido"

COLUNAS_FLUXO = [*COLUNAS_PARCELA, COL_VENCIDO_FLUXO]


class ErroProjecao(RuntimeError):
    pass


# --- Curva ------------------------------------------------------------------

# Uma curva por (data, mercadoria) durante o processo. Ver `Curva.carregar`.
_CACHE_CURVA: dict[tuple[dt.date, str], "Curva"] = {}


def limpar_cache_curva() -> None:
    """Esquece as curvas ja lidas. Util em teste e em processo de vida longa."""
    _CACHE_CURVA.clear()


@dataclass
class Curva:
    """Vertices da curva numa data-base. `fator(ndc)` da o fator acumulado."""

    data_base: dt.date
    mercadoria: str
    ndc: list[int] = field(default_factory=list)
    fator_spot: list[float] = field(default_factory=list)
    ndu: list[int] = field(default_factory=list)

    @classmethod
    def carregar(
        cls,
        data_base: dt.date | str,
        mercadoria: str = MERCADORIA_PADRAO,
        usar_cache: bool = True,
    ) -> "Curva":
        """A curva de uma data, do banco. O resultado fica em cache no processo.

        Sem o cache, `projetar` consulta a curva uma vez por fundo. Enquanto
        eram 3 fundos isso passava despercebido; com os 35 do escopo bullet
        viraram 35 idas ao banco por data -- e cada ida e uma chance de a rede
        cair no meio. A curva de uma data nao muda durante a rodada.
        """
        from . import db, queries

        if isinstance(data_base, str):
            data_base = dt.date.fromisoformat(data_base)
        chave = (data_base, mercadoria)
        if usar_cache and chave in _CACHE_CURVA:
            return _CACHE_CURVA[chave]

        sql, params = queries.curva_juros(data_base, mercadoria)
        try:
            df = db.consultar(sql, params)
        except Exception as exc:  # pyodbc.Error e afins
            raise ErroProjecao(
                f"Nao foi possivel consultar a curva em {db.SERVIDOR}: {exc}. "
                f"Confira a conexao com a rede do banco."
            ) from exc
        if df.empty:
            raise ErroProjecao(
                f"Sem curva {mercadoria} para {data_base:%d/%m/%Y} em TB_CURVA_JUROS_DIARIA. "
                f"Feriado ou curva ainda nao carregada."
            )
        df = df.sort_values("NDC")
        log.info("Curva %s de %s: %d vertices", mercadoria, data_base, len(df))
        curva = cls(
            data_base=data_base,
            mercadoria=mercadoria,
            ndc=[int(v) for v in df["NDC"]],
            fator_spot=[float(v) for v in df["Fator_Spot"]],
            ndu=[int(v) if pd.notna(v) else 0 for v in df["NDU"]],
        )
        _CACHE_CURVA[chave] = curva
        return curva

    def _vertice(self, ndc: int) -> int:
        import bisect

        posicao = bisect.bisect_right(self.ndc, ndc) - 1
        if posicao < 0:
            raise ErroProjecao(f"Curva sem vertice com NDC <= {ndc}.")
        return posicao

    def fator(self, ndc: int) -> float:
        """Fator acumulado do maior vertice <= ndc. 1.0 para ndc <= 0."""
        return 1.0 if ndc <= 0 else self.fator_spot[self._vertice(ndc)]

    def dias_uteis(self, ndc: int) -> int:
        """Dias uteis ate o vertice, para compor taxa de base 252.

        A curva traz NDU e NDC lado a lado, entao a contagem de dias uteis nao
        precisa de calendario de feriados proprio.
        """
        if ndc <= 0:
            return 0
        if not self.ndu:
            raise ErroProjecao("Curva sem NDU: nao da para compor taxa base 252.")
        return self.ndu[self._vertice(ndc)]

    @property
    def ndc_maximo(self) -> int:
        return self.ndc[-1] if self.ndc else 0


# --- Projecao de uma operacao ----------------------------------------------

def _base_taxa(operacao) -> int:
    """Base da taxa da operacao; 360 quando ausente.

    `NaN or 360` devolveria NaN -- NaN e truthy em Python --, e as operacoes
    lidas da planilha nao trazem esse campo.
    """
    valor = operacao.get("base_taxa")
    if valor is None or pd.isna(valor):
        return BASE_PADRAO
    return int(valor)


def _pesos(amortizacoes: list[float]) -> list[float]:
    """Peso de cada parcela na amortizacao do principal.

    Quando o cronograma futuro nao tem amortizacao (so juros ate um bullet fora
    do horizonte lido), o principal inteiro vai para a ultima data -- e o que
    sobra de sensato sem inventar cronograma.
    """
    total = sum(amortizacoes)
    if total > 0:
        return [valor / total for valor in amortizacoes]
    pesos = [0.0] * len(amortizacoes)
    if pesos:
        pesos[-1] = 1.0
    return pesos


def projetar_operacao(
    codigo_op: str,
    valor_base: float,
    data_base: dt.date,
    parcelas: pd.DataFrame,
    taxa_ad: float,
    indexado: bool,
    curva: Curva | None,
    indexador: str = "CDI",
    cnpj_fundo: str = "",
    base_taxa: int = BASE_PADRAO,
    acrual_inicial: float = 0.0,
) -> pd.DataFrame:
    """Fluxo futuro de uma operacao. `parcelas` ja deve vir so com as futuras.

    `base_taxa` diz sobre que contagem a taxa pre compoe: 360 (dias corridos,
    convencao das NC/CCB que vem da planilha) ou 252 (dias uteis, usada por
    papeis como CRA). No caso 252 a contagem vem do NDU da curva.
    """
    if parcelas.empty:
        return pd.DataFrame(columns=COLUNAS_PARCELA)

    parcelas = parcelas.sort_values("dt_vencimento")
    datas = [
        d.date() if isinstance(d, pd.Timestamp) else pd.to_datetime(d).date()
        for d in parcelas["dt_vencimento"]
    ]
    amortizacoes = [float(v or 0.0) for v in parcelas["vl_amortizacao"]]
    numeros = [int(n) for n in parcelas["nr_parcela"]]
    pesos = _pesos(amortizacoes)
    taxa = float(taxa_ad or 0.0)
    tp_indice = f"100% {indexador}" if indexado else "PRE"

    if indexado and curva is None:
        raise ErroProjecao(f"{codigo_op}: operacao indexada exige a curva.")
    if int(base_taxa) == 252 and curva is None:
        raise ErroProjecao(f"{codigo_op}: taxa base 252 exige a curva, para o NDU.")

    linhas = []
    saldo = float(valor_base)
    fator_anterior = 1.0
    acrual = float(acrual_inicial or 0.0)

    for numero, data, peso in zip(numeros, datas, pesos):
        dias = (data - data_base).days
        fator_cdi = curva.fator(dias) if indexado else 1.0
        # A taxa pre compoe sobre dias uteis ou corridos, conforme a base.
        expoente = curva.dias_uteis(dias) if int(base_taxa) == 252 else dias
        fator = fator_cdi * (1 + taxa) ** expoente

        juros = saldo * (fator / fator_anterior - 1) if fator_anterior else 0.0
        amortizacao = float(valor_base) * peso
        linhas.append({
            "cnpj_fundo": cnpj_fundo,
            "codigo_op": codigo_op,
            "nr_parcela": numero,
            "dt_vencimento": data,
            "qt_dias": dias,
            "saldo_anterior": saldo,
            "vl_juros": juros,
            # O acrual sai inteiro na PRIMEIRA parcela futura -- e o proximo
            # pagamento, e e nele que o juro ja corrido e liquidado.
            "vl_acrual": acrual if not linhas else 0.0,
            "vl_amortizacao": amortizacao,
            "vl_parcela": juros + amortizacao + (acrual if not linhas else 0.0),
            "tp_indice": tp_indice,
            "origem": ORIGEM_CRONOGRAMA,
        })
        saldo -= amortizacao
        fator_anterior = fator

    return pd.DataFrame(linhas)[COLUNAS_PARCELA]


def projetar_bullet(
    codigo_op: str,
    valor_base: float,
    data_base: dt.date,
    dt_vencimento: dt.date,
    cnpj_fundo: str = "",
    origem: str = ORIGEM_BULLET_PRESUMIDO,
) -> pd.DataFrame:
    """Fluxo de uma operacao bullet: uma parcela, tudo no vencimento.

    Sem correcao ate o vencimento, por decisao: a parcela e o proprio
    `Valor_Liquido` da data-base. Duas razoes.

    A primeira e que a diferenca e pequena no prazo que importa -- as bullets
    que pesam na liquidez vencem em dias ou semanas, e o accrual desse trecho e
    ruido. As de prazo longo, onde a diferenca seria grande, estao alem da
    janela de resgate e tem o Valor Liquido zerado de qualquer forma.

    A segunda e que assim a soma das amortizacoes bate exatamente com o valor da
    carteira, como ja acontece no fluxo vindo do cronograma. Corrigir faria o
    total do fluxo passar da posicao e a conferencia perderia a ancora.

    `qt_dias` sai em dias CORRIDOS: e o que aloca a parcela na faixa de prazo e
    o que se compara ao prazo de cotizacao, contado em calendario.
    """
    dias = (dt_vencimento - data_base).days
    if dias < 0:
        return pd.DataFrame(columns=COLUNAS_PARCELA)

    # Sem cronograma nao ha data de cupom anterior, entao nao ha como separar o
    # acrual: a premissa bullet segue com o Valor_Liquido inteiro como principal.
    valor = float(valor_base)
    linha = {
        "cnpj_fundo": cnpj_fundo,
        "codigo_op": codigo_op,
        "nr_parcela": 1,
        "dt_vencimento": dt_vencimento,
        "qt_dias": dias,
        "saldo_anterior": valor,
        "vl_juros": 0.0,
        "vl_acrual": 0.0,
        "vl_amortizacao": valor,
        "vl_parcela": valor,
        "tp_indice": "BULLET",
        "origem": origem,
    }
    return pd.DataFrame([linha])[COLUNAS_PARCELA]


def pesos_do_estoque(bd: pd.DataFrame, data_base: dt.date,
                     coluna_prazo: str = "Prazo_Atual_Dias_Corridos",
                     coluna_valor: str = "Valor_Presente",
                     coluna_status: str = "Status_Recebivel") -> pd.DataFrame:
    """Vencimentos dos recebiveis A VENCER do fundo, com o peso de cada data.

    Uma linha por data de vencimento (data_base + prazo em dias corridos), com
    `peso` = valor presente da data / valor presente total. E o perfil de
    amortizacao do pool, usado para distribuir o valor da carteira das familias
    agregadas (MEZAN/SRP). Vencido e prazo negativo ficam de fora: o que ja
    venceu nao e caixa futuro. Devolve vazio sem BD ou sem coluna.
    """
    if bd is None or bd.empty or coluna_prazo not in bd or coluna_valor not in bd:
        return pd.DataFrame(columns=["dt_vencimento", "qt_dias", "peso", "valor_presente"])
    df = bd
    if coluna_status in df:
        df = df[df[coluna_status].fillna("").astype(str).str.strip().str.upper() == "A VENCER"]
    prazo = pd.to_numeric(df[coluna_prazo], errors="coerce")
    valor = pd.to_numeric(df[coluna_valor], errors="coerce").fillna(0.0)
    ok = prazo.notna() & (prazo >= 0) & (valor > 0)
    if not ok.any():
        return pd.DataFrame(columns=["dt_vencimento", "qt_dias", "peso", "valor_presente"])
    agr = valor[ok].groupby(prazo[ok].astype(int)).sum().sort_index()
    total = float(agr.sum())
    return pd.DataFrame({
        "dt_vencimento": [data_base + dt.timedelta(days=int(d)) for d in agr.index],
        "qt_dias": [int(d) for d in agr.index],
        "peso": (agr / total).values,
        "valor_presente": agr.values,
    })


def projetar_estoque(codigo_op: str, valor_base: float, pesos: pd.DataFrame,
                     cnpj_fundo: str = "", origem: str = ORIGEM_ESTOQUE) -> pd.DataFrame:
    """Fluxo de uma operacao agregada: o valor da carteira repartido pelo perfil
    do estoque. Sem juros (o valor presente ja e o marcado) -- a soma das
    amortizacoes bate com a carteira, como nas demais pernas."""
    if pesos is None or pesos.empty:
        return pd.DataFrame(columns=COLUNAS_PARCELA)
    valor = float(valor_base)
    linhas = []
    saldo = valor
    for n, (_, p) in enumerate(pesos.iterrows(), start=1):
        amort = valor * float(p["peso"])
        linhas.append({
            "cnpj_fundo": cnpj_fundo, "codigo_op": codigo_op, "nr_parcela": n,
            "dt_vencimento": p["dt_vencimento"], "qt_dias": int(p["qt_dias"]),
            "saldo_anterior": saldo, "vl_juros": 0.0, "vl_acrual": 0.0,
            "vl_amortizacao": amort, "vl_parcela": amort, "tp_indice": "ESTOQUE", "origem": origem,
        })
        saldo -= amort
    return pd.DataFrame(linhas)[COLUNAS_PARCELA]


# --- Projecao do fundo ------------------------------------------------------

@dataclass
class ResultadoProjecao:
    fluxo: pd.DataFrame
    resumo: pd.DataFrame
    sem_cronograma: list[tuple[str, float]]
    sem_parcela_futura: list[tuple[str, float]]
    alem_da_curva: list[tuple[str, int]]
    # Projetadas pela premissa bullet, com o estado de conferencia de cada uma.
    # Dicts com codigo_op, valor, dt_vencimento, qt_dias, estado e origem.
    bullets: list[dict] = field(default_factory=list)
    # Operacoes agregadas (MEZAN/SRP) projetadas pelo estoque do fundo:
    # dicts com codigo_op, valor, n_parcelas.
    pelo_estoque: list[dict] = field(default_factory=list)
    # Operacoes que nem a premissa alcanca: sem data de vencimento, ou com mais
    # de uma data -- ai nao e bullet e nao ha o que presumir.
    bullet_impossivel: list[dict] = field(default_factory=list)
    # Operacao com cronograma cuja data de vencimento discorda da carteira. Nao
    # muda a projecao -- o cadastro segue mandando --, mas nao pode sumir: ou a
    # leitura do documento errou, ou houve aditamento, ou o administrador esta
    # com a data errada. Qualquer das tres precisa de alguem olhando.
    vencimento_divergente: list[dict] = field(default_factory=list)
    # `Codigo_Ativo` das operacoes que ficaram sem cronograma, por codigo. E o
    # que revela familia que deveria estar em `bullets.FAMILIAS_PRESUMIDAS` e
    # nao esta -- sem isso, um papel inteiro sumiria do fluxo em silencio.
    familia_sem_cronograma: dict[str, str] = field(default_factory=dict)
    # Preenchido so com `decompor_acrual=True`: uma `acrual.Decomposicao` por
    # operacao separada. Vazio significa que a projecao rodou no modo antigo.
    decomposicoes: list = field(default_factory=list)

    @property
    def total_acrual(self) -> float:
        return sum(d.acrual for d in self.decomposicoes)

    @property
    def conciliacao(self) -> dict:
        """Os tres numeros que fecham a conferencia quando ha decomposicao.

        Sem decomposicao a soma das amortizacoes fecha sozinha com a carteira.
        Com ela, o acrual sai das amortizacoes e vira linha propria -- entao a
        amarracao passa a ser `principal + acrual = posicao`.
        """
        if self.fluxo.empty:
            return {}
        principal = float(self.fluxo["vl_amortizacao"].sum())
        acr = float(self.fluxo["vl_acrual"].sum())
        return {
            "principal_projetado": principal,
            "acrual_a_receber": acr,
            "posicao_da_carteira": principal + acr,
        }

    @property
    def total_projetado(self) -> float:
        if self.fluxo.empty:
            return 0.0
        return float(self.fluxo["vl_parcela"].sum())


def _data_simples(valor) -> dt.date | None:
    """Converte para `date` o que vier (Timestamp, date, texto); None se nao der."""
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return pd.Timestamp(valor).date()
    except (ValueError, TypeError):
        return None


def _e_ajuste_de_dia_util(da_carteira: dt.date, do_cadastro: dt.date) -> bool:
    """O cadastro e a carteira rolada para o proximo dia util?"""
    if do_cadastro <= da_carteira:
        return False
    try:
        from .calendario import proximo_dia_util
        return proximo_dia_util(da_carteira) == do_cadastro
    except Exception:  # sem calendario de feriados: so fim de semana
        data = da_carteira
        while data.weekday() >= 5:
            data += dt.timedelta(days=1)
        return data == do_cadastro


def _divergencia_de_vencimento(operacao, posicao) -> dict | None:
    """Compara o vencimento final do cadastro com o da carteira.

    Devolve None quando batem ou quando falta uma das datas -- ausencia nao e
    divergencia, e cobrar dado que nunca existiu so gera ruido.
    """
    do_cadastro = _data_simples(operacao.get("dt_vencimento"))
    da_carteira = _data_simples(posicao.get("dt_vencimento"))
    if do_cadastro is None or da_carteira is None or do_cadastro == da_carteira:
        return None
    # O Beyond grava o vencimento ja rolado para o proximo dia util (carnaval,
    # Finados, fim de semana); a carteira do administrador guarda a data do
    # documento. Em 30/09/2026 as 13 "divergencias" do MULTIPLICA FIDC eram
    # todas isso (-1/-2 dias). Para o caixa, vale o dia util -- nao e divergencia.
    if _e_ajuste_de_dia_util(da_carteira, do_cadastro):
        return None
    return {
        "dt_cadastro": do_cadastro,
        "dt_carteira": da_carteira,
        "dias": (da_carteira - do_cadastro).days,
    }


def projetar(
    posicoes: pd.DataFrame,
    cronograma,
    data_base: dt.date,
    curva: Curva | None = None,
    cnpj_fundo: str = "",
    mercadoria: str = MERCADORIA_PADRAO,
    bullet: bool = False,
    estados_bullet: dict[str, str] | None = None,
    decompor_acrual: bool = False,
    pesos_estoque: pd.DataFrame | None = None,
) -> ResultadoProjecao:
    """Projeta o fluxo de varias operacoes.

    `posicoes` precisa de `codigo_op` e `valor_base` -- uma linha por operacao,
    ja agregada. `cronograma` e um `cronograma.Cronograma`.

    Com `bullet=True`, operacao sem cronograma nao e descartada: e projetada
    como bullet a partir da carteira. O cronograma sempre ganha -- a premissa e
    o piso, e some sozinha quando a planilha chega. `estados_bullet` traz o
    estado de conferencia por codigo (ver `bullets.py`); sem ele, tudo conta
    como nao conferido.
    """
    from . import acrual as ac
    from . import bullets as bl

    if curva is None:
        curva = Curva.carregar(data_base, mercadoria)
    estados_bullet = estados_bullet or {}

    # Uma unica ida ao banco pelo CDI realizado, cobrindo o periodo de acrual
    # mais antigo de toda a carteira. Consultar por operacao seria uma consulta
    # por papel -- centenas por fundo.
    serie_cdi: dict = {}
    if decompor_acrual:
        mais_antigo = data_base
        for _, posicao in posicoes.iterrows():
            operacao = cronograma.operacao(str(posicao["codigo_op"]))
            if operacao is None:
                continue
            inicio, _ = ac.ultimo_evento(
                cronograma.parcelas_de(str(posicao["codigo_op"])), data_base, operacao)
            if inicio is not None:
                mais_antigo = min(mais_antigo, inicio)
        if mais_antigo < data_base:
            serie_cdi = ac.serie_cdi(mais_antigo, data_base)

    origem_por_estado = {
        bl.CONFIRMADO: ORIGEM_BULLET_CONFIRMADO,
        bl.PLANILHA_PENDENTE: ORIGEM_BULLET_PLANILHA_PENDENTE,
    }

    fluxos: list[pd.DataFrame] = []
    resumos: list[dict] = []
    sem_cronograma: list[tuple[str, float]] = []
    sem_parcela_futura: list[tuple[str, float]] = []
    alem_da_curva: list[tuple[str, int]] = []
    bullets: list[dict] = []
    bullet_impossivel: list[dict] = []
    vencimento_divergente: list[dict] = []
    familia_sem_cronograma: dict[str, str] = {}
    decomposicoes: list = []
    pelo_estoque: list[dict] = []
    tem_estoque = pesos_estoque is not None and not pesos_estoque.empty

    for _, posicao in posicoes.iterrows():
        codigo = str(posicao["codigo_op"])
        valor = float(posicao["valor_base"] or 0.0)
        codigo_ativo = posicao.get("codigo_ativo")

        operacao = cronograma.operacao(codigo)
        if operacao is None and tem_estoque and valor != 0 and bl.e_familia_estoque(codigo_ativo):
            # Linha agregada do pool (MEZAN/SRP): o fluxo e o perfil de
            # vencimento dos recebiveis a vencer do proprio fundo. Ver
            # `bullets.FAMILIAS_ESTOQUE`.
            #
            # A perna NEGATIVA vai pelo mesmo perfil. MEZAN/SRP vem em pares de
            # contrapartida (no MULTIPLICA FIDC em 30/09/2026, R$ 1,75 bi
            # positivos contra R$ 1,57 bi negativos): com `valor > 0` so a
            # positiva era repartida pelo estoque, e caia na janela, enquanto
            # a negativa ficava bullet no vencimento, fora dela. O fluxo de DC
            # em 29 dias saia em R$ 624 mi contra R$ 38,8 mi da matriz.
            # Mesmo perfil nas duas pernas: o par se anula data a data, e o
            # que sobra e o liquido do pool (rodada 19, 21/10/2026).
            fluxo_est = projetar_estoque(codigo, valor, pesos_estoque, cnpj_fundo)
            fluxos.append(fluxo_est)
            pelo_estoque.append({"codigo_op": codigo, "valor": valor, "n_parcelas": len(fluxo_est),
                                 "codigo_ativo": str(codigo_ativo or "")})
            resumos.append({
                "cnpj_fundo": cnpj_fundo, "codigo_op": codigo, "valor_base": valor,
                "n_parcelas": len(fluxo_est),
                "primeiro_vencimento": fluxo_est["dt_vencimento"].min(),
                "ultimo_vencimento": fluxo_est["dt_vencimento"].max(),
                "vl_juros": 0.0, "vl_amortizacao": valor, "vl_parcela": valor,
                "tp_indice": "ESTOQUE",
            })
            continue
        if operacao is None:
            # A premissa vale por escopo do administrador (`bullet=True`) ou
            # por familia que entra sozinha, como o CRA -- ver
            # `bullets.FAMILIAS_PRESUMIDAS`.
            presumida = bl.e_familia_presumida(codigo_ativo)
            if not bullet and not presumida:
                sem_cronograma.append((codigo, valor))
                familia_sem_cronograma[codigo] = str(codigo_ativo or "")
                continue

            vencimento = posicao.get("dt_vencimento")
            n_datas = int(posicao.get("n_vencimentos") or 0)
            if pd.isna(vencimento) or n_datas != 1:
                # Sem data unica de vencimento nao ha bullet a presumir. Volta
                # a ser omissao -- consciente, e reportada.
                motivo = ("sem data de vencimento" if pd.isna(vencimento)
                          else f"{n_datas} datas de vencimento")
                bullet_impossivel.append(
                    {"codigo_op": codigo, "valor": valor, "motivo": motivo,
                     "codigo_ativo": str(codigo_ativo or "")}
                )
                sem_cronograma.append((codigo, valor))
                familia_sem_cronograma[codigo] = str(codigo_ativo or "")
                continue

            data_venc = pd.Timestamp(vencimento).date()
            # A regra da familia vem antes do estado: se o papel e bullet por
            # construcao, nao ha conferencia a fazer nem fila em que entrar.
            por_regra = bl.e_familia_bullet(posicao.get("codigo_ativo"))
            if por_regra:
                estado, origem = bl.POR_REGRA, ORIGEM_BULLET_REGRA
            else:
                estado = estados_bullet.get(codigo, bl.PENDENTE)
                origem = origem_por_estado.get(estado, ORIGEM_BULLET_PRESUMIDO)
            fluxo_bullet = projetar_bullet(
                codigo_op=codigo, valor_base=valor, data_base=data_base,
                dt_vencimento=data_venc, cnpj_fundo=cnpj_fundo, origem=origem,
            )
            if fluxo_bullet.empty:
                # Vencimento no passado: nao e fluxo futuro.
                sem_parcela_futura.append((codigo, valor))
                continue

            bullets.append({
                "codigo_op": codigo, "valor": valor, "dt_vencimento": data_venc,
                "qt_dias": int(fluxo_bullet["qt_dias"].iloc[0]),
                "estado": estado, "origem": origem,
                "codigo_ativo": str(codigo_ativo or ""),
                # Entrou por familia, nao pelo escopo do administrador.
                "presumida_por_familia": presumida and not bullet,
            })
            fluxos.append(fluxo_bullet)
            resumos.append({
                "cnpj_fundo": cnpj_fundo, "codigo_op": codigo, "valor_base": valor,
                "n_parcelas": 1,
                "primeiro_vencimento": data_venc, "ultimo_vencimento": data_venc,
                "vl_juros": 0.0, "vl_amortizacao": valor, "vl_parcela": valor,
                "tp_indice": "BULLET",
            })
            continue

        # O cadastro manda na projecao, mas a carteira e a fonte do
        # administrador: divergencia entre as duas nao pode passar calada.
        divergencia = _divergencia_de_vencimento(operacao, posicao)
        if divergencia:
            vencimento_divergente.append({"codigo_op": codigo, "valor": valor,
                                          **divergencia})

        futuras = cronograma.parcelas_de(codigo, desde=data_base)
        if futuras.empty:
            sem_parcela_futura.append((codigo, valor))
            continue

        # O acrual sai do principal ANTES da projecao: ele nao e principal e
        # nao pode ser rateado pelos pesos de amortizacao -- vai inteiro no
        # proximo pagamento. Ver `acrual.py`.
        valor_principal, acrual_inicial = valor, 0.0
        if decompor_acrual:
            d = ac.decompor(codigo, valor, data_base, operacao,
                            cronograma.parcelas_de(codigo), serie_cdi)
            if d is not None:
                valor_principal, acrual_inicial = d.principal, d.acrual
                decomposicoes.append(d)
                if not d.confiavel:
                    log.warning(
                        "%s: %d dia(s) util(eis) sem CDI -- acrual subestimado.",
                        codigo, len(d.dias_sem_cdi))

        indexado = bool(operacao["indexado"])
        fluxo = projetar_operacao(
            codigo_op=codigo,
            valor_base=valor_principal,
            data_base=data_base,
            parcelas=futuras,
            taxa_ad=operacao["taxa_ad_efetiva"],
            indexado=indexado,
            curva=curva,
            indexador=str(operacao["indexador"] or "CDI"),
            cnpj_fundo=cnpj_fundo,
            base_taxa=_base_taxa(operacao),
            acrual_inicial=acrual_inicial,
        )
        if fluxo.empty:
            continue

        prazo_maximo = int(fluxo["qt_dias"].max())
        if indexado and prazo_maximo > curva.ndc_maximo:
            alem_da_curva.append((codigo, prazo_maximo))

        fluxos.append(fluxo)
        resumos.append({
            "cnpj_fundo": cnpj_fundo,
            "codigo_op": codigo,
            "valor_base": valor,
            "n_parcelas": len(fluxo),
            "primeiro_vencimento": fluxo["dt_vencimento"].min(),
            "ultimo_vencimento": fluxo["dt_vencimento"].max(),
            "vl_juros": float(fluxo["vl_juros"].sum()),
            "vl_amortizacao": float(fluxo["vl_amortizacao"].sum()),
            "vl_parcela": float(fluxo["vl_parcela"].sum()),
            "tp_indice": fluxo["tp_indice"].iloc[0],
        })

    fluxo = (
        pd.concat(fluxos, ignore_index=True) if fluxos
        else pd.DataFrame(columns=COLUNAS_FLUXO)
    )
    if not fluxo.empty:
        # O flag de vencido e da OPERACAO e vem de `posicoes_do_produto`; as
        # parcelas nao o conhecem. Carimbar aqui, e nao deixar para quem consome,
        # e o que permite ao Valor Liquido e a escada reproduzirem o corte que a
        # matriz faz -- ver `COL_VENCIDO_FLUXO`.
        #
        # `map` e nao `merge`: merge com sufixos já produziu coluna duplicada neste
        # arquivo, e aqui a chave e unica por construcao (`posicoes` vem agrupada).
        if COL_VENCIDO_FLUXO in posicoes.columns:
            de_op = dict(zip(posicoes["codigo_op"].astype(str),
                             posicoes[COL_VENCIDO_FLUXO].fillna(False).astype(bool)))
            fluxo[COL_VENCIDO_FLUXO] = (
                fluxo["codigo_op"].astype(str).map(de_op).fillna(False).astype(bool))
        else:
            # Chamador antigo que nao passa o flag. Falso e o unico padrao seguro
            # para nao esconder fluxo, mas fica no log: quem consumir vai tratar
            # tudo como nao vencido.
            log.warning("posicoes sem '%s': o fluxo sai sem o flag de vencido.",
                        COL_VENCIDO_FLUXO)
            fluxo[COL_VENCIDO_FLUXO] = False
        fluxo = fluxo.sort_values(["codigo_op", "nr_parcela"]).reset_index(drop=True)

    return ResultadoProjecao(
        fluxo=fluxo,
        resumo=pd.DataFrame(resumos),
        sem_cronograma=sem_cronograma,
        sem_parcela_futura=sem_parcela_futura,
        alem_da_curva=alem_da_curva,
        bullets=bullets,
        bullet_impossivel=bullet_impossivel,
        vencimento_divergente=vencimento_divergente,
        familia_sem_cronograma=familia_sem_cronograma,
        decomposicoes=decomposicoes,
        pelo_estoque=pelo_estoque,
    )


def posicoes_do_produto(
    carteira_tratada: pd.DataFrame,
    produto: str = "DC",
    somente_nao_vencidos: bool = False,
    excluir_ativos_vencidos: bool = True,
    regra_vencidos=None,
    ignorar_valor_zero: bool = True,
) -> pd.DataFrame:
    """Uma linha por operacao, com o valor da carteira agregado.

    `excluir_ativos_vencidos` tira as linhas que SAO ativo vencido (`VC*`): elas
    nao sao operacao e nao tem cronograma. `somente_nao_vencidos` alem disso
    tira as marcadas pela propagacao por emissor -- e o recorte que alimenta as
    faixas de prazo.

    `ignorar_valor_zero` descarta operacao sem valor na carteira. Sem valor nao
    ha fluxo a projetar, entao cobrar cronograma dela e pedir dado que nao muda
    nada. E o caso das parcelas vencidas zeradas: tem codigo `VC*` mas escapam da
    regra do administrador, que exige valor positivo.

    `ativo_vencido` sai daqui mesmo quando `somente_nao_vencidos` e falso: o
    recorte fica com quem consome. A projecao precisa da operacao vencida (ela
    tem cronograma e o fluxo dela e informacao), mas o Valor Liquido e a escada
    de liquidez nao podem conta-la como caixa -- e o mesmo corte que a matriz faz
    ao mandar a linha para "Vencido". `any` e nao `all`: operacao com uma parcela
    marcada esta contaminada por inteiro, porque o flag vem do EMISSOR.
    """
    from .analise import COL_PRODUTO
    from .cronograma import normalizar_codigo
    from .tratamentos import COL_ATIVO_VENCIDO, selecao_vencidos_carteira

    df = carteira_tratada
    selecao = df[COL_PRODUTO].fillna("").astype(str).str.strip().str.upper() == produto.upper()

    if excluir_ativos_vencidos and regra_vencidos is not None:
        selecao &= ~selecao_vencidos_carteira(df, regra_vencidos).fillna(False)
    if somente_nao_vencidos:
        selecao &= df[COL_ATIVO_VENCIDO].fillna("").astype(str).str.strip().str.upper() != "SIM"

    colunas = ["codigo_op", "valor_base", "linhas", "dt_vencimento",
               "n_vencimentos", "codigo_ativo", "ativo_vencido"]
    recorte = df[selecao]
    if recorte.empty:
        return pd.DataFrame(columns=colunas)

    # A data de vencimento vem junto porque a projecao bullet depende so dela e
    # do valor -- nao ha cronograma a consultar. `n_vencimentos` guarda quantas
    # datas distintas a mesma operacao tem na carteira: mais de uma significa
    # que ela nao e bullet, e ai a presuncao nao se aplica.
    agrupado = (
        recorte.assign(
            codigo_op=recorte["Codigo_Op"].map(normalizar_codigo),
            _v=pd.to_numeric(recorte["Valor_Liquido"], errors="coerce").fillna(0.0),
            _venc=pd.to_datetime(recorte.get("Data_Vencimento"), errors="coerce"),
            _vencido=(recorte[COL_ATIVO_VENCIDO].fillna("").astype(str)
                      .str.strip().str.upper() == "SIM"),
        )
        .dropna(subset=["codigo_op"])
        .groupby("codigo_op", as_index=False)
        .agg(
            valor_base=("_v", "sum"),
            linhas=("_v", "size"),
            dt_vencimento=("_venc", "max"),
            n_vencimentos=("_venc", "nunique"),
            codigo_ativo=("Codigo_Ativo", "first"),
            ativo_vencido=("_vencido", "any"),
        )
    )
    if ignorar_valor_zero:
        agrupado = agrupado[agrupado["valor_base"] != 0]
    return agrupado.sort_values("codigo_op").reset_index(drop=True)


COLUNAS_MONETARIAS = ("saldo_anterior", "vl_juros", "vl_amortizacao", "vl_parcela")


def gravar(resultado: ResultadoProjecao, destino: Path) -> Path:
    """Grava o CSV no formato que o Excel pt-BR entende.

    Sem `decimal=","` o Excel le 691.064419737381 como 691064419737381 -- ponto
    vira separador de milhar e o valor explode. Os monetarios saem com 2 casas:
    isto e o arquivo de leitura; a precisao cheia fica na camada de fatos.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    df = resultado.fluxo.copy()
    for coluna in COLUNAS_MONETARIAS:
        if coluna in df.columns:
            df[coluna] = pd.to_numeric(df[coluna], errors="coerce").round(2)
    df.to_csv(destino, index=False, sep=";", encoding="utf-8-sig", decimal=",")
    return destino
