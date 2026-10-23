"""Painel diario de liquidez em HTML, gerado a partir da camada de fatos.

Autocontido: um arquivo que abre do disco, sem servidor, sem CDN, sem
biblioteca de grafico. Os fatos ficam no parquet; aqui entra apenas o que vai
ser desenhado, ja agregado. Mesmo padrao de `documentacao.py`.

Graficos sao SVG montado em Python. Um `file://` nao carrega CDN, e embutir uma
biblioteca de grafico pesaria mais do que todo o resto do painel.

Cores: um hue sequencial (azul) para magnitude, e a paleta de status reservada
para enquadramento -- que aqui significa literalmente bom/ruim. Status nunca
aparece sozinho: vem sempre com rotulo, porque cor nao pode carregar o sentido.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
from pathlib import Path

import pandas as pd

from . import fatos as ft

log = logging.getLogger(__name__)

ARQUIVO_PADRAO = Path("painel.html")
LOGO = Path("config") / "marca" / "multiplica.png"

# Os relatorios individuais ficam numa subpasta ao lado do painel, e os links
# entre eles sao relativos -- a pasta inteira pode ser copiada ou colocada num
# compartilhamento sem quebrar nada.
PASTA_FUNDOS = "fundos"


def arquivo_do_fundo(cnpj) -> str:
    """Nome do arquivo do relatorio individual: o CNPJ so com digitos.

    A pontuacao do CNPJ nao serve para nome de arquivo, e o nome do fundo muda
    com o tempo -- o CNPJ nao. Assim o link de ontem continua valendo amanha.
    """
    import re

    return re.sub(r"\D", "", str(cnpj)) + ".html"

# --- Marca ------------------------------------------------------------------
# Tema da Multiplica, extraido do `ppt/theme/theme2.xml` das apresentacoes do
# comite -- nao estimado de imagem. Navy do fundo, azul dos titulos, ciano do
# logo, e a rampa clara de apoio.
NAVY, AZUL, CIANO = "#002346", "#0063c7", "#00e1ff"
NAVY_ESCURO = "#001a33"
AZUL_CLARO, AZUL_PALIDO, QUASE_BRANCO = "#41a1ff", "#93c9ff", "#f5faff"

# Cinzas derivados do proprio navy, para a tinta secundaria nao brigar com a
# marca. Contrastes conferidos sobre cada superficie (o menor e 3,24:1).
CORES = {
    "claro": {
        "superficie": "#ffffff", "plano": QUASE_BRANCO,
        "tinta": NAVY, "tinta2": "#597087", "tinta3": "#8091a2",
        "grade": "#d1e8ff", "eixo": AZUL_PALIDO,
        "serie": AZUL,
        # Status fica na paleta reservada, nao no vermelho/amarelo puros do
        # deck: #ffff00 sobre branco da 1,07:1 -- ilegivel. O sentido (vermelho
        # = ruim) se mantem, o tom e que muda.
        "bom": "#0ca30c", "atencao": "#fab219", "critico": "#d03b3b",
        "neutro": "#8091a2",
        "borda": "rgba(0,35,70,0.12)",
    },
    "escuro": {
        "superficie": NAVY, "plano": NAVY_ESCURO,
        "tinta": "#ffffff", "tinta2": AZUL_PALIDO, "tinta3": "#738699",
        "grade": "#1a3959", "eixo": "#294664",
        "serie": AZUL_CLARO,
        "bom": "#0ca30c", "atencao": "#fab219", "critico": "#d03b3b",
        "neutro": "#738699",
        "borda": "rgba(255,255,255,0.12)",
    },
}


def _logo_embutido(caminho: Path = LOGO) -> str:
    """Logo como data URI. Sem arquivo, o painel sai so com o texto."""
    import base64

    caminho = Path(caminho)
    if not caminho.exists():
        log.warning("Logo nao encontrado em %s; painel sai sem a marca.", caminho)
        return ""
    dados = base64.b64encode(caminho.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{dados}"

# --- Limite do Caixa Potencial / PL ----------------------------------------
# 15% e o piso definido pela area. A faixa de atencao (ate 20%) marca quem esta
# dentro mas com pouca folga -- e escolha de apresentacao, nao regra: mudar aqui
# nao afeta calculo nenhum, so a cor da barra.
LIMITE_CAIXA_PL = 0.15
ATENCAO_CAIXA_PL = 0.20

# Teto da escala do grafico. O indice vai de -12% a 284%, e num eixo linear ate
# o maximo a faixa 0-30% -- onde esta o limite e a maioria dos fundos --
# ocuparia 14% da largura. Com teto, a resolucao fica onde importa; quem passa
# do teto e desenhado ate a borda com marca de corte e o valor real no rotulo.
# Acima do limite, a diferenca entre 60% e 280% nao muda decisao nenhuma.
TETO_CAIXA_PL = 0.50

# Coluna do enquadramento, rotulo, coluna do valor que gerou a marca e o limite.
# Os quatro primeiros vem prontos da etapa de indicadores; Caixa/PL nao tem
# coluna de enquadramento na origem e e derivado aqui, contra LIMITE_CAIXA_PL.
# O numerador e o mesmo nos cinco -- caixa potencial -- entao vai escrito por
# extenso: "Cx" economizava espaco e escondia de que caixa se trata.
CRITERIOS = (
    ("enq_caixa_pl", "Caixa Potencial / PL", "≥ 15%", "caixa_pl", LIMITE_CAIXA_PL),
    ("enq_cotista", "Caixa Potencial / Cotista", "> 15%", "cx_cotista", 0.15),
    ("enq_gdp", "Caixa Potencial / GDP", "> 15%", "cx_gdp", 0.15),
    ("enq_distribuidor", "Caixa Potencial / Distribuidor", "> 1", "cx_distribuidor", 1.0),
    ("enq_rco", "Caixa Potencial / Resgate", "> 1", "caixa_resgate", 1.0),
)

FAIXAS = (
    (0, 0, "0"), (1, 7, "1–7"), (8, 15, "8–15"), (16, 29, "16–29"),
    (30, 59, "30–59"), (60, 89, "60–89"), (90, 10**9, "90+"),
)


# --- Formatacao -------------------------------------------------------------

def _n(valor, casas: int = 2) -> str:
    """Numero no formato pt-BR."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return "—"
    texto = f"{float(valor):,.{casas}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _pct(valor, casas: int = 2) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return "—"
    return _n(float(valor) * 100, casas) + "%"


def _mil(valor, casas: int = 0) -> str:
    """Valor em R$ MIL. A notacao de dinheiro da publicacao inteira.

    Painel e relatorio do fundo escrevem tudo assim. Antes as tabelas diziam
    "PL (R$ mil)" e os graficos ao lado traziam reais cheios -- duas grandezas
    da mesma coisa na mesma pagina, e a diferenca entre elas e de mil vezes.

    Milhar e nao real cheio porque carteira de fundo se lê em milhares: os tres
    digitos finais nao mudam decisao nenhuma e atrapalham a comparacao visual.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return "—"
    return _n(float(valor) / 1000, casas)


def _mil_rs(valor, casas: int = 0) -> str:
    """`_mil` com "R$" e "mil" por extenso, para tooltip e texto corrido."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return "—"
    return f"R$ {_mil(valor, casas)} mil"


def _data_br(texto) -> str:
    try:
        return dt.date.fromisoformat(str(texto)[:10]).strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return str(texto)


def _e(texto) -> str:
    return html.escape("" if texto is None else str(texto))


# --- Leitura da camada de fatos --------------------------------------------

def _ler(tabela: str, data: str, destino: Path) -> pd.DataFrame:
    caminho = ft.caminho_particao(tabela, data, destino)
    return pd.read_parquet(caminho) if caminho.exists() else pd.DataFrame()


def _derivar_enq_caixa_pl(df: pd.DataFrame) -> pd.DataFrame:
    """Enquadramento de Caixa/PL, que a etapa de indicadores nao calcula.

    Fica aqui e nao no fato para nao exigir reprocessamento: o limite e uma
    leitura do painel sobre um valor que ja esta gravado. Quem nao tem Caixa/PL
    continua n/d -- ausencia de dado nao e desenquadramento.
    """
    if df.empty or "caixa_pl" not in df.columns:
        return df
    valor = pd.to_numeric(df["caixa_pl"], errors="coerce")
    return df.assign(
        enq_caixa_pl=valor.ge(LIMITE_CAIXA_PL).where(valor.notna())
    )


def carregar(destino: Path = ft.DIR_BI) -> dict:
    """Junta o que o painel precisa: a data mais recente e a anterior."""
    datas = ft.datas_gravadas("fato_indicadores", destino)
    if not datas:
        raise FileNotFoundError(
            f"Camada de fatos vazia em {Path(destino).resolve()}. "
            f"Rode 'python -m liquidez fatos gerar' primeiro."
        )
    atual, anterior = datas[-1], (datas[-2] if len(datas) > 1 else None)
    ind = _so_ativos(_derivar_enq_caixa_pl(_ler("fato_indicadores", atual, destino)),
                     destino)
    return {
        "datas": datas,
        "data": atual,
        "data_anterior": anterior,
        "indicadores": ind,
        "indicadores_ant": _so_ativos(_derivar_enq_caixa_pl(
            _ler("fato_indicadores", anterior, destino) if anterior else pd.DataFrame()),
            destino),
        "matriz": _ler("fato_matriz", atual, destino),
        "fluxo": _ler("fato_fluxo", atual, destino),
        "vol": _carregar_vol(destino, ind),
        "risco": _carregar_risco(destino),
    }


def _so_ativos(ind: pd.DataFrame, destino: Path) -> pd.DataFrame:
    """Tira do painel os fundos marcados como inativos no cadastro.

    O filtro vive AQUI, e nao so na geracao dos fatos, porque particao antiga
    nao volta a ser gerada: o LOGIC FIDC saiu da gestao da Multiplica em agosto
    de 2026, mas segue dentro das 44 particoes anteriores, e sem esta linha ele
    continuaria no painel enquanto a data-base fosse uma delas.

    A dimensao e a fonte: ela e reescrita inteira a cada rodada e por isso
    reflete o cadastro de agora, nao o do dia da particao.

    Ausencia da dimensao ou da coluna nao filtra nada -- o painel existe para
    mostrar liquidez, e sumir com fundo por falta de metadado seria pior que
    mostrar um a mais.
    """
    caminho = Path(destino) / "dim_fundo" / "dados.parquet"
    if ind.empty or not caminho.exists():
        return ind
    dim = pd.read_parquet(caminho)
    if "ativo" not in dim.columns or "cnpj_fundo" not in dim.columns:
        return ind
    inativos = set(dim.loc[dim["ativo"] == False, "cnpj_fundo"])  # noqa: E712
    if not inativos:
        return ind
    fora = ind["cnpj_fundo"].isin(inativos)
    if fora.any():
        log.info("Painel: %d fundo(s) inativo(s) fora da tabela.", int(fora.sum()))
    return ind[~fora]


