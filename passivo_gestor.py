"""O passivo por QUEM DECIDE: o gestor do fundo cotista, ou o próprio titular.

    python passivo_gestor.py [AAAA-MM-DD]

Quando o cotista é um fundo, quem decide aplicar e resgatar não é o fundo: é
o gestor dele. Agrupar por gestor junta o que a lista por fundo separa — a
MONGERAL AEGON aparece como cinco nomes soltos (MCA II, MAG MCA 30, QP 11
SOMMA, MORE C 180, MCA INSTITUCIONAL) e é uma decisão só, de R$ 2,08 bi.

Quando o cotista não é fundo, o titular é ele mesmo: pessoa física, empresa.

QUEM É FUNDO vem do registro da CVM, não do nome. O robô `cvm_gestao` já
baixa `registro_fundo_classe.zip` todo dia e destila em
`indice/mercado_AAAA-MM-DD.csv.gz`. A regra por nome errava: "MORE C 180
FCFIDC" tem FIDC colado em FC, escapava do \\bFIDC\\b, e R$ 338,0 mi de fundo
entravam como investidor direto.

FUNDO DA CASA é o que está no nosso cadastro OU é gerido por uma gestora do
grupo -- o MONUMENTAL, o MHX e o RM são geridos pela Multiplica em
administrador de fora: a decisão é nossa, não é dinheiro de terceiro.
"""

from __future__ import annotations

import datetime as dt
import glob
import gzip
import json
import sys
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI))

import grafo_passivo as gp                       # noqa: E402

try:
    import grafo_fundo as gf                     # noqa: E402
except Exception:                                # noqa: BLE001
    gf = None

SAIDA = AQUI / "passivo_gestor.html"
INDICE = AQUI.parent / "cvm_gestao" / "indice"
TOPO_TABELA = 150
TOPO_DETALHE = 25

# Gestoras do grupo, por CNPJ -- nome muda, CNPJ não.
GESTORAS_CASA = {
    "07252227000173": "Multiplica Capital Asset Management",
    "32003165000103": "Multiplica Wealth Management",
    "18076466000136": "Multiplica Ribeirão Preto Capital",
}

# Passivo direto da MUL_GLD_PASSIVO_CONSOLIDADO (de onde o Datalake monta a HOD_PAS_*);
# a base de notas deduplicada e o de-para vivem em liquidez/queries.py.
from liquidez.queries import (  # noqa: E402
    PASSIVO_DISTRIBUIDORES, PASSIVO_JOIN_DISTRIBUIDOR, PASSIVO_JOIN_FUNDO,
    PASSIVO_NOTAS, SQL_PASSIVO_ULTIMA,
)

SQL = f"""
SELECT p.doc_digitos AS doc,
       p.nome AS cotista,
       REPLACE(REPLACE(REPLACE(f.CNPJFundo,'.',''),'/',''),'-','') AS cnpj_fundo,
       f.nmFundoInterno AS fundo, f.nmCotaInterno AS classe,
       SUM(CAST(p.vlCorrigido AS FLOAT)) AS saldo
FROM {PASSIVO_NOTAS} AS p
JOIN dbo.HOD_TB_CADASTRO_FUNDOS f ON {PASSIVO_JOIN_FUNDO}
WHERE f.Situacao='NORMAL' AND f.nmCotaInterno<>'ORIGINAL'
GROUP BY p.doc_digitos, p.nome,
         REPLACE(REPLACE(REPLACE(f.CNPJFundo,'.',''),'/',''),'-',''),
         f.nmFundoInterno, f.nmCotaInterno
"""


def cadastro_cvm() -> tuple[pd.DataFrame, str]:
    arqs = sorted(glob.glob(str(INDICE / "mercado_*.csv.gz")))
    if not arqs:
        raise SystemExit(f"indice da CVM nao encontrado em {INDICE}")
    with gzip.open(arqs[-1], "rt", encoding="utf-8", errors="replace") as fh:
        c = pd.read_csv(fh, sep=";", dtype=str).fillna("")
    c["cnpj"] = c["cnpj"].str.replace(r"\D", "", regex=True)
    return c.drop_duplicates("cnpj").set_index("cnpj"), Path(arqs[-1]).name


def carregar(data: str | None):
    from liquidez import db

    if data is None:
        d = db.consultar(SQL_PASSIVO_ULTIMA)
        data = str(d["u"].iloc[0])[:10]

    cvm, arq = cadastro_cvm()
    p = db.consultar(SQL, (data,))
    p["cotista"] = p["cotista"].astype(str).str.strip()
    # O SQL tira ponto, barra e traco, mas NAO letra: o cadastro guarda
    # "0000000None" como documento, e `strip("0")` devolvia "None" -- nao
    # vazio. Resultado: 83 cotistas sem documento caiam num balde so, que
    # o rotulo chamava de "MARKAB PARTICIPACOES" por ser a maior posicao,
    # somando R$ 513,4 mi onde a MARKAB tem R$ 378,7 mi. Sem os digitos
    # primeiro, o teste nao pega.
    p["doc"] = p["doc"].astype(str).str.replace(r"\D", "", regex=True)
    ruim = p["doc"].str.strip("0") == ""
    p.loc[ruim, "doc"] = "SEMDOC-" + p.loc[ruim, "cotista"]
    p["grupo"] = p["classe"].map(gp.grupo_da_classe)

    p["gestor"] = p["doc"].map(cvm["gestor"]).fillna("")
    p["cnpj_gestor"] = p["doc"].map(cvm["cnpj_gestor"]).fillna("")
    p["e_fundo"] = p["doc"].isin(cvm.index)

    nossos = set(p["cnpj_fundo"])
    p["tipo"] = ["casa" if (d in nossos or cg in GESTORAS_CASA)
                 else "terceiro" if ef else "direto"
                 for d, cg, ef in zip(p["doc"], p["cnpj_gestor"], p["e_fundo"])]

    # Quem decide: o gestor, quando o cotista e fundo; senao o proprio titular.
    # Fundo sem gestor no registro cai no proprio documento, para nao sumir.
    p["chave"] = [("G:" + cg) if (ef and cg and cg != "00000000000000")
                  else ("D:" + d)
                  for d, cg, ef in zip(p["doc"], p["cnpj_gestor"], p["e_fundo"])]
    p["decisor"] = [
        (GESTORAS_CASA.get(cg) or g or n)
        if k.startswith("G:") else n
        for k, cg, g, n in zip(p["chave"], p["cnpj_gestor"], p["gestor"],
                               p["cotista"])]
    return data, p, arq


