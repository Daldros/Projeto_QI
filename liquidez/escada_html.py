"""Escada de liquidez de UM fundo numa data, com horizonte e faixas escolhidos, em HTML.

    python -m liquidez escada-html --fundo "AMADEUS FIDC" --data 2026-09-30 \\
        --saida dados\\amadeus_2026-09-30 --horizonte 21 --limite 90

O relatorio por fundo do painel usa o cc do cadastro, as faixas fixas do
painel (0, 1-7, 8-15, 16-29, 30-59, 60-89, 90+) e mostra sempre a data mais
recente de `bi/`. Esta pagina responde a outra pergunta: "com resgate em D+H, o
que o fundo tem e quando vira caixa, ate N dias, e quanto esta vencido?".

De onde vem cada pedaco -- as MESMAS regras do pipeline, so com outra janela:

- Caixa em D+H: `_processar_fundo` com `prazo_cot = H` (relatorio e indicadores
  recalculados com H no lugar do cc). Ao lado, o mesmo calculo no cc do fundo.
- Escada: a matriz (`analisar_carteira`) e o estoque (`exposicao_estoque`)
  refeitos ate `limite` dias, para as faixas alem do cc existirem; o DC vem do
  fluxo projetado (`bi/fato_fluxo` da data), parcela a parcela; cotas de fundo
  pelo prazo de resgate da tabela de prazos; CPR e tesouraria no dia 0.
- Vencidos (coluna propria, fora da escada): ativo vencido da carteira, DC de
  emissor vencido no fluxo, recebivel vencido do estoque e recebivel A VENCER de
  sacado que tem titulo vencido (marca de carrego -- regra mantida pela area em
  05/11/2026).

So agregados: nenhum cotista, sacado ou documento entra na pagina.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# Escada pedida pela area em 05/11/2026: liquidez imediata (D+0), ate 15, 30,
# 60, 90, 180 e 360 dias, e acima de 360.
CORTES_PADRAO = (0, 15, 30, 60, 90, 180, 360)
LIMITE_PADRAO = 360


CAT_DC = "Direitos creditórios (fluxo)"
CAT_DC_SEM_FLUXO = "Direitos creditórios (sem fluxo)"
CAT_ESTOQUE = "Recebíveis do estoque"
CAT_COTAS = "Cotas de fundos"
CAT_CAIXA = "CPR e tesouraria"
CAT_OUTROS = "Renda fixa e outros"
CATEGORIAS = (CAT_DC, CAT_DC_SEM_FLUXO, CAT_ESTOQUE, CAT_COTAS, CAT_OUTROS, CAT_CAIXA)
# Direitos creditorios: o DC da carteira (com e sem fluxo) e os recebiveis do
# estoque do fundo investido. E a coluna "dos quais direitos creditorios".
CATS_DC = (CAT_DC, CAT_DC_SEM_FLUXO, CAT_ESTOQUE)

# O DC de emissor vencido esta na linha "Vencidos" da matriz (V_CARTEIRA); as
# parcelas dele no fluxo sao descartadas para nao contar duas vezes.
V_CARTEIRA = "Carteira: ativo vencido"
V_ESTOQUE = "Estoque: recebível vencido"
V_CARREGO = "Estoque: a vencer de sacado com vencido (carrego)"
VENCIDOS = (V_CARTEIRA, V_ESTOQUE, V_CARREGO)


@dataclass
class Escada:
    fundo: str
    cnpj: str
    data: str
    horizonte: int
    cc: int | None
    limite: int
    cortes: tuple[int, ...]
    pl: float
    entradas: pd.DataFrame            # categoria, dias, valor  (escada)
    vencidos: pd.DataFrame            # componente, dias, valor
    fora: dict = field(default_factory=dict)     # no PL, fora da escada e dos vencidos
    cotas_substituidas: float = 0.0   # cotas A VENCER/VENCIDA trocadas pelo estoque
    estoque_total: float = 0.0
    caixa_h: dict = field(default_factory=dict)
    caixa_cc: dict = field(default_factory=dict)
    relatorio_h: pd.DataFrame = field(default_factory=pd.DataFrame)

    # --- faixas --------------------------------------------------------------
    def rotulos(self) -> list[str]:
        """Faixa a faixa (sem acumular): 'Imediata', '1–15 d', ..., '> 360 d'."""
        r, ini = [], 0
        for fim in self.cortes:
            r.append("Imediata" if fim == 0 else (f"{ini} d" if ini == fim else f"{ini}–{fim} d"))
            ini = fim + 1
        return r + [f"> {self.cortes[-1]} d"]

    def rotulos_ate(self) -> list[str]:
        """Acumulado: 'Liquidez imediata', 'Até 15 dias', ..., 'Acima de 360 dias'."""
        r = ["Liquidez imediata" if fim == 0 else f"Até {fim} dias" for fim in self.cortes]
        return r + [f"Acima de {self.cortes[-1]} dias"]

    def faixa_de(self, dias) -> int | None:
        if dias is None or pd.isna(dias):
            return None
        d = max(float(dias), 0.0)
        for i, fim in enumerate(self.cortes):
            if d <= fim:
                return i
        return len(self.cortes)

    def por_faixa(self, df: pd.DataFrame, chave: str) -> pd.DataFrame:
        """chave x faixa (+ 'sem prazo'), em R$."""
        cols = self.rotulos() + ["sem prazo"]
        if df.empty:
            return pd.DataFrame(columns=cols)
        indices = [self.faixa_de(d) for d in df["dias"]]
        f = df.assign(_r=[cols[i] if i is not None else "sem prazo" for i in indices])
        t = f.pivot_table(index=chave, columns="_r", values="valor", aggfunc="sum", fill_value=0.0)
        return t.reindex(columns=cols, fill_value=0.0)

    def ate(self, dias: int) -> float:
        d = pd.to_numeric(self.entradas["dias"], errors="coerce")
        return float(self.entradas.loc[d.notna() & (d <= dias), "valor"].sum())


# --- calculo -------------------------------------------------------------------

def _resumo_caixa(dados: dict) -> dict:
    ind = dados["indicadores"]
    return {
        "prazo": dados["prazo_cotizacao"],
        "caixa": ind.caixa_potencial_mil * 1000,
        "caixa_pl": ind.caixa_pl,
        "cx_cotista": ind.cx_cotista, "enq_cotista": ind.enq_cotista,
        "cx_gdp": ind.cx_gdp, "enq_gdp": ind.enq_gdp,
        "cx_distribuidor": ind.cx_distribuidor, "enq_distribuidor": ind.enq_distribuidor,
        "caixa_resgate": ind.caixa_resgate, "enq_rco": ind.enq_rco,
        "resgate_max": ind.resgate_max_mil * 1000,
    }


def _fluxo_da_data(destino_bi: Path, data: str, cnpj: str) -> pd.DataFrame:
    from . import fatos as ft
    from .cvm import so_digitos

    caminho = ft.caminho_particao("fato_fluxo", data, destino_bi)
    if not caminho.exists():
        return pd.DataFrame()
    todos = pd.read_parquet(caminho)
    return todos[todos["cnpj_fundo"].map(so_digitos) == so_digitos(cnpj)].copy()


def calcular(cadastro, fundo, saida: Path, data: str, horizonte: int,
             limite: int = LIMITE_PADRAO, cortes: tuple[int, ...] = CORTES_PADRAO,
             destino_bi: Path = Path("bi")) -> Escada:
    from .analise import (
        LINHA_EXPOSICAO, LINHA_VENCIDOS, VALOR_CHEIO,
        _estoque_proprio, _produtos_de_cotas, analisar_carteira,
    )
    from .cli import _processar_fundo
    from .cotas import MapaCotas, ResolvedorCotas
    from .cronograma import normalizar_codigo
    from .fluxo import COL_VENCIDO_FLUXO
    from .prazos import TabelaPrazos
    from .tratamentos import (
        COL_ATIVO_VENCIDO, COL_ATIVOS_EXCLUIDOS, COL_BASE_ORIGEM, COL_QTDE_DIAS, PRODUTO_COMPENSADO,
    )

    cortes = tuple(sorted(int(c) for c in cortes))
    limite = max(int(limite), cortes[-1])
    h = _processar_fundo(cadastro, fundo, saida, data, prazo_cot=horizonte)
    ref = (h if fundo.cc == horizonte
           else _processar_fundo(cadastro, fundo, saida, data, prazo_cot=fundo.cc))
    res = h["tratamento"]
    data = str(h["data_posicao"])[:10]

    entradas, vencidos = [], []
    fora = {"DC COMPENSADO (pares que se anulam)": 0.0}
    substituidas = estoque_total = 0.0

    def entra(cat, dias, valor):
        if valor:
            entradas.append({"categoria": cat, "dias": dias, "valor": float(valor)})

    def vence(comp, dias, valor):
        if valor:
            vencidos.append({"componente": comp, "dias": dias, "valor": float(valor)})

    # --- fluxo do DC ------------------------------------------------------------
    fluxo = _fluxo_da_data(Path(destino_bi), data, fundo.cnpj)
    tem_fluxo = not fluxo.empty
    if tem_fluxo:
        marcado = (pd.to_numeric(fluxo["vl_amortizacao"], errors="coerce").fillna(0.0)
                   + pd.to_numeric(fluxo.get("vl_acrual", 0.0), errors="coerce").fillna(0.0))
        venc = (fluxo[COL_VENCIDO_FLUXO].fillna(False).astype(bool)
                if COL_VENCIDO_FLUXO in fluxo.columns else pd.Series(False, index=fluxo.index))
        for d, v, x in zip(pd.to_numeric(fluxo["qt_dias"], errors="coerce"), marcado, venc):
            if not x:
                entra(CAT_DC, d, v)
        no_fluxo = set(fluxo["codigo_op"].astype(str))
        car = res.carteira
        dc = car[car[COL_BASE_ORIGEM].fillna("").astype(str).str.strip().str.upper() == "DC"]
        sem = dc[~dc["Codigo_Op"].map(normalizar_codigo).astype(str).isin(no_fluxo)]
        for _, r in sem.iterrows():
            # vencido e excluido seguem a matriz: vencido vai pela linha
            # "Vencidos" dela, excluido nao entra em nada.
            if str(r.get(COL_ATIVO_VENCIDO, "")).upper() == "SIM" \
                    or str(r.get(COL_ATIVOS_EXCLUIDOS, "")).upper() == "SIM":
                continue
            entra(CAT_DC_SEM_FLUXO, r[COL_QTDE_DIAS], pd.to_numeric(r["Valor_Liquido"], errors="coerce"))

    # --- matriz da carteira ate o limite ----------------------------------------
    matriz = analisar_carteira(res.carteira, limite, res.regra)
    de_cotas = _produtos_de_cotas(fundo)
    alto = f">{limite}"
    for prod in [c for c in matriz.columns if c != LINHA_EXPOSICAO]:
        t = str(prod).strip().upper()
        col = pd.to_numeric(matriz[prod], errors="coerce").fillna(0.0)
        if t in de_cotas:
            continue
        if t == PRODUTO_COMPENSADO.upper():
            fora["DC COMPENSADO (pares que se anulam)"] += float(col[LINHA_EXPOSICAO])
            continue
        if t in VALOR_CHEIO:
            entra(CAT_CAIXA, 0, col[LINHA_EXPOSICAO])
            continue
        vence(V_CARTEIRA, None, col.get(LINHA_VENCIDOS, 0.0))
        if t == "DC" and tem_fluxo:
            continue                       # o DC vivo ja entrou pelo fluxo
        cat = CAT_DC_SEM_FLUXO if t == "DC" else CAT_OUTROS
        dias = [d for d in matriz.index if isinstance(d, int)]
        for d in dias:
            entra(cat, d, col[d])
        entra(cat, limite + 1, col.get(alto, 0.0))
        resto = float(col[LINHA_EXPOSICAO]) - float(col[dias].sum()) - float(col.get(alto, 0.0)) \
            - float(col.get(LINHA_VENCIDOS, 0.0))
        if abs(resto) > 0.005:
            entra(cat, None, resto)

    # --- estoque, linha a linha (mesmos cortes de `exposicao_estoque`) ---------
    bd = res.bd
    if bd is not None and not bd.empty:
        bd = bd[~_estoque_proprio(bd, res.carteira)]
        valor = pd.to_numeric(bd["Valor_Presente"], errors="coerce").fillna(0.0)
        status = bd["Status_Recebivel"].fillna("").astype(str).str.strip().str.upper()
        carrego = bd["Vencidos"].astype(str).str.upper() if "Vencidos" in bd.columns \
            else pd.Series("", index=bd.index)
        dias = pd.to_numeric(bd["Prazo_Atual_Dias_Corridos"], errors="coerce")
        estoque_total = float(valor.sum())
        for v, s, c, d in zip(valor, status, carrego, dias):
            if s == "VENCIDO":
                vence(V_ESTOQUE, d, v)
            elif c == "SIM":
                vence(V_CARREGO, d, v)
            elif c == "NAO":
                entra(CAT_ESTOQUE, None if pd.isna(d) else d, v)

    # --- cotas de fundo pelo prazo de resgate -----------------------------------
    resolvedor = ResolvedorCotas(TabelaPrazos.carregar(), MapaCotas.carregar(), cadastro)
    for _, r in h["outros_fundos"].iterrows():
        cod, nome = str(r["cod_fundo"]).strip().upper(), str(r["fundo"]).strip().upper()
        exp = float(r["exposicao"])
        # Mesma leitura de `montar_relatorio`: a cota A VENCER do fundo investido
        # e medida pelo estoque dele, e a VENCIDA tambem -- entram pelo estoque.
        if cod.endswith("AV") or nome.endswith(("A VENCER", "A VENC")) \
                or cod.endswith("VEN") or nome.endswith(("VENCIDOS", "VENCIDO", "VENCID")):
            substituidas += exp
        else:
            entra(CAT_COTAS, resolvedor.prazo(r["fundo"]), exp)

    rel = h["relatorio"].copy()
    return Escada(
        fundo=fundo.nome_interno, cnpj=fundo.cnpj, data=data, horizonte=int(horizonte),
        cc=fundo.cc, limite=limite, cortes=cortes, pl=h["pl_mil"] * 1000,
        entradas=pd.DataFrame(entradas, columns=["categoria", "dias", "valor"]),
        vencidos=pd.DataFrame(vencidos, columns=["componente", "dias", "valor"]),
        fora=fora, cotas_substituidas=substituidas, estoque_total=estoque_total,
        caixa_h=_resumo_caixa(h), caixa_cc=_resumo_caixa(ref),
        relatorio_h=rel)


# --- texto -----------------------------------------------------------------------

def _brl(v, casas=2) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    t = f"{float(v):,.{casas}f}"
    return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _mi(v) -> str:
    return _brl(float(v) / 1e6, 2)


def _pct(v) -> str:
    return "—" if v is None or pd.isna(v) else _brl(float(v) * 100, 2) + "%"


def conciliacao(e: Escada, venc_total: float) -> str:
    soma = float(e.entradas["valor"].sum()) + float(venc_total) + sum(e.fora.values())
    esperado = e.pl - e.cotas_substituidas + e.estoque_total
    return (f"Conciliacao: escada R$ {_brl(e.entradas['valor'].sum())} + vencidos R$ {_brl(venc_total)}"
            f" + DC compensado R$ {_brl(sum(e.fora.values()))} = R$ {_brl(soma)}. Esperado: PL R$ {_brl(e.pl)}"
            f" - cotas A VENCER/VENCIDA R$ {_brl(e.cotas_substituidas)} + estoque que as substitui"
            f" R$ {_brl(e.estoque_total)} = R$ {_brl(esperado)}. Diferenca R$ {_brl(soma - esperado)}.")


def escada_ate(e: Escada, tab: pd.DataFrame, venc_total: float) -> pd.DataFrame:
    """Escada no formato pedido: 'Liquidez imediata', 'Ate 15 dias', ... acumulados,
    'Acima de N dias' com o que sobra, e Vencidos -- total e dos quais DC."""
    faixas = [c for c in tab.columns if c != "sem prazo"]
    total = tab[faixas].sum()
    dc = tab.loc[[c for c in CATS_DC if c in tab.index], faixas].sum()
    linhas = []
    for i, rot in enumerate(e.rotulos_ate()):
        ultimo = i == len(faixas) - 1
        v = float(total.iloc[i]) if ultimo else float(total.iloc[: i + 1].sum())
        d = float(dc.iloc[i]) if ultimo else float(dc.iloc[: i + 1].sum())
        linhas.append({"Prazo": rot, "Valor": v, "% do PL": v / e.pl if e.pl else None,
                       "Dos quais direitos creditórios": d})
    sem = float(tab["sem prazo"].sum()) if "sem prazo" in tab.columns else 0.0
    if abs(sem) > 0.005:
        linhas.append({"Prazo": "Sem prazo", "Valor": sem, "% do PL": sem / e.pl if e.pl else None,
                       "Dos quais direitos creditórios": float(tab.loc[[c for c in CATS_DC if c in tab.index],
                                                                       "sem prazo"].sum())})
    linhas.append({"Prazo": "Vencidos", "Valor": venc_total,
                   "% do PL": venc_total / e.pl if e.pl else None,
                   "Dos quais direitos creditórios": venc_total})
    return pd.DataFrame(linhas)


def resumo_texto(e: Escada) -> str:
    """O mesmo conteudo da pagina, para a saida do terminal (sem cotista)."""
    tab = e.por_faixa(e.entradas, "categoria")
    linhas = [f"{e.fundo} | {e.cnpj} | posicao {e.data} | H = D+{e.horizonte} | cc {e.cc}"
              f" | escada ate {e.limite} dias | PL R$ {_brl(e.pl)}"]
    for rot, cx in ((f"D+{e.horizonte} (H)", e.caixa_h), (f"D+{e.cc} (cc)", e.caixa_cc)):
        linhas.append(f"Caixa Potencial em {rot:<12} R$ {_brl(cx['caixa']):>18}  ({_pct(cx['caixa_pl'])} do PL)"
                      f" | Cx/Cotista {_pct(cx['cx_cotista'])} | Cx/GDP {_pct(cx['cx_gdp'])}"
                      f" | Caixa/Resgate {_pct(cx['caixa_resgate'])}")
    linhas.append(f"Escada somada ate D+{e.horizonte}: R$ {_brl(e.ate(e.horizonte))}")
    linhas.append("")
    vt0 = float(e.vencidos["valor"].sum()) if not e.vencidos.empty else 0.0
    linhas.append(f"{'ESCADA (acumulada)':<24}{'VALOR':>20}{'% PL':>10}{'DOS QUAIS DC':>20}")
    for _, r in escada_ate(e, tab, vt0).iterrows():
        linhas.append(f"{r['Prazo']:<24}{_brl(r['Valor']):>20}{_pct(r['% do PL']):>10}"
                      f"{_brl(r['Dos quais direitos creditórios']):>20}")
    linhas.append("")
    cab = f"{'':<32}" + "".join(f"{c:>16}" for c in tab.columns) + f"{'TOTAL':>16}"
    linhas.append(cab)
    for cat in [c for c in CATEGORIAS if c in tab.index]:
        lin = tab.loc[cat]
        linhas.append(f"{cat:<32}" + "".join(f"{_brl(v):>16}" for v in lin) + f"{_brl(lin.sum()):>16}")
    tot = tab.sum()
    linhas.append(f"{'TOTAL':<32}" + "".join(f"{_brl(v):>16}" for v in tot) + f"{_brl(tot.sum()):>16}")
    linhas.append("")
    vt = e.vencidos.groupby("componente")["valor"].sum() if not e.vencidos.empty else pd.Series(dtype=float)
    linhas.append(f"VENCIDOS (fora da escada): R$ {_brl(vt.sum())}")
    for comp in VENCIDOS:
        if comp in vt.index:
            linhas.append(f"   {comp:<52} R$ {_brl(vt[comp]):>18}")
    linhas.append(conciliacao(e, vt.sum()))
    return "\n".join(linhas)


# --- pagina --------------------------------------------------------------------

CORES_CAT = {
    CAT_DC: "var(--serie)", CAT_DC_SEM_FLUXO: "var(--serie2)", CAT_ESTOQUE: "var(--ciano)",
    CAT_COTAS: "var(--neutro)", CAT_OUTROS: "var(--azul-claro)", CAT_CAIXA: "var(--bom)",
}


def _passo_redondo(amplitude: float, marcas: int = 5) -> float:
    """Passo de grade 1/2/2,5/5 x 10^n, para o eixo ler numero redondo."""
    import math

    if amplitude <= 0:
        return 1.0
    bruto = amplitude / marcas
    ordem = 10 ** math.floor(math.log10(bruto))
    for m in (1, 2, 2.5, 5, 10):
        if m * ordem >= bruto:
            return m * ordem
    return 10 * ordem


def _acumulada(tab: pd.DataFrame) -> pd.DataFrame:
    """Categoria x faixa ACUMULADA ('ate N'); a ultima faixa ('acima de') fica como e."""
    faixas = [c for c in tab.columns if c != "sem prazo"]
    t = tab[faixas].copy()
    acum = t.iloc[:, :-1].cumsum(axis=1)
    acum[faixas[-1]] = t[faixas[-1]]
    return acum


def _svg(e: Escada, tab: pd.DataFrame, venc_total: float) -> str:
    """Barras empilhadas ACUMULADAS ('ate N dias'), 'acima de' e Vencidos a parte.

    O que e negativo (CPR a pagar) desce abaixo do zero.
    """
    import math

    tab = _acumulada(tab)
    curtos = ["Imediata" if fim == 0 else f"até {fim} d" for fim in e.cortes] + [f"> {e.cortes[-1]} d"]
    rot = list(tab.columns)
    nomes = dict(zip(rot, curtos))
    titulos = dict(zip(rot, e.rotulos_ate()))
    colunas = rot + ["Vencidos"]
    pos = {c: float(tab[c].clip(lower=0).sum()) for c in rot}
    neg = {c: float(tab[c].clip(upper=0).sum()) for c in rot}
    pos["Vencidos"], neg["Vencidos"] = max(venc_total, 0.0), min(venc_total, 0.0)
    passo_v = _passo_redondo(max(pos.values()) - min(neg.values()))
    teto = math.ceil(max(pos.values()) / passo_v) * passo_v or passo_v
    piso = math.floor(min(neg.values()) / passo_v) * passo_v
    larg, alt, esq, topo, baixo = 760, 350, 64, 28, 306
    passo = (larg - esq - 12) / len(colunas)
    barra = passo * 0.62

    def y(v):
        return baixo - (v - piso) / (teto - piso) * (baixo - topo)

    partes = [f'<svg viewBox="0 0 {larg} {alt}" role="img" class="grafico" '
              f'aria-label="Escada de liquidez por faixa de prazo, em R$ milhões">']
    v = piso
    while v <= teto + passo_v / 2:
        yy = y(v)
        partes.append(f'<line x1="{esq}" x2="{larg - 8}" y1="{yy:.1f}" y2="{yy:.1f}" class="grade"/>'
                      f'<text x="{esq - 6}" y="{yy + 4:.1f}" class="eixo" text-anchor="end">{_mi(v)}</text>')
        v += passo_v
    for i, c in enumerate(colunas):
        x = esq + i * passo + (passo - barra) / 2
        if c == "Vencidos":
            y0, y1 = y(0), y(venc_total)
            partes.append(f'<rect x="{x:.1f}" y="{min(y0, y1):.1f}" width="{barra:.1f}" '
                          f'height="{abs(y0 - y1):.1f}" class="venc"><title>Vencidos: R$ '
                          f'{_brl(venc_total)}</title></rect>')
        else:
            acima = abaixo = 0.0
            for cat in CATEGORIAS:
                if cat not in tab.index:
                    continue
                val = float(tab.loc[cat, c])
                if val == 0:
                    continue
                if val > 0:
                    y0, y1 = y(acima), y(acima + val)
                    acima += val
                else:
                    y1, y0 = y(abaixo), y(abaixo + val)
                    abaixo += val
                partes.append(f'<rect x="{x:.1f}" y="{min(y0, y1):.1f}" width="{barra:.1f}" '
                              f'height="{abs(y0 - y1):.1f}" style="fill:{CORES_CAT[cat]}">'
                              f'<title>{html.escape(titulos[c])} · {html.escape(cat)}: R$ {_brl(val)}</title></rect>')
        total = float(tab[c].sum()) if c != "Vencidos" else venc_total
        partes.append(f'<text x="{x + barra / 2:.1f}" y="{y(pos[c]) - 6:.1f}" class="valor" '
                      f'text-anchor="middle">{_mi(total)}</text>')
        partes.append(f'<text x="{x + barra / 2:.1f}" y="{baixo + 20}" class="eixo" '
                      f'text-anchor="middle">{html.escape(nomes.get(c, c))}</text>')
    partes.append(f'<line x1="{esq}" x2="{larg - 8}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="base"/></svg>')
    return "".join(partes)


def _tabela(df: pd.DataFrame, cabecalho: list[str], formato=None, classe_ultima=False) -> str:
    formato = formato or (lambda c, v: html.escape(str(v)) if not isinstance(v, (int, float)) else _brl(v))
    th = "".join(f"<th>{html.escape(c)}</th>" for c in cabecalho)
    corpo = []
    for i, (_, r) in enumerate(df.iterrows()):
        cls = ' class="total"' if classe_ultima and i == len(df) - 1 else ""
        corpo.append(f"<tr{cls}>" + "".join(f"<td>{formato(c, r[c])}</td>" for c in df.columns) + "</tr>")
    return f'<div class="rolagem"><table><thead><tr>{th}</tr></thead><tbody>{"".join(corpo)}</tbody></table></div>'


def _kpi(titulo, valor, sub, classe="") -> str:
    return (f'<div class="kpi {classe}"><div class="kpi-t">{html.escape(titulo)}</div>'
            f'<div class="kpi-v">{valor}</div><div class="kpi-s">{sub}</div></div>')


def _semaforo(v, limite=0.15) -> str:
    if v is None or pd.isna(v):
        return ""
    return "ok" if v >= limite else "ruim"


def montar_html(e: Escada) -> str:
    from .painel import CORES, _logo_embutido

    tab = e.por_faixa(e.entradas, "categoria")
    tab = tab.loc[[c for c in CATEGORIAS if c in tab.index]]
    vt = e.vencidos.groupby("componente")["valor"].sum() if not e.vencidos.empty else pd.Series(dtype=float)
    venc_total = float(vt.sum())

    # tabela da escada: categoria x faixa, mais total e acumulado
    t = tab.copy()
    t["Total"] = t.sum(axis=1)
    linhas = t.reset_index().rename(columns={"categoria": "Categoria"})
    tot = pd.DataFrame([["Total"] + list(t.sum())], columns=linhas.columns)
    acum = pd.DataFrame([["Acumulado (até o prazo)"] + list(t.drop(columns=["Total"]).sum().cumsum()) + [None]],
                        columns=linhas.columns)
    pct = pd.DataFrame([["% do PL (acumulado)"]
                        + [v / e.pl if e.pl else None for v in t.drop(columns=["Total"]).sum().cumsum()]
                        + [None]], columns=linhas.columns)
    escada = pd.concat([linhas, tot, acum, pct], ignore_index=True)

    def fmt_escada(c, v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "—"
        if isinstance(v, str):
            return html.escape(v)
        return _brl(v)

    corpo_escada = []
    for _, r in escada.iterrows():
        cls = {"Total": "total", "Acumulado (até o prazo)": "sub",
               "% do PL (acumulado)": "sub"}.get(r["Categoria"], "")
        celulas = []
        for c in escada.columns:
            v = r[c]
            if r["Categoria"] == "% do PL (acumulado)" and c != "Categoria":
                celulas.append(f"<td>{_pct(v) if v is not None else '—'}</td>")
            else:
                celulas.append(f"<td>{fmt_escada(c, v)}</td>")
        corpo_escada.append(f'<tr class="{cls}">' + "".join(celulas) + "</tr>")
    cab = "".join(f"<th>{html.escape(c)}</th>" for c in escada.columns)
    tabela_escada = (f'<div class="rolagem"><table><thead><tr>{cab}</tr></thead>'
                     f'<tbody>{"".join(corpo_escada)}</tbody></table></div>')

    ate = escada_ate(e, tab, venc_total)
    corpo_ate = []
    for _, r in ate.iterrows():
        cls = "venc-linha" if r["Prazo"] == "Vencidos" else ""
        corpo_ate.append(f'<tr class="{cls}"><td>{html.escape(r["Prazo"])}</td><td>{_brl(r["Valor"])}</td>'
                         f'<td>{_pct(r["% do PL"])}</td><td>{_brl(r["Dos quais direitos creditórios"])}</td></tr>')
    tabela_ate = ('<div class="rolagem"><table class="ate"><thead><tr><th>Prazo</th><th>Valor (R$)</th>'
                  '<th>% do PL</th><th>Dos quais direitos creditórios (R$)</th></tr></thead><tbody>'
                  + "".join(corpo_ate) + "</tbody></table></div>")

    # vencidos por componente e por faixa de vencimento (o carrego ainda tem prazo)
    vf = e.por_faixa(e.vencidos.assign(dias=e.vencidos["dias"]), "componente") if not e.vencidos.empty \
        else pd.DataFrame()
    if not vf.empty:
        vf = vf.loc[[c for c in VENCIDOS if c in vf.index]]
        vf["Total"] = vf.sum(axis=1)
        vf_l = vf.reset_index().rename(columns={"componente": "Componente"})
        vf_l = pd.concat([vf_l, pd.DataFrame([["Total"] + list(vf.sum())], columns=vf_l.columns)],
                         ignore_index=True)
        cab_v = list(vf_l.columns)
        tabela_venc = _tabela(vf_l, cab_v, classe_ultima=True)
    else:
        tabela_venc = "<p>Nenhum vencido na data.</p>"

    # composicao do caixa em H
    rel = e.relatorio_h.copy()
    rel = rel[["codigo_ativo", "exposicao", "liq_duration", "valor_liquido"]]
    rel = pd.concat([rel, pd.DataFrame([["Total", rel["exposicao"].sum(), None,
                                         rel["valor_liquido"].sum()]], columns=rel.columns)],
                    ignore_index=True)
    rel["liq_duration"] = rel["liq_duration"].map(lambda v: "—" if v is None or pd.isna(v) else f"D+{int(v)}")
    tabela_rel = _tabela(rel, ["Ativo", "Exposição (R$)", "Prazo", f"Valor líquido em D+{e.horizonte} (R$)"],
                         classe_ultima=True)

    h, cc = e.caixa_h, e.caixa_cc
    kpis = "".join([
        _kpi("PL", f"R$ {_mi(e.pl)} mi", f"posição de {dt.date.fromisoformat(e.data):%d/%m/%Y}"),
        _kpi(f"Caixa Potencial em D+{e.horizonte}", f"R$ {_mi(h['caixa'])} mi",
             f"{_pct(h['caixa_pl'])} do PL · mínimo 15%", _semaforo(h["caixa_pl"])),
        _kpi(f"Referência: cc do fundo (D+{e.cc})", f"R$ {_mi(cc['caixa'])} mi",
             f"{_pct(cc['caixa_pl'])} do PL", _semaforo(cc["caixa_pl"])),
        _kpi("Vencidos (fora da escada)", f"R$ {_mi(venc_total)} mi",
             f"{_pct(venc_total / e.pl if e.pl else None)} do PL", "ruim" if venc_total > 0 else ""),
    ])

    def ind(rot, chave, enq):
        lin = []
        for nome, cx in ((f"D+{e.horizonte}", h), (f"D+{e.cc}", cc)):
            v, ok = cx[chave], cx[enq]
            marca = "—" if ok is None else ("enquadrado" if ok else "desenquadrado")
            lin.append(f"<td>{_pct(v)}</td><td class=\"{'ok' if ok else 'ruim' if ok is False else ''}\">{marca}</td>")
        return f"<tr><td>{rot}</td>{''.join(lin)}</tr>"

    tabela_ind = (
        '<div class="rolagem"><table><thead><tr><th>Indicador</th>'
        f'<th>D+{e.horizonte}</th><th></th><th>D+{e.cc}</th><th></th></tr></thead><tbody>'
        + ind("Caixa Potencial / Maior cotista", "cx_cotista", "enq_cotista")
        + ind("Caixa Potencial / GDP", "cx_gdp", "enq_gdp")
        + ind("Caixa Potencial / Maior distribuidor", "cx_distribuidor", "enq_distribuidor")
        + ind("Caixa Potencial / Resgate máximo", "caixa_resgate", "enq_rco")
        + "</tbody></table></div>")

    legenda = "".join(f'<span><i style="background:{CORES_CAT[c]}"></i>{html.escape(c)}</span>'
                      for c in CATEGORIAS if c in tab.index) + '<span><i class="venc"></i>Vencidos</span>'
    fora = "".join(f"<li>No PL, fora da escada: {html.escape(k)}, R$ {_brl(v)}.</li>"
                   for k, v in e.fora.items() if v)

    def tokens(modo):
        c = dict(CORES[modo])
        c.update({"serie2": "#41a1ff" if modo == "claro" else "#93c9ff",
                  "ciano": "#00b8d4" if modo == "claro" else "#00e1ff",
                  "azul-claro": "#93c9ff" if modo == "claro" else "#5f8fbf"})
        return "".join(f"--{k}:{v};" for k, v in c.items())

    logo = _logo_embutido()
    marca = f'<img src="{logo}" alt="Multiplica" class="logo">' if logo else "<strong>Multiplica</strong>"
    titulo = f"Escada de liquidez · {e.fundo}"
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titulo)}</title>
<style>
:root{{color-scheme:light;{tokens("claro")}}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{color-scheme:dark;{tokens("escuro")}}}}}
:root[data-theme="dark"]{{color-scheme:dark;{tokens("escuro")}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--plano);color:var(--tinta);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}
.faixa{{background:#002346;padding:14px 16px}}.logo{{height:30px;display:block}}
main{{max-width:1120px;margin:0 auto;padding:16px}}
h1{{font-size:22px;margin:8px 0 2px}}h2{{font-size:17px;margin:28px 0 8px}}
.sub{{color:var(--tinta2)}}p.nota{{color:var(--tinta2);font-size:13px;margin:6px 0}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:16px 0}}
.kpi{{background:var(--superficie);border:1px solid var(--borda);border-radius:10px;padding:12px 14px;border-left:4px solid var(--eixo)}}
.kpi.ok{{border-left-color:var(--bom)}}.kpi.ruim{{border-left-color:var(--critico)}}
.kpi-t{{font-size:13px;color:var(--tinta2)}}.kpi-v{{font-size:22px;font-weight:650;margin:2px 0}}.kpi-s{{font-size:13px;color:var(--tinta2)}}
.cartao{{background:var(--superficie);border:1px solid var(--borda);border-radius:10px;padding:14px}}
.grafico{{width:100%;height:auto;display:block}}.tela-grafico{{min-width:560px}}
.grafico .grade{{stroke:var(--grade)}}.grafico .base{{stroke:var(--eixo)}}
.grafico .eixo{{fill:var(--tinta2);font-size:12px}}.grafico .valor{{fill:var(--tinta);font-size:12px;font-weight:600}}
.grafico .venc,.legenda i.venc{{fill:var(--critico);background:var(--critico)}}
.legenda{{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--tinta2);margin-top:8px}}
.legenda i{{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:6px;vertical-align:-1px}}
.rolagem{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}}
th,td{{padding:6px 8px;border-bottom:1px solid var(--borda);text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}}th{{color:var(--tinta2);font-weight:600}}
tr.total td{{font-weight:650;border-top:2px solid var(--eixo)}}tr.sub td{{color:var(--tinta2)}}
td.ok{{color:var(--bom)}}td.ruim{{color:var(--critico)}}
table.ate{{max-width:720px}}table.ate td:first-child{{font-weight:600}}tr.venc-linha td{{color:var(--critico)}}
ul{{margin:6px 0;padding-left:20px;color:var(--tinta2);font-size:13px}}
</style></head><body>
<div class="faixa">{marca}</div>
<main>
<h1>{html.escape(titulo)}</h1>
<div class="sub">CNPJ {html.escape(e.cnpj)} · posição de {dt.date.fromisoformat(e.data):%d/%m/%Y} ·
horizonte de resgate H = D+{e.horizonte} (cc do cadastro: D+{e.cc}) · escada até {e.limite} dias</div>
<div class="kpis">{kpis}</div>

<h2>Escada de liquidez</h2>
{tabela_ate}
<p class="nota">Valores acumulados: "Até 30 dias" é tudo o que vira caixa em até 30 dias, incluindo a
liquidez imediata. "Acima de {e.cortes[-1]} dias" é o que sobra. Direitos creditórios = DC da carteira
(pelo fluxo projetado) + recebíveis do estoque do fundo investido. Vencidos não entram na escada.</p>

<h2>Escada acumulada por categoria (R$ milhões)</h2>
<div class="cartao"><div class="rolagem"><div class="tela-grafico">{_svg(e, tab, venc_total)}</div></div><div class="legenda">{legenda}</div></div>
<p class="nota">Cada barra acumula tudo o que vira caixa até aquele prazo; o número sobre ela é o
total líquido. O que é negativo (CPR a pagar, provisão) aparece abaixo do zero.</p>
<p class="nota">Valor de hoje de cada ativo, posto na faixa em que vira caixa. Direitos creditórios
pelo fluxo projetado, parcela a parcela (principal + juro já corrido); recebíveis do estoque pelo
prazo do título; cotas de fundo pelo prazo de resgate; CPR e tesouraria no dia 0. Vencidos ficam
numa barra à parte e não somam na escada.</p>

<h2>Escada por categoria, faixa a faixa (R$)</h2>
{tabela_escada}
<p class="nota">Escada somada até D+{e.horizonte}: R$ {_brl(e.ate(e.horizonte))}. O Caixa Potencial
oficial em D+{e.horizonte} (acima) aplica as regras do relatório: cota de fundo só conta se resgata
até D+{e.horizonte}, e o valor de CPR/tesouraria entra cheio.</p>

<h2>Vencidos (R$)</h2>
{tabela_venc}
<p class="nota">"A vencer de sacado com vencido" é a marca de carrego: basta um título vencido do
sacado para todos os títulos dele saírem da escada. As colunas mostram quando esses títulos
venceriam.</p>

<h2>Indicadores de liquidez</h2>
{tabela_ind}

<h2>Composição do Caixa Potencial em D+{e.horizonte}</h2>
{tabela_rel}

<h2>Conciliação</h2>
<ul>
<li>{html.escape(conciliacao(e, venc_total))}</li>
<li>As cotas A VENCER/VENCIDA de fundo investido (R$ {_brl(e.cotas_substituidas)}) não entram
pelo valor da cota: entram pelo estoque do fundo investido (R$ {_brl(e.estoque_total)}), título a
título.</li>
{fora}
</ul>
<p class="nota">Fontes: carteira (datalake, MUL_GLD_CARTEIRA_UNIFICADA), estoque
(HOD_TB_ESTOQUE_UNIFICADO), cronograma das operações (Beyond, leitura do próprio fundo) e
fluxo projetado com a curva DI1 da data. Página só com agregados: sem cotista, sacado ou
documento. Gerado em {dt.datetime.now():%d/%m/%Y %H:%M}.</p>
</main></body></html>
"""


def gerar(cadastro, fundo, saida: Path, data: str, horizonte: int, limite: int = LIMITE_PADRAO,
          cortes: tuple[int, ...] = CORTES_PADRAO, destino_bi: Path = Path("bi"),
          html_saida: Path | None = None) -> tuple[Path, Escada]:
    e = calcular(cadastro, fundo, saida, data, horizonte, limite, cortes, destino_bi)
    caminho = Path(html_saida) if html_saida else (
        Path("relatorios") / f"escada_{''.join(ch for ch in fundo.cnpj if ch.isdigit())}"
                             f"_{e.data}_H{e.horizonte}_{e.limite}d.html")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(montar_html(e), encoding="utf-8")
    return caminho, e
