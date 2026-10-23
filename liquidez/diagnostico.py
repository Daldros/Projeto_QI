"""Resumo da composicao da carteira extraida, para calibrar o recorte de produto.

O codigo do gestor seleciona as operacoes a projetar por `Base_Origem == "DC"`
(`fluxo.posicoes_do_produto`). No riscogc a `HOD_TB_CARTEIRA_UNIFICADA` nao
traz esse rotulo -- na ultima leitura so havia "Renda Fixa" e "TESOURARIA" --
e por isso o `fluxo` do MULTIPLICA FIDC nao achou nada para projetar. Antes
de trocar o filtro e preciso ver o que a carteira de fato carrega: este modulo
le a base local (sem banco) e agrupa por Base_Origem x Modalidade, com
exemplos de codigo, para a decisao ser tomada sobre dado e nao sobre palpite.
"""

from __future__ import annotations

import pandas as pd

COLUNAS_GRUPO = ("Base_Origem", "Modalidade")
COLUNAS_EXEMPLO = ("Codigo_Op", "Codigo_Ativo", "Nome_Ativo", "Emissor")
COL_VALOR = "Valor_Liquido"


def _texto(serie: pd.Series) -> pd.Series:
    return serie.fillna("(nulo)").astype(str).str.strip().replace("", "(vazio)")


def _exemplos(serie: pd.Series, n: int) -> str:
    valores = _texto(serie)
    valores = valores[valores != "(nulo)"]
    distintos = valores.drop_duplicates().head(n).tolist()
    return ", ".join(distintos) if distintos else "-"


def resumir_carteira(df: pd.DataFrame, exemplos: int = 3) -> list[dict]:
    """Uma linha por Base_Origem x Modalidade: linhas, operacoes, valor, exemplos."""
    if df.empty:
        return []
    grupo = [c for c in COLUNAS_GRUPO if c in df.columns]
    if not grupo:
        raise KeyError(f"Carteira sem as colunas {COLUNAS_GRUPO}; presentes: {list(df.columns)}")
    base = df.copy()
    for coluna in grupo:
        base[coluna] = _texto(base[coluna])
    saida: list[dict] = []
    for chave, parte in base.groupby(grupo, sort=False):
        chave = chave if isinstance(chave, tuple) else (chave,)
        linha = dict(zip(grupo, chave))
        linha["linhas"] = int(len(parte))
        if "Codigo_Op" in parte:
            ops = _texto(parte["Codigo_Op"])
            linha["operacoes"] = int(ops[ops != "(nulo)"].nunique())
        if COL_VALOR in parte:
            linha["valor"] = float(pd.to_numeric(parte[COL_VALOR], errors="coerce").fillna(0).sum())
        for coluna in COLUNAS_EXEMPLO:
            if coluna in parte:
                linha[f"ex_{coluna}"] = _exemplos(parte[coluna], exemplos)
        saida.append(linha)
    saida.sort(key=lambda l: -l.get("valor", 0))
    return saida


def distribuicao(df: pd.DataFrame, coluna: str, limite: int = 12) -> list[tuple[str, int]]:
    """Valores distintos de uma coluna com a contagem, os mais frequentes primeiro."""
    if coluna not in df.columns:
        return []
    contagem = _texto(df[coluna]).value_counts()
    return [(str(k), int(v)) for k, v in contagem.head(limite).items()]


