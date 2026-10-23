"""Grafo de exposição de um fundo: quem investe nele, e onde ele aplica.

Serve a dois consumidores:

  - pagina propria:  python grafo_fundo.py "23.216.398/0001-01" [AAAA-MM-DD]
  - fragmento embutido no relatorio por fundo, via `fragmento()`

Sao DOIS desenhos, um por ponta (quebrados em 29/09/2026; antes era um so, de
tres colunas, com o fundo no meio):

    QUEM INVESTE           cotistas --> [o fundo, por classe]
    ONDE O FUNDO INVESTE   [o fundo, por classe] --> carteira de cotas

O bloco de classes aparece nos dois, porque e o mesmo fundo visto de cada lado, e
troca de lado para a seta sempre ler da origem para o destino.

Cada desenho mostra dois percentuais, e eles sao simetricos:

    quem investe:  peso no PL do fundo  e  peso DENTRO da classe comprada
    onde investe:  peso no PL do fundo  e  peso DENTRO do fundo investido

O segundo numero e sempre "quanto isso representa do bloco menor" -- e ali que
a concentracao aparece. No MULTIPLICA o HOD II e 17,34% do fundo e 100% da
subordinada; o VICENZA e 0,81% do nosso PL e 100% do PL dele.

Fontes, e por que sao diferentes nas duas pontas:

  ENTRADA  `MUL_GLD_PASSIVO_CONSOLIDADO` (antes, `HOD_PAS_POSICAO_NOTA` +
           `HOD_PAS_CAD_COTISTA`). Diz quem entrou e em que classe.
  SAIDA    `MUL_GLD_CARTEIRA_UNIFICADA`, Base_Origem = 'Outros Fundos'. NAO o
           passivo: ele so enxerga fundo do cadastro do administrador e veria
           R$ 199 mi das 14 posicoes do MULTIPLICA, contra R$ 587 mi da
           carteira.
  PL ALVO  `bi/fato_indicadores` -- o PL que o relatorio de liquidez calcula.
           Fundo externo nao tem, e fica em branco de proposito.

Todas as classes CSS levam prefixo `gf-`: este HTML e embutido no relatorio por
fundo, que ja usa `.painel` e `.gr` com outro sentido.
"""

from __future__ import annotations

import base64
import datetime as dt
import html
import re
import sys
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).parent
LOGO = AQUI / "config" / "marca" / "multiplica.png"
DIR_BI = AQUI / "bi" / "fato_indicadores"

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

GRUPOS = {"sub": ("Subordinada", "critico"), "mez": ("Mezanino", "atencao"),
          "sen": ("Sênior", "serie"), "uni": ("Única / outra", "neutro")}

# Quantos cotistas aparecem nomeados, por classe. O restante vira um no unico
# "outros N cotistas", que preserva o total sem alongar o desenho.
TOPO_POR_CLASSE = 7      # na visao geral, que mostra TODAS as classes juntas
TOPO_DETALHE = 25        # no painel de uma senioridade, pagina propria
TOPO_EMBUTIDO = 20       # idem, embutido no relatorio de liquidez


def grupo(texto) -> str:
    """Senioridade pelo nome da classe OU do ativo na carteira.

    Os dois lados escrevem diferente: o cadastro diz "SUBORDINADA", a carteira
    abrevia para "SUB"/"SB" no fim do nome. Por isso os alternativos levam
    `\\w*` -- sem ele `\\bSUBORD\\b` nao casa com SUBORDINADA, que foi o que
    jogou a classe de primeira perda para "outra" no primeiro desenho.
    """
    t = str(texto or "").strip().upper()
    if re.search(r"\b(SUBORD\w*|SUB|SB|JUNIOR\w*|JR)\b", t):
        return "sub"
    if re.search(r"\b(MEZ\w*|MZ\d?)\b", t):
        return "mez"
    if re.search(r"\b(SENIOR\w*|SEN|SR\d?)\b", t):
        return "sen"
    return "uni"


def _e(t) -> str:
    return html.escape(str(t if t is not None else ""))


def _n(v) -> str:
    return f"{v:,.0f}".replace(",", ".")


def _dig(t) -> str:
    return "".join(c for c in str(t) if c.isdigit())


def _slug(t) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(t).lower()).strip("-")


def _pc(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:.2f}%"


# --- Consultas --------------------------------------------------------------

# Passivo direto da MUL_GLD_PASSIVO_CONSOLIDADO (de onde o Datalake monta a HOD_PAS_*);
# a base de notas deduplicada e o de-para vivem em liquidez/queries.py.
from liquidez.queries import (  # noqa: E402
    PASSIVO_DISTRIBUIDORES, PASSIVO_JOIN_DISTRIBUIDOR, PASSIVO_JOIN_FUNDO,
    PASSIVO_NOTAS, SQL_PASSIVO_ULTIMA,
)

SQL_ENTRA = f"""
SELECT f.CNPJFundo AS cnpj_fundo, f.nmCotaInterno AS classe,
       p.nome AS cotista, p.doc AS doc,
       SUM(CAST(p.vlCorrigido AS FLOAT)) AS saldo
FROM {PASSIVO_NOTAS} AS p
INNER JOIN dbo.HOD_TB_CADASTRO_FUNDOS AS f ON {PASSIVO_JOIN_FUNDO}
WHERE f.Situacao = 'NORMAL'
  AND f.nmCotaInterno <> 'ORIGINAL'
GROUP BY f.CNPJFundo, f.nmCotaInterno, p.nome, p.doc
"""

# Cota de fundo na carteira nao tem um rotulo so: cada administrador batiza
# o bloco do seu jeito. Filtrar apenas "Outros Fundos" pegava R$ 4,10 bi de
# R$ 10,73 bi em 09/09/2026 e deixava 62 fundos com a carteira de fundos
# vazia -- o ADVENTURE FIM CP IE, por exemplo, tem R$ 1,17 bi em
# "Fundos-Fundos" e aparecia sem nenhuma posicao.
#
# Ficam DE FORA, de proposito:
#   "PL COTAS"    classes do proprio fundo (passivo), nao investimento;
#   "Renda_Fixa"  bloco misto -- quase tudo titulo, poucas linhas "COTA_*".
BASES_COTA = (
    "Outros Fundos",
    "Fundos-Fundos",
    "Fundos-Fundos_a_Mercado",
    "COTAS DE FUNDOS EXTERNOS",
    "Carteira de Fundos de Investimentos",
    "PORTFOLIO_INVESTIDO",
    "COTASAPLICADAS",
    "Cotas de Investimento",
    "COTAS FUNDOS DE INVESTIMENTOS",
)