def _carregar_risco(destino: Path) -> pd.DataFrame:
    """Retorno, vol e VaR por FUNDO, do composto das classes por `valorCota`.

    Todas as classes entram, inclusive senior e mezanino: retorno e risco saem
    da serie de COTAS, que mede cada classe pelo que ela de fato rendeu. A
    subordinacao muda o PL do fundo -- e isso e tratado na etapa 6, tirando a
    linha `PL COTAS` da carteira -- mas nao muda o que as cotas fizeram.

    Indexado pelo CNPJ so em digitos, que e a chave usada na tabela de fundos.
    Igual ao `_carregar_vol`: ausencia de serie nao e erro -- o painel nasceu
    para mostrar liquidez, e risco de mercado e acrescimo.
    """
    from . import varcota as vc

    try:
        serie = vc.carregar(destino)
        if serie.empty:
            return pd.DataFrame()
        met = vc.metricas_por_fundo(serie)
        if met.empty:
            return pd.DataFrame()
        met = met.copy()
        met["_d"] = met["cnpj_fundo"].astype(str).str.replace(r"\D", "", regex=True)
        return met.set_index("_d")
    except Exception:
        log.warning("Metricas de risco por fundo indisponiveis.", exc_info=True)
        return pd.DataFrame()


def _carregar_vol(destino: Path, ind: pd.DataFrame) -> list[dict]:
    """Volatilidade maxima por fundo, se a serie de cotas estiver nos fatos.

    Ausencia nao e erro: quem nunca rodou `cotas serie` ve o painel so com a
    liquidez. E falha no calculo tambem nao derruba nada -- risco de mercado e
    acrescimo, liquidez e o que o painel existe para mostrar.
    """
    from . import varcota as vc

    try:
        serie = vc.carregar(destino)
        if serie.empty:
            return []
        return _dados_vol(vc.vol_maxima_por_fundo(serie), ind)
    except Exception:
        log.exception("Falha ao calcular a volatilidade por fundo; "
                      "o painel sai so com a liquidez.")
        return []


# --- SVG --------------------------------------------------------------------

def _svg_barras(
    rotulos: list[str],
    valores: list[float],
    titulos: list[str],
    largura: int = 720,
    altura_barra: int = 26,
    formato=lambda v: _n(v, 0),
) -> str:
    """Barras horizontais, uma serie. Rotulo direto no fim de cada barra.

    Horizontal porque os rotulos de faixa e de mes sao longos; uma serie so,
    entao um hue sequencial e nao paleta categorica.
    """
    if not valores:
        return '<p class="vazio">Sem dados.</p>'
    maximo = max(valores) or 1
    esquerda, direita = 96, 132
    plot = largura - esquerda - direita
    altura = len(valores) * (altura_barra + 8) + 16

    partes = [
        f'<svg viewBox="0 0 {largura} {altura}" width="100%" height="{altura}" '
        f'role="img" class="gr">'
    ]
    for i, (rotulo, valor, titulo) in enumerate(zip(rotulos, valores, titulos)):
        y = 8 + i * (altura_barra + 8)
        comprimento = max(0.0, valor / maximo) * plot
        partes.append(
            f'<text x="{esquerda - 10}" y="{y + altura_barra * 0.68}" '
            f'text-anchor="end" class="rot">{_e(rotulo)}</text>'
        )
        # 4px de raio so na ponta do dado, ancorada na linha de base
        partes.append(
            f'<rect x="{esquerda}" y="{y}" width="{comprimento:.2f}" '
            f'height="{altura_barra}" rx="4" class="barra">'
            f'<title>{_e(titulo)}</title></rect>'
        )
        partes.append(
            f'<text x="{esquerda + comprimento + 8}" y="{y + altura_barra * 0.68}" '
            f'class="val">{_e(formato(valor))}</text>'
        )
    partes.append(
        f'<line x1="{esquerda}" y1="0" x2="{esquerda}" y2="{altura}" class="base"/>'
    )
    partes.append("</svg>")
    return "".join(partes)


def _svg_enquadramento(dados: list[dict], largura: int = 720) -> str:
    """Barra empilhada por criterio: enquadrado / desenquadrado / sem dado.

    Cores de status porque o dado E status. Cada segmento leva rotulo com o
    numero, entao a cor nunca carrega o sentido sozinha.
    """
    if not dados:
        return '<p class="vazio">Sem dados.</p>'
    # A faixa dos rotulos acompanha o texto: com os nomes por extenso, uma
    # margem fixa cortava "Caixa Potencial / Distribuidor" no meio.
    direita = 16
    esquerda = min(320, max(150, int(max(len(d["rotulo"]) for d in dados) * 7.2) + 16))
    alt_barra, espaco = 30, 14
    plot = largura - esquerda - direita
    altura = len(dados) * (alt_barra + espaco) + 8
    total = max((d["bom"] + d["ruim"] + d["nd"]) for d in dados) or 1

    partes = [
        f'<svg viewBox="0 0 {largura} {altura}" width="100%" height="{altura}" '
        f'role="img" class="gr">'
    ]
    for i, d in enumerate(dados):
        y = 4 + i * (alt_barra + espaco)
        x = esquerda
        partes.append(
            f'<text x="{esquerda - 12}" y="{y + alt_barra * 0.68}" text-anchor="end" '
            f'class="rot">{_e(d["rotulo"])}</text>'
        )
        for chave, classe, nome in (("bom", "seg-bom", "Enquadrado"),
                                    ("ruim", "seg-ruim", "Desenquadrado"),
                                    ("nd", "seg-nd", "Sem dado")):
            n = d[chave]
            if not n:
                continue
            w = n / total * plot
            partes.append(
                f'<rect x="{x:.2f}" y="{y}" width="{max(w - 2, 0.5):.2f}" '
                f'height="{alt_barra}" rx="4" class="{classe}">'
                f'<title>{_e(d["rotulo"])}: {n} {nome}</title></rect>'
            )
            if w > 26:  # so rotula dentro se couber com folga
                partes.append(
                    f'<text x="{x + w / 2 - 1:.2f}" y="{y + alt_barra * 0.68}" '
                    f'text-anchor="middle" class="dentro">{n}</text>'
                )
            x += w
    partes.append("</svg>")
    return "".join(partes)


def _banda(valor: float) -> tuple[str, str]:
    """Faixa do semaforo de um Caixa/PL. Devolve (classe, rotulo)."""
    if valor < LIMITE_CAIXA_PL:
        return "critico", "Abaixo do limite"
    if valor < ATENCAO_CAIXA_PL:
        return "atencao", "Pouca folga"
    return "bom", "Confortável"


def _svg_caixa_pl(dados: list[dict], largura: int = 720) -> str:
    """Caixa Potencial / PL por fundo, contra o limite.

    Um valor contra um limite e um medidor, nao uma barra solta: a linha do
    limite tem que aparecer no mesmo desenho, senao o leitor precisa comparar
    de cabeca. A cor diz a faixa e o rotulo repete em texto -- semaforo sem
    legenda escrita nao serve para quem nao distingue as cores.
    """
    if not dados:
        return '<p class="vazio">Nenhum fundo com Caixa/PL calculado.</p>'
    esquerda, direita = 214, 96
    alt, espaco = 13, 5
    plot = largura - esquerda - direita
    altura = len(dados) * (alt + espaco) + 34

    # Caixa Potencial pode ser NEGATIVO -- ha fundos assim. A escala precisa
    # comecar abaixo de zero quando for o caso, senao a barra negativa vira um
    # tracinho a direita do zero e o sinal, que e a informacao mais importante,
    # some. No topo, o teto: sem ele um fundo de 284% achata todo o resto.
    valores = [d["valor"] for d in dados]
    minimo = min(valores + [0.0])
    maximo = min(max(valores + [ATENCAO_CAIXA_PL * 1.15]), TETO_CAIXA_PL)
    faixa = maximo - minimo or 1.0

    def x_de(valor: float) -> float:
        return esquerda + (min(valor, maximo) - minimo) / faixa * plot

    x0, x_limite, x_atencao = x_de(0.0), x_de(LIMITE_CAIXA_PL), x_de(ATENCAO_CAIXA_PL)

    base, passo = 24, alt + espaco
    # `extra` e o que a altura tem alem das linhas -- a folga do topo e do
    # rodape. Guardado para o filtro recalcular a altura sem deixar vazio.
    extra = altura - base - len(dados) * passo
    partes = [
        f'<svg viewBox="0 0 {largura} {altura}" width="100%" height="{altura}" '
        f'role="img" class="gr" id="gr-caixa-pl" data-base="{base}" '
        f'data-passo="{passo}" data-extra="{extra}" data-largura="{largura}">'
    ]
    partes.append(
        f'<rect x="{x_limite:.1f}" y="20" width="{x_atencao - x_limite:.1f}" '
        f'height="{altura - 26}" class="faixa-at"/>'
    )
    # Cada fundo e um grupo posicionado por transform: ordenar e so reatribuir o
    # deslocamento vertical, sem redesenhar nada. As posicoes horizontais
    # dependem so da escala, que a ordenacao nao muda.
    for i, d in enumerate(dados):
        y = base + i * passo
        x_valor = x_de(d["valor"])
        # O piso de 1,5px existe para o valor quase-zero nao sumir, mas tem que
        # respeitar o lado: sem isso uma barra de -0,04% era empurrada para a
        # direita do zero e lia-se como positiva.
        if d["valor"] < 0:
            comprimento = max(x0 - x_valor, 1.5)
            inicio = x0 - comprimento
        else:
            comprimento = max(x_valor - x0, 1.5)
            inicio = x0
        classe, rotulo = _banda(d["valor"])
        cortada = d["valor"] > maximo
        # A linha inteira e um link para o relatorio do fundo. O <a> fica por
        # fora do <g>: a ordenacao mexe no transform do <g> e nao precisa saber
        # que ha um link em volta.
        alvo = f"{PASTA_FUNDOS}/{arquivo_do_fundo(d['cnpj'])}" if d.get("cnpj") else None
        partes.append(f'<a href="{alvo}">' if alvo else "")
        partes.append(
            f'<g class="linha" data-nome="{_e(d["fundo"])}" data-valor="{d["valor"]}" '
            f'data-fechado="{1 if d.get("fechado") else 0}" '
            # Sem `data-gestor` aqui o filtro de gestor comparava contra
            # undefined e escondia o painel inteiro. Ver `montarMedidor`.
            f'data-gestor="{_e(d.get("gestor") or "")}" '
            f'transform="translate(0,{y})">'
        )
        partes.append(
            f'<text x="{esquerda - 10}" y="{alt * 0.74:.1f}" text-anchor="end" '
            f'class="rot pequeno">{_e(d["fundo"][:30])}</text>'
        )
        partes.append(
            f'<rect x="{inicio:.2f}" y="0" width="{comprimento:.2f}" height="{alt}" '
            f'rx="4" class="med-{classe}">'
            f'<title>{_e(d["fundo"])}: {_pct(d["valor"])} — {rotulo}'
            + (f" (escala cortada em {_pct(maximo, 0)})" if cortada else "")
            + f'\nCaixa R$ {_n(d["caixa"], 0)} mil / PL R$ {_n(d["pl"], 0)} mil'
            f'</title></rect>'
        )
        if cortada:
            # Marca de corte: a barra nao termina ali, a escala e que acaba.
            ponta = inicio + comprimento
            partes.append(
                f'<path d="M{ponta + 3:.1f} 0 l7 {alt / 2:.1f} l-7 {alt / 2:.1f} z" '
                f'class="med-{classe} corte"/>'
            )
        partes.append(
            f'<text x="{max(x_valor, x0) + (14 if cortada else 8):.2f}" '
            f'y="{alt * 0.8:.1f}" class="val pequeno">'
            f'{_e(_pct(d["valor"], 1))}</text>'
        )
        partes.append("</g>")
        partes.append("</a>" if alvo else "")
    # Zero e limite sao referencias, em tinta neutra: nao competem com o status.
    if minimo < 0:
        partes.append(f'<line x1="{x0:.1f}" y1="16" x2="{x0:.1f}" y2="{altura - 6}" '
                      f'class="base"/>')
        partes.append(f'<text x="{x0:.1f}" y="11" text-anchor="middle" '
                      f'class="rot pequeno">0</text>')
    partes.append(
        f'<line x1="{x_limite:.1f}" y1="16" x2="{x_limite:.1f}" y2="{altura - 6}" '
        f'class="limite"/>'
    )
    partes.append(
        f'<text x="{x_limite:.1f}" y="11" text-anchor="middle" class="rot pequeno">'
        f'limite {_pct(LIMITE_CAIXA_PL, 0)}</text>'
    )
    partes.append("</svg>")
    return "".join(partes)