def consolidar(p: pd.DataFrame):
    """Um registro por decisor, com os veiculos e os fundos que alcanca."""
    total = float(p["saldo"].sum())
    # Veiculo que TAMBEM e fundo nosso tem relatorio proprio: guardamos o
    # CNPJ para a pagina poder linkar. O HOD II, por exemplo, e veiculo da
    # Multiplica Capital e fundo do nosso relatorio ao mesmo tempo.
    nossos = set(p["cnpj_fundo"])
    rot = (p.sort_values("saldo", ascending=False)
           .drop_duplicates("chave").set_index("chave")["decisor"])

    # Gestor tem VEICULOS (fundos dele, um documento cada); titular direto tem
    # CONTAS (o mesmo documento sob varias grafias). Contar documento nos dois
    # dava 1 na coluna e N no detalhe, com o mesmo rotulo.
    d = p.groupby("chave").agg(
        docs=("doc", "nunique"), contas=("cotista", "nunique"),
        nossos=("cnpj_fundo", "nunique"),
        valor=("saldo", "sum"), tipo=("tipo", "first"))
    d["nome"] = d.index.map(rot)
    d["pct"] = d["valor"] / total * 100
    d = d.sort_values("valor", ascending=False)
    d["acum"] = d["pct"].cumsum()
    d["pos"] = range(1, len(d) + 1)
    d["gestor"] = d.index.str.startswith("G:")
    d["veiculos"] = d["docs"].where(d["gestor"], d["contas"])

    pv = p.pivot_table(index="chave", columns="grupo", values="saldo",
                       aggfunc="sum").fillna(0.0)
    for g, *_ in gp.GRUPOS:
        d[g] = d.index.map(pv[g]).fillna(0.0) if g in pv.columns else 0.0

    veic, fund, trio = {}, {}, {}
    for k, g in p.groupby("chave"):
        # O veiculo de um gestor e o FUNDO dele -- um documento. O cadastro
        # guarda o mesmo fundo sob varias grafias ("PRIME FIM CP" e "PRIME
        # FUNDO DE INVESTIMENTO MULTIMERCADO..."), entao agrupar por nome
        # inventava veiculo. Para titular direto e o contrario: o documento
        # e um so e o que existe de fato sao as contas, por nome.
        e_gestor = str(k).startswith("G:")
        g = g.copy()
        if e_gestor:
            canon = (g.sort_values("saldo", ascending=False)
                     .drop_duplicates("doc").set_index("doc")["cotista"])
            g["veiculo"] = g["doc"].map(canon)
        else:
            g["veiculo"] = g["cotista"]
        # Documento do veiculo, so quando ele e fundo nosso -- e o que vira
        # link para o relatorio por fundo.
        g["v_cn"] = g["doc"].where(g["doc"].isin(nossos), "")

        v = (g.groupby("veiculo", as_index=False)
             .agg(saldo=("saldo", "sum"), cn=("v_cn", "max"))
             .sort_values("saldo", ascending=False))
        veic[k] = [{"n": r.veiculo, "v": round(float(r.saldo), 2),
                    "cn": r.cn} for r in v.itertuples()]
        # Cruzamento veiculo x fundo x classe: e o que o grafo individual
        # precisa para ligar as tres colunas.
        t = (g.groupby(["veiculo", "v_cn", "cnpj_fundo", "fundo", "classe",
                        "grupo"], as_index=False)["saldo"].sum()
             .sort_values("saldo", ascending=False))
        trio[k] = [{"vn": r.veiculo, "vcn": r.v_cn, "cn": r.cnpj_fundo,
                    "f": r.fundo, "c": r.classe, "g": r.grupo,
                    "v": round(float(r.saldo), 2)} for r in t.itertuples()]
        f = (g.groupby(["cnpj_fundo", "fundo", "classe", "grupo"],
                       as_index=False)["saldo"].sum()
             .sort_values("saldo", ascending=False))
        fund[k] = [{"f": r.fundo, "c": r.classe, "g": r.grupo,
                    "cn": r.cnpj_fundo, "v": round(float(r.saldo), 2)}
                   for r in f.itertuples()]

    # PL de cada fundo e de cada classe: base do "% do PL" na ponta direita,
    # que e onde a concentracao aparece.
    plf = {c: round(float(v), 2)
           for c, v in p.groupby("cnpj_fundo")["saldo"].sum().items()}
    plc = {f"{c}|{cl}": round(float(v), 2)
           for (c, cl), v in
           p.groupby(["cnpj_fundo", "classe"])["saldo"].sum().items()}
    return d, veic, fund, trio, plf, plc


# --- O grafo geral: quem decide -> onde aplica -------------------------------

# Quantos decisores entram no desenho de conjunto. Cem cobrem 99,0% do passivo
# (medido em 31/08/2026) e alcancam 83 dos 89 fundos; os 385 restantes somam 1,0%
# e so alongariam a pagina. O grafo individual, ao clicar na tabela, continua
# valendo para todos os 485.
TOPO_GRAFO = 100

# Area da simulacao de forcas. Mais alta que a do `grafo_passivo` (1160x780)
# porque aqui sao ~186 bolas contra ~100 la: com a area de origem elas empilhavam
# e a etapa de separacao empurrava tudo para as bordas.
LARG_BOLHA, ALT_BOLHA = 1120, 1080