SQL_SAI = f"""
SELECT docFundo AS cnpj_fundo, dsNomeFundo AS Nome_Do_Fundo, cdAtivo AS Codigo_Ativo,
       dsNomeAtivo AS Nome_Ativo, CAST(vlLiquido AS FLOAT) AS valor
FROM dbo.MUL_GLD_CARTEIRA_UNIFICADA
WHERE dtReferencia = ? AND dsBaseOrigem IN ({",".join("?" * len(BASES_COTA))})
"""

# Onde cada fundo aparece como COTISTA de outro. E o unico vinculo entre a
# linha da carteira e o CNPJ do alvo: a carteira nao traz o CNPJ do investido
# (`Emissor` vem vazio), so o nome do ativo. O casamento e feito por valor.
SQL_POSICOES = f"""
SELECT p.doc_digitos AS doc_cotista,
       f.CNPJFundo AS cnpj_alvo, f.nmFundoInterno AS alvo,
       f.nmCotaInterno AS classe_alvo,
       SUM(CAST(p.vlCorrigido AS FLOAT)) AS saldo
FROM {PASSIVO_NOTAS} AS p
INNER JOIN dbo.HOD_TB_CADASTRO_FUNDOS AS f ON {PASSIVO_JOIN_FUNDO}
WHERE f.Situacao = 'NORMAL'
  AND f.nmCotaInterno <> 'ORIGINAL'
GROUP BY p.doc_digitos, f.CNPJFundo, f.nmFundoInterno, f.nmCotaInterno
"""


def _pl_do_relatorio(data: str) -> dict:
    """PL por CNPJ, da camada de fatos. Só fundo que o pipeline processa."""
    p = DIR_BI / f"data={data}" / "dados.parquet"
    if not p.exists():
        antes = sorted(d for d in DIR_BI.glob("data=*")
                       if d.name.split("=")[1] <= data)
        if not antes:
            return {}
        p = antes[-1] / "dados.parquet"
    d = pd.read_parquet(p)
    d["cnpj"] = d["cnpj_fundo"].astype(str).str.replace(r"\D", "", regex=True)
    return {c: float(v) * 1000 for c, v in zip(d["cnpj"], d["pl_mil"])}


def carregar_todos(data: str | None = None) -> dict:
    """Carga unica para todos os fundos. Uma consulta de cada, nao uma por fundo."""
    from liquidez import db

    # O passivo nao anda no mesmo passo da carteira: em 22/09/2026 a carteira
    # ia ate 17/09 e o passivo parava em 09/09. Pedir a data do relatorio e
    # receber zero linhas seria pior que recuar -- entao recua para a ultima
    # posicao de passivo ate a data pedida, e diz qual usou.
    alvo = data
    d = db.consultar(
        SQL_PASSIVO_ULTIMA + (" WHERE dtReferencia <= ?" if data else ""),
        (data,) if data else None)
    data = str(d["u"].iloc[0])[:10] if d["u"].iloc[0] is not None else data
    if alvo and data != alvo:
        import logging
        logging.getLogger(__name__).info(
            "Passivo nao tem %s; grafo usa a posicao de %s.", alvo, data)

    entra = db.consultar(SQL_ENTRA, (data,))
    entra["cnpj"] = entra["cnpj_fundo"].map(_dig)
    entra["grupo"] = entra["classe"].map(grupo)

    sai = db.consultar(SQL_SAI, (data, *BASES_COTA))
    sai["cnpj"] = sai["cnpj_fundo"].map(_dig)
    sai["grupo"] = sai["Nome_Ativo"].map(grupo)

    pos = db.consultar(SQL_POSICOES, (data,))

    plc = (entra.groupby(["cnpj", "classe"])["saldo"].sum())
    return {
        "data": data, "entra": entra, "sai": sai, "pos": pos,
        "pl_relatorio": _pl_do_relatorio(data),
        "pl_classe": {(c, k): v for (c, k), v in plc.items()},
    }


def recortar(cnpj: str, dados: dict) -> dict | None:
    """O recorte de um fundo, já com o peso dentro do alvo calculado."""
    chave = _dig(cnpj)
    entra = dados["entra"][dados["entra"]["cnpj"] == chave].copy()
    if entra.empty:
        return None

    sai = dados["sai"][dados["sai"]["cnpj"] == chave].copy()
    pos = dados["pos"][dados["pos"]["doc_cotista"] == chave]

    # Linhas do proprio fundo classificadas como cota de fundo sao recebivel
    # proprio, nao cota de terceiro: saem do desenho e viram nota.
    #
    # O prefixo do nome sozinho erra: "ADVENTURE 2 FUNDO DE INVESTIMENTO"
    # comeca com "ADVENTURE" e e OUTRO fundo. Quem casa em valor com uma
    # posicao de passivo do proprio cotista e investimento de verdade e fica.
    nome_curto = str(sai["Nome_Do_Fundo"].iloc[0]).split()[0].upper() if len(sai) else ""
    proprio_m = (sai["Nome_Ativo"].astype(str).str.upper()
                 .str.startswith(nome_curto)) if nome_curto else sai["valor"] < 0
    if len(pos):
        casa = sai["valor"].apply(
            lambda v: bool(((pos["saldo"] - v).abs() <= 1.0).any()))
        proprio_m = proprio_m & ~casa
    proprio = float(sai[proprio_m]["valor"].sum())
    sai = sai[~proprio_m].copy()
    for col in ("alvo", "classe_alvo"):
        sai[col] = None
    sai["pl_alvo"] = float("nan")
    sai["pl_classe_alvo"] = float("nan")
    for i, r in sai.iterrows():
        m = pos[(pos["saldo"] - r["valor"]).abs() <= 1.0] if len(pos) else pos
        if len(m):
            q = m.iloc[0]
            k = _dig(q["cnpj_alvo"])
            sai.at[i, "alvo"] = q["alvo"]
            sai.at[i, "classe_alvo"] = q["classe_alvo"]
            sai.at[i, "pl_alvo"] = dados["pl_relatorio"].get(k, float("nan"))
            sai.at[i, "pl_classe_alvo"] = dados["pl_classe"].get(
                (k, q["classe_alvo"]), float("nan"))
    sai["pct_alvo"] = sai["valor"] / sai["pl_alvo"] * 100
    sai["pct_classe_alvo"] = sai["valor"] / sai["pl_classe_alvo"] * 100

    return {"entra": entra, "sai": sai, "proprio": proprio,
            "confirmado": float(pos["saldo"].sum()) if len(pos) else 0.0,
            "data": dados["data"]}


