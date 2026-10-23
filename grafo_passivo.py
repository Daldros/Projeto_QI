"""Grafo de exposicao entre fundos, com a senioridade da cota na aresta.

Quem aplica em quem, e em QUE classe. A classe e o atributo de risco: comprar
subordinada de outro fundo e assumir primeira perda de uma carteira que nao e
sua; comprar senior e outra coisa. O grafo torna visivel o que a tabela de
cotistas esconde -- especialmente a cadeia, que aqui chega a 9 niveis.

    python grafo_passivo.py [AAAA-MM-DD]

Sem data, usa a posicao de passivo mais recente do banco. Estilo e tokens vem
do painel de liquidez: e a mesma publicacao.
"""

from __future__ import annotations

import base64
import datetime as dt
import html
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

try:                      # so para o CSS do grafo do fundo; sem ele a pagina
    import grafo_fundo as gf   # sai igual, com o desenho sem estilo proprio
except Exception:         # noqa: BLE001
    gf = None

AQUI = Path(__file__).parent
LOGO = AQUI / "config" / "marca" / "multiplica.png"
SAIDA = AQUI / "grafo_passivo.html"

NAVY, AZUL, CIANO = "#002346", "#0063c7", "#00e1ff"
NAVY_ESCURO = "#001a33"
AZUL_CLARO, AZUL_PALIDO, QUASE_BRANCO = "#41a1ff", "#93c9ff", "#f5faff"

CORES = {
    "claro": {
        "superficie": "#ffffff", "plano": QUASE_BRANCO,
        "tinta": NAVY, "tinta2": "#597087", "tinta3": "#8091a2",
        "grade": "#d1e8ff", "eixo": AZUL_PALIDO, "serie": AZUL,
        "bom": "#0ca30c", "atencao": "#fab219", "critico": "#d03b3b",
        "neutro": "#8091a2", "borda": "rgba(0,35,70,0.12)",
    },
    "escuro": {
        "superficie": NAVY, "plano": NAVY_ESCURO,
        "tinta": "#ffffff", "tinta2": AZUL_PALIDO, "tinta3": "#738699",
        "grade": "#1a3959", "eixo": "#294664", "serie": AZUL_CLARO,
        "bom": "#0ca30c", "atencao": "#fab219", "critico": "#d03b3b",
        "neutro": "#738699", "borda": "rgba(255,255,255,0.12)",
    },
}

# Senioridade -> grupo de risco. A ordem importa: "SUBORDINADA MEZANINO" nao
# existe, mas "MEZANINO" comeca com MEZ e tem que ser testado antes de SEN.
GRUPOS = [
    ("sub", "Subordinada", "critico", ("SUBORD", "JUNIOR", "JR")),
    ("mez", "Mezanino", "atencao", ("MEZ",)),
    ("sen", "Sênior", "serie", ("SEN",)),
    ("uni", "Única", "neutro", ("UNICA", "ÚNICA")),
    # Sem prefixo: nunca sai de grupo_da_classe, so de quem monta a aresta sem
    # saber a senioridade. Serve para nao passar por "Única" o que e ignorado.
    ("nd", "Não identificada", "tinta3", ()),
]


def grupo_da_classe(classe) -> str:
    t = str(classe or "").strip().upper()
    for chave, _rot, _cor, prefixos in GRUPOS:
        if t.startswith(prefixos):
            return chave
    return "uni"


# Passivo e carteira direto do datalake (MUL_GLD_PASSIVO_CONSOLIDADO, de onde o
# Datalake monta a HOD_PAS_*, e MUL_GLD_CARTEIRA_UNIFICADA); a base de notas
# deduplicada e o de-para vivem em liquidez/queries.py.
from liquidez.queries import (  # noqa: E402
    PASSIVO_DISTRIBUIDORES, PASSIVO_JOIN_DISTRIBUIDOR, PASSIVO_JOIN_FUNDO,
    PASSIVO_NOTAS, SQL_PASSIVO_ULTIMA,
)
from liquidez.tabelas import CARTEIRA  # noqa: E402

SQL = f"""
SELECT f.CNPJFundo AS cnpj_destino, f.nmFundoInterno AS nome_destino,
       f.nmCotaInterno AS classe,
       p.nome AS cotista, p.doc AS doc_origem,
       d.nmDistribuidora AS distribuidor,
       SUM(CAST(p.vlCorrigido AS FLOAT)) AS saldo
FROM {PASSIVO_NOTAS} AS p
INNER JOIN dbo.HOD_TB_CADASTRO_FUNDOS AS f ON {PASSIVO_JOIN_FUNDO}
LEFT JOIN {PASSIVO_DISTRIBUIDORES} AS d ON {PASSIVO_JOIN_DISTRIBUIDOR}
WHERE f.Situacao = 'NORMAL' AND f.nmCotaInterno <> 'ORIGINAL'
GROUP BY f.CNPJFundo, f.nmFundoInterno, f.nmCotaInterno,
         p.nome, p.doc, d.nmDistribuidora
"""


# O que o fundo COMPRA sai da carteira, nao do passivo. O passivo so enxerga
# fundo do cadastro do administrador: no MULTIPLICA ele via 5 posicoes e
# R$ 114,0 mi, contra 14 posicoes e R$ 586,6 mi da carteira -- 80% do que o
# fundo aplica em cota de fundo e de terceiro e ficava invisivel.
#
# A lista de rotulos vem de grafo_fundo, que e quem define o recorte; a copia
# abaixo so cobre o caso de o import falhar.
BASES_COTA = getattr(gf, "BASES_COTA", None) or (
    "Outros Fundos", "Fundos-Fundos", "Fundos-Fundos_a_Mercado",
    "COTAS DE FUNDOS EXTERNOS", "Carteira de Fundos de Investimentos",
    "PORTFOLIO_INVESTIDO", "COTASAPLICADAS", "Cotas de Investimento",
    "COTAS FUNDOS DE INVESTIMENTOS")

SQL_CARTEIRA = f"""
SELECT docFundo AS cnpj_fundo, dsNomeFundo AS Nome_Do_Fundo, cdAtivo AS Codigo_Ativo,
       dsNomeAtivo AS Nome_Ativo, CAST(vlLiquido AS FLOAT) AS valor
FROM {CARTEIRA}
WHERE dtReferencia = ? AND dsBaseOrigem IN ({",".join("?" * len(BASES_COTA))})
"""


def _dig(s):
    return s.astype(str).str.replace(r"\D", "", regex=True)


# Cotista de fora cujo NOME denuncia um fundo. Sao fundos de terceiro, que nao
# estao no cadastro do administrador -- a relacao e a mesma (fundo comprando
# cota de fundo), so nao da para seguir a cadeia para tras.
import re as _re

_PADRAO_FUNDO = _re.compile(
    r"\b(FIDC|FIC|FIM|FIP|FII|FIAGRO|FI\b|FUNDO DE INVESTIMENTO|"
    r"FI FINANCEIRO|MULTIMERCADO)\b", _re.I)


def parece_fundo(nome) -> bool:
    return bool(_PADRAO_FUNDO.search(str(nome or "")))


# Senioridade pelo NOME do ativo, para a posicao de carteira que nao casa com
# fundo do cadastro: na carteira a classe vem no sufixo ("ARC DEB I FIDC SUB").
_RE_SUB = _re.compile(r"\b(SUBORD\w*|SUB|SB|JUNIOR\w*|JR)\b")
_RE_MEZ = _re.compile(r"\b(MEZ\w*|MZ\d?)\b")
_RE_SEN = _re.compile(r"\b(SENIOR\w*|SEN|SR\d?)\b")


def grupo_do_ativo(nome) -> str:
    t = str(nome or "").upper()
    if _RE_SUB.search(t):
        return "sub"
    if _RE_MEZ.search(t):
        return "mez"
    if _RE_SEN.search(t):
        return "sen"
    return "uni"


# Casamento carteira -> fundo do cadastro. Nao da para ir pelo nome: a carteira
# chama de "PHENIX FIDC NP" o que o cadastro chama de "ARTEB FIDC". Vai pelo
# valor, com tolerancia relativa -- as duas bases precificam com diferenca
# pequena (ali, R$ 83.146,97 em R$ 85,4 mi, 0,097%).
TOL_CASAMENTO = 0.01     # 1% do valor da posicao


def casar(posicoes: pd.DataFrame, passivo: pd.DataFrame) -> dict:
    """Indice da posicao de carteira -> linha do passivo, 1 para 1.

    Guloso pelo menor desvio: sem isso "ARC SPECIAL I FID SR" (28.265.442,27)
    rouba o par do "SOLDI INVEST FIDC" (28.126.662,21), que difere 0,49%.
    """
    pares = []
    for i, v in posicoes["valor"].items():
        if not v or v <= 0:
            continue
        d = (passivo["saldo"] - v).abs() / v
        for j in d[d <= TOL_CASAMENTO].index:
            pares.append((float(d[j]), i, j))
    pares.sort()
    usados_i, usados_j, mapa = set(), set(), {}
    for _erro, i, j in pares:
        if i in usados_i or j in usados_j:
            continue
        usados_i.add(i)
        usados_j.add(j)
        mapa[i] = j
    return mapa


def carregar(data: str | None):
    """Arestas na data: fundo -> fundo e investidor de fora -> fundo."""
    from liquidez import db

    if data is None:
        d = db.consultar(SQL_PASSIVO_ULTIMA)
        data = str(d["u"].iloc[0])[:10]

    p = db.consultar(SQL, (data,))
    uni = db.consultar(
        "SELECT DISTINCT CNPJFundo, nmFundoInterno "
        "FROM dbo.HOD_TB_CADASTRO_FUNDOS WHERE CNPJFundo IS NOT NULL")
    uni["num"] = _dig(uni["CNPJFundo"])
    nomes = dict(zip(uni["num"], uni["nmFundoInterno"]))
    no_cadastro = set(uni["num"])

    p["origem"] = _dig(p["doc_origem"])
    p["destino"] = _dig(p["cnpj_destino"])
    p["grupo"] = p["classe"].map(grupo_da_classe)
    p["nome_destino"] = p["nome_destino"].astype(str).str.strip()
    total = float(p["saldo"].sum())

    interno = p["origem"].isin(no_cadastro) & (p["origem"] != p["destino"])
    a = p[interno].copy()
    a["nome_origem"] = a["origem"].map(nomes).fillna(a["cotista"])
    a["tipo_origem"] = "fundo"

    # --- investidores de fora ------------------------------------------------
    ext = p[~interno].copy()
    ext["nome_origem"] = ext["cotista"].astype(str).str.strip()
    # Documento invalido (so zeros) nao identifica ninguem, mas o saldo e real:
    # entra como no proprio, rotulado, em vez de sumir da conta.
    ext["origem"] = ext["origem"].where(ext["origem"].str.strip("0") != "",
                                        "SEMDOC-" + ext["nome_origem"])
    # A tabela de detalhe nao pode herdar o corte do desenho: somar so parte
    # dos cotistas e chamar aquilo de passivo do fundo e errado. Aqui entra
    # TODO mundo -- fundo, investidor direto, pessoa fisica -- com uma linha
    # por (investidor, fundo, classe), para nao fundir MEZ A com MEZ B.
    ext["tipo_origem"] = ["fundo_externo" if parece_fundo(n) else "investidor"
                          for n in ext["nome_origem"]]
    todas = (pd.concat([a, ext], ignore_index=True)
             .groupby(["origem", "destino", "classe", "grupo"], as_index=False)
             .agg(saldo=("saldo", "sum"),
                  nome_origem=("nome_origem", "first"),
                  nome_destino=("nome_destino", "first"),
                  tipo_origem=("tipo_origem", "first")))

    saidas = _carteira(db, data, p, no_cadastro, nomes)

    # O mapa de bolhas e so dos fundos da casa, e sem senioridade: uma aresta
    # por par, somando as classes. Duas escolhas, por motivos diferentes:
    #
    # - a senioridade sai daqui porque e o eixo do grafo do fundo selecionado,
    #   logo abaixo, onde tem espaco para ser lida; aqui so multiplicava
    #   arestas entre as mesmas duas bolas;
    # - a aresta vem do PASSIVO, nao da carteira, porque "A e cotista de B" e
    #   informacao direta quando B e nosso -- nao depende de casar nome nem
    #   valor. O fundo de terceiro, que so a carteira enxerga, aparece no
    #   grafo do fundo selecionado.
    a = (a.groupby(["origem", "destino"], as_index=False)
         .agg(saldo=("saldo", "sum"),
              nome_origem=("nome_origem", "first"),
              nome_destino=("nome_destino", "first"),
              classes=("classe", "nunique")))
    a["tipo_origem"] = "fundo"
    for f in (a, todas, saidas):
        for c in ("nome_origem", "nome_destino"):
            f[c] = f[c].astype(str).str.strip()

    # Base da concentracao: uma linha por (fundo, cotista, documento, canal),
    # somando as classes. Aqui o cotista continua separado por NOME -- e a
    # comparacao entre chaves que o painel faz.
    bruto = (p.assign(cotista=p["cotista"].astype(str).str.strip())
             .rename(columns={"origem": "doc"})
             .groupby(["destino", "nome_destino", "cotista", "doc",
                       "distribuidor"], dropna=False, as_index=False)["saldo"]
             .sum())
    return data, a, total, ext, todas, bruto, saidas