def formatar(resumo: list[dict], df: pd.DataFrame) -> str:
    linhas = []
    total_valor = sum(l.get("valor", 0) for l in resumo)
    linhas.append(f"Linhas ........ {len(df)}")
    if "Codigo_Op" in df.columns:
        sem_op = int(_texto(df["Codigo_Op"]).eq("(nulo)").sum())
        linhas.append(f"Sem Codigo_Op . {sem_op}")
    if "Data_Aquisicao" in df.columns:
        datas = pd.to_datetime(df["Data_Aquisicao"], errors="coerce").dropna()
        if not datas.empty:
            linhas.append(f"Aquisicao ..... {datas.min():%d/%m/%Y} a {datas.max():%d/%m/%Y}")
    linhas.append(f"Valor Liquido . {total_valor:,.2f}")
    linhas.append("")
    linhas.append("Base_Origem x Modalidade (maior valor primeiro)")
    for l in resumo:
        chave = " | ".join(str(l[c]) for c in COLUNAS_GRUPO if c in l)
        pct = (l.get("valor", 0) / total_valor * 100) if total_valor else 0
        linhas.append(f"  {chave}")
        linhas.append(f"     linhas {l['linhas']:>5}  operacoes {l.get('operacoes', '-'):>5}"
                      f"  valor {l.get('valor', 0):>18,.2f}  ({pct:5.1f}%)")
        for coluna in COLUNAS_EXEMPLO:
            if f"ex_{coluna}" in l:
                linhas.append(f"     {coluna:<13} {l[f'ex_{coluna}']}")
    for coluna in ("Status", "Origem"):
        dist = distribuicao(df, coluna)
        if dist:
            linhas.append("")
            linhas.append(f"{coluna}: " + " | ".join(f"{k}={v}" for k, v in dist))
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Cobertura do estoque (BD) sobre as operacoes de DC da carteira
# ---------------------------------------------------------------------------
#
# O gestor mediu no GAD (QI) que o `Codigo_Op` da carteira E o `ID_Recebivel`
# do estoque: o recebivel pulverizado (duplicata, boleto, escrow) ja esta no BD
# com Data_Vencimento e Valor_Presente -- ou seja, o cronograma das operacoes
# que hoje ficam bullet (DIRPX, SRP, MEZAN, CONFD) pode vir dali, sem Beyond.
# Antes de ligar isso ao fluxo, e preciso saber se a chave casa tambem nos
# fundos da SINGULARE (ID_Recebivel = dsSeuNumero + '_' + dsNuDocumento na
# carga do Datalake) e quanto do valor da carteira o estoque explica.

COL_ID_RECEBIVEL = "ID_Recebivel"
COL_VALOR_PRESENTE = "Valor_Presente"


def _familia(codigo_ativo) -> str:
    texto = str(codigo_ativo or "").strip().upper()
    return texto if texto else "(sem codigo)"


def cobertura_estoque(carteira: pd.DataFrame, bd: pd.DataFrame, produto: str = "DC") -> dict:
    """Quanto do DC da carteira tem recebivel correspondente no BD.

    Casa `Codigo_Op` com `ID_Recebivel` de duas formas: exata e pelo PREFIXO
    antes do primeiro '_' (formato da carga do Datalake). Devolve, por familia
    de `Codigo_Ativo`, operacoes, operacoes casadas, valor da carteira, valor
    presente casado e numero de recebiveis -- e os exemplos de chave de cada lado.
    """
    if carteira.empty:
        return {"familias": [], "exemplos_carteira": [], "exemplos_bd": [], "casamento": "nenhum"}
    # A carga crua ainda traz "Renda Fixa": o de-para de produto e o mesmo do
    # tratar_fundo, senao o recorte de DC sai vazio (foi o que aconteceu em 05/10).
    from .tratamentos import aplicar_depara_produtos, carregar_depara_produtos
    carteira = aplicar_depara_produtos(carteira, carregar_depara_produtos())
    base = carteira[_texto(carteira["Base_Origem"]).str.upper() == produto.upper()].copy()
    base["_op"] = _texto(base["Codigo_Op"]).str.upper()
    base["_fam"] = base["Codigo_Ativo"].map(_familia)
    base["_valor"] = pd.to_numeric(base.get(COL_VALOR, 0), errors="coerce").fillna(0)

    ids = _texto(bd[COL_ID_RECEBIVEL]).str.upper() if (not bd.empty and COL_ID_RECEBIVEL in bd) else pd.Series(dtype=str)
    vp = pd.to_numeric(bd.get(COL_VALOR_PRESENTE, 0), errors="coerce").fillna(0) if not bd.empty else pd.Series(dtype=float)
    por_exato = vp.groupby(ids).agg(["sum", "size"]) if len(ids) else pd.DataFrame(columns=["sum", "size"])
    prefixo = ids.str.split("_").str[0] if len(ids) else ids
    por_prefixo = vp.groupby(prefixo).agg(["sum", "size"]) if len(ids) else pd.DataFrame(columns=["sum", "size"])

    exatos = int(base["_op"].isin(por_exato.index).sum())
    prefixos = int(base["_op"].isin(por_prefixo.index).sum())
    tabela, chave = (por_exato, "exato") if exatos >= prefixos else (por_prefixo, "prefixo antes de '_'")
    if exatos == 0 and prefixos == 0:
        chave = "nenhum"

    familias = []
    for fam, parte in base.groupby("_fam", sort=False):
        ops = parte.groupby("_op")["_valor"].sum()
        casadas = ops.index[ops.index.isin(tabela.index)]
        familias.append({
            "familia": fam,
            "operacoes": int(len(ops)),
            "casadas": int(len(casadas)),
            "valor_carteira": float(ops.sum()),
            "valor_casado": float(ops.loc[casadas].sum()),
            "valor_presente_bd": float(tabela.loc[casadas, "sum"].sum()) if len(casadas) else 0.0,
            "recebiveis": int(tabela.loc[casadas, "size"].sum()) if len(casadas) else 0,
        })
    familias.sort(key=lambda f: -f["valor_carteira"])
    return {
        "casamento": chave,
        "familias": familias,
        "exemplos_carteira": base["_op"].drop_duplicates().head(5).tolist(),
        "exemplos_bd": ids.drop_duplicates().head(5).tolist() if len(ids) else [],
        "bd_linhas": int(len(bd)),
        "bd_tipos": distribuicao(bd, "Tipo_Recebivel", 8) if not bd.empty else [],
        "bd_exemplos_por_tipo": _exemplos_por_tipo(bd),
        "bd_status": distribuicao(bd, "Status_Recebivel", 6) if not bd.empty else [],
    }