# --- Desenho ----------------------------------------------------------------

# A largura do desenho e casada com o `.limite` do relatorio (1120 menos o
# respiro), para o SVG renderizar em escala 1:1 e NAO exigir rolagem lateral.
# Com viewBox maior que o container, o navegador ou encolhe o texto ou obriga
# a rolar -- e ler valor rolando de lado e o que se quer evitar.
LARG, ALT_NO, PASSO = 1100, 19, 26

# Em 29/09/2026 o desenho de tres colunas foi quebrado em DOIS, a pedido do
# usuario: um grafico para quem investe no fundo, outro para onde ele aplica. O
# bloco de classes aparece nos dois -- e o mesmo fundo visto das duas pontas -- e
# troca de lado para a seta sempre ler da origem para o destino:
#
#   quem investe   cotistas ----> [fundo]
#   onde aplica    [fundo] ----> investidos
#
# A coluna de nomes ganhou 620px (eram 360 e 410). Cabendo os dois graficos na
# largura inteira em vez de dividi-la em tres, o nome do cotista deixa de ser
# truncado em 28 caracteres.
LADOS = {
    # (x do bloco de classes, x da coluna de nos, largura da coluna de nos)
    "entra": {"x_bloco": 790, "x_no": 0, "w_no": 620, "bloco_a_direita": True},
    "sai": {"x_bloco": 0, "x_no": 410, "w_no": 620, "bloco_a_direita": False},
}
W_BLOCO = 240
NOME_MAX = 48        # caracteres do nome que cabem em 620px a 11px


def _classes_do_passivo(entra, total, filtro, x_bloco, y_meio, meio_h):
    """O bloco de classes do fundo: retangulo por senioridade, altura pelo PL.

    Aparece nos DOIS graficos -- e o mesmo fundo, visto de cada ponta -- e mostra
    SEMPRE todas as classes, mesmo com filtro: tirar as outras faria a faixa
    escolhida ocupar a altura inteira e perder a proporcao, que e metade da
    informacao. As que nao sao o filtro ficam apagadas, nao ausentes.

    Devolve (partes do SVG, {classe: (y, altura)}) -- as faixas servem para a seta
    do lado "quem investe" chegar na altura da classe comprada.
    """
    p, faixas, ys = [], {}, y_meio
    for _, cl in (entra.groupby(["classe", "grupo"])["saldo"].sum()
                  .reset_index().sort_values("saldo", ascending=False)).iterrows():
        h = max(cl["saldo"] / total * meio_h, 26)
        faixas[cl["classe"]] = (ys, h)
        apaga = " gf-apagada" if filtro and cl["grupo"] != filtro else ""
        p.append(f'<rect x="{x_bloco}" y="{ys:.1f}" width="{W_BLOCO}" '
                 f'height="{h:.1f}" rx="5" class="gf-faixa gf-f-{cl["grupo"]}'
                 f'{apaga}" data-abre="g-{cl["grupo"]}" role="button" '
                 f'tabindex="0"><title>{_e(cl["classe"])} — clique para ver só '
                 f'esta senioridade</title></rect>')
        p.append(f'<text x="{x_bloco + 12}" y="{ys + 17:.1f}" class="gf-cl">'
                 f'{_e(cl["classe"])}</text>')
        p.append(f'<text x="{x_bloco + W_BLOCO - 12}" y="{ys + 17:.1f}" '
                 f'text-anchor="end" class="gf-clv">{_n(cl["saldo"])}</text>')
        if h > 40:
            p.append(f'<text x="{x_bloco + 12}" y="{ys + 33:.1f}" class="gf-clp">'
                     f'{cl["saldo"] / total * 100:.2f}% do PL</text>')
        ys += h + 6
    return p, faixas


def _cabecalhos(p, geo, cab_no, sub_no, nome_fundo, y_meio, rotulos):
    """Titulo de cada coluna e o rotulo das tres colunas numericas do no."""
    bloco = (_e(nome_fundo)[:26], "classes, pelo PL")
    no = (cab_no, sub_no)
    pares = ([(geo["x_no"], no), (geo["x_bloco"], bloco)]
             if geo["bloco_a_direita"] else
             [(geo["x_bloco"], bloco), (geo["x_no"], no)])
    for x, (cab, sub) in pares:
        p.append(f'<text x="{x}" y="52" class="gf-cab">{cab}</text>')
        p.append(f'<text x="{x}" y="70" class="gf-cabp">{sub}</text>')
    # Dois percentuais lado a lado nao se distinguem, e medem coisas opostas.
    fim = geo["x_no"] + geo["w_no"]
    for dx, rot in zip((116, 62, 8), rotulos):
        p.append(f'<text x="{fim - dx}" y="{y_meio - 12}" text-anchor="end" '
                 f'class="gf-ch">{rot}</text>')


def _no(x, w, y, nome, valor, pct, pct2, classe_pct2, dica) -> str:
    """Uma linha da coluna de nos: nome a esquerda, tres numeros a direita."""
    return (
        f'<g class="gf-no"><rect x="{x}" y="{y}" width="{w}" '
        f'height="{ALT_NO}" rx="4"/>'
        f'<text x="{x + 8}" y="{y + 13.5}">{_e(nome[:NOME_MAX])}</text>'
        f'<text x="{x + w - 116}" y="{y + 13.5}" text-anchor="end" '
        f'class="gf-v">{_n(valor)}</text>'
        f'<text x="{x + w - 62}" y="{y + 13.5}" text-anchor="end" '
        f'class="gf-pc">{pct:.2f}%</text>'
        f'<text x="{x + w - 8}" y="{y + 13.5}" text-anchor="end" '
        f'class="{classe_pct2}">{pct2}</text>'
        f'<title>{dica}</title></g>')