# Teto da escala da volatilidade. A mediana e 3,4% e o topo, 126% (o ORYX). Sem
# corte, esse unico fundo achataria os outros 70. A solucao e a mesma do
# Caixa/PL: a barra ganha uma seta dizendo que a ESCALA acabou, nao o valor, e o
# numero exato fica escrito ao lado.
TETO_VOL = 0.40

# Acima disso, um unico dia responde por mais da metade da variancia -- a
# volatilidade deixou de medir oscilacao recorrente e passou a medir o tamanho
# de um evento isolado. Nao invalida o numero; muda o que ele quer dizer.
CONC_ALTA = 0.50


def _svg_duplo(dados: list[dict], largura: int = 950) -> str:
    """Caixa/PL e VaR lado a lado, uma linha por fundo, na MESMA ordem.

    Dois paineis, nao dois eixos. Sobrepor as duas medidas no mesmo plano seria
    o erro classico de serie temporal com duas escalas: a proporcao entre as
    barras passaria a depender do enquadramento escolhido, e nao do dado. Aqui
    cada painel tem a sua escala e o seu zero; o que os dois compartilham e
    apenas a LINHA -- ou seja, o fundo.

    O ganho e a leitura horizontal: percorrer a linha do fundo responde de uma
    vez "tem caixa?" e "oscila quanto?", que hoje exige rolar a pagina e
    guardar a ordem de cabeca.

    Fundo sem serie de cota fica com o painel da direita vazio, e nao ausente da
    lista: sumir da linha faria parecer que ele nao tem risco, quando o que ele
    nao tem e medida.
    """
    if not dados:
        return '<p class="vazio">Nenhum fundo com Caixa/PL calculado.</p>'

    rotulo, plot_a, val_a, vao, plot_b, val_b = 200, 268, 62, 22, 268, 62
    x_a = rotulo
    x_b = rotulo + plot_a + val_a + vao
    alt, espaco = 13, 5
    topo = 40
    altura = len(dados) * (alt + espaco) + topo + 10

    # --- escala do painel A: Caixa/PL, com zero e teto, como no medidor solo
    vals_a = [d["valor"] for d in dados]
    min_a = min(vals_a + [0.0])
    max_a = min(max(vals_a + [ATENCAO_CAIXA_PL * 1.15]), TETO_CAIXA_PL)
    faixa_a = (max_a - min_a) or 1.0

    def xa(v: float) -> float:
        return x_a + (min(v, max_a) - min_a) / faixa_a * plot_a

    # --- escala do painel B: volatilidade, sempre de zero
    vals_b = [d["var"] for d in dados if d.get("var") is not None]
    max_b = min(max(vals_b + [0.05]), TETO_VOL) if vals_b else 0.05

    def xb(v: float) -> float:
        return x_b + min(v, max_b) / (max_b or 1.0) * plot_b

    a0, a_lim, a_at = xa(0.0), xa(LIMITE_CAIXA_PL), xa(ATENCAO_CAIXA_PL)

    base, passo = topo, alt + espaco
    extra = altura - base - len(dados) * passo
    p = [
        f'<svg viewBox="0 0 {largura} {altura}" width="100%" height="{altura}" '
        f'role="img" class="gr" id="gr-duplo" data-base="{base}" '
        f'data-passo="{passo}" data-extra="{extra}" data-largura="{largura}">'
    ]
    # Titulo de cada painel: sem isso as duas metades parecem a mesma medida.
    p.append(f'<text x="{x_a}" y="13" class="rot titulo-painel">'
             f'Caixa Potencial / PL</text>')
    p.append(f'<text x="{x_b}" y="13" class="rot titulo-painel">'
             f'Volatilidade anual — classe mais volátil</text>')
    p.append(f'<rect x="{a_lim:.1f}" y="{topo - 8}" width="{a_at - a_lim:.1f}" '
             f'height="{altura - topo - 2}" class="faixa-at"/>')

    for i, d in enumerate(dados):
        y = base + i * passo
        alvo = f"{PASTA_FUNDOS}/{arquivo_do_fundo(d['cnpj'])}" if d.get("cnpj") else None
        p.append(f'<a href="{alvo}">' if alvo else "")
        p.append(
            f'<g class="linha" data-nome="{_e(d["fundo"])}" data-valor="{d["valor"]}" '
            f'data-var="{d["var"] if d.get("var") is not None else ""}" '
            f'data-fechado="{1 if d.get("fechado") else 0}" '
            f'data-gestor="{_e(d.get("gestor") or "")}" '
            f'transform="translate(0,{y})">'
        )
        p.append(f'<text x="{rotulo - 10}" y="{alt * 0.74:.1f}" text-anchor="end" '
                 f'class="rot pequeno">{_e(d["fundo"][:28])}</text>')

        # --- painel A -------------------------------------------------------
        xv = xa(d["valor"])
        if d["valor"] < 0:
            comp = max(a0 - xv, 1.5)
            ini = a0 - comp
        else:
            comp = max(xv - a0, 1.5)
            ini = a0
        classe, faixa_txt = _banda(d["valor"])
        cortada = d["valor"] > max_a
        p.append(
            f'<rect x="{ini:.2f}" y="0" width="{comp:.2f}" height="{alt}" rx="4" '
            f'class="med-{classe}"><title>{_e(d["fundo"])}: '
            f'Caixa/PL {_pct(d["valor"])} — {faixa_txt}'
            + (f" (escala cortada em {_pct(max_a, 0)})" if cortada else "")
            + f'\nCaixa R$ {_n(d["caixa"], 0)} mil / PL R$ {_n(d["pl"], 0)} mil'
            f'</title></rect>'
        )
        if cortada:
            ponta = ini + comp
            p.append(f'<path d="M{ponta + 3:.1f} 0 l7 {alt / 2:.1f} '
                     f'l-7 {alt / 2:.1f} z" class="med-{classe} corte"/>')
        p.append(f'<text x="{max(xv, a0) + (14 if cortada else 8):.2f}" '
                 f'y="{alt * 0.8:.1f}" class="val pequeno">'
                 f'{_e(_pct(d["valor"], 1))}</text>')

        # --- painel B -------------------------------------------------------
        if d.get("var") is None:
            p.append(f'<text x="{x_b + 4}" y="{alt * 0.8:.1f}" '
                     f'class="val pequeno sem-dado">sem série</text>')
        else:
            xvb = xb(d["var"])
            comp_b = max(xvb - x_b, 1.5)
            cort_b = d["var"] > max_b
            cls_b = "bom" if d.get("recorrente") else "nd"
            p.append(
                f'<rect x="{x_b:.2f}" y="0" width="{comp_b:.2f}" height="{alt}" '
                f'rx="4" class="var-{cls_b}"><title>{_e(d["fundo"])}: '
                f'volatilidade {_pct(d["var"], 1)} a.a.'
                + (f" (escala cortada em {_pct(max_b, 0)})" if cort_b else "")
                + f'\nClasse mais volátil: {_e(d.get("classe") or "—")}'
                + (f" (de {d['n_classes']} classes)"
                   if (d.get("n_classes") or 0) > 1 else "")
                + f'\nPior queda em um dia: {_pct(d["pior"], 2)}'
                + (f" em {_data_br(d['pior_em'])}" if d.get("pior_em") else "")
                + f'\n{d["obs"]} observações'
                + (f' · o maior dia explica {_pct(d["conc"], 0)} da variância'
                   if d.get("conc") is not None else "")
                + ("\nCota não marcada a mercado: o desvio baixo mede a ausência "
                   "de marcação, não risco baixo" if d.get("accrual") else
                   ("" if d.get("recorrente") else
                    "\nOscilação concentrada em um único dia — não é volatilidade "
                    "recorrente"))
                + '</title></rect>'
            )
            if cort_b:
                ponta = x_b + comp_b
                p.append(f'<path d="M{ponta + 3:.1f} 0 l7 {alt / 2:.1f} '
                         f'l-7 {alt / 2:.1f} z" class="var-{cls_b} corte"/>')
            p.append(f'<text x="{xvb + (14 if cort_b else 8):.2f}" '
                     f'y="{alt * 0.8:.1f}" class="val pequeno">'
                     f'{_e(_pct(d["var"], 1))}</text>')

        p.append("</g>")
        p.append("</a>" if alvo else "")

    # Referencias: zero e limite no painel A, zero no painel B.
    if min_a < 0:
        p.append(f'<line x1="{a0:.1f}" y1="{topo - 12}" x2="{a0:.1f}" '
                 f'y2="{altura - 6}" class="base"/>')
    p.append(f'<line x1="{a_lim:.1f}" y1="{topo - 12}" x2="{a_lim:.1f}" '
             f'y2="{altura - 6}" class="limite"/>')
    p.append(f'<text x="{a_lim:.1f}" y="{topo - 16}" text-anchor="middle" '
             f'class="rot pequeno">limite {_pct(LIMITE_CAIXA_PL, 0)}</text>')
    p.append(f'<line x1="{x_b:.1f}" y1="{topo - 12}" x2="{x_b:.1f}" '
             f'y2="{altura - 6}" class="base"/>')
    p.append(f'<text x="{x_b:.1f}" y="{topo - 16}" text-anchor="middle" '
             f'class="rot pequeno">0</text>')
    p.append("</svg>")
    return "".join(p)


def _select_gestor(dados: list[dict], id_base: str = "filtro-fechados") -> str:
    """Lista suspensa de gestores. Vazia se todos os fundos tem o mesmo.

    Um filtro com uma opcao so nao filtra nada -- ocuparia espaco na barra e
    sugeriria uma escolha que nao existe.
    """
    nomes = sorted({d.get("gestor") for d in dados if d.get("gestor")})
    if len(nomes) < 2:
        return ""
    conta = {n: sum(1 for d in dados if d.get("gestor") == n) for n in nomes}
    opcoes = "".join(
        f"<option value='{_e(n)}'>{_e(n)} ({conta[n]})</option>" for n in nomes
    )
    return (
        f"<label class='sel'><span>gestor</span>"
        f"<select id='{id_base}-gestor'>"
        f"<option value=''>todos ({len(dados)})</option>{opcoes}</select></label>"
    )


