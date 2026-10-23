"""As seis consultas migradas do VBA.

O SQL e o mesmo dos modulos `Arq_ant` e `Arq_Download`; a unica mudanca e a
troca da concatenacao de string por parametros vinculados (`?`), que evita
quebra com apostrofo no nome do fundo e injecao de SQL.

Cada funcao devolve `(sql, params)`.
"""

from __future__ import annotations

import datetime as dt

from .tabelas import CARTEIRA, MEC, PASSIVO

Consulta = tuple[str, tuple]


def _data(valor: dt.date | str) -> str:
    """Formata a data como yyyy-mm-dd, igual ao Format(...) do VBA."""
    if isinstance(valor, str):
        return valor
    return valor.strftime("%Y-%m-%d")


def escapar_like(texto: str) -> str:
    """Escapa os curingas do LIKE. Usar sempre junto com ESCAPE '\\'."""
    for caractere in ("\\", "%", "_", "["):
        texto = texto.replace(caractere, "\\" + caractere)
    return texto


# --- BD: estoque unificado (Consultar_Estoque_Unificado) --------------------

def estoque_unificado(cnpj: str, data: dt.date | str) -> Consulta:
    sql = (
        "SELECT * "
        "FROM dbo.HOD_TB_ESTOQUE_UNIFICADO "
        "WHERE CNPJ_Fundo = ? AND Data_Posicao = ?"
    )
    return sql, (cnpj, _data(data))


# --- Carteira: carteira unificada (Consultar_Carteira) ----------------------

def carteira_unificada(cnpj: str, data: dt.date | str) -> Consulta:
    sql = (
        # A MUL_GLD_CARTEIRA_UNIFICADA nomeia as colunas no padrao do Datalake
        # (docFundo, dtReferencia, vlLiquido...). Os apelidos devolvem os nomes
        # da carteira antiga, que o tratamento e a analise ja conhecem -- o
        # de-para vive SO aqui. Sem `dtCarga`: e coluna do clone do riscogc e
        # nao existe na origem do datalake.
        "SELECT cu.dtReferencia AS Data_Posicao, cu.dsNomeFundo AS Nome_Do_Fundo,"
        " cu.docFundo AS CPF_CNPJ, cu.dsBaseOrigem AS Base_Origem, cu.cdOp AS Codigo_Op,"
        " cu.cdAtivo AS Codigo_Ativo, cu.dsNomeAtivo AS Nome_Ativo, cu.dsModalidade AS Modalidade,"
        " cu.dsEmissor AS Emissor, cu.dsStatus AS Status, cu.dtAquisicao AS Data_Aquisicao,"
        " cu.dtVencimento AS Data_Vencimento, cu.vlCustoTotal AS Custo_Total, cu.qtTotal AS Qtd_Total,"
        " cu.vlPuMercado AS PU_Mercado, cu.vlBruto AS Valor_Bruto, cu.vlLiquido AS Valor_Liquido,"
        " cu.txAno AS Taxa_Ano, cu.cdFundo, cu.dsOrigem "
        f"FROM {CARTEIRA} AS cu "
        "WHERE cu.docFundo = ? AND cu.dtReferencia = ?"
    )
    return sql, (cnpj, _data(data))


# --- DB_Ativo: recorte da carteira (CarregarAtivosPassivos) -----------------

def db_ativo(cnpj: str, data: dt.date | str) -> Consulta:
    sql = (
        "SELECT cu.dtReferencia AS Data_Posicao, cu.dsNomeFundo AS Nome_Do_Fundo, cu.docFundo AS CPF_CNPJ,"
        " cu.cdAtivo AS Codigo_Ativo, cu.dsNomeAtivo AS Nome_Ativo, cu.dtVencimento AS Data_Vencimento,"
        " cu.vlLiquido AS Valor_Liquido"
        f" FROM {CARTEIRA} AS cu"
        " WHERE 1 = 1"
        " AND cu.dtReferencia = ?"
        " AND cu.docFundo = ?"
        " ORDER BY cu.vlLiquido DESC"
    )
    return sql, (_data(data), cnpj)