def _seta(grupo, x1, y1, x2, y2, largura) -> str:
    """Bezier de uma coluna a outra. O controle fica a 40% do vao: offset fixo
    jogava o controle para DENTRO da coluna seguinte e a curva voltava sobre si."""
    cx = (x2 - x1) * 0.4
    return (f'<path class="gf-ar gf-a-{grupo}" stroke-width="{largura:.2f}" '
            f'd="M{x1} {y1:.1f} C{x1 + cx:.0f} {y1:.1f} '
            f'{x2 - cx:.0f} {y2:.1f} {x2} {y2:.1f}"/>')


def desenhar_entra(nome_fundo, entra, filtro=None, topo=TOPO_POR_CLASSE) -> str:
    """QUEM INVESTE: cotista -> classe do fundo. Com `filtro`, só a senioridade."""
    total = float(entra["saldo"].sum())
    classes = (entra.groupby(["classe", "grupo"])["saldo"].sum()
               .reset_index().sort_values("saldo", ascending=False))
    pl_classes = dict(zip(classes["classe"], classes["saldo"]))

    itens = []
    for _, cl in classes.iterrows():
        if filtro and cl["grupo"] != filtro:
            continue
        g = (entra[entra["classe"] == cl["classe"]]
             .groupby("cotista")["saldo"].sum().sort_values(ascending=False))
        nomes = [(n, float(v)) for n, v in g.head(topo).items()]
        resto = g.iloc[topo:]
        if len(resto):
            nomes.append((f"outros {len(resto)} cotistas", float(resto.sum())))
        itens.extend((cl["classe"], cl["grupo"], n, v) for n, v in nomes)

    geo = LADOS["entra"]
    alt = max(len(itens), 6) * PASSO + 150
    meio_h = min(alt - 150, 420)
    y_meio = 100

    p = [f'<svg viewBox="0 0 {LARG} {alt}" class="gf-gr" role="img" '
         f'aria-label="Quem investe em {_e(nome_fundo)}">']
    _cabecalhos(p, geo, "QUEM INVESTE", "cotistas, por classe", nome_fundo,
                y_meio, ("valor", "% do PL", "% da classe"))
    bloco, faixas = _classes_do_passivo(entra, total, filtro, geo["x_bloco"],
                                        y_meio, meio_h)
    p.extend(bloco)

    vmax = max((v for *_x, v in itens), default=1)
    fim_no = geo["x_no"] + geo["w_no"]
    y = y_meio
    for classe, g, nome, valor in itens:
        fy, fh = faixas[classe]
        p.append(_seta(g, fim_no, y + ALT_NO / 2, geo["x_bloco"], fy + fh / 2,
                       0.7 + (valor / vmax) ** 0.5 * 4.6))
        pct = valor / total * 100
        pct_cl = valor / (float(pl_classes.get(classe, 0)) or 1.0) * 100
        p.append(_no(
            geo["x_no"], geo["w_no"], y, nome, valor, pct,
            f"{pct_cl:.2f}%", "gf-pa",
            f'{_e(nome)}\n{_e(classe)} · R$ {valor:,.2f}\n'
            f'{pct:.2f}% do PL do fundo\n{pct_cl:.2f}% da classe {_e(classe)}'))
        y += PASSO

    p.append("</svg>")
    return "".join(p)


def desenhar_sai(nome_fundo, entra, sai, filtro=None) -> str:
    """ONDE O FUNDO INVESTE: fundo -> cada posição da carteira de cotas.

    `entra` entra só para o bloco de classes e para o PL do denominador -- o lado
    do ativo não depende de quem investe, mas o "% do PL" precisa de um PL.
    """
    total = float(entra["saldo"].sum())
    if filtro:
        sai = sai[sai["grupo"] == filtro]
    sai = sai.sort_values("valor", ascending=False)

    geo = LADOS["sai"]
    alt = max(len(sai), 6) * PASSO + 150
    meio_h = min(alt - 150, 420)
    y_meio = 100

    p = [f'<svg viewBox="0 0 {LARG} {alt}" class="gf-gr" role="img" '
         f'aria-label="Onde {_e(nome_fundo)} investe">']
    _cabecalhos(p, geo, "ONDE O FUNDO INVESTE", "carteira, cotas de fundo",
                nome_fundo, y_meio, ("valor", "% nosso PL", "% do alvo"))
    bloco, _faixas = _classes_do_passivo(entra, total, filtro, geo["x_bloco"],
                                         y_meio, meio_h)
    p.extend(bloco)

    vmax = float(sai["valor"].max()) if len(sai) else 1.0
    borda = geo["x_bloco"] + W_BLOCO
    ym = y_meio + meio_h / 2
    y = y_meio
    for _, r in sai.iterrows():
        p.append(_seta(r["grupo"], borda, ym, geo["x_no"], y + ALT_NO / 2,
                       0.7 + (r["valor"] / vmax) ** 0.5 * 4.6))
        pct = r["valor"] / total * 100
        alvo = r.get("pct_alvo")
        tem = alvo is not None and not pd.isna(alvo)
        dica = (f"\n{alvo:.2f}% do PL de {_e(str(r.get('alvo') or ''))}"
                f"\n{r.get('pct_classe_alvo'):.2f}% da classe "
                f"{_e(str(r.get('classe_alvo') or ''))}"
                if tem else "\npeso no alvo: fundo fora do relatório")
        p.append(_no(
            geo["x_no"], geo["w_no"], y, str(r["Nome_Ativo"]), r["valor"], pct,
            f"{alvo:.2f}%" if tem else "—",
            "gf-pa" if tem else "gf-pa gf-vazio",
            f'{_e(r["Nome_Ativo"])}\nR$ {r["valor"]:,.2f}\n'
            f'{pct:.2f}% do nosso PL{dica}'))
        y += PASSO

    p.append("</svg>")
    return "".join(p)