def grafo_geral(p: pd.DataFrame, d: pd.DataFrame, plf: dict,
                topo: int = TOPO_GRAFO) -> tuple[str, str, dict]:
    """Mapa de bolhas: os `topo` maiores decisores e os fundos em que aplicam.

    A posicao NAO e dado -- sai da simulacao de forcas do `grafo_passivo`
    (Fruchterman-Reingold, deterministica: mesma carga, mesmo desenho). O que ela
    revela e vizinhanca: decisor que divide fundo com outro decisor e puxado para
    perto dele, e os aglomerados que se formam sao grupos de fundos com a mesma
    base de investidores. Um satelite isolado e fundo que depende de um so.

    Tamanho da bola: o valor. Decisor vem solido, na cor do tipo (casa, terceiro,
    direto); fundo vem vazado, para a vista separar as duas naturezas sem ler
    rotulo. A cor da linha e a senioridade da maior classe daquela ligacao.

    Uma ligacao por par (decisor, fundo) -- nao por classe. Abrir por classe
    levaria 281 linhas a 396 e o mapa embolava; o detalhe por classe continua no
    grafo individual, ao clicar na tabela.

    Devolve (svg, legenda, resumo).
    """
    chaves = list(d.head(topo).index)
    sub = p[p["chave"].isin(set(chaves))]

    # Arestas agregadas por (decisor, fundo); a cor vem da classe de maior saldo.
    ag = (sub.groupby(["chave", "cnpj_fundo", "fundo"], as_index=False)
          .agg(valor=("saldo", "sum"), classes=("classe", "nunique")))
    maior_g = (sub.sort_values("saldo", ascending=False)
               .drop_duplicates(["chave", "cnpj_fundo"])
               .set_index(["chave", "cnpj_fundo"])["grupo"])
    ag["grupo"] = [maior_g.get((k, c), "nd")
                   for k, c in zip(ag["chave"], ag["cnpj_fundo"])]

    ordem_f = (ag.groupby(["cnpj_fundo", "fundo"], as_index=False)["valor"].sum()
               .sort_values("valor", ascending=False))

    total = float(p["saldo"].sum())

    # `info` no formato que `gp.leiaute` consome. O numero da bola: o decisor usa
    # a posicao dele no ranking -- a mesma da tabela abaixo, para os dois se
    # lerem juntos; o fundo leva "F" na frente, senao 1 seria ambiguo.
    info: dict = {}
    for k in chaves:
        linha = d.loc[k]
        info["D:" + str(k)] = {
            "total": float(linha["valor"]), "num": str(int(linha["pos"])),
            "nome": str(linha["nome"]), "papel": "decisor",
            "tipo": str(linha["tipo"]),
            "pct": float(linha["pct"]), "fundos": int(linha["nossos"]),
        }
    for i, r in enumerate(ordem_f.itertuples(), start=1):
        pl = float(plf.get(r.cnpj_fundo) or 0.0)
        info["F:" + str(r.cnpj_fundo)] = {
            "total": float(r.valor), "num": f"F{i}",
            "nome": str(r.fundo), "papel": "fundo", "tipo": "fundo",
            "pct": (r.valor / pl * 100) if pl else None, "fundos": 0,
        }

    a = pd.DataFrame({
        "origem": ["D:" + str(k) for k in ag["chave"]],
        "destino": ["F:" + str(c) for c in ag["cnpj_fundo"]],
        "saldo": ag["valor"].astype(float),
        "grupo": ag["grupo"],
        "classes": ag["classes"],
    })

    nos, pos, raio = gp.leiaute(info, a, LARG_BOLHA, ALT_BOLHA)

    # Recorta o quadro no que foi desenhado: a nuvem quase nunca tem o formato da
    # area, e deixar faixa vazia dos lados encolhe o desenho na tela.
    xs = [pos[n][0] for n in nos]
    ys = [pos[n][1] for n in nos]
    rr = max(raio.values())
    x0, y0 = min(xs) - rr - 10, min(ys) - rr - 24
    larg = max(xs) + rr + 10 - x0
    alt = max(ys) + rr + 14 - y0

    svg = [f'<svg viewBox="{x0:.0f} {y0:.0f} {larg:.0f} {alt:.0f}" '
           f'class="pg-gg pg-bolha" role="img" aria-label="Mapa de bolhas: '
           f'{len(chaves)} decisores e os fundos em que aplicam">']

    # Arestas primeiro, para as bolas ficarem por cima e o numero legivel.
    vmax = float(a["saldo"].max()) if len(a) else 1.0
    for r in a.sort_values("saldo").itertuples():
        o, dd = r.origem, r.destino
        if o not in pos or dd not in pos:
            continue
        (x1, y1), (x2, y2) = pos[o], pos[dd]
        vx, vy = x2 - x1, y2 - y1
        L = (vx * vx + vy * vy) ** 0.5 or 1.0
        # Curva para o lado: o feixe que chega num hub abre em leque em vez de
        # virar um traco grosso onde nao se conta quantas ligacoes sao.
        curva = min(L * 0.14, 52)
        cx, cy = (x1 + x2) / 2 - vy / L * curva, (y1 + y2) / 2 + vx / L * curva
        ax, ay = gp._borda(x1, y1, cx, cy, raio[o])
        bx, by = gp._borda(x2, y2, cx, cy, raio[dd] + 2)
        w = 0.5 + (r.saldo / vmax) ** 0.5 * 4.6
        nc = int(r.classes or 1)
        svg.append(
            f'<path class="pg-lk pg-g-{r.grupo}" data-o="{gp._e(o)}" '
            f'data-d="{gp._e(dd)}" stroke-width="{w:.2f}" '
            f'd="M{ax:.1f} {ay:.1f} Q{cx:.1f} {cy:.1f} {bx:.1f} {by:.1f}">'
            f'<title>{gp._e(info[o]["nome"])} → {gp._e(info[dd]["nome"])}\n'
            f'R$ {r.saldo:,.2f}{f" em {nc} classes" if nc > 1 else ""}</title>'
            f'</path>')

    # Bolas: da menor para a maior, para a grande nao ficar escondida atras.
    for n in sorted(nos, key=lambda n: info[n]["total"]):
        v, (x, y), rd = info[n], pos[n], raio[n]
        if rd >= gp.R_DENTRO:
            rot = (f'<text class="pg-nd" x="{x:.1f}" y="{y:.1f}" '
                   f'style="font-size:{min(rd * 0.95, 14):.1f}px">'
                   f'{v["num"]}</text>')
        else:
            rot = (f'<text class="pg-nf" x="{x:.1f}" y="{y - rd - 3.5:.1f}">'
                   f'{v["num"]}</text>')
        if v["papel"] == "decisor":
            dica = (f'{v["num"]} · {gp._e(v["nome"])}\n{v["tipo"]}\n'
                    f'R$ {v["total"]:,.2f} — {v["pct"]:.2f}% do passivo\n'
                    f'{v["fundos"]} fundo(s)')
        else:
            dica = (f'{v["num"]} · {gp._e(v["nome"])}\n'
                    f'R$ {v["total"]:,.2f} destes {len(chaves)} decisores'
                    + (f'\n{v["pct"]:.2f}% do passivo do fundo'
                       if v["pct"] is not None else ""))
        # Circulo invisivel de alvo, maior que a bola. Sem ele a bola de 4,5px e
        # alvo de mouse quase impossivel, e a do fundo e pior ainda: ela e vazada
        # (`fill: none`), e circulo sem preenchimento so recebe evento na LINHA
        # do traco -- 1,3px. O alvo leva `pointer-events` e a bola visivel nao.
        alvo = max(rd + 4.0, 9.0)
        svg.append(
            f'<g class="pg-no pg-{v["papel"]} pg-t-{v["tipo"]}" '
            f'data-n="{gp._e(n)}" tabindex="0">'
            f'<circle class="pg-hit" cx="{x:.1f}" cy="{y:.1f}" '
            f'r="{alvo:.1f}"/>'
            f'<circle class="pg-bola" cx="{x:.1f}" cy="{y:.1f}" '
            f'r="{rd:.1f}"/>{rot}'
            f'<title>{dica}</title></g>')
    svg.append("</svg>")

    # Legenda numerada, duas listas: sem ela o numero na bola nao diz nada.
    def lista(itens):
        return "".join(
            f'<li class="pg-ch pg-{info[n]["papel"]} pg-t-{info[n]["tipo"]}" '
            f'data-n="{gp._e(n)}" title="{gp._e(info[n]["nome"])} — '
            f'R$ {info[n]["total"]:,.0f}"><i class="pg-pt"></i>'
            f'<b>{info[n]["num"]}</b>'
            f'<span class="pg-nm">{gp._e(info[n]["nome"])}</span></li>'
            for n in itens)

    d_nos = ["D:" + str(k) for k in chaves]
    f_nos = ["F:" + str(c) for c in ordem_f["cnpj_fundo"]]
    legenda = (
        '<div class="pg-chaves">'
        f'<div><div class="pg-cht">Decisores ({len(d_nos)})</div>'
        f'<ol class="pg-lst">{lista(d_nos)}</ol></div>'
        f'<div><div class="pg-cht">Fundos ({len(f_nos)})</div>'
        f'<ol class="pg-lst">{lista(f_nos)}</ol></div></div>')

    # Quem depende de um decisor so: e a leitura de risco que o desenho permite.
    por_fundo = ag.groupby("cnpj_fundo").agg(n=("chave", "nunique"),
                                             v=("valor", "sum"))
    maior_lig = (ag.sort_values("valor", ascending=False)
                 .drop_duplicates("cnpj_fundo"))
    dependentes = []
    for r in maior_lig.itertuples():
        pl = float(plf.get(r.cnpj_fundo) or 0.0)
        if pl and r.valor / pl >= 0.99 and int(por_fundo.loc[r.cnpj_fundo, "n"]) == 1:
            dependentes.append((str(r.fundo), d.loc[r.chave, "nome"], r.valor))

    resumo = {
        "decisores": len(chaves), "fundos": len(ordem_f), "ligacoes": len(ag),
        "cobertura": float(d.head(topo)["valor"].sum()) / total * 100,
        "de": len(d), "dependentes": sorted(dependentes, key=lambda x: -x[2]),
    }
    return "".join(svg), legenda, resumo


ROT_TIPO = {"casa": "casa", "terceiro": "terceiro", "direto": "direto"}
NOME_TIPO = {"casa": "gestão da casa", "terceiro": "gestor de terceiro",
             "direto": "investidor direto"}


def montar(data: str, p: pd.DataFrame, d: pd.DataFrame, veic: dict,
           fund: dict, trio: dict, plf: dict, plc: dict, arq: str) -> str:
    total = float(p["saldo"].sum())
    por_tipo = p.groupby("tipo")["saldo"].sum()
    gestores = d[d["gestor"]]
    diretos = d[~d["gestor"]]

    svg_geral, legenda_geral, rg = grafo_geral(p, d, plf)
    # Fundo cujo passivo inteiro vem de um decisor so: e a leitura que o desenho
    # permite e a tabela nao. Sai escrito, nao como cor no grafico -- resgate ali
    # nao tem quem compense.
    if rg["dependentes"]:
        itens = " · ".join(
            f"<b>{gp._e(f)}</b> ({gp._e(str(n)[:28])})"
            for f, n, _v in rg["dependentes"][:8])
        resto = (f" e outros {len(rg['dependentes']) - 8}"
                 if len(rg["dependentes"]) > 8 else "")
        aviso_dep = (
            f'<p class="nota"><b>{len(rg["dependentes"])} fundo(s) com passivo '
            f'inteiro de um decisor só:</b> {itens}{resto}. Resgate ali não tem '
            f'quem compense.</p>')
    else:
        aviso_dep = ""

    kpis = [
        ("Passivo da casa", f"R$ {total / 1e9:,.2f} bi",
         f"{p['cnpj_fundo'].nunique()} fundos, posição de "
         f"{dt.date.fromisoformat(data):%d/%m/%Y}"),
        ("Decisores", f"{len(d)}",
         f"{len(gestores)} gestores e {len(diretos)} titulares diretos"),
        ("Sob gestão de terceiro",
         f"{por_tipo.get('terceiro', 0) / total * 100:.1f}%",
         f"R$ {por_tipo.get('terceiro', 0) / 1e9:,.2f} bi"),
        ("Maior decisor", f"{d.iloc[0]['pct']:.2f}%",
         f"{d.iloc[0]['nome'][:38]} — R$ {d.iloc[0]['valor'] / 1e9:,.2f} bi"),
    ]

    linhas = "".join(
        f'<tr data-k="{gp._e(r.Index)}" data-t="{r.tipo}" '
        f'data-n="{gp._e(str(r.nome).upper())}">'
        f'<td>{r.pos}</td>'
        f'<td><b>{gp._e(str(r.nome)[:54])}</b>'
        + (f'<span class="tp">{r.veiculos} '
           f'{"veículos" if r.gestor else "contas"}</span>'
           if r.veiculos > 1 else "")
        + f'</td><td class="tipo">{NOME_TIPO[r.tipo]}</td>'
        f'<td>{r.veiculos}</td><td>{r.nossos}</td>'
        f'<td>{r.valor:,.2f}</td><td><b>{r.pct:.2f}%</b></td>'
        f'<td>{r.acum:.1f}%</td></tr>'
        for r in d.head(TOPO_TABELA).itertuples())

    dados = json.dumps({
        "d": {k: {"nome": str(v.nome), "tipo": NOME_TIPO[v.tipo],
                  "gestor": bool(v.gestor), "veiculos": int(v.veiculos),
                  "nossos": int(v.nossos), "pos": int(v.pos),
                  "valor": round(float(v.valor), 2),
                  "pct": round(float(v.pct), 4),
                  "g": {g: round(float(getattr(v, g)), 2) for g, *_ in gp.GRUPOS}}
              for k, v in zip(d.index, d.itertuples())},
        "veic": veic, "fund": fund, "trio": trio,
        "plf": plf, "plc": plc, "total": round(total, 2),
    }, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")

    estilo_gf = gf.ESTILO if gf else ""
    cores = json.dumps({k: cor for k, _r, cor, _p in gp.GRUPOS})
    rotulos = json.dumps({k: rot for k, rot, _c, _p in gp.GRUPOS},
                         ensure_ascii=False)
    logo = gp._logo()
    marca = (f'<img src="{logo}" alt="Multiplica">' if logo
             else '<div class="titulo">MULTIPLICA</div>')
    hoje = dt.date.today()

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>O passivo por gestor — Multiplica</title>
<style>
:root {{ color-scheme: light;
{gp._tokens("claro")}}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  color-scheme: dark;
{gp._tokens("escuro")}}} }}
:root[data-theme="dark"] {{ color-scheme: dark;
{gp._tokens("escuro")}}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--plano); color: var(--tinta); padding-bottom: 64px; }}
.limite {{ max-width: 1400px; margin: 0 auto; padding: 0 24px; }}
.marca {{ background: {gp.NAVY}; padding: 26px 0 28px; margin-bottom: 8px; }}
.marca .limite {{ display: flex; align-items: center;
  justify-content: space-between; gap: 20px; flex-wrap: wrap; }}
