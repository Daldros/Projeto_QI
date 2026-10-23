"""Apresentacao de liquidez para o comite: capa com os fundos escolhidos e uma
pagina por fundo, nos blocos A-F de `docs/plano-apresentacao.md`.

E a "versao melhorada" do painel, e convive com ele: `painel` continua gerando
o material antigo (painel.html + fundos/*.html) a partir dos mesmos fatos;
`apresentacao` le os mesmos fatos (bi/), o fluxo e a carteira tratada
(dados/tratado) e escreve um HTML so, no padrao da marca Multiplica.

Ordem dos blocos = ordem da pergunta do comite:
  A  ancoras do fundo e os limites da politica (o que o gestor ja calcula)
  B  de onde vem a liquidez: composicao do PL e escada por VERTICE ANBIMA em
     dias uteis (1, 2, 3, 4, 5, 10, 21, 42, 63)
  C  o que pode sair: resgate potencial por vertice (percentil x PL) e
     concentracao do passivo (top 1/3/5, HHI)
  D  cobertura ativo x passivo por vertice -- caixa acumulado / resgate
  E  estresse no prazo de cotizacao: 3 cenarios (decisao da area em 05/10/2026,
     "primeiro esses, depois melhorar")
  F  premissas e qualidade do dado: Beyond, bullets, cotas sem prazo, vencidos

NUMEROS. Nada aqui recalcula indicador: PL, caixa potencial, maior cotista,
GDP e RCO vem de `fato_indicadores`, como no painel. O que e novo e derivado
do que ja existe: a escada corta o fluxo projetado e a carteira tratada por
data de vertice em dias uteis; o resgate potencial e o percentil do
`fato_anbima` vezes o PL; a cobertura e a razao; o estresse combina os dois.
Quando um insumo falta (fluxo nao gravado, carteira tratada ausente), o bloco
diz isso em vez de mostrar zero.

MARCA. Fundo branco, texto Azul #002346, destaque Blue 3; capa Azul com Neon
so como acento; Wix Madefor Display. Graficos de UMA serie na cor da marca,
rotulo direto -- a paleta Multiplica e um azul unico e nao separa series lado
a lado (validado em 05/10/2026). Estado em Green 1 / Yellow 1 / Red 2 sempre
com texto. Logo oficial de config/marca, nunca redesenhado.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
from pathlib import Path

import pandas as pd

from . import fatos as ft

log = logging.getLogger("liquidez")

ARQUIVO_PADRAO = Path("apresentacao.html")
LOGO_NEGATIVO = Path("config") / "marca" / "multiplica_logo_negativo.svg"
VERTICES_PADRAO = (1, 2, 3, 4, 5, 10, 21, 42, 63)
BASES_COTA = ("OUTROS FUNDOS", "FUNDOS-FUNDOS", "FUNDOS-FUNDOS_A_MERCADO")
PRODUTO_FLUXO = "DC"
FECHADO = 999


# ---------------------------------------------------------------------------
# Calendario: vertice em dias uteis -> data
# ---------------------------------------------------------------------------

def _e_dia_util(data: dt.date) -> bool:
    try:
        from .calendario import e_dia_util
        return e_dia_util(data)
    except Exception:  # sem feriados.csv: so fim de semana
        return data.weekday() < 5


def data_do_vertice(data_base: dt.date, dias_uteis: int) -> dt.date:
    """A data que fica `dias_uteis` dias uteis depois da data-base."""
    data, faltam = data_base, dias_uteis
    while faltam > 0:
        data += dt.timedelta(days=1)
        if _e_dia_util(data):
            faltam -= 1
    return data


# ---------------------------------------------------------------------------
# Bloco B: escada por vertice
# ---------------------------------------------------------------------------

def _data(valor) -> dt.date | None:
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    return pd.Timestamp(valor).date()


def escada_por_vertice(carteira_tratada: pd.DataFrame, fluxo: pd.DataFrame, data_base: dt.date,
                       vertices: tuple[int, ...] = VERTICES_PADRAO) -> list[dict]:
    """Caixa acumulado em cada vertice (dias uteis), em R$.

    Duas pernas, somadas por data de vertice:
      - fluxo projetado das operacoes de DC: parcelas (amortizacao + acrual, o
        valor "marcado" que o fatos usa) com vencimento ate a data do vertice;
      - carteira tratada, menos o DC: linhas com prazo (Qtde_Dias, dias corridos)
        cuja data-base + prazo cai ate o vertice, fora vencidos e excluidos.
        Prazo 999 (fechado / sem liquidez) nunca entra; sem prazo nao entra.
    """
    saida = []
    carteira = carteira_tratada if carteira_tratada is not None else pd.DataFrame()
    fluxo = fluxo if fluxo is not None else pd.DataFrame()

    outras = pd.DataFrame()
    if not carteira.empty and "Base_Origem" in carteira:
        base = carteira["Base_Origem"].fillna("").astype(str).str.strip().str.upper()
        sel = base != PRODUTO_FLUXO
        for coluna in ("Ativo Vencido", "Ativos Excluidos"):
            if coluna in carteira:
                sel &= carteira[coluna].fillna("").astype(str).str.upper() != "SIM"
        outras = carteira[sel].copy()
        outras["_dias"] = pd.to_numeric(outras.get("Qtde_Dias"), errors="coerce")
        outras["_valor"] = pd.to_numeric(outras.get("Valor_Liquido"), errors="coerce").fillna(0.0)
        outras = outras[outras["_dias"].notna() & (outras["_dias"] < FECHADO)]
        outras["_data"] = [data_base + dt.timedelta(days=int(d)) for d in outras["_dias"]]

    parcelas = pd.DataFrame()
    if not fluxo.empty and "dt_vencimento" in fluxo:
        parcelas = fluxo.copy()
        parcelas["_data"] = [_data(v) for v in parcelas["dt_vencimento"]]
        valor = pd.to_numeric(parcelas.get("vl_amortizacao"), errors="coerce").fillna(0.0)
        if "vl_acrual" in parcelas:
            valor = valor + pd.to_numeric(parcelas["vl_acrual"], errors="coerce").fillna(0.0)
        parcelas["_valor"] = valor
        parcelas = parcelas[parcelas["_data"].notna()]

    for v in vertices:
        limite = data_do_vertice(data_base, v)
        de_outras = float(outras.loc[outras["_data"] <= limite, "_valor"].sum()) if not outras.empty else 0.0
        de_dc = float(parcelas.loc[parcelas["_data"] <= limite, "_valor"].sum()) if not parcelas.empty else 0.0
        saida.append({"vertice": v, "data": limite, "caixa": de_outras + de_dc,
                      "caixa_outras": de_outras, "caixa_dc": de_dc})
    return saida


# ---------------------------------------------------------------------------
# Bloco C/D: resgate potencial, concentracao, cobertura
# ---------------------------------------------------------------------------

def resgate_por_vertice(anbima: pd.DataFrame, pl: float) -> dict[int, dict]:
    """{vertice: {percentil, resgate}} a partir do fato_anbima do fundo."""
    saida = {}
    if anbima is None or anbima.empty:
        return saida
    for _, l in anbima.iterrows():
        h = int(l["horizonte_dias"])
        p = float(l["percentil_95"])
        saida[h] = {"percentil": p, "resgate": p * pl, "enquadrado": bool(l.get("enquadrado", False))}
    return saida


def cobertura_por_vertice(escada: list[dict], resgates: dict[int, dict]) -> list[dict]:
    linhas = []
    for e in escada:
        r = resgates.get(e["vertice"])
        razao = (e["caixa"] / r["resgate"]) if r and r["resgate"] > 0 else None
        estado = None if razao is None else ("ok" if razao >= 1 else "atencao" if razao >= 0.8 else "risco")
        linhas.append({**e, "resgate": r["resgate"] if r else None,
                       "percentil": r["percentil"] if r else None, "razao": razao, "estado": estado})
    return linhas


def concentracao(db_passivo: pd.DataFrame, coluna_saldo: str = "vlSaldoTotal") -> dict:
    """Top 1/3/5 e HHI do passivo (saldos positivos)."""
    if db_passivo is None or db_passivo.empty or coluna_saldo not in db_passivo:
        return {"n": 0, "top1": 0.0, "top3": 0.0, "top5": 0.0, "hhi": None, "total": 0.0}
    saldos = pd.to_numeric(db_passivo[coluna_saldo], errors="coerce").fillna(0.0)
    saldos = saldos[saldos > 0].sort_values(ascending=False)
    total = float(saldos.sum())
    if total <= 0:
        return {"n": 0, "top1": 0.0, "top3": 0.0, "top5": 0.0, "hhi": None, "total": 0.0}
    pesos = saldos / total
    return {"n": int(len(saldos)), "top1": float(pesos.iloc[:1].sum()), "top3": float(pesos.iloc[:3].sum()),
            "top5": float(pesos.iloc[:5].sum()), "hhi": float((pesos ** 2).sum()), "total": total}


# ---------------------------------------------------------------------------
# Bloco E: estresse
# ---------------------------------------------------------------------------

def estresse(caixa_cc: float, fluxo_dc_ate_cc: float, maior_cotista: float, resgate_max: float,
             pl: float) -> list[dict]:
    """Tres cenarios no prazo de cotizacao (decisao da area, 05/10/2026).

    1. Resgate: sai o maior cotista e repete-se o pior resgate historico.
    2. Ativo: os recebiveis de DC que venceriam ate o prazo atrasam (saem do caixa).
    3. Combinado: 1 + 2.
    `sobra` negativa e caixa que falta.
    """
    saida_1 = maior_cotista + resgate_max
    cen = [
        {"n": 1, "nome": "Resgate: sai o maior cotista + pior resgate histórico",
         "detalhe": f"saída {_mi(saida_1)} contra caixa {_mi(caixa_cc)}", "sobra": caixa_cc - saida_1},
        {"n": 2, "nome": "Ativo: recebíveis de DC do prazo atrasam",
         "detalhe": f"caixa {_mi(caixa_cc)} − {_mi(fluxo_dc_ate_cc)} do fluxo de DC", "sobra": caixa_cc - fluxo_dc_ate_cc},
        {"n": 3, "nome": "Combinado (1 + 2)",
         "detalhe": "resgate e atraso ao mesmo tempo", "sobra": caixa_cc - saida_1 - fluxo_dc_ate_cc},
    ]
    for c in cen:
        c["sobra_pl"] = (c["sobra"] / pl) if pl else None
    return cen


# ---------------------------------------------------------------------------
# Bloco F: premissas
# ---------------------------------------------------------------------------

def premissas(carteira_tratada: pd.DataFrame, fluxo: pd.DataFrame, beyond: dict, bullets: dict,
              cnpj_numerico: str) -> dict:
    p = {"beyond_ops": len(beyond or {}), "ncpx_ops": 0, "cotas_sem_prazo": 0, "cotas_sem_prazo_valor": 0.0,
         "cotas_total": 0, "cotas_valor": 0.0, "vencidos_ops": 0, "vencidos_valor": 0.0, "pdd_valor": 0.0,
         "bullets_pendentes": []}
    c = carteira_tratada if carteira_tratada is not None else pd.DataFrame()
    if not c.empty and "Base_Origem" in c:
        base = c["Base_Origem"].fillna("").astype(str).str.strip().str.upper()
        ativo = c.get("Codigo_Ativo", pd.Series("", index=c.index)).fillna("").astype(str).str.upper()
        valor = pd.to_numeric(c.get("Valor_Liquido"), errors="coerce").fillna(0.0)
        dias = pd.to_numeric(c.get("Qtde_Dias"), errors="coerce")
        cotas = base.isin(BASES_COTA)
        p["cotas_total"] = int(c.loc[cotas, "Codigo_Op"].nunique()) if "Codigo_Op" in c else int(cotas.sum())
        p["cotas_valor"] = float(valor[cotas].sum())
        sem = cotas & (dias.isna() | (dias >= FECHADO))
        p["cotas_sem_prazo"] = int(c.loc[sem, "Codigo_Op"].nunique()) if "Codigo_Op" in c else int(sem.sum())
        p["cotas_sem_prazo_valor"] = float(valor[sem].sum())
        dc = base == PRODUTO_FLUXO
        p["ncpx_ops"] = int(c.loc[dc & ativo.str.startswith("NCPX"), "Codigo_Op"].nunique()) if "Codigo_Op" in c else 0
        if "Ativo Vencido" in c:
            venc = dc & (c["Ativo Vencido"].fillna("").astype(str).str.upper() == "SIM") & ativo.str.startswith("VC")
            p["vencidos_ops"] = int(c.loc[venc, "Codigo_Op"].nunique()) if "Codigo_Op" in c else int(venc.sum())
            p["vencidos_valor"] = float(valor[venc].sum())
        p["pdd_valor"] = float(valor[dc & (ativo.str.startswith("PDD") | ativo.str.startswith("PDC"))].sum())
    if fluxo is not None and not fluxo.empty and "origem" in fluxo and "codigo_op" in fluxo:
        est = fluxo[fluxo["origem"].fillna("").astype(str) == "estoque"]
        p["estoque_ops"] = int(est["codigo_op"].nunique())
        p["estoque_valor"] = float(pd.to_numeric(est.get("vl_amortizacao"), errors="coerce").fillna(0.0).sum())
    else:
        p["estoque_ops"], p["estoque_valor"] = 0, 0.0
    ops = (bullets or {}).get("operacoes", {})
    if ops and fluxo is not None and not fluxo.empty and "codigo_op" in fluxo:
        proprio = fluxo[fluxo["cnpj_fundo"].astype(str).str.replace(r"\D", "", regex=True) == cnpj_numerico] \
            if "cnpj_fundo" in fluxo else fluxo
        valores = pd.to_numeric(proprio.get("vl_amortizacao"), errors="coerce").fillna(0.0).groupby(proprio["codigo_op"]).sum()
        for codigo, d in ops.items():
            if d.get("estado") in ("pendente", "estoque") and codigo in valores.index:
                p["bullets_pendentes"].append({"codigo": codigo, "valor": float(valores[codigo]),
                                               "estado": d.get("estado")})
        p["bullets_pendentes"].sort(key=lambda b: -abs(b["valor"]))
    return p


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------

def _ler_fato(tabela: str, data: str, destino: Path) -> pd.DataFrame:
    caminho = ft.caminho_particao(tabela, data, destino)
    return pd.read_parquet(caminho) if caminho.exists() else pd.DataFrame()


def _ler_local(caminho: Path) -> pd.DataFrame:
    for ext in (".parquet", ".csv"):
        c = caminho.with_suffix(ext)
        if c.exists():
            return pd.read_parquet(c) if ext == ".parquet" else pd.read_csv(c, sep=";", encoding="utf-8-sig")
    return pd.DataFrame()


def _so_digitos(texto) -> str:
    return "".join(ch for ch in str(texto or "") if ch.isdigit())


def carregar_fundo(fundo, data: str, destino_bi: Path, saida_dados: Path) -> dict:
    """Tudo o que a pagina de um fundo precisa; cada ausencia vira aviso."""
    from . import beyond_cronograma as bc
    from . import bullets as bl

    cnpj_num = fundo.cnpj_numerico
    ind = _ler_fato("fato_indicadores", data, destino_bi)
    ind = ind[ind["cnpj_fundo"].map(_so_digitos) == cnpj_num] if not ind.empty else ind
    anb = _ler_fato("fato_anbima", data, destino_bi)
    anb = anb[anb["cnpj_fundo"].map(_so_digitos) == cnpj_num] if not anb.empty else anb
    rel = _ler_fato("fato_relatorio", data, destino_bi)
    rel = rel[rel["cnpj_fundo"].map(_so_digitos) == cnpj_num] if not rel.empty else rel
    flx = _ler_fato("fato_fluxo", data, destino_bi)
    flx = flx[flx["cnpj_fundo"].map(_so_digitos) == cnpj_num] if not flx.empty else flx
    cart = _ler_local(Path(saida_dados) / "tratado" / f"carteira_{cnpj_num}")
    passivo = _ler_local(Path(saida_dados) / "db_passivo")
    if not passivo.empty and "cnpj_fundo" in passivo:
        passivo = passivo[passivo["cnpj_fundo"].map(_so_digitos) == cnpj_num]
    avisos = []
    if ind.empty:
        avisos.append("sem fato_indicadores nesta data: rode 'fatos gerar'")
    if flx.empty:
        avisos.append("sem fato_fluxo: rode 'fluxo --salvar --fatos'")
    if cart.empty:
        avisos.append("sem carteira tratada em dados/tratado: rode 'tratar --salvar'")
    if passivo.empty:
        avisos.append("sem db_passivo na carga: concentração indisponível")
    try:
        beyond = bc.carregar(fundo.cnpj)
    except Exception:
        beyond = {}
    bullets = bl.carregar()
    return {"fundo": fundo, "indicadores": ind.iloc[0].to_dict() if not ind.empty else {},
            "anbima": anb, "relatorio": rel, "fluxo": flx, "carteira": cart, "passivo": passivo,
            "beyond": beyond, "bullets": bullets, "avisos": avisos}


def analisar(ctx: dict, data_base: dt.date, vertices: tuple[int, ...] = VERTICES_PADRAO) -> dict:
    """Deriva os blocos B-F a partir do que foi carregado. Puro: sem disco."""
    ind = ctx["indicadores"]
    fundo = ctx["fundo"]
    pl = float(ind.get("pl_mil", 0) or 0) * 1000
    cc = int(ind.get("prazo_cotizacao") or fundo.cc or FECHADO)
    escada = escada_por_vertice(ctx["carteira"], ctx["fluxo"], data_base, vertices)
    resg = resgate_por_vertice(ctx["anbima"], pl)
    cob = cobertura_por_vertice(escada, resg)
    conc = concentracao(ctx["passivo"])

    caixa_cc = float(ind.get("caixa_potencial_mil", 0) or 0) * 1000
    limite_cc = data_base + dt.timedelta(days=cc) if cc < FECHADO else None
    fluxo_dc_cc = 0.0
    if limite_cc is not None and ctx["fluxo"] is not None and not ctx["fluxo"].empty:
        f = ctx["fluxo"]
        datas = pd.Series([_data(v) for v in f["dt_vencimento"]], index=f.index)
        val = pd.to_numeric(f.get("vl_amortizacao"), errors="coerce").fillna(0.0)
        if "vl_acrual" in f:
            val = val + pd.to_numeric(f["vl_acrual"], errors="coerce").fillna(0.0)
        fluxo_dc_cc = float(val[datas.notna() & (datas <= limite_cc)].sum())
    est = estresse(caixa_cc, fluxo_dc_cc, float(ind.get("maior_cotista_mil", 0) or 0) * 1000,
                   float(ind.get("resgate_max_mil", 0) or 0) * 1000, pl) if cc < FECHADO else []

    composicao = []
    rel = ctx["relatorio"]
    if rel is not None and not rel.empty and "origem" in rel:
        prod = rel[rel["origem"] == "produto"]
        for _, l in prod.sort_values("exposicao", ascending=False).iterrows():
            composicao.append({"produto": str(l["codigo_ativo"]), "exposicao": float(l["exposicao"]),
                               "liquido": float(l["valor_liquido"])})
    prem = premissas(ctx["carteira"], ctx["fluxo"], ctx["beyond"], ctx["bullets"], fundo.cnpj_numerico)
    return {"pl": pl, "cc": cc, "caixa_cc": caixa_cc, "fluxo_dc_cc": fluxo_dc_cc, "escada": escada,
            "cobertura": cob, "concentracao": conc, "estresse": est, "composicao": composicao,
            "premissas": prem}


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def _e(t) -> str:
    return html.escape(str(t if t is not None else ""), quote=True)


def _mi(v, casas: int = 1) -> str:
    """R$ em milhoes, formato brasileiro."""
    if v is None:
        return "—"
    s = f"{v / 1e6:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s} mi"


def _pct(v, casas: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.{casas}f}%".replace(".", ",")


def _x(v, casas: int = 1) -> str:
    if v is None:
        return "—"
    return (f"{v:.0f}×" if v >= 10 else f"{v:.{casas}f}×").replace(".", ",")


def _br(data) -> str:
    d = _data(data)
    return d.strftime("%d/%m/%Y") if d else "—"


def _estado(enq) -> tuple[str, str]:
    if enq is None:
        return "pendente", "sem dado"
    return ("ok", "enquadrado") if enq else ("risco", "desenquadrado")


def _logo(caminho: Path = LOGO_NEGATIVO) -> str:
    if not Path(caminho).exists():
        return "<span style='color:#fff;font-weight:600'>Multiplica</span>"
    svg = Path(caminho).read_text(encoding="utf-8")
    svg = svg[svg.index("<svg"):]
    return svg.replace("<svg ", '<svg role="img" aria-label="Multiplica" ', 1)


def _svg_barras_h(dados: list[tuple[str, float, str]]) -> str:
    """Uma serie, barras horizontais, rotulo direto. dados: (rotulo, valor, complemento)."""
    if not dados:
        return "<p class='nota'>sem dados</p>"
    W, ml, mr, mt = 520, 170, 118, 8
    alt = (230 - mt - 8) / len(dados)
    mx = max(abs(v) for _, v, _ in dados) or 1.0
    partes = [f'<svg class="gr" viewBox="0 0 {W} 230" role="img">']
    for i, (r, v, comp) in enumerate(dados):
        y = mt + i * alt + 4
        hh = alt - 9
        w = max(2.0, (W - ml - mr) * abs(v) / mx)
        partes.append(f'<text x="{ml - 10}" y="{y + hh / 2 + 4:.1f}" text-anchor="end" font-size="12" class="t">{_e(r)}</text>')
        partes.append(f'<rect x="{ml}" y="{y:.1f}" width="{w:.1f}" height="{hh:.1f}" rx="4" class="b"><title>{_e(r)}: {_e(_mi(v))}</title></rect>')
        partes.append(f'<text x="{ml + w + 8:.1f}" y="{y + hh / 2 + 4:.1f}" font-size="12" class="t">{_e(_mi(v).replace("R$ ", ""))} <tspan class="f">{_e(comp)}</tspan></text>')
    partes.append("</svg>")
    return "".join(partes)


def _svg_colunas(dados: list[tuple[str, float | None, str]], eixo: str) -> str:
    """Uma serie, colunas por vertice. Valor None = coluna tracejada (sem dado)."""
    W, H, ml, mr, mt, mb = 520, 230, 56, 10, 18, 34
    valores = [v for _, v, _ in dados if v is not None]
    mx = max(valores) if valores else 1.0
    if mx <= 0:
        mx = 1.0
    passo = 10 ** max(0, len(str(int(mx))) - 1)
    teto = ((int(mx // passo) + 1) * passo)
    pw = (W - ml - mr) / max(1, len(dados))

    def esc(v):
        return (H - mb) - (H - mt - mb) * v / teto

    partes = [f'<svg class="gr" viewBox="0 0 {W} {H}" role="img">']
    for k in range(5):
        t = teto * k / 4
        y = esc(t)
        partes.append(f'<line x1="{ml}" x2="{W - mr}" y1="{y:.1f}" y2="{y:.1f}" class="g"/>'
                      f'<text x="{ml - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="10" class="f">{_e(_mi(t, 0).replace("R$ ", "").replace(" mi", ""))}</text>')
    for i, (r, v, tip) in enumerate(dados):
        x = ml + i * pw + pw * 0.2
        w = pw * 0.6
        y0 = H - mb
        if v is None:
            partes.append(f'<rect x="{x:.1f}" y="{mt}" width="{w:.1f}" height="{y0 - mt:.1f}" rx="4" class="vazio"/>')
        else:
            y = esc(v)
            partes.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{max(2.0, y0 - y):.1f}" rx="4" class="b"><title>{_e(tip)}</title></rect>')
            partes.append(f'<text x="{x + w / 2:.1f}" y="{y - 5:.1f}" text-anchor="middle" font-size="10" class="t">{_e(_mi(v, 0).replace("R$ ", "").replace(" mi", ""))}</text>')
        partes.append(f'<text x="{x + w / 2:.1f}" y="{H - mb + 16}" text-anchor="middle" font-size="11" class="m">{_e(r)}</text>')
    partes.append(f'<text x="{W - mr}" y="{H - 4}" text-anchor="end" font-size="10" class="f">{_e(eixo)}</text></svg>')
    return "".join(partes)


def _kpi(rotulo: str, valor: str, legenda: str, estado: str | None = None) -> str:
    cls = f" {estado}" if estado else ""
    return (f"<div class='card kpi{cls}'><div class='r'>{_e(rotulo)}</div><div class='v'>{_e(valor)}</div>"
            f"<div class='l'>{_e(legenda)}</div></div>")


def _pagina_fundo(ctx: dict, an: dict, data_base: dt.date, slug: str, ativa: bool) -> str:
    f = ctx["fundo"]
    ind = ctx["indicadores"]
    cc = an["cc"]
    fechado = cc >= FECHADO
    partes = [f"<section class='pagina' data-fundo='{slug}' data-ativa='{'true' if ativa else 'false'}'>"]

    # cabecalho
    enqs = [ind.get(k) for k in ("enq_cotista", "enq_gdp", "enq_distribuidor", "enq_rco")]
    anb_ok = bool(len(ctx["anbima"])) and bool(ctx["anbima"]["enquadrado"].all())
    todos = [e for e in enqs if e is not None]
    if not ind:
        est_cls, est_txt = "pendente", "sem fatos nesta data"
    elif all(todos) and anb_ok:
        est_cls, est_txt = "ok", f"enquadrado nos {len(todos) + 1} critérios"
    else:
        falhos = sum(1 for e in todos if e is False) + (0 if anb_ok else 1)
        est_cls, est_txt = "risco", f"{falhos} critério(s) fora"
    cotiz = "fechado (sem resgate)" if fechado else f"cotização D+{cc}"
    partes.append(
        f"<div class='cab'><div><p class='eyebrow'>{_e(f.codinome_beyond or f.nome_interno)}</p><h2>{_e(f.nome_interno)}</h2>"
        f"<p class='muted small'>Administrador {_e(f.administrador or '—')} · {_e(cotiz)} · "
        f"{int(ind.get('n_cotistas') or 0)} cotistas · posição {data_base:%d/%m/%Y}</p></div>"
        f"<span class='estado {est_cls}'>{_e(est_txt)}</span></div>")
    for a in ctx["avisos"]:
        partes.append(f"<p class='aviso'>⚠ {_e(a)}</p>")
    if not ind:
        partes.append("</section>")
        return "".join(partes)

    # A
    pl, caixa = an["pl"], an["caixa_cc"]
    partes.append("<div class='bloco'><div class='bloco-cab'><span class='eyebrow'>bloco a</span><h3>Âncoras do fundo e os limites da política</h3></div><div class='grid g5 num'>")
    partes.append(_kpi("Patrimônio líquido", _mi(pl), "fato_indicadores"))
    partes.append(_kpi(f"Caixa potencial em D+{cc}" if not fechado else "Caixa potencial", _mi(caixa),
                       f"{_pct(ind.get('caixa_pl'))} do PL · fluxo projetado + ativos com prazo"))
    e1 = _estado(ind.get("enq_cotista"))
    partes.append(_kpi("Maior cotista", _mi(float(ind.get("maior_cotista_mil") or 0) * 1000),
                       f"caixa cobre {_x(ind.get('cx_cotista'))} · {e1[1]}", e1[0]))
    e2 = _estado(ind.get("enq_gdp"))
    partes.append(_kpi("GDP (2,33 × σ dos resgates)", _mi(float(ind.get("gdp_mil") or 0) * 1000),
                       f"caixa cobre {_x(ind.get('cx_gdp'))} · {e2[1]}", e2[0]))
    e3 = _estado(ind.get("enq_rco"))
    partes.append(_kpi("Maior resgate histórico", _mi(float(ind.get("resgate_max_mil") or 0) * 1000),
                       f"caixa cobre {_x(ind.get('caixa_resgate'))} · {e3[1]}", e3[0]))
    partes.append("</div></div>")

    # B
    comp = [(c["produto"], c["exposicao"], _pct(c["exposicao"] / pl) if pl else "") for c in an["composicao"][:7]]
    esc = [(f"D+{e['vertice']}", e["caixa"], f"D+{e['vertice']} ({e['data']:%d/%m}): {_mi(e['caixa'])} — DC {_mi(e['caixa_dc'])} + outros {_mi(e['caixa_outras'])}")
           for e in an["escada"]]
    prem = an["premissas"]
    partes.append("<div class='bloco'><div class='bloco-cab'><span class='eyebrow'>bloco b</span><h3>De onde vem a liquidez</h3></div><div class='grid g2'>")
    partes.append(f"<div class='card'><p class='small muted'>Composição do PL por produto (exposição)</p>{_svg_barras_h(comp)}"
                  f"<p class='nota'>Cotas de outros fundos: {prem['cotas_total']} posições, {_mi(prem['cotas_valor'])}; "
                  f"{prem['cotas_sem_prazo']} sem prazo conhecido ({_mi(prem['cotas_sem_prazo_valor'])}) entram como liquidez zero.</p></div>")
    partes.append(f"<div class='card'><p class='small muted'>Escada de liquidez por vértice ANBIMA (dias úteis), caixa acumulado</p>{_svg_colunas(esc, 'vértices em dias úteis · R$ mi acumulado')}"
                  f"<p class='nota'>Fluxo projetado das operações de DC (Beyond, cronogramas, bullet) + ativos com prazo na carteira tratada. "
                  f"Caixa potencial do gestor em D+{cc} (dias corridos): {_mi(caixa)}.</p></div></div></div>")

    # C
    resg = [(f"D+{c['vertice']}", c["resgate"], f"D+{c['vertice']}: {_pct(c['percentil'], 2)} do PL = {_mi(c['resgate'])}") for c in an["cobertura"]]
    conc = an["concentracao"]
    partes.append("<div class='bloco'><div class='bloco-cab'><span class='eyebrow'>bloco c</span><h3>O que pode sair</h3></div><div class='grid g2'>")
    if fechado:
        partes.append("<div class='card'><p class='small muted'>Resgate potencial</p><p class='nota'>Fundo fechado: não há resgate a pedido. O passivo sai por amortização programada — a leitura de cobertura passa a ser das amortizações (passo 19 do plano).</p></div>")
    else:
        partes.append(f"<div class='card'><p class='small muted'>Resgate potencial por vértice — percentil 95 ANBIMA × PL</p>{_svg_colunas(resg, 'percentil 95 × PL · R$ mi acumulado')}"
                      "<p class='nota'>Método em uso: percentil fixo por vértice aplicado ao PL. Troca pela matriz ANBIMA por segmento de cotista quando a classificação estiver comprovada no banco.</p></div>")
    linhas_conc = (
        f"<tr><td>Maior cotista</td><td class='n'>{_pct(conc['top1'])} do passivo</td></tr>"
        f"<tr><td>Três maiores</td><td class='n'>{_pct(conc['top3'])}</td></tr>"
        f"<tr><td>Cinco maiores</td><td class='n'>{_pct(conc['top5'])}</td></tr>"
        f"<tr><td>HHI</td><td class='n'>{('%.3f' % conc['hhi']).replace('.', ',') if conc['hhi'] is not None else '—'}</td></tr>"
        f"<tr><td>Cotistas com saldo</td><td class='n'>{conc['n']}</td></tr>"
        f"<tr><td>Maior distribuidor</td><td class='n'>{_mi(float(ind.get('maior_distribuidor_mil') or 0) * 1000)}</td></tr>"
    ) if conc["n"] else "<tr><td colspan='2'>db_passivo ausente na carga</td></tr>"
    partes.append(f"<div class='card'><p class='small muted'>Concentração do passivo</p><div class='tabela'><table><thead><tr><th>Medida</th><th class='n'>Valor</th></tr></thead><tbody class='num'>{linhas_conc}</tbody></table></div></div></div></div>")

    # D
    if not fechado:
        celulas = []
        for c in an["cobertura"]:
            if c["razao"] is None:
                celulas.append(f"<div class='v pendente'><b>—</b><span>D+{c['vertice']}</span></div>")
            else:
                celulas.append(f"<div class='v {c['estado']}' title='caixa {_e(_mi(c['caixa']))} ÷ resgate {_e(_mi(c['resgate']))}'><b>{_e(_x(c['razao']))}</b><span>D+{c['vertice']}</span></div>")
        partes.append("<div class='bloco'><div class='bloco-cab'><span class='eyebrow'>bloco d</span><h3>Cobertura ativo × passivo por vértice</h3><span class='small muted'>caixa acumulado ÷ resgate potencial acumulado</span></div>"
                      f"<div class='card'><div class='cob num'>{''.join(celulas)}</div>"
                      "<div class='legenda'><span><i class='ok'></i>≥ 1,0 coberto</span><span><i class='atencao'></i>0,8 a 1,0 atenção</span><span><i class='risco'></i>&lt; 0,8 descoberto</span></div>"
                      "<p class='nota'>Cada vértice usa só o que vence até ele (dias úteis). É a leitura ANBIMA / CVM 175 num quadro: onde a razão cai abaixo de 1, o fundo depende de vender ativo ou de o resgate não vir.</p></div></div>")

    # E
    if an["estresse"]:
        partes.append("<div class='bloco'><div class='bloco-cab'><span class='eyebrow'>bloco e</span><h3>Estresse no prazo de cotização</h3><span class='small muted'>primeira versão dos cenários (área, 05/10/2026)</span></div><div class='grid g3 num'>")
        for c in an["estresse"]:
            est = "ok" if c["sobra"] >= 0 else "risco"
            partes.append(_kpi(f"{c['n']} · {c['nome']}", ("sobra " if c["sobra"] >= 0 else "falta ") + _mi(abs(c["sobra"])),
                               f"{c['detalhe']} · {_pct(c['sobra_pl'])} do PL", est))
        partes.append("</div></div>")

    # F
    bul = prem["bullets_pendentes"]
    lista_bul = "".join(f"<li><span class='num'>{_e(b['codigo'])}</span> {_e(_mi(b['valor']))}</li>" for b in bul[:12])
    partes.append("<div class='bloco'><div class='bloco-cab'><span class='eyebrow'>bloco f</span><h3>Premissas e qualidade do dado</h3></div><div class='card'>")
    partes.append(f"<div class='premissa'><span class='tag'>cobertura</span><p><b>Beyond: {prem['beyond_ops']} operações com cronograma real</b> de {prem['ncpx_ops']} notas comerciais na carteira. O resto das operações de DC é projetado como pagamento único no vencimento (bullet).</p></div>")
    if prem.get("estoque_ops"):
        partes.append(f"<div class='premissa'><span class='tag'>premissa</span><p><b>{prem['estoque_ops']} linha(s) agregada(s) (MEZAN/SRP) projetada(s) pelo estoque do fundo</b> ({_mi(prem['estoque_valor'])}): o valor da carteira foi repartido pelos vencimentos dos recebíveis a vencer, pro rata ao valor presente. É o look-through do pool; vencidos ficam fora.</p></div>")
    partes.append(f"<div class='premissa'><span class='tag'>premissa</span><p><b>{len(bul)} bullets a conferir</b> ({_mi(sum(b['valor'] for b in bul))}). Se amortizam, o caixa está subestimado.</p>{'<ul class=bul>' + lista_bul + '</ul>' if lista_bul else ''}</div>")
    partes.append(f"<div class='premissa'><span class='tag'>premissa</span><p><b>{prem['cotas_sem_prazo']} cotas de outros fundos sem prazo</b> ({_mi(prem['cotas_sem_prazo_valor'])}) entram como liquidez zero. Atenção: cota de FIDC com cotização D+30 também não é liquidez garantida — o fundo investido tem o mesmo problema; o look-through pelo estoque dele é o próximo passo.</p></div>")
    partes.append(f"<div class='premissa'><span class='tag'>regra</span><p><b>Vencidos:</b> {prem['vencidos_ops']} operações ({_mi(prem['vencidos_valor'])}) fora da liquidez; PDD {_mi(prem['pdd_valor'])}. Vencido = Codigo_Ativo VC* com quantidade &gt; 0.</p></div>")
    partes.append("</div></div></section>")
    return "".join(partes)


def _card_capa(ctx: dict, an: dict, slug: str, ativa: bool) -> str:
    f = ctx["fundo"]
    ind = ctx["indicadores"]
    if not ind:
        anc = "<div><b>—</b><span>PL</span></div><div><b>—</b><span>caixa / PL</span></div><div><b>—</b><span>maior cotista / PL</span></div>"
        est = "<span class='estado pendente'>sem fatos nesta data</span>"
    else:
        pl = an["pl"]
        mc = float(ind.get("maior_cotista_mil") or 0) * 1000
        anc = (f"<div><b>{_e(_mi(pl, 0).replace('R$ ', '').replace(' mi', ''))}</b><span>PL, R$ mi</span></div>"
               f"<div><b>{_e(_pct(ind.get('caixa_pl')))}</b><span>caixa potencial / PL</span></div>"
               f"<div><b>{_e(_pct(mc / pl) if pl else '—')}</b><span>maior cotista / PL</span></div>")
        enqs = [ind.get(k) for k in ("enq_cotista", "enq_gdp", "enq_distribuidor", "enq_rco") if ind.get(k) is not None]
        anb_ok = bool(len(ctx["anbima"])) and bool(ctx["anbima"]["enquadrado"].all())
        ok = all(enqs) and anb_ok
        est = f"<span class='estado {'ok' if ok else 'risco'}'>{'enquadrado' if ok else 'critério fora'}</span>"
    cc = an["cc"]
    cotiz = "fechado" if cc >= FECHADO else f"cotização D+{cc}"
    return (f"<button class='fundo-card' data-fundo='{slug}' aria-current='{'true' if ativa else 'false'}'>"
            f"<div class='nome'>{_e(f.codinome_beyond or f.nome_interno)}</div>"
            f"<div class='cod'>{_e(f.nome_interno)} · {_e(f.cnpj)} · {_e(f.administrador or '')} · {_e(cotiz)}</div>"
            f"<div class='ancoras num'>{anc}</div>{est}</button>")


CSS = """
:root{--azul:#002346;--azul-escuro:#001A33;--neon:#00E1FF;--blue3:#215EFF;--blue7:#94C9FF;--blue9:#D1E8FF;--blue10:#EBF5FF;--blue11:#F5FAFF;
--grey1:#636363;--grey2:#8C8C8C;--grey4:#EFEFF0;--ok:#679940;--atencao:#C6B261;--risco:#C52225;
--bg:#fff;--bg-soft:var(--blue11);--fg:var(--azul);--muted:var(--grey1);--faint:var(--grey2);--border:var(--blue9);--accent:var(--blue3);--bar:var(--azul);--grid:var(--grey4);--card:#fff;
--font:'Wix Madefor Display','Aptos','Segoe UI',system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--font);line-height:1.5;font-size:15px}
.wrap{max-width:1120px;margin:0 auto;padding-inline:20px;padding-block:0 48px}
h1,h2,h3{margin:0;font-weight:500;letter-spacing:-.01em}h1{font-size:clamp(1.7rem,3.6vw,2.4rem);font-weight:400;line-height:1.1}h2{font-size:1.35rem}h3{font-size:1rem;font-weight:600}p{margin:0}
.eyebrow{font-size:.72rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}.muted{color:var(--muted)}.small{font-size:.82rem}.num{font-variant-numeric:tabular-nums}
.hero{background:var(--azul);color:#fff;position:relative;overflow:hidden}.hero .wrap{padding-block:28px 36px}
.hero-top{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}.hero-top svg{height:30px;width:auto;display:block}
.hero .eyebrow{color:var(--neon)}.hero h1{margin-top:22px;max-width:40ch}.hero h1 .d{color:var(--neon)}.hero .sub{margin-top:8px;color:#B3D9FF;max-width:62ch}
.capa{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:26px}
.fundo-card{background:var(--azul-escuro);border:1px solid #1C3C66;border-radius:12px;padding:16px 18px;min-width:0;color:#fff;text-align:left;font:inherit;cursor:pointer}
.fundo-card:hover,.fundo-card:focus-visible{border-color:var(--neon);outline:none}.fundo-card[aria-current="true"]{border-color:var(--neon);box-shadow:inset 0 0 0 1px var(--neon)}
.fundo-card .nome{font-size:1.05rem;font-weight:600}.fundo-card .cod{color:#94C9FF;font-size:.78rem}
.fundo-card .ancoras{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:12px}.fundo-card .ancoras b{display:block;font-size:1.05rem;font-weight:600}.fundo-card .ancoras span{font-size:.72rem;color:#94C9FF}
.estado{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:2px 10px;font-size:.75rem;font-weight:600;margin-top:10px}.estado::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.estado.ok{background:#E8F1DE;color:#3F6A1E}.estado.atencao{background:#F6F1DC;color:#6E5E12}.estado.risco{background:#F9DCDC;color:#8E1416}.estado.pendente{background:#E6EEF7;color:#2C4A72}
.hero .estado.ok{background:rgba(103,153,64,.22);color:#B1D397}.hero .estado.risco{background:rgba(197,34,37,.25);color:#F9B9BA}.hero .estado.pendente{background:rgba(148,201,255,.15);color:#94C9FF}
.insumos{display:flex;gap:18px;flex-wrap:wrap;margin-top:20px;font-size:.8rem;color:#94C9FF}.insumos b{color:#fff;font-weight:500}
.pagina{display:none;padding-top:30px}.pagina[data-ativa="true"]{display:block}
.cab{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;padding-bottom:14px;border-bottom:1px solid var(--border)}
.bloco{margin-top:34px}.bloco-cab{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:14px}.bloco-cab .eyebrow{min-width:64px}
.grid{display:grid;gap:14px}.g2{grid-template-columns:repeat(2,minmax(0,1fr))}.g3{grid-template-columns:repeat(3,minmax(0,1fr))}.g5{grid-template-columns:repeat(5,minmax(0,1fr))}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;min-width:0}
.kpi .r{font-size:.78rem;color:var(--muted)}.kpi .v{font-size:1.45rem;font-weight:600;margin-top:2px;line-height:1.15}.kpi .l{font-size:.78rem;color:var(--faint);margin-top:6px}
.kpi.ok .v{color:#3F6A1E}.kpi.risco .v{color:var(--risco)}
table{width:100%;border-collapse:collapse;font-size:.88rem}th{text-align:left;font-weight:600;font-size:.74rem;letter-spacing:.04em;text-transform:uppercase;color:#fff;background:var(--azul);padding:8px 10px}
td{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}tbody tr:nth-child(even) td{background:var(--bg-soft)}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}.tabela{overflow-x:auto}
.nota{font-size:.82rem;color:var(--muted);margin-top:10px;max-width:72ch}.aviso{margin-top:10px;font-size:.85rem;color:#6E5E12;background:#F6F1DC;border-radius:8px;padding:8px 12px}
.premissa{display:flex;gap:10px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--border)}.premissa:last-child{border-bottom:0}
.premissa .tag{flex:0 0 auto;font-size:.7rem;font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:2px 8px;border-radius:999px;background:var(--blue10);color:var(--azul)}
.bul{margin:6px 0 0;padding-left:18px;font-size:.82rem;columns:2;column-gap:24px}
svg text{font-family:var(--font)}.gr{width:100%;height:auto;display:block}.gr .t{fill:var(--fg)}.gr .f{fill:var(--faint)}.gr .m{fill:var(--muted)}.gr .b{fill:var(--bar)}.gr .g{stroke:var(--grid);stroke-width:1}.gr .vazio{fill:none;stroke:var(--border);stroke-dasharray:3 3}
.legenda{display:flex;gap:16px;flex-wrap:wrap;font-size:.78rem;color:var(--muted);margin-top:8px}.legenda i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.legenda i.ok{background:var(--ok)}.legenda i.atencao{background:var(--atencao)}.legenda i.risco{background:var(--risco)}
.cob{display:grid;grid-template-columns:repeat(9,minmax(0,1fr));gap:6px}.cob .v{border-radius:8px;padding:10px 6px;text-align:center;min-width:0}.cob .v b{display:block;font-size:1rem}.cob .v span{font-size:.7rem;opacity:.85}
.cob .ok{background:#E8F1DE;color:#2F5A12}.cob .atencao{background:#F6F1DC;color:#6E5E12}.cob .risco{background:#F9DCDC;color:#8E1416}.cob .pendente{background:var(--blue10);color:var(--azul)}
.rodape{margin-top:44px;padding-top:16px;border-top:1px solid var(--border);display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font-size:.78rem;color:var(--faint)}
@media (max-width:760px){.capa,.g3,.g5,.g2{grid-template-columns:1fr}.cob{grid-template-columns:repeat(3,minmax(0,1fr))}.bul{columns:1}}
"""

JS = """
(function(){const cards=[...document.querySelectorAll('.fundo-card')],pags=[...document.querySelectorAll('.pagina')];
function mostrar(id){pags.forEach(p=>p.dataset.ativa=String(p.dataset.fundo===id));cards.forEach(c=>c.setAttribute('aria-current',String(c.dataset.fundo===id)));
try{if(location.hash.slice(1)!==id)history.replaceState(null,'','#'+id);}catch(e){}}
cards.forEach(c=>c.addEventListener('click',()=>{mostrar(c.dataset.fundo);document.querySelector('main').scrollIntoView({behavior:'smooth',block:'start'});}));
const h=location.hash.slice(1);if(cards.some(c=>c.dataset.fundo===h))mostrar(h);})();
"""


def montar(paginas: list[tuple[dict, dict]], data_base: dt.date, insumos: dict) -> str:
    """HTML completo. `paginas` = [(ctx, analise)] na ordem da capa."""
    slugs = [f"f{i}" for i in range(len(paginas))]
    capa = "".join(_card_capa(c, a, s, i == 0) for i, ((c, a), s) in enumerate(zip(paginas, slugs)))
    corpo = "".join(_pagina_fundo(c, a, data_base, s, i == 0) for i, ((c, a), s) in enumerate(zip(paginas, slugs)))
    ins = "".join(f"<span>{_e(k)} <b>{_e(v)}</b></span>" for k, v in insumos.items())
    return (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Liquidez Multiplica</title>"
        "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Wix+Madefor+Display:wght@400;500;600;700&display=swap'>"
        f"<style>{CSS}</style></head><body>"
        f"<header class='hero'><div class='wrap'><div class='hero-top'>{_logo()}"
        f"<div class='small' style='color:#94C9FF'>Risco de liquidez · posição <b style='color:#fff;font-weight:500'>{data_base:%d/%m/%Y}</b> · gerado {dt.date.today():%d/%m/%Y}</div></div>"
        "<p class='eyebrow' style='margin-top:26px'>comitê de risco · fidc</p>"
        "<h1>liquidez dos fundos: o que vence <span class='d'>cobre o que pode sair?</span></h1>"
        "<p class='sub'>Uma página por fundo, na ordem da pergunta do comitê: enquadramento, de onde vem a liquidez, o que pode sair, cobertura por vértice, estresse e as premissas que sustentam cada número.</p>"
        f"<div class='capa'>{capa}</div><div class='insumos'>{ins}</div></div></header>"
        f"<main class='wrap'>{corpo}"
        f"<div class='rodape'><span>Multiplica · risco de liquidez · fonte: datalake (carteira, passivo) e riscogc (estoque, Beyond, curva) · posição {data_base:%d/%m/%Y}</span>"
        "<span>gerado por python -m liquidez apresentacao · plano em docs/plano-apresentacao.md</span></div></main>"
        f"<script>{JS}</script></body></html>"
    )


def gerar(fundos: list, destino_bi: Path = ft.DIR_BI, saida_dados: Path = Path("dados"),
          saida: Path = ARQUIVO_PADRAO, data: str | None = None) -> Path:
    """Gera a apresentacao para a lista de fichas, na data dos fatos (ou `data`)."""
    datas = ft.datas_gravadas("fato_indicadores", destino_bi)
    if not datas:
        raise FileNotFoundError(f"Camada de fatos vazia em {Path(destino_bi).resolve()}. Rode 'fatos gerar'.")
    data = data or datas[-1]
    data_base = dt.date.fromisoformat(str(data)[:10])
    paginas = []
    for f in fundos:
        ctx = carregar_fundo(f, data, Path(destino_bi), Path(saida_dados))
        for a in ctx["avisos"]:
            log.warning("%s: %s", f.nome_interno, a)
        paginas.append((ctx, analisar(ctx, data_base)))
    insumos = {"fatos": data, "fundos": str(len(fundos)),
               "beyond": ", ".join(f"{c['fundo'].codinome_beyond or '-'} {len(c['beyond'])} op." for c, _ in paginas)}
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(montar(paginas, data_base, insumos), encoding="utf-8")
    log.info("Apresentacao gerada: %s (%d fundos, posicao %s)", saida, len(fundos), data)
    return saida