def _juntar_caixa_var(caixa: list[dict], var: list[dict]) -> list[dict]:
    """Uma linha por fundo com as duas medidas. A base e o Caixa/PL.

    Fundo sem VaR entra com `var=None` -- ele existe no painel de liquidez e
    tem de continuar aparecendo. O contrario nao acontece: todo fundo com serie
    de cota esta no `fato_indicadores`.
    """
    def digitos(v) -> str:
        return "".join(c for c in str(v or "") if c.isdigit())

    por_cnpj = {digitos(d["cnpj"]): d for d in var if d.get("cnpj")}
    juntos = []
    for d in caixa:
        v = por_cnpj.get(digitos(d.get("cnpj")))
        juntos.append({
            **d,
            "var": v["valor"] if v else None,
            "classe": v["classe"] if v else None,
            "n_classes": v["n_classes"] if v else 0,
            "conc": v["conc"] if v else None,
            "pior": v["pior"] if v else None,
            "pior_em": v["pior_em"] if v else "",
            "obs": v["obs"] if v else 0,
            "accrual": bool(v and v.get("accrual")),
            "recorrente": bool(v and v.get("recorrente")),
        })
    return juntos


def _dados_vol(met: pd.DataFrame, ind: pd.DataFrame) -> list[dict]:
    """Junta a volatilidade maxima por fundo com nome, prazo e CNPJ."""
    if met.empty or ind.empty:
        return []
    base = ind.copy()
    base["_d"] = base["cnpj_fundo"].astype(str).str.replace(r"\D", "", regex=True)
    juntos = met.merge(
        base[["_d", "cnpj_fundo", "nome_fundo", "prazo_cotizacao"]],
        left_on="cnpj_fundo", right_on="_d", how="inner", suffixes=("", "_ind"),
    )
    if juntos.empty:
        return []
    dados = []
    for _, f in juntos.sort_values("vol_anual", ascending=False).iterrows():
        conc = f.get("conc_pior_dia")
        dados.append({
            "fundo": str(f["nome_fundo"]),
            "cnpj": f["cnpj_fundo_ind"],
            "valor": float(f["vol_anual"]),
            "classe": str(f["classe"]).title(),
            "n_classes": int(f["n_classes"]),
            "conc": float(conc) if pd.notna(conc) else None,
            "pior": float(f["pior_dia"]),
            "pior_em": (str(pd.Timestamp(f["pior_dia_em"]).date())
                        if pd.notna(f["pior_dia_em"]) else ""),
            "pl": float(f["pl_fundo"]),
            "obs": int(f["obs"]),
            "accrual": bool(f["accrual"]),
            "fechado": _e_fechado(f["prazo_cotizacao"]),
            # "Recorrente" quer dizer que a oscilacao se repete ao longo da
            # serie, em vez de vir de um dia so -- e que a cota e marcada a
            # mercado, senao o desvio baixo mede ausencia de marcacao.
            "recorrente": bool(
                not f["accrual"] and (pd.isna(conc) or conc <= CONC_ALTA)
            ),
        })
    return dados


def _svg_semaforo(contagem: dict, total: int, largura: int = 720) -> str:
    """Resumo do semaforo: quantos fundos em cada faixa, em barra unica.

    O detalhe abaixo mostra so os piores, entao seria todo vermelho e o
    semaforo nao apareceria. Esta barra da a proporcao do conjunto.
    """
    if not total:
        return ""
    faixas = [
        ("critico", f"Abaixo de {_pct(LIMITE_CAIXA_PL, 0)}", contagem.get("critico", 0)),
        ("atencao", "Pouca folga", contagem.get("atencao", 0)),
        ("bom", "Confortável", contagem.get("bom", 0)),
    ]
    # Faixa acima da barra para o rotulo que nao cabe dentro do segmento. Some
    # o numero quando a faixa e estreita e justamente a faixa estreita e a que
    # interessa: 4 fundos em 98 dao 29px, e o "4" desaparecia.
    topo, alt = 15, 34
    altura = topo + alt
    x = 0.0
    partes = [f'<svg viewBox="0 0 {largura} {altura}" width="100%" '
              f'height="{altura}" role="img" class="gr">']
    fora = []
    for classe, rotulo, n in faixas:
        if not n:
            continue
        w = n / total * largura
        partes.append(
            f'<rect x="{x:.2f}" y="{topo}" width="{max(w - 2, 1):.2f}" height="{alt}" '
            f'rx="4" class="med-{classe}"><title>{_e(rotulo)}: {n} de {total} '
            f'fundo(s)</title></rect>'
        )
        if w > 44:
            partes.append(
                f'<text x="{x + w / 2 - 1:.2f}" y="{topo + alt * 0.63:.1f}" '
                f'text-anchor="middle" class="dentro">{n}</text>'
            )
        else:
            fora.append((x + w / 2 - 1, w, classe, n))
        x += w

    for centro, w, classe, n in fora:
        # Ancora o texto no proprio segmento e puxa um tracinho ate ele -- sem a
        # linha, um numero solto acima da barra nao diz a que faixa pertence.
        cx = min(max(centro, 10), largura - 10)
        partes.append(
            f'<text x="{cx:.1f}" y="{topo - 5}" text-anchor="middle" '
            f'class="val pequeno rot-{classe}">{n}</text>'
        )
        partes.append(
            f'<line x1="{centro:.1f}" y1="{topo - 3}" x2="{centro:.1f}" '
            f'y2="{topo + 3}" class="chamada-{classe}"/>'
        )
    partes.append("</svg>")
    return "".join(partes)


def _e_fechado(prazo) -> bool:
    """Fundo sem janela de resgate. 999 e o sentinela do cadastro."""
    if prazo is None or pd.isna(prazo):
        return False
    return int(prazo) >= PRAZO_SEM_LIMITE


# So a forma societaria sai. Cortar tambem o ramo ("ASSET MANAGEMENT",
# "WEALTH MANAGEMENT") parecia bom para encurtar, mas colapsava
# "MULTIPLICA CAPITAL ASSET MANAGEMENT" e "MULTIPLICA WEALTH MANAGEMENT" -- dois
# gestores diferentes -- no mesmo rotulo "Multiplica". Rotulo de filtro que
# funde duas entidades e pior que rotulo comprido.
_FORMA_SOCIETARIA = (" LTDA", " S.A", " S/A", " SA", " EIRELI", " ME", " EPP")


def gestor_curto(nome) -> str:
    """Nome do gestor sem a forma societaria, para rotulo e filtro."""
    texto = str(nome or "").strip().upper().rstrip(". ")
    if not texto:
        return "não informado"
    mudou = True
    while mudou:
        mudou = False
        for forma in _FORMA_SOCIETARIA:
            if texto.endswith(forma):
                texto = texto[: -len(forma)].rstrip(". ,")
                mudou = True
    return texto.title() or str(nome)


def _gestor(linha) -> str:
    return gestor_curto(linha.get("gestor") if hasattr(linha, "get") else None)


def _dados_caixa_pl(ind: pd.DataFrame) -> tuple[list[dict], int, dict]:
    """Fundos ordenados do menor Caixa/PL, com a contagem por faixa."""
    if ind.empty or "caixa_pl" not in ind.columns:
        return [], 0, {}
    df = ind[ind["caixa_pl"].notna()].sort_values("caixa_pl")
    contagem = {"critico": 0, "atencao": 0, "bom": 0}
    for v in df["caixa_pl"]:
        contagem[_banda(float(v))[0]] += 1
    linhas = [
        {"fundo": f["nome_fundo"], "valor": float(f["caixa_pl"]),
         "caixa": float(f["caixa_potencial_mil"] or 0), "pl": float(f["pl_mil"] or 0),
         "cnpj": f["cnpj_fundo"],
         "gestor": _gestor(f),
         # Fundo fechado nao sofre resgate: o Caixa/PL dele nao decide nada, e
         # sao mais da metade do painel. O filtro deixa isolar os que decidem.
         "fechado": _e_fechado(f.get("prazo_cotizacao"))}
        for _, f in df.iterrows()
    ]
    return linhas, len(df), contagem


# --- Blocos -----------------------------------------------------------------

def _kpis(ind: pd.DataFrame, ind_ant: pd.DataFrame) -> list[dict]:
    def desenq(df):
        if df.empty:
            return 0
        cols = [c for c, *_ in CRITERIOS if c in df.columns]
        return int(df[cols].eq(False).any(axis=1).sum())

    pl = float(ind["pl_mil"].sum()) if "pl_mil" in ind else 0.0
    caixa = float(ind["caixa_potencial_mil"].sum()) if "caixa_potencial_mil" in ind else 0.0
    n_desenq = desenq(ind)
    n_desenq_ant = desenq(ind_ant)

    return [
        {"rotulo": "Fundos no painel", "valor": f"{len(ind)}", "nota": "com ponta ativa na carga"},
        {"rotulo": "Com algum desenquadramento", "valor": f"{n_desenq}",
         "nota": (f"{n_desenq - n_desenq_ant:+d} desde a data anterior"
                  if not ind_ant.empty else "sem data anterior"),
         "estado": "ruim" if n_desenq else "bom"},
        {"rotulo": "PL somado (R$ mil)", "valor": _n(pl, 0), "nota": "soma dos fundos listados"},
        {"rotulo": "Caixa potencial (R$ mil)", "valor": _n(caixa, 0),
         "nota": f"{_pct(caixa / pl if pl else None)} do PL"},
    ]


def _dados_enquadramento(ind: pd.DataFrame) -> list[dict]:
    saida = []
    for coluna, rotulo, limite, _, _ in CRITERIOS:
        if coluna not in ind.columns:
            continue
        serie = ind[coluna]
        saida.append({
            "rotulo": f"{rotulo}  ({limite})",
            "bom": int(serie.eq(True).sum()),
            "ruim": int(serie.eq(False).sum()),
            "nd": int(serie.isna().sum()),
        })
    return saida


def _escada(matriz: pd.DataFrame,
            formato=None) -> tuple[list[str], list[float], list[str], float]:
    """Exposicao da carteira por faixa de prazo, somada nos fundos.

    `Vencidos` sai do grafico e volta como numero a parte: ele e uma ordem de
    grandeza maior que as faixas e, na mesma escala, achatava a escada inteira
    -- escondia exatamente o que o grafico existe para mostrar. Nao e faixa de
    prazo, e outra categoria.

    `formato` monta o valor do tooltip. O padrao e o R$ cheio do painel; o
    relatorio do fundo passa o dele, que escreve tudo em R$ mil.
    """
    formato = formato or (lambda v: f"R$ {_n(v)}")
    if matriz.empty:
        return [], [], [], 0.0
    df = matriz[(matriz["tabela"] == "carteira") & (~matriz["e_total"].fillna(False))]
    dias = pd.to_numeric(df["dias_prazo"], errors="coerce")
    rotulos, valores, titulos = [], [], []
    for inicio, fim, rotulo in FAIXAS:
        recorte = df[(dias >= inicio) & (dias <= fim)]
        valores.append(float(recorte["valor"].sum()))
        rotulos.append(f"{rotulo} dias")
        titulos.append(f"{rotulo} dias: {formato(recorte['valor'].sum())}")
    vencidos = df[df["faixa_prazo"].astype(str).str.strip().str.lower() == "vencidos"]
    return rotulos, valores, titulos, float(vencidos["valor"].sum())