def montar_saidas(car: pd.DataFrame, passivo: pd.DataFrame,
                  nomes: dict) -> pd.DataFrame:
    """Posicoes de carteira em cota de fundo, por fundo comprador.

    `passivo` e a posicao de passivo inteira, usada para descobrir QUAL fundo
    do cadastro esta por tras de cada linha da carteira -- a carteira nao traz
    o CNPJ do alvo, so o nome de pregao do ativo.
    """
    linhas = []
    for cnpj in sorted(set(car["origem"]) | set(passivo["doc"])):
        g = car[car["origem"] == cnpj].copy()
        meu = passivo[passivo["doc"] == cnpj]
        mapa = casar(g, meu) if len(meu) and len(g) else {}

        # Linha do proprio fundo classificada como cota de fundo e recebivel
        # proprio, nao investimento: no MULTIPLICA sao R$ 808,0 mi em
        # "MULTIPLICA A VENC" e "MULTIPLICA VENCID".
        #
        # Mas o nome nao basta para decidir: "ADVENTURE 2 FUNDO DE
        # INVESTIMENTO" comeca com "ADVENTURE" e e OUTRO fundo (64.862.157 x
        # 43.165.094) -- descartar pelo prefixo apagava R$ 169,3 mi de
        # investimento real. So cai fora o que, alem do nome, nao casou com
        # nenhuma posicao de passivo.
        if len(g):
            curto = str(g["nome_origem"].iloc[0]).split()[0].upper()
            propria = (g["Nome_Ativo"].astype(str).str.upper()
                       .str.startswith(curto) & ~g.index.isin(mapa))
            g = g[~propria]

        vistos = set()
        for i, r in g.iterrows():
            j = mapa.get(i)
            if j is not None:
                q = meu.loc[j]
                destino, nome_d = q["destino"], q["nome_destino"]
                classe, externo = q["classe"], False
                vistos.add(destino)
            else:
                destino = "EXT-" + _re.sub(r"\W+", "", str(r["Nome_Ativo"]).upper())
                nome_d = str(r["Nome_Ativo"]).strip()
                classe, externo = ROTULO_G[grupo_do_ativo(r["Nome_Ativo"])], True
            linhas.append({
                "origem": cnpj, "nome_origem": r["nome_origem"],
                "destino": destino, "nome_destino": nome_d,
                "ativo": str(r["Nome_Ativo"]).strip(),
                "classe": classe, "grupo": grupo_da_classe(classe)
                if not externo else grupo_do_ativo(r["Nome_Ativo"]),
                "saldo": float(r["valor"]), "externo": externo,
                "fonte": "carteira"})

        # Nem toda carteira identifica o fundo comprado. O HOD II FIC FIDC tem
        # as 63 linhas dele em "Renda Fixa" com nome generico
        # ("MEZAN-SINGCTVM-CDI"), e o administrador nao diz o alvo -- pela
        # carteira ele nao investe em nada. Mas o passivo dos 21 fundos
        # investidos sabe: eles listam o HOD II como cotista, R$ 1,28 bi.
        # Entao o passivo completa o que a carteira nao identificou, sem
        # repetir o alvo que ela ja trouxe.
        nome_o = (str(g["nome_origem"].iloc[0]) if len(g)
                  else str(meu["nome_doc"].iloc[0]) if "nome_doc" in meu
                  else nomes.get(cnpj, cnpj))
        for _, q in meu.iterrows():
            if q["destino"] in vistos:
                continue
            linhas.append({
                "origem": cnpj, "nome_origem": nome_o,
                "destino": q["destino"], "nome_destino": q["nome_destino"],
                "ativo": "(pelo passivo do fundo investido)",
                "classe": q["classe"], "grupo": grupo_da_classe(q["classe"]),
                "saldo": float(q["saldo"]), "externo": False,
                "fonte": "passivo"})
    cols = ["origem", "nome_origem", "destino", "nome_destino", "ativo",
            "classe", "grupo", "saldo", "externo", "fonte"]
    return pd.DataFrame(linhas, columns=cols)


ROTULO_G = {k: rot for k, rot, _c, _p in GRUPOS}


def _carteira(db, data, p, no_cadastro, nomes) -> pd.DataFrame:
    """Le a carteira e devolve as saidas ja casadas com o cadastro."""
    car = db.consultar(SQL_CARTEIRA, (data, *BASES_COTA))
    if car.empty:
        return pd.DataFrame(columns=["origem", "nome_origem", "destino",
                                     "nome_destino", "ativo", "classe",
                                     "grupo", "saldo", "externo",
                                     "nome_alvo_carteira"])
    car["origem"] = _dig(car["cnpj_fundo"])
    car["nome_origem"] = car["Nome_Do_Fundo"].astype(str).str.strip()
    car = car[car["valor"].notna() & (car["valor"] > 0)]
    # Uma linha por (fundo cotista, fundo investido, classe). Sem somar, o
    # mesmo cotista sob duas grafias vira duas posicoes menores e nenhuma
    # casa com o valor da carteira.
    passivo = (p[p["origem"].isin(no_cadastro) & (p["origem"] != p["destino"])]
               .groupby(["origem", "destino", "nome_destino", "classe"],
                        as_index=False)["saldo"].sum()
               .rename(columns={"origem": "doc"}))
    return montar_saidas(car, passivo, nomes)


def estrutura(a: pd.DataFrame, todas: pd.DataFrame | None = None,
              universo: set | None = None,
              saidas: pd.DataFrame | None = None):
    """Niveis e estatistica por no. Nivel = maior distancia desde uma fonte.

    Quem entra no desenho vem de `a`; quanto cada um recebe e investe vem de
    `todas`, a relacao inteira. Sem isso o tamanho da bola e o tooltip ficam
    medindo o corte do desenho em vez do passivo do fundo.

    `universo` acrescenta fundos sem nenhuma ligacao fundo->fundo: eles nao
    tem aresta, mas existem e tem passivo, entao viram bola solta -- da para
    seleciona-los e ver os cotistas na tabela.
    """
    nos = sorted(set(a["origem"]) | set(a["destino"]) | (universo or set()))
    # Investe = o que a CARTEIRA mostra, inclusive em fundo de terceiro, que
    # nao vira bola mas e dinheiro aplicado. Recebe = o passivo. As arestas
    # desenhadas sao outra coisa: so o pedaco fundo-da-casa dessa relacao.
    sai, comprado = defaultdict(float), defaultdict(float)
    fonte_sai = a if saidas is None else saidas
    for o, s in zip(fonte_sai["origem"], fonte_sai["saldo"]):
        sai[o] += s
    for d, s in zip(a["destino"], a["saldo"]):
        comprado[d] += s
    passivo = defaultdict(float)
    if todas is not None:
        for d, s in zip(todas["destino"], todas["saldo"]):
            passivo[d] += s
    entra = {n: (passivo[n] if passivo.get(n) else comprado.get(n, 0.0))
             for n in nos}

    adj = defaultdict(set)
    for _, r in a.iterrows():
        adj[r["origem"]].add(r["destino"])

    nivel = {n: 0 for n in nos}
    for _ in range(len(nos)):
        mudou = False
        for x in nos:
            for y in adj[x]:
                if nivel[y] < nivel[x] + 1:
                    nivel[y] = nivel[x] + 1
                    mudou = True
        if not mudou:
            break

    nomes = {}
    if todas is not None:   # cobre o fundo sem nenhuma aresta desenhada
        nomes.update(dict(zip(todas["destino"], todas["nome_destino"])))
    nomes.update(dict(zip(a["origem"], a["nome_origem"])))
    nomes.update(dict(zip(a["destino"], a["nome_destino"])))

    info = {}
    for n in nos:
        papel = ("intermediario" if sai[n] > 0 and entra[n] > 0
                 else "fonte" if sai[n] > 0 else "destino")
        # Sem passivo na base = fundo que a casa nao administra. A bola existe
        # porque alguem daqui comprou cota dele.
        tipo = "fundo" if passivo.get(n) else "fundo_externo"
        # Tamanho da bola e ordem = PL, nao "investe + recebe". Somar os dois
        # conta o mesmo dinheiro duas vezes: o ativo de um FIC E o PL dele
        # aplicado. Isso inflava quem e FIC (ativo ~ PL, dobrava) e nao
        # inflava FIDC de recebivel direto -- o HOD II vinha em 1o com
        # R$ 2,85 bi de "total" tendo PL de R$ 1,40 bi, contra R$ 2,09 bi do
        # MULTIPLICA. O ranking media formato de fundo, nao tamanho.
        info[n] = {"nome": nomes.get(n, n), "nivel": nivel[n],
                   "investe": sai[n], "recebe": entra[n],
                   "total": entra[n], "papel": papel, "tipo": tipo}

    # Numero de cada no. Cabe na bola pequena, o nome nao. A ordem e por
    # exposicao, entao a chave ao lado do grafo ja e um ranking -- e o mesmo
    # numero vale nas duas vistas.
    for i, n in enumerate(sorted(info, key=lambda x: (-info[x]["total"], x)), 1):
        info[n]["num"] = i
    return info


# --- Desenho: mapa de rede --------------------------------------------------
#
# Bolas e ligacoes. A posicao nao e dado -- sai de uma simulacao de forcas
# (Fruchterman-Reingold): quem se liga se atrai, todo mundo se repele. O que
# ela revela e vizinhanca: os aglomerados que se formam sao grupos de fundos
# que se financiam entre si. O tamanho da bola e exposicao total (investe +
# recebe); a cor da aresta e a senioridade da cota.

LARG_REDE, ALT_REDE = 1160, 780
R_MIN, R_MAX = 4.5, 26.0
R_DENTRO = 9.5        # a partir daqui o numero cabe dentro da bola


def _raios(info: dict, nos: list) -> np.ndarray:
    t = np.array([info[n]["total"] for n in nos], float)
    tmax = t.max() if len(t) and t.max() > 0 else 1.0
    return R_MIN + np.sqrt(t / tmax) * (R_MAX - R_MIN)


def leiaute(info: dict, a: pd.DataFrame, larg: int, alt: int,
            iteracoes: int = 700):
    """Posicao das bolas. Deterministico: mesma carga, mesmo desenho."""
    nos = sorted(info, key=lambda n: (-info[n]["total"], n))
    idx = {n: i for i, n in enumerate(nos)}
    n = len(nos)
    r = _raios(info, nos)

    # Partida em espiral de Fermat (angulo aureo): os maiores no centro, nada
    # coincidente -- duas bolas no mesmo ponto nao teriam direcao de repulsao.
    ang = np.arange(n) * 2.39996323
    rad = np.sqrt(np.arange(n) / max(n - 1, 1)) * min(larg, alt) * 0.42
    pos = np.c_[larg / 2 + rad * np.cos(ang), alt / 2 + rad * np.sin(ang)]

    # Para o leiaute a direcao nao importa: o par se atrai nos dois sentidos.
    peso = defaultdict(float)
    for o, d, s in zip(a["origem"], a["destino"], a["saldo"]):
        i, j = idx.get(o), idx.get(d)
        if i is None or j is None or i == j:
            continue
        peso[(min(i, j), max(i, j))] += float(s)
    pares = np.array(list(peso) or [(0, 0)], int).reshape(-1, 2)
    w = np.array(list(peso.values()) or [1.0], float)
    w = 0.3 + 0.7 * np.log1p(w) / np.log1p(w.max())   # R$ comprime muito

    k = np.sqrt(larg * alt / max(n, 1)) * 0.82        # distancia de conforto
    temp = min(larg, alt) * 0.14
    esfria = temp / (iteracoes + 1)

    for _ in range(iteracoes):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt((delta ** 2).sum(-1))
        np.fill_diagonal(dist, np.inf)                # zera a auto-repulsao
        f = ((k * k / dist ** 2)[..., None] * delta).sum(1)

        dd = pos[pares[:, 0]] - pos[pares[:, 1]]
        dl = np.sqrt((dd ** 2).sum(-1)) + 1e-9
        at = ((dl / k) * w)[:, None] * (dd / dl[:, None])
        np.add.at(f, pares[:, 0], -at)
        np.add.at(f, pares[:, 1], at)

        f += (np.array([larg / 2, alt / 2]) - pos) * 0.014   # gravidade
        fl = np.sqrt((f ** 2).sum(-1)) + 1e-9
        pos += f / fl[:, None] * np.minimum(fl, temp)[:, None]
        temp -= esfria

    # Enquadra ANTES de separar: a escala encolhe distancia e nao encolhe raio,
    # entao escalar depois desfaz a separacao e as bolas voltam a se cobrir.
    m = R_MAX + 26
    lo, hi = pos.min(0), pos.max(0)
    vao = np.maximum(hi - lo, 1e-6)
    esc = min((larg - 2 * m) / vao[0], (alt - 2 * m) / vao[1], 1.7)
    pos = (pos - (lo + hi) / 2) * esc + np.array([larg / 2, alt / 2])

    # Bola sobre bola esconde numero: separa o que encostou. A bola grande
    # cede menos que a pequena -- o hub e a referencia visual, quem desvia
    # e o satelite. Na bola pequena o numero fica por fora, entao ela reserva
    # o espaco do rotulo alem do proprio raio.
    r_sep = r + np.where(r < R_DENTRO, 7.0, 0.0)
    inercia = (r / r.max())[:, None]
    for _ in range(300):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt((delta ** 2).sum(-1))
        np.fill_diagonal(dist, np.inf)
        minimo = r_sep[:, None] + r_sep[None, :] + 4
        if not (dist < minimo).any():
            break
        folga = np.where(dist < minimo, minimo - dist, 0.0)
        empurrao = (folga[..., None] * delta / dist[..., None]).sum(1)
        pos += empurrao * 0.5 / (0.35 + inercia)

    np.clip(pos, r[:, None] + 2,
            np.array([larg, alt]) - r[:, None] - 2, out=pos)
    return nos, {n_: tuple(pos[i]) for n_, i in idx.items()}, dict(zip(nos, r))


def _borda(px, py, qx, qy, d):
    """Ponto a `d` do centro (px,py), na direcao de (qx,qy)."""
    vx, vy = qx - px, qy - py
    L = (vx * vx + vy * vy) ** 0.5 or 1.0
    return px + vx / L * d, py + vy / L * d


