"""Mapa do datalake: onde estao as tabelas que a liquidez ainda le do riscogc.

`python -m liquidez datalake mapear`. So SELECT, e so metadados (sys.* do
datalake) e agregados: nenhuma linha de cotista ou de sacado sai na tela, so
nomes de tabela, de coluna, de fundo e de classe, datas e contagens.

Carteira, passivo e cotas ja vem do datalake (`tabelas.py`). Faltam o estoque,
o cadastro de fundos e a movimentacao (HOD_*), e as tabelas que nunca foram
HOD (Beyond, curva, CDI, feriados, distribuidores, de-para do Beyond). Nenhum
repositorio tem as colunas das MUL_GLD_* novas (ESTOQUE, COTISTAS,
COTISTAS_ADM, AQUISICOES), entao o mapa as levanta para a troca seguinte:

  1. conexao: servidor, banco, login, acesso e collation dos dois bancos;
  2. inventario das MUL_* e DLK_* (tipo, linhas, sinonimo);
  3. colunas de cada uma;
  4. datas: primeira, ultima e linhas no ultimo dia (so com indice na data
     ou tabela pequena; tabela grande sem indice nao e varrida);
  5. candidatas por nome, em qualquer prefixo, para cada tabela do riscogc;
  6. colunas com os nomes que essas tabelas usam (ISIN, Recebivel...);
  7. cotas: fundos do cadastro local com e sem serie na MEC;
  8. passivo: as colunas do proprio passivo (docFundo, dsClasse) dao conta
     do que hoje vem da HOD_TB_CADASTRO_FUNDOS?

Cada parte que falhar (permissao, coluna que nao existe) vira uma linha
"(falhou: ...)" e o mapa segue.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable

import pandas as pd

from . import db
from .queries import PASSIVO_ADMINISTRADORES
from .tabelas import BANCO_DATALAKE, PASSIVO

log = logging.getLogger(__name__)

DL = BANCO_DATALAKE

# Tabela maior que isso so tem as datas lidas se a coluna de data for a
# primeira chave de um indice (MIN/MAX viram busca no indice).
LIMITE_VARREDURA = 2_000_000

FILTRO_MUL_DLK = "(o.name LIKE 'MUL[_]%' OR o.name LIKE 'DLK[_]%')"

# Coluna de data que diz "de quando e a posicao", na ordem de preferencia.
# As de auditoria (dhInsercao, dhAlteracao, dtCarga) nao servem.
DATAS_PREFERIDAS = ("dtreferencia", "dtposicao", "dataposicao", "data_posicao", "data",
                    "dtmovimento", "data_movimentacao", "dtmovimentacao")
DATAS_AUDITORIA = ("dtcarga", "dtinsercao", "dtalteracao", "dtatualizacao")
TIPOS_DATA = ("date", "datetime", "datetime2", "smalldatetime")

# Copia, backup e teste nao sao fonte: ficam fora das candidatas.
DESCARTE = ("BKP", "BACKUP", "LEGADO", "_OLD", "TMP", "TEMP", "TESTE", "_STG")

# Tabela do riscogc -> pedacos de NOME de tabela que a denunciam no datalake.
CANDIDATAS = (
    ("estoque (HOD_TB_ESTOQUE_UNIFICADO)", ("ESTOQUE", "RECEBIV")),
    ("cadastro de fundos (HOD_TB_CADASTRO_FUNDOS)", ("CADASTRO", "FUNDO", "CLASSE")),
    ("movimentacao (HOD_MCT_REU_MOVIMENTACAO_PROCESSADA)", ("MOVIMENT", "RESGATE", "APLICAC")),
    ("Beyond (TB_LIQUIDEZ_OPERACOES_4)", ("BEYOND", "BYD", "OPERAC")),
    ("curva DI1 (TB_CURVA_JUROS_DIARIA)", ("CURVA", "JUROS", "DI1", "VERTICE")),
    ("CDI (TB_TAXAS)", ("TAXA", "CDI", "INDICADOR")),
    ("feriados (AUD_DIM_FERIADOS)", ("FERIADO", "CALENDAR")),
    ("distribuidores (TB_PASSIVO_DISTRIBUIDORES)", ("DISTRIBU",)),
    ("de-para do Beyond (AUD_DIM_DEPARA_FUNDOS)", ("DEPARA", "DE_PARA")),
)

# Tabela do riscogc -> pedacos de NOME DE COLUNA que ela usa (sem "_", maiusculo).
COLUNAS_CHAVE = (
    ("estoque", ("RECEBIV", "SACADO", "PRESENTE", "PDD", "CEDENTE")),
    ("cadastro de fundos", ("ISIN", "CONDOMIN", "LIQUIDACAO", "CARENCIA", "COTIZA", "DURACAO",
                            "TIPOCOTA", "CLASSE")),
    ("movimentacao", ("RESGATE", "MOVIMENT", "APLICACAO")),
    ("curva", ("FATOR", "VERTICE", "MERCADORIA")),
    ("CDI", ("CDI", "SELIC")),
    ("feriados", ("FERIADO", "DIAUTIL")),
)
MAX_POR_PADRAO = 30
MAX_COLUNAS = 40


def _q(nome: str) -> str:
    """Nome entre colchetes, com `]` escapado."""
    return "[" + str(nome).replace("]", "]]") + "]"


def _primeira_linha(exc: Exception) -> str:
    return (str(exc).splitlines() or [""])[0][:120]


def _nulo(v) -> bool:
    return v is None or pd.isna(v)


def _int(v) -> int:
    return 0 if _nulo(v) else int(v)


def _descartar(nome: str) -> bool:
    alto = str(nome).upper()
    return any(d in alto for d in DESCARTE)


def tipo_coluna(tipo: str, tamanho, precisao, escala) -> str:
    """varchar(50), decimal(38,8), datetime2(7)... como no DDL."""
    tipo = str(tipo)
    n = _int(tamanho)
    if tipo in ("varchar", "char", "varbinary", "binary"):
        return f"{tipo}({'max' if n == -1 else n})"
    if tipo in ("nvarchar", "nchar"):
        return f"{tipo}({'max' if n == -1 else n // 2})"
    if tipo in ("decimal", "numeric"):
        return f"{tipo}({_int(precisao)},{_int(escala)})"
    if tipo in ("datetime2", "time", "datetimeoffset"):
        return f"{tipo}({_int(escala)})"
    return tipo


def escolher_data(colunas: pd.DataFrame) -> str | None:
    """Coluna de data da posicao, pela lista de preferencia; senao a primeira
    de tipo data que comece com dt/data e nao seja de auditoria."""
    datas = colunas[colunas["tipo"].isin(TIPOS_DATA)]
    nomes = {str(c).lower(): str(c) for c in datas["coluna"]}
    for preferida in DATAS_PREFERIDAS:
        if preferida in nomes:
            return nomes[preferida]
    for baixo, original in nomes.items():
        if baixo.startswith(("dt", "data")) and baixo not in DATAS_AUDITORIA:
            return original
    return None


# --- SQL do catalogo (tudo no banco datalake, em tres partes) -----------------

SQL_CONEXAO = (
    "SELECT @@SERVERNAME AS servidor, DB_NAME() AS banco, SUSER_SNAME() AS usuario,"
    " HAS_DBACCESS(?) AS acesso_datalake,"
    " CONVERT(VARCHAR(128), DATABASEPROPERTYEX(DB_NAME(), 'Collation')) AS collation_banco,"
    " CONVERT(VARCHAR(128), DATABASEPROPERTYEX(?, 'Collation')) AS collation_datalake"
)

SQL_OBJETOS = f"""
SELECT o.name AS objeto, s.name AS esquema, o.type AS tipo,
       CONVERT(VARCHAR(10), o.create_date, 23) AS criado,
       CONVERT(VARCHAR(10), o.modify_date, 23) AS alterado,
       (SELECT SUM(p.rows) FROM {DL}.sys.partitions AS p
         WHERE p.object_id = o.object_id AND p.index_id IN (0, 1)) AS linhas,
       sy.base_object_name AS sinonimo_de