.marca img {{ height: 84px; width: auto; max-width: 100%; display: block; }}
@media (max-width: 900px) {{ .marca img {{ height: 60px; }} }}
.marca .titulo {{ color: #fff; font-size: 19px; font-weight: 640; }}
.marca .quando {{ color: {gp.AZUL_PALIDO}; font-size: 13px; margin-top: 2px; }}
.marca .quando b {{ color: {gp.CIANO}; font-weight: 620; }}
.tema {{ display: inline-flex; align-items: center; gap: 7px;
  background: transparent; color: {gp.AZUL_PALIDO};
  border: 1px solid rgba(255,255,255,0.28); border-radius: 999px;
  padding: 7px 14px; font: inherit; font-size: 12.5px; cursor: pointer;
  line-height: 1; }}
h1 {{ font-size: 22px; margin: 0 0 4px; font-weight: 650; }}
h2 {{ font-size: 15px; margin: 36px 0 4px; font-weight: 650; }}
h3 {{ font-size: 13.5px; font-weight: 650; margin: 20px 0 2px; }}
.sub {{ color: var(--tinta2); font-size: 13px; margin: 0 0 8px; }}
.nota {{ color: var(--tinta3); font-size: 12px; margin: 0 0 14px; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(215px,1fr));
  gap: 12px; margin-top: 18px; }}
.kpi {{ background: var(--superficie); border: 1px solid var(--borda);
  border-radius: 10px; padding: 14px 16px; }}
.kpi .r {{ color: var(--tinta2); font-size: 12px; }}
.kpi .v {{ font-size: 25px; font-weight: 640; margin: 4px 0 2px; line-height: 1.1; }}
.kpi .n {{ color: var(--tinta3); font-size: 11px; }}
.cartao {{ background: var(--superficie); border: 1px solid var(--borda);
  border-radius: 10px; padding: 14px 16px; margin-top: 8px; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12.5px; }}
th, td {{ text-align: left; padding: 6px 10px;
  border-bottom: 1px solid var(--borda); }}
th {{ color: var(--tinta2); font-weight: 600; font-size: 11.5px;
  text-transform: uppercase; letter-spacing: .04em; }}
td:nth-child(n+4) {{ text-align: right; font-variant-numeric: tabular-nums; }}
td:first-child {{ text-align: right; color: var(--tinta3); width: 34px;
  font-variant-numeric: tabular-nums; }}
td.tipo {{ color: var(--tinta2); font-size: 11.5px; }}
tbody tr {{ cursor: pointer; }}
tbody tr:hover td {{ background: var(--plano); }}
tbody tr.sel td {{ background: color-mix(in srgb, var(--serie) 16%,
  var(--superficie)); }}
tr[hidden] {{ display: none; }}
.tp {{ font-size: 10px; color: var(--tinta3); border: 1px solid var(--borda);
  border-radius: 3px; padding: 0 4px; margin-left: 6px; white-space: nowrap; }}
.barra {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
  margin: 14px 0 4px; }}
.filtro {{ font: inherit; font-size: 12px; cursor: pointer;
  background: var(--superficie); color: var(--tinta2);
  border: 1px solid var(--borda); border-radius: 999px; padding: 6px 13px; }}
.filtro.ativo {{ background: var(--tinta); color: var(--superficie);
  border-color: var(--tinta); }}
#busca {{ font: inherit; font-size: 12.5px; padding: 6px 12px;
  border: 1px solid var(--borda); border-radius: 999px; min-width: 250px;
  background: var(--superficie); color: var(--tinta); }}
.faixas {{ display: flex; gap: 2px; margin: 10px 0 4px; height: 26px;
  border-radius: 5px; overflow: hidden; }}
.faixas div {{ display: flex; align-items: center; justify-content: center;
  font-size: 10.5px; color: #fff; font-weight: 620; min-width: 2px; }}
.duas {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px 22px; }}
@media (max-width: 980px) {{ .duas {{ grid-template-columns: 1fr; }} }}
.cabeca {{ display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  border-bottom: 1px solid var(--borda); padding-bottom: 8px; }}
.cabeca h3 {{ font-size: 15px; margin: 0; }}
.cabeca span {{ color: var(--tinta2); font-size: 12px; }}
.det td {{ cursor: default; }}
.aviso {{ background: var(--superficie); border: 1px solid var(--borda);
  border-left: 3px solid var(--atencao); border-radius: 8px;
  padding: 12px 16px; margin-top: 16px; font-size: 13px; color: var(--tinta2); }}
.aviso b {{ color: var(--atencao); }}
.rodape {{ color: var(--tinta3); font-size: 11.5px; margin-top: 40px;
  border-top: 1px solid var(--borda); padding-top: 14px; }}
/* Abas de senioridade, dentro do detalhe. */
.filtro.fc {{ display: inline-flex; align-items: center; gap: 6px; }}
.filtro.fc .lgp {{ width: 9px; height: 9px; border-radius: 50%;
  display: inline-block; }}
.filtro.fc .tp {{ margin-left: 2px; border: 0; color: inherit; opacity: .7; }}
.gf-faixa {{ cursor: pointer; }}
/* Linha do grafo que é fundo nosso: abre o relatório por fundo. */
.gf-no.gf-link rect {{ stroke: var(--serie); stroke-width: 1.4; }}
.gf-no.gf-link {{ cursor: alias; }}
.gf-no.gf-link:hover rect {{ fill: var(--plano); stroke-width: 2.2; }}
a.rel {{ color: var(--serie); text-decoration: none;
  border-bottom: 1px dotted var(--serie); }}
a.rel:hover {{ border-bottom-style: solid; }}

/* --- o mapa de bolhas: quem decide -> onde aplica --- */
.pg-comchave {{ display: flex; gap: 14px; align-items: flex-start;
  flex-wrap: wrap; }}
.pg-vista {{ flex: 1 1 560px; min-width: 0; }}
.pg-gg {{ display: block; width: 100%; height: auto; }}
.pg-gg text {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  text-anchor: middle; pointer-events: none;
  font-variant-numeric: tabular-nums; }}
/* Numero dentro da bola quando cabe; senao encostado em cima dela, com halo da
   cor do fundo para nao se perder sobre uma linha. */
.pg-nd {{ fill: var(--tinta); font-weight: 640; dominant-baseline: central; }}
.pg-nf {{ fill: var(--tinta2); font-size: 8.5px; paint-order: stroke;
  stroke: var(--superficie); stroke-width: 2.6px; stroke-linejoin: round; }}
