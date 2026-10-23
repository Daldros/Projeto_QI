"""Classifica as cotas de outros fundos da carteira pelo cadastro da CVM.

Cada cota de fundo de terceiro precisa de uma de duas coisas para contar no
Caixa Potencial: o prazo de resgate (fundo ABERTO) ou o cronograma de
amortizacao (fundo FECHADO, que nao resgata). O riscogc nao conhece esses
fundos -- em 13/10/2026 nenhuma das 13 cotas do MULTIPLICA FIDC casou com
HOD_TB_CADASTRO_FUNDOS --, entao a classificacao vem do registro da CVM
(`cvm.py`), que traz `Forma_Condominio` por classe.

O elo entre a carteira e a CVM e o NOME, porque a carteira da QI nomeia a cota
por mnemonico ("ARCDEBSR") e por um nome truncado em ~20 caracteres ("ARC DEB
I FIDC SR"). O casamento e por pontuacao de tokens, com as abreviacoes do
mercado expandidas (FIDC, SR, MZ, SUB, NP...). E uma SUGESTAO: a saida traz as
melhores candidatas e a pontuacao, e quem conhece os fundos confirma.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .regras_adm import normalizar

log = logging.getLogger(__name__)

# Abreviacoes que a carteira usa e o nome oficial escreve por extenso.
EXPANSOES = {
    "FIDC": ("FUNDO", "INVESTIMENTO", "DIREITOS", "CREDITORIOS"),
    "FIDCNP": ("FUNDO", "INVESTIMENTO", "DIREITOS", "CREDITORIOS", "NAO", "PADRONIZADOS"),
    "FIC": ("FUNDO", "INVESTIMENTO", "COTAS"),
    "FICFIDC": ("FUNDO", "INVESTIMENTO", "COTAS", "DIREITOS", "CREDITORIOS"),
    "FIM": ("FUNDO", "INVESTIMENTO", "MULTIMERCADO"),
    "FIP": ("FUNDO", "INVESTIMENTO", "PARTICIPACOES"),
    "FII": ("FUNDO", "INVESTIMENTO", "IMOBILIARIO"),
    "NP": ("NAO", "PADRONIZADOS"),
    "SR": ("SENIOR",), "SEN": ("SENIOR",), "SENIOR": ("SENIOR",),
    "MZ": ("MEZANINO",), "MEZ": ("MEZANINO",), "MZ1": ("MEZANINO",), "MZA": ("MEZANINO",),
    "SUB": ("SUBORDINADA",), "JR": ("SUBORDINADA",), "JUNIOR": ("SUBORDINADA",),
    "RF": ("RENDA", "FIXA"), "CP": ("CREDITO", "PRIVADO"), "LP": ("LONGO", "PRAZO"),
    "RESP": ("RESPONSABILIDADE",), "LTDA": ("LIMITADA",),
}
# Palavras que nao distinguem um fundo do outro.
RUIDO = {"DE", "EM", "DO", "DA", "DOS", "DAS", "E", "O", "A", "FUNDO", "INVESTIMENTO",
         "CLASSE", "UNICA", "COTAS", "RESPONSABILIDADE", "LIMITADA", "MULTIMERCADO"}

ABERTO = "Aberto"
FECHADO = "Fechado"


def tokens(nome: str | None) -> set[str]:
    """Tokens relevantes do nome, com abreviacoes expandidas e ruido fora."""
    texto = normalizar(nome)
    texto = re.sub(r"[^A-Z0-9 ]+", " ", texto)
    saida: set[str] = set()
    for t in texto.split():
        saida.update(EXPANSOES.get(t, (t,)))
    return {t for t in saida if t not in RUIDO}


def pontuar(nome_carteira: str, nome_cvm: str) -> float:
    """Fracao dos tokens da carteira presentes no nome da CVM (0..1).

    Token da carteira pode estar truncado ("FIDCNP MZ1" vem de um nome cortado),
    por isso vale tambem o prefixo: "SORAN" casa com "SORANO".
    """
    a, b = tokens(nome_carteira), tokens(nome_cvm)
    if not a or not b:
        return 0.0
    achados = 0
    for t in a:
        if t in b or (len(t) >= 4 and any(x.startswith(t) for x in b)):
            achados += 1
    return achados / len(a)


@dataclass
class Candidata:
    cnpj: str
    denominacao: str
    forma_condominio: str
    tipo: str
    situacao: str
    pontuacao: float


@dataclass
class Classificacao:
    codigo_ativo: str
    nome_carteira: str
    valor: float
    candidatas: list[Candidata] = field(default_factory=list)

    @property
    def melhor(self) -> Candidata | None:
        return self.candidatas[0] if self.candidatas else None

    @property
    def acao(self) -> str:
        m = self.melhor
        if m is None or m.pontuacao < 0.5:
            return "conferir nome: sem candidata confiavel na CVM"
        if normalizar(m.forma_condominio) == normalizar(FECHADO):
            return "FECHADO: cadastrar cronograma de amortizacao (nao resgata)"
        if normalizar(m.forma_condominio) == normalizar(ABERTO):
            return "ABERTO: cadastrar o prazo de resgate D+N (prazos set)"
        return "condominio nao informado na CVM: conferir regulamento"


def ler_classes_cvm(caminho_zip: Path) -> list[dict]:
    """Classes do registro da CVM com o que a classificacao precisa."""
    from .cvm import ARQ_CLASSE, ARQ_FUNDO

    with zipfile.ZipFile(caminho_zip) as pacote:
        fundos = {f.get("ID_Registro_Fundo", ""): f
                  for f in csv.DictReader(io.StringIO(pacote.read(ARQ_FUNDO).decode("latin-1")), delimiter=";")}
        classes = list(csv.DictReader(io.StringIO(pacote.read(ARQ_CLASSE).decode("latin-1")), delimiter=";"))
    saida = []
    for c in classes:
        f = fundos.get(c.get("ID_Registro_Fundo", ""), {})
        saida.append({
            "cnpj": (c.get("CNPJ_Classe") or f.get("CNPJ_Fundo") or "").strip(),
            "denominacao": (c.get("Denominacao_Social") or "").strip(),
            "forma_condominio": (c.get("Forma_Condominio") or "").strip(),
            "tipo": (f.get("Tipo_Fundo") or c.get("Tipo_Classe") or "").strip(),
            "situacao": (c.get("Situacao") or "").strip(),
        })
    return saida


def classificar(cotas: list[tuple[str, str, float]], classes: list[dict],
                top: int = 3, minimo: float = 0.34) -> list[Classificacao]:
    """`cotas` = [(codigo_ativo, nome_carteira, valor)]. Devolve uma linha por cota."""
    # Nome da CVM tokenizado uma vez: sao dezenas de milhares de classes.
    indexadas = [(c, tokens(c["denominacao"])) for c in classes if c["denominacao"]]
    resultado = []
    for codigo, nome, valor in cotas:
        alvo = tokens(nome)
        pontuadas = []
        for c, toks in indexadas:
            if not alvo or not toks:
                continue
            achados = sum(1 for t in alvo if t in toks or (len(t) >= 4 and any(x.startswith(t) for x in toks)))
            p = achados / len(alvo)
            if p >= minimo:
                pontuadas.append(Candidata(c["cnpj"], c["denominacao"], c["forma_condominio"],
                                           c["tipo"], c["situacao"], p))
        # Pontuacao primeiro; empate favorece fundo ativo e FIDC.
        pontuadas.sort(key=lambda k: (-k.pontuacao,
                                      normalizar(k.situacao) == "CANCELADO",
                                      "DIREITOS CREDITORIOS" not in normalizar(k.denominacao)))
        resultado.append(Classificacao(codigo, nome, valor, pontuadas[:top]))
    return resultado


def escrever_csv(linhas: list[Classificacao], destino: Path) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8-sig", newline="") as arq:
        w = csv.writer(arq, delimiter=";")
        w.writerow(["codigo_ativo", "nome_carteira", "valor", "acao",
                    "cnpj_sugerido", "denominacao_cvm", "forma_condominio", "tipo", "situacao", "pontuacao"])
        for l in linhas:
            m = l.melhor
            w.writerow([l.codigo_ativo, l.nome_carteira, f"{l.valor:.2f}", l.acao,
                        m.cnpj if m else "", m.denominacao if m else "", m.forma_condominio if m else "",
                        m.tipo if m else "", m.situacao if m else "", f"{m.pontuacao:.2f}" if m else ""])
    return destino