FROM {DL}.sys.objects AS o
JOIN {DL}.sys.schemas AS s ON s.schema_id = o.schema_id
LEFT JOIN {DL}.sys.synonyms AS sy ON sy.object_id = o.object_id
WHERE o.type IN ('U', 'V', 'SN')
ORDER BY o.name
"""

SQL_COLUNAS = f"""
SELECT o.name AS objeto, c.column_id AS ordem, c.name AS coluna, t.name AS tipo,
       c.max_length AS tamanho, c.precision AS precisao, c.scale AS escala,
       c.is_nullable AS nula
FROM {DL}.sys.columns AS c
JOIN {DL}.sys.objects AS o ON o.object_id = c.object_id
JOIN {DL}.sys.types AS t ON t.user_type_id = c.user_type_id
WHERE o.type IN ('U', 'V')
ORDER BY o.name, c.column_id
"""

# Primeira coluna de cada indice das MUL_/DLK_: MIN/MAX nela e busca, nao varredura.
SQL_INDICES = f"""
SELECT DISTINCT o.name AS objeto, c.name AS coluna
FROM {DL}.sys.index_columns AS ic
JOIN {DL}.sys.indexes AS i ON i.object_id = ic.object_id AND i.index_id = ic.index_id
JOIN {DL}.sys.columns AS c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
JOIN {DL}.sys.objects AS o ON o.object_id = ic.object_id
WHERE ic.key_ordinal = 1 AND i.is_disabled = 0 AND i.type IN (1, 2)
  AND o.type = 'U' AND {FILTRO_MUL_DLK}
