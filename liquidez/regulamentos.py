"""Regulamentos vigentes, uma pasta por fundo.

Duas fontes, porque nenhuma sozinha resolve.

A PRIMARIA e o dataset `FI/DOC/EVENTUAL` do portal de dados abertos da CVM --
o mesmo portal de onde `cvm.py` ja tira o cadastro. Cada linha e uma entrega de
documento, e as de `TP_DOC = "REGUL FDO"` sao os regulamentos. A linha traz o
link direto do PDF, entao nao ha o que raspar: le-se o CSV e baixa-se o arquivo.
Cobre todos os tipos de fundo -- FIDC, FII, FIAGRO, FIP e os FI/555.

A SECUNDARIA e o FNET (`fnet.bmfbovespa.com.br`), o repositorio da B3 onde os
fundos estruturados entregam seus documentos. Ele NAO substitui o primeiro: so
tem estruturado (na carteira, 56 dos 97 fundos; nenhum FI ou FIP). Serve para
uma coisa so, e por isso continua aqui: o CSV da CVM e consolidado com alguns
dias de atraso, e nesse intervalo uma alteracao recem-entregue aparece no FNET e
ainda nao no CSV. Em 27/08/2026 era o caso do MOZART FIDC -- FNET em 25/08,
CSV parado em 18/08.

As duas fontes se cruzam sem conflito: para os estruturados, o proprio
`LINK_ARQ` do CSV da CVM ja aponta para o FNET, com o mesmo id do documento. E
por isso que a deduplicacao e feita pela URL -- a mesma entrega vinda das duas
fontes colapsa em uma so.

VIGENTE. Nao existe marcador de "em vigor" em nenhuma das duas. Cada alteracao e
entregue como um regulamento inteiro e novo, entao o vigente e a entrega mais
recente. Recente por `DT_COMPTC` -- a data do proprio instrumento --, com a data
de entrega so desempatando. As duas quase sempre concordam; quando discordam, e
porque um regulamento antigo foi protocolado depois de um mais novo, e ai quem
manda e a data do documento, nao a do protocolo. Em 27/08/2026 isso acontecia em
3 dos 97 fundos, e nesses o modulo grava OS DOIS candidatos e marca a ficha como
ambigua, em vez de escolher em silencio.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import re
import ssl
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .cvm import so_digitos

log = logging.getLogger(__name__)

# --- Fonte 1: dados abertos da CVM -----------------------------------------
URL_EVENTUAL = "https://dados.cvm.gov.br/dados/FI/DOC/EVENTUAL/DADOS/eventual_fi_{ano}.csv"
TP_DOC_REGULAMENTO = "REGUL FDO"
ANO_MINIMO = 2015

# --- Fonte 2: FNET ----------------------------------------------------------
FNET = "https://fnet.bmfbovespa.com.br/fnet/publico"
URL_BUSCA_FNET = f"{FNET}/pesquisarGerenciadorDocumentosDados"
URL_DOWNLOAD_FNET = f"{FNET}/downloadDocumento"
CATEGORIA_REGULAMENTO = 5  # "Regulamento", no menu de categorias do FNET

DIR_CACHE = Path(".cache") / "cvm"
DIR_REGULAMENTOS = Path("regulamentos")
ARQUIVO_FICHA = "regulamento.json"
ARQUIVO_INDICE = "_indice.json"

CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

TIMEOUT = 120
TENTATIVAS = 4
TRABALHADORES = 4

INVALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # proibidos em nome de pasta no Windows


class ErroRegulamento(RuntimeError):
    pass


@dataclass(frozen=True)
class Entrega:
    """Uma entrega de regulamento, venha de onde vier."""

    url: str
    comptc: str          # data do instrumento (ISO), '' quando a fonte nao diz
    receb: str           # data do protocolo (ISO)
    fonte: str           # 'cvm' | 'fnet'
    identificador: str = ""
    nome_arquivo: str = ""

    @property
    def ordem(self) -> tuple[str, str]:
        """Vigente e o instrumento mais novo; o protocolo so desempata."""
        return (self.comptc or self.receb, self.receb)


# --- Rede -------------------------------------------------------------------

def _contexto() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _abrir(url: str, tentativas: int = TENTATIVAS, timeout: int = TIMEOUT) -> bytes:
    """GET com insistencia. O FNET cai; desistir na primeira perde o fundo."""
    ultimo: Exception | None = None
    for i in range(tentativas):
        try:
            pedido = urllib.request.Request(url, headers=CABECALHOS)
            with urllib.request.urlopen(pedido, timeout=timeout, context=_contexto()) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            ultimo = e
            log.debug("tentativa %d falhou (%s): %s", i + 1, type(e).__name__, url)
            time.sleep(4 * (i + 1))
    raise ErroRegulamento(f"sem resposta depois de {tentativas} tentativas: {ultimo}")


# --- Fonte 1: o CSV de documentos eventuais ---------------------------------

def _baixar_ano(ano: int, cache: Path) -> Path:
    """Um CSV por ano, em cache.

    Ano corrente muda todo dia e e rebaixado quando o cache nao e de hoje. Ano
    fechado nao muda mais -- fica guardado para sempre.
    """
    cache.mkdir(parents=True, exist_ok=True)
    destino = cache / f"eventual_fi_{ano}.csv"
    corrente = ano >= dt.date.today().year
    if destino.exists():
        de_hoje = dt.date.fromtimestamp(destino.stat().st_mtime) == dt.date.today()
        if not corrente or de_hoje:
            return destino
    log.info("Baixando documentos eventuais da CVM: %d", ano)
    dados = _abrir(URL_EVENTUAL.format(ano=ano), timeout=300)
    destino.write_bytes(dados)
    log.info("  %d: %.1f MB", ano, len(dados) / 1024 / 1024)
    return destino


def _cnpj_da_linha(linha: dict) -> str:
    """O layout mudou: ate 2025 era CNPJ_FUNDO; em 2026 virou CNPJ_FUNDO_CLASSE."""
    return so_digitos(linha.get("CNPJ_FUNDO_CLASSE") or linha.get("CNPJ_FUNDO"))


def _ler_ano(caminho: Path) -> dict[str, list[Entrega]]:
    texto = caminho.read_text(encoding="latin-1")
    por_cnpj: dict[str, list[Entrega]] = defaultdict(list)
    for linha in csv.DictReader(io.StringIO(texto), delimiter=";"):
        if (linha.get("TP_DOC") or "").strip().upper() != TP_DOC_REGULAMENTO:
            continue
        url = (linha.get("LINK_ARQ") or "").strip()
        if not url:
            continue
        por_cnpj[_cnpj_da_linha(linha)].append(
            Entrega(
                url=url,
                comptc=(linha.get("DT_COMPTC") or "").strip(),
                receb=(linha.get("DT_RECEB") or "").strip(),
                fonte="cvm",
                nome_arquivo=(linha.get("NM_ARQ") or "").strip(),
            )
        )
    return por_cnpj


def carregar_eventual(cnpjs: dict[str, int] | set[str], cache: Path = DIR_CACHE,
                      ano_minimo: int = ANO_MINIMO) -> dict[str, list[Entrega]]:
    """Varre os CSVs do ano corrente para tras ate cobrir todos os CNPJs pedidos.

    Fundo que nunca alterou o regulamento so aparece no ano em que foi
    registrado, entao parar no ano corrente perderia a maioria. A varredura para
    assim que todo mundo tem pelo menos uma entrega: na carteira de 27/08/2026,
    2026 a 2023 bastam.

    `cnpjs` pode vir como um mapa CNPJ -> ano de constituicao. Vale a pena: um
    unico fundo sem regulamento publicado arrastaria a varredura ate 2015 atras
    de algo que nao existe. O HERITAGE II FIP, constituido em 30/03/2026, e
    exatamente esse caso -- procurar por ele em 2025 ou antes e procurar por um
    fundo que ainda nao existia.
    """
    piso = dict(cnpjs) if isinstance(cnpjs, dict) else {c: ano_minimo for c in cnpjs}
    achados: dict[str, list[Entrega]] = defaultdict(list)
    faltam = set(piso)
    for ano in range(dt.date.today().year, ano_minimo - 1, -1):
        faltam = {c for c in faltam if ano >= piso.get(c, ano_minimo)}
        if not faltam:
            break
        try:
            por_cnpj = _ler_ano(_baixar_ano(ano, cache))
        except ErroRegulamento as e:
            log.warning("ano %d indisponivel na CVM: %s", ano, e)
            continue
        for cnpj in list(faltam):
            entregas = por_cnpj.get(cnpj)
            if entregas:
                achados[cnpj].extend(entregas)
                faltam.discard(cnpj)
        # Quem ja apareceu pode ter mais entregas nos anos seguintes; como a
        # varredura vem do mais novo para o mais antigo, o vigente ja foi visto.
        log.info("  %d: faltam %d de %d CNPJs", ano, len(faltam), len(piso))
        if not faltam:
            break
    return achados


# --- Fonte 2: o FNET --------------------------------------------------------

def _iso(texto: str) -> str:
    """'dd/mm/aaaa HH:MM' -> 'aaaa-mm-dd'. O que nao casar vira ''."""
    bruto = str(texto or "").strip()
    for formato in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(bruto, formato).date().isoformat()
        except ValueError:
            continue
    return ""


def buscar_fnet(cnpj: str, limite: int = 30) -> list[Entrega]:
    """Regulamentos de um CNPJ no FNET.

    O parametro de busca e `cnpjFundo`, com os 14 digitos e sem mascara. Os
    nomes parecidos -- `cnpj`, `termo`, `cnpjFundoPesquisa` -- NAO dao erro: o
    FNET responde 200 e devolve a fila global de entregas de todo o mercado, o
    que passa por resultado valido ate voce reparar que os documentos sao de
    outros fundos. Por isso a resposta e conferida contra o CNPJ pedido.
    """
    digitos = so_digitos(cnpj)
    if len(digitos) != 14:
        raise ValueError(f"CNPJ invalido para busca no FNET: {cnpj!r}")
    consulta = {
        "d": 0, "s": 0, "l": limite,
        "o[0][dataEntrega]": "desc",
        "cnpjFundo": digitos,
        "idCategoriaDocumento": CATEGORIA_REGULAMENTO,
    }
    corpo = _abrir(f"{URL_BUSCA_FNET}?{urllib.parse.urlencode(consulta)}")
    dados = json.loads(corpo.decode("utf-8", "replace")).get("data") or []

    entregas = []
    for doc in dados:
        do_doc = so_digitos(doc.get("cnpjFundo"))
        if do_doc and do_doc != digitos:
            log.warning("FNET devolveu documento de %s na busca por %s; descartado",
                        do_doc, digitos)
            continue
        if str(doc.get("status") or "").strip() != "AC":  # AC = ativo com visualizacao
            continue
        entregas.append(Entrega(
            url=f"{URL_DOWNLOAD_FNET}?id={doc.get('id')}",
            comptc=_iso(doc.get("dataReferencia")),
            receb=_iso(doc.get("dataEntrega")),
            fonte="fnet",
            identificador=str(doc.get("id") or ""),
        ))
    return entregas


# --- Selecao ----------------------------------------------------------------

def _chave(entrega: Entrega) -> str:
    """Mesma entrega vinda das duas fontes tem a mesma URL -- e onde elas colam."""
    return entrega.url.strip().lower().replace("http://", "https://")


def consolidar(entregas: list[Entrega]) -> list[Entrega]:
    """Junta as fontes, tira repetidas e ordena da mais nova para a mais antiga.

    Quando a mesma URL vem das duas, fica a da CVM: e a que traz `DT_COMPTC`
    preenchida com a data do instrumento.
    """
    por_url: dict[str, Entrega] = {}
    for e in entregas:
        chave = _chave(e)
        atual = por_url.get(chave)
        if atual is None or (e.fonte == "cvm" and atual.fonte != "cvm"):
            por_url[chave] = e
    return sorted(por_url.values(), key=lambda e: e.ordem, reverse=True)


def escolher(entregas: list[Entrega]) -> tuple[list[Entrega], bool]:
    """O vigente. Devolve tambem se a escolha foi ambigua.

    Ambigua e quando a entrega mais recente por data de instrumento nao e a mais
    recente por data de protocolo. Nesse caso as duas voltam, e quem le decide --
    esconder uma delas seria fingir uma certeza que a fonte nao da.
    """
    if not entregas:
        return [], False
    por_instrumento = max(entregas, key=lambda e: e.ordem)
    por_protocolo = max(entregas, key=lambda e: (e.receb, e.comptc))
    if _chave(por_instrumento) == _chave(por_protocolo):
        return [por_instrumento], False
    return [por_instrumento, por_protocolo], True


# --- Disco ------------------------------------------------------------------

def nome_de_pasta(nome: str) -> str:
    """Nome do fundo virado nome de pasta valido no Windows."""
    limpo = INVALIDOS.sub("-", (nome or "").strip())
    limpo = re.sub(r"\s+", " ", limpo).strip(" .")
    return limpo or "SEM NOME"


def nome_de_arquivo(entrega: Entrega) -> str:
    """`Regulamento 2025-06-23.pdf` -- a data deixa a versao visivel na pasta."""
    data = entrega.comptc or entrega.receb or "sem data"
    return f"Regulamento {data}.pdf"


def baixar_pdf(entrega: Entrega) -> bytes:
    """Confere o cabecalho do arquivo, nao so o HTTP 200."""
    corpo = _abrir(entrega.url)
    if not corpo.startswith(b"%PDF"):
        raise ErroRegulamento(
            f"{entrega.url} nao devolveu um PDF (primeiros bytes: {corpo[:16]!r})")
    return corpo


@dataclass
class Resultado:
    """O que aconteceu com um fundo nesta rodada."""

    fundo: str
    cnpj: str
    situacao: str          # baixado | ja_tinha | sem_regulamento | erro
    pasta: Path | None = None
    arquivos: list[str] = field(default_factory=list)
    data: str | None = None
    fontes: list[str] = field(default_factory=list)
    ambiguo: bool = False
    detalhe: str | None = None


def _cnpjs_do_fundo(ficha) -> list[str]:
    """CNPJ do fundo e os das classes.

    Depois da RCVM 175 parte dos documentos passou a ser entregue sob o CNPJ da
    CLASSE. Sem isso, fundo que tem regulamento publicado apareceria como sem.
    """
    chaves = [so_digitos(ficha.cnpj)]
    for classe in ((ficha.cvm or {}).get("classes") or []):
        digitos = so_digitos(classe.get("cnpj"))
        if len(digitos) == 14 and digitos not in chaves:
            chaves.append(digitos)
    return chaves


def _ficha_json(ficha, entregas: list[Entrega], escolhidas: list[Entrega],
                ambiguo: bool) -> dict:
    return {
        "fundo": ficha.nome_interno,
        "cnpj": ficha.cnpj,
        "baixado_em": dt.date.today().isoformat(),
        "ambiguo": ambiguo,
        "nota_ambiguidade": (
            "Ha um regulamento protocolado depois de outro com data de instrumento "
            "mais recente. Os dois estao na pasta; confira qual esta em vigor."
            if ambiguo else None
        ),
        "vigente": [
            {"arquivo": nome_de_arquivo(e), "data_instrumento": e.comptc,
             "data_entrega": e.receb, "fonte": e.fonte, "url": e.url}
            for e in escolhidas
        ],
        "entregas_conhecidas": [
            {"data_instrumento": e.comptc, "data_entrega": e.receb,
             "fonte": e.fonte, "url": e.url}
            for e in entregas
        ],
    }


def baixar_fundo(ficha, entregas_cvm: dict[str, list[Entrega]],
                 destino: Path = DIR_REGULAMENTOS, usar_fnet: bool = True,
                 historico: bool = False, refazer: bool = False) -> Resultado:
    """Grava o regulamento de um fundo em `destino/<nome do fundo>/`."""
    rotulo = ficha.nome_interno
    chaves = _cnpjs_do_fundo(ficha)

    candidatas: list[Entrega] = []
    for chave in chaves:
        candidatas.extend(entregas_cvm.get(chave, []))

    if usar_fnet:
        for chave in chaves:
            try:
                candidatas.extend(buscar_fnet(chave))
            except (ErroRegulamento, ValueError) as e:
                log.warning("%s: FNET indisponivel (%s); seguindo so com a CVM",
                            rotulo, e)

    entregas = consolidar(candidatas)
    if not entregas:
        return Resultado(rotulo, ficha.cnpj, "sem_regulamento")

    escolhidas, ambiguo = escolher(entregas)
    alvos = entregas if historico else escolhidas

    pasta = Path(destino) / nome_de_pasta(rotulo)
    pasta.mkdir(parents=True, exist_ok=True)

    gravados, reaproveitados = [], []
    try:
        for entrega in alvos:
            arquivo = pasta / nome_de_arquivo(entrega)
            if arquivo.exists() and not refazer:
                reaproveitados.append(arquivo.name)
                continue
            arquivo.write_bytes(baixar_pdf(entrega))
            gravados.append(arquivo.name)
    except ErroRegulamento as e:
        return Resultado(rotulo, ficha.cnpj, "erro", pasta=pasta,
                         arquivos=gravados, detalhe=str(e))

    (pasta / ARQUIVO_FICHA).write_text(
        json.dumps(_ficha_json(ficha, entregas, escolhidas, ambiguo),
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    principal = escolhidas[0]
    return Resultado(
        rotulo, ficha.cnpj,
        "baixado" if gravados else "ja_tinha",
        pasta=pasta,
        arquivos=gravados + reaproveitados,
        data=principal.comptc or principal.receb,
        fontes=sorted({e.fonte for e in escolhidas}),
        ambiguo=ambiguo,
    )


def baixar_todos(fichas, destino: Path = DIR_REGULAMENTOS, cache: Path = DIR_CACHE,
                 usar_fnet: bool = True, historico: bool = False, refazer: bool = False,
                 trabalhadores: int = TRABALHADORES) -> list[Resultado]:
    """Baixa o regulamento de varios fundos. Paralelo moderado: o FNET piora sob pressao."""
    fichas = list(fichas)
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)

    alvos: dict[str, int] = {}
    for ficha in fichas:
        constituicao = str(ficha.data_constituicao or "")[:4]
        ano = int(constituicao) if constituicao.isdigit() else ANO_MINIMO
        for cnpj in _cnpjs_do_fundo(ficha):
            alvos[cnpj] = min(alvos.get(cnpj, ano), ano)
    entregas_cvm = carregar_eventual(alvos, cache)

    def um(ficha):
        r = baixar_fundo(ficha, entregas_cvm, destino, usar_fnet=usar_fnet,
                         historico=historico, refazer=refazer)
        log.info("%-46s %-16s %s%s", r.fundo[:46], r.situacao, r.data or "",
                 "  [ambiguo]" if r.ambiguo else "")
        return r

    with ThreadPoolExecutor(max_workers=trabalhadores) as pool:
        resultados = list(pool.map(um, fichas))

    indice = {
        "gerado_em": dt.datetime.now().isoformat(timespec="seconds"),
        "fontes": {"cvm": URL_EVENTUAL, "fnet": URL_BUSCA_FNET if usar_fnet else None},
        "fundos": [
            {"fundo": r.fundo, "cnpj": r.cnpj, "situacao": r.situacao,
             "data": r.data, "fontes": r.fontes, "ambiguo": r.ambiguo,
             "pasta": str(r.pasta) if r.pasta else None,
             "arquivos": r.arquivos, "detalhe": r.detalhe}
            for r in sorted(resultados, key=lambda x: x.fundo)
        ],
    }
    (destino / ARQUIVO_INDICE).write_text(
        json.dumps(indice, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resultados