# --- DB_Passivo: posicao de cotistas (CarregarAtivosPassivos) ---------------
#
# Fonte: MUL_GLD_PASSIVO_CONSOLIDADO, lida na origem do datalake desde
# 23/10/2026 (de 21 a 23/10, no clone do riscogc). Desde 10/09/2026 a
# HOD_PAS_POSICAO_NOTA e a HOD_PAS_CAD_COTISTA sao montadas a partir desta mesma
# tabela pela rotina do Datalake (usp_Carregar_HOD_PAS_POSICAO_NOTA), com os
# mesmos codigos. Ler direto tira a etapa intermediaria e pega a correcao de
# nota ja carregada, que a carga da HOD pula (06/10/2026: R$ 0,6 mi de
# diferenca no MULTIPLICA FIDC; em 30/09 as duas batiam em todos os fundos).
# De-para:
#     cdClasseAdm <- cdFundo   cdCotistaAdm <- cdCotista   idNotaAdm <- nrNota
#     dataPosicao <- dtReferencia   vlCorrigido <- vlCorrigido
# As regras da carga entram aqui para o resultado ser o mesmo da HOD:
#   - uma linha por (data, classe, cotista, nota): a consolidada tem nota
#     repetida, e vence a de dhAlteracao mais recente. Somar tudo inflaria o
#     saldo do cotista;
#   - so SINGULARE e PLANNER, que eram os administradores da HOD (BTG e
#     Minicom nao estao no passivo consolidado);
#   - nome e documento do cotista vem da propria linha (antes, do
#     HOD_PAS_CAD_COTISTA).
# O COLLATE DATABASE_DEFAULT e o mesmo da procedure: a consolidada tem a
# collation do datalake, e comparar com a HOD_TB_CADASTRO_FUNDOS e a
# TB_PASSIVO_DISTRIBUIDORES (riscogc) quebraria sem ele.
#
# `PASSIVO_NOTAS` tem um `?` (a data) e entra no FROM como `p`, com as colunas
# que a HOD dava: dataPosicao, cdClasseAdm, nome, doc, doc_digitos, vlCorrigido.
PASSIVO_ADMINISTRADORES = ("SINGULARE", "PLANNER")

PASSIVO_NOTAS = (
    "(SELECT x.dataPosicao, x.cdClasseAdm, x.nome, x.doc, x.doc_digitos, x.vlCorrigido"
    " FROM (SELECT m.dtReferencia AS dataPosicao,"
    " CONVERT(VARCHAR(50), LTRIM(RTRIM(m.cdFundo))) AS cdClasseAdm,"
    " m.dsNomeCotista AS nome, m.docCotista AS doc,"
    " REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(m.docCotista)), '.', ''), '/', ''), '-', '') AS doc_digitos,"
    " m.vlCorrigido,"
    " ROW_NUMBER() OVER (PARTITION BY m.dtReferencia, LTRIM(RTRIM(m.cdFundo)),"
    " LTRIM(RTRIM(m.cdCotista)), LTRIM(RTRIM(m.nrNota))"
    " ORDER BY m.dhAlteracao DESC, m.id DESC) AS rn"
    f" FROM {PASSIVO} AS m"
    " WHERE m.dtReferencia = ?"
    " AND UPPER(LTRIM(RTRIM(m.dsAdministrador))) IN ("
    + ", ".join(f"'{a}'" for a in PASSIVO_ADMINISTRADORES) + ")"
    " AND m.cdFundo IS NOT NULL AND m.cdCotista IS NOT NULL AND m.nrNota IS NOT NULL"
    ") AS x WHERE x.rn = 1)"
)

# Classe da nota -> fundo. `p` e `PASSIVO_NOTAS`, `f` a HOD_TB_CADASTRO_FUNDOS.
PASSIVO_JOIN_FUNDO = (
    "CONVERT(VARCHAR(50), f.idFundoAdm) COLLATE DATABASE_DEFAULT"
    " = p.cdClasseAdm COLLATE DATABASE_DEFAULT"
)

# Distribuidor do cotista (conta e ordem), UMA linha por documento e casando so
# pelos digitos: com o LEFT JOIN direto, documento repetido na tabela de
# distribuidores duplicava a nota e inflava o saldo (diagnostico inicial, 6.6),
# e grafia diferente do documento (com ou sem pontuacao) deixava de casar.
PASSIVO_DISTRIBUIDORES = (
    "(SELECT REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(DocumentoDistribuidor)), '.', ''), '/', ''), '-', '') AS doc_digitos,"
    " MAX(nmDistribuidora) AS nmDistribuidora"
    " FROM dbo.TB_PASSIVO_DISTRIBUIDORES"
    " GROUP BY REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(DocumentoDistribuidor)), '.', ''), '/', ''), '-', ''))"
)
PASSIVO_JOIN_DISTRIBUIDOR = (
    "d.doc_digitos COLLATE DATABASE_DEFAULT = p.doc_digitos COLLATE DATABASE_DEFAULT"
)

# Ultima posicao de passivo (os scripts de passivo recuam para ela).
SQL_PASSIVO_ULTIMA = f"SELECT MAX(dtReferencia) AS u FROM {PASSIVO}"


# Ultima data de cada base que a liquidez le, para o `atualidade`. As do
# datalake sao lidas na origem. As HOD do riscogc sao recarregadas pela rotina
# do projeto Datalake (`python main.py --rodar-clones`) e param quando ela nao
# roda. NOLOCK como no mapa_08 do Datalake, para nao esperar carga em andamento.
SQL_ATUALIDADE = (
    ("carteira (datalake)", f"SELECT MAX(dtReferencia) AS u FROM {CARTEIRA} WITH (NOLOCK)"),
    ("passivo (datalake)", f"SELECT MAX(dtReferencia) AS u FROM {PASSIVO} WITH (NOLOCK)"),
    ("cotas MEC (datalake)", f"SELECT MAX(dtReferencia) AS u FROM {MEC} WITH (NOLOCK)"),
    ("estoque HOD", "SELECT MAX(Data_Posicao) AS u FROM dbo.HOD_TB_ESTOQUE_UNIFICADO WITH (NOLOCK)"),
    ("movimentacao HOD", "SELECT MAX(data_movimentacao) AS u FROM dbo.HOD_MCT_REU_MOVIMENTACAO_PROCESSADA WITH (NOLOCK)"),
    ("Beyond (leitura)", "SELECT MAX(Data) AS u FROM dbo.TB_LIQUIDEZ_OPERACOES_4 WITH (NOLOCK)"),
    ("curva DI1", "SELECT MAX(Data) AS u FROM dbo.TB_CURVA_JUROS_DIARIA WITH (NOLOCK) WHERE Mercadoria = 'DI1'"),
    ("CDI (TB_TAXAS)", "SELECT MAX(Data) AS u FROM dbo.TB_TAXAS WITH (NOLOCK) WHERE Ativo = 'CDI_Over'"),
)