"""

# Passivo na ultima data: o que as colunas dele dizem de cada classe (cdFundo),
# por administrador (o mesmo codigo pode existir em dois).
SQL_PASSIVO_CLASSES = f"""
SELECT LTRIM(RTRIM(m.cdFundo)) AS cdFundo,
       UPPER(LTRIM(RTRIM(m.dsAdministrador))) AS administrador,
       MIN(m.docFundo) AS doc_min, MAX(m.docFundo) AS doc_max,
       MAX(m.dsNomeFundo) AS dsNomeFundo,
       MIN(m.dsClasse) AS classe_min, MAX(m.dsClasse) AS classe_max,
       MAX(m.dtReferencia) AS data, COUNT(*) AS linhas
FROM {PASSIVO} AS m
WHERE m.dtReferencia = (SELECT MAX(dtReferencia) FROM {PASSIVO})
GROUP BY LTRIM(RTRIM(m.cdFundo)), UPPER(LTRIM(RTRIM(m.dsAdministrador)))
"""

SQL_CADASTRO_ADM = (
    "SELECT idFundoAdm, CNPJFundo, nmFundoInterno, nmCotaInterno, Situacao"
    " FROM dbo.HOD_TB_CADASTRO_FUNDOS WHERE idFundoAdm IS NOT NULL"
)


# --- partes ---------------------------------------------------------------------

def _mul_dlk(objetos: pd.DataFrame) -> pd.DataFrame:
    nomes = objetos["objeto"].astype(str).str.upper()
    return objetos[nomes.str.startswith(("MUL_", "DLK_"))]


def parte_conexao(consultar: Callable) -> list[str]:
    c = consultar(SQL_CONEXAO, (DL, DL)).iloc[0]
    return [
        f"servidor ............ {c['servidor']}",
        f"banco da conexao .... {c['banco']}  (collation {c['collation_banco']})",
        f"login ............... {c['usuario']}",
        f"banco datalake ...... {DL}  (acesso: {'SIM' if _int(c['acesso_datalake']) == 1 else 'NAO'};"
        f" collation {c['collation_datalake']})",
    ]


def parte_inventario(objetos: pd.DataFrame) -> list[str]:
    alvo = _mul_dlk(objetos)
    if alvo.empty:
        return ["(nenhum objeto MUL_ ou DLK_ visivel para este login)"]
    linhas = [f"{'OBJETO':<44} {'TIPO':<4} {'LINHAS':>13}  CRIADO     ALTERADO   SINONIMO DE"]
    for _, o in alvo.iterrows():
        n = "" if _nulo(o["linhas"]) else f"{_int(o['linhas']):,}".replace(",", ".")
        linhas.append(f"{o['objeto']:<44} {str(o['tipo']).strip():<4} {n:>13}  {o['criado']} {o['alterado']}"
                      f"  {o['sinonimo_de'] if isinstance(o['sinonimo_de'], str) else ''}")
    linhas.append(f"{len(alvo)} objeto(s)")
    return linhas


def parte_colunas(objetos: pd.DataFrame, colunas: pd.DataFrame) -> list[str]:
    linhas = []
    for nome in _mul_dlk(objetos)["objeto"]:
        c = colunas[colunas["objeto"] == nome]
        if c.empty:
            continue
        descr = [f"{r['coluna']} {tipo_coluna(r['tipo'], r['tamanho'], r['precisao'], r['escala'])}"
                 + ("" if _int(r["nula"]) else " NOT NULL") for _, r in c.iterrows()]
        linhas.append(f"{nome} ({len(c)} colunas):")
        linhas.append("    " + ", ".join(descr))
    return linhas or ["(sem colunas visiveis)"]


def parte_datas(consultar: Callable, objetos: pd.DataFrame, colunas: pd.DataFrame,
                indices: pd.DataFrame) -> list[str]:
    indexadas = {(str(r["objeto"]), str(r["coluna"]).lower()) for _, r in indices.iterrows()}
    linhas = [f"{'OBJETO':<44} {'COLUNA':<20} {'DE':<10} {'ATE':<10} {'DATAS':>6} {'NO ULTIMO DIA':>14}"]
    for _, o in _mul_dlk(objetos).iterrows():
        nome, tipo = str(o["objeto"]), str(o["tipo"]).strip()
        if tipo != "U":
            continue
        col = escolher_data(colunas[colunas["objeto"] == nome])
        if col is None:
            linhas.append(f"{nome:<44} (sem coluna de data de posicao)")
            continue
        n = _int(o["linhas"])
        indexada = (nome, col.lower()) in indexadas
        pequena = n <= LIMITE_VARREDURA
        if not (indexada or pequena):
            linhas.append(f"{nome:<44} {col:<20} (pulada: {n:,} linhas e {col} sem indice)".replace(",", "."))
            continue
        tabela = f"{DL}.{_q(o['esquema'])}.{_q(nome)}"
        c = _q(col)
        try:
            # Tudo numa consulta e sem devolver a data como parametro: o driver
            # legado do Windows entrega date/datetime2 como texto.
            distintas = f"COUNT(DISTINCT {c})" if pequena else "NULL"
            r = consultar(
                f"SELECT MIN({c}) AS de, MAX({c}) AS ate, {distintas} AS datas,"
                f" (SELECT COUNT(*) FROM {tabela} WITH (NOLOCK)"
                f"   WHERE {c} = (SELECT MAX({c}) FROM {tabela} WITH (NOLOCK))) AS ultimo"
                f" FROM {tabela} WITH (NOLOCK)").iloc[0]
            de = "" if _nulo(r["de"]) else str(r["de"])[:10]
            ate = "" if _nulo(r["ate"]) else str(r["ate"])[:10]
            datas = "" if _nulo(r["datas"]) else str(_int(r["datas"]))
            ult = "" if _nulo(r["ultimo"]) else f"{_int(r['ultimo']):,}".replace(",", ".")
            linhas.append(f"{nome:<44} {col:<20} {de:<10} {ate:<10} {datas:>6} {ult:>14}")
        except Exception as exc:  # noqa: BLE001 - uma tabela nao derruba o mapa
            linhas.append(f"{nome:<44} {col:<20} (falhou: {_primeira_linha(exc)})")
    return linhas


def parte_candidatas(objetos: pd.DataFrame, colunas: pd.DataFrame) -> list[str]:
    linhas = []
    nomes = objetos[~objetos["objeto"].map(_descartar)]
    for alvo, pedacos in CANDIDATAS:
        achadas = nomes[nomes["objeto"].astype(str).str.upper().map(
            lambda n: any(p in n for p in pedacos))]
        linhas.append(f"-- {alvo}: {len(achadas)} candidata(s) por nome ({', '.join(pedacos)})")
        for _, o in achadas.iterrows():
            nome = str(o["objeto"])
            n = "" if _nulo(o["linhas"]) else f"{_int(o['linhas']):,} linhas".replace(",", ".")
            sin = f" -> {o['sinonimo_de']}" if isinstance(o["sinonimo_de"], str) else ""
            linhas.append(f"   {nome} [{str(o['tipo']).strip()}] {n}{sin}")
            if nome.upper().startswith(("MUL_", "DLK_")):
                continue                     # colunas ja estao na parte 3
            c = colunas[colunas["objeto"] == nome]["coluna"].astype(str).tolist()
            if c:
                resto = f" (+{len(c) - MAX_COLUNAS})" if len(c) > MAX_COLUNAS else ""
                linhas.append("      " + ", ".join(c[:MAX_COLUNAS]) + resto)
    return linhas


def parte_colunas_chave(colunas: pd.DataFrame) -> list[str]:
    linhas = []
    c = colunas[~colunas["objeto"].map(_descartar)]
    normal = c["coluna"].astype(str).str.upper().str.replace("_", "", regex=False)
    for alvo, pedacos in COLUNAS_CHAVE:
        linhas.append(f"-- {alvo}")
        for pedaco in pedacos:
            achadas = c[normal.str.contains(pedaco, regex=False)]
            pares = sorted({f"{o}.{col}" for o, col in zip(achadas["objeto"], achadas["coluna"])})
            resto = f" (+{len(pares) - MAX_POR_PADRAO})" if len(pares) > MAX_POR_PADRAO else ""
            linhas.append(f"   {pedaco:<11} {', '.join(pares[:MAX_POR_PADRAO]) or '-'}{resto}")
    return linhas


def parte_cotas(fichas: Iterable, ate) -> list[str]:
    """Fundos do cadastro local com e sem serie na MEC (via de-para e cadastro)."""
    from . import varcota as vc

    fichas = list(fichas)
    if not fichas:
        return ["(cadastro local vazio: rode `configurar --aplicar` para ter as fichas)"]
    serie = vc.extrair(ate)
    linhas = [f"Janela de {vc.JANELA_DIAS} dias ate {ate}: {len(serie)} linha(s), "
              f"{serie['cnpj_fundo'].nunique() if not serie.empty else 0} fundo(s) com serie."]
    com, sem = [], []
    for f in sorted(fichas, key=lambda f: f.nome_interno):
        s = serie[serie["cnpj_fundo"] == f.cnpj_numerico] if not serie.empty else serie
        if s.empty:
            sem.append(f"   {f.nome_interno[:40]:<40} {f.cnpj}")
            continue
        classes = ", ".join(sorted(s["classe"].astype(str).unique()))
        com.append(f"   {f.nome_interno[:40]:<40} {f.cnpj}  {s['data'].min():%d/%m/%Y} a"
                   f" {s['data'].max():%d/%m/%Y}  {s['data'].nunique():>4} dia(s)  {classes}")
    linhas.append(f"COM serie ({len(com)}):")
    linhas += com or ["   -"]
    linhas.append(f"SEM serie ({len(sem)}):")
    linhas += sem or ["   -"]
    return linhas


def _digitos(v) -> str:
    return "" if _nulo(v) else re.sub(r"\D", "", str(v))


def parte_passivo(consultar: Callable) -> list[str]:
    """As colunas do passivo dao conta do cadastro? So agregados por classe."""
    p = consultar(SQL_PASSIVO_CLASSES)
    if p.empty:
        return ["(passivo vazio)"]
    cad = consultar(SQL_CADASTRO_ADM)
    cad["idFundoAdm"] = cad["idFundoAdm"].astype(str).str.strip()
    cad = cad.drop_duplicates("idFundoAdm").set_index("idFundoAdm")
    p["cdFundo"] = p["cdFundo"].astype(str).str.strip()
    p["usado"] = p["administrador"].isin(PASSIVO_ADMINISTRADORES)
    p = p.join(cad, on="cdFundo")
    p["no_cadastro"] = p["CNPJFundo"].notna()
    p["doc"] = p["doc_max"].map(_digitos)
    p["cnpj_cad"] = p["CNPJFundo"].map(_digitos)

    usados = p[p["usado"]]
    data = str(p["data"].max())[:10]
    linhas = [
        f"Ultima data do passivo: {data}. Classes (cdFundo x administrador): {len(p)}, das quais"
        f" {len(usados)} de {'/'.join(PASSIVO_ADMINISTRADORES)} (as que a liquidez le).",
        "Por administrador: " + ", ".join(f"{a} {n}" for a, n in p.groupby("administrador").size().items()),
        f"Nas usadas: com linha no cadastro {int(usados['no_cadastro'].sum())};"
        f" sem {int((~usados['no_cadastro']).sum())} (ficam fora do join de hoje).",
    ]
    vazio = (usados["doc"] == "").sum()
    varia = (usados["doc_min"].map(_digitos) != usados["doc"]).sum()
    casa = usados[usados["no_cadastro"]]
    igual = (casa["doc"] == casa["cnpj_cad"]).sum()
    linhas.append(f"docFundo: vazio em {int(vazio)}; muda dentro da classe em {int(varia)};"
                  f" igual ao CNPJFundo do cadastro em {int(igual)} de {len(casa)}.")
    diverge = casa[casa["doc"] != casa["cnpj_cad"]]
    for _, r in diverge.head(20).iterrows():
        doc = r["doc_max"] if isinstance(r["doc_max"], str) else "-"
        linhas.append(f"   cdFundo {r['cdFundo']:<12} docFundo {doc:<20}"
                      f" cadastro {r['CNPJFundo']} ({str(r['nmFundoInterno'])[:30]})")
    if len(diverge) > 20:
        linhas.append(f"   (+{len(diverge) - 20} classe(s))")
    linhas.append("Classe no cadastro (nmCotaInterno, Situacao) x dsClasse do passivo, classes usadas:")
    pares = (casa.assign(dsClasse=casa["classe_max"].fillna("-"),
                         situacao=casa["Situacao"].fillna("-"),
                         nmCotaInterno=casa["nmCotaInterno"].fillna("-"))
             .groupby(["nmCotaInterno", "situacao", "dsClasse"]).size()
             .sort_values(ascending=False))
    for (cota, situacao, classe), n in list(pares.items())[:40]:
        linhas.append(f"   {str(cota)[:28]:<28} {str(situacao)[:10]:<10} {str(classe)[:40]:<40} {n:>4}")
    if len(pares) > 40:
        linhas.append(f"   (+{len(pares) - 40} par(es))")
    return linhas


# --- execucao ---------------------------------------------------------------------

def mapear(fichas: Iterable = (), ate=None) -> list[str]:
    """Roda as oito partes e devolve o texto, linha a linha."""
    consultar = db.consultar
    ate = ate or pd.Timestamp.today().date().isoformat()
    saida: list[str] = []

    def parte(numero: int, titulo: str, funcao: Callable[[], list[str]]) -> None:
        saida.append("")
        saida.append(f"== {numero}. {titulo}")
        try:
            saida.extend(funcao())
        except Exception as exc:  # noqa: BLE001 - uma parte nao derruba o mapa
            log.debug("parte %d falhou", numero, exc_info=True)
            saida.append(f"(falhou: {_primeira_linha(exc)})")

    vazio = pd.DataFrame(columns=["objeto", "esquema", "tipo", "criado", "alterado", "linhas",
                                  "sinonimo_de", "ordem", "coluna", "tamanho", "precisao",
                                  "escala", "nula"])
    cat = {"objetos": vazio, "colunas": vazio, "indices": pd.DataFrame(columns=["objeto", "coluna"])}

    def catalogo() -> list[str]:
        cat["objetos"] = consultar(SQL_OBJETOS)
        cat["colunas"] = consultar(SQL_COLUNAS)
        cat["indices"] = consultar(SQL_INDICES)
        tipos = cat["objetos"]["tipo"].astype(str).str.strip().value_counts()
        return [f"{len(cat['objetos'])} objeto(s) no {DL}: "
                + ", ".join(f"{t} {n}" for t, n in tipos.items())
                + f"; {len(cat['colunas'])} coluna(s) em tabelas e views."]

    parte(1, "conexao", lambda: parte_conexao(consultar))
    parte(2, f"inventario MUL_/DLK_ do {DL}", lambda: catalogo() + parte_inventario(cat["objetos"]))
    parte(3, "colunas das MUL_/DLK_", lambda: parte_colunas(cat["objetos"], cat["colunas"]))
    parte(4, "datas das MUL_/DLK_ (coluna de posicao)",
          lambda: parte_datas(consultar, cat["objetos"], cat["colunas"], cat["indices"]))
    parte(5, "candidatas por nome, qualquer prefixo (sem copias e backups)",
          lambda: parte_candidatas(cat["objetos"], cat["colunas"]))
    parte(6, "colunas com os nomes que a liquidez usa", lambda: parte_colunas_chave(cat["colunas"]))
    parte(7, "cotas: fundos do cadastro local x serie na MEC", lambda: parte_cotas(fichas, ate))
    parte(8, "passivo: colunas proprias x HOD_TB_CADASTRO_FUNDOS", lambda: parte_passivo(consultar))
    return saida
