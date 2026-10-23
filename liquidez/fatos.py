"""Camada de fatos para BI -- o historico que o relatorio nao guarda.

Os `.xlsx` sao apresentacao: um arquivo por fundo, por data, e a carga anterior e
substituida a cada rodada. Nada acumula, entao nao da para perguntar como o
Caixa Potencial de um fundo evoluiu.

Aqui as mesmas grandezas ja calculadas pelo pipeline sao gravadas em formato
tidy, particionado por data:

    bi/fato_indicadores/data=2026-07-28/dados.parquet
    bi/fato_relatorio/data=2026-07-28/dados.parquet
    ...

Particionar por pasta e o formato que o Power BI le nativamente, e permite
recarregar uma data sem tocar nas demais. Volume e irrisorio -- 97 fundos x 250
dias uteis dao 24 mil linhas de indicadores por ano.

Nada aqui recalcula: tudo vem do que as etapas 6, 8, 9 e os indicadores ja
produziram. Se a regra mudar no Python, o fato muda junto -- e o motivo de o BI
nao reimplementar as etapas em DAX.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

DIR_BI = Path("bi")

TABELAS_POR_DATA = (
    "fato_indicadores",
    "fato_anbima",
    "fato_relatorio",
    "fato_matriz",
    "fato_fluxo",
)
DIMENSOES = ("dim_fundo", "dim_operacao")


def _texto_data(data: dt.date | str) -> str:
    if isinstance(data, dt.date):
        return data.isoformat()
    return str(data)


def caminho_particao(tabela: str, data_posicao, destino: Path = DIR_BI) -> Path:
    return Path(destino) / tabela / f"data={_texto_data(data_posicao)}" / "dados.parquet"


CHAVE_PADRAO = "cnpj_fundo"


def gravar_tabela(
    df: pd.DataFrame,
    tabela: str,
    data_posicao,
    destino: Path = DIR_BI,
    chave: str | None = CHAVE_PADRAO,
) -> Path:
    """Grava a particao da data, substituindo apenas as chaves reprocessadas.

    Reprocessar um fundo so nao pode apagar os outros daquela data. Com `chave`,
    a particao existente e preservada e so as linhas daquelas chaves sao
    trocadas. Com `chave=None`, a particao inteira e substituida.
    """
    caminho = caminho_particao(tabela, data_posicao, destino)
    caminho.parent.mkdir(parents=True, exist_ok=True)

    if chave and caminho.exists() and chave in df.columns:
        anterior = pd.read_parquet(caminho)
        if chave in anterior.columns:
            reprocessadas = set(df[chave])
            preservadas = anterior[~anterior[chave].isin(reprocessadas)]
            if not preservadas.empty:
                df = pd.concat([preservadas, df], ignore_index=True)
                log.info("%s/%s: %d linha(s) de outros fundos preservadas",
                         tabela, _texto_data(data_posicao), len(preservadas))

    df.to_parquet(caminho, index=False)
    return caminho


def gravar_dimensao(df: pd.DataFrame, nome: str, destino: Path = DIR_BI) -> Path:
    """Dimensao e retrato do agora, nao serie: sobrescreve, sem particao."""
    caminho = Path(destino) / nome / "dados.parquet"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(caminho, index=False)
    return caminho


# --- Transformacoes ---------------------------------------------------------

def de_indicadores(indicadores, fundo, data_posicao) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Uma linha por fundo/data, mais a abertura do ANBIMA por horizonte."""
    registro = indicadores.to_dict()
    anbima = registro.pop("anbima", [])
    # `fundo`/`cnpj` do dataclass viram `nome_fundo`/`cnpj_fundo`, o par usado
    # em todas as tabelas -- uma convencao so para o BI relacionar.
    registro.pop("fundo", None)
    registro.pop("cnpj", None)

    registro.update(
        data_posicao=_texto_data(data_posicao),
        cnpj_fundo=fundo.cnpj,
        nome_fundo=fundo.nome_interno,
        administrador=fundo.administrador,
        gestor=fundo.gestor,
        # Cadastral, como administrador e gestor: vem da ficha e nao do calculo.
        # Fica no fato para o painel nao ter de reabrir as 100 fichas so por um
        # rotulo -- e para o BI enxergar a forma junto do resto.
        forma_condominio=fundo.forma_condominio,
        data_constituicao=fundo.data_constituicao,
        situacao_cvm=fundo.situacao_cvm,
        data_inicio_situacao=fundo.data_inicio_situacao,
    )
    df_ind = pd.DataFrame([registro])

    linhas = [
        {
            "data_posicao": _texto_data(data_posicao),
            "cnpj_fundo": fundo.cnpj,
            "nome_fundo": fundo.nome_interno,
            "horizonte_dias": item["horizonte_dias"],
            "percentil_95": item["percentil_95"],
            "enquadrado": item["enquadrado"],
        }
        for item in anbima
    ]
    return df_ind, pd.DataFrame(linhas)


def de_relatorio(relatorio: pd.DataFrame, fundo, data_posicao) -> pd.DataFrame:
    """Bloco por ativo da etapa 9."""
    if relatorio.empty:
        return pd.DataFrame()
    df = relatorio.copy()
    df.insert(0, "data_posicao", _texto_data(data_posicao))
    if "nome_fundo" not in df.columns:
        df["nome_fundo"] = fundo.nome_interno
    return df


