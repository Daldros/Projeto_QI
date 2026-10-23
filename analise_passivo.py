"""Como a chave de agregacao muda a leitura de concentracao do passivo.

    python analise_passivo.py ["23.216.398/0001-01"] [AAAA-MM-DD]

ANALISE SEPARADA, de proposito. Nao altera o grafo do relatorio de liquidez,
que segue agrupando por NOME de cotista. Aqui a mesma posicao e lida por tres
chaves diferentes:

  NOME         como o relatorio mostra hoje
  DOCUMENTO    a contraparte de fato -- e o que se aproxima de grupo economico
  DISTRIBUIDOR o canal por onde o dinheiro entrou

A diferenca nao e pequena. No MULTIPLICA em 09/09/2026 o passivo tem 837
cotistas por nome e 229 por documento: o BTG entra como 560 contas de cliente
final sob o mesmo CNPJ, nenhuma grande o bastante para aparecer, somando
R$ 75,4 milhoes -- o 6o maior da senior se agregado.

O que ISTO nao resolve: documento nao e grupo economico. Duas empresas do mesmo
controlador tem CNPJ diferente e continuam separadas aqui. Agregar por grupo
exige um de-para de controle que nao existe na base -- esta pagina serve para
dimensionar o problema e decidir se vale construi-lo.
"""

from __future__ import annotations

import base64
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI))

import grafo_fundo as gf
from grafo_fundo import _e, _n, _pc
# Passivo direto da MUL_GLD_PASSIVO_CONSOLIDADO (de onde o Datalake monta a HOD_PAS_*);
# a base de notas deduplicada e o de-para vivem em liquidez/queries.py.
from liquidez.queries import (  # noqa: E402
    PASSIVO_DISTRIBUIDORES, PASSIVO_JOIN_DISTRIBUIDOR, PASSIVO_JOIN_FUNDO,
    PASSIVO_NOTAS, SQL_PASSIVO_ULTIMA,
)

# Parametros: (data, cnpj) -- a data vai no FROM, dentro de PASSIVO_NOTAS.
SQL = f"""
SELECT f.nmCotaInterno AS classe, p.nome AS cotista,
       p.doc_digitos AS doc,
       d.nmDistribuidora AS distribuidor,
       SUM(CAST(p.vlCorrigido AS FLOAT)) AS saldo
FROM {PASSIVO_NOTAS} AS p
INNER JOIN dbo.HOD_TB_CADASTRO_FUNDOS AS f ON {PASSIVO_JOIN_FUNDO}
LEFT JOIN {PASSIVO_DISTRIBUIDORES} AS d ON {PASSIVO_JOIN_DISTRIBUIDOR}
WHERE f.CNPJFundo = ?
  AND f.Situacao = 'NORMAL' AND f.nmCotaInterno <> 'ORIGINAL'
GROUP BY f.nmCotaInterno, p.nome, p.doc_digitos, d.nmDistribuidora
"""


def hhi(saldos: pd.Series) -> float:
    """Herfindahl em pontos (0 a 10.000). Mede concentracao, nao tamanho."""
    s = saldos[saldos > 0]
    if s.empty:
        return 0.0
    return float(((s / s.sum()) ** 2).sum() * 10000)


def _rotulo_doc(d: pd.DataFrame) -> str:
    """Nome para um documento com varias grafias: o prefixo comum, ou o maior."""
    nomes = d.sort_values("saldo", ascending=False)["cotista"].astype(str)
    if nomes.nunique() == 1:
        return nomes.iloc[0]
    partes = [n.split() for n in nomes]
    comum = []
    for i in range(min(len(p) for p in partes)):
        t = {p[i] for p in partes}
        if len(t) == 1:
            comum.append(partes[0][i])
        else:
            break
    base = " ".join(comum) if comum else nomes.iloc[0]
    return f"{base} ({nomes.nunique()} contas)"


def visoes(p: pd.DataFrame) -> dict:
    """As tres leituras da mesma posicao."""
    p = p.copy()
    p["distribuidor"] = (p["distribuidor"].fillna("").astype(str).str.strip()
                         .replace("", "(sem distribuidor)"))
    # Rotulo por documento: consolida as grafias sob um nome so.
    rot = {doc: _rotulo_doc(g) for doc, g in p.groupby("doc")}
    p["por_documento"] = p["doc"].map(rot)
    return {
        "nome": ("Por nome de cotista", "cotista",
                 "Como o relatório de liquidez mostra hoje."),
        "documento": ("Por documento (CPF/CNPJ)", "por_documento",
                      "A contraparte de fato. Conta omnibus colapsa numa linha."),
        "distribuidor": ("Por distribuidor", "distribuidor",
                         "O canal de entrada. Cotista direto fica sem canal."),
    }, p