def _fluxo_mes(fluxo: pd.DataFrame, meses: int = 15, formato=None):
    formato = formato or (lambda v: f"R$ {_n(v)}")
    if fluxo.empty:
        return [], [], []
    df = fluxo.assign(mes=pd.to_datetime(fluxo["dt_vencimento"]).dt.to_period("M").astype(str))
    agrupado = df.groupby("mes")["vl_parcela"].agg(["sum", "size"]).sort_index().head(meses)
    rotulos = [f"{m[5:]}/{m[:4]}" for m in agrupado.index]
    valores = [float(v) for v in agrupado["sum"]]
    titulos = [f"{r}: {formato(v)} em {int(n)} parcela(s)"
               for r, v, n in zip(rotulos, valores, agrupado["size"])]
    return rotulos, valores, titulos


def _mudancas(ind: pd.DataFrame, ind_ant: pd.DataFrame) -> list[dict]:
    """Quem entrou ou saiu de desenquadramento desde a data anterior."""
    if ind.empty or ind_ant.empty:
        return []
    atual = ind.set_index("cnpj_fundo")
    antes = ind_ant.set_index("cnpj_fundo")
    linhas = []
    for cnpj in atual.index.intersection(antes.index):
        for coluna, rotulo, _, col_valor, limite in CRITERIOS:
            if coluna not in atual.columns or coluna not in antes.columns:
                continue
            a, b = antes.loc[cnpj, coluna], atual.loc[cnpj, coluna]
            if pd.isna(a) or pd.isna(b) or bool(a) == bool(b):
                continue
            linhas.append({
                "fundo": atual.loc[cnpj, "nome_fundo"],
                "criterio": rotulo,
                "limite": limite,
                "v_antes": antes.loc[cnpj, col_valor] if col_valor in antes.columns else None,
                "v_agora": atual.loc[cnpj, col_valor] if col_valor in atual.columns else None,
                "de": "Enquadrado" if a else "Desenquadrado",
                "para": "Enquadrado" if b else "Desenquadrado",
                "piorou": bool(a) and not bool(b),
            })

    # Piores primeiro e, dentro deles, a maior queda: quem despencou aparece
    # antes de quem so encostou no limite.
    def queda(x):
        if x["v_antes"] is None or x["v_agora"] is None:
            return 0.0
        if pd.isna(x["v_antes"]) or pd.isna(x["v_agora"]):
            return 0.0
        return float(x["v_antes"]) - float(x["v_agora"])

    linhas.sort(key=lambda x: (not x["piorou"], -queda(x), x["fundo"]))
    return linhas


# --- Estilo -----------------------------------------------------------------

def _tokens(modo: str) -> str:
    c = CORES[modo]
    return "".join(f"    --{k}: {v};\n" for k, v in c.items())


ESTILO = f"""
.painel {{
  color-scheme: light;
{_tokens("claro")}}}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) .painel {{
    color-scheme: dark;
{_tokens("escuro")}  }}
}}
:root[data-theme="dark"] .painel {{
  color-scheme: dark;
{_tokens("escuro")}}}

* {{ box-sizing: border-box; }}
body {{ margin: 0; }}
.painel {{
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--plano); color: var(--tinta);
  padding: 0 0 64px; min-height: 100vh;
}}
.limite {{ max-width: 1120px; margin: 0 auto; padding: 0 24px; }}

/* Faixa da marca: o logo tem a tipografia em branco, feita para fundo escuro,
   entao a faixa e navy mesmo no modo claro. */
.marca {{ background: {NAVY}; padding: 28px 0 30px; margin-bottom: 28px; }}
.marca .limite {{ display: flex; align-items: center; justify-content: space-between;
                 gap: 20px; flex-wrap: wrap; }}
/* A faixa cresce junto com o logo: mantida a folga em volta, senao ele encosta
   na borda do navy. Em tela estreita o logo encolhe em vez de empurrar o resto. */
.marca img {{ height: 104px; width: auto; max-width: 100%; display: block; }}
@media (max-width: 900px) {{ .marca img {{ height: 72px; }} }}
@media (max-width: 640px) {{ .marca img {{ height: 48px; }} }}
.marca .titulo {{ color: #fff; font-size: 19px; font-weight: 640; }}
.marca .quando {{ color: {AZUL_PALIDO}; font-size: 13px; margin-top: 2px; }}
.marca .quando b {{ color: {CIANO}; font-weight: 620; }}

/* Escolha de tema. Fica na faixa navy, entao e estilizado contra ela e nao
   contra as superficies do corpo. Traz rotulo alem do simbolo: icone sozinho
   nao diz o que faz. */
.tema {{ display: inline-flex; align-items: center; gap: 7px;
        background: transparent; color: {AZUL_PALIDO};
        border: 1px solid rgba(255,255,255,0.28); border-radius: 999px;
        padding: 7px 14px; font: inherit; font-size: 12.5px; cursor: pointer;
        font-family: inherit; line-height: 1; }}
.tema:hover {{ color: #fff; border-color: {CIANO}; }}
.tema:focus-visible {{ outline: 2px solid {CIANO}; outline-offset: 2px; }}
.tema .simbolo {{ font-size: 14px; line-height: 1; }}

h1 {{ font-size: 22px; margin: 0 0 4px; font-weight: 650; }}
h2 {{ font-size: 15px; margin: 40px 0 4px; font-weight: 650; }}
.sub {{ color: var(--tinta2); font-size: 13px; margin: 0 0 8px; }}
.nota {{ color: var(--tinta3); font-size: 12px; margin: 0 0 16px; }}

.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
        gap: 12px; margin-top: 20px; }}
.kpi {{ background: var(--superficie); border: 1px solid var(--borda);
       border-radius: 10px; padding: 14px 16px; }}
.kpi .r {{ color: var(--tinta2); font-size: 12px; }}
.kpi .v {{ font-size: 30px; font-weight: 640; margin: 4px 0 2px; line-height: 1.1; }}
.kpi .n {{ color: var(--tinta3); font-size: 11px; }}
.kpi.ruim .v {{ color: var(--critico); }}
.kpi.bom .v {{ color: var(--bom); }}

.cartao {{ background: var(--superficie); border: 1px solid var(--borda);
          border-radius: 10px; padding: 18px 20px 12px; margin-top: 8px;
          overflow-x: auto; }}
.gr {{ display: block; }}
.gr .rot {{ fill: var(--tinta2); font-size: 12px; }}
.gr .val {{ fill: var(--tinta); font-size: 12px; font-variant-numeric: tabular-nums; }}
.gr .dentro {{ fill: #fff; font-size: 11px; font-weight: 600; }}
.gr .barra {{ fill: var(--serie); }}
.gr .base {{ stroke: var(--eixo); stroke-width: 1; }}
.gr .seg-bom {{ fill: var(--bom); }}
.gr .seg-ruim {{ fill: var(--critico); }}
.gr .seg-nd {{ fill: var(--neutro); }}
.gr rect:hover {{ opacity: .82; }}
.gr .pequeno {{ font-size: 11px; }}
/* VaR nao tem limite acordado, entao nao usa a paleta de status: a cor aqui
   diz se o NUMERO merece credito, nao se o fundo esta bem ou mal. */
.gr .var-bom {{ fill: var(--serie); }}
.gr .var-nd {{ fill: var(--neutro); }}
.gr .titulo-painel {{ fill: var(--tinta2); font-size: 12px; font-weight: 640; }}
.gr .sem-dado {{ fill: var(--tinta3); font-style: italic; }}
.legenda .sem-dado {{ color: var(--tinta3); font-style: italic; }}
.gr .med-bom {{ fill: var(--bom); }}
.gr .med-atencao {{ fill: var(--atencao); }}
.gr .med-critico {{ fill: var(--critico); }}
.gr .faixa-at {{ fill: var(--atencao); opacity: .10; }}
/* Rotulo que nao coube dentro do segmento: sai por cima, na cor do status, com
   um tracinho ligando ao segmento a que pertence. */
.gr .rot-bom {{ fill: var(--bom); font-weight: 600; }}
.gr .rot-atencao {{ fill: var(--atencao); font-weight: 600; }}
.gr .rot-critico {{ fill: var(--critico); font-weight: 600; }}
.gr .chamada-bom {{ stroke: var(--bom); stroke-width: 1.5; }}
.gr .chamada-atencao {{ stroke: var(--atencao); stroke-width: 1.5; }}
.gr .chamada-critico {{ stroke: var(--critico); stroke-width: 1.5; }}
.gr .limite {{ stroke: var(--tinta2); stroke-width: 2; }}
.gr .corte {{ opacity: .55; }}

.legenda {{ display: flex; flex-wrap: wrap; gap: 16px; margin: 10px 0 4px;
           font-size: 12px; color: var(--tinta2); }}
.legenda span {{ display: inline-flex; align-items: center; gap: 6px; }}
.ch {{ width: 10px; height: 10px; border-radius: 3px; display: inline-block; }}

table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ padding: 7px 10px; text-align: right; white-space: nowrap;
         border-bottom: 1px solid var(--grade); }}
th:first-child, td:first-child {{ text-align: left; white-space: normal; }}
th {{ color: var(--tinta2); font-weight: 600; font-size: 12px; cursor: pointer;
     position: sticky; top: 0; background: var(--superficie); }}
th:hover {{ color: var(--tinta); }}
td {{ font-variant-numeric: tabular-nums; }}
tbody tr:hover {{ background: color-mix(in srgb, var(--serie) 7%, transparent); }}
.tag {{ display: inline-flex; align-items: center; gap: 5px; font-size: 12px; }}
.tag::before {{ content: ""; width: 8px; height: 8px; border-radius: 50%; }}
.tag.bom::before {{ background: var(--bom); }}
.tag.ruim::before {{ background: var(--critico); }}
.tag.nd::before {{ background: var(--neutro); }}
.tag.bom {{ color: var(--bom); }}
.tag.ruim {{ color: var(--critico); }}
.tag.nd {{ color: var(--tinta3); }}
.piorou td:first-child {{ box-shadow: inset 3px 0 0 var(--critico); }}
.melhorou td:first-child {{ box-shadow: inset 3px 0 0 var(--bom); }}

/* Controles de ordenacao do medidor. Ficam numa linha acima do grafico, nunca
   dentro dele. */
.ordenar {{ display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
           margin: 14px 0 10px; padding-top: 12px;
           border-top: 1px solid var(--grade); font-size: 12px; }}
.ordenar span {{ color: var(--tinta2); margin-right: 2px; }}
.ordenar button {{ font: inherit; font-family: inherit; cursor: pointer;
                  padding: 5px 11px; border-radius: 999px;
                  border: 1px solid var(--eixo); background: transparent;
                  color: var(--tinta2); }}
.ordenar button:hover {{ color: var(--tinta); border-color: var(--serie); }}
.ordenar button.ativo {{ background: var(--serie); border-color: var(--serie);
                        color: #fff; }}
/* Lista suspensa do filtro de gestor: mesma altura e mesma tipografia dos
   botoes ao lado, senao a barra fica com dois pesos visuais diferentes. */
.ordenar .sel {{ display: inline-flex; align-items: center; gap: 6px; }}
.ordenar select {{ font: inherit; font-size: 12px; padding: 4px 8px;
                  border-radius: 6px; border: 1px solid var(--borda);
                  background: var(--superficie); color: var(--tinta);
                  cursor: pointer; max-width: 220px; }}
.ordenar select:hover {{ border-color: var(--serie); }}
.ordenar select.filtrando {{ background: var(--atencao); border-color: var(--atencao);
                            color: #1a1a1a; font-weight: 600; }}
.ordenar button.filtrando {{ background: var(--atencao); border-color: var(--atencao);
                            color: #1a1200; font-weight: 600; }}
.ordenar .separador {{ width: 1px; height: 18px; background: var(--eixo);
                      margin: 0 4px; }}
.ordenar button:focus-visible {{ outline: 2px solid var(--serie); outline-offset: 2px; }}

/* Link para o relatorio do fundo: na tabela e no grafico. Discreto no repouso
   -- 96 nomes sublinhados de azul viram ruido -- e explicito no hover. */
a.ir {{ color: inherit; text-decoration: none; }}
a.ir:hover {{ color: var(--serie); text-decoration: underline; }}
.gr a {{ cursor: pointer; }}
.gr a:hover .rot {{ fill: var(--serie); }}
.gr a:focus-visible {{ outline: 2px solid var(--serie); }}

.busca {{ margin: 12px 0; }}
.busca input {{ width: 100%; max-width: 340px; padding: 8px 10px; font-size: 13px;
               border: 1px solid var(--eixo); border-radius: 8px;
               background: var(--superficie); color: var(--tinta);
               font-family: inherit; }}
.fraco {{ color: var(--tinta3); }}
.vazio {{ color: var(--tinta3); font-size: 13px; padding: 8px 0; }}
/* Janela de retorno que a serie nao cobre inteira: ambar para separar de
   "sem dado", que sai com o mesmo travessao em tinta normal. */
.parcial {{ color: var(--atencao); font-weight: 700; }}
.fora {{ color: var(--tinta2); font-size: 12px; margin: 10px 0 2px;
        padding-top: 10px; border-top: 1px solid var(--grade); }}
details {{ margin-top: 10px; }}
summary {{ color: var(--tinta2); font-size: 12px; cursor: pointer; }}
summary:hover {{ color: var(--tinta); }}
footer {{ color: var(--tinta3); font-size: 11px; margin-top: 40px;
         border-top: 1px solid var(--grade); padding-top: 12px; }}
@media print {{ .busca {{ display: none; }} .cartao {{ break-inside: avoid; }} }}
"""