/* Decisor solido, na cor do tipo; fundo vazado. A vista separa as duas naturezas
   antes de ler qualquer rotulo. */
.pg-no {{ cursor: pointer; }}
.pg-no:focus {{ outline: none; }}
/* O alvo de mouse: invisivel e maior que a bola. `pointer-events: all` faz o
   circulo sem preenchimento receber evento na area inteira, nao so no traco. */
.pg-hit {{ fill: transparent; stroke: none; pointer-events: all; }}
.pg-bola {{ stroke-width: 1.3; pointer-events: none; }}
.pg-t-casa .pg-bola {{ fill: color-mix(in srgb, var(--serie) 30%, var(--superficie));
  stroke: var(--serie); }}
.pg-t-terceiro .pg-bola {{ fill: color-mix(in srgb, var(--atencao) 30%, var(--superficie));
  stroke: var(--atencao); }}
.pg-t-direto .pg-bola {{ fill: color-mix(in srgb, var(--critico) 26%, var(--superficie));
  stroke: var(--critico); }}
.pg-fundo .pg-bola {{ fill: none; stroke: var(--tinta3);
  stroke-dasharray: 3 2; }}
.pg-no:hover .pg-bola, .pg-no:focus .pg-bola {{ stroke-width: 2.8; }}
.pg-lk {{ fill: none; opacity: .38; pointer-events: none; }}
.pg-g-sub {{ stroke: var(--critico); }} .pg-g-mez {{ stroke: var(--atencao); }}
.pg-g-sen {{ stroke: var(--serie); }}   .pg-g-uni {{ stroke: var(--neutro); }}
.pg-g-nd {{ stroke: var(--tinta3); }}
/* Com 281 linhas cruzando, apagar o resto e a unica forma de seguir uma. O estado
   fica no SVG e nao em cada elemento: um `classList` na raiz troca o desenho. */
.pg-gg.focando .pg-lk {{ opacity: .04; }}
.pg-gg.focando .pg-lk.on {{ opacity: .95; }}
.pg-gg.focando .pg-no {{ opacity: .18; }}
.pg-gg.focando .pg-no.on {{ opacity: 1; }}
/* O FUNDO selecionado tem de PARECER selecionado. Vazado e tracejado, ele acendia
   como um anel cinza de 1,3px e a selecao nao se via -- foi o que o usuario
   apontou em 29/09/2026. Selecionado, ele ganha preenchimento, traco continuo e
   a cor da serie: deixa de ser "o que sobrou aceso" e passa a ser o alvo. */
.pg-gg.focando .pg-no.on .pg-bola {{ stroke-width: 2.6; }}
.pg-gg.focando .pg-fundo.on .pg-bola {{
  fill: color-mix(in srgb, var(--serie) 38%, var(--superficie));
  stroke: var(--serie); stroke-dasharray: none; }}
/* O numero do que esta aceso tambem sobe de peso: em bola pequena o rotulo fica
   fora dela, e e por ele que se le qual fundo foi alcancado. */
.pg-gg.focando .pg-no.on .pg-nf {{ fill: var(--tinta); font-weight: 700; }}
/* Fixado por clique: anel externo, para distinguir do realce passageiro. */
.pg-gg.focando .pg-no.fixo .pg-bola {{ stroke-width: 3.4; }}
/* Legenda: o numero na bola nao diz nada sem ela. */
.pg-chaves {{ flex: 1 1 300px; max-width: 460px; display: flex; gap: 12px; }}
.pg-chaves > div {{ flex: 1 1 0; min-width: 0; }}
.pg-cht {{ font-size: 10px; letter-spacing: .04em; text-transform: uppercase;
  color: var(--tinta3); font-weight: 650; margin-bottom: 4px; }}
.pg-lst {{ margin: 0; padding: 0; list-style: none; font-size: 10.5px;
  line-height: 1.34; max-height: 620px; overflow-y: auto; }}
.pg-ch {{ display: flex; align-items: center; gap: 5px; cursor: pointer;
  padding: 1px 4px; border-radius: 3px; color: var(--tinta2); }}
.pg-ch:hover {{ background: var(--plano); color: var(--tinta); }}
.pg-ch b {{ min-width: 24px; text-align: right; color: var(--tinta);
  font-weight: 640; font-variant-numeric: tabular-nums; }}
.pg-ch .pg-nm {{ overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }}
.pg-ch .pg-pt {{ width: 7px; height: 7px; border-radius: 50%; flex: 0 0 7px;
  background: var(--serie); }}
.pg-ch.pg-t-terceiro .pg-pt {{ background: var(--atencao); }}
.pg-ch.pg-t-direto .pg-pt {{ background: var(--critico); }}
.pg-ch.pg-fundo .pg-pt {{ background: none;
  box-shadow: inset 0 0 0 1.5px var(--tinta3); }}
/* O item da legenda acende com o desenho: selecionar um gestor na nuvem tem de
   dizer QUAIS fundos, e o nome esta aqui -- na bola so cabe o numero. */
.pg-ch.on {{ background: color-mix(in srgb, var(--serie) 16%, var(--superficie));
  color: var(--tinta); font-weight: 600; }}
{estilo_gf}
</style></head><body>

<header class="marca"><div class="limite">{marca}
<div style="text-align:right">
  <div class="titulo">O passivo por quem decide</div>
  <div class="quando">Gestor do fundo cotista, ou o próprio titular &middot;
     posição <b>{dt.date.fromisoformat(data):%d/%m/%Y}</b></div>
  <div style="margin-top:10px"><button class="tema" id="tema" type="button">
    <span class="simbolo">&#9790;</span><span class="rotulo">Tema escuro</span>
  </button></div>
</div></div></header>

<main class="limite">
<h1>Quem decide sobre o passivo</h1>
<p class="sub">Quando o cotista é um fundo, quem aplica e resgata não é o
   fundo — é o gestor dele. Agrupar por gestor junta o que a lista por fundo
   separa. Quando o cotista não é fundo, o titular é ele mesmo.</p>

<div class="kpis">{"".join(
  f'<div class="kpi"><div class="r">{gp._e(r)}</div>'
  f'<div class="v">{gp._e(v)}</div><div class="n">{gp._e(n)}</div></div>'
  for r, v, n in kpis)}</div>

<h2>Quem decide, e onde aplica</h2>
<p class="nota">Os <b>{rg['decisores']}</b> maiores decisores de {rg['de']} —
   {rg['cobertura']:.1f}% do passivo — e os <b>{rg['fundos']}</b> fundos em que
   eles entram, em {rg['ligacoes']} ligações. Bola <b>cheia</b> é decisor, na cor
   do tipo; bola <b>vazada</b> é fundo nosso. O tamanho é o valor, a espessura da
   linha também, e a cor da linha é a senioridade da maior classe daquela
   ligação.</p>
<p class="nota">A <b>posição não é dado</b>: sai de uma simulação de forças — quem
   se liga se atrai, todo mundo se repele. O que ela mostra é vizinhança: decisor
   que divide fundo com outro é puxado para perto dele, e os aglomerados são
   grupos de fundos com a mesma base de investidores. Bola na periferia, ligada a
   uma só, é fundo que depende de um decisor.</p>
<p class="nota"><b>Como selecionar:</b> passe o mouse numa bola — ou num nome da
   legenda — e as ligações dela ficam acesas, o resto apaga, e <b>os fundos
   alcançados acendem também</b>, no desenho e na legenda. <b>Clique</b> para
   fixar a seleção e poder conferir a lista com calma; clique de novo, clique fora
   ou <kbd>Esc</kbd> para soltar.</p>
<div class="cartao pg-comchave" style="margin-top:12px">
  <div class="pg-vista">{svg_geral}</div>
  {legenda_geral}
</div>
{aviso_dep}

<h2>A lista</h2>
<p class="nota">Clique numa linha para ver os veículos do gestor e os nossos
   fundos que ele alcança. <b>Veículos</b> é quantos fundos diferentes dele
   estão no nosso passivo; <b>nossos fundos</b> é em quantos dos nossos ele
   entrou.</p>
<div class="barra">
  <input id="busca" type="search" placeholder="buscar gestor ou titular…">
  <button type="button" class="filtro ativo" data-t="todos">Todos</button>
  <button type="button" class="filtro" data-t="terceiro">Gestor de terceiro</button>
  <button type="button" class="filtro" data-t="direto">Investidor direto</button>
  <button type="button" class="filtro" data-t="casa">Gestão da casa</button>
  <span id="conta" class="nota" style="margin:0"></span>