def metricas(p: pd.DataFrame, chave: str) -> dict:
    pl = float(p["saldo"].sum())
    g = p.groupby(chave)["saldo"].sum().sort_values(ascending=False)
    return {"n": len(g), "pl": pl, "maior": float(g.iloc[0]),
            "top3": float(g.head(3).sum()), "top5": float(g.head(5).sum()),
            "hhi": hhi(g), "serie": g}


def tabela_comparativa(p, defs) -> str:
    linhas = []
    for chave, (rot, col, _d) in defs.items():
        m = metricas(p, col)
        # 10.000 / HHI = numero de cotistas de tamanho IGUAL que produziriam a
        # mesma concentracao. E a leitura que dispensa tabela de referencia.
        equiv = 10000 / m["hhi"] if m["hhi"] else float("nan")
        linhas.append(
            f'<tr><td><b>{_e(rot)}</b></td><td>{m["n"]}</td>'
            f'<td>{m["maior"] / m["pl"] * 100:.2f}%</td>'
            f'<td>{m["top3"] / m["pl"] * 100:.2f}%</td>'
            f'<td>{m["top5"] / m["pl"] * 100:.2f}%</td>'
            f'<td><b>{m["hhi"]:,.0f}</b></td>'
            f'<td>{equiv:,.1f}</td></tr>')
    return ('<table><thead><tr><th>Chave</th><th>Cotistas</th>'
            '<th>Maior</th><th>3 maiores</th><th>5 maiores</th>'
            '<th>HHI</th><th>Equivale a</th></tr></thead><tbody>'
            + "".join(linhas) + "</tbody></table>")


def legenda_hhi(p: pd.DataFrame) -> str:
    """A legenda com os números DESTE fundo.

    O exemplo tem que ser o do proprio relatorio: a primeira versao usava os
    numeros do MULTIAGRO fixos e saia igual nos tres, dizendo "mesmo tendo 96
    cotistas" numa pagina de 38.
    """
    m_nome, m_doc = metricas(p, "cotista"), metricas(p, "por_documento")
    eq_doc = 10000 / m_doc["hhi"] if m_doc["hhi"] else float("nan")
    delta = m_doc["hhi"] - m_nome["hhi"]
    var = (delta / m_nome["hhi"] * 100) if m_nome["hhi"] else 0.0
    colapsam = m_nome["n"] - m_doc["n"]
    maior = m_doc["maior"] / m_doc["pl"] * 100

    if colapsam <= 0:
        efeito = (
            "<p>Neste fundo a chave <b>não muda nada</b>: são "
            f"{m_doc['n']} nomes para {m_doc['n']} documentos, sem conta "
            "omnibus e sem duplicidade de cadastro. O HHI fica igual nas duas "
            "contagens. Onde há omnibus, a diferença aparece — vale comparar "
            "com os outros fundos.</p>")
    else:
        efeito = (
            f"<p>Aqui a consolidação junta <b>{colapsam} nome(s)</b> em menos "
            f"documentos e move o índice de <b>{m_nome['hhi']:,.0f}</b> para "
            f"<b>{m_doc['hhi']:,.0f}</b> ({var:+.0f}%)"
            + (", sem alterar quem lidera o ranking — a concentração estava "
               "escondida numa posição pulverizada em muitas linhas.</p>"
               if abs(var) > 1 else
               ", quase sem efeito: as contas consolidadas são pequenas "
               "demais para pesar.</p>"))

    return f"""
<div class="legenda-hhi">
  <h3>O que é o HHI</h3>
  <p><b>Índice Herfindahl-Hirschman.</b> Soma o quadrado da participação de
     cada cotista no PL e multiplica por 10.000:</p>
  <p class="formula">HHI = Σ (participação de cada cotista)² × 10.000</p>
  <p>Vai de perto de <b>0</b> (passivo infinitamente pulverizado) a
     <b>10.000</b> (um único cotista com 100%).</p>

  <h3>Como ler, neste fundo</h3>
  <p>A coluna <b>Equivale a</b> é <code>10.000 ÷ HHI</code>: o número de
     cotistas <b>de tamanho igual</b> que produziriam a mesma concentração.
     Aqui, o HHI por documento é <b>{m_doc['hhi']:,.0f}</b> — o passivo
     concentra como se tivesse <b>{eq_doc:,.1f} cotistas iguais</b>, mesmo
     tendo <b>{m_doc['n']}</b>. É assim que o índice trata a cauda longa:
     posição pequena quase não pesa, porque a participação entra ao quadrado.
     O maior sozinho tem {maior:.2f}% do PL.</p>

  <h3>Por que ele, e não “os 3 maiores”</h3>
  <p>O percentual dos maiores olha só o topo. O HHI usa a distribuição inteira
     e dá peso maior a quem é grande. Dois fundos podem ter o mesmo percentual
     nos três maiores e riscos diferentes — um com o quarto colocado em 10%,
     outro com o quarto em 0,5%. O top&nbsp;3 não separa os dois; o HHI
     separa.</p>
  {efeito}

  <h3 class="ressalva">Uma ressalva sobre limiares</h3>
  <p>É comum ver “abaixo de 1.500 é pulverizado, acima de 2.500 é
     concentrado”. Esses cortes vêm do <b>antitruste</b>, onde o HHI mede
     participação de mercado de empresas. <b>Não existe régua regulatória
     equivalente para passivo de fundo</b>, e por esses cortes emprestados
     este fundo seria “pulverizado” — mesmo com um cotista de {maior:.2f}%.</p>
  <p>Use o número <b>comparativamente</b>: entre fundos, entre chaves de
     agregação, e do mesmo fundo ao longo do tempo. O valor absoluto, isolado,
     não diz muito.</p>
</div>"""