def desenhar_rede(info: dict, a: pd.DataFrame) -> str:
    nos, pos, raio = leiaute(info, a, LARG_REDE, ALT_REDE)
    vmax = a["saldo"].max() or 1.0

    # A nuvem quase nunca tem o formato da tela. Recorta o quadro no que foi
    # desenhado, em vez de deixar faixa vazia dos lados. A folga de cima e
    # maior porque o rotulo fica acima da bola.
    xs = [pos[n][0] for n in nos]
    ys = [pos[n][1] for n in nos]
    rr = max(raio.values())
    x0, y0 = min(xs) - rr - 10, min(ys) - rr - 24
    larg = max(xs) + rr + 10 - x0
    alt = max(ys) + rr + 12 - y0

    p = [f'<svg viewBox="{x0:.0f} {y0:.0f} {larg:.0f} {alt:.0f}" '
         f'class="gr gr-rede" id="grafo-rede" '
         f'role="img" aria-label="Mapa de rede da exposicao entre fundos">',
         '<defs><marker id="seta" viewBox="0 0 8 8" refX="7" refY="4" '
         'markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" '
         'orient="auto"><path d="M0 0 L8 4 L0 8 z" '
         'fill="var(--serie)"/></marker></defs>']

    p.append('<g id="arestas-rede">')
    for _, e in a.sort_values("saldo").iterrows():
        o, d = e["origem"], e["destino"]
        if o not in pos or d not in pos or o == d:
            continue
        (x1, y1), (x2, y2) = pos[o], pos[d]
        vx, vy = x2 - x1, y2 - y1
        L = (vx * vx + vy * vy) ** 0.5 or 1.0
        # Curva para o lado: ida e volta entre os mesmos dois fundos nao se
        # sobrepoem, e o feixe que chega num hub abre em leque.
        curva = min(L * 0.14, 52)
        cx, cy = (x1 + x2) / 2 - vy / L * curva, (y1 + y2) / 2 + vx / L * curva
        ax, ay = _borda(x1, y1, cx, cy, raio[o])
        bx, by = _borda(x2, y2, cx, cy, raio[d] + 7)
        w = 0.5 + (e["saldo"] / vmax) ** 0.5 * 4.6
        nc = int(e.get("classes", 1) or 1)
        p.append(
            f'<path class="ar" data-o="{o}" data-d="{d}" '
            f'stroke-width="{w:.2f}" marker-end="url(#seta)" '
            f'd="M{ax:.1f} {ay:.1f} Q{cx:.1f} {cy:.1f} {bx:.1f} {by:.1f}">'
            f'<title>{_e(e["nome_origem"])} → {_e(e["nome_destino"])}\n'
            f'R$ {e["saldo"]:,.2f}'
            f'{f" em {nc} classes" if nc > 1 else ""}</title></path>')
    p.append("</g>")

    p.append('<g id="nos-rede">')
    for n in sorted(nos, key=lambda n: info[n]["total"]):
        v, (x, y), r = info[n], pos[n], raio[n]
        # Numero dentro da bola quando cabe; senao, encostado em cima dela.
        if r >= R_DENTRO:
            rot = (f'<text class="nd" x="{x:.1f}" y="{y:.1f}" '
                   f'style="font-size:{min(r * 1.05, 15):.1f}px">'
                   f'{v["num"]}</text>')
        else:
            rot = (f'<text class="nf" x="{x:.1f}" y="{y - r - 3.5:.1f}">'
                   f'{v["num"]}</text>')
        p.append(
            f'<g class="no p-{v["papel"]} t-{v["tipo"]}" data-n="{n}">'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}"/>{rot}'
            f'<title>{v["num"]} · {_e(v["nome"])}\nnível {v["nivel"]} · '
            f'{v["papel"]} · {v["tipo"]}\ninveste R$ {v["investe"]:,.0f}\n'
            f'recebe R$ {v["recebe"]:,.0f}</title></g>')
    p.append("</g></svg>")
    return "".join(p)


def chaves(info: dict) -> tuple[str, str]:
    """As duas colunas laterais: numero, nome e o que o fundo e."""
    ordem = sorted(info, key=lambda n: info[n]["num"])
    meio = (len(ordem) + 1) // 2

    def coluna(parte):
        return "".join(
            f'<li class="chave p-{info[n]["papel"]} t-{info[n]["tipo"]}" '
            f'data-n="{n}" title="{_e(info[n]["nome"])} — exposição '
            f'R$ {info[n]["total"]:,.0f}"><i class="pt"></i>'
            f'<b>{info[n]["num"]}</b><span class="nm">'
            f'{_e(info[n]["nome"])}</span></li>' for n in parte)

    return coluna(ordem[:meio]), coluna(ordem[meio:])


ROTULO_TIPO = {"fundo": "fundo da casa", "fundo_externo": "fundo de terceiro",
               "investidor": "investidor direto"}


# --- Concentracao do passivo ------------------------------------------------
#
# A mesma leitura de analise_passivo.py, para os 3 fundos que ela cobria,
# aplicada a todos. A pergunta: quem e a contraparte muda conforme a chave de
# agregacao -- nome, documento ou distribuidor. O documento e o que se
# aproxima de grupo economico; nome infla a contagem quando o mesmo titular
# aparece sob varias grafias ou o distribuidor abre conta por cliente final.

LIMITE_SEM_DOC = 50.0    # acima disso a visao por documento nao se sustenta
ALERTA_SEM_DOC = 5.0     # acima disso ela sai com ressalva
TOPO_COLAPSO = 10


def hhi(serie: pd.Series) -> float:
    """Herfindahl em pontos (0 a 10.000). Mede concentracao, nao tamanho."""
    s = serie[serie > 0]
    if s.empty:
        return 0.0
    return float(((s / s.sum()) ** 2).sum() * 10000)


def _rotulo_doc(nomes: pd.Series) -> str:
    """Nome de um documento com varias grafias: o prefixo comum, ou o maior."""
    n = nomes.astype(str)
    if n.nunique() == 1:
        return n.iloc[0]
    partes = [x.split() for x in n]
    comum = []
    for i in range(min(len(x) for x in partes)):
        t = {x[i] for x in partes}
        if len(t) != 1:
            break
        comum.append(partes[0][i])
    base = " ".join(comum) if comum else n.iloc[0]
    return f"{base} ({n.nunique()} contas)"


def _medidas(g: pd.DataFrame, chave: str) -> dict:
    s = g.groupby(chave)["saldo"].sum().sort_values(ascending=False)
    return {"n": int(len(s)), "maior": float(s.iloc[0]),
            "top3": float(s.head(3).sum()), "top5": float(s.head(5).sum()),
            "hhi": round(hhi(s), 1)}


def concentracao(bruto: pd.DataFrame) -> tuple[dict, list]:
    """Concentracao por fundo nas tres chaves. Devolve (por_fundo, avisos).

    Fundo sem informacao que sustente a leitura fica de fora e entra em
    `avisos` -- e melhor nao mostrar do que mostrar um indice calculado sobre
    documento que nao identifica ninguem.
    """
    por, avisos = {}, []
    for cnpj, g in bruto.groupby("destino"):
        nome = str(g["nome_destino"].iloc[0])
        pl = float(g["saldo"].sum())
        if pl <= 0:
            avisos.append((cnpj, nome, "PL zerado ou negativo no passivo"))
            continue

        g = g.copy()
        ruim = g["doc"].str.strip("0") == ""
        v_sem = float(g.loc[ruim, "saldo"].sum())
        pct_sem = v_sem / pl * 100
        if pct_sem > LIMITE_SEM_DOC:
            avisos.append((cnpj, nome, f"{pct_sem:.1f}% do passivo sem "
                                       f"CPF/CNPJ válido no cadastro"))
            continue

        # Documento invalido nao colapsa com ninguem: cada um fica por si, em
        # vez de virar um unico cotista gigante chamado "0000000".
        g["doc"] = g["doc"].where(~ruim, "SEMDOC-" + g["cotista"])
        rot = {d: _rotulo_doc(x.sort_values("saldo", ascending=False)["cotista"])
               for d, x in g.groupby("doc")}
        g["por_doc"] = g["doc"].map(rot)
        g["dist"] = (g["distribuidor"].fillna("").astype(str).str.strip()
                     .replace("", "(sem distribuidor)"))

        com_dist = float(g.loc[g["dist"] != "(sem distribuidor)",
                               "saldo"].sum()) / pl * 100

        # Documentos sob mais de um nome: e de onde vem a diferenca entre as
        # duas primeiras chaves.
        nn = g.groupby("doc")["cotista"].nunique()
        colapso = []
        for d in nn[nn > 1].index:
            x = g[g["doc"] == d]
            colapso.append({"doc": d, "nome": rot[d], "nomes": int(nn[d]),
                            "v": round(float(x["saldo"].sum()), 2)})
        colapso.sort(key=lambda r: -r["v"])

        por[cnpj] = {
            "pl": round(pl, 2),
            "nome": _medidas(g, "cotista"),
            "doc": _medidas(g, "por_doc"),
            "dist": _medidas(g, "dist"),
            "sem_doc": round(pct_sem, 2),
            "cob_dist": round(com_dist, 1),
            "colapso": colapso[:TOPO_COLAPSO],
            "n_colapso": len(colapso),
        }
    return por, avisos