# Grade das barras. Precisa de folga entre o fim da barra e a coluna de valor:
# com a barra indo ate 790 e o numero terminando em 804, um valor de 11 digitos
# ("263.619.593", ~78px a 11,5px) comecava DENTRO da barra. A barra para em 670
# e o valor comeca, no pior caso, em ~724.
B_LARG, B_ALT, B_GAP = 1100, 20, 6
B_X, B_W = 320, 350
B_CV, B_CB, B_CP = 800, 905, 1010    # fim de cada coluna numerica
B_TOPO = 30                          # espaco do cabecalho


def barras(itens, total_bloco, pl, cor, rot_nome="cotista") -> str:
    """Barras horizontais de um recorte. `itens` = [(nome, valor), ...]."""
    if not itens:
        return '<p class="nota">Sem posições.</p>'
    alt = B_TOPO + len(itens) * (B_ALT + B_GAP) + 6
    vmax = max(v for _n, v in itens) or 1

    p = [f'<svg viewBox="0 0 {B_LARG} {alt}" class="gf-gr gf-gb" role="img">',
         f'<text x="10" y="14" class="gf-bh">{_e(rot_nome)}</text>',
         f'<text x="{B_X}" y="14" class="gf-bh">participação</text>']
    for x, rot in ((B_CV, "valor (R$)"), (B_CB, "% do bloco"),
                   (B_CP, "% do PL")):
        p.append(f'<text x="{x}" y="14" text-anchor="end" '
                 f'class="gf-bh">{rot}</text>')
    p.append(f'<line x1="10" y1="21" x2="{B_CP}" y2="21" class="gf-bl"/>')

    y = B_TOPO
    for nome, valor in itens:
        w = max(valor / vmax * B_W, 2)
        p.append(f'<text x="10" y="{y + 14}" class="gf-bn">'
                 f'{_e(str(nome)[:42])}</text>')
        p.append(f'<rect x="{B_X}" y="{y + 3}" width="{w:.1f}" '
                 f'height="{B_ALT - 6}" rx="3" class="gf-bb gf-b-{cor}"/>')
        for x, cls, val in ((B_CV, "gf-bv", _n(valor)),
                            (B_CB, "gf-bp", f"{valor / total_bloco * 100:.2f}%"),
                            (B_CP, "gf-bq", f"{valor / pl * 100:.2f}%")):
            p.append(f'<text x="{x}" y="{y + 14}" '
                     f'text-anchor="end" class="{cls}">{val}</text>')
        p.append(f'<title>{_e(str(nome))}\nR$ {valor:,.2f}\n'
                 f'{valor / total_bloco * 100:.2f}% do bloco\n'
                 f'{valor / pl * 100:.2f}% do PL</title>')
        y += B_ALT + B_GAP
    p.append("</svg>")
    return "".join(p)


# --- Fragmento --------------------------------------------------------------

# Prefixo do id dos paineis de cada parte. As duas barras de abas convivem na
# mesma pagina; sem prefixo, "Subordinada" do ativo abriria o painel do passivo.
LADO_ATIVO, LADO_PASSIVO = "a", "p"


def _kpis(itens) -> str:
    return "".join(
        f'<div class="kpi"><div class="r">{_e(r)}</div>'
        f'<div class="v">{_e(v)}</div><div class="n">{_e(n)}</div></div>'
        for r, v, n in itens)

