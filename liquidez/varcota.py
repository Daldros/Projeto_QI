"""Retorno e VaR parametrico a partir da serie historica de cotas.

A serie vem da `MUL_GLD_MEC_CONSOLIDADA` do banco datalake (ver `CONSULTA`),
com CNPJ e nome da classe tirados do cadastro de fundos. Um FIDC nao tem "uma"
cota: tem senior, mezanino e subordinada, com risco incomparavel entre si -- no
MULTIPLICA a subordinada tem 180x o VaR da senior com um terco do PL. Por isso
tudo aqui e POR CLASSE, e nao existe funcao que devolva "o VaR do fundo".

Tres cuidados que a fonte exige:

1. `idFundo` repetia a mesma classe em variantes `_GER` e `_IND` na
   HOD_TB_COTAS_FUNDOS, a fonte ate 23/10/2026. O PL repete, mas a COTA nao --
   sao series diferentes. A que vale e a que reproduz o PL, entao o filtro e a
   identidade `qtCotas x valorCota = valorPL`, nunca o sufixo do nome: ha
   classe que so existe como `_IND` e ainda assim e a cota patrimonial
   correta. A MEC traz uma serie por carteira, mas o de-para aponta algumas
   para a variante, e o filtro continua valendo.

2. Ha quebra de cadastro: a senior do ECOAGRO aparece com cota de R$ 63 milhoes
   e 0,000002 cotas, o que produz retorno de +58.297.477% num dia. Retorno com
   modulo acima de `CORTE_QUEBRA` sai da conta e e contado a parte.

3. Cota de FIDC nao e preco de mercado -- ela sobe por accrual e cai em eventos
   discretos (PDD, amortizacao). A volatilidade dessa serie mede o tamanho dos
   eventos de cadastro tanto quanto o risco. Por isso o VaR historico e
   calculado JUNTO e a razao entre os dois vai no resultado: quando ela estoura,
   o proprio relatorio avisa que o numero parametrico nao deve ser usado.

O que NAO se faz aqui: filtrar a queda discreta da subordinada para a serie
ficar bem-comportada. Aquela queda e o risco que interessa.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .tabelas import DEPARA_FUNDOS, MEC

log = logging.getLogger(__name__)

TABELA = "serie_cota"

# Nivel definido pela area de risco em 11/08/2026.
NIVEL = 0.95
Z = 1.6448536269514722          # quantil da normal padrao em 95%
LAMBDA = 0.94                   # decaimento EWMA, padrao RiskMetrics
CORTE_QUEBRA = 0.50             # |retorno| acima disso e cadastro, nao mercado
MIN_OBS = 60                    # menos que isso nao sustenta estimativa de vol
JANELA_DIAS = 400               # ~13 meses, para o ano completo caber com folga
LIMIAR_ACCRUAL = 0.5            # autocorrelacao acima disso: cota nao marcada
BANDA_RAZAO = 1.5               # parametrico/historico fora disso pede atencao

# Le a MEC no datalake, desde 23/10/2026.
#
# Ate entao a serie vinha da HOD_TB_COTAS_FUNDOS (riscogc), e antes de
# 19/08/2026 da view VW_BI_LIQUIDEZ_PL, que escolhia a variante errada em 25
# fundos. A HOD juntava 29 origens; as que tinham a cota indexada (`_IND`) e o
# historico longo pararam em 11/09/2026, e desde 10/09 ela so recebia a MEC,
# carregada pela rotina do projeto Datalake (usp_Carregar_HOD_TB_COTAS_FUNDOS)
# pelo mesmo de-para usado aqui:
#     MEC.idCliente = DLK_DEPARA_FUNDOS.cdCarteira -> cdFundoInterno = idFundo
# Ler a MEC direto tira essa rotina do caminho. O preco: a MEC comeca em
# 13/02/2026, cobre ~150 carteiras (Singulare, Amplis, Minicom) e so tem a cota
# patrimonial -- sem `_IND`, toda serie sai PATRIMONIAL (ver `extrair`).
#
# As regras da carga da HOD continuam aqui: uma linha por (data, fundo), e na
# duplicidade vence a de dhAlteracao mais recente; valor com 12+ digitos
# inteiros fica de fora (na HOD nao cabia em DECIMAL(20,8) e era descartado).
CONSULTA = f"""
SELECT x.Data, x.idFundo, x.qtCotas, x.valorCota, x.valorPL
FROM (
    SELECT m.dtReferencia AS Data,
           CONVERT(VARCHAR(255), d.cdFundoInterno) AS idFundo,
           m.qtCotas, m.vlCota AS valorCota, m.vlPatrimonio AS valorPL,
           ROW_NUMBER() OVER (PARTITION BY m.dtReferencia, CONVERT(VARCHAR(255), d.cdFundoInterno)
                              ORDER BY m.dhAlteracao DESC, m.id DESC) AS rn
    FROM {MEC} AS m
    JOIN {DEPARA_FUNDOS} AS d ON CONVERT(VARCHAR(50), d.cdCarteira) = m.idCliente
    WHERE m.dtReferencia BETWEEN ? AND ?
      AND m.idCliente IS NOT NULL AND d.cdFundoInterno IS NOT NULL
) AS x
WHERE x.rn = 1 AND x.valorCota > 0 AND x.qtCotas > 0
  AND x.valorCota < 1000000000000 AND x.qtCotas < 1000000000000
  AND ABS(x.valorPL) < 1000000000000
"""

# Carteira da MEC sem de-para nao entra no join e sumiria calada. Avisar custa
# uma consulta e evita descobrir a perda so quando um fundo some do painel.
CONSULTA_SEM_DEPARA = f"""
SELECT COUNT(DISTINCT m.idCliente) AS sem_depara
FROM {MEC} AS m
WHERE m.dtReferencia BETWEEN ? AND ? AND m.idCliente IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM {DEPARA_FUNDOS} AS d
                  WHERE CONVERT(VARCHAR(50), d.cdCarteira) = m.idCliente
                    AND d.cdFundoInterno IS NOT NULL)