# Roda no <head>, antes de pintar: le a escolha salva e ja carimba o <html>.
# Se ficasse no fim do corpo, o painel piscaria no tema errado ao abrir.
SCRIPT_CEDO = """
try {
  var t = localStorage.getItem('painel-tema');
  if (t === 'claro' || t === 'escuro') {
    document.documentElement.dataset.theme = (t === 'escuro') ? 'dark' : 'light';
  }
} catch (e) { /* file:// sem localStorage: fica no tema do sistema */ }
"""

SCRIPT = """
// --- Escolha de tema -------------------------------------------------------
// Tres estados: sistema (padrao), claro e escuro. Enquanto o usuario nao
// escolhe, o painel segue o sistema operacional; escolhido, a preferencia dele
// manda e fica salva.
(function () {
  var botao = document.getElementById('tema');
  if (!botao) return;
  var simbolo = botao.querySelector('.simbolo');
  var rotulo = botao.querySelector('.rotulo');
  var ordem = ['sistema', 'claro', 'escuro'];
  var textos = {
    sistema: ['◐', 'Tema do sistema'],
    claro:   ['☀', 'Tema claro'],
    escuro:  ['☾', 'Tema escuro']
  };

  function ler() {
    try {
      var t = localStorage.getItem('painel-tema');
      return (t === 'claro' || t === 'escuro') ? t : 'sistema';
    } catch (e) { return 'sistema'; }
  }

  function aplicar(estado) {
    var raiz = document.documentElement;
    if (estado === 'sistema') delete raiz.dataset.theme;
    else raiz.dataset.theme = (estado === 'escuro') ? 'dark' : 'light';
    simbolo.textContent = textos[estado][0];
    rotulo.textContent = textos[estado][1];
    botao.title = 'Clique para alternar (sistema, claro, escuro)';
    try {
      if (estado === 'sistema') localStorage.removeItem('painel-tema');
      else localStorage.setItem('painel-tema', estado);
    } catch (e) { /* sem localStorage: vale so nesta sessao */ }
  }

  aplicar(ler());
  botao.addEventListener('click', function () {
    aplicar(ordem[(ordem.indexOf(ler()) + 1) % ordem.length]);
  });
})();

// --- Ordenacao do medidor de Caixa/PL --------------------------------------
// Cada fundo e um <g> posicionado por transform, entao reordenar e so reatribuir
// o deslocamento vertical. As posicoes horizontais vem da escala, que nao muda.
// Recebe os ids porque ha DOIS medidores com este comportamento -- Caixa/PL e
// VaR. Duplicar a funcao deixaria os dois desalinhados no primeiro ajuste.
function montarMedidor(idSvg, seletorBarra, idFiltro, rotuloValor) {
  var svg = document.getElementById(idSvg);
  var barra = document.querySelector(seletorBarra);
  if (!svg || !barra) return;
  var base = parseFloat(svg.dataset.base);
  var passo = parseFloat(svg.dataset.passo);
  var extra = parseFloat(svg.dataset.extra);
  var largura = parseFloat(svg.dataset.largura);
  var linhas = Array.prototype.slice.call(svg.querySelectorAll('g.linha'));
  var faixa = svg.querySelector('.faixa-at');
  var verticais = svg.querySelectorAll('line.base, line.limite');
  var filtro = document.getElementById(idFiltro);
  var seletorGestor = document.getElementById(idFiltro + '-gestor');
  var soAbertos = false;
  var gestorAtual = '';

  function passa(g) {
    if (soAbertos && g.dataset.fechado === '1') { return false; }
    if (gestorAtual && g.dataset.gestor !== gestorAtual) { return false; }
    return true;
  }

  // Redesenhar so reatribui o deslocamento vertical: as posicoes horizontais
  // dependem da escala, que nem a ordem nem os filtros mudam.
  function redesenhar() {
    var visiveis = linhas.filter(passa);
    linhas.forEach(function (g) {
      g.style.display = passa(g) ? '' : 'none';
    });
    visiveis.forEach(function (g, i) {
      g.setAttribute('transform', 'translate(0,' + (base + i * passo) + ')');
    });
    // A altura acompanha o que ficou visivel -- senao sobra area em branco
    // do tamanho dos fundos escondidos.
    var altura = base + visiveis.length * passo + extra;
    svg.setAttribute('height', altura);
    svg.setAttribute('viewBox', '0 0 ' + largura + ' ' + altura);
    if (faixa) { faixa.setAttribute('height', Math.max(altura - 26, 0)); }
    verticais.forEach(function (l) { l.setAttribute('y2', altura - 6); });
  }

  function ordenar(chave, dir) {
    var sinal = (dir === 'desc') ? -1 : 1;
    function num(g, campo) {
      var v = parseFloat(g.dataset[campo]);
      return isNaN(v) ? null : v;
    }
    function cmp(a, b) {
      if (chave === 'valor' || chave === 'var') {
        var x = num(a, chave), y = num(b, chave);
        // Fundo sem a medida vai SEMPRE para o fim, nos dois sentidos:
        // ausencia de dado nao e um valor baixo, e no topo pareceria um.
        if (x === null && y === null) { return 0; }
        if (x === null) { return 1; }
        if (y === null) { return -1; }
        return sinal * (x - y);
      }
      return sinal * a.dataset.nome.localeCompare(b.dataset.nome, 'pt-BR');
    }
    linhas.sort(cmp);
    redesenhar();
  }

  barra.querySelectorAll('button[data-ordem]').forEach(function (bt) {
    bt.addEventListener('click', function () {
      // Clicar no botao ja ativo inverte o sentido.
      if (bt.classList.contains('ativo')) {
        bt.dataset.dir = (bt.dataset.dir === 'asc') ? 'desc' : 'asc';
      }
      barra.querySelectorAll('button[data-ordem]').forEach(function (o) {
        o.classList.remove('ativo');
      });
      bt.classList.add('ativo');
      var asc = bt.dataset.dir === 'asc';
      var seta = asc ? ' ↑' : ' ↓';
      if (bt.dataset.ordem === 'nome') {
        bt.textContent = 'Nome ' + (asc ? 'A→Z' : 'Z→A');
      } else {
        bt.textContent = (bt.dataset.rotulo || rotuloValor) + seta;
      }
      ordenar(bt.dataset.ordem, bt.dataset.dir);
    });
  });

  // O rotulo do botao mostra quantos passam nos OUTROS filtros -- com um gestor
  // escolhido, "Todos os 98" seria mentira: o que se ve sao os daquele gestor.
  function rotularFiltro() {
    if (!filtro) return;
    var base = linhas.filter(function (g) {
      return !gestorAtual || g.dataset.gestor === gestorAtual;
    });
    var abertosAgora = base.filter(function (g) {
      return g.dataset.fechado !== '1';
    });
    filtro.textContent = soAbertos
      ? ('Só abertos: ' + abertosAgora.length)
      : ('Todos os ' + base.length);
  }

  if (seletorGestor) {
    seletorGestor.addEventListener('change', function () {
      gestorAtual = seletorGestor.value;
      seletorGestor.classList.toggle('filtrando', !!gestorAtual);
      rotularFiltro();
      redesenhar();
    });
  }

  // Fundo fechado nao sofre resgate: o Caixa/PL dele nao decide nada, e sao
  // mais da metade do painel. O filtro isola os que decidem.
  if (filtro) {
    filtro.addEventListener('click', function () {
      soAbertos = !soAbertos;
      filtro.dataset.soAbertos = soAbertos ? '1' : '0';
      rotularFiltro();
      filtro.classList.toggle('ativo', !soAbertos);
      filtro.classList.toggle('filtrando', soAbertos);
      redesenhar();
      var aviso = document.getElementById(idFiltro + '-aviso');
      if (aviso) { aviso.style.display = soAbertos ? '' : 'none'; }
    });
  }
}

// Sai um medidor OU o outro: com serie de cota vem o duplo (Caixa/PL +
// volatilidade), sem ela vem so o Caixa/PL. Chamar os dois e barato -- o que
// nao esta na pagina volta no `if (!svg || !barra) return`. Enquanto so o
// duplo era montado, o painel sem serie de cota abria com a barra inteira
// morta: ordenacao, "so abertos" e gestor sem nenhum efeito.
montarMedidor('gr-duplo', '.ordenar-duplo', 'filtro-fechados', 'Caixa/PL');
montarMedidor('gr-caixa-pl', '.ordenar-duplo', 'filtro-fechados', 'Caixa/PL');

// Ordenacao e filtro da tabela de fundos. Sem biblioteca: o painel abre de file://.
document.querySelectorAll('table.ordenavel').forEach(function (tabela) {
  tabela.querySelectorAll('th').forEach(function (th, col) {
    th.addEventListener('click', function () {
      var corpo = tabela.tBodies[0];
      var linhas = Array.prototype.slice.call(corpo.rows);
      var desc = th.dataset.desc !== 'sim';
      tabela.querySelectorAll('th').forEach(function (o) { delete o.dataset.desc; });
      th.dataset.desc = desc ? 'sim' : 'nao';
      linhas.sort(function (a, b) {
        var x = a.cells[col].dataset.v, y = b.cells[col].dataset.v;
        var nx = parseFloat(x), ny = parseFloat(y);
        if (!isNaN(nx) && !isNaN(ny)) return desc ? ny - nx : nx - ny;
        x = (x || a.cells[col].textContent).trim();
        y = (y || b.cells[col].textContent).trim();
        return desc ? y.localeCompare(x, 'pt-BR') : x.localeCompare(y, 'pt-BR');
      });
      linhas.forEach(function (l) { corpo.appendChild(l); });
    });
  });
});
var busca = document.getElementById('busca');
if (busca) {
  busca.addEventListener('input', function () {
    var termo = busca.value.toLowerCase();
    document.querySelectorAll('#tabela-fundos tbody tr').forEach(function (tr) {
      tr.style.display = tr.textContent.toLowerCase().indexOf(termo) >= 0 ? '' : 'none';
    });
  });
}
"""