def dados_json(info: dict, todas: pd.DataFrame, saidas: pd.DataFrame,
               conc: dict, avisos: list) -> str:
    """Nos e arestas para a tabela de detalhe montar no clique.

    Vai a relacao inteira, nao a desenhada: a tabela precisa fechar com o
    passivo do fundo. Quem nao esta no desenho entra sem numero -- aparece
    na lista, so nao da para clicar para ir ate a bola dele.
    """
    nos = {n: {"n": v["num"], "nome": v["nome"], "nivel": v["nivel"],
               "tipo": ROTULO_TIPO.get(v["tipo"], v["tipo"]),
               "papel": v["papel"],
               "investe": round(v["investe"], 2),
               "recebe": round(v["recebe"], 2)}
           for n, v in info.items()}
    # Quem nao e fundo da casa nao tem bola, entao o nome vem daqui. O mesmo
    # documento aparece sob varias grafias no cadastro ("BANCO BTG PACTUAL
    # S.A." e "BTG 005420392"): fica a da maior posicao, nao a primeira que
    # sair da consulta.
    fora = (todas[~todas["origem"].isin(nos)]
            .sort_values("saldo", ascending=False)
            .drop_duplicates("origem"))
    for o, nome, tipo in zip(fora["origem"], fora["nome_origem"],
                             fora["tipo_origem"]):
        nos[o] = {"n": None, "nome": nome,
                  "tipo": ROTULO_TIPO.get(tipo, tipo), "fora": True}

    # `arestas` e o passivo (quem investe no fundo); `saidas` e a carteira
    # (em que fundo ele aplica). Sao bases diferentes de proposito -- juntar
    # as duas contaria duas vezes a mesma relacao entre fundos da casa.
    for d, nome in zip(saidas["destino"], saidas["nome_destino"]):
        if d not in nos:
            nos[d] = {"n": None, "nome": nome,
                      "tipo": "fundo de terceiro", "fora": True}

    pacote = {
        "nos": nos,
        "arestas": [{"o": r.origem, "d": r.destino, "g": r.grupo,
                     "c": str(r.classe), "v": round(float(r.saldo), 2)}
                    for r in todas.itertuples()],
        "saidas": [{"o": r.origem, "d": r.destino, "g": r.grupo,
                    "c": str(r.classe), "v": round(float(r.saldo), 2),
                    "a": str(r.ativo), "x": bool(r.externo),
                    "f": str(r.fonte)}
                   for r in saidas.itertuples()],
        "conc": conc,
        "semconc": {c: m for c, _n, m in avisos},
    }
    # "</script>" dentro do JSON fecharia o bloco antes da hora.
    return (json.dumps(pacote, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c"))


# --- Desenho: colunas por nivel ---------------------------------------------

ALT_NO, ESPACO, LARG_NO = 17, 7, 150


def desenhar(info: dict, a: pd.DataFrame) -> tuple[str, int]:
    niveis = sorted({v["nivel"] for v in info.values()})
    col = {k: i for i, k in enumerate(niveis)}
    passo = ALT_NO + ESPACO
    por_nivel = defaultdict(list)
    for n, v in info.items():
        por_nivel[v["nivel"]].append(n)
    for k in por_nivel:
        por_nivel[k].sort(key=lambda n: -info[n]["total"])

    maior_col = max(len(v) for v in por_nivel.values())
    alt = maior_col * passo + 96
    larg = len(niveis) * (LARG_NO + 56) + 40

    pos = {}
    for k, lista in por_nivel.items():
        x = 20 + col[k] * (LARG_NO + 56)
        y0 = 70 + (maior_col - len(lista)) * passo / 2
        for i, n in enumerate(lista):
            pos[n] = (x, y0 + i * passo)

    vmax = a["saldo"].max()
    partes = [f'<svg viewBox="0 0 {larg} {alt}" class="gr" id="grafo" '
              f'role="img" aria-label="Grafo de exposicao entre fundos">']

    partes.append('<g id="arestas">')
    for _, r in a.sort_values("saldo").iterrows():
        o, d = r["origem"], r["destino"]
        if o not in pos or d not in pos:
            continue
        x1, y1 = pos[o][0] + LARG_NO, pos[o][1] + ALT_NO / 2
        x2, y2 = pos[d][0], pos[d][1] + ALT_NO / 2
        dx = max((x2 - x1) * 0.45, 26)
        w = 0.6 + (r["saldo"] / vmax) ** 0.5 * 5.4
        partes.append(
            f'<path class="ar" data-o="{o}" data-d="{d}" '
            f'stroke-width="{w:.2f}" '
            f'd="M{x1:.1f} {y1:.1f} C{x1 + dx:.1f} {y1:.1f} '
            f'{x2 - dx:.1f} {y2:.1f} {x2:.1f} {y2:.1f}">'
            f'<title>{_e(r["nome_origem"])} → {_e(r["nome_destino"])}\n'
            f'R$ {r["saldo"]:,.2f}</title></path>')
    partes.append("</g>")

    partes.append('<g id="nos">')
    for n, v in info.items():
        x, y = pos[n]
        partes.append(
            f'<g class="no p-{v["papel"]} t-{v["tipo"]}" data-n="{n}">'
            f'<rect x="{x}" y="{y}" width="{LARG_NO}" height="{ALT_NO}" rx="4"/>'
            f'<text x="{x + 7}" y="{y + 12}"><tspan class="cod">'
            f'{v["num"]}</tspan> {_e(v["nome"][:20])}</text>'
            f'<title>{v["num"]} · {_e(v["nome"])}\nnível {v["nivel"]} · {v["papel"]}'
            f' · {v["tipo"]}\n'
            f'investe R$ {v["investe"]:,.0f}\nrecebe R$ {v["recebe"]:,.0f}'
            f'</title></g>')
    partes.append("</g>")

    for k in niveis:
        x = 20 + col[k] * (LARG_NO + 56)
        partes.append(f'<text x="{x}" y="42" class="rot-nivel">nível {k}</text>')
        partes.append(f'<text x="{x}" y="58" class="rot-peq">'
                      f'{len(por_nivel[k])} fundo(s)</text>')

    partes.append("</svg>")
    return "".join(partes), alt


def _e(t) -> str:
    return html.escape(str(t if t is not None else ""))


def _tokens(modo: str) -> str:
    return "".join(f"    --{k}: {v};\n" for k, v in CORES[modo].items())


def _logo() -> str:
    if not LOGO.exists():
        return ""
    return ("data:image/png;base64,"
            + base64.b64encode(LOGO.read_bytes()).decode("ascii"))


def montar(data: str, a: pd.DataFrame, info: dict, total_passivo: float,
           ext: pd.DataFrame, todas: pd.DataFrame, bruto: pd.DataFrame,
           saidas: pd.DataFrame) -> str:
    svg_rede = desenhar_rede(info, a)
    svg_niveis, _alt = desenhar(info, a)
    chave_esq, chave_dir = chaves(info)
    conc, avisos = concentracao(bruto)
    dados_js = dados_json(info, todas, saidas, conc, avisos)
    # O grafo do fundo selecionado e desenhado no navegador, mas com as classes
    # CSS de grafo_fundo -- mesma linguagem visual do relatorio por fundo.
    estilo_gf = gf.ESTILO if gf else ""
    sem = "".join(
        f'<li><b>{_e(n)}</b> — {_e(m)}</li>' for _c, n, m in
        sorted(avisos, key=lambda x: x[1]))
    bloco_sem = ("" if not avisos else f"""
<h2>Fundos sem concentração de passivo</h2>
<p class="sub">{len(avisos)} fundo(s) ficaram de fora do painel de
   concentração: o cadastro não sustenta a leitura por documento. Eles
   continuam no grafo e nas tabelas de cotistas.</p>
<div class="cartao"><ul class="faltou">{sem}</ul></div>""")
    # Rotulo e cor de cada senioridade saem de GRUPOS, para as abas do painel
    # do fundo nao divergirem do resto da pagina.
    grupos_js = json.dumps({k: [rot, cor] for k, rot, cor, _p in GRUPOS},
                           ensure_ascii=False)
    ordem_js = json.dumps([k for k, *_ in GRUPOS])
    logo = _logo()
    marca = (f'<img src="{logo}" alt="Multiplica">' if logo
             else '<div class="titulo">MULTIPLICA</div>')

    # A senioridade saiu do desenho, entao os numeros por classe vem da
    # relacao inteira -- que e onde ela continua existindo.
    por_grupo = todas.groupby("grupo")["saldo"].agg(["size", "sum"])
    papeis = defaultdict(int)
    for v in info.values():
        papeis[v["papel"]] += 1

    # O que a casa aplica em cota de fundo vem da carteira; e o de terceiro
    # e a parte que o passivo nao via.
    aplicado = float(saidas["saldo"].sum())
    de_terceiro = float(saidas.loc[saidas["externo"], "saldo"].sum())
    por_sai = saidas.groupby("grupo")["saldo"].sum()
    kpis = [
        ("Aplicado em cota de fundo", f"R$ {aplicado / 1e9:,.2f} bi",
         f"{len(saidas)} posições em {saidas['destino'].nunique()} fundos"),
        ("Em fundo de terceiro", f"R$ {de_terceiro / 1e9:,.2f} bi",
         f"{de_terceiro / aplicado * 100:.1f}% do aplicado — "
         f"invisível pelo passivo" if aplicado else "—"),
        ("Em cota subordinada",
         f"R$ {por_sai.get('sub', 0.0) / 1e9:,.2f} bi",
         "primeira perda de carteira de terceiro"),
        ("Intermediários", f"{papeis['intermediario']}",
         "compram e são comprados — risco empilhado"),
        ("Vem de fora", f"R$ {ext['saldo'].sum() / 1e9:,.2f} bi",
         f"{ext['origem'].nunique()} investidores que não são fundo da casa; "
         f"aparecem na tabela, não no grafo"),
    ]

    # Os maiores investidores de fora, com quantos fundos cada um alcanca.
    top_ext = (ext.groupby("origem")
               .agg(nome=("cotista", "first"), saldo=("saldo", "sum"),
                    fundos=("cnpj_destino", "nunique"))
               .nlargest(15, "saldo"))
    linhas_ext = "".join(
        f'<tr><td><b>{_e(str(r["nome"])[:44])}</b></td>'
        f'<td>{"fundo" if parece_fundo(r["nome"]) else "direto"}</td>'
        f'<td>{int(r["fundos"])}</td><td>{r["saldo"]:,.2f}</td>'
        f'<td>{r["saldo"] / ext["saldo"].sum() * 100:.2f}%</td></tr>'
        for _, r in top_ext.iterrows())

    inter = sorted((v for v in info.values() if v["papel"] == "intermediario"),
                   key=lambda v: -v["total"])[:12]
    linhas_inter = "".join(
        f'<tr><td><b>{_e(v["nome"])}</b></td><td>{v["nivel"]}</td>'
        f'<td>{v["recebe"]:,.0f}</td><td>{v["investe"]:,.0f}</td></tr>'
        for v in inter)

    # Quem compra subordinada de quem sai da CARTEIRA: e o lado do ativo, e
    # so ela enxerga o fundo de terceiro.
    sub = saidas[saidas["grupo"] == "sub"].nlargest(12, "saldo")
    linhas_sub = "".join(
        f'<tr><td><b>{_e(r["nome_origem"])}</b></td>'
        f'<td>{_e(r["nome_destino"])}'
        + ('<span class="tp">terceiro</span>' if r["externo"] else "")
        + f'</td><td>{_e(r["classe"])}</td>'
        f'<td>{r["saldo"]:,.2f}</td></tr>' for _, r in sub.iterrows())

    hoje = dt.date.today()
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grafo de exposição entre fundos — Multiplica</title>
<style>
:root {{ color-scheme: light;
{_tokens("claro")}}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  color-scheme: dark;
{_tokens("escuro")}}} }}
:root[data-theme="dark"] {{ color-scheme: dark;
{_tokens("escuro")}}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--plano); color: var(--tinta); padding-bottom: 64px; }}
.limite {{ max-width: 1400px; margin: 0 auto; padding: 0 24px; }}
.marca {{ background: {NAVY}; padding: 26px 0 28px; margin-bottom: 8px; }}
.marca .limite {{ display: flex; align-items: center; justify-content: space-between;
  gap: 20px; flex-wrap: wrap; }}
.marca img {{ height: 84px; width: auto; display: block; }}
.marca .titulo {{ color: #fff; font-size: 19px; font-weight: 640; }}
.marca .quando {{ color: {AZUL_PALIDO}; font-size: 13px; margin-top: 2px; }}
.marca .quando b {{ color: {CIANO}; font-weight: 620; }}
.tema {{ display: inline-flex; align-items: center; gap: 7px; background: transparent;
  color: {AZUL_PALIDO}; border: 1px solid rgba(255,255,255,0.28);
  border-radius: 999px; padding: 7px 14px; font: inherit; font-size: 12.5px;
  cursor: pointer; line-height: 1; }}
h1 {{ font-size: 22px; margin: 0 0 4px; font-weight: 650; }}
h2 {{ font-size: 15px; margin: 40px 0 4px; font-weight: 650; }}
.sub {{ color: var(--tinta2); font-size: 13px; margin: 0 0 8px; }}
.nota {{ color: var(--tinta3); font-size: 12px; margin: 0 0 14px; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px,1fr));
  gap: 12px; margin-top: 20px; }}
.kpi {{ background: var(--superficie); border: 1px solid var(--borda);
  border-radius: 10px; padding: 14px 16px; }}
.kpi .r {{ color: var(--tinta2); font-size: 12px; }}
.kpi .v {{ font-size: 26px; font-weight: 640; margin: 4px 0 2px; line-height: 1.1; }}
.kpi .n {{ color: var(--tinta3); font-size: 11px; }}
.cartao {{ background: var(--superficie); border: 1px solid var(--borda);
  border-radius: 10px; padding: 16px 18px; margin-top: 8px; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12.5px; }}
th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--borda); }}
th {{ color: var(--tinta2); font-weight: 600; font-size: 11.5px;
  text-transform: uppercase; letter-spacing: .04em; }}
td:nth-child(n+3) {{ text-align: right; font-variant-numeric: tabular-nums; }}
.barra {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
  margin: 12px 0 4px; }}
.filtro {{ display: inline-flex; align-items: center; gap: 6px; font: inherit;
  font-size: 12px; cursor: pointer; background: var(--superficie);
  color: var(--tinta2); border: 1px solid var(--borda); border-radius: 999px;
  padding: 6px 12px; }}
.filtro .ch {{ width: 9px; height: 9px; border-radius: 2px; display: inline-block; }}
.filtro.ativo {{ color: var(--tinta); border-color: var(--tinta3); }}
.filtro:not(.ativo) {{ opacity: .42; }}
.gr {{ display: block; width: 100%; height: auto; min-width: 900px; }}
.gr-rede {{ min-width: 0; }}
.vista {{ display: none; }}
.vista.ativa {{ display: block; }}
.aba {{ font: inherit; font-size: 12px; cursor: pointer; background: var(--superficie);
  color: var(--tinta2); border: 1px solid var(--borda); border-radius: 999px;
  padding: 6px 14px; }}
.aba.ativa {{ background: var(--tinta); color: var(--superficie);
  border-color: var(--tinta); }}
.ar {{ fill: none; opacity: .42; stroke: var(--serie);
  transition: opacity .12s; }}