</div>
<div class="cartao"><table>
<thead><tr><th>#</th><th>Decisor</th><th>Tipo</th>
<th>Veículos / contas</th>
<th>Nossos fundos</th><th>Valor (R$)</th><th>% do passivo</th>
<th>Acum.</th></tr></thead>
<tbody id="lista">{linhas}</tbody></table></div>

<div id="det" class="cartao" style="margin-top:10px">
  <p class="nota" style="margin:0">Nenhum decisor selecionado — clique numa
     linha da tabela.</p>
</div>

<div class="aviso">
  <b>Quem é fundo vem do registro da CVM</b>, não do nome — a regra por nome
  deixava passar “MORE C 180 FCFIDC”, R$ 338,0 mi, como investidor direto.
  E <b>fundo gerido pela Multiplica em administrador de fora conta como
  casa</b>: a decisão é nossa. Conta omnibus de distribuidor continua
  colapsada num documento só, e ali o titular é canal, não dono.
</div>

<div class="rodape">
  Gerado em {hoje:%d/%m/%Y} de <i>MUL_GLD_PASSIVO_CONSOLIDADO</i>
  e <i>HOD_TB_CADASTRO_FUNDOS</i>, posição de
  {dt.date.fromisoformat(data):%d/%m/%Y}; gestor do cadastro da CVM em
  <i>{gp._e(arq)}</i>. A tabela lista os {TOPO_TABELA} maiores de {len(d)}
  decisores; o detalhe mostra até {TOPO_DETALHE} linhas por bloco.
</div>
</main>