def fragmento(cnpj: str, nome_fundo: str, dados: dict,
              embutido: bool = False) -> str:
    """A seção completa do grafo: abas, grafos e tabelas. "" se não houver dado."""
    rec = recortar(cnpj, dados)
    if rec is None:
        return ""
    entra, sai = rec["entra"], rec["sai"]
    pl = float(entra["saldo"].sum())
    if pl <= 0:
        return ""
    topo = TOPO_EMBUTIDO if embutido else TOPO_DETALHE

    classes = (entra.groupby(["classe", "grupo"])["saldo"].sum()
               .reset_index().sort_values("saldo", ascending=False))
    n_cot = entra.groupby("classe")["cotista"].nunique()

    # Abas de senioridade, uma barra POR LADO. Os ids levam o prefixo do lado
    # ("a-" para o ativo, "p-" para o passivo) porque as duas barras convivem na
    # mesma pagina: sem prefixo, clicar em "Subordinada" no ativo abriria tambem o
    # painel do passivo, e vice-versa. O `SCRIPT` resolve o prefixo pelo
    # `.gf-lado` que contem o clique.
    abas_a = [f'<button type="button" class="gf-aba gf-ativa" '
              f'data-painel="{LADO_ATIVO}-geral">Visão geral</button>']
    abas_p = [f'<button type="button" class="gf-aba gf-ativa" '
              f'data-painel="{LADO_PASSIVO}-geral">Visão geral</button>']
    paineis_a, paineis_p = [], []

    for g, (rot, cor) in GRUPOS.items():
        cls_g = classes[classes["grupo"] == g]
        sai_g = sai[sai["grupo"] == g]
        if cls_g.empty and sai_g.empty:
            continue
        entra_g = entra[entra["grupo"] == g]
        v_entra, v_sai = float(entra_g["saldo"].sum()), float(sai_g["valor"].sum())
        n_c = entra_g["cotista"].nunique()
        bolinha = f'<i class="gf-ch-cor" style="background:var(--{cor})"></i>'

        # --- o lado do ativo: o que o fundo COMPRA nesse degrau ---------------
        abas_a.append(
            f'<button type="button" class="gf-aba" '
            f'data-painel="{LADO_ATIVO}-g-{g}">{bolinha}{rot}'
            f'<span class="gf-qt">{len(sai_g)}</span></button>')
        if len(sai_g):
            it = [(r["Nome_Ativo"], float(r["valor"]))
                  for _, r in sai_g.sort_values("valor", ascending=False).iterrows()]
            detalhe_a = (
                f'<p class="sub">{len(sai_g)} posição(ões), R$ {v_sai:,.2f} — '
                f'<b>{v_sai / pl * 100:.2f}% do PL</b>.</p>'
                f'<div class="cartao" style="margin-top:12px">'
                f'{desenhar_sai(nome_fundo, entra, sai, filtro=g)}</div>'
                f'<div class="cartao" style="margin-top:12px">'
                f'{barras(it, v_sai, pl, cor, "fundo investido")}</div>')
        else:
            detalhe_a = ('<p class="nota">Nenhuma posição nesta senioridade.</p>')
        paineis_a.append(
            f'<section class="gf-painel" id="{LADO_ATIVO}-g-{g}" hidden>'
            f'<h3 class="gf-tit" style="margin-top:14px">'
            f'Onde o fundo compra {_e(rot.lower())} de outros</h3>'
            f'{detalhe_a}</section>')

        # --- o lado do passivo: o que o fundo VENDE nesse degrau --------------
        abas_p.append(
            f'<button type="button" class="gf-aba" '
            f'data-painel="{LADO_PASSIVO}-g-{g}">{bolinha}{rot}'
            f'<span class="gf-qt">{n_c}</span></button>')
        if len(entra_g):
            d = entra_g.groupby("cotista")["saldo"].sum().sort_values(ascending=False)
            itens = [(n, float(v)) for n, v in d.head(topo).items()]
            resto = d.iloc[topo:]
            if len(resto):
                itens.append((f"outros {len(resto)} cotistas", float(resto.sum())))
            detalhe_p = (
                f'<p class="sub">{n_c} cotista(s) em '
                f'{", ".join(cls_g["classe"])}, R$ {v_entra:,.2f} — '
                f'<b>{v_entra / pl * 100:.2f}% do PL</b>.</p>'
                f'<div class="cartao" style="margin-top:12px">'
                f'{desenhar_entra(nome_fundo, entra, filtro=g, topo=topo)}</div>'
                f'<div class="cartao" style="margin-top:12px">'
                f'{barras(itens, v_entra, pl, cor)}</div>')
        else:
            detalhe_p = (f'<p class="nota">O fundo não tem classe '
                         f'{_e(rot.lower())} no passivo.</p>')
        paineis_p.append(
            f'<section class="gf-painel" id="{LADO_PASSIVO}-g-{g}" hidden>'
            f'<h3 class="gf-tit" style="margin-top:14px">'
            f'Quem investe na {_e(rot.lower())} do fundo</h3>'
            f'{detalhe_p}</section>')

    linhas_cl = "".join(
        f'<tr><td><span class="tag {GRUPOS[c["grupo"]][1]}">{_e(c["classe"])}'
        f'</span></td><td>{int(n_cot.get(c["classe"], 0))}</td>'
        f'<td>{c["saldo"]:,.2f}</td>'
        f'<td><b>{c["saldo"] / pl * 100:.2f}%</b></td></tr>'
        for _, c in classes.iterrows())
    linhas_pos = "".join(
        f'<tr><td>{_e(str(r["Nome_Ativo"])[:40])}</td>'
        f'<td style="text-align:left"><span class="tag '
        f'{GRUPOS[r["grupo"]][1]}">{GRUPOS[r["grupo"]][0]}</span></td>'
        f'<td>{r["valor"]:,.2f}</td>'
        f'<td><b>{r["valor"] / pl * 100:.2f}%</b></td>'
        f'<td>{_pc(r["pct_alvo"])}</td>'
        f'<td><b>{_pc(r["pct_classe_alvo"])}</b></td></tr>'
        for _, r in sai.sort_values("valor", ascending=False).iterrows())

    n_alvo = int(sai["pct_alvo"].notna().sum())
    v_alvo = float(sai.loc[sai["pct_alvo"].notna(), "valor"].sum())
    v_sai_tot = float(sai["valor"].sum())
    sub = entra[entra["grupo"] == "sub"]

    # Um conjunto de indicadores por PARTE: misturados, obrigavam a procurar qual
    # dos quatro respondia a pergunta que se estava fazendo.
    maior = (sai.loc[sai["valor"].idxmax()] if len(sai) else None)
    kpis_ativo = [
        ("Investido em fundos", f"R$ {v_sai_tot / 1e6:,.0f} mi",
         f"{len(sai)} posições · {v_sai_tot / pl * 100:.1f}% do PL"),
        ("Maior posição",
         f"{float(maior['valor']) / pl * 100:.2f}%" if maior is not None else "—",
         (str(maior["Nome_Ativo"])[:28] if maior is not None else "sem posição")),
        ("Peso medido no alvo", f"{n_alvo} de {len(sai)}",
         "só fundo do relatório de liquidez"),
    ]
    kpis_passivo = [
        ("Passivo (PL)", f"R$ {pl / 1e6:,.0f} mi",
         f"{entra['cotista'].nunique()} cotistas em {len(classes)} classes"),
        ("Primeira perda", f"R$ {sub['saldo'].sum() / 1e6:,.0f} mi",
         f"{sub['cotista'].nunique()} cotista(s) na subordinada"),
        ("Maior cotista",
         f"{float(entra.groupby('cotista')['saldo'].sum().max()) / pl * 100:.2f}%"
         if len(entra) else "—",
         "do PL, somando todas as classes"),
    ]

    quando = f"{dt.date.fromisoformat(rec['data']):%d/%m/%Y}"

    # Duas PARTES, o ativo primeiro (pedido do usuario em 29/09/2026). Cada uma
    # fecha em si: titulo, indicadores, abas de senioridade, grafico e tabela. O
    # que era uma secao com os dois lados misturados obrigava a ler o desenho
    # inteiro para responder a uma pergunta de um lado so.
    return f"""
<h2>Onde o fundo aplica</h2>
<p class="nota">Posição de {quando}. Cada posição em cota de fundo, ligada ao
   bloco de classes do próprio fundo. O segundo percentual é o peso <b>dentro do
   fundo investido</b> — é nele que a concentração aparece, e ele só existe para
   fundo que o relatório de liquidez processa.</p>
<div class="kpis">{_kpis(kpis_ativo)}</div>
<div class="gf-lado" data-lado="{LADO_ATIVO}">
  <div class="gf-abas">{"".join(abas_a)}</div>
  <section class="gf-painel" id="{LADO_ATIVO}-geral">
    <div class="cartao" style="margin-top:12px">
      {desenhar_sai(nome_fundo, entra, sai)}</div>
    <div class="cartao" style="margin-top:12px">
      <h3 class="gf-tit" style="margin-top:0">Posição a posição</h3>
      <table><thead><tr><th>Fundo investido</th>
      <th style="text-align:left">Classe</th><th>Valor</th><th>% nosso PL</th>
      <th>% PL alvo</th><th>% classe alvo</th></tr></thead>
      <tbody>{linhas_pos}</tbody></table>
      <p class="nota" style="margin:10px 0 0">As duas últimas colunas medem o
         peso <b>dentro do fundo investido</b>, e só existem para fundo que o
         relatório de liquidez processa — {n_alvo} de {len(sai)},
         R$ {v_alvo:,.0f} de R$ {v_sai_tot:,.0f}. Fundo externo fica em branco
         de propósito. Recebível próprio classificado em Outros Fundos
         (R$ {rec['proprio']:,.0f}) está fora.</p>
    </div>
  </section>
  {"".join(paineis_a)}
</div>

<h2>Quem investe no fundo</h2>
<p class="nota">Posição de {quando}. Cada cotista, na classe em que entrou. O
   segundo percentual é o peso <b>dentro da classe</b> — um cotista com 3% do PL
   pode ser 100% da subordinada, e é isso que decide quem absorve a primeira
   perda.</p>
<div class="kpis">{_kpis(kpis_passivo)}</div>
<div class="gf-lado" data-lado="{LADO_PASSIVO}">
  <div class="gf-abas">{"".join(abas_p)}</div>
  <section class="gf-painel" id="{LADO_PASSIVO}-geral">
    <div class="cartao" style="margin-top:12px">
      {desenhar_entra(nome_fundo, entra, topo=TOPO_POR_CLASSE)}</div>
    <div class="cartao" style="margin-top:12px">
      <h3 class="gf-tit" style="margin-top:0">O passivo, por classe</h3>
      <table><thead><tr><th>Classe</th><th>Cotistas</th><th>Saldo (R$)</th>
      <th>% do PL</th></tr></thead><tbody>{linhas_cl}</tbody></table>
    </div>
  </section>
  {"".join(paineis_p)}
</div>
"""