def tabela_top(p, col, pl, n=15) -> str:
    g = (p.groupby(col)
         .agg(saldo=("saldo", "sum"), contas=("cotista", "nunique"),
              classes=("classe", "nunique"))
         .sort_values("saldo", ascending=False).head(n))
    g["acum"] = g["saldo"].cumsum() / pl * 100
    return ('<table><thead><tr><th>Cotista</th><th>Contas</th><th>Classes</th>'
            '<th>Saldo (R$)</th><th>% do PL</th><th>Acum.</th></tr></thead>'
            '<tbody>' + "".join(
                f'<tr><td>{_e(str(i)[:46])}</td><td>{int(r["contas"])}</td>'
                f'<td>{int(r["classes"])}</td><td>{r["saldo"]:,.2f}</td>'
                f'<td><b>{r["saldo"] / pl * 100:.2f}%</b></td>'
                f'<td>{r["acum"]:.1f}%</td></tr>'
                for i, r in g.iterrows()) + "</tbody></table>")


def tabela_colapso(p) -> str:
    """Documentos que aparecem sob mais de um nome -- a origem da diferenca."""
    n = p.groupby("doc")["cotista"].nunique()
    mult = n[n > 1].sort_values(ascending=False)
    if mult.empty:
        return '<p class="nota">Nenhum documento com mais de um nome.</p>'
    pl = float(p["saldo"].sum())
    linhas = []
    for doc in mult.index:
        g = p[p["doc"] == doc]
        linhas.append(
            f'<tr><td><code>{_e(doc)}</code></td>'
            f'<td>{_e(_rotulo_doc(g)[:40])}</td><td>{int(mult[doc])}</td>'
            f'<td>{g["saldo"].sum():,.2f}</td>'
            f'<td><b>{g["saldo"].sum() / pl * 100:.2f}%</b></td></tr>')
    return ('<table><thead><tr><th>Documento</th><th>Nome consolidado</th>'
            '<th>Nomes</th><th>Saldo (R$)</th><th>% do PL</th></tr></thead>'
            '<tbody>' + "".join(linhas) + "</tbody></table>")


def marca() -> str:
    """O logo embutido como data URI. Sem o arquivo, sai o nome em texto."""
    if not gf.LOGO.exists():
        return '<div class="titulo">MULTIPLICA</div>'
    dados = base64.b64encode(gf.LOGO.read_bytes()).decode("ascii")
    return f'<img src="data:image/png;base64,{dados}" alt="Multiplica">'