"""

# CNPJ e classe de cada idFundo. O cadastro inteiro, nao so o que casa: e nas
# variantes `_GER`/`_IND` que mora o nome da classe quando a linha base diz
# "ORIGINAL". Ainda no riscogc: o datalake nao tem cadastro de fundos conhecido.
CONSULTA_CADASTRO = """
SELECT idFundoInterno, nmCotaInterno, CNPJFundo
FROM dbo.HOD_TB_CADASTRO_FUNDOS
WHERE idFundoInterno IS NOT NULL
"""

# CDI diario, para o excedente do Sharpe. `Valor` e a taxa em % ao ano na base
# 252 e a coluna e varchar na origem -- a conversao e explicita do nosso lado.
CONSULTA_CDI = """
SELECT Data, Valor
FROM TB_TAXAS
WHERE Ativo = 'CDI_Over' AND Data BETWEEN ? AND ?
ORDER BY Data
"""
BASE_ANO = 252

# `nmCotaInterno` vem "ORIGINAL" nas linhas base do cadastro -- e um rotulo de
# procedencia, nao uma classe. Quem carrega a classe de verdade sao as variantes
# `_GER`/`_IND` do MESMO idFundo, cujos valores sao identicos (conferido:
# diferenca maxima 0,000000). Sem tratar isso, "ORIGINAL" virava uma classe
# propria em 81 fundos e ganhava de UNICA como referencia do Sharpe.
ROTULO_SEM_CLASSE = {"ORIGINAL", "", "NONE", "NAN"}

# Sufixo do idFundo -> classe, para quando nenhuma variante existir. E a mesma
# convencao que o proprio `idFundo` usa: PLA_GAD FIDC_SUB, SIN_LIMAJ FIDC_MEZ1.
_SUFIXO_CLASSE = (
    ("SUB", "SUBORDINADA"), ("SEN", "SENIOR"), ("MEZ", "MEZANINO"),
    ("UNICA", "UNICA"),
)


def _classe_do_sufixo(id_fundo) -> str | None:
    """Classe deduzida do sufixo do idFundo. None quando nao reconhece."""
    texto = str(id_fundo or "").strip().upper()
    if "_" not in texto:
        return None
    sufixo = texto.rsplit("_", 1)[-1]
    # `_GER`/`_IND` sao variantes, nao classes: volta um nivel.
    while sufixo in ("GER", "IND") and "_" in texto:
        texto = texto.rsplit("_", 1)[0]
        sufixo = texto.rsplit("_", 1)[-1]
    for prefixo, classe in _SUFIXO_CLASSE:
        if sufixo.startswith(prefixo):
            resto = sufixo[len(prefixo):].strip()
            return f"{classe} {resto}".strip() if resto else classe
    return None


# Ordem de subordinacao, do mais protegido ao que absorve a perda. Governa a
# ordenacao das tabelas e o passo da rampa de cor no grafico -- a cor codifica
# senioridade, entao a ordem precisa ser a economica, nao a alfabetica.
#
# O segundo termo separa serie corrente de serie encerrada: ha "SUBORDINADA OLD"
# convivendo com "SUBORDINADA", e sem isso a antiga vencia por ser maior na
# ordem alfabetica.
def ordem_classe(nome) -> tuple[int, int, str]:
    texto = str(nome or "").strip().upper()
    antiga = 1 if texto.endswith(" OLD") else 0
    if texto.startswith("SENIOR"):
        grupo = 0
    elif texto.startswith("MEZANINO"):
        grupo = 1
    elif texto.startswith("SUBORDINADA"):
        grupo = 2
    elif texto.startswith("UNICA"):
        grupo = 3
    else:
        grupo = 4
    return grupo, antiga, texto


# --- Extracao ---------------------------------------------------------------

CHAVE = ["cnpj_fundo", "id_base", "data"]

# Variantes do MESMO idFundo. Precisam colapsar numa serie so -- se virarem
# duas, o PL do fundo conta em dobro.
_VARIANTES = ("_GER_IND", "_IND", "_GER")


def id_base(id_fundo) -> str:
    """idFundo sem o sufixo de variante.

    `PLA_ADVENTURE 2 FIM_UNICA` e `PLA_ADVENTURE 2 FIM_UNICA_IND` sao a mesma
    cota vista de dois jeitos. Nao da para juntar pelo nome da classe: o
    cadastro rotula a variante `_IND` do ADVENTURE 2 como "UNICA OLD", e o par
    entrava como duas classes com o mesmo PL -- R$ 170,7 milhoes de patrimonio
    fantasma num fundo so.
    """
    texto = str(id_fundo or "").strip()
    alto = texto.upper()
    for sufixo in _VARIANTES:
        if alto.endswith(sufixo):
            return texto[: -len(sufixo)]
    return texto


def extrair(ate, dias: int = JANELA_DIAS) -> pd.DataFrame:
    """Le a janela da MEC e devolve a cota por classe, com o PL ao lado.

    A MEC so tem a cota PATRIMONIAL, a que reproduz o PL
    (`qtCotas x cota = PL`) e por isso CAI quando o fundo amortiza, mesmo sem
    ninguem perder nada. De 19/08 a 23/10/2026 a cota vinha da variante `_IND`
    da HOD_TB_COTAS_FUNDOS, a indexada, que nao e reduzida por amortizacao nem
    por aplicacao: a SENIOR 2 do GO KENERSON aparecia com -62,86% no periodo
    pela patrimonial e +22,71% pela indexada. A `_IND` deixou de ser alimentada
    em 11/09/2026 e nao existe no datalake, entao a amortizacao volta a
    aparecer como queda nas senior e mezanino que amortizam. `fonte_cota` diz
    PATRIMONIAL em toda linha e `retornos_negativos` lista esses dias.
    """
    from .db import consultar

    fim = pd.Timestamp(ate).normalize()
    inicio = fim - pd.Timedelta(days=dias)
    janela = (inicio.date().isoformat(), fim.date().isoformat())
    bruto = consultar(CONSULTA, janela)
    if bruto.empty:
        log.warning("Serie de cotas vazia entre %s e %s.", inicio.date(), fim.date())
        return pd.DataFrame(columns=["data", "cnpj_fundo", "classe", "id_fundo",
                                     "qt_cotas", "valor_cota", "valor_pl",
                                     "fonte_cota"])

    try:
        sem_depara = int(consultar(CONSULTA_SEM_DEPARA, janela)["sem_depara"].iloc[0])
        if sem_depara:
            log.warning("%d carteira(s) da MEC com cota no periodo nao tem de-para "
                        "em DLK_DEPARA_FUNDOS; ficam de fora.", sem_depara)
    except Exception:
        log.debug("Nao foi possivel contar as carteiras sem de-para.", exc_info=True)

    for coluna in ("qtCotas", "valorCota", "valorPL"):
        bruto[coluna] = pd.to_numeric(bruto[coluna], errors="coerce")
    bruto["Data"] = pd.to_datetime(bruto["Data"], errors="coerce")
    bruto = bruto.dropna(subset=["Data", "qtCotas", "valorCota", "valorPL"])

    bruto = _rotular(bruto, consultar(CONSULTA_CADASTRO))
    bruto = bruto.rename(columns={"Data": "data", "idFundo": "id_fundo",
                                  "qtCotas": "qt_cotas", "valorCota": "valor_cota",
                                  "valorPL": "valor_pl"})
    bruto["id_base"] = bruto["id_fundo"].map(id_base)

    # A identidade e o teste de que a linha e mesmo a cota patrimonial.
    fecha = ((bruto["qt_cotas"] * bruto["valor_cota"] - bruto["valor_pl"]).abs()
             <= bruto["valor_pl"].abs() * 1e-6 + 0.01)
    if not fecha.all():
        log.warning("%d linha(s) de %d idFundo nao fecham qtCotas x cota = PL; "
                    "ficam de fora.", int((~fecha).sum()),
                    bruto.loc[~fecha, "id_fundo"].nunique())

    # Base e _GER, quando ambas passam, sao iguais -- uma basta.
    saida = (bruto[fecha].sort_values(CHAVE + ["id_fundo"])
                         .drop_duplicates(CHAVE)
                         .assign(fonte_cota="PATRIMONIAL"))[
        ["data", "cnpj_fundo", "classe", "id_fundo", "id_base",
         "qt_cotas", "valor_cota", "valor_pl", "fonte_cota"]]

    # Dois idFundo distintos podem cair na MESMA classe na mesma data -- e o
    # caso do SERRA FII, com `PLA_...` e `SIN_...` reportando o mesmo fundo em
    # paralelo, valores identicos. Somar as duas dobrava o PL (R$ 71,2 mi onde
    # o patrimonio e R$ 35,6 mi). Fica uma; o desempate por idFundo so existe
    # para a escolha nao variar entre rodadas.
    antes_dedup = len(saida)
    por_serie = saida.groupby(["cnpj_fundo", "classe"]).size()
    saida = (saida.sort_values(["cnpj_fundo", "classe", "data", "id_fundo"])
                  .drop_duplicates(["cnpj_fundo", "classe", "data"]))
    if antes_dedup > len(saida):
        repetidas = por_serie - saida.groupby(["cnpj_fundo", "classe"]).size()
        alvo = repetidas[repetidas > 0]
        log.warning("%d linha(s) de classe repetida na mesma data removida(s), "
                    "em %d serie(s) -- mais de um idFundo para a mesma classe: %s.",
                    antes_dedup - len(saida), len(alvo),
                    ", ".join(f"{c}/{k}" for c, k in list(alvo.index)[:5]))

    series = saida.groupby(["cnpj_fundo", "classe"]).ngroups
    log.info("Serie de cotas (MEC): %d linha(s), %d fundo(s), %d classe(s).",
             len(saida), saida["cnpj_fundo"].nunique(), series)
    if series:
        log.warning("%d serie(s) sem cota indexada; usam a patrimonial, que a "
                    "amortizacao reduz -- retorno e vol saem distorcidos nas que "
                    "amortizam.", series)
    return saida.reset_index(drop=True)


def _classe_da_base(id_fundo, rotulos: dict[str, str]) -> str:
    """Classe de um idFundo como a serie patrimonial a teria (ver `_rotular`)."""
    texto = str(id_fundo or "").strip()
    base = id_base(texto)
    for candidato in (base, f"{base}_GER", f"{base}_IND", f"{base}_GER_IND"):
        rotulo = rotulos.get(candidato, "")
        if rotulo not in ROTULO_SEM_CLASSE:
            return rotulo
    return _classe_do_sufixo(base) or rotulos.get(texto) or rotulos.get(base) or "ORIGINAL"


def _rotular(bruto: pd.DataFrame, cadastro: pd.DataFrame) -> pd.DataFrame:
    """CNPJ e classe de cada linha da MEC, pelo idFundo que o de-para deu.

    A classe e a que a serie patrimonial tinha na HOD: a da linha base do
    cadastro ou, quando ela diz "ORIGINAL" (rotulo de procedencia, nao classe),
    a da variante `_GER`/`_IND` do mesmo idFundo, que carrega o nome correto;
    sem nenhuma, a do sufixo do idFundo. Vale tambem quando o de-para aponta
    para a variante: o cadastro chama a `_IND` de alguns fundos de "... OLD", e
    herdar esse rotulo marcaria a classe como encerrada. Sobrando alguma sem
    resolver, ela fica como esta e o log conta -- e melhor uma classe estranha
    visivel que uma classe inventada.

    idFundo sem linha no cadastro (nem a propria nem a da base) fica sem CNPJ e
    cai fora; o log conta, para a perda nao passar calada.
    """
    cad = cadastro.dropna(subset=["idFundoInterno"]).copy()
    cad["idFundoInterno"] = cad["idFundoInterno"].astype(str).str.strip()
    cad["rotulo"] = cad["nmCotaInterno"].fillna("").astype(str).str.strip().str.upper()
    cad["cnpj"] = cad["CNPJFundo"].fillna("").astype(str).map(lambda c: re.sub(r"\D", "", c))
    cad = cad.drop_duplicates("idFundoInterno")
    rotulos = dict(zip(cad["idFundoInterno"], cad["rotulo"]))
    cnpjs = {i: c for i, c in zip(cad["idFundoInterno"], cad["cnpj"]) if c}

    bruto = bruto.copy()
    bruto["idFundo"] = bruto["idFundo"].astype(str).str.strip()
    ids = bruto["idFundo"].unique()
    cnpj = {i: cnpjs.get(i) or cnpjs.get(id_base(i)) for i in ids}
    classe = {i: _classe_da_base(i, rotulos) for i in ids}
    bruto["cnpj_fundo"] = bruto["idFundo"].map(cnpj)
    bruto["classe"] = bruto["idFundo"].map(classe)

    orfaos = sorted(i for i in ids if not cnpj[i])
    if orfaos:
        log.warning("%d idFundo do de-para nao tem linha em HOD_TB_CADASTRO_FUNDOS; "
                    "sem CNPJ, ficam de fora: %s.", len(orfaos), ", ".join(orfaos[:5]))
    sem_classe = sorted(i for i in ids if cnpj[i] and classe[i] in ROTULO_SEM_CLASSE)
    if sem_classe:
        log.warning("%d idFundo seguem sem classe reconhecivel: %s.",
                    len(sem_classe), ", ".join(sem_classe[:5]))
    return bruto[bruto["cnpj_fundo"].notna()]


def extrair_cdi(ate, dias: int = JANELA_DIAS) -> pd.DataFrame:
    """CDI diario da janela, ja convertido em fator de um dia util.

    A origem publica a taxa em % ao ano na base 252, entao o fator do dia e
    `(1 + taxa/100) ** (1/252)`. Somar taxa anual dividida por 252 daria quase o
    mesmo numero e estaria errado pelo mesmo motivo que juro simples e diferente
    de composto -- ao longo de um ano a diferenca sai da terceira casa.
    """
    from .db import consultar

    fim = pd.Timestamp(ate).normalize()
    inicio = fim - pd.Timedelta(days=dias)
    d = consultar(CONSULTA_CDI, (inicio.date().isoformat(), fim.date().isoformat()))
    if d.empty:
        log.warning("Serie de CDI vazia entre %s e %s; Sharpe nao sera calculado.",
                    inicio.date(), fim.date())
        return pd.DataFrame(columns=["data", "cdi_aa", "fator"])

    d["data"] = pd.to_datetime(d["Data"], errors="coerce")
    # A coluna e varchar: virgula decimal apareceria como texto invalido, entao
    # troca antes de converter em vez de deixar virar NaN em silencio.
    d["cdi_aa"] = pd.to_numeric(
        d["Valor"].astype(str).str.strip().str.replace(",", ".", regex=False),
        errors="coerce")
    d = d.dropna(subset=["data", "cdi_aa"]).sort_values("data")
    d["fator"] = (1 + d["cdi_aa"] / 100.0) ** (1.0 / BASE_ANO)

    log.info("CDI: %d dia(s) uteis de %s a %s, taxa de %.2f%% a %.2f%% a.a.",
             len(d), d["data"].min().date(), d["data"].max().date(),
             d["cdi_aa"].min(), d["cdi_aa"].max())
    return d[["data", "cdi_aa", "fator"]].reset_index(drop=True)


def _cdi_do_intervalo(cdi: pd.DataFrame, datas: pd.Series) -> pd.Series:
    """CDI acumulado entre cada observacao de cota e a anterior.

    Nao basta pegar o CDI do dia: entre uma sexta e a segunda seguinte passam
    tres dias corridos mas so um dia util de CDI, e depois de feriado prolongado
    passam varios. Acumular o fator no intervalo (t-1, t] resolve os dois casos
    sem precisar de calendario proprio -- a propria serie de CDI ja e o
    calendario de dias uteis.
    """
    if cdi.empty:
        return pd.Series(index=datas.index[1:], dtype=float)

    c = cdi.set_index("data")["fator"].sort_index()
    # Produto acumulado: dividir dois pontos dele devolve o fator do intervalo.
    acum = c.cumprod()
    # `asof` pega o ultimo dia util com CDI ate a data -- cota lancada em dia
    # sem CDI (fim de semana, ajuste) nao vira lacuna.
    posicoes = acum.reindex(acum.index.union(pd.DatetimeIndex(datas))).ffill()
    nos_pontos = posicoes.reindex(pd.DatetimeIndex(datas))
    return pd.Series((nos_pontos.to_numpy()[1:] / nos_pontos.to_numpy()[:-1]) - 1.0,
                     index=datas.index[1:])


def cdi_acumulado(cdi: pd.DataFrame, datas) -> list[float | None]:
    """CDI acumulado em cada data, com base zero na primeira delas.

    Serve para o CDI entrar no grafico de rentabilidade na mesma unidade das
    classes: as duas viram "quanto rendeu desde o inicio da janela", e ai o
    eixo unico e honesto e a distancia vertical significa diferenca de retorno.

    Base na primeira data do GRAFICO, nao na primeira data do CDI: o benchmark
    tem de partir de onde a serie do fundo parte, senao a comparacao embute um
    trecho que o fundo nao viveu.
    """
    if cdi.empty or len(datas) == 0:
        return [None] * len(datas)

    c = cdi.set_index("data")["fator"].sort_index()
    acum = c.cumprod()
    alvo = pd.DatetimeIndex([pd.Timestamp(d) for d in datas])
    # `ffill` sobre a uniao resolve data do fundo que caia em dia sem CDI --
    # ali vale o ultimo fechamento, que e o que de fato rendeu ate ali.
    nos_pontos = acum.reindex(acum.index.union(alvo)).ffill().reindex(alvo)
    base = nos_pontos.iloc[0]
    if pd.isna(base) or base == 0:
        return [None] * len(datas)
    return [None if pd.isna(v) else float(v / base - 1.0) for v in nos_pontos]


# Classes remuneradas por contrato -- CDI + taxa pre. A cota indexada delas so
# acumula: retorno negativo ali nao e oscilacao de mercado, e sinal de que algo
# esta errado (PDD comendo a remuneracao, erro de cadastro, ou a serie
# patrimonial entrando no lugar da indexada). Sempre avisar.
CLASSES_REMUNERADAS = ("SENIOR", "MEZANINO")


def e_classe_remunerada(classe) -> bool:
    texto = str(classe or "").strip().upper()
    return texto.startswith(CLASSES_REMUNERADAS)


def retornos_negativos(serie: pd.DataFrame, corte: float = CORTE_QUEBRA) -> pd.DataFrame:
    """Dias de retorno negativo em senior/mezanino -- que nao deveriam existir.

    Sao classes remuneradas a CDI + taxa pre: a cota indexada sobe todo dia
    util. Queda ali merece investigacao, entao a funcao devolve o dia, o
    tamanho e a fonte da cota, em vez de um booleano.

    Quebra de cadastro (|retorno| > `corte`) fica de fora: aquilo e outro
    problema, ja contado a parte.
    """
    if serie.empty:
        return pd.DataFrame()
    achados = []
    for (cnpj, classe), g in serie.groupby(["cnpj_fundo", "classe"]):
        if not e_classe_remunerada(classe):
            continue
        g = g.sort_values("data")
        r = g["valor_cota"].pct_change()
        ruim = (r < 0) & (r.abs() <= corte)
        if not ruim.any():
            continue
        d = g[ruim]
        achados.append(pd.DataFrame({
            "cnpj_fundo": cnpj, "classe": classe,
            "data": d["data"], "valor_cota": d["valor_cota"],
            "retorno": r[ruim],
            "fonte_cota": d.get("fonte_cota", pd.Series("?", index=d.index)),
        }))
    if not achados:
        return pd.DataFrame()
    return (pd.concat(achados, ignore_index=True)
              .sort_values("retorno")
              .reset_index(drop=True))


def caminho(destino: Path) -> Path:
    return Path(destino) / TABELA / "dados.parquet"


def caminho_cdi(destino: Path) -> Path:
    return Path(destino) / "serie_cdi" / "dados.parquet"


def gravar_cdi(df: pd.DataFrame, destino: Path) -> Path:
    alvo = caminho_cdi(destino)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(alvo, index=False)
    return alvo


def carregar_cdi(destino: Path) -> pd.DataFrame:
    """O CDI gravado. Ausencia so tira o Sharpe; o VaR nao depende dele."""
    alvo = caminho_cdi(destino)
    if not alvo.exists():
        return pd.DataFrame()
    df = pd.read_parquet(alvo)
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"])
    return df


def gravar(df: pd.DataFrame, destino: Path) -> Path:
    """Tabela unica, nao particionada por data.

    A serie inteira sao dezenas de milhares de linhas -- particionar por
    data-base repetiria os mesmos 13 meses em cada pasta sem ganho nenhum.
    """
    alvo = caminho(destino)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(alvo, index=False)
    return alvo


def carregar(destino: Path) -> pd.DataFrame:
    """A serie gravada. Ausencia nao e erro: o relatorio so omite a secao."""
    alvo = caminho(destino)
    if not alvo.exists():
        return pd.DataFrame()
    df = pd.read_parquet(alvo)
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"])
    return df


# --- Estatistica ------------------------------------------------------------

def vol_ewma(retornos: pd.Series, lam: float = LAMBDA) -> float:
    """Desvio-padrao com peso exponencial, o mais recente pesando mais.

    Peso igual leva um ano para reagir a mudanca de regime, e essas carteiras
    mudam de regime quando entra inadimplencia -- a media simples ainda estaria
    mostrando o mundo anterior.
    """
    x = np.asarray(retornos, dtype=float)
    if len(x) < 2:
        return float("nan")
    pesos = lam ** np.arange(len(x) - 1, -1, -1)
    pesos /= pesos.sum()
    media = float((pesos * x).sum())
    return float(np.sqrt((pesos * (x - media) ** 2).sum()))


def janelas(ate) -> dict[str, pd.Timestamp]:
    """Inicio das janelas de retorno, a partir da data-base.

    - `12m`: retornos POSTERIORES a mesma data do ano anterior. O retorno de um
      dia mede o movimento da vespera para ele, entao o primeiro que pertence
      aos 12 meses e o do dia seguinte ao corte -- incluir o proprio corte
      contaria um movimento que aconteceu no ano anterior.
    - `ano`: do primeiro dia util do ano corrente ate a data-base.
    - `mes`: do primeiro dia util do mes da data-base ate ela.

    `ano` e `mes` sao CALENDARIO, e nao "ultimos 12 meses" ou "ultimos 30 dias":
    o corte e o dia 1o, entao a janela encolhe e reinicia com a virada. Em
    31/07/2026 o mes tem o mes inteiro; em 01/08 teria um dia.

    A referencia e UMA para todas as classes, e nao a ultima data de cada uma:
    comparar classes medidas em janelas diferentes nao compara nada.
    """
    ate = pd.Timestamp(ate)
    return {
        "12m": ate - pd.DateOffset(years=1),
        "ano": pd.Timestamp(year=ate.year, month=1, day=1),
        "mes": pd.Timestamp(year=ate.year, month=ate.month, day=1),
    }


def _retorno_na_janela(
    retornos: pd.Series, datas: pd.Series, inicio: pd.Timestamp, incluir_corte: bool
) -> tuple[float, int, pd.Timestamp | None]:
    """(retorno composto, observacoes, primeira data) dentro da janela."""
    dentro = datas >= inicio if incluir_corte else datas > inicio
    r = retornos[dentro]
    if r.empty:
        return float("nan"), 0, None
    return float((1 + r).prod() - 1), len(r), pd.Timestamp(datas[dentro].min())


def metricas(
    serie: pd.DataFrame,
    cdi: pd.DataFrame | None = None,
    nivel: float = NIVEL,
    lam: float = LAMBDA,
    min_obs: int = MIN_OBS,
) -> pd.DataFrame:
    """Uma linha por (cnpj_fundo, classe) com retorno, vol, VaR e Sharpe.

    `serie` e o recorte ja lido de `serie_cota`. Classe com menos de `min_obs`
    retornos uteis fica de fora: estimar vol com meia duzia de pontos produz um
    numero com casas decimais e nenhuma informacao.

    `cdi` e a serie de `serie_cdi`; sem ela o Sharpe sai vazio e o resto segue.
    """
    if serie.empty:
        return pd.DataFrame()
    cdi = cdi if cdi is not None else pd.DataFrame()

    # Data-base UNICA, da serie inteira: as janelas de 12m e do ano tem de ser
    # as mesmas para todas as classes de todos os fundos.
    corte = janelas(serie["data"].max())

    z = float(abs(np.sqrt(2) * _erfinv(2 * nivel - 1))) if nivel != NIVEL else Z
    linhas = []
    for (cnpj, classe), g in serie.groupby(["cnpj_fundo", "classe"]):
        g = g.sort_values("data")
        r = g["valor_cota"].pct_change().dropna()
        if r.empty:
            continue

        # O CDI do mesmo intervalo, alinhado ANTES do corte de quebra, para
        # sobrar so o par (retorno da cota, CDI do periodo) nas datas boas.
        cdi_periodo = _cdi_do_intervalo(cdi, g["data"]) if not cdi.empty else None

        quebra = r.abs() > CORTE_QUEBRA
        n_quebras = int(quebra.sum())
        r = r[~quebra]
        if len(r) < min_obs:
            continue
        if cdi_periodo is not None:
            cdi_periodo = cdi_periodo.reindex(r.index)

        vol_d = vol_ewma(r, lam)
        cota_ini = float(g["valor_cota"].iloc[0])
        cota_fim = float(g["valor_cota"].iloc[-1])
        dias = max((g["data"].iloc[-1] - g["data"].iloc[0]).days, 1)
        ac1 = float(r.autocorr(1)) if len(r) > 3 else float("nan")
        pl = float(g["valor_pl"].iloc[-1])
        var_pct = z * vol_d
        # O historico nao entra no numero oficial -- ele existe para denunciar
        # quando a premissa normal nao se sustenta.
        var_hist = -float(np.percentile(r, (1 - nivel) * 100))

        # O retorno vem do PRODUTO dos retornos filtrados, e nao da razao entre
        # as pontas da cota. A razao das pontas embute as quebras que o filtro
        # tirou -- e ai a serie ganhava um retorno que a volatilidade fingia
        # nao ter visto. No ENERGY 21, um salto de +69,78% em 31/10/2025 fazia
        # o retorno da tabela ser +52,5% enquanto o mesmo periodo, sem a quebra,
        # rendeu -10,2%: o Sharpe saia +3,24 em vez de -1,76, com o sinal
        # trocado.
        ret_periodo = float((1 + r).prod() - 1)
        # Anualizacao por dias UTEIS (252/n), a mesma do Sharpe movel. Antes
        # eram 365/dias corridos aqui e 252/n la, e as duas nao batiam.
        ret_anual = float((1 + ret_periodo) ** (BASE_ANO / len(r)) - 1)
        vol_anual = vol_d * np.sqrt(BASE_ANO)

        # Janelas fechadas, sobre os MESMOS retornos filtrados: 12 meses e o
        # ano corrente. `cobre_*` diz se a serie alcanca o inicio da janela --
        # sem isso, uma classe emitida em marco exibiria como "12 meses" o
        # retorno de quatro, e pareceria comparavel com a que tem os doze.
        datas_r = g.loc[r.index, "data"]
        ret_12m, obs_12m, de_12m = _retorno_na_janela(
            r, datas_r, corte["12m"], incluir_corte=False)
        ret_ano, obs_ano, de_ano = _retorno_na_janela(
            r, datas_r, corte["ano"], incluir_corte=True)
        ret_mes, obs_mes, de_mes = _retorno_na_janela(
            r, datas_r, corte["mes"], incluir_corte=True)
        primeira = pd.Timestamp(datas_r.min())

        # --- Sharpe -------------------------------------------------------
        # Forma classica: (retorno do fundo - retorno do CDI) / volatilidade DO
        # FUNDO, tudo anualizado. O denominador e a vol do fundo, nao a do
        # excedente -- sao coisas diferentes, e a do excedente mede tracking
        # error, nao o risco que o cotista corre.
        #
        # A vantagem pratica de usar exatamente `vol_anual` no denominador e que
        # os tres termos ficam no tooltip do Sharpe no relatorio: anualizado,
        # "CDI a.a." e "Vol. a.a.". Quem desconfiar do numero confere a divisao
        # a olho, sem precisar da nossa palavra.
        sharpe = cdi_ac = ret_cdi_aa = pct_cdi = float("nan")
        cdi_12m = cdi_ano = cdi_mes = float("nan")
        obs_cdi = 0
        if cdi_periodo is not None:
            par = pd.DataFrame({"r": r, "c": cdi_periodo}).dropna()
            obs_cdi = len(par)
            if obs_cdi >= min_obs:
                # O CDI nas MESMAS janelas do fundo, sobre as MESMAS datas: sao
                # os numeros que a tabela poe lado a lado, e medi-los em
                # recortes diferentes tornaria a comparacao decorativa.
                datas_par = g.loc[par.index, "data"]
                cdi_12m, _, _ = _retorno_na_janela(
                    par["c"], datas_par, corte["12m"], incluir_corte=False)
                cdi_ano, _, _ = _retorno_na_janela(
                    par["c"], datas_par, corte["ano"], incluir_corte=True)
                cdi_mes, _, _ = _retorno_na_janela(
                    par["c"], datas_par, corte["mes"], incluir_corte=True)
                cdi_ac = float((1 + par["c"]).prod() - 1)
                ret_cdi_aa = float((1 + cdi_ac) ** (BASE_ANO / len(par)) - 1)
                # O retorno do fundo e recalculado no MESMO recorte do CDI --
                # `par` pode ter menos linhas que `r` se faltar CDI em alguma
                # data, e comparar periodos diferentes seria comparar outra
                # coisa.
                ret_fundo_aa = float((1 + par["r"]).prod() ** (BASE_ANO / len(par)) - 1)
                if vol_anual > 1e-12:
                    sharpe = (ret_fundo_aa - ret_cdi_aa) / vol_anual
                if abs(cdi_ac) > 1e-12:
                    pct_cdi = float(ret_periodo / cdi_ac)

        linhas.append({
            "cnpj_fundo": cnpj, "classe": classe,
            "obs": len(r), "obs_cdi": obs_cdi, "quebras": n_quebras,
            "de": g["data"].iloc[0], "ate": g["data"].iloc[-1],
            "pl": pl,
            "ret_periodo": ret_periodo,
            "ret_anual": ret_anual,
            "ret_12m": ret_12m,
            "obs_12m": obs_12m,
            "de_12m": de_12m,
            "cobre_12m": bool(primeira <= corte["12m"]),
            "ret_ano": ret_ano,
            "obs_ano": obs_ano,
            "de_ano": de_ano,
            "cobre_ano": bool(primeira <= corte["ano"]),
            "ret_mes": ret_mes,
            "obs_mes": obs_mes,
            "de_mes": de_mes,
            "cobre_mes": bool(primeira <= corte["mes"]),
            "vol_dia": vol_d,
            "vol_anual": vol_anual,
            "var_1d_pct": var_pct,
            "var_1d_rs": var_pct * pl,
            "var_21d_pct": var_pct * np.sqrt(21),
            "var_21d_rs": var_pct * np.sqrt(21) * pl,
            "var_hist_pct": var_hist,
            "razao_hist": var_hist / var_pct if var_pct > 1e-12 else float("nan"),
            "sharpe": sharpe,
            "cdi_periodo": cdi_ac,
            "cdi_12m": cdi_12m,
            "cdi_ano": cdi_ano,
            "cdi_mes": cdi_mes,
            "cdi_anual": ret_cdi_aa,
            "pct_cdi": pct_cdi,
            "autocorr1": ac1,
            "accrual": bool(ac1 > LIMIAR_ACCRUAL) if pd.notna(ac1) else False,
            "dias_negativos": float((r < 0).mean()),
            "curtose": float(r.kurtosis()) if len(r) > 3 else float("nan"),
            # A perda que de fato aconteceu, no mesmo recorte da vol e do VaR
            # (sem as quebras). E o numero que denuncia quando a serie cai por
            # evento discreto: a ORYX perdeu 44,75% no periodo, com 42,22% deles
            # num unico dia -- coisa que vol e VaR medio diluem.
            "pior_dia": float(r.min()),
            "melhor_dia": float(r.max()),
        })

    if not linhas:
        return pd.DataFrame()
    d = pd.DataFrame(linhas)
    d["_ordem"] = d["classe"].map(ordem_classe)
    return (d.sort_values(["cnpj_fundo", "_ordem"])
             .drop(columns="_ordem").reset_index(drop=True))


def vol_maxima_por_fundo(
    serie: pd.DataFrame,
    lam: float = LAMBDA,
    min_obs: int = MIN_OBS,
) -> pd.DataFrame:
    """A classe mais volatil de cada fundo, com a volatilidade dela.

    Por que a MAXIMA e nao a media nem a composta: num FIDC a senior anda por
    accrual e quase nao oscila, entao qualquer media diluiria o risco na
    estrutura de subordinacao. Quem carrega a oscilacao e a ponta subordinada, e
    o numero que descreve o fundo e o dela.

    Volatilidade em vez de VaR resolve dois problemas de uma vez: nao supoe
    normalidade -- o desvio e o que a serie mostrou -- e nao aplica quantil, que
    era o que fazia o VaR do ORYX chegar a 207% ao ano. A anualizacao por raiz
    de 252 continua sendo convencao, mas sobre um parametro de escala ela e bem
    menos fragil que sobre um quantil de perda.

    `conc_pior_dia` e a fracao da variancia que vem do MAIOR desvio isolado.
    Quando passa de metade, a volatilidade nao descreve oscilacao recorrente e
    sim o tamanho de um evento unico -- no ORYX, um dia responde por 80%.
    """
    if serie.empty:
        return pd.DataFrame()

    linhas = []
    for (cnpj, classe), g in serie.groupby(["cnpj_fundo", "classe"]):
        g = g.sort_values("data")
        r = g["valor_cota"].pct_change().dropna()
        r = r[r.abs() <= CORTE_QUEBRA]
        if len(r) < min_obs:
            continue

        vol_d = vol_ewma(r, lam)
        desvios = (r - r.mean()) ** 2
        soma = float(desvios.sum())
        conc = float(desvios.max() / soma) if soma > 0 else float("nan")
        ac1 = float(r.autocorr(1)) if len(r) > 3 else float("nan")
        i_pior = r.idxmin()

        linhas.append({
            "cnpj_fundo": cnpj, "classe": classe, "obs": len(r),
            "pl": float(g["valor_pl"].iloc[-1]),
            "vol_anual": vol_d * np.sqrt(BASE_ANO),
            "conc_pior_dia": conc,
            "pior_dia": float(r.min()),
            "pior_dia_em": pd.Timestamp(g.loc[i_pior, "data"])
            if pd.notna(i_pior) else pd.NaT,
            "autocorr1": ac1,
            "accrual": bool(ac1 > LIMIAR_ACCRUAL) if pd.notna(ac1) else False,
        })

    if not linhas:
        return pd.DataFrame()

    d = pd.DataFrame(linhas)

    # Serie encerrada ("... OLD") nao descreve o fundo de hoje, e costuma ser a
    # MAIS volatil justamente por ser antiga e ja ter passado pelos eventos.
    # Sem este corte, o painel mostrava a vol de uma subordinada encerrada em 4
    # fundos -- no PENINSULA AZUL, 155% vindos de uma classe que nao existe
    # mais. A regra e a mesma de `classe_referencia`: a corrente ganha, e a
    # antiga so entra se nao houver corrente naquele fundo.
    d["_antiga"] = d["classe"].map(lambda c: ordem_classe(c)[1])
    tem_corrente = d.groupby("cnpj_fundo")["_antiga"].transform("min") == 0
    d = d[(d["_antiga"] == 0) | ~tem_corrente].drop(columns="_antiga")

    # PL do FUNDO, nao da classe: o da classe mais volatil seria so uma fatia, e
    # o painel compara fundos entre si.
    pl_fundo = d.groupby("cnpj_fundo")["pl"].sum().rename("pl_fundo")
    n_classes = d.groupby("cnpj_fundo")["classe"].size().rename("n_classes")
    maior = d.loc[d.groupby("cnpj_fundo")["vol_anual"].idxmax()]
    return (maior.merge(pl_fundo, on="cnpj_fundo")
                 .merge(n_classes, on="cnpj_fundo")
                 .reset_index(drop=True))


def metricas_por_fundo(
    serie: pd.DataFrame,
    nivel: float = NIVEL,
    lam: float = LAMBDA,
    min_obs: int = MIN_OBS,
) -> pd.DataFrame:
    """Uma linha por FUNDO: o VaR do retorno composto das classes.

    Fundo nao tem cota -- tem classes, cada uma com a sua. O numero por fundo
    que faz sentido e o retorno de quem tivesse TODAS as classes na proporcao do
    PL, que e o que o ativo do fundo fez. Peso do dia anterior, porque o retorno
    de hoje se aplica ao patrimonio de ontem.

    Somar o VaR das classes daria quase o mesmo (mediana 1,00x em 31/07/2026,
    porque as classes de um FIDC sao lastreadas na mesma carteira e se movem
    juntas), mas so por coincidencia aritmetica: a soma assume correlacao 1, que
    a estrutura de subordinacao nega. O composto nao precisa dessa suposicao.

    Efeito colateral util: compor as classes elimina o problema de accrual. A
    senior sozinha quase nao oscila; misturada com a subordinada, a serie passa
    a se mover -- em 31/07/2026, nenhum dos 71 compostos tinha autocorrelacao
    acima de 0,5, contra 4 das series por classe.
    """
    if serie.empty:
        return pd.DataFrame()

    corte = janelas(serie["data"].max())
    z = Z if nivel == NIVEL else float(abs(np.sqrt(2) * _erfinv(2 * nivel - 1)))
    linhas = []
    for cnpj, g in serie.groupby("cnpj_fundo"):
        cotas = g.pivot_table(index="data", columns="classe", values="valor_cota")
        pls = g.pivot_table(index="data", columns="classe", values="valor_pl")
        if cotas.shape[1] == 0 or len(cotas) < min_obs + 1:
            continue

        peso = pls.shift(1)
        peso = peso.div(peso.sum(axis=1), axis=0)
        comp = (cotas.pct_change() * peso).sum(axis=1, min_count=1).dropna()

        n_quebras = int((comp.abs() > CORTE_QUEBRA).sum())
        comp = comp[comp.abs() <= CORTE_QUEBRA]
        if len(comp) < min_obs:
            continue

        vol_d = vol_ewma(comp, lam)
        pl = float(pls.iloc[-1].sum())
        var_1d = z * vol_d
        var_hist = -float(np.percentile(comp, (1 - nivel) * 100))
        # A pior queda de um dia nao supoe nada: e o que de fato aconteceu. Em
        # carteira de credito, onde a perda chega em salto de provisao, ela
        # descreve melhor que qualquer quantil.
        pior = float(comp.min())
        i_pior = comp.idxmin()

        # Retorno do MESMO composto que gera a vol e o VaR -- produto dos
        # retornos diarios ja filtrados, nao a razao entre as pontas da cota.
        # A razao das pontas embutiria as quebras que o filtro acabou de tirar,
        # e o fundo ganharia um retorno que a volatilidade fingiu nao ver.
        ret_periodo = float((1 + comp).prod() - 1)
        ret_anual = float((1 + ret_periodo) ** (BASE_ANO / len(comp)) - 1)

        # As mesmas janelas da tabela por classe, sobre o composto. O indice do
        # composto JA e a data, entao a janela sai direto dele.
        datas_comp = pd.Series(comp.index, index=comp.index)
        ret_12m, obs_12m, _ = _retorno_na_janela(
            comp, datas_comp, corte["12m"], incluir_corte=False)
        ret_ano, obs_ano, _ = _retorno_na_janela(
            comp, datas_comp, corte["ano"], incluir_corte=True)
        ret_mes, obs_mes, _ = _retorno_na_janela(
            comp, datas_comp, corte["mes"], incluir_corte=True)
        primeira = pd.Timestamp(comp.index.min())

        linhas.append({
            "cnpj_fundo": cnpj, "obs": len(comp), "quebras": n_quebras,
            "classes": int(cotas.shape[1]),
            "de": comp.index.min(), "ate": comp.index.max(),
            "pl": pl,
            "ret_periodo": ret_periodo,
            "ret_anual": ret_anual,
            "ret_12m": ret_12m,
            "obs_12m": obs_12m,
            "cobre_12m": bool(primeira <= corte["12m"]),
            "ret_ano": ret_ano,
            "obs_ano": obs_ano,
            "cobre_ano": bool(primeira <= corte["ano"]),
            "ret_mes": ret_mes,
            "obs_mes": obs_mes,
            "cobre_mes": bool(primeira <= corte["mes"]),
            "vol_anual": vol_d * np.sqrt(BASE_ANO),
            "var_1d_pct": var_1d,
            "var_1d_rs": var_1d * pl,
            "var_anual_pct": var_1d * np.sqrt(BASE_ANO),
            "var_hist_pct": var_hist,
            "razao_hist": var_hist / var_1d if var_1d > 1e-12 else float("nan"),
            "pior_dia": pior,
            "pior_dia_em": pd.Timestamp(i_pior) if pd.notna(i_pior) else pd.NaT,
            "autocorr1": float(comp.autocorr(1)) if len(comp) > 3 else float("nan"),
        })

    if not linhas:
        return pd.DataFrame()
    return pd.DataFrame(linhas).reset_index(drop=True)


# Janela do Sharpe movel, em observacoes. Seis meses e o meio-termo: com 252 a
# serie de ~266 pontos renderia uns 14 valores e nao mostraria evolucao nenhuma;
# com 21 o indice pula a cada evento isolado e vira ruido. Com 126 sobram ~140
# pontos e o desenho ainda responde a mudanca de regime.
JANELA_MOVEL = 126


# Minimo de meses para o Sharpe mensal. Com menos que isso o desvio de uma
# amostra de meses nao sustenta divisao.
MIN_MESES = 6


def classe_referencia(classes) -> str | None:
    """A classe cujo Sharpe representa o fundo.

    Num FIDC, senior e mezanino sao remuneradas a CDI + taxa por contrato: o
    retorno delas e quase deterministico e a volatilidade tende a zero, entao o
    Sharpe sai sempre positivo e enorme -- 44 e 56 no MULTIPLICA. Aquilo nao
    mede gestao, mede a existencia do contrato. Quem absorve resultado e a
    JUNIOR (subordinada), e e o Sharpe dela que diz alguma coisa.

    Fundo sem estrutura de classes tem `UNICA`, e ai a referencia e ele proprio.

    A escolha e pela maior subordinacao presente: se nao houver subordinada,
    cai para a mezanino, e so entao para a senior -- sempre a classe mais
    exposta que existir.
    """
    nomes = [str(c) for c in classes if str(c).strip()]
    if not nomes:
        return None
    # Serie encerrada ("... OLD") so entra se nao houver corrente -- ela existe
    # no historico mas nao descreve o fundo de hoje.
    correntes = [c for c in nomes if ordem_classe(c)[1] == 0]
    nomes = correntes or nomes

    def chave(c):
        grupo, antiga, texto = ordem_classe(c)
        # Dentro do grupo, a corrente ganha: `antiga` entra negado porque a
        # escolha e por `max`.
        return grupo, -antiga, texto

    fidc = [c for c in nomes if ordem_classe(c)[0] <= 2]
    return max(fidc or nomes, key=chave)


def serie_referencia(serie: pd.DataFrame) -> pd.DataFrame:
    """So as linhas da classe de referencia de cada fundo."""
    if serie.empty:
        return serie
    escolha = (serie.groupby("cnpj_fundo")["classe"]
               .apply(lambda s: classe_referencia(s.unique())))
    alvo = serie["cnpj_fundo"].map(escolha)
    return serie[serie["classe"] == alvo].copy()


def _vol_ewma_expandida(x: np.ndarray, min_obs: int, lam: float = LAMBDA) -> np.ndarray:
    """Vol EWMA usando TODO o historico ate cada ponto. NaN antes de `min_obs`."""
    n = len(x)
    saida = np.full(n, np.nan)
    for i in range(min_obs - 1, n):
        trecho = x[: i + 1]
        pesos = lam ** np.arange(len(trecho) - 1, -1, -1)
        pesos /= pesos.sum()
        media = float((pesos * trecho).sum())
        saida[i] = float(np.sqrt((pesos * (trecho - media) ** 2).sum()))
    return saida


def sharpe_acumulado(
    serie: pd.DataFrame,
    cdi: pd.DataFrame,
    min_obs: int = MIN_OBS,
    lam: float = LAMBDA,
) -> pd.DataFrame:
    """Sharpe do retorno acumulado DESDE O INICIO da serie, ponto a ponto.

    Janela que cresce, nao janela que desliza: cada ponto responde "considerando
    tudo o que se sabe ate aqui, quanto o fundo rendeu acima do CDI por unidade
    de risco". O ultimo ponto e, por construcao, o numero unico da tabela.

    Diferenca de leitura em relacao a janela movel: aqui o desenho estabiliza
    com o tempo, porque cada novo dia pesa menos no acumulado. Movimento no fim
    da serie significa mudanca grande, nao ruido.
    """
    if serie.empty or cdi.empty:
        return pd.DataFrame(columns=["cnpj_fundo", "classe", "data", "sharpe"])

    saida = []
    for (cnpj, classe), g in serie.groupby(["cnpj_fundo", "classe"]):
        g = g.sort_values("data")
        r = g["valor_cota"].pct_change().dropna()
        c = _cdi_do_intervalo(cdi, g["data"])
        par = pd.DataFrame({"r": r, "c": c, "data": g["data"]}).dropna()
        par = par[par["r"].abs() <= CORTE_QUEBRA]
        if len(par) < min_obs:
            continue

        n = np.arange(1, len(par) + 1)
        fundo_aa = np.expm1(np.log1p(par["r"]).cumsum().to_numpy() * (BASE_ANO / n))
        cdi_aa = np.expm1(np.log1p(par["c"]).cumsum().to_numpy() * (BASE_ANO / n))
        vol_aa = _vol_ewma_expandida(par["r"].to_numpy(), min_obs, lam) * np.sqrt(BASE_ANO)
        with np.errstate(invalid="ignore", divide="ignore"):
            s = np.where(vol_aa > 1e-12, (fundo_aa - cdi_aa) / vol_aa, np.nan)

        ok = ~np.isnan(s)
        if not ok.any():
            continue
        saida.append(pd.DataFrame({
            "cnpj_fundo": cnpj, "classe": classe,
            "data": par["data"].to_numpy()[ok], "sharpe": s[ok],
        }))

    if not saida:
        return pd.DataFrame(columns=["cnpj_fundo", "classe", "data", "sharpe"])
    return pd.concat(saida, ignore_index=True)


def sharpe_mensal(
    serie: pd.DataFrame,
    cdi: pd.DataFrame,
    min_meses: int = MIN_MESES,
) -> pd.DataFrame:
    """Sharpe sobre retornos MENSAIS, acumulado desde o inicio.

    O retorno de cada mes e a composicao dos retornos diarios dele; o Sharpe de
    cada ponto usa todos os meses ate ali, anualizado por raiz de 12.

    Por que mensal alem do diario: retorno diario de cota de FIDC e quase todo
    accrual com saltos ocasionais de provisao, e o desvio diario acaba medindo
    o calendario de reconhecimento mais que o risco. Agregando em mes, o salto
    se dilui no periodo em que de fato pertence e a serie fica mais parecida
    com o que o cotista percebe.

    Aqui o desvio e o SIMPLES, nao EWMA: sao ~13 pontos, e o peso exponencial
    concentraria quase tudo nos dois ultimos meses.
    """
    if serie.empty or cdi.empty:
        return pd.DataFrame(columns=["cnpj_fundo", "classe", "data", "sharpe"])

    saida = []
    for (cnpj, classe), g in serie.groupby(["cnpj_fundo", "classe"]):
        g = g.sort_values("data")
        r = g["valor_cota"].pct_change().dropna()
        c = _cdi_do_intervalo(cdi, g["data"])
        par = pd.DataFrame({"r": r, "c": c}).dropna()
        par = par[par["r"].abs() <= CORTE_QUEBRA]
        par.index = pd.DatetimeIndex(g["data"].reindex(par.index))
        if par.empty:
            continue

        # Fim de mes: o ponto do mes vale para o fechamento dele.
        mensal = par.resample("ME").apply(lambda x: (1 + x).prod() - 1).dropna()
        if len(mensal) < min_meses:
            continue

        n = np.arange(1, len(mensal) + 1)
        fundo_aa = np.expm1(np.log1p(mensal["r"]).cumsum().to_numpy() * (12 / n))
        cdi_aa = np.expm1(np.log1p(mensal["c"]).cumsum().to_numpy() * (12 / n))
        vol = mensal["r"].expanding(min_meses).std(ddof=1).to_numpy() * np.sqrt(12)
        with np.errstate(invalid="ignore", divide="ignore"):
            s = np.where(vol > 1e-12, (fundo_aa - cdi_aa) / vol, np.nan)

        ok = ~np.isnan(s)
        if not ok.any():
            continue
        saida.append(pd.DataFrame({
            "cnpj_fundo": cnpj, "classe": classe,
            "data": mensal.index.to_numpy()[ok], "sharpe": s[ok],
        }))

    if not saida:
        return pd.DataFrame(columns=["cnpj_fundo", "classe", "data", "sharpe"])
    return pd.concat(saida, ignore_index=True)


def _vol_ewma_movel(x: np.ndarray, janela: int, lam: float = LAMBDA) -> np.ndarray:
    """Vol EWMA em cada janela deslizante. NaN onde ainda nao ha janela cheia.

    Os pesos sao os mesmos em toda janela -- so o trecho de dados muda -- entao
    sao calculados uma vez e reaproveitados. Com `rolling().apply()` cada janela
    reentraria no interpretador e as ~120 series levavam minutos.
    """
    n = len(x)
    saida = np.full(n, np.nan)
    if n < janela:
        return saida
    pesos = lam ** np.arange(janela - 1, -1, -1)
    pesos = pesos / pesos.sum()
    # Uma matriz (n-janela+1, janela) com todas as janelas, sem copiar dados.
    vistas = np.lib.stride_tricks.sliding_window_view(x, janela)
    medias = vistas @ pesos
    desvios = vistas - medias[:, None]
    saida[janela - 1:] = np.sqrt((desvios ** 2) @ pesos)
    return saida


def sharpe_movel(
    serie: pd.DataFrame,
    cdi: pd.DataFrame,
    janela: int = JANELA_MOVEL,
    min_obs: int = MIN_OBS,
    lam: float = LAMBDA,
) -> pd.DataFrame:
    """Sharpe em janela movel, uma linha por (fundo, classe, data).

    Mesma definicao do numero unico: (retorno do fundo - retorno do CDI) sobre
    a volatilidade DO FUNDO, tudo anualizado dentro da janela. Os dois tem de
    concordar -- se o grafico usasse outra formula, o ultimo ponto dele nao
    bateria com a tabela ao lado e ninguem saberia qual acreditar.

    O ponto de cada data resume os `janela` retornos ANTERIORES a ela -- e um
    retrovisor, nao uma previsao. Duas leituras que ele permite e o numero unico
    esconde: quando o excedente virou, e se a queda veio de retorno pior ou de
    volatilidade maior.

    Serie accrual fica de fora do desenho, nao daqui: a decisao do que exibir e
    do relatorio, e o dado bruto continua disponivel para conferencia.
    """
    if serie.empty or cdi.empty:
        return pd.DataFrame(columns=["cnpj_fundo", "classe", "data", "sharpe"])

    saida = []
    for (cnpj, classe), g in serie.groupby(["cnpj_fundo", "classe"]):
        g = g.sort_values("data")
        r = g["valor_cota"].pct_change().dropna()
        if len(r) < janela:
            continue
        c = _cdi_do_intervalo(cdi, g["data"])
        par = pd.DataFrame({"r": r, "c": c, "data": g["data"]}).dropna()
        par = par[par["r"].abs() <= CORTE_QUEBRA]
        if len(par) < max(janela, min_obs):
            continue

        # Retorno acumulado na janela, anualizado -- para o fundo e para o CDI.
        # `log1p`/`expm1` em vez de `rolling().apply(prod)`: a versao com apply
        # roda em Python puro por janela e leva minutos nas 120 series.
        fundo_aa = np.expm1(
            np.log1p(par["r"]).rolling(janela).sum() * (BASE_ANO / janela))
        cdi_aa = np.expm1(
            np.log1p(par["c"]).rolling(janela).sum() * (BASE_ANO / janela))
        # Vol EWMA, a MESMA do numero unico e a mesma que a tabela exibe. Com
        # desvio simples aqui, o grafico e a tabela discordariam por metodo, e
        # nao so por janela -- e ninguem teria como saber qual acreditar.
        vol_aa = _vol_ewma_movel(par["r"].to_numpy(), janela, lam) * np.sqrt(BASE_ANO)
        vol_aa = pd.Series(vol_aa, index=par.index)
        # Vol nula na janela devolveria divisao por zero; virar NaN e o certo --
        # ali nao ha Sharpe definido, e nao um Sharpe infinito.
        s = ((fundo_aa - cdi_aa) / vol_aa.where(vol_aa > 1e-12)).dropna()
        if s.empty:
            continue
        saida.append(pd.DataFrame({
            "cnpj_fundo": cnpj, "classe": classe,
            "data": par["data"].reindex(s.index), "sharpe": s.to_numpy(),
        }))

    if not saida:
        return pd.DataFrame(columns=["cnpj_fundo", "classe", "data", "sharpe"])
    return pd.concat(saida, ignore_index=True)


def retorno_vs_var(
    serie: pd.DataFrame,
    nivel: float = NIVEL,
    lam: float = LAMBDA,
    min_obs: int = MIN_OBS,
) -> pd.DataFrame:
    """Retorno de cada dia ao lado do VaR que valia para AQUELE dia.

    A tabela de VaR entrega um numero so por classe, estimado com a janela
    inteira. Ele nao responde a pergunta que a area de risco de fato faz: nos
    dias em que o fundo perdeu, a perda cabia no limite que estava publicado na
    vespera? Aqui cada data tem o seu proprio VaR, e a comparacao com o retorno
    realizado vira contagem de excecoes -- o teste de aderencia do modelo.

    O VaR do dia `t` sai da volatilidade estimada ate `t-1`, nunca ate `t`. Sem
    esse deslocamento a perda do dia entra na sua propria previsao: a EWMA
    engorda no mesmo ponto em que o retorno cai, o limite acompanha a queda e a
    excecao desaparece do desenho. O modelo pareceria perfeito justamente onde
    falhou.

    A vol e EWMA EXPANDIDA -- todo o historico ate a vespera, com peso
    exponencial -- e nao janela movel: e a mesma formula do numero unico da
    tabela, so que reestimada a cada dia. Duas formulas para o mesmo nome
    deixariam o leitor sem saber qual acreditar.

    O ultimo ponto da linha NAO e o numero da tabela, e nao deveria ser: a
    tabela estima com a janela inteira, incluindo o ultimo retorno, entao o que
    ela publica e o limite que vale para o PROXIMO dia util -- o ponto seguinte
    desta mesma linha, que ainda nao tem retorno com que ser confrontado. Em
    serie que anda por accrual e salta num dia isolado os dois chegam a divergir
    por ordens de grandeza, e isso e o dado falando: o salto do ultimo dia entra
    inteiro na estimativa da tabela e nao entra na previsao que existia antes
    dele.

    Devolve uma linha por (cnpj_fundo, classe, data) com:
      - `retorno`: o retorno realizado do dia, sem as quebras de cadastro;
      - `var_1d_pct`: a PERDA limite prevista para o dia, positiva, NaN durante
        os `min_obs` dias de aquecimento em que ainda nao ha estimativa;
      - `excedeu`: o retorno furou o limite (so tem sentido onde ha VaR).

    Classe sem `min_obs` retornos uteis fica de fora, igual a `metricas`: o
    grafico nao pode mostrar serie que a tabela ao lado nao mostra.
    """
    colunas = ["cnpj_fundo", "classe", "data", "retorno", "var_1d_pct", "excedeu"]
    if serie.empty:
        return pd.DataFrame(columns=colunas)

    z = float(abs(np.sqrt(2) * _erfinv(2 * nivel - 1))) if nivel != NIVEL else Z
    saida = []
    for (cnpj, classe), g in serie.groupby(["cnpj_fundo", "classe"]):
        g = g.sort_values("data")
        par = pd.DataFrame({
            "data": g["data"],
            "r": g["valor_cota"].pct_change(),
        }).dropna()
        # Quebra de cadastro fora, pelo mesmo motivo de sempre: +58 milhoes por
        # cento nao e perda de mercado, e sozinha reescalaria o eixo inteiro.
        par = par[par["r"].abs() <= CORTE_QUEBRA]
        if len(par) < min_obs:
            continue

        r = par["r"].to_numpy()
        vol = _vol_ewma_expandida(r, min_obs, lam)
        # O deslocamento de um dia e o que torna isto uma previsao: o primeiro
        # ponto fica sem VaR porque nao existe vespera antes dele.
        var = z * np.concatenate(([np.nan], vol[:-1]))

        with np.errstate(invalid="ignore"):
            excedeu = r < -var
        saida.append(pd.DataFrame({
            "cnpj_fundo": cnpj, "classe": classe,
            "data": par["data"].to_numpy(),
            "retorno": r,
            "var_1d_pct": var,
            "excedeu": np.where(np.isnan(var), False, excedeu),
        }))

    if not saida:
        return pd.DataFrame(columns=colunas)
    return pd.concat(saida, ignore_index=True)


def aderencia(pontos: pd.DataFrame, nivel: float = NIVEL) -> dict:
    """Quantas excecoes houve contra quantas o modelo admite.

    A `NIVEL` de 95%, o VaR promete ser furado em 5% dos dias -- excecao nao e
    defeito, e o preco do quantil. Defeito e a frequencia observada nao bater
    com a prometida, e ai o sinal importa: acima, o VaR subestima a perda;
    muito abaixo, ele esta inflado e imobiliza limite a toa.

    A banda de tolerancia e o intervalo binomial de dois desvios em torno do
    esperado. Com 250 dias a 95%, ela vai de ~6 a ~19 excecoes: fora dai o
    desvio deixa de caber em acaso amostral.
    """
    if pontos.empty:
        return {}
    com_var = pontos[pontos["var_1d_pct"].notna()]
    n = len(com_var)
    if not n:
        return {}
    obs = int(com_var["excedeu"].sum())
    p = 1 - nivel
    esperado = p * n
    desvio = float(np.sqrt(n * p * (1 - p)))
    if obs > esperado + 2 * desvio:
        estado, leitura = "ruim", ("furos acima do previsto — o VaR está "
                                   "subestimando a perda")
    elif obs < esperado - 2 * desvio:
        estado, leitura = "nd", ("furos abaixo do previsto — o VaR está "
                                 "conservador para esta série")
    else:
        estado, leitura = "bom", "furos dentro do previsto para o nível"
    return {
        "obs_com_var": n,
        "excecoes": obs,
        "taxa": obs / n,
        "esperado": esperado,
        "taxa_esperada": p,
        "banda": (max(0.0, esperado - 2 * desvio), esperado + 2 * desvio),
        "estado": estado,
        "leitura": leitura,
    }


def _erfinv(x: float) -> float:
    """Inversa da funcao erro, para nivel de confianca diferente do padrao."""
    from math import log as ln
    from math import sqrt

    a = 0.147
    ln1 = ln(1 - x * x)
    t1 = 2 / (np.pi * a) + ln1 / 2
    return float(np.sign(x) * sqrt(sqrt(t1 * t1 - ln1 / a) - t1))


def confiabilidade(linha) -> tuple[str, str]:
    """Classifica o quanto o numero parametrico merece credito.

    Devolve (classe_css, explicacao). Nao e enfeite: em 2026-07-31 so 10 das
    113 series tinham parametrico coerente com o que a serie de fato fez, e
    exibir os outros 103 sem ressalva seria apresentar ruido como medida.
    """
    razao = linha.get("razao_hist")
    if linha.get("accrual"):
        return "nd", ("cota não marcada a mercado — sobe por accrual, então a "
                      "volatilidade não mede risco")
    # VaR historico negativo quer dizer que o percentil 5% dos retornos ainda
    # foi GANHO: a serie praticamente nao cai. Nesse caso nao ha perda observada
    # com que comparar, e a razao entre os dois nao significa nada.
    if pd.isna(razao) or linha.get("var_hist_pct", 0) <= 0:
        return "nd", ("no período, mesmo o 5º pior dia foi de ganho — não há "
                      "perda observada para aferir o paramétrico")
    if razao > BANDA_RAZAO:
        return "ruim", ("cauda pesada: o histórico é maior que o paramétrico, "
                        "que portanto SUBESTIMA a perda")
    if razao < 1 / 10:
        return "nd", ("dominado por salto isolado: o paramétrico é muito maior "
                      "que qualquer perda observada")
    if razao < 1 / BANDA_RAZAO:
        return "nd", "paramétrico acima do observado — leia com reserva"
    return "bom", "paramétrico coerente com o histórico observado"


# Acima disso o Sharpe deixou de medir qualidade de gestao e passou a medir a
# ausencia de oscilacao no denominador. Um fundo de credito que sobe 4% acima do
# CDI com vol de 0,08% devolve Sharpe na casa das dezenas -- nao porque seja
# excepcional, mas porque a cota nao e marcada a mercado.
SHARPE_IMPLAUSIVEL = 5.0


def leitura_sharpe(linha) -> tuple[str, str]:
    """O Sharpe desta classe merece credito?

    Sharpe e retorno excedente dividido por volatilidade. Quando a cota anda por
    accrual, o denominador tende a zero e a razao explode -- o numero fica
    enorme justamente onde a medida perde sentido, que e o pior tipo de erro
    porque parece um resultado otimo.
    """
    s = linha.get("sharpe")
    if pd.isna(s):
        return "nd", "sem CDI suficiente na janela para calcular"
    if linha.get("accrual"):
        return "nd", ("cota não marcada a mercado: o denominador tende a zero e "
                      "infla o índice — não compare com fundo marcado")
    if abs(s) > SHARPE_IMPLAUSIVEL:
        return "nd", (f"acima de {SHARPE_IMPLAUSIVEL:g} o índice reflete a falta de "
                      f"oscilação da cota, não a qualidade do retorno")
    if s < 0:
        return "ruim", "rendeu abaixo do CDI no período"
    return "bom", "retorno acima do CDI com volatilidade compatível"