# --- Estilo e script, para quem embute --------------------------------------

ESTILO = """
.gf-abas { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }
.gf-aba { display: inline-flex; align-items: center; gap: 7px; font: inherit;
  font-size: 12.5px; cursor: pointer; background: var(--superficie);
  color: var(--tinta2); border: 1px solid var(--borda); border-radius: 999px;
  padding: 8px 15px; }
.gf-aba .gf-ch-cor { width: 9px; height: 9px; border-radius: 50%;
  display: inline-block; }
.gf-aba .gf-qt { color: var(--tinta3); font-size: 11px; }
.gf-aba:hover { border-color: var(--tinta3); }
.gf-aba.gf-ativa { background: var(--serie); border-color: var(--serie);
  color: #fff; }
.gf-aba.gf-ativa .gf-qt { color: rgba(255,255,255,.75); }
.gf-painel[hidden] { display: none; }
/* 560px de minimo: dentro do relatorio (limite de 1120) as duas tabelas nao
   cabem lado a lado e empilham, cada uma com a largura inteira. A de posicoes
   tem seis colunas -- espremida em meia largura ela voltaria a exigir rolagem,
   que e justamente o que se quer evitar. */
.gf-grade2 { display: grid;
  grid-template-columns: repeat(auto-fit, minmax(560px, 1fr));
  gap: 12px; margin-top: 12px; }
h3.gf-tit { font-size: 13.5px; font-weight: 650; margin: 24px 0 2px; }
/* Sem `min-width`: o viewBox ja cabe no container, entao o SVG escala para a
   largura disponivel sem rolagem lateral. Em tela estreita ele encolhe junto,
   que e melhor que obrigar a rolar para ler um numero. */
.gf-gr { display: block; width: 100%; height: auto; }
.gf-gr text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.gf-cab { fill: var(--tinta2); font-size: 12px; font-weight: 650;
  letter-spacing: .06em; }
.gf-cabp { fill: var(--tinta3); font-size: 10.5px; }
.gf-ch { fill: var(--tinta3); font-size: 9.5px; letter-spacing: .04em;
  text-transform: uppercase; }
.gf-ar { fill: none; opacity: .5; }
.gf-a-sub { stroke: var(--critico); } .gf-a-mez { stroke: var(--atencao); }
.gf-a-sen { stroke: var(--serie); }   .gf-a-uni { stroke: var(--neutro); }
.gf-no rect { fill: var(--superficie); stroke: var(--borda); }
.gf-no text { fill: var(--tinta); font-size: 11px; }
.gf-no text.gf-v { fill: var(--tinta2); font-variant-numeric: tabular-nums; }
.gf-no text.gf-pc { fill: var(--tinta); font-size: 10.5px; font-weight: 640;
  font-variant-numeric: tabular-nums; }
/* Peso dentro do bloco menor: destacado, porque e a concentracao que o
   percentual sobre o PL do fundo esconde. */
.gf-no text.gf-pa { fill: var(--critico); font-size: 10.5px; font-weight: 700;
  font-variant-numeric: tabular-nums; }
.gf-no text.gf-pa.gf-vazio { fill: var(--tinta3); font-weight: 400; }
.gf-faixa { stroke-width: 1.5; cursor: pointer; }
.gf-faixa:hover { fill-opacity: .34; }
.gf-faixa.gf-apagada { fill-opacity: .05; stroke-opacity: .3; }
.gf-f-sub { fill: var(--critico); fill-opacity: .16; stroke: var(--critico); }
.gf-f-mez { fill: var(--atencao); fill-opacity: .16; stroke: var(--atencao); }
.gf-f-sen { fill: var(--serie); fill-opacity: .13; stroke: var(--serie); }
.gf-f-uni { fill: var(--neutro); fill-opacity: .13; stroke: var(--neutro); }
.gf-cl { fill: var(--tinta); font-size: 12px; font-weight: 640; }
.gf-clv { fill: var(--tinta); font-size: 12px;
  font-variant-numeric: tabular-nums; }
.gf-clp { fill: var(--tinta2); font-size: 10.5px; }
.gf-bn { fill: var(--tinta); font-size: 11.5px; }
.gf-bv, .gf-bp { fill: var(--tinta2); font-size: 11.5px;
  font-variant-numeric: tabular-nums; }
.gf-bq { fill: var(--tinta); font-size: 11.5px; font-weight: 640;
  font-variant-numeric: tabular-nums; }
.gf-bh { fill: var(--tinta3); font-size: 10px; letter-spacing: .04em;
  text-transform: uppercase; }
.gf-bl { stroke: var(--borda); stroke-width: 1; }
.gf-b-critico { fill: var(--critico); } .gf-b-atencao { fill: var(--atencao); }
.gf-b-serie { fill: var(--serie); }     .gf-b-neutro { fill: var(--neutro); }
.tag.critico::before { background: var(--critico); }
.tag.critico { color: var(--critico); }
.tag.atencao::before { background: var(--atencao); }
.tag.atencao { color: var(--atencao); }
.tag.serie::before { background: var(--serie); } .tag.serie { color: var(--serie); }
.tag.neutro::before { background: var(--neutro); }
.tag.neutro { color: var(--tinta3); }
"""