def montar(nome_fundo, data, p, defs, sai) -> str:
    pl = float(p["saldo"].sum())
    m_nome = metricas(p, "cotista")
    m_doc = metricas(p, "por_documento")

    kpis = [
        ("Passivo (PL)", f"R$ {pl / 1e6:,.0f} mi", "posição do fundo"),
        ("Cotistas por nome", f"{m_nome['n']}", "como o relatório conta hoje"),
        ("Cotistas por documento", f"{m_doc['n']}",
         f"{m_nome['n'] - m_doc['n']} a menos — contas do mesmo CPF/CNPJ"),
        ("Maior contraparte", f"{m_doc['maior'] / pl * 100:.2f}%",
         f"contra {m_nome['maior'] / pl * 100:.2f}% pela contagem por nome"),
    ]

    # Grafo com o passivo agregado por documento: reaproveita o desenho do
    # relatorio, trocando so a coluna que identifica o cotista.
    entra_doc = p.rename(columns={"cotista": "_nome_orig",
                                  "por_documento": "cotista"}).copy()
    entra_doc["grupo"] = entra_doc["classe"].map(gf.grupo)
    svg_doc = gf.desenhar_entra(nome_fundo, entra_doc, topo=12)

    entra_nome = p.copy()
    entra_nome["grupo"] = entra_nome["classe"].map(gf.grupo)
    svg_nome = gf.desenhar_entra(nome_fundo, entra_nome, topo=12)

    secoes = []
    for chave, (rot, col, desc) in defs.items():
        m = metricas(p, col)
        secoes.append(f"""
<h2>{_e(rot)}</h2>
<p class="sub">{_e(desc)} — {m['n']} cotista(s), HHI {m['hhi']:,.0f}.</p>
<div class="cartao">{tabela_top(p, col, pl)}</div>""")

    hoje = dt.date.today()
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(nome_fundo)} — concentração do passivo</title>
<style>
:root {{ color-scheme: light;
{gf._tokens("claro")}}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  color-scheme: dark;
{gf._tokens("escuro")}}} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--plano); color: var(--tinta); padding-bottom: 64px; }}
.limite {{ max-width: 1160px; margin: 0 auto; padding: 0 24px; }}
.marca {{ background: {gf.NAVY}; padding: 26px 0 28px; margin-bottom: 8px; }}
.marca .limite {{ display: flex; align-items: center;
  justify-content: space-between; gap: 20px; flex-wrap: wrap; }}