def de_matriz(matriz: pd.DataFrame, fundo, data_posicao, tabela: str = "carteira") -> pd.DataFrame:
    """Matriz produto x faixa de prazo (etapas 6 e 8) em formato longo.

    A matriz vem com a faixa no indice e um produto por coluna -- formato de
    planilha. Para BI o util e uma linha por (faixa, produto, valor).
    """
    if matriz is None or matriz.empty:
        return pd.DataFrame()
    longo = (
        matriz.reset_index(names="faixa_prazo")
        .melt(id_vars="faixa_prazo", var_name="produto", value_name="valor")
    )
    # A matriz nasce preenchida de zeros: uma faixa por dia ate o prazo, por
    # produto. Guardar isso todo dia daria dezenas de milhoes de linhas por ano
    # sem informacao -- na escada de liquidez, ausencia E zero.
    longo = longo[longo["valor"].notna() & (pd.to_numeric(longo["valor"], errors="coerce") != 0)]
    # A faixa mistura numero (0, 1, 2...) com rotulo (">29", "Vencidos"): vira
    # texto para o parquet ter tipo unico. `dias_prazo` guarda a parte ordenavel.
    longo["dias_prazo"] = pd.to_numeric(longo["faixa_prazo"], errors="coerce")
    longo["faixa_prazo"] = longo["faixa_prazo"].astype(str)
    longo["produto"] = longo["produto"].astype(str)
    longo["valor"] = pd.to_numeric(longo["valor"], errors="coerce")
    # A matriz traz totais como se fossem mais um produto e mais uma faixa.
    # Sem marcar, um SUM(valor) por fundo contaria tudo em dobro.
    rotulos_totais = {"exposição total", "exposicao total"}
    longo["e_total"] = (
        longo["produto"].str.strip().str.lower().isin(rotulos_totais)
        | longo["faixa_prazo"].str.strip().str.lower().isin(rotulos_totais)
    )
    longo.insert(0, "data_posicao", _texto_data(data_posicao))
    longo.insert(1, "cnpj_fundo", fundo.cnpj)
    longo.insert(2, "nome_fundo", fundo.nome_interno)
    longo.insert(3, "tabela", tabela)
    return longo


def de_fluxo(fluxo: pd.DataFrame, data_posicao) -> pd.DataFrame:
    if fluxo is None or fluxo.empty:
        return pd.DataFrame()
    df = fluxo.copy()
    df.insert(0, "data_posicao", _texto_data(data_posicao))
    return df


def dim_fundo(fundos) -> pd.DataFrame:
    """Retrato do cadastro: o que o BI precisa para filtrar e agrupar."""
    linhas = [
        {
            "cnpj_fundo": f.cnpj,
            "cnpj_numerico": f.cnpj_numerico,
            "nome_fundo": f.nome_interno,
            "administrador": f.administrador,
            "gestor": f.gestor,
            "cnpj_gestor": f.cnpj_gestor,
            "tipo": getattr(f, "tipo_fundo", None),
            "pz_resg": f.pz_resg,
            "cc": f.cc,
            # Cadastrais tambem aqui, e nao so no fato: a dimensao cobre os 100
            # fundos e o fato so os que tem posicao na data. Fundo que saiu da
            # carteira em junho nunca ganharia esses campos pelo fato -- a
            # particao dele e antiga e nao volta a ser gerada.
            "forma_condominio": getattr(f, "forma_condominio", None),
            "data_constituicao": getattr(f, "data_constituicao", None),
            "situacao_cvm": getattr(f, "situacao_cvm", None),
            "data_inicio_situacao": getattr(f, "data_inicio_situacao", None),
            "ativo": getattr(f, "ativo", None),
        }
        for f in fundos
    ]
    return pd.DataFrame(linhas)


def dim_operacao(cronogramas: dict) -> pd.DataFrame:
    """Cadastro contratual das operacoes, de todos os fundos importados."""
    partes = [c.operacoes for c in cronogramas.values() if not c.operacoes.empty]
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()


# --- Inventario -------------------------------------------------------------

def datas_gravadas(tabela: str, destino: Path = DIR_BI) -> list[str]:
    """Datas ja presentes na tabela, lidas dos nomes das pastas."""
    base = Path(destino) / tabela
    if not base.exists():
        return []
    return sorted(
        p.name.split("=", 1)[1] for p in base.iterdir()
        if p.is_dir() and p.name.startswith("data=")
    )


def _linhas_do_parquet(caminho: Path) -> int:
    """Contagem pelos metadados: nao carrega os dados."""
    import pyarrow.parquet as pq

    return pq.ParquetFile(caminho).metadata.num_rows


def resumir(destino: Path = DIR_BI) -> pd.DataFrame:
    """Quantas particoes e quantas linhas cada tabela tem."""
    linhas = []
    for tabela in TABELAS_POR_DATA:
        datas = datas_gravadas(tabela, destino)
        total = 0
        for data in datas:
            caminho = caminho_particao(tabela, data, destino)
            if caminho.exists():
                total += _linhas_do_parquet(caminho)
        linhas.append({
            "tabela": tabela,
            "particoes": len(datas),
            "linhas": total,
            "primeira_data": datas[0] if datas else None,
            "ultima_data": datas[-1] if datas else None,
        })
    for nome in DIMENSOES:
        caminho = Path(destino) / nome / "dados.parquet"
        linhas.append({
            "tabela": nome,
            "particoes": 1 if caminho.exists() else 0,
            "linhas": _linhas_do_parquet(caminho) if caminho.exists() else 0,
            "primeira_data": None,
            "ultima_data": None,
        })
    return pd.DataFrame(linhas)