<script id="dados" type="application/json">{dados}</script>
<script>
(function () {{
  var D = JSON.parse(document.getElementById('dados').textContent);
  var COR = {cores}, ROT = {rotulos};
  function moeda(v) {{ return v.toLocaleString('pt-BR',
    {{minimumFractionDigits: 2, maximumFractionDigits: 2}}); }}
  function pct(v, b) {{ return (b ? (v / b * 100).toFixed(2) : '0.00') + '%'; }}
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

  var TETO = {TOPO_DETALHE};

  // --- realce no mapa de bolhas ---------------------------------------------
  //
  // Com 281 linhas cruzando, seguir uma a olho nu nao da. Ao apontar uma bola,
  // as ligacoes dela ficam opacas, o resto quase desaparece, e as bolas da outra
  // ponta acendem junto -- que e a resposta procurada ("este gestor esta em
  // quais fundos?", "de quem este fundo depende?").
  //
  // A legenda dispara o mesmo realce: com bola de 4,5px no meio da nuvem,
  // acertar o nome na lista e mais facil que acertar o circulo.
  //
  // O estado vive numa classe na RAIZ do SVG; so as ligacoes envolvidas recebem
  // `on`. Marcar 281 elementos a cada movimento do mouse seria trabalho inutil.
  (function () {{
    var gg = document.querySelector('.pg-gg');
    if (!gg) return;
    var lks = Array.prototype.slice.call(gg.querySelectorAll('.pg-lk'));
    var nos = Array.prototype.slice.call(gg.querySelectorAll('.pg-no'));
    var chs = Array.prototype.slice.call(
      document.querySelectorAll('.pg-ch'));
    var porNo = {{}};
    nos.forEach(function (n) {{ porNo[n.dataset.n] = n; }});

    // Clique FIXA a selecao. Passar o mouse e leitura de relance; para conferir
    // nome por nome na legenda a selecao tem de parar de pouco. Clicar de novo,
    // clicar no vazio ou Esc solta.
    var fixado = null;

    function limpar() {{
      if (fixado) return;             // com selecao fixa, sair nao desfaz
      apagar();
    }}

    function apagar() {{
      gg.classList.remove('focando');
      lks.forEach(function (l) {{ l.classList.remove('on'); }});
      nos.forEach(function (n) {{
        n.classList.remove('on'); n.classList.remove('fixo'); }});
      chs.forEach(function (c) {{ c.classList.remove('on'); }});
    }}

    function focar(chave) {{
      if (!porNo[chave]) return;
      var vizinhos = {{}};
      vizinhos[chave] = 1;
      lks.forEach(function (l) {{
        var casa = l.dataset.o === chave || l.dataset.d === chave;
        l.classList.toggle('on', casa);
        if (casa) {{ vizinhos[l.dataset.o] = 1; vizinhos[l.dataset.d] = 1; }}
      }});
      nos.forEach(function (n) {{
        var aceso = !!vizinhos[n.dataset.n];
        n.classList.toggle('on', aceso);
        n.classList.toggle('fixo', aceso && n.dataset.n === chave
                           && chave === fixado);
      }});
      // A legenda acende junto: e nela que se le o nome de quem foi alcancado,
      // porque na bola pequena so cabe o numero.
      chs.forEach(function (c) {{
        c.classList.toggle('on', !!vizinhos[c.dataset.n]); }});
      gg.classList.add('focando');
    }}

    nos.concat(chs).forEach(function (el) {{
      el.addEventListener('mouseenter', function () {{
        if (!fixado) focar(el.dataset.n); }});
      el.addEventListener('focus', function () {{
        if (!fixado) focar(el.dataset.n); }});
      el.addEventListener('mouseleave', limpar);
      el.addEventListener('click', function (ev) {{
        ev.preventDefault();
        ev.stopPropagation();
        if (fixado === el.dataset.n) {{ fixado = null; apagar(); return; }}
        fixado = el.dataset.n;
        focar(fixado);
      }});
    }});
    // Clique fora do desenho e Esc soltam a selecao fixa.
    document.addEventListener('click', function () {{
      if (fixado) {{ fixado = null; apagar(); }} }});
    document.addEventListener('keydown', function (ev) {{
      if (ev.key === 'Escape' && fixado) {{ fixado = null; apagar(); }} }});
  }})();

  // --- o grafo do decisor selecionado --------------------------------------
  //
  // Tres colunas, como no relatorio por fundo: os veiculos dele, ele aberto
  // por senioridade, e os nossos fundos. Na ponta direita o segundo
  // percentual e o peso DENTRO do fundo investido -- e ali que a
  // concentracao aparece: 2% da posicao dele pode ser 100% do PL do fundo.
  var GD = {{L: 1100, H: 19, P: 26, XE: 0, WE: 360, XM: 420, WM: 210,
             XD: 690, WD: 410, TV: 15, TF: 25}};

  function grafoDecisor(k, filtro) {{
    var v = D.d[k], tri = D.trio[k] || [], fu = (D.fund[k] || []).slice();
    if (!tri.length) return '';
    var total = v.valor;
    // Filtrado, as duas pontas mostram so aquela senioridade. O bloco do
    // meio continua inteiro, apagado nas outras faixas: tirar as demais
    // faria a escolhida ocupar tudo e perder a proporcao.
    if (filtro) {{
      tri = tri.filter(function (t) {{ return t.g === filtro; }});
      fu = fu.filter(function (i) {{ return i.g === filtro; }});
      if (!tri.length) return '';
    }}

    // Coluna do meio: SEMPRE todas as senioridades dele, mesmo filtrado.
    var bandas = [], soma = {{}};
    (D.trio[k] || []).forEach(function (t) {{
      soma[t.g] = (soma[t.g] || 0) + t.v; }});
    ['sub', 'mez', 'sen', 'uni', 'nd'].forEach(function (g) {{
      if (soma[g]) bandas.push(g); }});

    // Esquerda: veiculo x senioridade, para a linha nascer na faixa certa.
    var esq = {{}}, ordemV = [];
    tri.forEach(function (t) {{
      if (!esq[t.vn]) {{ esq[t.vn] = {{v: 0, g: {{}}, cn: t.vcn || ''}};
        ordemV.push(t.vn); }}
      esq[t.vn].v += t.v;
      esq[t.vn].g[t.g] = (esq[t.vn].g[t.g] || 0) + t.v;
    }});
    ordemV.sort(function (a, b) {{ return esq[b].v - esq[a].v; }});
    var vs = ordemV.slice(0, GD.TV);
    var restoV = ordemV.slice(GD.TV);

    fu.sort(function (a, b) {{ return b.v - a.v; }});
    var fs = fu.slice(0, GD.TF), restoF = fu.slice(GD.TF);

    var nE = vs.length + (restoV.length ? 1 : 0);
    var nD = fs.length + (restoF.length ? 1 : 0);
    var alt = Math.max(nE, nD, 6) * GD.P + 150;
    var meio = Math.min(alt - 150, 420), y0 = 100;
    var p = ['<svg viewBox="0 0 ' + GD.L + ' ' + alt + '" class="gf-gr" '
             + 'role="img" aria-label="Exposição do decisor">'];

    [[GD.XE, 'OS VEÍCULOS DELE', v.gestor ? 'fundos que ele gere' : 'contas'],
     [GD.XM, esc(v.nome).slice(0, 22), 'por senioridade'],
     [GD.XD, 'NOSSOS FUNDOS', 'onde o dinheiro entrou']].forEach(function (t) {{
      p.push('<text x="' + t[0] + '" y="52" class="gf-cab">' + t[1] + '</text>');
      p.push('<text x="' + t[0] + '" y="70" class="gf-cabp">' + t[2] + '</text>');
    }});
    [[GD.XE + GD.WE, ['valor', '% dele', '']],
     [GD.XD + GD.WD, ['valor', '% dele', '% do PL']]].forEach(function (t) {{
      [116, 62, 8].forEach(function (dx, i) {{
        if (!t[1][i]) return;
        p.push('<text x="' + (t[0] - dx) + '" y="' + (y0 - 12) + '" '
          + 'text-anchor="end" class="gf-ch">' + t[1][i] + '</text>');
      }});
    }});

    var ys = y0, faixa = {{}};
    bandas.forEach(function (g) {{
      var h = Math.max(soma[g] / total * meio, 26);
      faixa[g] = [ys, h];
      p.push('<rect x="' + GD.XM + '" y="' + ys.toFixed(1) + '" width="'
        + GD.WM + '" height="' + h.toFixed(1) + '" rx="5" '
        + 'class="gf-faixa gf-f-' + g
        + (filtro && g !== filtro ? ' gf-apagada' : '')
        + '" data-cls="' + g + '" role="button" tabindex="0"><title>'
        + ROT[g] + ' — R$ ' + moeda(soma[g]) + '\\n'
        + (filtro === g ? 'clique para ver todas as senioridades'
                        : 'clique para ver só esta senioridade')
        + '</title></rect>');
      p.push('<text x="' + (GD.XM + 12) + '" y="' + (ys + 17).toFixed(1)
        + '" class="gf-cl">' + ROT[g] + '</text>');
      p.push('<text x="' + (GD.XM + GD.WM - 12) + '" y="' + (ys + 17).toFixed(1)
        + '" text-anchor="end" class="gf-clv">'
        + Math.round(soma[g]).toLocaleString('pt-BR') + '</text>');
      if (h > 40) {{
        p.push('<text x="' + (GD.XM + 12) + '" y="' + (ys + 33).toFixed(1)
          + '" class="gf-clp">' + pct(soma[g], total) + ' dele</text>');
      }}
      ys += h + 6;
    }});

    function linha(x, w, y, nome, cols, extra) {{
      var s = '<g class="gf-no"' + (extra || '') + '><rect x="' + x + '" y="'
        + y + '" width="' + w + '" height="' + GD.H + '" rx="4"/>'
        + '<text x="' + (x + 8) + '" y="' + (y + 13.5) + '">'
        + esc(nome).slice(0, 34) + '</text>';
      [[116, 'gf-v'], [62, 'gf-pc'], [8, 'gf-pa']].forEach(function (t, i) {{
        if (cols[i] === undefined) return;
        s += '<text x="' + (x + w - t[0]) + '" y="' + (y + 13.5)
          + '" text-anchor="end" class="' + t[1] + '">' + cols[i] + '</text>';
      }});
      return s;
    }}

    var vmaxE = esq[ordemV[0]].v || 1, y = y0;
    var cx = (GD.XM - (GD.XE + GD.WE)) * 0.4;
    vs.forEach(function (nome) {{
      var e = esq[nome];
      Object.keys(e.g).forEach(function (g) {{
        if (!faixa[g]) return;
        var f = faixa[g], y2 = f[0] + f[1] / 2;
        var w = 0.7 + Math.sqrt(e.g[g] / vmaxE) * 4.6;
        p.push('<path class="gf-ar gf-a-' + g + '" stroke-width="' + w.toFixed(2)
          + '" d="M' + (GD.XE + GD.WE) + ' ' + (y + GD.H / 2).toFixed(1)
          + ' C' + (GD.XE + GD.WE + cx).toFixed(0) + ' ' + (y + GD.H / 2).toFixed(1)
          + ' ' + (GD.XM - cx).toFixed(0) + ' ' + y2.toFixed(1)
          + ' ' + GD.XM + ' ' + y2.toFixed(1) + '"/>');
      }});
      p.push(linha(GD.XE, GD.WE, y, nome,
        [Math.round(e.v).toLocaleString('pt-BR'), pct(e.v, total)],
        e.cn ? ' data-rel="' + e.cn + '" class="gf-link"' : '')
        + '<title>' + esc(nome) + '\\nR$ ' + moeda(e.v) + '\\n'
        + pct(e.v, total) + ' da posição dele'
        + (e.cn ? '\\n\\nfundo nosso — clique para vê-lo no grafo de exposição' : '')
        + '</title></g>');
      y += GD.P;
    }});
    if (restoV.length) {{
      var sv = 0;
      restoV.forEach(function (n) {{ sv += esq[n].v; }});
      p.push(linha(GD.XE, GD.WE, y, 'outros ' + restoV.length + ' veículo(s)',
        [Math.round(sv).toLocaleString('pt-BR'), pct(sv, total)]) + '</g>');
    }}

    var vmaxD = fs[0] ? fs[0].v : 1;
    var borda = GD.XM + GD.WM, cxd = (GD.XD - borda) * 0.4;
    y = y0;
    fs.forEach(function (i) {{
      var f = faixa[i.g] || [y0, meio], y2 = y + GD.H / 2;
      var w = 0.7 + Math.sqrt(i.v / vmaxD) * 4.6;
      var ym = f[0] + f[1] / 2;
      p.push('<path class="gf-ar gf-a-' + i.g + '" stroke-width="' + w.toFixed(2)
        + '" d="M' + borda + ' ' + ym.toFixed(1)
        + ' C' + (borda + cxd).toFixed(0) + ' ' + ym.toFixed(1)
        + ' ' + (GD.XD - cxd).toFixed(0) + ' ' + y2.toFixed(1)
        + ' ' + GD.XD + ' ' + y2.toFixed(1) + '"/>');
      var pl = D.plf[i.cn] || 0, plc = D.plc[i.cn + '|' + i.c] || 0;
      p.push(linha(GD.XD, GD.WD, y, i.f + ' · ' + i.c,
        [Math.round(i.v).toLocaleString('pt-BR'), pct(i.v, total),
         pl ? pct(i.v, pl) : '—'],
        ' data-rel="' + i.cn + '" class="gf-link"')
        + '<title>' + esc(i.f) + '\\n' + esc(i.c) + ' · R$ ' + moeda(i.v)
        + '\\n' + pct(i.v, total) + ' da posição dele'
        + '\\n' + pct(i.v, pl) + ' do PL do fundo'
        + '\\n' + pct(i.v, plc) + ' da classe ' + esc(i.c)
        + '\\n\\nclique para vê-lo no grafo de exposição'
        + '</title></g>');
      y += GD.P;
    }});
    if (restoF.length) {{
      var sf = 0;
      restoF.forEach(function (i) {{ sf += i.v; }});
      p.push(linha(GD.XD, GD.WD, y, 'outros ' + restoF.length + ' fundo(s)',
        [Math.round(sf).toLocaleString('pt-BR'), pct(sf, total), '']) + '</g>');
    }}

    p.push('</svg>');
    return p.join('');
  }}

  function tabela(itens, cab, total, comClasse) {{
    if (!itens.length) return '<p class="nota">Nada aqui.</p>';
    var corpo = itens.slice(0, TETO).map(function (i) {{
      var nome = esc(comClasse ? i.f : i.n);
      // Fundo nosso vira link para o relatorio por fundo.
      if (i.cn) nome = '<a class="rel" href="grafo_passivo.html#' + i.cn
        + '" target="_blank" rel="noopener">' + nome + '</a>';
      return '<tr><td></td><td>'
        + (comClasse ? '<i class="tp" style="border-color:var(--' + COR[i.g]
            + ');color:var(--' + COR[i.g] + ')">' + esc(i.c) + '</i> ' : '')
        + nome + '</td><td></td><td>' + moeda(i.v)
        + '</td><td>' + pct(i.v, total) + '</td></tr>';
    }}).join('');
    if (itens.length > TETO) {{
      var r = itens.slice(TETO), s = 0;
      r.forEach(function (i) {{ s += i.v; }});
      corpo += '<tr><td></td><td class="nota">+ ' + r.length
        + ' linha(s)</td><td></td><td>' + moeda(s) + '</td><td>'
        + pct(s, total) + '</td></tr>';
    }}
    return '<table><thead><tr><th></th><th>' + cab
      + '</th><th></th><th>Valor (R$)</th><th>%</th></tr></thead><tbody>'
      + corpo + '</tbody></table>';
  }}

  // Senioridade escolhida dentro do grafo do decisor. Zera ao trocar de
  // decisor -- a classe de um nao faz sentido no outro.
  var clsSel = null, decSel = null;

  function abasCls(k) {{
    var soma = {{}};
    (D.trio[k] || []).forEach(function (t) {{
      soma[t.g] = (soma[t.g] || 0) + t.v; }});
    var v = D.d[k], b = ['<button type="button" class="filtro fc'
      + (clsSel ? '' : ' ativo') + '" data-c="">Todas</button>'];
    ['sub', 'mez', 'sen', 'uni', 'nd'].forEach(function (g) {{
      if (!soma[g]) return;
      b.push('<button type="button" class="filtro fc'
        + (clsSel === g ? ' ativo' : '') + '" data-c="' + g + '">'
        + '<i class="lgp" style="background:var(--' + COR[g] + ')"></i>'
        + ROT[g] + '<span class="tp">' + pct(soma[g], v.valor)
        + '</span></button>');
    }});
    return '<div class="barra" style="margin:6px 0 0">' + b.join('') + '</div>';
  }}

  function pintarDet(k) {{
    var cx = document.getElementById('det'), v = D.d[k];
    if (!v) return;
    if (k !== decSel) {{ clsSel = null; decSel = k; }}
    var faixa = '';
    ['sub', 'mez', 'sen', 'uni', 'nd'].forEach(function (g) {{
      var x = v.g[g] || 0;
      if (x <= 0) return;
      faixa += '<div style="flex:' + x + ';background:var(--' + COR[g]
        + ')" title="' + ROT[g] + ': R$ ' + moeda(x) + ' — '
        + pct(x, v.valor) + '">'
        + (x / v.valor > 0.08 ? ROT[g] + ' ' + pct(x, v.valor) : '')
        + '</div>';
    }});
    cx.innerHTML =
      '<div class="cabeca"><h3>' + v.pos + ' · ' + esc(v.nome) + '</h3>'
      + '<span>' + v.tipo + ' · R$ ' + moeda(v.valor) + ' · '
      + pct(v.valor, D.total) + ' do passivo</span></div>'
      + '<h3>Onde esse dinheiro está, por senioridade</h3>'
      + '<div class="faixas">' + faixa + '</div>'
      + '<h3>O grafo dele</h3>'
      + '<p class="nota" style="margin:0 0 4px">À esquerda os veículos, no '
      + 'meio ele aberto por senioridade, à direita os nossos fundos. Na '
      + 'ponta direita, <b>% do PL</b> é o quanto ele detém <i>daquele fundo</i>'
      + ' — é aí que a concentração aparece: uma posição pequena para ele '
      + 'pode ser metade do fundo. Clique numa faixa de senioridade (ou numa '
      + 'aba) para ver só aquela classe dos dois lados.</p>'
      + abasCls(k) + '<div class="cartao">' + grafoDecisor(k, clsSel)
      + '</div>'
      + '<div class="duas det">'
      + '<div><h3>' + (v.gestor ? 'Os veículos dele' : 'As contas dele')
      + ' <span class="nota">— ' + v.veiculos + '</span></h3>'
      + tabela(D.veic[k] || [], v.gestor ? 'Fundo do gestor' : 'Conta',
               v.valor, false) + '</div>'
      + '<div><h3>Nossos fundos que ele alcança <span class="nota">— '
      + v.nossos + '</span></h3>'
      + tabela(D.fund[k] || [], 'Fundo · classe', v.valor, true)
      + '</div></div>';
    if (!clsSel) {{ cx.scrollIntoView({{behavior: 'smooth', block: 'nearest'}}); }}
  }}

  // A aba e a faixa do grafo fazem a mesma coisa: trocam a senioridade.
  document.getElementById('det').addEventListener('click', function (ev) {{
    var alvo = ev.target;
    if (!alvo.closest) return;
    var aba = alvo.closest('.filtro.fc');
    if (aba) {{ clsSel = aba.getAttribute('data-c') || null;
      pintarDet(decSel); return; }}
    var fx = alvo.closest('.gf-faixa');
    if (fx) {{
      var g = fx.getAttribute('data-cls');
      clsSel = (clsSel === g) ? null : g;
      pintarDet(decSel);
      return;
    }}
    // Linha do grafo que é fundo nosso: abre o relatório dele em outra aba.
    var no = alvo.closest('.gf-no[data-rel]');
    if (no) {{
      window.open('grafo_passivo.html#' + no.getAttribute('data-rel'),
                  '_blank', 'noopener');
    }}
  }});

  var linhas = Array.prototype.slice.call(
    document.querySelectorAll('#lista tr'));
  var tipo = 'todos', termo = '';

  function filtrar() {{
    var n = 0, soma = 0;
    linhas.forEach(function (tr) {{
      var ok = (tipo === 'todos' || tr.dataset.t === tipo)
        && (!termo || tr.dataset.n.indexOf(termo) >= 0);
      tr.hidden = !ok;
      if (ok) {{ n++; soma += (D.d[tr.dataset.k] || {{}}).valor || 0; }}
    }});
    document.getElementById('conta').textContent =
      n + ' de ' + linhas.length + ' — R$ ' + moeda(soma) + ' ('
      + pct(soma, D.total) + ' do passivo)';
  }}

  document.querySelectorAll('.filtro[data-t]').forEach(function (bt) {{
    bt.addEventListener('click', function () {{
      tipo = bt.dataset.t;
      document.querySelectorAll('.filtro[data-t]').forEach(function (o) {{
        o.classList.toggle('ativo', o === bt); }});
      filtrar();
    }});
  }});
  document.getElementById('busca').addEventListener('input', function (ev) {{
    termo = ev.target.value.toUpperCase(); filtrar(); }});

  // Clicar na linha abre o grafo e as tabelas do decisor.
  document.getElementById('lista').addEventListener('click', function (ev) {{
    var tr = ev.target.closest && ev.target.closest('tr[data-k]');
    if (!tr) return;
    linhas.forEach(function (o) {{ o.classList.toggle('sel', o === tr); }});
    pintarDet(tr.getAttribute('data-k'));
  }});

  filtrar();
}})();
</script></body></html>
"""


if __name__ == "__main__":
    data = sys.argv[1] if len(sys.argv) > 1 else None
    data, p, arq = carregar(data)
    d, veic, fund, trio, plf, plc = consolidar(p)
    SAIDA.write_text(montar(data, p, d, veic, fund, trio, plf, plc, arq),
                     encoding="utf-8")

    total = float(p["saldo"].sum())
    t = p.groupby("tipo")["saldo"].sum()
    print(f"posicao ....... {data}   (CVM: {arq})")
    print(f"passivo ....... R$ {total:,.2f} em {p['cnpj_fundo'].nunique()} fundos")
    print(f"decisores ..... {len(d)}   "
          f"({int(d['gestor'].sum())} gestores, "
          f"{int((~d['gestor']).sum())} titulares diretos)")
    for k in ("casa", "terceiro", "direto"):
        if k in t:
            print(f"   {NOME_TIPO[k]:<20} R$ {t[k]:>18,.2f}  "
                  f"{t[k] / total * 100:5.1f}%")
    print(f"maior decisor . {d.iloc[0]['nome'][:44]} "
          f"({d.iloc[0]['pct']:.2f}%)")
    print(f"gerado ........ {SAIDA}  ({SAIDA.stat().st_size / 1024:,.0f} KB)")
