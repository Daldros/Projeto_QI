"""De-para das cotas de fundo: do codigo da carteira ao prazo de resgate.

Cada administrador identifica a cota do seu jeito, e a tabela de prazos --
digitada a mao -- so conhece o mnemonico:

    QI Tech    HOD II FIC FIDC SEN     mnemonico, ja resolvido pela tabela
    VORTX      BR01SACTF034            ISIN
    PLANNER    44517                   codigo interno do administrador

O cadastro `HOD_TB_CADASTRO_FUNDOS` do riscogc guarda as tres chaves e mais o
prazo, entao serve de fonte para as duas ultimas. Este modulo tira dali um
de-para local e resolve, nesta ordem:

    1. mnemonico na tabela de prazos   -- revisada por quem conhece os fundos
    2. ISIN no cadastro de cotas
    3. idFundoAdm no cadastro de cotas

e, chegando ao cadastro, tira o prazo assim:

    a. `pz_resg` da ficha, se o CNPJ for de um fundo da casa
    b. `LiquidacaoResgate` lido do texto -- "D+1 ATE D+29" vira 29
    c. `Condominio = FECHADO` ou "NAO HA" viram SEM_PRAZO: fundo fechado nao
       resgata, e e isso que o 999 significa na tabela

VALE SO PARA COTAS, nao para ativos. Um ISIN numa operacao de DC e uma
debenture, e resolve-lo para o prazo de um fundo daria liquidez a papel que nao
a tem. Por isso a resolucao entra apenas no laco de cotas do relatorio.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

ARQUIVO_PADRAO = Path("config") / "isin_cotas.csv"

# ISO 6166: 2 letras de pais, 9 alfanumericos, 1 digito verificador.
PADRAO_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
PADRAO_NUMERICO = re.compile(r"^\d+$")

# "D+1 ATE D+29" -> 29 (o pior caso, nao o melhor); "D+91" -> 91.
PADROES_PRAZO = (
    re.compile(r"D\s*\+\s*(\d+)\s*AT[EÉ]\s*D\s*\+\s*(\d+)", re.I),
    re.compile(r"^\s*D\s*\+\s*(\d+)\s*$", re.I),
)

SEM_PRAZO = 999          # mesmo sentinela da tabela de prazos
FECHADO = "FECHADO"
SEM_RESGATE = ("NAO HA", "NÃO HÁ")

COLUNAS = ("chave", "tipo_chave", "cnpj_fundo", "nome_fundo", "nome_cota",
           "tipo_fundo", "condominio", "situacao", "liquidacao_resgate", "prazo")


def e_isin(codigo) -> bool:
    """O texto tem forma de ISIN? Nao valida o digito verificador."""
    if not codigo:
        return False
    return bool(PADRAO_ISIN.match(str(codigo).strip().upper()))


def e_codigo_adm(codigo) -> bool:
    """Codigo interno do administrador: so digitos."""
    if not codigo:
        return False
    return bool(PADRAO_NUMERICO.match(str(codigo).strip()))


def _normalizar(texto) -> str:
    import unicodedata

    bruto = str(texto or "").strip().upper()
    return "".join(c for c in unicodedata.normalize("NFKD", bruto)
                   if not unicodedata.combining(c))


def ler_prazo(liquidacao, condominio="") -> int | None:
    """Dias ate a liquidacao do resgate, a partir do texto do cadastro.

    Fundo fechado devolve SEM_PRAZO em vez de None: nao e desconhecimento, e a
    informacao de que nao ha resgate -- e a tabela de prazos ja representa isso
    com 999.

    O CONDOMINIO DECIDE PRIMEIRO. Fundo fechado nao tem resgate, e o que o
    cadastro escreveu em `liquidacao_resgate` nao muda isso: o campo costuma vir
    preenchido por inercia mesmo onde nao se aplica. Ler o texto antes produzia
    prazo curto em fundo sem resgate -- em 26 das 159 linhas fechadas do de-para
    de 16/09/2026, sendo 21 com "D+1" devolvendo 1. E o erro que colocava
    HERITAGE, CARLOTA, INVERNESS e outros FIP e FIM fechados na faixa de um dia.
    """
    if _normalizar(condominio) == FECHADO:
        return SEM_PRAZO

    texto = _normalizar(liquidacao)
    if texto in SEM_RESGATE:
        return SEM_PRAZO
    if texto:
        for padrao in PADROES_PRAZO:
            achado = padrao.search(texto)
            if achado:
                return int(achado.groups()[-1])
    return None


@dataclass
class Cota:
    chave: str
    tipo_chave: str          # "isin" ou "codigo_adm"
    cnpj_fundo: str = ""
    nome_fundo: str = ""
    nome_cota: str = ""
    tipo_fundo: str = ""
    condominio: str = ""
    situacao: str = ""
    liquidacao_resgate: str = ""
    prazo: int | None = None

    def to_row(self) -> dict:
        d = {c: getattr(self, c) or "" for c in COLUNAS}
        d["prazo"] = "" if self.prazo is None else str(self.prazo)
        return d

    @classmethod
    def from_row(cls, row: dict) -> "Cota":
        prazo = (row.get("prazo") or "").strip()
        dados = {c: (row.get(c) or "").strip() for c in COLUNAS if c != "prazo"}
        return cls(prazo=int(prazo) if prazo else None, **dados)


class MapaCotas:
    """Lookup de cota por ISIN ou por codigo do administrador."""

    def __init__(self, registros: list[Cota] | None = None):
        self.registros = registros or []
        self._por_chave: dict[str, list[Cota]] = {}
        for r in self.registros:
            if r.chave:
                self._por_chave.setdefault(r.chave.strip().upper(), []).append(r)

    def __len__(self) -> int:
        return len(self._por_chave)

    @classmethod
    def carregar(cls, caminho: Path = ARQUIVO_PADRAO) -> "MapaCotas":
        """O de-para gravado. Ausente e o mesmo que vazio -- sem ele a cota por
        ISIN ou codigo segue sem prazo, como era antes desta funcionalidade."""
        caminho = Path(caminho)
        if not caminho.exists():
            log.info("Sem de-para de cotas em %s; so o mnemonico sera resolvido.", caminho)
            return cls([])
        with caminho.open(encoding="utf-8-sig", newline="") as arq:
            registros = [Cota.from_row(l) for l in csv.DictReader(arq, delimiter=";")]
        log.info("De-para de cotas carregado: %d chave(s)", len(registros))
        return cls(registros)

    def gravar(self, caminho: Path = ARQUIVO_PADRAO) -> Path:
        caminho = Path(caminho)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with caminho.open("w", encoding="utf-8-sig", newline="") as arq:
            escritor = csv.DictWriter(arq, fieldnames=list(COLUNAS), delimiter=";")
            escritor.writeheader()
            for r in sorted(self.registros, key=lambda x: (x.tipo_chave, x.chave.upper())):
                escritor.writerow(r.to_row())
        return caminho

    def obter(self, codigo) -> list[Cota]:
        """Todas as cotas com essa chave. Mais de uma = series do mesmo fundo."""
        if not codigo:
            return []
        return self._por_chave.get(str(codigo).strip().upper(), [])


# --- Sincronizacao com o riscogc --------------------------------------------

def baixar(cadastro=None) -> tuple[list[Cota], dict]:
    """Le o cadastro de cotas do riscogc e monta o de-para.

    Descarta o que nao tem forma da chave: o campo ISIN aceita texto livre e ha
    "NAO HA" digitado nele. Sem esse filtro, oito CNPJ colidiriam numa chave so.

    A FICHA VENCE, JA NA GRAVACAO. Onde o CNPJ e de um fundo da casa, o prazo
    gravado e o `pz_resg` da ficha -- que foi levantado regulamento por
    regulamento -- e nao o que se deduz do `LiquidacaoResgate` do riscogc. Sem
    isso o sincronizar desfazia a revisao: media 16 linhas divergentes em
    16/09/2026, entre elas OLIVENZA (179 no banco, 180 no regulamento), ZURIQUE
    ("NAO HA" no banco, 91 no regulamento) e MURALE (29 contra 30).

    O prazo do banco continua valendo para quem nao tem ficha -- fundo de
    terceiro, que e a maior parte do de-para.
    """
    from . import db, queries

    sql, params = queries.cadastro_cotas()
    df = db.consultar(sql, params)

    por_cnpj = {}
    if cadastro is not None:
        from .cvm import so_digitos

        por_cnpj = {so_digitos(f.cnpj): f for f in cadastro.fundos.values()}

    relatorio = {"lidos": len(df), "isin": 0, "codigo_adm": 0,
                 "descartados": 0, "sem_prazo_no_cadastro": 0, "da_ficha": 0}
    registros: list[Cota] = []
    vistos: set[tuple[str, str, str]] = set()

    for _, linha in df.iterrows():
        cnpj = str(linha.get("CNPJFundo") or "").strip()
        prazo = ler_prazo(linha.get("LiquidacaoResgate"), linha.get("Condominio"))
        if por_cnpj:
            from .cvm import so_digitos

            fundo = por_cnpj.get(so_digitos(cnpj))
            if fundo is not None and fundo.pz_resg is not None:
                if prazo != fundo.pz_resg:
                    relatorio["da_ficha"] += 1
                prazo = fundo.pz_resg
        if prazo is None:
            relatorio["sem_prazo_no_cadastro"] += 1

        for bruto, tipo, valido in (
            (linha.get("ISIN"), "isin", e_isin),
            (linha.get("idFundoAdm"), "codigo_adm", e_codigo_adm),
        ):
            chave = str(bruto or "").strip().upper()
            if not valido(chave) or not cnpj:
                continue
            # Mesma chave, mesmo CNPJ e mesmo prazo: e a mesma coisa repetida no
            # cadastro. So vira registro novo quando diverge em algo que importa.
            assinatura = (chave, cnpj, str(prazo))
            if assinatura in vistos:
                continue
            vistos.add(assinatura)
            relatorio[tipo] += 1
            registros.append(Cota(
                chave=chave, tipo_chave=tipo, cnpj_fundo=cnpj,
                nome_fundo=str(linha.get("nmFundoInterno") or "").strip(),
                nome_cota=str(linha.get("nmCotaInterno") or "").strip(),
                tipo_fundo=str(linha.get("TipoFundo") or "").strip(),
                condominio=str(linha.get("Condominio") or "").strip(),
                situacao=str(linha.get("Situacao") or "").strip(),
                liquidacao_resgate=str(linha.get("LiquidacaoResgate") or "").strip(),
                prazo=prazo,
            ))

    relatorio["gravados"] = len(registros)
    return registros, relatorio


# --- Resolucao do prazo -----------------------------------------------------

class ResolvedorCotas:
    """Prazo de resgate de uma cota, pelo mnemonico, pelo ISIN ou pelo codigo.

    A ordem importa: o mnemonico manda, porque a tabela de prazos e revisada por
    quem conhece os fundos. O cadastro do banco e o caminho de recurso.
    """

    def __init__(self, tabela_prazos, mapa: MapaCotas | None = None, cadastro=None):
        self.tabela = tabela_prazos
        self.mapa = mapa or MapaCotas([])
        self._por_cnpj = {}
        if cadastro is not None:
            from .cvm import so_digitos

            self._por_cnpj = {so_digitos(f.cnpj): f for f in cadastro.fundos.values()}
        self.resolvidos: list[dict] = []
        self.pendentes: list[dict] = []

    def prazo(self, codigo) -> int | None:
        direto = self.tabela.prazo_por_ativo(codigo)
        if direto is not None:
            return direto

        if not (e_isin(codigo) or e_codigo_adm(codigo)):
            return None

        cotas = self.mapa.obter(codigo)
        if not cotas:
            self.pendentes.append({
                "codigo": str(codigo),
                "motivo": "chave fora do de-para (fundo externo?)",
            })
            return None

        # A ficha da casa vence o cadastro do banco: e o valor que a area revisa.
        from .cvm import so_digitos

        for cota in cotas:
            fundo = self._por_cnpj.get(so_digitos(cota.cnpj_fundo))
            if fundo is not None and fundo.pz_resg is not None:
                self._anotar(codigo, cota, fundo.pz_resg, "ficha do fundo")
                return fundo.pz_resg

        prazos = {c.prazo for c in cotas if c.prazo is not None}
        if not prazos:
            c = cotas[0]
            self.pendentes.append({
                "codigo": str(codigo), "cnpj": c.cnpj_fundo, "nome": c.nome_fundo,
                "motivo": "cadastro sem LiquidacaoResgate nem Condominio",
            })
            return None
        if len(prazos) > 1:
            # Series da mesma chave com prazos diferentes: a carteira nao diz
            # qual classe e. Adivinhar seria pior que apontar.
            c = cotas[0]
            self.pendentes.append({
                "codigo": str(codigo), "cnpj": c.cnpj_fundo, "nome": c.nome_fundo,
                "motivo": f"prazos divergentes entre as series: {sorted(prazos)}",
            })
            return None

        prazo = prazos.pop()
        self._anotar(codigo, cotas[0], prazo, "cadastro do banco")
        return prazo

    def _anotar(self, codigo, cota: Cota, prazo: int, fonte: str) -> None:
        self.resolvidos.append({
            "codigo": str(codigo), "cnpj": cota.cnpj_fundo,
            "nome": cota.nome_fundo, "cota": cota.nome_cota,
            "tipo_chave": cota.tipo_chave, "prazo": prazo, "fonte": fonte,
        })