def _exemplos_por_tipo(bd: pd.DataFrame, n: int = 2) -> list[tuple[str, str]]:
    """Dois ID_Recebivel por Tipo_Recebivel: mostra o formato da chave de cada fonte."""
    if bd.empty or "Tipo_Recebivel" not in bd or COL_ID_RECEBIVEL not in bd:
        return []
    saida = []
    for tipo, parte in bd.groupby(_texto(bd["Tipo_Recebivel"]), sort=False):
        saida.append((str(tipo), _exemplos(parte[COL_ID_RECEBIVEL], n)))
    return saida


def formatar_cobertura(r: dict) -> str:
    linhas = [f"Chave que casa ....... {r['casamento']}",
              f"BD .................. {r.get('bd_linhas', 0)} recebiveis",
              f"   Codigo_Op (carteira) ex.: {', '.join(r['exemplos_carteira']) or '-'}",
              f"   ID_Recebivel (BD)   ex.: {', '.join(r['exemplos_bd']) or '-'}"]
    if r.get("bd_tipos"):
        linhas.append("   Tipo_Recebivel: " + " | ".join(f"{k}={v}" for k, v in r["bd_tipos"]))
    for tipo, ex in r.get("bd_exemplos_por_tipo", []):
        linhas.append(f"      {tipo:<24} {ex}")
    if r.get("bd_status"):
        linhas.append("   Status_Recebivel: " + " | ".join(f"{k}={v}" for k, v in r["bd_status"]))
    linhas.append("")
    linhas.append(f"{'Familia':<10} {'ops':>5} {'casadas':>7} {'valor carteira':>18} {'valor casado':>18} {'VP no BD':>18} {'receb.':>7}")
    tot = {"operacoes": 0, "casadas": 0, "valor_carteira": 0.0, "valor_casado": 0.0, "valor_presente_bd": 0.0, "recebiveis": 0}
    for f in r["familias"]:
        linhas.append(f"{f['familia']:<10} {f['operacoes']:>5} {f['casadas']:>7} {f['valor_carteira']:>18,.2f}"
                      f" {f['valor_casado']:>18,.2f} {f['valor_presente_bd']:>18,.2f} {f['recebiveis']:>7}")
        for k in tot:
            tot[k] += f[k]
    linhas.append(f"{'TOTAL':<10} {tot['operacoes']:>5} {tot['casadas']:>7} {tot['valor_carteira']:>18,.2f}"
                  f" {tot['valor_casado']:>18,.2f} {tot['valor_presente_bd']:>18,.2f} {tot['recebiveis']:>7}")
    return "\n".join(linhas)