.marca img {{ height: 84px; width: auto; max-width: 100%; display: block; }}
@media (max-width: 900px) {{ .marca img {{ height: 60px; }} }}
.marca .titulo {{ color: #fff; font-size: 19px; font-weight: 640; }}
.marca .quando {{ color: {gf.AZUL_PALIDO}; font-size: 13px; margin-top: 2px; }}
.marca .quando b {{ color: {gf.CIANO}; font-weight: 620; }}
h1 {{ font-size: 22px; margin: 0 0 4px; font-weight: 650; }}
h2 {{ font-size: 15px; margin: 36px 0 4px; font-weight: 650; }}
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
td:nth-child(n+2) {{ text-align: right; font-variant-numeric: tabular-nums; }}
td:first-child {{ text-align: left; }}
code {{ font-size: 11.5px; color: var(--tinta2); }}
.aviso {{ background: var(--superficie); border: 1px solid var(--borda);
  border-left: 3px solid var(--atencao); border-radius: 8px;
  padding: 12px 16px; margin-top: 14px; font-size: 13px; }}
.aviso b {{ color: var(--atencao); }}
.rodape {{ color: var(--tinta3); font-size: 11.5px; margin-top: 40px;
  border-top: 1px solid var(--borda); padding-top: 14px; }}
/* Legenda do HHI: em duas colunas quando cabe, para nao virar um paredao de
   texto embaixo da tabela. */
.legenda-hhi {{ background: var(--superficie); border: 1px solid var(--borda);
  border-radius: 10px; padding: 16px 20px; margin-top: 12px;
  columns: 2; column-gap: 32px; }}
@media (max-width: 860px) {{ .legenda-hhi {{ columns: 1; }} }}
.legenda-hhi h3 {{ font-size: 12.5px; font-weight: 650; margin: 0 0 4px;
  color: var(--tinta); break-after: avoid; }}
.legenda-hhi h3:not(:first-child) {{ margin-top: 14px; }}
.legenda-hhi h3.ressalva {{ color: var(--atencao); }}
.legenda-hhi p {{ font-size: 12.5px; color: var(--tinta2); margin: 0 0 8px;
  line-height: 1.5; }}
.legenda-hhi .formula {{ font-family: ui-monospace, "Cascadia Mono", Consolas,
  monospace; font-size: 12px; color: var(--tinta); background: var(--plano);
  border: 1px solid var(--borda); border-radius: 6px; padding: 8px 10px; }}
.legenda-hhi code {{ font-size: 11.5px; }}
{gf.ESTILO}
</style></head><body>
<header class="marca"><div class="limite">
{marca()}
<div style="text-align:right">
  <div class="titulo">{_e(nome_fundo)} — concentração do passivo</div>
  <div class="quando">Análise separada &middot; posição
     <b>{dt.date.fromisoformat(data):%d/%m/%Y}</b></div>
</div></div></header>

<main class="limite">
<h1>A mesma posição, três contagens</h1>
<p class="sub">Quem é a contraparte depende da chave usada para agregar. Esta
   página não altera o relatório de liquidez — serve para decidir se a régua
   deve mudar.</p>
<div class="kpis">{"".join(
  f'<div class="kpi"><div class="r">{_e(r)}</div><div class="v">{_e(v)}</div>'
  f'<div class="n">{_e(n)}</div></div>' for r, v, n in kpis)}</div>

<h2>Concentração por chave</h2>
<p class="nota">A mesma posição, contada de três formas. Veja a legenda do HHI
   abaixo da tabela.</p>
<div class="cartao">{tabela_comparativa(p, defs)}</div>
{legenda_hhi(p)}

<h2>De onde vem a diferença</h2>
<p class="sub">Documentos que aparecem no cadastro sob mais de um nome.</p>
<div class="cartao">{tabela_colapso(p)}</div>

<div class="aviso">
  <b>Documento não é grupo econômico.</b> Duas empresas do mesmo controlador
  têm CNPJ diferente e continuam separadas aqui. Agregar por grupo exige um
  de-para de controle que não existe na base. Esta página dimensiona o
  problema; construir o de-para é outra decisão.
</div>

<h2>O grafo, agregado por documento</h2>
<p class="nota">Mesmo desenho do relatório, trocando só a chave do cotista.
   Compare com o de baixo.</p>
<div class="cartao">{svg_doc}</div>

<h2>O grafo, agregado por nome — como está hoje</h2>
<div class="cartao">{svg_nome}</div>

{"".join(secoes)}

<div class="rodape">
  Gerado em {hoje:%d/%m/%Y} de <i>MUL_GLD_PASSIVO_CONSOLIDADO</i>
  e <i>TB_PASSIVO_DISTRIBUIDORES</i>, posição de
  {dt.date.fromisoformat(data):%d/%m/%Y}. O distribuidor vem do de-para pelo
  documento do cotista; quem não está lá aparece como “(sem distribuidor)”.
</div>
</main></body></html>
"""


if __name__ == "__main__":
    cnpj = sys.argv[1] if len(sys.argv) > 1 else "23.216.398/0001-01"
    data = sys.argv[2] if len(sys.argv) > 2 else None

    dados = gf.carregar_todos(data)
    data = dados["data"]
    from liquidez import db

    n = db.consultar("SELECT TOP 1 nmFundoInterno AS n "
                     "FROM dbo.HOD_TB_CADASTRO_FUNDOS WHERE CNPJFundo = ?",
                     (cnpj,))
    nome = n["n"].iloc[0] if not n.empty else cnpj

    p = db.consultar(SQL, (data, cnpj))
    if p.empty:
        sys.exit(f"sem passivo para {cnpj} em {data}")
    defs, p = visoes(p)
    sai = gf.recortar(cnpj, dados)["sai"]

    saida = AQUI / f"passivo_{gf._dig(cnpj)}.html"
    saida.write_text(montar(nome, data, p, defs, sai), encoding="utf-8")

    pl = p["saldo"].sum()
    print(f"fundo ......... {nome}")
    print(f"posicao ....... {data}   PL R$ {pl:,.2f}")
    for chave, (rot, col, _d) in defs.items():
        m = metricas(p, col)
        print(f"  {rot:<26} {m['n']:>4} cotistas   maior "
              f"{m['maior'] / pl * 100:>6.2f}%   HHI {m['hhi']:>7,.0f}")
    print(f"gerado ........ {saida}  ({saida.stat().st_size / 1024:,.0f} KB)")