# --- Montagem ---------------------------------------------------------------

def _tag(valor) -> str:
    if valor is None or pd.isna(valor):
        return '<span class="tag nd">n/d</span>'
    return ('<span class="tag bom">Enquadrado</span>' if bool(valor)
            else '<span class="tag ruim">Desenquadrado</span>')


# 999 e o valor que o cadastro traz quando o fundo nao tem janela restritiva --
# e e o numero que a apuracao de fato usa. Aparece como esta: a coluna existe
# para mostrar o parametro aplicado, e trocar 999 por um rotulo esconderia
# justamente o que ela deveria expor.
PRAZO_SEM_LIMITE = 999


def _prazo(valor) -> str:
    if valor is None or pd.isna(valor):
        return '<span class="tag nd">n/d</span>'
    return f"{int(valor)} d"


def _prazo_v(valor) -> float:
    """Chave de ordenacao: n/d por ultimo, junto do sem prazo."""
    if valor is None or pd.isna(valor):
        return float(PRAZO_SEM_LIMITE + 1)
    return float(valor)


def _tabela_fundos(ind: pd.DataFrame, risco: pd.DataFrame | None = None) -> str:
    if ind.empty:
        return '<p class="vazio">Sem fundos.</p>'
    risco = risco if risco is not None else pd.DataFrame()
    df = ind.sort_values("pl_mil", ascending=False)
    cabec = ["Fundo", "Gestor", "Administrador", "PL (R$ mil)", "Prazo resg.",
             "Caixa (R$ mil)", "Caixa/PL",
             "Cx/Cotista", "Cx/GDP", "Caixa/Resg", "Cotistas",
             # Retorno e risco do composto das classes ponderado pelo PL --
             # o fundo nao tem cota propria, tem classes. Ver
             # `varcota.metricas_por_fundo`.
             # "Ret a.a." aqui e o ANO CALENDARIO -- 1o de janeiro ate a
             # data-base --, e "Ret a.m." o mes calendario. Nao sao anualizacao
             # nem "ultimos 30 dias". Decisao da area em 21/08/2026: a
             # anualizacao projetava para doze meses series de tres, e em 21 dos
             # 112 fundos multiplicava o retorno por mais de dois -- saiu das
             # tabelas e ficou no tooltip do Sharpe, que e quem usa ela.
             "Ret per.", "Ret 12m", "Ret a.a.", "Ret a.m.",
             "Vol. a.a.", "VaR 1d"]
    linhas = [
        "<table class='ordenavel' id='tabela-fundos'><thead><tr>"
        + "".join(f"<th>{_e(c)}</th>" for c in cabec)
        + "</tr></thead><tbody>"
    ]
    for _, f in df.iterrows():
        alvo = f"{PASTA_FUNDOS}/{arquivo_do_fundo(f['cnpj_fundo'])}"
        celulas = [
            f"<td data-v=\"{_e(f['nome_fundo'])}\">"
            f"<a class='ir' href='{alvo}'>{_e(f['nome_fundo'])}</a></td>",
            # Gestor pela CVM, em forma curta. O nome completo fica no title --
            # a coluna precisa caber ao lado das outras dez.
            f"<td data-v=\"{_e(_gestor(f))}\" title=\"{_e(f.get('gestor'))}\">"
            f"{_e(_gestor(f))}</td>",
            f"<td data-v=\"{_e(f.get('administrador'))}\">{_e(f.get('administrador'))}</td>",
            f"<td data-v=\"{float(f['pl_mil'] or 0)}\">{_n(f['pl_mil'], 0)}</td>",
            # A janela que define o caixa potencial: so entra no caixa o ativo
            # que se converte dentro dela. Sem o prazo ao lado, dois fundos com
            # caixas muito diferentes parecem incomparaveis sem motivo.
            f"<td data-v=\"{_prazo_v(f.get('prazo_cotizacao'))}\">"
            f"{_prazo(f.get('prazo_cotizacao'))}</td>",
            f"<td data-v=\"{float(f['caixa_potencial_mil'] or 0)}\">{_n(f['caixa_potencial_mil'], 0)}</td>",
            f"<td data-v=\"{f['caixa_pl'] if pd.notna(f['caixa_pl']) else -1}\">{_pct(f['caixa_pl'])}</td>",
            f"<td data-v=\"{f['cx_cotista'] if pd.notna(f['cx_cotista']) else -1}\">"
            f"{_tag(f['enq_cotista'])}</td>",
            f"<td data-v=\"{f['cx_gdp'] if pd.notna(f['cx_gdp']) else -1}\">"
            f"{_tag(f['enq_gdp'])}</td>",
            f"<td data-v=\"{f['caixa_resgate'] if pd.notna(f['caixa_resgate']) else -1}\">"
            f"{_tag(f['enq_rco'])}</td>",
            f"<td data-v=\"{int(f['n_cotistas'] or 0)}\">{_n(f['n_cotistas'], 0)}</td>",
        ]
        # Fundo sem serie de cotas na janela fica com travessao nas quatro
        # ultimas: ausencia de medida nao pode virar zero, que ordenaria junto
        # do fundo que de fato nao rendeu nada.
        chave = "".join(c for c in str(f["cnpj_fundo"]) if c.isdigit())
        r = risco.loc[chave] if chave in risco.index else None
        for campo, fmt in (("ret_periodo", _pct), ("ret_12m", _pct),
                           ("ret_ano", _pct), ("ret_mes", _pct),
                           ("vol_anual", _pct), ("var_1d_pct", _pct)):
            valor = None if r is None else r.get(campo)
            # Serie que nao alcanca o inicio da janela nao tem 12 meses -- nem
            # ano corrente -- a medir. Ali o numero seria o mesmo da coluna do
            # periodo, e tres colunas identicas parecem defeito de calculo em
            # vez do que sao: falta de historico. Ver `_cel_janela`.
            janela = campo.removeprefix("ret_")
            if (janela in ("12m", "ano", "mes") and r is not None
                    and not bool(r.get(f"cobre_{janela}", True))):
                celulas.append(
                    '<td data-v="-1e18" title="Série mais curta que a janela; '
                    'o retorno do que existe está na coluna do período.">'
                    '<span class="parcial">—</span></td>')
                continue
            ordem = valor if valor is not None and pd.notna(valor) else -1e18
            celulas.append(f"<td data-v=\"{ordem}\">{fmt(valor)}</td>")
        linhas.append("<tr>" + "".join(celulas) + "</tr>")
    linhas.append("</tbody></table>")
    return "".join(linhas)


def _tabela_mudancas(mudancas: list[dict], data_ant: str | None) -> str:
    if data_ant is None:
        return ('<p class="vazio">Só há uma data na base — as comparações aparecem '
                'a partir da segunda rodada.</p>')
    if not mudancas:
        return (f'<p class="vazio">Nenhum fundo mudou de enquadramento desde '
                f'{_data_br(data_ant)}.</p>')
    linhas = ["<table><thead><tr><th>Fundo</th><th>Critério</th><th>Limite</th>"
              f"<th>{_data_br(data_ant)}</th><th>Agora</th><th>Situação</th>"
              "</tr></thead><tbody>"]
    for m in mudancas:
        classe = "piorou" if m["piorou"] else "melhorou"
        seta = "▼" if m["piorou"] else "▲"
        chip = "ruim" if m["piorou"] else "bom"
        linhas.append(
            f"<tr class='{classe}'><td>{_e(m['fundo'])}</td>"
            f"<td>{_e(m['criterio'])}</td>"
            f"<td>{_pct(m['limite'], 0)}</td>"
            f"<td>{_pct(m['v_antes'])}</td>"
            f"<td>{seta} {_pct(m['v_agora'])}</td>"
            f"<td><span class='tag {chip}'>{_e(m['para'])}</span></td></tr>"
        )
    linhas.append("</tbody></table>")
    return "".join(linhas)


def _tabela_caixa_pl(dados: list[dict]) -> str:
    """A gemea do medidor: os mesmos numeros sem depender de cor."""
    if not dados:
        return ""
    linhas = ["<table class='ordenavel'><thead><tr><th>Fundo</th>"
              "<th>Caixa (R$ mil)</th><th>PL (R$ mil)</th><th>Caixa/PL</th>"
              "<th>Situação</th></tr></thead><tbody>"]
    for d in dados:
        classe, rotulo = _banda(d["valor"])
        chip = {"critico": "ruim", "atencao": "nd", "bom": "bom"}[classe]
        linhas.append(
            f"<tr><td data-v=\"{_e(d['fundo'])}\">{_e(d['fundo'])}</td>"
            f"<td data-v=\"{d['caixa']}\">{_n(d['caixa'], 0)}</td>"
            f"<td data-v=\"{d['pl']}\">{_n(d['pl'], 0)}</td>"
            f"<td data-v=\"{d['valor']}\">{_pct(d['valor'])}</td>"
            f"<td><span class='tag {chip}'>{_e(rotulo)}</span></td></tr>"
        )
    linhas.append("</tbody></table>")
    return f"<details><summary>Ver como tabela</summary>{''.join(linhas)}</details>"


def _tabela_simples(rotulos: list[str], valores: list[float],
                    cabec: tuple[str, str], formato=None) -> str:
    """A tabela-gemea de cada grafico: todo valor legivel sem depender de cor.

    `formato` existe para o relatorio do fundo, que escreve tudo em R$ mil. O
    padrao segue sendo o R$ cheio do painel.
    """
    if not rotulos:
        return ""
    formato = formato or (lambda v: _n(v))
    linhas = [f"<table><thead><tr><th>{_e(cabec[0])}</th><th>{_e(cabec[1])}</th>"
              "</tr></thead><tbody>"]
    for r, v in zip(rotulos, valores):
        linhas.append(f"<tr><td>{_e(r)}</td><td>{_e(formato(v))}</td></tr>")
    linhas.append("</tbody></table>")
    return f"<details><summary>Ver como tabela</summary>{''.join(linhas)}</details>"