# As abas passaram a viver DENTRO de cada `.gf-lado` -- uma barra para o ativo,
# outra para o passivo. O escopo por lado e o que impede o clique numa parte de
# fechar o painel da outra: com a busca global de antes, abrir "Subordinada" no
# ativo escondia tudo do passivo, porque o `hidden` era aplicado a todo painel da
# pagina cujo id nao fosse o clicado.
#
# A faixa de classe no desenho continua levando so o sufixo (`g-sub`): o mesmo
# `desenhar_*` serve aos dois lados e nao conhece o prefixo. Quem resolve e o
# `.gf-lado` que contem o clique.
SCRIPT = """
(function () {
  var lados = Array.prototype.slice.call(
    document.querySelectorAll('.gf-lado'));
  if (!lados.length) return;
  lados.forEach(function (lado) {
    var abas = Array.prototype.slice.call(lado.querySelectorAll('.gf-aba'));
    var paineis = Array.prototype.slice.call(
      lado.querySelectorAll('.gf-painel'));
    if (!abas.length) return;
    function abrir(id) {
      paineis.forEach(function (s) { s.hidden = s.id !== id; });
      abas.forEach(function (a) {
        a.classList.toggle('gf-ativa', a.dataset.painel === id); });
    }
    abas.forEach(function (a) {
      a.addEventListener('click', function () { abrir(a.dataset.painel); });
    });
    lado.querySelectorAll('.gf-faixa[data-abre]').forEach(function (r) {
      function ir() { abrir(lado.dataset.lado + '-' + r.dataset.abre); }
      r.addEventListener('click', ir);
      r.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); ir(); }
      });
    });
  });
})();
"""


# --- Página própria ---------------------------------------------------------

def _tokens(modo: str) -> str:
    return "".join(f"  --{k}: {v};\n" for k, v in CORES[modo].items())


def pagina(cnpj: str, nome_fundo: str, dados: dict) -> str:
    corpo = fragmento(cnpj, nome_fundo, dados)
    if not corpo:
        return ""
    logo = ("data:image/png;base64,"
            + base64.b64encode(LOGO.read_bytes()).decode("ascii")
            if LOGO.exists() else "")
    marca = (f'<img src="{logo}" alt="Multiplica">' if logo
             else '<div class="titulo">MULTIPLICA</div>')
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(nome_fundo)} — exposição</title>
<style>
:root {{ color-scheme: light;
{_tokens("claro")}}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  color-scheme: dark;
{_tokens("escuro")}}} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--plano); color: var(--tinta); padding-bottom: 64px; }}
.limite {{ max-width: 1400px; margin: 0 auto; padding: 0 24px; }}
.marca {{ background: {NAVY}; padding: 26px 0 28px; margin-bottom: 8px; }}
.marca .limite {{ display: flex; align-items: center;
  justify-content: space-between; gap: 20px; flex-wrap: wrap; }}
.marca img {{ height: 84px; width: auto; display: block; }}
.marca .titulo {{ color: #fff; font-size: 19px; font-weight: 640; }}
h2 {{ font-size: 15px; margin: 34px 0 4px; font-weight: 650; }}
.sub {{ color: var(--tinta2); font-size: 13px; margin: 0 0 8px; }}
.nota {{ color: var(--tinta3); font-size: 12px; margin: 0 0 14px; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(215px,1fr));
  gap: 12px; margin-top: 16px; }}
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
td:nth-child(n+3) {{ text-align: right; font-variant-numeric: tabular-nums; }}
.tag {{ display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; }}
.tag::before {{ content: ""; width: 8px; height: 8px; border-radius: 50%; }}
{ESTILO}
</style></head><body>
<header class="marca"><div class="limite">{marca}
<div style="text-align:right"><div class="titulo">{_e(nome_fundo)}</div></div>
</div></header>
<main class="limite">{corpo}</main>
<script>{SCRIPT}</script></body></html>
"""


if __name__ == "__main__":
    cnpj = sys.argv[1] if len(sys.argv) > 1 else "23.216.398/0001-01"
    data = sys.argv[2] if len(sys.argv) > 2 else None
    dados = carregar_todos(data)
    from liquidez import db

    n = db.consultar("SELECT TOP 1 nmFundoInterno AS n "
                     "FROM dbo.HOD_TB_CADASTRO_FUNDOS WHERE CNPJFundo = ?",
                     (cnpj,))
    nome = n["n"].iloc[0] if not n.empty else cnpj
    html_txt = pagina(cnpj, nome, dados)
    if not html_txt:
        sys.exit(f"sem passivo para {cnpj} em {dados['data']}")
    saida = AQUI / f"grafo_{_dig(cnpj)}.html"
    saida.write_text(html_txt, encoding="utf-8")
    rec = recortar(cnpj, dados)
    print(f"fundo ......... {nome} ({cnpj})")
    print(f"posicao ....... {dados['data']}")
    print(f"entra ......... R$ {rec['entra']['saldo'].sum():,.2f}  "
          f"({rec['entra']['cotista'].nunique()} cotistas)")
    print(f"sai ........... R$ {rec['sai']['valor'].sum():,.2f}  "
          f"({len(rec['sai'])} posicoes)")
    print(f"gerado ........ {saida}  ({saida.stat().st_size / 1024:,.0f} KB)")