def db_passivo(cnpj: str, data: dt.date | str) -> Consulta:
    sql = (
        "SELECT p.dataPosicao, f.nmFundoInterno, f.CNPJFundo,"
        " p.nome AS nmCotista, p.doc AS docCotista,"
        " d.nmDistribuidora AS nmDistribuidor, SUM(p.vlCorrigido) AS vlSaldoTotal"
        f" FROM {PASSIVO_NOTAS} AS p"
        f" INNER JOIN dbo.HOD_TB_CADASTRO_FUNDOS AS f ON {PASSIVO_JOIN_FUNDO}"
        f" LEFT JOIN {PASSIVO_DISTRIBUIDORES} AS d ON {PASSIVO_JOIN_DISTRIBUIDOR}"
        " WHERE 1 = 1"
        " AND f.CNPJFundo = ?"
        " AND f.Situacao = 'NORMAL'"
        " AND f.nmCotaInterno <> 'ORIGINAL'"
        " GROUP BY p.dataPosicao, f.nmFundoInterno, f.CNPJFundo,"
        " p.nome, p.doc, d.nmDistribuidora"
        " ORDER BY vlSaldoTotal DESC"
    )
    return sql, (_data(data), cnpj)


# --- MOV: movimentacao processada (Consultar_Movimento) ---------------------

def movimentacao(nome_produto: str) -> Consulta:
    """Sem filtro de data, igual ao VBA: filtra so por nome_produto."""
    sql = (
        "SELECT * "
        "FROM dbo.HOD_MCT_REU_MOVIMENTACAO_PROCESSADA "
        "WHERE nome_produto LIKE ? ESCAPE '\\'"
    )
    return sql, (escapar_like(nome_produto) + "%",)


# --- Curva de juros: projecao do fluxo futuro -------------------------------

def curva_juros(data: dt.date | str, mercadoria: str = "DI1") -> Consulta:
    """Vertices da curva numa data-base, para projetar o CDI futuro.

    `Fator_Spot` ja e o fator acumulado do vertice, entao a projecao e uma
    multiplicacao -- nao ha composicao dia a dia aqui. O NDC e em dias corridos
    a partir da data-base.
    """
    sql = (
        "SELECT NDC, NDU, Fator_Spot "
        "FROM dbo.TB_CURVA_JUROS_DIARIA "
        "WHERE Mercadoria = ? AND Data = ? "
        "ORDER BY NDC"
    )
    return sql, (mercadoria, _data(data))


# --- Cadastro de cotas: de-para de ISIN para CNPJ ---------------------------

def cadastro_cotas() -> Consulta:
    """Cotas cadastradas, para resolver a cota que a carteira nao nomeia.

    Cada administrador identifica a cota de um jeito: a VORTX pelo ISIN, a
    PLANNER pelo `idFundoAdm` numerico, a QI pelo mnemonico. As duas primeiras
    saem daqui.

    O cadastro e por COTA, nao por fundo: um CNPJ com senior, mezanino e
    subordinada ocupa varias linhas. `Condominio` diz se o fundo e fechado --
    fechado nao resgata, e o prazo dele e o "sem prazo" da tabela.

    Nao filtra por situacao: cota encerrada ainda pode estar numa carteira
    antiga que se reprocesse.
    """
    sql = (
        "SELECT idFundoInterno, idFundoAdm, nmFundoInterno, nmCotaInterno, "
        "       TipoCota, TipoFundo, Condominio, CNPJFundo, ISIN, Situacao, "
        "       LiquidacaoResgate, CarenciaResgate, PrazoDeDuracao "
        "FROM dbo.HOD_TB_CADASTRO_FUNDOS "
        "WHERE (ISIN IS NOT NULL AND LTRIM(RTRIM(ISIN)) <> '') "
        "   OR (idFundoAdm IS NOT NULL AND LTRIM(RTRIM(idFundoAdm)) <> '') "
        "ORDER BY CNPJFundo, nmCotaInterno"
    )
    return sql, ()


# --- Beyond: operacoes de liquidez (Consultar_Beyond) -----------------------

def beyond(codinome: str, data: dt.date | str) -> Consulta:
    sql = (
        "SELECT * "
        "FROM dbo.TB_LIQUIDEZ_OPERACOES_4 "
        "WHERE Fundo = ? AND Data = ?"
    )
    return sql, (codinome, _data(data))