def montar(dados: dict) -> str:
    ind, ind_ant = dados["indicadores"], dados["indicadores_ant"]
    data, data_ant = dados["data"], dados["data_anterior"]

    caixa_pl, total_cpl, contagem_cpl = _dados_caixa_pl(ind)
    cortados = sum(1 for d in caixa_pl if d["valor"] > TETO_CAIXA_PL)
    maior_cpl = max((d["valor"] for d in caixa_pl), default=0.0)
    dados_vol = dados.get("vol") or []
    linhas_duplo = _juntar_caixa_var(caixa_pl, dados_vol) if dados_vol else []
    esc_rot, esc_val, esc_tit, vencidos = _escada(dados["matriz"], formato=_mil_rs)
    flx_rot, flx_val, flx_tit = _fluxo_mes(dados["fluxo"], formato=_mil_rs)

    kpis = "".join(
        f"<div class='kpi {k.get('estado','')}'><div class='r'>{_e(k['rotulo'])}</div>"
        f"<div class='v'>{_e(k['valor'])}</div><div class='n'>{_e(k['nota'])}</div></div>"
        for k in _kpis(ind, ind_ant)
    )

    legenda_status = (
        "<div class='legenda'>"
        "<span><i class='ch' style='background:var(--bom)'></i>Enquadrado</span>"
        "<span><i class='ch' style='background:var(--critico)'></i>Desenquadrado</span>"
        "<span><i class='ch' style='background:var(--neutro)'></i>Sem dado</span>"
        "</div>"
    )

    logo = _logo_embutido()
    botao_tema = (
        "<button class='tema' id='tema' type='button' aria-live='polite'>"
        "<span class='simbolo' aria-hidden='true'>◐</span>"
        "<span class='rotulo'>Tema</span></button>"
    )
    marca = (
        "<div class='marca'><div class='limite'>"
        + (f"<img src='{logo}' alt='Multiplica'>" if logo else
           "<div class='titulo'>Multiplica</div>")
        + "<div><div class='titulo'>Risco de Liquidez</div>"
        + f"<div class='quando'>Data-base <b>{_data_br(data)}</b>"
        + (f" · comparado com {_data_br(data_ant)}" if data_ant else "")
        + "</div></div>"
        + botao_tema
        + "</div></div>"
    )

    partes = [
        "<div class='painel'>",
        marca,
        "<div class='limite'>",
        f"<p class='sub'>{len(ind)} fundo(s) na posição · "
        f"{len(dados['datas'])} data(s) acumuladas na base</p>",
        f"<div class='kpis'>{kpis}</div>",

        "<h2>Enquadramento por critério</h2>",
        f"<p class='nota'>Quantos dos {len(ind)} fundos passam em cada limite. "
        "“Sem dado” são os que não têm denominador — fundo sem distribuidor cadastrado, "
        "por exemplo.</p>",
        f"<div class='cartao'>{_svg_enquadramento(_dados_enquadramento(ind))}"
        f"{legenda_status}</div>",

        # O bloco de cruzamento de limites entre datas saiu a pedido. As funcoes
        # `_mudancas` e `_tabela_mudancas` seguem no modulo: para trazer de
        # volta, basta reinserir as duas linhas aqui.

        "<h2>Caixa Potencial sobre o PL</h2>",
        f"<p class='nota'>Todos os {total_cpl} fundos com o índice calculado, do menor "
        f"para o maior. Limite de {_pct(LIMITE_CAIXA_PL, 0)}; até "
        f"{_pct(ATENCAO_CAIXA_PL, 0)} conta como pouca folga."
        + (f" A escala vai até {_pct(TETO_CAIXA_PL, 0)}: {cortados} fundo(s) passam disso "
           f"e aparecem com marca de corte (▶), sempre com o valor real ao lado — sem o "
           f"teto, o maior ({_pct(maior_cpl, 0)}) achataria todo o resto."
           if cortados else ""),
        f"<p class='nota' id='filtro-fechados-aviso' style='display:none'>"
        f"Mostrando só os fundos com janela de resgate definida. Os "
        f"{sum(1 for d in caixa_pl if d.get('fechado'))} fundos fechados "
        f"(prazo 999) estão ocultos: sem resgate, o Caixa/PL deles não decide "
        f"nada. O semáforo acima segue contando todos.</p>"
        f"<div class='cartao'>{_svg_semaforo(contagem_cpl, total_cpl)}"
        "<div class='ordenar ordenar-duplo'><span>Fundo a fundo, ordenado por</span>"
        "<button type='button' data-ordem='valor' data-dir='asc' class='ativo' "
        "data-rotulo='Caixa/PL'>Caixa/PL ↑</button>"
        + ("<button type='button' data-ordem='var' data-dir='desc' "
           "data-rotulo='Volatilidade'>Volatilidade ↓</button>"
           if dados_vol else "")
        + "<button type='button' data-ordem='nome' data-dir='asc'>Nome A→Z</button>"
        f"<span class='separador'></span><span>mostrando</span>"
        f"<button type='button' id='filtro-fechados' data-so-abertos='0' "
        f"class='ativo'>Todos os {total_cpl}</button>"
        + _select_gestor(caixa_pl)
        + "</div>"
        + (_svg_duplo(linhas_duplo) if dados_vol else _svg_caixa_pl(caixa_pl))
        + f"<div class='legenda'>"
        f"<span><i class='ch' style='background:var(--critico)'></i>"
        f"Abaixo de {_pct(LIMITE_CAIXA_PL, 0)} — {contagem_cpl.get('critico', 0)} fundo(s)</span>"
        f"<span><i class='ch' style='background:var(--atencao)'></i>"
        f"Pouca folga — {contagem_cpl.get('atencao', 0)}</span>"
        f"<span><i class='ch' style='background:var(--bom)'></i>"
        f"Confortável — {contagem_cpl.get('bom', 0)}</span></div>"
        + (f"<div class='legenda'><span class='rot'>Volatilidade:</span>"
           f"<span><i class='ch' style='background:var(--serie)'></i>"
           f"Oscilação recorrente — "
           f"{sum(1 for d in dados_vol if d.get('recorrente'))}</span>"
           f"<span><i class='ch' style='background:var(--neutro)'></i>"
           f"Concentrada em um dia — "
           f"{sum(1 for d in dados_vol if not d.get('recorrente'))}</span>"
           f"<span class='sem-dado'>sem série — "
           f"{sum(1 for d in linhas_duplo if d.get('var') is None)}</span></div>"
           if dados_vol else "")
        + _tabela_caixa_pl(caixa_pl) + "</div>",
    ]

    if dados_vol:
        n_conc = sum(1 for d in dados_vol if not d.get("recorrente"))
        partes += [
            "<p class='nota'><b>Sobre o painel da direita.</b> Volatilidade "
            "anualizada da <b>classe mais volátil</b> de cada fundo — num FIDC "
            "a sênior anda por accrual e quase não oscila, então uma média "
            "diluiria o risco na estrutura de subordinação; quem carrega a "
            "oscilação é a ponta subordinada. Cada painel tem a <b>sua própria "
            "escala</b>; o que os dois compartilham é a linha, ou seja, o "
            "fundo. Não há limite de volatilidade acordado pela área, então "
            "ali não há semáforo: a cor diz apenas o que o número está medindo. "
            f"Em cinza, os <b>{n_conc}</b> fundos em que <b>um único dia "
            f"responde por mais de {_pct(CONC_ALTA, 0)} da variância</b> — ali "
            f"a volatilidade mede o tamanho de um evento isolado, não oscilação "
            f"recorrente. O <b>ORYX</b> é o caso extremo: 80% da variância vem "
            f"do dia 21/07/2026, quando a cota caiu 42%."
            + (f" A escala vai até {_pct(TETO_VOL, 0)}; "
               f"{sum(1 for d in dados_vol if d['valor'] > TETO_VOL)} fundo(s) "
               f"passam disso e aparecem com marca de corte (▶), sempre com o "
               f"valor real ao lado."
               if any(d["valor"] > TETO_VOL for d in dados_vol) else "")
            + "</p>",
        ]

    partes += [
        "<h2>Escada de liquidez</h2>",
        "<p class='nota'>Exposição da carteira por faixa de prazo, somada nos "
        "fundos, em <b>R$ mil</b>. Passe o mouse para o valor.</p>",
        f"<div class='cartao'>{_svg_barras(esc_rot, esc_val, esc_tit, formato=_mil)}"
        + (f"<p class='fora'>Fora da escada — <b>Vencidos</b>: "
           f"{_mil_rs(vencidos)}. Não é faixa de prazo, e na mesma escala achatava "
           f"as demais.</p>" if vencidos else "")
        + f"{_tabela_simples(esc_rot, esc_val, ('Faixa', 'R$ mil'), _mil)}</div>",

        "<h2>Fluxo futuro por mês</h2>",
        "<p class='nota'>Parcelas projetadas dos fundos com cronograma "
        "cadastrado, em <b>R$ mil</b>.</p>",
        f"<div class='cartao'>{_svg_barras(flx_rot, flx_val, flx_tit, formato=_mil)}"
        f"{_tabela_simples(flx_rot, flx_val, ('Mês', 'R$ mil'), _mil)}</div>",

        "<h2>Fundos</h2>",
        "<p class='nota'>Clique no cabeçalho para ordenar. <b>Prazo resg.</b> é a "
        "janela de cotização usada para apurar o caixa potencial: só entra no "
        "caixa o ativo que se converte dentro dela. <b>999 d</b> é o valor que o "
        "cadastro traz quando o fundo não tem janela restritiva — e é o prazo "
        "que a apuração aplica.</p>",
        "<div class='busca'><input id='busca' type='search' "
        "placeholder='Filtrar por fundo ou administrador…' aria-label='Filtrar'></div>",
        f"<div class='cartao'>{_tabela_fundos(ind, dados.get('risco'))}</div>",

        "<footer>Gerado por <code>python -m liquidez painel</code> em "
        f"{dt.datetime.now().strftime('%d/%m/%Y %H:%M')} a partir da camada de fatos. "
        "Não editar à mão: qualquer alteração se perde na próxima geração.</footer>",
        "</div></div>",
    ]

    return (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Painel de Liquidez — {_data_br(data)}</title>"
        f"<script>{SCRIPT_CEDO}</script>"
        f"<style>{ESTILO}</style></head><body>"
        + "".join(partes)
        + f"<script>{SCRIPT}</script></body></html>"
    )


def gerar(
    destino_bi: Path = ft.DIR_BI,
    saida: Path = ARQUIVO_PADRAO,
    com_fundos: bool = True,
) -> Path:
    dados = carregar(Path(destino_bi))
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(montar(dados), encoding="utf-8")
    log.info("Painel gerado: %s (%d fundos, posicao %s)",
             saida, len(dados["indicadores"]), dados["data"])

    # Os relatorios individuais sao gerados junto: o painel linka para eles, e
    # painel sem as paginas do lado daria link quebrado. Uma falha aqui nao
    # invalida o painel, que ja esta gravado.
    if com_fundos:
        from .relatorio_fundo import gerar_todos

        try:
            gerar_todos(destino_bi, saida.parent / PASTA_FUNDOS)
        except Exception:
            log.exception("Falha ao gerar os relatorios individuais; o painel saiu.")
    return saida