.ar.oculta {{ display: none; }}
.ar.apaga {{ opacity: .06; }}
.ar.acesa {{ opacity: .95; }}
.no rect {{ fill: var(--superficie); stroke: var(--borda); }}
.no text {{ fill: var(--tinta); font-size: 10.5px;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
.no {{ cursor: pointer; }}
.no.p-intermediario rect {{ stroke: var(--atencao); stroke-width: 1.4; }}
.no.p-fonte rect {{ stroke: var(--serie); }}
/* Investidor que nao e fundo do cadastro: tracejado, para nao se confundir
   com fundo da casa. Fundo de terceiro tambem, mas em azul. */
.no.t-investidor rect {{ stroke-dasharray: 3 2; stroke: var(--tinta3); }}
.no.t-fundo_externo rect {{ stroke-dasharray: 3 2; stroke: var(--serie); }}
.no.apaga {{ opacity: .22; }}
/* Bolas. Fundo da casa e solido; quem e so investidor vem vazado e tracejado,
   para a vista separar o que a gente administra do que chega de fora. */
.gr-rede .no circle {{ fill: color-mix(in srgb, var(--serie) 22%, var(--superficie));
  stroke: var(--serie); stroke-width: 1.2; }}
.gr-rede .no.p-intermediario circle {{ stroke: var(--atencao); stroke-width: 2.2;
  fill: color-mix(in srgb, var(--atencao) 20%, var(--superficie)); }}
.gr-rede .no.t-investidor circle {{ fill: none; stroke: var(--tinta3);
  stroke-dasharray: 3 2; }}
.gr-rede .no.t-fundo_externo circle {{ fill: none; stroke: var(--serie);
  stroke-dasharray: 3 2; }}
.gr-rede .no text {{ text-anchor: middle; pointer-events: none;
  font-variant-numeric: tabular-nums; }}
.gr-rede .no .nd {{ fill: var(--tinta); font-weight: 640;
  dominant-baseline: central; }}
.gr-rede .no .nf {{ fill: var(--tinta2); font-size: 8.5px; paint-order: stroke;
  stroke: var(--superficie); stroke-width: 2.6px; stroke-linejoin: round; }}
.gr-rede .no:hover circle {{ stroke-width: 2.8; }}
.no .cod {{ fill: var(--tinta3); font-weight: 640; }}
/* Chave: numero e nome, nas duas laterais do desenho. */
.comchave {{ display: flex; gap: 14px; align-items: flex-start;
  flex-wrap: wrap; }}
.vistas {{ flex: 1 1 520px; min-width: 0; }}
.chaves {{ flex: 1 1 214px; max-width: 260px; margin: 0; padding: 0;
  list-style: none; font-size: 10.5px; line-height: 1.34; }}
.chave {{ display: flex; align-items: center; gap: 5px; cursor: pointer;
  padding: 1px 4px; border-radius: 3px; color: var(--tinta2); }}
.chave b {{ min-width: 21px; text-align: right; color: var(--tinta);
  font-weight: 640; font-variant-numeric: tabular-nums; }}
.chave .nm {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.chave .pt {{ width: 7px; height: 7px; border-radius: 50%; flex: 0 0 7px;
  background: var(--serie); }}
.chave.p-intermediario .pt {{ background: var(--atencao); }}
.chave.t-investidor .pt {{ background: transparent;
  box-shadow: inset 0 0 0 1px var(--tinta3); }}
.chave.t-fundo_externo .pt {{ background: transparent;
  box-shadow: inset 0 0 0 1px var(--serie); }}
.chave:hover {{ background: var(--plano); color: var(--tinta); }}
.chave.sel {{ background: color-mix(in srgb, var(--serie) 20%, var(--superficie));
  color: var(--tinta); }}
/* Detalhe do fundo selecionado */
.detalhe {{ margin-top: 12px; }}
/* As duas partes do painel do fundo, cada uma com a sua barra de botoes. O filete
   e a distancia dizem que sao perguntas diferentes: a de cima e sobre o passivo
   do fundo, a de baixo sobre o que ele compra. Sem a separacao as duas barras de
   botoes pareciam uma so, com o dobro dos botoes. */
.detalhe .gf-lado + .gf-lado {{ margin-top: 26px; padding-top: 18px;
  border-top: 2px solid var(--borda); }}
.detalhe .gf-lado > h3.gf-tit {{ font-size: 14.5px; }}
.detalhe .vazio {{ color: var(--tinta3); font-size: 12.5px; margin: 2px 0; }}
.detalhe .cabeca {{ display: flex; align-items: baseline; gap: 10px;
  flex-wrap: wrap; border-bottom: 1px solid var(--borda); padding-bottom: 8px;
  margin-bottom: 4px; }}
.detalhe h3 {{ font-size: 15px; margin: 0; font-weight: 650; }}
.detalhe .cabeca span {{ color: var(--tinta2); font-size: 12px; }}
.detalhe h4 {{ font-size: 12px; margin: 14px 0 2px; font-weight: 620;
  color: var(--tinta2); text-transform: uppercase; letter-spacing: .04em; }}
.detalhe h4 span {{ text-transform: none; letter-spacing: 0;
  color: var(--tinta3); font-weight: 500; }}
.duas {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px 22px; }}
@media (max-width: 900px) {{ .duas {{ grid-template-columns: 1fr; }} }}
.detalhe td {{ cursor: pointer; }}
.detalhe tr.so-lista td {{ cursor: default; color: var(--tinta2); }}
.detalhe td:nth-child(1) {{ text-align: right; color: var(--tinta3);
  font-variant-numeric: tabular-nums; width: 28px; }}
.detalhe td:nth-child(2) {{ text-align: left; }}
.detalhe tr:hover td {{ background: var(--plano); }}
/* Cabecalho de classe: subtotal da senioridade. */
.detalhe tr.cab-classe td {{ background: var(--plano); cursor: default;
  font-weight: 620; color: var(--tinta); border-bottom-color: var(--tinta3);
  padding-top: 10px; }}
.detalhe tr.cab-classe td span {{ font-weight: 500; color: var(--tinta3); }}
.detalhe tr.cab-classe:hover td {{ background: var(--plano); }}
.detalhe tr.cab-classe + tr td {{ padding-top: 5px; }}
.detalhe .tp {{ font-size: 10px; color: var(--tinta3); border: 1px solid
  var(--borda); border-radius: 3px; padding: 0 4px; margin-left: 5px;
  white-space: nowrap; }}
/* Concentracao do passivo, do fundo selecionado. */
.conc {{ margin-top: 22px; border-top: 1px solid var(--borda);
  padding-top: 6px; }}
.conc h4 span {{ text-transform: none; letter-spacing: 0; font-weight: 500;
  color: var(--tinta3); }}
.conc td {{ cursor: default; }}
.conc td:nth-child(1), .conc td:nth-child(2) {{ text-align: left; }}
.conc tr.forte td {{ font-weight: 620; color: var(--tinta); }}
.conc .hhi {{ font-weight: 640; }}
.conc code {{ font-size: 11px; color: var(--tinta3); }}
.conc .semdado {{ color: var(--tinta3); }}
.conc .ress {{ border-left: 3px solid var(--atencao); padding-left: 10px; }}
.conc .ress b {{ color: var(--atencao); }}
.sem-conc {{ border-left: 3px solid var(--atencao); padding-left: 10px;
  color: var(--tinta2); font-size: 12.5px; margin-top: 18px; }}
.sem-conc b {{ color: var(--atencao); }}
ul.faltou {{ margin: 0; padding-left: 18px; font-size: 12.5px;
  color: var(--tinta2); line-height: 1.6; }}
ul.faltou b {{ color: var(--tinta); }}
{estilo_gf}
/* Regua sob o cabecalho das barras. */
.gf-bl {{ stroke: var(--borda); stroke-width: 1; }}
/* Faixa de onde nascem as linhas do ativo: e ela que absorve a primeira
   perda das escolhas de carteira. */
.gf-faixa.gf-ancora {{ stroke: var(--tinta); stroke-width: 1.6;
  stroke-dasharray: 5 3; }}
.detalhe .nada {{ color: var(--tinta3); font-size: 12px; padding: 8px 2px; }}
.pastilha {{ display: inline-block; width: 8px; height: 8px; border-radius: 2px;
  margin-right: 6px; vertical-align: -1px; }}
.rot-nivel {{ fill: var(--tinta2); font-size: 11.5px; font-weight: 600;
  font-family: system-ui, sans-serif; }}
.rot-peq {{ fill: var(--tinta3); font-size: 10.5px;
  font-family: system-ui, sans-serif; }}
.rodape {{ color: var(--tinta3); font-size: 11.5px; margin-top: 40px;
  border-top: 1px solid var(--borda); padding-top: 14px; }}
</style></head><body>

<header class="marca"><div class="limite">{marca}
<div style="text-align:right">
  <div class="titulo">Grafo de exposição entre fundos</div>
  <div class="quando">Quem aplica em quem, e em que classe &middot;
     posição <b>{dt.date.fromisoformat(data):%d/%m/%Y}</b></div>
  <div style="margin-top:10px"><button class="tema" id="tema" type="button">
    <span class="simbolo">&#9790;</span><span class="rotulo">Tema escuro</span>
  </button></div>
</div></div></header>

<main class="limite">
<h1>A senioridade está na aresta</h1>
<p class="sub">Comprar subordinada de outro fundo é assumir primeira perda de
   uma carteira que não é sua. Comprar sênior é outra coisa. O grafo separa as
   duas.</p>

<div class="kpis">{"".join(
    f'<div class="kpi"><div class="r">{_e(r)}</div><div class="v">{_e(v)}</div>'
    f'<div class="n">{_e(n)}</div></div>' for r, v, n in kpis)}</div>

<h2>O grafo</h2>
<p class="nota">O grafo é só de fundos: uma bola por fundo, a seta apontando
   para o fundo investido. <b>O tamanho é o PL</b> — a soma do passivo do
   fundo — e a chave ao lado está na mesma ordem. Somar o que ele aplica ao
   que recebe contaria o mesmo dinheiro duas vezes: o ativo de um fundo de
   cotas <i>é</i> o PL dele aplicado. A posição não é dado: sai de uma
   simulação de forças, em que quem se liga se atrai, então o que ela mostra é
   vizinhança — os aglomerados são grupos que se financiam entre si. Contorno
   âmbar = intermediário, compra e é comprado. Bola solta = fundo sem ligação
   com outro fundo da casa; o passivo dele está todo fora.
   <b>Os cotistas que não são fundo da casa não viram bola</b> — eles aparecem
   na tabela do fundo selecionado, logo abaixo.</p>
<div class="barra">
  <button type="button" class="aba ativa" data-v="rede">Mapa de rede</button>
  <button type="button" class="aba" data-v="niveis">Camadas da cadeia</button>
  <span style="flex:1"></span>
  <button type="button" class="filtro" id="limpar"
    style="border-style:dashed">limpar seleção</button></div>
<p class="nota">Clique numa bola para isolar as ligações dela — a seleção vale
   nas duas vistas. Use os filtros para ver uma classe por vez; passe o mouse
   sobre a aresta para o valor. A chave dos lados é clicável e está em ordem
   de exposição — o nº 1 é o maior.</p>
<div class="cartao"><div class="comchave">
  <ul class="chaves">{chave_esq}</ul>
  <div class="vistas">
    <div class="vista ativa" data-v="rede">{svg_rede}</div>
    <div class="vista" data-v="niveis">{svg_niveis}</div>
  </div>
  <ul class="chaves">{chave_dir}</ul>
</div></div>

<div id="detalhe" class="cartao detalhe">
  <p class="vazio">Nenhum fundo selecionado — clique numa bola, num número da
     chave ou num retângulo. O painel abre em <b>duas partes</b>: primeiro
     <b>quem investe no fundo</b> (o passivo inteiro dele: fundos, cotistas
     diretos e pessoas físicas), depois <b>onde o fundo é investidor</b>.</p>
  <div class="corpo" hidden>
    <div class="cabeca"><h3 id="det-nome"></h3><span id="det-sub"></span></div>
    <div id="conc" class="conc"></div>
  </div>
</div>

<h2>Os intermediários</h2>
<p class="sub">Compram e são comprados. É neles que o risco se empilha — a perda
   de uma carteira lá embaixo sobe pela cadeia.</p>
<div class="cartao"><table>
<thead><tr><th>Fundo</th><th>Nível</th><th>Recebe (R$)</th>
<th>Investe (R$)</th></tr></thead>
<tbody>{linhas_inter}</tbody></table></div>

<h2>Os maiores investidores de fora</h2>
<p class="sub">Cotistas que não são fundo do cadastro. Alguns são fundos de
   terceiro — a relação é a mesma, só não dá para seguir a cadeia para trás.</p>
<div class="cartao"><table>
<thead><tr><th>Investidor</th><th>Tipo</th><th>Fundos</th><th>Valor (R$)</th>
<th>% do que vem de fora</th></tr></thead>
<tbody>{linhas_ext}</tbody></table></div>

{bloco_sem}

<h2>Quem compra subordinada de quem</h2>
<p class="sub">As doze maiores posições de primeira perda em carteira de
   terceiro.</p>
<div class="cartao"><table>
<thead><tr><th>Investidor</th><th>Fundo investido</th><th>Classe</th>
<th>Valor (R$)</th></tr></thead>
<tbody>{linhas_sub}</tbody></table></div>

<div class="rodape">
  Gerado em {hoje:%d/%m/%Y} a partir de <i>MUL_GLD_PASSIVO_CONSOLIDADO</i>
  e <i>HOD_TB_CADASTRO_FUNDOS</i>, posição de
  {dt.date.fromisoformat(data):%d/%m/%Y}. Uma aresta existe quando o documento
  do cotista também é CNPJ de fundo do cadastro. Exposição indireta (fundo que
  investe em fundo fora do cadastro) não aparece.
</div>
</main>

<script id="dados" type="application/json">{dados_js}</script>
<script>
(function () {{
  var D = JSON.parse(document.getElementById('dados').textContent);
  function moeda(v) {{ return v.toLocaleString('pt-BR',
    {{minimumFractionDigits: 2, maximumFractionDigits: 2}}); }}
  function esc(t) {{ return String(t).replace(/[&<>"]/g, function (c) {{
    return {{'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}}[c]; }}); }}

  var b = document.getElementById('tema'), raiz = document.documentElement;
  function escuro() {{ var m = raiz.getAttribute('data-theme');
    return m ? m === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches; }}
  function pintar() {{ var e = escuro();
    b.querySelector('.simbolo').textContent = e ? '\\u2600' : '\\u263D';
    b.querySelector('.rotulo').textContent = e ? 'Tema claro' : 'Tema escuro'; }}
  b.addEventListener('click', function () {{
    raiz.setAttribute('data-theme', escuro() ? 'light' : 'dark'); pintar(); }});
  pintar();

  var arestas = Array.prototype.slice.call(document.querySelectorAll('.ar'));
  var nos = Array.prototype.slice.call(document.querySelectorAll('.no'));
  var chaves = Array.prototype.slice.call(document.querySelectorAll('.chave'));
  var sel = null;

  function pintarGrafo() {{
    arestas.forEach(function (p) {{
      if (!sel) {{ p.classList.remove('apaga', 'acesa'); return; }}
      var liga = p.dataset.o === sel || p.dataset.d === sel;
      p.classList.toggle('acesa', liga);
      p.classList.toggle('apaga', !liga);
    }});
    var vivos = {{}};
    arestas.forEach(function (p) {{
      if (p.classList.contains('apaga')) return;
      vivos[p.dataset.o] = true; vivos[p.dataset.d] = true;
    }});
    nos.forEach(function (n) {{
      n.classList.toggle('apaga', !!sel && !vivos[n.dataset.n]);
    }});
    chaves.forEach(function (c) {{
      c.classList.toggle('sel', c.dataset.n === sel);
    }});
    pintarDetalhe();
  }}

  function escolher(n) {{ sel = (sel === n) ? null : n; pintarGrafo(); }}

  // --- tabela do fundo selecionado -----------------------------------------
  function pct(v, base) {{
    return (base ? (v / base * 100).toFixed(2) : '0.00') + '%';
  }}
  function n0(v) {{ return Math.round(v).toLocaleString('pt-BR'); }}

  // PL de cada fundo e de cada classe, pelo passivo -- serve para medir o
  // peso de uma posicao DENTRO do fundo investido, na ponta direita.
  var PL = {{}}, PLC = {{}};
  D.arestas.forEach(function (e) {{
    PL[e.d] = (PL[e.d] || 0) + e.v;
    var k = e.d + '\\u0001' + e.c;
    PLC[k] = (PLC[k] || 0) + e.v;
  }});

  function pintarDetalhe() {{
    var caixa = document.getElementById('detalhe');
    var vazio = caixa.querySelector('.vazio');
    var corpo = caixa.querySelector('.corpo');
    if (!sel || !D.nos[sel]) {{
      vazio.hidden = false; corpo.hidden = true; return; }}
    vazio.hidden = true; corpo.hidden = false;
    var v = D.nos[sel];
    document.getElementById('det-nome').textContent = v.n + ' · ' + v.nome;
    document.getElementById('det-sub').textContent =
      v.tipo + ' · ' + v.papel + ' · nível ' + v.nivel;
    pintarConc(sel);
  }}

  // --- o grafo do fundo -----------------------------------------------------
  //
  // Tres colunas: quem investe (por classe), o fundo aberto em classes, e os
  // fundos em que ele aplica. E aqui que a senioridade e lida -- o mapa de
  // bolhas la em cima nao separa classe, so mostra a ligacao. As abas por
  // senioridade repetem o que o relatorio de liquidez por fundo ja faz.

  // Em 30/09/2026 o desenho de tres colunas foi quebrado em DOIS, a pedido do
  // usuario: um grafico para quem investe no fundo, outro para onde ele e
  // investidor. O bloco de classes aparece nos dois -- e o mesmo fundo visto de
  // cada ponta -- e troca de lado para a seta sempre ler da origem para o
  // destino:
  //
  //   quem investe        cotistas ----> [o fundo]
  //   onde e investidor   [o fundo] ----> fundos investidos
  //
  // A coluna de nomes ganhou 620px (eram 360 e 410): cabendo os dois graficos na
  // largura inteira em vez de dividi-la em tres, o nome do cotista deixa de ser
  // truncado em 30 caracteres.
  var GF = {{L: 1100, H: 19, P: 26, WB: 240, WN: 620, NOME: 48}};
  var LADO = {{entra: {{xn: 0, xb: 790, blocoDir: true}},
               sai: {{xb: 0, xn: 410, blocoDir: false}}}};
  var TOPO_GERAL = 7;     // visao geral, com todas as classes juntas
  var TOPO_CLASSE = 20;   // painel de uma senioridade
  var GRUPOS = {grupos_js};
  var ORDEM_G = {ordem_js};

  function soma(l) {{ return l.reduce(function (s, e) {{ return s + e.v; }}, 0); }}

  // As classes do passivo do fundo, da maior para a menor. Serve aos dois
  // graficos: o bloco central e o mesmo.
  function classesDe(entra) {{
    var cls = {{}}, ordem = [];
    entra.forEach(function (e) {{
      if (!cls[e.c]) {{ cls[e.c] = {{g: e.g, soma: 0, itens: []}}; ordem.push(e.c); }}
      cls[e.c].soma += e.v; cls[e.c].itens.push(e);
    }});
    ordem.sort(function (x, y) {{ return cls[y].soma - cls[x].soma; }});
    return {{cls: cls, ordem: ordem}};
  }}

  // O bloco de classes. Mostra SEMPRE todas, mesmo filtrado: tirar as outras
  // faria a faixa escolhida ocupar a altura inteira e perder a proporcao, que e
  // metade da informacao. `ancora` marca a faixa de onde as linhas do ativo
  // nascem -- so o grafico do ativo passa esse argumento.
  function blocoClasses(p, cls, ordem, total, xb, y0, meio, filtro, ancora) {{
    var ys = y0, faixa = {{}};
    ordem.forEach(function (c) {{
      var h = Math.max(cls[c].soma / total * meio, 26);
      faixa[c] = [ys, h];
      var ap = (filtro && cls[c].g !== filtro) ? ' gf-apagada' : '';
      if (ancora && cls[c].g === ancora) ap += ' gf-ancora';
      p.push('<rect x="' + xb + '" y="' + ys.toFixed(1) + '" width="' + GF.WB
        + '" height="' + h.toFixed(1) + '" rx="5" class="gf-faixa gf-f-'
        + cls[c].g + ap + '" data-abre="' + cls[c].g + '" role="button" '
        + 'tabindex="0"><title>' + esc(c)
        + ' — clique para ver só esta senioridade</title></rect>');
      p.push('<text x="' + (xb + 12) + '" y="' + (ys + 17).toFixed(1)
        + '" class="gf-cl">' + esc(c) + '</text>');
      p.push('<text x="' + (xb + GF.WB - 12) + '" y="' + (ys + 17).toFixed(1)
        + '" text-anchor="end" class="gf-clv">' + n0(cls[c].soma) + '</text>');
      if (h > 40) {{
        p.push('<text x="' + (xb + 12) + '" y="' + (ys + 33).toFixed(1)
          + '" class="gf-clp">' + pct(cls[c].soma, total) + ' do PL</text>');
      }}
      ys += h + 6;
    }});
    return faixa;
  }}

  // Titulo das duas colunas e rotulo das tres colunas numericas do no.
  function cabGF(p, lado, cabN, subN, nome, y0, rots) {{
    var bloco = [lado.xb, esc(nome).slice(0, 26), 'classes, pelo PL'];
    var no = [lado.xn, cabN, subN];
    (lado.blocoDir ? [no, bloco] : [bloco, no]).forEach(function (t) {{
      p.push('<text x="' + t[0] + '" y="52" class="gf-cab">' + t[1] + '</text>');
      p.push('<text x="' + t[0] + '" y="70" class="gf-cabp">' + t[2] + '</text>');
    }});
    [116, 62, 8].forEach(function (dx, i) {{
      p.push('<text x="' + (lado.xn + GF.WN - dx) + '" y="' + (y0 - 12) + '" '
        + 'text-anchor="end" class="gf-ch">' + rots[i] + '</text>');
    }});
  }}

  // Uma linha da coluna de nos: nome a esquerda, tres numeros a direita.
  function noGF(xn, y, nome, v, p1, p2, cls2, id, dica) {{
    return '<g class="gf-no"' + (id ? ' data-n="' + esc(id) + '"' : '')
      + '><rect x="' + xn + '" y="' + y + '" width="' + GF.WN
      + '" height="' + GF.H + '" rx="4"/>'
      + '<text x="' + (xn + 8) + '" y="' + (y + 13.5) + '">'
      + esc(String(nome).slice(0, GF.NOME)) + '</text>'
      + '<text x="' + (xn + GF.WN - 116) + '" y="' + (y + 13.5)
      + '" text-anchor="end" class="gf-v">' + n0(v) + '</text>'
      + '<text x="' + (xn + GF.WN - 62) + '" y="' + (y + 13.5)
      + '" text-anchor="end" class="gf-pc">' + p1 + '</text>'
      + '<text x="' + (xn + GF.WN - 8) + '" y="' + (y + 13.5)
      + '" text-anchor="end" class="' + cls2 + '">' + p2 + '</text>'
      + '<title>' + dica + '</title></g>';
  }}

  // QUEM INVESTE: cada cotista, ligado a faixa da classe que ele comprou.
  function grafoEntra(n, filtro) {{
    var entra = D.arestas.filter(function (e) {{ return e.d === n; }});
    if (!entra.length) return '';
    var total = soma(entra);
    var topo = filtro ? TOPO_CLASSE : TOPO_GERAL;
    var k = classesDe(entra), cls = k.cls, ordem = k.ordem;

    var itens = [];
    ordem.forEach(function (c) {{
      if (filtro && cls[c].g !== filtro) return;
      var it = cls[c].itens.slice().sort(function (x, y) {{ return y.v - x.v; }});
      it.slice(0, topo).forEach(function (e) {{
        itens.push({{c: c, g: cls[c].g, id: e.o,
                    nome: (D.nos[e.o] || {{}}).nome || e.o, v: e.v}});
      }});
      if (it.length > topo) {{
        var r = it.slice(topo);
        itens.push({{c: c, g: cls[c].g, id: null,
                    nome: 'outros ' + r.length + ' cotistas', v: soma(r)}});
      }}
    }});

    var lado = LADO.entra;
    var alt = Math.max(itens.length, 6) * GF.P + 150;
    var meio = Math.min(alt - 150, 420), y0 = 100;
    var p = ['<svg viewBox="0 0 ' + GF.L + ' ' + alt + '" class="gf-gr" '
             + 'role="img" aria-label="Quem investe no fundo">'];
    cabGF(p, lado, 'QUEM INVESTE', 'cotistas, por classe', D.nos[n].nome, y0,
          ['valor', '% do PL', '% da classe']);
    var faixa = blocoClasses(p, cls, ordem, total, lado.xb, y0, meio, filtro,
                             null);

    var vmax = itens.reduce(function (m, e) {{ return Math.max(m, e.v); }}, 1);
    var fim = lado.xn + GF.WN, cx = (lado.xb - fim) * 0.4, y = y0;
    itens.forEach(function (e) {{
      var f = faixa[e.c], y2 = f[0] + f[1] / 2;
      var w = 0.7 + Math.sqrt(e.v / vmax) * 4.6;
      p.push('<path class="gf-ar gf-a-' + e.g + '" stroke-width="' + w.toFixed(2)
        + '" d="M' + fim + ' ' + (y + GF.H / 2).toFixed(1)
        + ' C' + (fim + cx).toFixed(0) + ' ' + (y + GF.H / 2).toFixed(1)
        + ' ' + (lado.xb - cx).toFixed(0) + ' ' + y2.toFixed(1)
        + ' ' + lado.xb + ' ' + y2.toFixed(1) + '"/>');
      p.push(noGF(lado.xn, y, e.nome, e.v, pct(e.v, total),
        pct(e.v, cls[e.c].soma), 'gf-pa', e.id,
        esc(e.nome) + '\\n' + esc(e.c) + ' · R$ ' + moeda(e.v) + '\\n'
        + pct(e.v, total) + ' do PL do fundo\\n'
        + pct(e.v, cls[e.c].soma) + ' da classe ' + esc(e.c)));
      y += GF.P;
    }});

    p.push('</svg>');
    return p.join('');
  }}

  // ONDE O FUNDO É INVESTIDOR: cada cota de fundo da casa que ele detem.
  //
  // O segundo percentual e o peso da posicao DENTRO do fundo investido, que e
  // onde a concentracao aparece -- 0,81% do nosso PL pode ser 100% do PL dele.
  //
  // De ONDE a linha nasce tambem e informacao: comprar cota de outro fundo e
  // decisao de ativo, e quem absorve a primeira perda dessa decisao e a
  // SUBORDINADA, nao o fundo em media. Na visao geral a linha nasce na faixa da
  // subordinada (ou no degrau mais baixo que existir); filtrado, nasce no degrau
  // escolhido, que e o que esta sendo comparado dos dois lados.
  function grafoSai(n, filtro) {{
    var entra = D.arestas.filter(function (e) {{ return e.d === n; }});
    var saem = D.saidas.filter(function (e) {{ return e.o === n; }});
    if (filtro) {{ saem = saem.filter(function (e) {{ return e.g === filtro; }}); }}
    if (!saem.length) return '';
    var total = soma(entra);
    var k = classesDe(entra), cls = k.cls, ordem = k.ordem;
    saem = saem.slice().sort(function (x, y) {{ return y.v - x.v; }});

    var ordemRisco = filtro ? [filtro] : ['sub', 'mez', 'nd', 'uni', 'sen'];
    var gAncora = null;
    for (var iA = 0; iA < ordemRisco.length && gAncora === null; iA++) {{
      var achou = false;
      ordem.forEach(function (c) {{
        if (cls[c].g === ordemRisco[iA]) achou = true; }});
      if (achou) gAncora = ordemRisco[iA];
    }}

    var lado = LADO.sai;
    var alt = Math.max(saem.length, 6) * GF.P + 150;
    var meio = Math.min(alt - 150, 420), y0 = 100;
    var p = ['<svg viewBox="0 0 ' + GF.L + ' ' + alt + '" class="gf-gr" '
             + 'role="img" aria-label="Onde o fundo é investidor">'];
    cabGF(p, lado, 'ONDE É INVESTIDOR', 'cota de fundo da casa',
          D.nos[n].nome, y0, ['valor', '% nosso PL', '% do alvo']);
    var faixa = blocoClasses(p, cls, ordem, total, lado.xb, y0, meio, filtro,
                             gAncora);

    var ini = null, fimA = null;
    ordem.forEach(function (c) {{
      if (cls[c].g !== gAncora) return;
      var f = faixa[c];
      if (ini === null || f[0] < ini) ini = f[0];
      if (fimA === null || f[0] + f[1] > fimA) fimA = f[0] + f[1];
    }});
    var ym = (ini === null) ? y0 + meio / 2 : (ini + fimA) / 2;

    var vmax = saem.reduce(function (m, e) {{ return Math.max(m, e.v); }}, 1);
    var borda = lado.xb + GF.WB, cx = (lado.xn - borda) * 0.4, y = y0;
    saem.forEach(function (e) {{
      var y2 = y + GF.H / 2;
      var w = 0.7 + Math.sqrt(e.v / vmax) * 4.6;
      p.push('<path class="gf-ar gf-a-' + e.g + '" stroke-width="' + w.toFixed(2)
        + '" d="M' + borda + ' ' + ym.toFixed(1)
        + ' C' + (borda + cx).toFixed(0) + ' ' + ym.toFixed(1)
        + ' ' + (lado.xn - cx).toFixed(0) + ' ' + y2.toFixed(1)
        + ' ' + lado.xn + ' ' + y2.toFixed(1) + '"/>');
      var nm = (D.nos[e.d] || {{}}).nome || e.d;
      // Fundo que a casa nao administra nao tem passivo na base: o peso dentro
      // do alvo fica em branco, de proposito, em vez de sair 0,00%.
      var pa = PL[e.d] || 0, pc = PLC[e.d + '\\u0001' + e.c] || 0;
      p.push(noGF(lado.xn, y, nm + ' · ' + e.c, e.v, pct(e.v, total),
        (pa ? pct(e.v, pa) : '—'), 'gf-pa' + (pa ? '' : ' gf-vazio'), e.d,
        esc(nm) + '\\n' + esc(e.c) + ' · R$ ' + moeda(e.v)
        + (e.f === 'passivo'
           ? '\\nvalor pelo passivo do fundo investido — a carteira do '
             + 'comprador não identifica o alvo'
           : '\\nna carteira: ' + esc(e.a))
        + '\\n' + pct(e.v, total) + ' do nosso PL\\n'
        + (pa ? pct(e.v, pa) + ' do PL de ' + esc(nm) + '\\n'
                + pct(e.v, pc) + ' da classe ' + esc(e.c)
              : 'fundo que a casa não administra: sem PL na base')));
      y += GF.P;
    }});

    p.push('</svg>');
    return p.join('');
  }}

  // Barras horizontais de um recorte. itens = [[nome, valor], ...]
  //
  // A grade precisa de folga entre o fim da barra e a coluna de valor: com a
  // barra indo ate 790 e o numero terminando em 804, "263.619.593" (~78px a
  // 11,5px) comecava DENTRO da barra. A barra agora para em 670.
  var BAR = {{L: 1100, H: 20, GAP: 6, XB: 320, WB: 350,
              CV: 800, CB: 905, CP: 1010, TOPO: 30}};

  // `acum`: quando o bloco E o proprio PL, "% do bloco" sairia igual a
  // "% do PL" -- coluna repetida. Nesse caso a do meio vira o acumulado,
  // que e o que responde "os N maiores somam quanto".
  function barras(itens, bloco, pl, cor, rotNome, acum) {{
    if (!itens.length) return '<p class="nota">Sem posições.</p>';
    var alt = BAR.TOPO + itens.length * (BAR.H + BAR.GAP) + 6;
    var vmax = itens.reduce(function (m, i) {{ return Math.max(m, i[1]); }}, 1);
    var p = ['<svg viewBox="0 0 ' + BAR.L + ' ' + alt + '" '
             + 'class="gf-gr gf-gb" role="img">'];

    p.push('<text x="10" y="14" class="gf-bh">' + rotNome + '</text>');
    p.push('<text x="' + BAR.XB + '" y="14" class="gf-bh">participação</text>');
    [[BAR.CV, 'valor (R$)'], [BAR.CB, acum ? 'acumulado' : '% do bloco'],
     [BAR.CP, '% do PL']].forEach(function (t) {{
      p.push('<text x="' + t[0] + '" y="14" text-anchor="end" class="gf-bh">'
        + t[1] + '</text>');
    }});
    p.push('<line x1="10" y1="21" x2="' + BAR.CP + '" y2="21" class="gf-bl"/>');

    var y = BAR.TOPO, corrido = 0;
    itens.forEach(function (i) {{
      var w = Math.max(i[1] / vmax * BAR.WB, 2);
      corrido += i[1];
      p.push('<text x="10" y="' + (y + 14) + '" class="gf-bn">'
        + esc(String(i[0]).slice(0, 42)) + '</text>');
      p.push('<rect x="' + BAR.XB + '" y="' + (y + 3) + '" width="'
        + w.toFixed(1) + '" height="' + (BAR.H - 6) + '" rx="3" '
        + 'class="gf-bb gf-b-' + cor + '"/>');
      [[BAR.CV, 'gf-bv', n0(i[1])],
       [BAR.CB, 'gf-bp', acum ? pct(corrido, pl) : pct(i[1], bloco)],
       [BAR.CP, 'gf-bq', pct(i[1], pl)]].forEach(function (t) {{
        p.push('<text x="' + t[0] + '" y="' + (y + 14) + '" '
          + 'text-anchor="end" class="' + t[1] + '">' + t[2] + '</text>');
      }});
      p.push('<title>' + esc(String(i[0])) + '\\nR$ ' + moeda(i[1]) + '\\n'
        + (acum ? pct(corrido, pl) + ' acumulado até aqui'
                : pct(i[1], bloco) + ' do bloco')
        + '\\n' + pct(i[1], pl) + ' do PL</title>');
      y += BAR.H + BAR.GAP;
    }});
    p.push('</svg>');
    return p.join('');
  }}

  // Um painel por senioridade: de um lado o risco que o fundo VENDE nesse
  // degrau, do outro o que ele COMPRA no mesmo degrau.
  // Um painel de senioridade POR LADO. Cada parte tem a sua barra de botoes, e o
  // id do painel leva o prefixo do lado ("e-" quem investe, "s-" onde e
  // investidor): as duas barras convivem na mesma pagina, e sem prefixo clicar
  // em "Subordinada" numa parte trocaria a aba da outra.
  function painelEntra(n, g) {{
    var rot = GRUPOS[g][0], cor = GRUPOS[g][1];
    var pl = soma(D.arestas.filter(function (e) {{ return e.d === n; }}));
    var ent = D.arestas.filter(function (e) {{ return e.d === n && e.g === g; }});
    var ve = soma(ent);

    var porCot = {{}}, cotas = [];
    ent.forEach(function (e) {{
      if (porCot[e.o] === undefined) {{ porCot[e.o] = 0; cotas.push(e.o); }}
      porCot[e.o] += e.v;
    }});
    cotas.sort(function (x, y) {{ return porCot[y] - porCot[x]; }});
    var itens = cotas.slice(0, TOPO_CLASSE).map(function (o) {{
      return [(D.nos[o] || {{}}).nome || o, porCot[o]]; }});
    if (cotas.length > TOPO_CLASSE) {{
      var r = cotas.slice(TOPO_CLASSE), sr = 0;
      r.forEach(function (o) {{ sr += porCot[o]; }});
      itens.push(['outros ' + r.length + ' cotistas', sr]);
    }}
    var classes = {{}};
    ent.forEach(function (e) {{ classes[e.c] = true; }});

    return '<section class="gf-painel" data-painel="e-' + g + '" hidden>'
      + '<div class="kpis" style="margin-top:14px">'
      + '<div class="kpi"><div class="r">Vende (' + rot.toLowerCase()
      + ' do fundo)</div><div class="v">' + pct(ve, pl) + '</div>'
      + '<div class="n">R$ ' + n0(ve) + ' · ' + cotas.length
      + ' cotista(s)</div></div></div>'
      + (ent.length
         ? '<p class="sub">' + cotas.length + ' cotista(s) em '
           + esc(Object.keys(classes).join(', '))
           + ', R$ ' + moeda(ve) + ' — <b>' + pct(ve, pl) + ' do PL</b>. '
           + 'Os ' + Math.min(cotas.length, TOPO_CLASSE) + ' maiores.</p>'
           + '<div class="cartao" style="margin-top:8px">'
           + grafoEntra(n, g) + '</div>'
           + '<div class="cartao" style="margin-top:8px">'
           + barras(itens, ve, pl, cor, 'cotista') + '</div>'
         : '<p class="nota">O fundo não tem classe ' + rot.toLowerCase()
           + ' no passivo.</p>')
      + '</section>';
  }}

  function painelSai(n, g) {{
    var rot = GRUPOS[g][0], cor = GRUPOS[g][1];
    var pl = soma(D.arestas.filter(function (e) {{ return e.d === n; }}));
    var sai = D.saidas.filter(function (e) {{ return e.o === n && e.g === g; }});
    var vs = soma(sai);
    var itSai = sai.slice().sort(function (x, y) {{ return y.v - x.v; }})
      .map(function (e) {{
        return [((D.nos[e.d] || {{}}).nome || e.d) + ' · ' + e.c, e.v]; }});

    return '<section class="gf-painel" data-painel="s-' + g + '" hidden>'
      + '<div class="kpis" style="margin-top:14px">'
      + '<div class="kpi"><div class="r">Compra (' + rot.toLowerCase()
      + ' de terceiros)</div><div class="v">' + pct(vs, pl) + '</div>'
      + '<div class="n">R$ ' + n0(vs) + ' · ' + sai.length
      + ' posição(ões)</div></div></div>'
      + (sai.length
         ? '<p class="sub">' + sai.length + ' posição(ões), R$ ' + moeda(vs)
           + ' — <b>' + pct(vs, pl) + ' do PL</b>.</p>'
           + '<div class="cartao" style="margin-top:8px">'
           + grafoSai(n, g) + '</div>'
           + '<div class="cartao" style="margin-top:8px">'
           + barras(itSai, vs, pl, cor, 'fundo investido · classe') + '</div>'
         : '<p class="nota">Nenhuma posição nesta senioridade.</p>')
      + '</section>';
  }}

  // As mesmas barras dos paineis de senioridade, mas somando as classes: na
  // visao geral a pergunta e quanto cada contraparte pesa no fundo inteiro.
  //
  // Uma funcao por lado desde 30/09/2026: as duas partes da secao precisam
  // intercalar grafico e barras do MESMO lado, e a versao unica devolvia os dois
  // blocos colados.
  function barrasEntra(n) {{
    var entra = D.arestas.filter(function (e) {{ return e.d === n; }});
    if (!entra.length) return '';
    var pl = soma(entra);
    var porCot = {{}}, cotas = [];
    entra.forEach(function (e) {{
      if (porCot[e.o] === undefined) {{ porCot[e.o] = 0; cotas.push(e.o); }}
      porCot[e.o] += e.v;
    }});
    cotas.sort(function (x, y) {{ return porCot[y] - porCot[x]; }});
    var itens = cotas.slice(0, TOPO_CLASSE).map(function (o) {{
      return [(D.nos[o] || {{}}).nome || o, porCot[o]]; }});
    if (cotas.length > TOPO_CLASSE) {{
      var r = cotas.slice(TOPO_CLASSE), sr = 0;
      r.forEach(function (o) {{ sr += porCot[o]; }});
      itens.push(['outros ' + r.length + ' cotistas', sr]);
    }}
    return '<p class="sub">' + cotas.length + ' cotista(s) somando todas as '
      + 'classes, R$ ' + moeda(pl) + '. Os '
      + Math.min(cotas.length, TOPO_CLASSE) + ' maiores.</p>'
      + '<div class="cartao" style="margin-top:8px">'
      + barras(itens, pl, pl, 'serie', 'cotista', true) + '</div>';
  }}

  function barrasSai(n) {{
    var entra = D.arestas.filter(function (e) {{ return e.d === n; }});
    var saem = D.saidas.filter(function (e) {{ return e.o === n; }});
    if (!saem.length) return '';
    var pl = soma(entra), vs = soma(saem);
    var it = saem.slice().sort(function (x, y) {{ return y.v - x.v; }})
      .map(function (e) {{
        return [((D.nos[e.d] || {{}}).nome || e.d) + ' · ' + e.c
                + (e.x ? '  [terceiro]' : '')
                + (e.f === 'passivo' ? '  [pelo passivo]' : ''), e.v]; }});
    var nx = saem.filter(function (e) {{ return e.x; }}).length;
    var np = saem.filter(function (e) {{ return e.f === 'passivo'; }}).length;
    return '<p class="sub">' + saem.length + ' posição(ões) em cota de fundo, '
      + 'R$ ' + moeda(vs) + ' — <b>' + pct(vs, pl) + ' do PL</b>'
      + (nx ? '. ' + nx + ' em fundo que a casa não administra' : '')
      + (np ? '. ' + np + ' vêm do passivo do fundo investido, porque a '
              + 'carteira do comprador não identifica o alvo' : '') + '.</p>'
      + '<div class="cartao" style="margin-top:8px">'
      + barras(it, vs, pl, 'serie', 'fundo investido · classe') + '</div>';
  }}

  // --- concentracao do passivo ---------------------------------------------
  var CHAVES = [['nome', 'Por nome de cotista'],
                ['doc', 'Por documento (CPF/CNPJ)'],
                ['dist', 'Por distribuidor']];

  function blocoConc(n) {{
    var c = D.conc[n];
    if (!c) {{
      return '<p class="sem-conc"><b>Sem concentração para este fundo.</b> '
        + esc((D.semconc && D.semconc[n])
              || 'não há passivo com cadastro suficiente na posição.')
        + '</p>';
    }}
    var eqd = c.doc.hhi ? 10000 / c.doc.hhi : 0;
    var colapsam = c.nome.n - c.doc.n;
    var dv = c.nome.hhi ? (c.doc.hhi - c.nome.hhi) / c.nome.hhi * 100 : 0;

    var kpis = [
      ['Passivo (PL)', 'R$ ' + n0(c.pl / 1e6) + ' mi', 'posição do fundo'],
      ['Cotistas por nome', n0(c.nome.n), 'como o relatório conta hoje'],
      ['Cotistas por documento', n0(c.doc.n),
       colapsam > 0 ? n0(colapsam) + ' a menos — contas do mesmo CPF/CNPJ'
                    : 'nenhuma conta omnibus neste fundo'],
      ['Maior contraparte', pct(c.doc.maior, c.pl),
       'contra ' + pct(c.nome.maior, c.pl) + ' pela contagem por nome']];

    var tab = '';
    CHAVES.forEach(function (par) {{
      var m = c[par[0]];
      var eq = m.hhi ? (10000 / m.hhi) : 0;
      var sem = par[0] === 'dist' && c.cob_dist === 0;
      tab += '<tr' + (par[0] === 'doc' ? ' class="forte"' : '') + '><td>'
        + par[1] + '</td>'
        + (sem ? '<td colspan="6" class="semdado">sem cadastro de '
                 + 'distribuidor neste fundo</td>'
               : '<td>' + m.n + '</td><td>' + pct(m.maior, c.pl) + '</td><td>'
                 + pct(m.top3, c.pl) + '</td><td>' + pct(m.top5, c.pl)
                 + '</td><td class="hhi">' + n0(m.hhi) + '</td><td>'
                 + eq.toFixed(1) + '</td>')
        + '</tr>';
    }});

    var col = c.colapso.length
      ? c.colapso.map(function (r) {{
          return '<tr><td><code>' + esc(r.doc) + '</code></td><td>'
            + esc(r.nome) + '</td><td>' + r.nomes + '</td><td>' + moeda(r.v)
            + '</td><td>' + pct(r.v, c.pl) + '</td></tr>'; }}).join('')
        + (c.n_colapso > c.colapso.length
           ? '<tr><td colspan="5" class="nada">+ '
             + (c.n_colapso - c.colapso.length) + ' documento(s)</td></tr>' : '')
      : '<tr><td colspan="5" class="nada">nenhum documento com mais de um '
        + 'nome</td></tr>';

    var hhi = '<b>HHI</b> = Σ (participação de cada cotista)² × 10.000, de ~0 '
      + '(pulverizado) a 10.000 (um cotista com 100%). <b>Equivale a</b> é '
      + '10.000 ÷ HHI: quantos cotistas <i>de tamanho igual</i> dariam a mesma '
      + 'concentração. Aqui, por documento, o passivo concentra como se tivesse '
      + '<b>' + eqd.toFixed(1) + ' cotistas iguais</b>, mesmo tendo <b>'
      + c.doc.n + '</b>; o maior sozinho tem ' + pct(c.doc.maior, c.pl) + '. '
      + (colapsam > 0
         ? 'A consolidação por documento junta <b>' + colapsam + ' nome(s)</b> '
           + 'e move o índice de ' + n0(c.nome.hhi) + ' para ' + n0(c.doc.hhi)
           + ' (' + (dv >= 0 ? '+' : '') + dv.toFixed(0) + '%).'
         : 'Neste fundo a chave não muda nada: são ' + c.doc.n + ' nomes para '
           + c.doc.n + ' documentos, sem conta omnibus nem duplicidade.');

    var ress = '<b>Documento não é grupo econômico</b> — duas empresas do mesmo '
      + 'controlador têm CNPJ diferente e continuam separadas. E os cortes '
      + '"1.500 / 2.500" do HHI vêm do antitruste: não há régua regulatória '
      + 'para passivo de fundo. Use o índice comparativamente.';
    if (c.sem_doc > {ALERTA_SEM_DOC}) {{
      ress += ' <b>Neste fundo, ' + c.sem_doc.toFixed(1) + '% do passivo não '
        + 'tem CPF/CNPJ válido</b> e não colapsa com ninguém.';
    }}
    if (c.cob_dist > 0 && c.cob_dist < 100) {{
      ress += ' Distribuidor identificado em ' + c.cob_dist.toFixed(0)
        + '% do PL; o resto aparece como "(sem distribuidor)".';
    }}

    return '<h4>Concentração do passivo — a mesma posição, três contagens</h4>'
      + '<p class="nota" style="margin:0 0 8px">Quem é a contraparte depende da '
      + 'chave usada para agregar. <b>Nome</b> é como o relatório de liquidez '
      + 'conta hoje; <b>documento</b> é a contraparte de fato — conta omnibus '
      + 'colapsa numa linha; <b>distribuidor</b> é o canal de entrada.</p>'
      + '<div class="kpis">' + kpis.map(function (k) {{
          return '<div class="kpi"><div class="r">' + k[0] + '</div>'
            + '<div class="v">' + k[1] + '</div><div class="n">' + k[2]
            + '</div></div>'; }}).join('') + '</div>'
      + '<div class="duas" style="margin-top:12px"><div>'
      + '<table><thead><tr><th>Chave</th><th>Cotistas</th><th>Maior</th>'
      + '<th>3 maiores</th><th>5 maiores</th><th>HHI</th><th>Equivale a</th>'
      + '</tr></thead><tbody>' + tab + '</tbody></table>'
      + '<p class="nota" style="margin:10px 0 0">' + hhi + '</p></div><div>'
      + '<h4 style="margin-top:0">De onde vem a diferença'
      + (c.n_colapso ? ' <span>— ' + c.n_colapso + ' documento(s) sob mais de '
                       + 'um nome</span>' : '') + '</h4>'
      + '<table><thead><tr><th>Documento</th><th>Nome consolidado</th>'
      + '<th>Nomes</th><th>Saldo (R$)</th><th>% do PL</th></tr></thead>'
      + '<tbody>' + col + '</tbody></table></div></div>'
      + '<p class="nota ress" style="margin:12px 0 0">' + ress + '</p>';
  }}

  function pintarConc(n) {{
    var cx = document.getElementById('conc');
    var entra = D.arestas.filter(function (e) {{ return e.d === n; }});
    var saem = D.saidas.filter(function (e) {{ return e.o === n; }});

    // Duas PARTES independentes, cada uma com a SUA barra de botoes, o SEU
    // grafico e as SUAS barras. O desenho de tres colunas foi quebrado em
    // 30/09/2026 e as abas em 30/09/2026 tambem: com uma barra so, escolher
    // "Subordinada" para olhar o ativo trocava tambem o lado do passivo, e as
    // duas perguntas nao se fazem no mesmo passo.
    //
    // As abas de cada lado contam o que AQUELE lado tem -- cotistas de um, posicoes
    // do outro. Antes o contador era "cotistas / posicoes" nos dois, metade dele
    // inutil em cada parte.
    var abasE = ['<button type="button" class="gf-aba gf-ativa" '
                 + 'data-aba="e-geral">Visão geral</button>'];
    var abasS = ['<button type="button" class="gf-aba gf-ativa" '
                 + 'data-aba="s-geral">Visão geral</button>'];
    var painE = [], painS = [];
    ORDEM_G.forEach(function (g) {{
      var ent = entra.filter(function (e) {{ return e.g === g; }});
      var sai = saem.filter(function (e) {{ return e.g === g; }});
      if (!ent.length && !sai.length) return;
      var bolinha = '<i class="gf-ch-cor" style="background:var(--'
        + GRUPOS[g][1] + ')"></i>' + GRUPOS[g][0];
      if (ent.length) {{
        var cot = {{}};
        ent.forEach(function (e) {{ cot[e.o] = true; }});
        abasE.push('<button type="button" class="gf-aba" data-aba="e-' + g
          + '">' + bolinha + '<span class="gf-qt">'
          + Object.keys(cot).length + '</span></button>');
        painE.push(painelEntra(n, g));
      }}
      if (sai.length) {{
        abasS.push('<button type="button" class="gf-aba" data-aba="s-' + g
          + '">' + bolinha + '<span class="gf-qt">' + sai.length
          + '</span></button>');
        painS.push(painelSai(n, g));
      }}
    }});

    var gEntra = grafoEntra(n, null), gSai = grafoSai(n, null);
    var comum = ' Clique numa faixa de classe (ou num botão acima) para abrir só '
      + 'aquela senioridade com os ' + TOPO_CLASSE + ' maiores; clique numa '
      + 'linha para ir àquele fundo.';

    cx.innerHTML =
      // --- parte 1: quem investe no fundo ---
      '<div class="gf-lado" data-lado="e">'
      + '<h3 class="gf-tit" style="margin-top:12px">Quem investe no fundo</h3>'
      + '<div class="gf-abas">' + abasE.join('') + '</div>'
      + '<section class="gf-painel" data-painel="e-geral">'
      + (gEntra
         ? '<p class="nota" style="margin:8px 0 6px">Cada cotista, ligado à '
           + 'faixa da classe que ele comprou. O segundo percentual é o peso '
           + '<b>dentro da classe</b> — um cotista com 3% do PL pode ser 100% da '
           + 'subordinada, e é isso que decide quem absorve a primeira perda.'
           + comum + '</p>'
           + '<div class="cartao" style="margin-bottom:6px">' + gEntra + '</div>'
         : '<p class="nota">Sem passivo na posição.</p>')
      + barrasEntra(n)
      + blocoConc(n) + '</section>' + painE.join('') + '</div>'
      // --- parte 2: onde o fundo e investidor ---
      + '<div class="gf-lado" data-lado="s">'
      + '<h3 class="gf-tit">Onde o fundo é investidor</h3>'
      + '<div class="gf-abas">' + abasS.join('') + '</div>'
      + '<section class="gf-painel" data-painel="s-geral">'
      + (gSai
         ? '<p class="nota" style="margin:8px 0 6px">Cada cota de fundo da casa '
           + 'que ele detém. O segundo percentual é o peso <b>dentro do fundo '
           + 'investido</b> — é aí que a concentração aparece: 0,81% do nosso PL '
           + 'pode ser 100% do PL dele. <b>As linhas nascem na faixa '
           + 'tracejada</b>: comprar cota de outro fundo é decisão de ativo, e '
           + 'quem absorve a primeira perda dessa decisão é a subordinada, não o '
           + 'fundo em média. Esta vista enxerga só cota de fundo da casa, pelo '
           + 'passivo; a carteira completa está no relatório de liquidez.'
           + comum + '</p>'
           + '<div class="cartao" style="margin-bottom:6px">' + gSai + '</div>'
         : '<p class="nota">O fundo não é investidor em cota de fundo da '
           + 'casa.</p>')
      + barrasSai(n)
      + '</section>' + painS.join('') + '</div>';
  }}

  // A troca de aba e ESCOPADA ao lado: `.gf-lado` delimita a parte. Com a busca
  // global de antes, abrir uma senioridade num lado escondia o painel do outro,
  // porque o `hidden` ia para todo painel cujo id nao fosse o clicado.
  function trocarAba(lado, id) {{
    if (!lado) return;
    lado.querySelectorAll('.gf-aba').forEach(function (b) {{
      b.classList.toggle('gf-ativa', b.getAttribute('data-aba') === id); }});
    lado.querySelectorAll('.gf-painel').forEach(function (s) {{
      s.hidden = s.getAttribute('data-painel') !== id; }});
  }}

  // No painel do fundo: o botao troca a senioridade daquele lado, e a faixa de
  // classe no grafico faz o mesmo -- ela sabe a senioridade (`data-abre`) mas nao
  // o lado, que sai do `.gf-lado` que a contem. Uma linha do desenho pula para
  // aquele fundo.
  document.getElementById('detalhe').addEventListener('click', function (ev) {{
    var alvo = ev.target;
    if (!alvo.closest) return;
    var aba = alvo.closest('.gf-aba');
    if (aba) {{
      trocarAba(aba.closest('.gf-lado'), aba.getAttribute('data-aba'));
      return;
    }}
    var faixa = alvo.closest('.gf-faixa');
    if (faixa) {{
      var lado = faixa.closest('.gf-lado');
      if (lado) {{
        trocarAba(lado, lado.getAttribute('data-lado') + '-'
                  + faixa.getAttribute('data-abre'));
      }}
      return;
    }}
    var no = alvo.closest('.gf-no[data-n], tr[data-n]');
    if (no) {{ escolher(no.getAttribute('data-n')); }}
  }});

  nos.forEach(function (n) {{
    n.addEventListener('click', function () {{ escolher(n.dataset.n); }});
  }});
  // Clicar na chave e o mesmo que clicar na bola -- e achar o nome numa lista
  // ordenada e mais rapido do que cacar a bola no meio do desenho.
  chaves.forEach(function (c) {{
    c.addEventListener('click', function () {{ escolher(c.dataset.n); }});
  }});
  document.getElementById('limpar').addEventListener('click', function () {{
    sel = null; pintarGrafo(); }});

  // As duas vistas desenham os mesmos dados; filtro e selecao valem nas duas,
  // porque o JS procura por .ar/.no no documento inteiro.
  document.querySelectorAll('.aba').forEach(function (bt) {{
    bt.addEventListener('click', function () {{
      var v = bt.dataset.v;
      document.querySelectorAll('.aba').forEach(function (o) {{
        o.classList.toggle('ativa', o === bt); }});
      document.querySelectorAll('.vista').forEach(function (o) {{
        o.classList.toggle('ativa', o.dataset.v === v); }});
    }});
  }});

  // Entrada por link: quem chega de outra pagina manda o CNPJ do fundo no
  // hash da URL (grafo_passivo.html#23216398000101) e o grafo ja abre com
  // ele selecionado. Sem hash, nada muda.
  function porHash() {{
    var alvo = '';
    try {{ alvo = decodeURIComponent((location.hash || '').slice(1)); }}
    catch (e) {{ alvo = (location.hash || '').slice(1); }}
    if (!alvo || !D.nos[alvo] || alvo === sel) return;
    sel = null;                       // escolher() alterna: zera antes
    escolher(alvo);
    var cx = document.getElementById('detalhe');
    if (cx) {{ cx.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
  }}
  porHash();
  window.addEventListener('hashchange', porHash);
}})();
</script></body></html>
"""


if __name__ == "__main__":
    data = sys.argv[1] if len(sys.argv) > 1 else None
    data, a, total, ext, todas, bruto, saidas = carregar(data)
    info = estrutura(a, todas, universo=set(todas["destino"]),
                     saidas=saidas)
    SAIDA.write_text(montar(data, a, info, total, ext, todas, bruto, saidas),
                     encoding="utf-8")
    conc, avisos = concentracao(bruto)
    entre = a[a["tipo_origem"] == "fundo"]["saldo"].sum()
    print(f"posicao ....... {data}")
    print(f"arestas ....... {len(a)}   nos: {len(info)}")
    print(f"fundo->fundo .. R$ {entre:,.2f} "
          f"({entre / total * 100:.1f}% do passivo)")
    print(f"de fora ....... R$ {ext['saldo'].sum():,.2f} "
          f"({ext['origem'].nunique()} investidores que nao sao fundo da casa)")
    ext_d = int(saidas["externo"].sum())
    print(f"carteira ...... {len(saidas)} posicao(oes) em cota de fundo, "
          f"R$ {saidas['saldo'].sum():,.2f}   "
          f"({ext_d} em fundo de terceiro, "
          f"R$ {saidas.loc[saidas['externo'], 'saldo'].sum():,.2f})")
    print(f"concentracao .. {len(conc)} fundo(s)"
          + (f"   SEM: {len(avisos)}" if avisos else ""))
    for _c, n, m in sorted(avisos, key=lambda x: x[1]):
        print(f"   ignorado: {n} -- {m}")
    print(f"gerado ........ {SAIDA}  ({SAIDA.stat().st_size / 1024:,.0f} KB)")
