"""Relatorio individual de liquidez por fundo, em HTML autocontido.

Um arquivo por fundo em `<bi>/fundos/<cnpj>.html`, e nao tudo dentro do painel:
o painel ja tem 160 KB com os 97 fundos; somando o historico de cada um iria
para a casa dos megabytes num arquivo so, e o navegador teria que ler os 97
para mostrar um. Assim o painel nao engorda e cada relatorio abre instantaneo.

Cada pagina responde duas perguntas, nesta ordem:
  - como o fundo esta hoje (posicao mais recente em que ele aparece);
  - como chegou ate aqui (todas as datas gravadas na camada de fatos).

Estilo, cores, tema claro/escuro e funcoes de SVG vem do `painel`: sao a mesma
publicacao, e divergir de la seria erro, nao liberdade.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from pathlib import Path

import pandas as pd

from . import fatos as ft
from . import painel as pn
from . import varcota as vc
# A escada e a coluna Valor Liquido tem de recortar o fluxo pelo MESMO criterio,
# senao a conferencia do rodape nao fecha. Importar os nomes, e nao repetir as
# constantes aqui, e o que garante isso -- ver `analise._liquido_do_fluxo`.
from .analise import PRODUTO_DO_FLUXO
from .fluxo import COL_VENCIDO_FLUXO

from .painel import (
    ATENCAO_CAIXA_PL,
    CRITERIOS,
    LIMITE_CAIXA_PL,
    PASTA_FUNDOS,
    arquivo_do_fundo,
    _banda,
    _data_br,
    _e,
    # A notacao de dinheiro vive no `painel` e vale para a publicacao inteira --
    # ver `painel._mil`. Definir uma copia aqui era o caminho para as duas
    # paginas divergirem de novo.
    _mil,
    _mil_rs,
    _n,
    _pct,
)

log = logging.getLogger(__name__)


# --- Leitura ----------------------------------------------------------------

def carregar_historico(destino: Path = ft.DIR_BI) -> pd.DataFrame:
    """Todas as datas de `fato_indicadores` numa tabela so.

    O historico e leve -- uma linha por fundo por data -- entao le tudo de uma
    vez. `fato_matriz` e `fato_fluxo` ficam de fora: sao pesados e so entram na
    data corrente de cada fundo, o que segura o tamanho da pagina.
    """
    destino = Path(destino)
    datas = ft.datas_gravadas("fato_indicadores", destino)
    if not datas:
        raise FileNotFoundError(
            f"Camada de fatos vazia em {destino.resolve()}. "
            f"Rode 'python -m liquidez fatos gerar' primeiro."
        )
    partes = []
    for data in datas:
        df = pn._ler("fato_indicadores", data, destino)
        if df.empty:
            continue
        df = pn._derivar_enq_caixa_pl(df)
        df = df.assign(data=data)
        partes.append(df)
    hist = _completar_cadastrais(pd.concat(partes, ignore_index=True), destino)
    # Fundo inativo nao ganha pagina, pelo mesmo motivo de nao entrar no painel:
    # ver `painel._so_ativos`. Sem isso o link sumiria da tabela e a pagina
    # continuaria la, acessivel por URL e sem nada dizendo que ela saiu.
    return pn._so_ativos(hist, destino)


# Atributos do FUNDO, nao da data: eles nao mudam de uma particao para a outra,
# e a dimensao e a fonte deles.
COLUNAS_CADASTRAIS = ("forma_condominio", "data_constituicao",
                      "situacao_cvm", "data_inicio_situacao")

# A situacao normal de um fundo operando. Escrever ela na pagina de 98 fundos
# seria ruido; o que informa e a excecao, entao so ela aparece.
SITUACAO_NORMAL = "EM FUNCIONAMENTO NORMAL"


def _completar_cadastrais(hist: pd.DataFrame, destino: Path) -> pd.DataFrame:
    """Preenche os campos cadastrais pela `dim_fundo` onde o fato nao os tem.

    O fato e particionado por data, e particao antiga nao volta a ser gerada --
    um campo novo so existe da data em que foi criado para a frente. Fundo que
    saiu da carteira antes disso ficaria sem ele para sempre: o MANI e o KAPAMA
    aparecem pela ultima vez em 03 e 05/06/2026 e sairiam com "não informado"
    num dado que a ficha tem desde sempre.

    A dimensao nao tem esse problema -- ela e sobrescrita inteira a cada rodada,
    com os 100 fundos. O fato continua mandando onde tem valor; a dimensao so
    tapa o buraco.
    """
    caminho = Path(destino) / "dim_fundo" / "dados.parquet"
    if not caminho.exists():
        return hist
    dim = pd.read_parquet(caminho)
    faltam = [c for c in COLUNAS_CADASTRAIS if c in dim.columns]
    if not faltam:
        return hist
    de_para = dim.set_index("cnpj_fundo")[faltam]
    for coluna in faltam:
        vindo = hist["cnpj_fundo"].map(de_para[coluna])
        hist[coluna] = (vindo if coluna not in hist.columns
                        else hist[coluna].where(hist[coluna].notna(), vindo))
    return hist


class _Particoes:
    """Cache das particoes pesadas, uma leitura por data em vez de por fundo."""

    def __init__(self, destino: Path):
        self.destino = Path(destino)
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}

    def de(self, tabela: str, data: str) -> pd.DataFrame:
        chave = (tabela, data)
        if chave not in self._cache:
            self._cache[chave] = pn._ler(tabela, data, self.destino)
        return self._cache[chave]

    def do_fundo(self, tabela: str, data: str, cnpj: str) -> pd.DataFrame:
        df = self.de(tabela, data)
        if df.empty or "cnpj_fundo" not in df.columns:
            return pd.DataFrame()
        return df[df["cnpj_fundo"] == cnpj]


# --- SVG --------------------------------------------------------------------

def _marcas_x(n: int, maximo: int = 12) -> list[int]:
    """Quais indices rotular no eixo do tempo, sem sobrepor texto.

    Doze marcas cabem por causa de duas escolhas somadas: o rotulo vai inclinado
    a 45 graus e em corpo 9px. `dd/mm/aaaa` horizontal em 11px ocupa ~60px;
    inclinado em 9px cai para ~35px de largura. No mais estreito dos tres
    graficos (490px de plot) treze marcas dao ~41px cada -- folga de ~6px.

    Passar disso encosta os rotulos. Para adensar mais seria preciso rotular so
    o mes, ou girar a 90 graus, que custa muito mais altura.
    """
    if n <= maximo:
        return list(range(n))
    passo = -(-n // maximo)  # teto da divisao
    marcas = list(range(0, n, passo))
    if marcas[-1] != n - 1:
        marcas.append(n - 1)
    return marcas


def _rotulos_eixo(datas: list[str], marcas: list[int]) -> list[str]:
    """Rotulo de cada marca do eixo do tempo, com dia, mes e ano.

    Antes o rotulo era cortado em cinco caracteres e o ano nunca aparecia, o que
    deixava a serie ambigua -- pior ainda quando ela cruza o ano.

    `_data_br` devolve a string crua quando nao consegue interpretar a data, e
    nesse caso o rotulo vai como veio.
    """
    return [_data_br(datas[i]) for i in marcas]


# Inclinacao dos rotulos de data. Quarenta e cinco graus e o angulo que troca
# largura por altura na mesma proporcao: o rotulo passa a ocupar ~42px em vez de
# ~60px na horizontal, o que devolve a densidade de marcas perdida quando o ano
# entrou. O custo e altura -- dai a margem de baixo dos graficos ser maior.
INCLINACAO_ROTULO = 45
# Altura que o texto inclinado ocupa: ~35px de largura x sen(45) mais a descida
# da fonte. Caiu de 46 para 38 quando o corpo do rotulo foi para 9px, e os 8px
# voltaram para a area do grafico.
ALTURA_ROTULO_INCLINADO = 38


def _rotulo_inclinado(x: float, y: float, texto: str,
                      classe: str = "rot rot-data") -> str:
    """Rotulo de eixo girado 45 graus, terminando na marca.

    `text-anchor="end"` com rotacao negativa faz o texto subir para a esquerda a
    partir do ponto da marca, que e a convencao de eixo de tempo inclinado: o fim
    do texto aponta para o tick a que ele pertence, e nao o comeco.
    """
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="end" class="{classe}" '
            f'transform="rotate(-{INCLINACAO_ROTULO} {x:.1f} {y:.1f})">'
            f"{_e(texto)}</text>")


def _svg_serie(
    datas: list[str],
    series: list[dict],
    largura: int = 720,
    altura: int = 230,
    formato=lambda v: _n(v, 0),
    limite: float | None = None,
    rotulo_limite: str = "",
    ancorar_zero: bool = True,
    pontas: bool = True,
) -> str:
    """Linha ao longo do tempo. Uma ou mais series, sempre no MESMO eixo.

    Duas escalas num grafico so e o erro mais comum de serie temporal: a
    proporcao entre as linhas passa a depender do enquadramento escolhido, nao
    do dado. Aqui as series compartilham unidade (R$ mil, ou %), entao o eixo
    unico e honesto -- e a distancia entre as linhas significa alguma coisa.

    `ancorar_zero` puxa o eixo ate o zero, que e o certo para valor -- uma
    barra de R$ 400 mil ao lado de uma de R$ 420 mil nao pode parecer o dobro.
    Cota indexada e o caso contrario: todas partem de 100 e o movimento vive
    entre 95 e 140, entao ancorar no zero achataria a serie inteira numa reta.

    `pontas` desliga o marcador e o rotulo no fim da linha, que em serie diaria
    de 250 pontos vira um borrao de texto sobreposto.
    """
    if not datas or not series:
        return '<p class="vazio">Sem histórico.</p>'

    # A base cresce para caber o rotulo de data inclinado a 45 graus, que ocupa
    # ~46px na vertical contra ~12px do texto horizontal.
    esquerda, direita, topo, base = 78, 104, 14, ALTURA_ROTULO_INCLINADO + 14
    altura = altura + (base - 34)
    plot_l = largura - esquerda - direita
    plot_a = altura - topo - base

    valores = [v for s in series for v in s["valores"] if v is not None]
    if not valores:
        return '<p class="vazio">Sem histórico.</p>'
    candidatos = list(valores)
    if limite is not None:
        candidatos.append(limite)
    minimo, maximo = min(candidatos), max(candidatos)
    if ancorar_zero and minimo > 0:
        minimo = 0.0  # a linha nao pode flutuar sem referencia ao zero
    if maximo == minimo:
        maximo = minimo + 1.0
    folga = (maximo - minimo) * 0.08
    minimo, maximo = minimo - folga, maximo + folga
    faixa = maximo - minimo

    n = len(datas)

    def x_de(i: int) -> float:
        return esquerda if n == 1 else esquerda + i / (n - 1) * plot_l

    def y_de(v: float) -> float:
        return topo + (maximo - v) / faixa * plot_a

    partes = [
        f'<svg viewBox="0 0 {largura} {altura}" width="100%" height="{altura}" '
        f'role="img" class="gr">'
    ]

    # Grade recessiva: quatro linhas horizontais, so para dar leitura de nivel.
    for k in range(5):
        v = minimo + (maximo - minimo) * k / 4
        y = y_de(v)
        partes.append(
            f'<line x1="{esquerda}" y1="{y:.1f}" x2="{esquerda + plot_l}" '
            f'y2="{y:.1f}" class="grade-h"/>'
        )
        partes.append(
            f'<text x="{esquerda - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'class="rot pequeno">{_e(formato(v))}</text>'
        )

    if limite is not None and minimo <= limite <= maximo:
        y = y_de(limite)
        partes.append(
            f'<line x1="{esquerda}" y1="{y:.1f}" x2="{esquerda + plot_l}" '
            f'y2="{y:.1f}" class="ref"/>'
        )
        partes.append(
            f'<text x="{esquerda + plot_l + 6}" y="{y + 4:.1f}" '
            f'class="rot pequeno">{_e(rotulo_limite)}</text>'
        )

    marcas = _marcas_x(n)
    for i, rotulo in zip(marcas, _rotulos_eixo(datas, marcas)):
        partes.append(_rotulo_inclinado(x_de(i), topo + plot_a + 14, rotulo))

    for s in series:
        classe = s.get("classe", "sr1")
        pontos = [(x_de(i), y_de(v), i, v)
                  for i, v in enumerate(s["valores"]) if v is not None]
        if not pontos:
            continue
        if len(pontos) > 1:
            d = " ".join(f"{'M' if k == 0 else 'L'}{x:.1f} {y:.1f}"
                         for k, (x, y, _, _) in enumerate(pontos))
            partes.append(f'<path d="{d}" class="ln {classe}"/>')
        # Em serie diaria os alvos de 10px se empilham e o navegador engasga;
        # ai o passo os espaca para caber um por ~8px de largura util.
        passo = max(1, len(pontos) // 90)
        for x, y, i, v in pontos[::passo]:
            # Alvo de mouse maior que o ponto: 22 datas em 540px deixam o
            # marcador visivel pequeno demais para acertar.
            partes.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="10" class="alvo">'
                f'<title>{_e(_data_br(datas[i]))} — {_e(s["nome"])}: '
                f'{_e(formato(v))}</title></circle>'
            )
        if pontas:
            x, y, _, v = pontos[-1]
            partes.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" class="pt {classe}"/>')
            # Rotulo direto na ponta: dispensa procurar a legenda para saber
            # onde cada linha terminou.
            partes.append(
                f'<text x="{x + 8:.1f}" y="{y + 4:.1f}" class="val pequeno {classe}-t">'
                f'{_e(formato(v))}</text>'
            )

    partes.append("</svg>")
    return "".join(partes)


def _svg_tira(datas: list[str], linhas: list[dict], largura: int = 720) -> str:
    """Enquadramento por criterio ao longo do tempo.

    Uma tira por criterio, um quadradinho por data. Serve para achar de relance
    a data em que o fundo cruzou o limite -- coisa que a serie de valores mostra
    mal quando sao cinco criterios de escalas diferentes.
    """
    if not datas or not linhas:
        return '<p class="vazio">Sem histórico.</p>'
    esquerda, direita = 214, 16
    alt, espaco = 16, 8
    plot = largura - esquerda - direita
    # Espaco do rotulo inclinado no lugar dos 24px que bastavam para o horizontal.
    altura = len(linhas) * (alt + espaco) + ALTURA_ROTULO_INCLINADO + 8
    largura_cel = plot / len(datas)

    partes = [
        f'<svg viewBox="0 0 {largura} {altura}" width="100%" height="{altura}" '
        f'role="img" class="gr">'
    ]
    for j, linha in enumerate(linhas):
        y = j * (alt + espaco)
        partes.append(
            f'<text x="{esquerda - 10}" y="{y + alt * 0.78:.1f}" text-anchor="end" '
            f'class="rot pequeno">{_e(linha["rotulo"])}</text>'
        )
        for i, estado in enumerate(linha["estados"]):
            classe = {True: "seg-bom", False: "seg-ruim"}.get(estado, "seg-nd")
            texto = {True: "Enquadrado", False: "Desenquadrado"}.get(estado, "Sem dado")
            x = esquerda + i * largura_cel
            partes.append(
                f'<rect x="{x:.2f}" y="{y}" width="{max(largura_cel - 2, 1):.2f}" '
                f'height="{alt}" rx="3" class="{classe}">'
                f'<title>{_e(_data_br(datas[i]))} — {_e(linha["rotulo"])}: '
                f'{texto}</title></rect>'
            )
    marcas = _marcas_x(len(datas))
    base_rotulo = len(linhas) * (alt + espaco) + 6
    for i, rotulo in zip(marcas, _rotulos_eixo(datas, marcas)):
        x = esquerda + i * largura_cel + largura_cel / 2
        partes.append(_rotulo_inclinado(x, base_rotulo, rotulo))
    partes.append("</svg>")
    return "".join(partes)


# --- Blocos -----------------------------------------------------------------

def _constituicao(data_constituicao, data_base) -> str:
    """" · Constituição dd/mm/aaaa (N dias)" para a linha de identidade.

    Dias CORRIDOS ate a data-base, e nao uteis: a idade do fundo e calendario,
    nao pregao. Devolve trecho vazio quando a ficha nao tem a data, para a linha
    do condominio nao ganhar um "não informado" solto.
    """
    if not data_constituicao:
        return ""
    try:
        inicio = dt.date.fromisoformat(str(data_constituicao)[:10])
        base = dt.date.fromisoformat(str(data_base)[:10])
    except (TypeError, ValueError):
        return ""
    dias = (base - inicio).days
    trecho = f" · Constituição <b>{_e(_data_br(inicio.isoformat()))}</b>"
    # Data futura nao rende "idade": diria "-30 dias", que nao e leitura de nada.
    if dias >= 0:
        trecho += f" <span class='desde'>({_n(dias, 0)} dias)</span>"
    return trecho


def _kpis_do_fundo(hist: pd.DataFrame) -> list[dict]:
    atual = hist.iloc[-1]
    primeira = hist.iloc[0]
    n = len(hist)

    def variacao(coluna: str) -> str:
        if n < 2:
            return "primeira posição na base"
        a, b = primeira[coluna], atual[coluna]
        if pd.isna(a) or pd.isna(b) or not a:
            return f"desde {_data_br(primeira['data'])}"
        return f"{(b / a - 1) * 100:+,.1f}% desde {_data_br(primeira['data'])}".replace(",", ".")

    cpl = atual["caixa_pl"]
    classe, rotulo = _banda(cpl) if pd.notna(cpl) else ("nd", "sem dado")
    estado = {"critico": "ruim", "bom": "bom"}.get(classe, "")

    # O prazo de resgate e o que define a janela do caixa potencial: so entra no
    # caixa o que se converte dentro dele. Sem esse numero a vista, o leitor nao
    # tem como julgar se o caixa esta alto ou baixo -- 20% em 1 dia e 20% em 180
    # dias sao coisas diferentes. Por isso ele vem junto dos KPIs, e nao
    # escondido na tabela de ativos.
    prazo = atual.get("prazo_cotizacao")
    fechado = pn._e_fechado(prazo)
    if pd.isna(prazo):
        prazo_valor, prazo_nota, prazo_estado = "n/d", "prazo não cadastrado", ""
    elif fechado:
        prazo_valor = f"{int(prazo)} d"
        prazo_nota = "fundo fechado — sem janela de resgate"
        prazo_estado = ""
    else:
        prazo_valor = f"{int(prazo)} d"
        prazo_nota = "janela do caixa potencial"
        prazo_estado = ""

    return [
        {"rotulo": "PL (R$ mil)", "valor": _n(atual["pl_mil"], 0),
         "nota": variacao("pl_mil")},
        {"rotulo": "Prazo de resgate", "valor": prazo_valor,
         "nota": prazo_nota, "estado": prazo_estado},
        {"rotulo": "Caixa potencial (R$ mil)", "valor": _n(atual["caixa_potencial_mil"], 0),
         "nota": variacao("caixa_potencial_mil")},
        {"rotulo": "Caixa Potencial / PL", "valor": _pct(cpl),
         "nota": f"{rotulo} · limite {_pct(LIMITE_CAIXA_PL, 0)}", "estado": estado},
        {"rotulo": "Cotistas", "valor": _n(atual["n_cotistas"], 0),
         "nota": f"{n} data(s) no histórico"},
    ]


def _tabela_criterios(atual: pd.Series) -> str:
    linhas = ["<table><thead><tr><th>Critério</th><th>Valor</th><th>Limite</th>"
              "<th>Situação</th></tr></thead><tbody>"]
    for coluna, rotulo, texto_limite, col_valor, limite in CRITERIOS:
        if coluna not in atual.index:
            continue
        valor = atual.get(col_valor)
        # Cx/Cotista e Cx/GDP sao razoes acima de 1, nao percentuais; Caixa/PL e
        # percentual. O limite escrito na tupla ja diz qual e qual.
        if limite <= 0.5:
            mostra = _pct(valor)
        else:
            mostra = _n(valor) if pd.notna(valor) else "n/d"
        linhas.append(
            f"<tr><td>{_e(rotulo)}</td><td>{mostra}</td>"
            f"<td>{_e(texto_limite)}</td><td>{pn._tag(atual[coluna])}</td></tr>"
        )
    linhas.append("</tbody></table>")
    return "".join(linhas)


def _tabela_por_ativo(rel: pd.DataFrame, prazo_cotizacao) -> str:
    """O resultado por ativo, no mesmo recorte do .xlsx.

    Colunas iguais as do `relatorio_excel` -- Codigo Ativo, Exposicao, Liq.
    Duration, Valor Liquido -- para quem compara os dois nao precisar traduzir
    nada. A coluna de origem e um acrescimo: ela diz por qual dos dois lacos a
    linha entrou, o que o Excel nao mostra e ajuda a entender o Valor Liquido.
    """
    if rel is None or rel.empty:
        return '<p class="vazio">Sem relatório para esta data.</p>'

    rotulos = {
        "produto": "produto",
        "outro_fundo": "cota de fundo",
        "outro_fundo_a_vencer": "cota — a vencer",
    }
    linhas = ["<table class='ordenavel'><thead><tr>"
              "<th>Código Ativo</th><th>Exposição (R$ mil)</th>"
              "<th>Liq. Duration</th>"
              "<th>Valor Líquido (R$ mil)</th><th>Origem</th>"
              "</tr></thead><tbody>"]
    for _, r in rel.iterrows():
        dur = r.get("liq_duration")
        # Duration acima do prazo de cotizacao e a razao de o Valor Liquido ser
        # zero -- marcar isso poupa o leitor de cruzar as duas colunas.
        fora = (pd.notna(dur) and pd.notna(prazo_cotizacao)
                and float(dur) > float(prazo_cotizacao))
        linhas.append(
            f"<tr><td data-v=\"{_e(r['codigo_ativo'])}\">{_e(r['codigo_ativo'])}</td>"
            f"<td data-v=\"{float(r['exposicao'] or 0)}\">{_mil(r['exposicao'])}</td>"
            f"<td data-v=\"{float(dur) if pd.notna(dur) else -1}\">"
            + (f"<span class='fora-janela'>{_n(dur, 0)}</span>" if fora
               else (_n(dur, 0) if pd.notna(dur) else "—"))
            + f"</td>"
            f"<td data-v=\"{float(r['valor_liquido'] or 0)}\">"
            f"{_mil(r['valor_liquido'])}</td>"
            f"<td data-v=\"{_e(r.get('origem'))}\">"
            f"{_e(rotulos.get(str(r.get('origem')), str(r.get('origem') or '')))}</td></tr>"
        )

    exp = float(rel["exposicao"].sum())
    liq = float(rel["valor_liquido"].sum())
    linhas.append(
        f"<tr class='total'><td>Total</td><td>{_mil(exp)}</td><td>—</td>"
        f"<td>{_mil(liq)}</td><td></td></tr>"
    )
    linhas.append("</tbody></table>")
    return "".join(linhas)


# Produtos da carteira que sao cota de fundo investido. A classificacao e pelo
# nome porque a matriz nao carrega a origem que o `fato_relatorio` tem. Sabe-se
# que esta lista e incompleta para alguns fundos -- por isso o rodape do
# grafico mostra a diferenca contra o Valor Liquido em vez de escondê-la.
PRODUTOS_FUNDO = {
    "Outros Fundos", "Fundos-Fundos", "Fundos-Fundos_a_Mercado",
    "Cotas de Investimento", "Carteira de Fundos de Investimentos",
    "PORTFOLIO_INVESTIDO", "COTASAPLICADAS",
    # Estes dois so aparecem no `Resultado por ativo`, com origem "produto".
    # Sem eles aqui, uma cota de fundo entraria na escada pelo dia 0.
    "COTAS FUNDOS DE INVESTIMENTOS", "COTAS DE FUNDOS EXTERNOS",
}


def _parcelas_do_fluxo(fluxo: pd.DataFrame | None):
    """O produto projetado, parcela a parcela, pela data em que cada uma vence.

    Regra fechada com o usuario em 29/09/2026: o Valor Liquido considera todo
    fluxo que vence antes da data de resgate, e a escada mostra a mesma coisa. Por
    isso a perna do `PRODUTO_DO_FLUXO` sai INTEIRA daqui e o `_escada_liquidez` a
    tira da matriz -- nao ha divisao entre operacao curta e longa a administrar,
    nem sobreposicao possivel.

    A versao anterior (da manha do mesmo dia) somava o fluxo so das operacoes que
    a matriz nao datava, para nao contar duas vezes. Funcionava, mas deixava a
    perna do DC medida em duas moedas: a parte curta pela carteira e a longa pelo
    fluxo. Vindo tudo do fluxo, o grafico e a coluna Valor Liquido passam a
    responder pela mesma conta.

    Duas exclusoes:

    * EMISSOR VENCIDO nao entra. O fluxo projeta a operacao vencida porque o
      cronograma dela existe, mas ela nao e caixa -- e a matriz ja a manda para a
      linha "Vencidos", que o rodape mostra. Sem o filtro o valor aparecia duas
      vezes na mesma pagina: R$ 9,1 mi dentro dos 29 dias do MULTIPLICA e R$ 68,5
      mi no total, de 15 das 108 operacoes.
    * JURO A APROPRIAR nao entra: a barra usa `vl_amortizacao + vl_acrual`, o
      valor de hoje, e nao `vl_parcela`. E a mesma moeda de `A Vencer C/ Carrego`
      do estoque, que divide o grafico com ela, e a que fecha ao centavo contra a
      carteira. Pela parcela cheia o fluxo do MULTIPLICA daria R$ 636,9 mi contra
      R$ 592,8 mi de posicao -- o grafico afirmaria ter mais do que o fundo tem.

    Devolve (valor por faixa, total, operacoes usadas).
    """
    zero = ([0.0] * len(pn.FAIXAS), 0.0, 0)
    if fluxo is None or fluxo.empty:
        return zero
    if COL_VENCIDO_FLUXO not in fluxo.columns:
        # Particao gravada antes de 29/09/2026. Recusar o fluxo inteiro, e nao
        # usa-lo sem o filtro: `analise._liquido_do_fluxo` recusa igual, entao a
        # pagina volta a ser a de antes -- escada e Valor Liquido pela matriz, os
        # dois conciliando. Usar aqui e recusar la deixaria o rodape sem fechar.
        log.warning("fato_fluxo sem '%s': escada pela matriz. "
                    "Regere o fluxo (python -m liquidez fluxo --fatos --acrual).",
                    COL_VENCIDO_FLUXO)
        return zero
    f = fluxo.copy()
    f["_dias"] = pd.to_numeric(f["qt_dias"], errors="coerce")
    # O valor marcado hoje: principal projetado mais o juro que ja estava corrido
    # na data-base. `vl_juros` -- o trecho de hoje ate o vencimento -- fica fora.
    f["_valor"] = (pd.to_numeric(f["vl_amortizacao"], errors="coerce").fillna(0.0)
                   + pd.to_numeric(f["vl_acrual"], errors="coerce").fillna(0.0))
    f = f[f["_dias"].notna()]
    if COL_VENCIDO_FLUXO in f.columns:
        f = f[~f[COL_VENCIDO_FLUXO].fillna(False).astype(bool)]
    if f.empty:
        return zero

    valores = []
    for inicio, fim_faixa, _ in pn.FAIXAS:
        valores.append(float(f[f["_dias"].between(inicio, fim_faixa)]["_valor"].sum()))
    return valores, float(f["_valor"].sum()), int(f["codigo_op"].nunique())


def _escada_liquidez(matriz: pd.DataFrame, relatorio: pd.DataFrame | None = None,
                     fluxo: pd.DataFrame | None = None, prazo_cotizacao=None,
                     formato=None):
    """Escada por faixa de prazo: todo ativo que nao e cota de fundo.

    Regra definida com a mesa em 02/09/2026. Entra no grafico todo ativo com
    prazo -- DC, renda fixa, cambio, o que houver na tabela `carteira` -- mais
    `A Vencer C/ Carrego` da tabela `estoque`. Sai apenas o que e cota de fundo
    investido, que vai como numero a parte no rodape.

    Ativo que a matriz NAO quebra por dia (CPR e tesouraria, tipicamente) entra
    no dia 0: sao caixa disponivel, e a fonte simplesmente nao lhes atribui
    prazo. O valor vem do `Resultado por ativo`, porque a matriz so guarda a
    exposicao bruta deles -- no MULTIPLICA FIDC o CPR tem exposicao -10,39 mi e
    valor liquido -10,39 mi, mas `Outros Ativos` tem exposicao 5,23 mi e valor
    liquido zero. Conferido: em todos os 96 fundos de 19/08/2026, todo produto
    com dia na matriz aparece com o mesmo nome no relatorio, entao cruzar as
    duas fontes por nome nao duplica ninguem.

    O estoque e a peca que faltava. A escada antiga (`painel._escada`) lia so
    `tabela == "carteira"`, e a escada diaria do `MULTIPLICA A VENC` mora no
    estoque: no MULTIPLICA FIDC eram R$ 302,55 mi -- o maior componente de
    liquidez do fundo -- que nunca apareciam no grafico.

    `A Vencer C/ Carrego`, nao `A Vencer`: so a serie com carrego concilia.
    Conferido no MULTIPLICA FIDC em 19/08/2026, ao centavo, contra o Valor
    Liquido do relatorio -- DC 18,50 e a vencer 302,55. Sem o carrego a mesma
    soma daria 414,79 e nao bateria com nada.

    O PRODUTO PROJETADO (`PRODUTO_DO_FLUXO`, hoje o DC) e a excecao: quando o
    fundo tem fluxo, ele sai da matriz e vem inteiro de `_parcelas_do_fluxo`.
    A matriz data a operacao pelo vencimento FINAL, entao operacao parcelada
    aparecia nela como um bloco unico em ">29" -- R$ 463,9 mi do MULTIPLICA na
    cauda, quando parte disso recebe dentro da janela. O fluxo tem a data de cada
    parcela. As linhas de "Vencidos" do produto CONTINUAM vindo da matriz: elas
    nao sao caixa e o fluxo delas ja foi excluido do outro lado.

    Devolve tambem o que ficou de fora, para o rodape poder fechar a conta.
    """
    formato = formato or _mil_rs
    vazio = ([], [], [], {"fundos": 0.0, "vencidos": 0.0,
                          "cauda": 0.0, "sem_prazo": 0.0, "total": 0.0,
                          "fluxo": 0.0, "fluxo_ops": 0, "produto_fluxo": None})
    if matriz is None or matriz.empty:
        return vazio

    m = matriz[~matriz["e_total"].fillna(False)].copy()
    m["dias"] = pd.to_numeric(m["dias_prazo"], errors="coerce")
    prod = m["produto"].astype(str)
    rotulo = m["faixa_prazo"].astype(str).str.strip().str.lower()

    e_carteira = m["tabela"] == "carteira"
    e_estoque = m["tabela"] == "estoque"
    e_fundo = prod.isin(PRODUTOS_FUNDO)
    com_dia = m["dias"].notna()
    # No estoque a linha de total vem rotulada "Exposição" com `e_total` FALSO
    # -- somar sem excluir dobra a serie inteira. Foi o que inflou a cauda para
    # R$ 3,2 bi numa versao anterior.
    e_soma = rotulo.str.startswith("exposi")
    e_vencido = rotulo.str.startswith("vencido")

    fluxo_faixa, fluxo_total, fluxo_ops = _parcelas_do_fluxo(fluxo)
    # Sem fluxo o produto segue pela matriz, como antes -- e o caso de 83 dos 100
    # fundos. A troca de fonte vale so onde ha fluxo para trocar.
    tem_fluxo = fluxo_total != 0.0
    e_projetado = e_carteira & (prod == PRODUTO_DO_FLUXO) & tem_fluxo

    # O grafico: tudo que tem prazo e nao e cota de fundo, mais o a vencer,
    # menos o produto que agora vem do fluxo.
    perna = ((e_carteira & ~e_fundo & ~e_projetado)
             | (e_estoque & (prod == "A Vencer C/ Carrego")))

    rotulos, valores, titulos = [], [], []
    for k, (inicio, fim, rot) in enumerate(pn.FAIXAS):
        da_matriz = float(m[perna & m["dias"].between(inicio, fim)]["valor"].sum())
        do_fluxo = fluxo_faixa[k]
        v = da_matriz + do_fluxo
        rotulos.append(f"{rot} dias")
        valores.append(v)
        # O tooltip abre a barra nas duas fontes quando as duas contribuem: sem
        # isso, uma faixa que hoje era zero e agora tem valor pareceria erro.
        if do_fluxo and da_matriz:
            titulos.append(f"{rot} dias: {formato(v)} "
                           f"({formato(da_matriz)} da carteira + "
                           f"{formato(do_fluxo)} de fluxo projetado)")
        elif do_fluxo:
            titulos.append(f"{rot} dias: {formato(v)} de fluxo projetado")
        else:
            titulos.append(f"{rot} dias: {formato(v)}")

    # Os ativos sem prazo na matriz entram no dia 0.
    sem_prazo = 0.0
    if relatorio is not None and not relatorio.empty:
        # O produto projetado conta como "ja na escada" mesmo tendo saido da
        # `perna`: ele esta no grafico pelo fluxo. Sem esta linha ele cairia aqui
        # como se nao tivesse prazo e seria somado outra vez no dia 0.
        ja_na_escada = (set(m[e_carteira & com_dia]["produto"].astype(str))
                        | ({PRODUTO_DO_FLUXO} if tem_fluxo else set()))
        r = relatorio[relatorio["origem"] == "produto"]
        cod = r["codigo_ativo"].astype(str)
        sem_prazo = float(
            r[~cod.isin(ja_na_escada) & ~cod.isin(PRODUTOS_FUNDO)]
            ["valor_liquido"].sum())
        if sem_prazo and valores:
            valores[0] += sem_prazo
            # Acumula na explicacao em vez de sobrescrever: o dia 0 pode ter tres
            # origens somadas -- carteira, fluxo projetado e ativo sem prazo -- e
            # reescrever o titulo escondia as duas primeiras.
            detalhe = [f"{formato(sem_prazo)} sem prazo na matriz"]
            if fluxo_faixa[0]:
                detalhe.insert(0, f"{formato(fluxo_faixa[0])} de fluxo projetado")
            titulos[0] = (f"{pn.FAIXAS[0][2]} dias: {formato(valores[0])} "
                          f"(inclui {' e '.join(detalhe)})")

    fora = {
        # So a parte dentro do prazo: e ela que concilia com o Valor Liquido.
        "fundos": float(m[e_carteira & com_dia & e_fundo]["valor"].sum()),
        # Vencido do que entra no grafico, mais o irmao com carrego no estoque
        # -- que so existe como linha de soma.
        "vencidos": float(
            m[(e_carteira & ~e_fundo & e_vencido)
              | (e_estoque & (prod == "Vencidos C/ Carrego") & e_soma)]["valor"].sum()),
        # Alem do prazo do fundo a matriz nao numera o dia, so rotula ">N".
        # Aqui entram tambem as cotas de fundo: e o que sobra da carteira.
        #
        # O produto projetado sai desta conta (`~e_projetado`) porque toda parcela
        # dele ja tem data no fluxo -- nao sobra cauda cega. Antes descontava-se
        # `fluxo_total` do resultado; subtrair deixou de ser necessario quando a
        # perna inteira passou a vir do fluxo, e continuar subtraindo tiraria da
        # cauda valor de OUTROS produtos.
        "cauda": float(
            m[((e_carteira & ~e_projetado)
               | (e_estoque & (prod == "A Vencer C/ Carrego")))
              & ~com_dia & ~e_soma & ~e_vencido]["valor"].sum()),
        "sem_prazo": sem_prazo,
        "total": float(sum(valores)),
        "fluxo": fluxo_total,
        "fluxo_ops": fluxo_ops,
        "produto_fluxo": PRODUTO_DO_FLUXO if tem_fluxo else None,
        "cc": None if prazo_cotizacao is None else int(prazo_cotizacao),
    }

    # O que cabe na janela de cotizacao -- e so isso que o Valor Liquido conta.
    # Antes do fluxo o grafico inteiro cabia nela por construcao (a matriz nao
    # numera dia alem do cc), e a conferencia podia somar todas as barras. Agora
    # o grafico vai alem, e a conta tem de recortar.
    #
    # O recorte sai das FONTES, nao das barras: com cc=180 (MULTIPLICA LONG TERM)
    # a janela corta a faixa "90+" pelo meio, e somar faixas inteiras erraria.
    if prazo_cotizacao is not None:
        cc = float(prazo_cotizacao)
        # `between(0, cc)` e nao `<= cc`: `analise.montar_relatorio` soma as
        # faixas com `0 <= faixa <= cc`, e dia negativo entraria aqui e nao la.
        # As barras ja tem esse piso, porque `pn.FAIXAS` comeca em 0.
        dentro = float(m[perna & com_dia & m["dias"].between(0, cc)]["valor"].sum())
        if tem_fluxo and fluxo is not None and not fluxo.empty:
            dentro += _fluxo_ate(fluxo, cc)
        fora["dentro"] = dentro + sem_prazo
    else:
        fora["dentro"] = None
    return rotulos, valores, titulos, fora


def _fluxo_ate(fluxo: pd.DataFrame, limite: float) -> float:
    """Valor marcado das parcelas que vencem ate `limite` dias, sem emissor vencido.

    Mesmas regras de `_parcelas_do_fluxo` -- se um dia divergirem, a conferencia
    do rodape deixa de fechar e o erro aparece na pagina em vez de passar calado.
    """
    if COL_VENCIDO_FLUXO not in fluxo.columns:
        return 0.0
    dias = pd.to_numeric(fluxo["qt_dias"], errors="coerce")
    sel = (dias.notna() & (dias <= limite)
           & ~fluxo[COL_VENCIDO_FLUXO].fillna(False).astype(bool))
    if not sel.any():
        return 0.0
    amort = pd.to_numeric(fluxo.loc[sel, "vl_amortizacao"], errors="coerce").fillna(0.0)
    acrual = pd.to_numeric(fluxo.loc[sel, "vl_acrual"], errors="coerce").fillna(0.0)
    return float(amort.sum() + acrual.sum())


def _rodape_escada(fora: dict, rel: pd.DataFrame | None) -> str:
    """O que ficou fora do grafico, e a conta contra os ativos liquidos.

    A pergunta que originou esta mudanca foi "por que a escada soma menos que
    os ativos liquidos?". Deixar a diferenca explicita e o que impede a
    pergunta de voltar -- e denuncia fundo em que a classificacao de produto
    esta incompleta, em vez de o numero sair errado calado.
    """
    itens = [
        ("Cotas de fundos investidos", fora["fundos"]),
        ("Além do prazo do fundo", fora["cauda"]),
        ("Vencidos", fora["vencidos"]),
    ]
    linhas = " · ".join(f"<b>{r}</b>: {_mil_rs(v)}" for r, v in itens if v)
    partes = []
    if linhas:
        partes.append(f"<p class='fora'>Fora do gráfico — {linhas}.</p>")

    fluxo = fora.get("fluxo") or 0.0
    produto = fora.get("produto_fluxo")
    if fluxo and produto:
        partes.append(
            f"<p class='fora'>A perna de <b>{_e(produto)}</b> vem do fluxo "
            f"projetado — <b>{_mil_rs(fluxo)}</b> em "
            f"<b>{fora.get('fluxo_ops', 0)}</b> operação(ões), cada parcela na "
            f"data em que vence. A matriz datava a operação pelo vencimento "
            f"final, então o que é parcelado aparecia como um bloco só, além do "
            f"prazo do fundo. Operação de emissor vencido fica fora: vai em "
            f"«Vencidos».</p>"
        )

    if rel is not None and not rel.empty and "valor_liquido" in rel.columns:
        liquido = float(rel["valor_liquido"].sum())
        # A conferencia recorta o grafico na janela de cotizacao: o Valor Liquido
        # conta o que vira caixa ATE o resgate, e as faixas seguintes do grafico
        # sao o que vem depois. Antes do fluxo esse recorte era o grafico inteiro,
        # porque a matriz nao numerava dia alem do cc.
        dentro = fora.get("dentro")
        cc = fora.get("cc")
        if dentro is None:
            dentro, rotulo = fora["total"], "gráfico"
        else:
            rotulo = f"gráfico até {int(cc)} dias" if cc is not None else "gráfico"
        soma = dentro + fora["fundos"]
        dif = liquido - soma
        # Tolerancia de mil reais: abaixo disso e arredondamento das fontes.
        # Acima, a diferenca fica escrita -- sem palpite de causa. No MULTIPLICA
        # FIDC ela era o CPR, que so existe como linha de total e nao tem quebra
        # por dia; em outro fundo pode ser outra coisa.
        fecha = ("fecha com os ativos líquidos" if abs(dif) < 1_000 else
                 f"difere em {_mil_rs(dif)} — a diferença está em ativo sem "
                 f"quebra por prazo na matriz; ver <b>Resultado por ativo</b>")
        partes.append(
            f"<p class='fora'>Conferência: {rotulo} {_mil_rs(dentro)} "
            f"+ cotas de fundos {_mil_rs(fora['fundos'])} "
            f"= {_mil_rs(soma)}, contra {_mil_rs(liquido)} de Valor Líquido "
            f"— {fecha}.</p>"
        )
    return "".join(partes)


# `_tabela_historico` foi removida em 29/09/2026 junto com a secao "Historico
# completo" que a usava. O historico continua na pagina, nos graficos de serie e na
# tira de enquadramento; a tabela repetia data por data o que eles mostram de
# relance. O `data-v` e o JS de ordenacao seguem em uso pelas outras tabelas.


# --- Cotas: retorno e VaR ---------------------------------------------------

# Quantos matizes a paleta categorica oferece. Passando disso a cor deixaria de
# identificar -- e por isso a atribuicao SATURA na ultima em vez de ciclar: duas
# classes com a mesma cor sao um problema visivel, que se resolve olhando a
# legenda; duas classes trocando de cor entre relatorios seriam um problema
# invisivel. Nenhum fundo da base chega a seis classes hoje.
N_CORES = 5


def _cor_da_classe(indice: int) -> tuple[str, str]:
    """(classe CSS da linha, cor da bolinha da legenda) para a i-esima classe.

    Devolve os dois juntos de proposito: o traco da linha vem de uma regra CSS
    e a legenda de um estilo embutido, e quando os dois eram montados em pontos
    diferentes do arquivo bastou renomear a variavel para a legenda apontar
    para uma cor que nao existia mais.
    """
    n = min(indice, N_CORES - 1) + 1
    return f"sn{n}", f"var(--cat{n})"

def _so_digitos(cnpj) -> str:
    """A camada de fatos guarda o CNPJ formatado; a view do banco, so digitos.

    Comparar os dois crus nunca casa -- e casa em silencio, devolvendo vazio
    como se o fundo nao tivesse serie.
    """
    return "".join(c for c in str(cnpj or "") if c.isdigit())


def _serie_do_fundo(serie: pd.DataFrame, cnpj: str) -> pd.DataFrame:
    if serie.empty or "cnpj_fundo" not in serie.columns:
        return pd.DataFrame()
    return serie[serie["cnpj_fundo"] == _so_digitos(cnpj)]


def _svg_cota(serie: pd.DataFrame, cdi: pd.DataFrame | None = None) -> tuple[str, str, str]:
    """Rentabilidade acumulada de cada classe desde o inicio da janela.

    Rentabilidade e nao nivel de cota porque a cota bruta de classes diferentes
    vive em ordens de grandeza distintas -- a subordinada do MULTIPLICA vale
    R$ 771.962 e a mezanino D, R$ 2.440. No mesmo eixo, a segunda seria uma reta
    colada no chao. Em rentabilidade, o eixo passa a medir o que interessa
    comparar: quanto cada classe rendeu.

    O zero e a linha de referencia real -- abaixo dela a classe perdeu valor --
    e por isso o eixo e ancorado nele.
    """
    if serie.empty:
        return "", "", ""

    classes = sorted(serie["classe"].unique(), key=vc.ordem_classe)
    datas = sorted(serie["data"].unique())
    eixo = [pd.Timestamp(d).date().isoformat() for d in datas]

    linhas, legenda, inicios = [], [], {}
    quebras_vistas = 0
    for i, classe in enumerate(classes):
        g = serie[serie["classe"] == classe].set_index("data").sort_index()
        if len(g) < 2:
            continue
        # A curva e o PRODUTO ACUMULADO dos retornos diarios filtrados -- a
        # mesma conta da tabela, ponto a ponto -- e nao `cota / cota_inicial`.
        #
        # A razao das pontas embutia as quebras de cadastro que o filtro tira.
        # No ECOAGRO MULTIPLICA a subordinada foi recortada em 12/11/2025 (cota
        # de 133,30 para 33,35, -74,98%) e recomposta em 17/11 (+120,10%); pela
        # razao das pontas o grafico mostrava -35,07% enquanto a tabela, ao lado,
        # publicava +17,92%. Duas respostas para a mesma pergunta na mesma
        # secao, com 53 pontos percentuais de distancia.
        #
        # Quebra vira passo NULO, e nao salto: tirar o retorno do produto e
        # exatamente o que `varcota.metricas` faz, entao o ultimo ponto da linha
        # passa a ser, por construcao, o `Ret per.` da tabela.
        r = g["valor_cota"].pct_change()
        n_quebras = int((r.abs() > vc.CORTE_QUEBRA).sum())
        quebras_vistas += n_quebras
        r = r.where(r.abs() <= vc.CORTE_QUEBRA)
        # Fracao, nao pontos percentuais: `_pct` faz a conversao na hora de
        # escrever, e assim o mesmo numero serve para o eixo e para o tooltip.
        por_data = ((1 + r.fillna(0.0)).cumprod() - 1.0).to_dict()
        inicios[classe] = g.index[0]
        css, cor = _cor_da_classe(i)
        linhas.append({
            "nome": classe.title(),
            "valores": [por_data.get(d) for d in datas],
            "classe": css,
        })
        legenda.append(
            f"<span><i class='ch' style='background:{cor}'></i>"
            f"{_e(classe.title())}</span>"
        )

    if not linhas:
        return "", "", ""

    # O CDI entra por ULTIMO para ser desenhado por cima das classes -- e a
    # referencia contra a qual se le o resto, e coberto por uma linha de classe
    # ele perderia a funcao.
    if cdi is not None and not cdi.empty:
        valores_cdi = vc.cdi_acumulado(cdi, datas)
        if any(v is not None for v in valores_cdi):
            linhas.append({"nome": "CDI", "valores": valores_cdi, "classe": "cdi"})
            legenda.append(
                "<span><i class='ch' style='background:var(--cdi)'></i>CDI</span>"
            )

    svg = _svg_serie(
        eixo, linhas, altura=250,
        formato=lambda v: _pct(v, 1),
        limite=0.0,
        rotulo_limite="0%",
        ancorar_zero=True,    # o zero e o ponto de partida de todas as classes
        pontas=False,         # 250 pontos diarios: rotulo de ponta vira borrao
    )

    # Cada classe parte do zero na SUA primeira data. Quando elas nao comecam
    # juntas, a distancia vertical entre as linhas nao e diferenca de
    # desempenho: e diferenca de tempo acumulado. Dizer isso e mais honesto que
    # cortar a serie no inicio comum e jogar historico fora.
    partes_nota = []
    if len(set(inicios.values())) > 1:
        detalhe = ", ".join(
            f"{c.title()} em {_data_br(str(pd.Timestamp(d).date()))}"
            for c, d in sorted(inicios.items(), key=lambda x: x[1])
        )
        partes_nota.append(
            f"As classes não começam na mesma data — cada uma parte de zero na "
            f"sua primeira observação ({detalhe}). Onde os inícios diferem, a "
            f"distância entre as linhas mistura desempenho com tempo acumulado."
        )
    # Quebra descartada tem de ser DITA no grafico, e nao so na tabela: a linha
    # segue reta onde a cota deu um salto, e sem a nota isso parece dado faltando.
    if quebras_vistas:
        partes_nota.append(
            f"<b>{quebras_vistas} observação(ões)</b> com variação diária acima "
            f"de {_pct(vc.CORTE_QUEBRA, 0)} foram tratadas como passo nulo — "
            f"salto desse tamanho é virada de cadastro, não mercado. A curva "
            f"segue de onde estava, e é por isso que o último ponto dela bate "
            f"com o <b>Ret per.</b> da tabela abaixo."
        )
    nota = (f"<p class='nota'>{' '.join(partes_nota)}</p>" if partes_nota else "")
    return svg, f"<div class='legenda'>{''.join(legenda)}</div>", nota


def _num(v) -> float:
    """Valor ordenavel para o `data-v`: ausente vai para o fim, nao para o topo."""
    return v if pd.notna(v) else -1e18


def _svg_sharpe(pontos: pd.DataFrame, mensal: bool = False) -> str:
    """Sharpe acumulado da classe de referencia, uma linha.

    Uma linha so porque o Sharpe do fundo passou a ser o da classe JUNIOR:
    senior e mezanino sao remuneradas a CDI + taxa por contrato, entao o Sharpe
    delas e sempre alto e mede a existencia do contrato, nao a gestao.
    """
    if pontos.empty:
        return ""
    g = pontos.sort_values("data")
    eixo = [pd.Timestamp(d).date().isoformat() for d in g["data"]]
    linha = [{"nome": "Sharpe", "valores": [float(v) for v in g["sharpe"]],
              "classe": "sn1"}]
    return _svg_serie(
        eixo, linha, altura=240,
        formato=lambda v: _n(v, 2),
        limite=0.0,          # zero e o CDI: abaixo dali o fundo rendeu menos
        rotulo_limite="CDI",
        ancorar_zero=True,   # ...e por isso o eixo tem de conter o zero
        # Rotulo na ponta mesmo no diario: com uma linha so ele nao colide com
        # nada, e o ultimo ponto e o numero que bate com a tabela -- deixa-lo
        # sem rotulo obrigaria a caçar o valor no tooltip.
        pontas=True,
    )


def _casas_pct(passo: float) -> int:
    """Decimais para que uma diferenca de `passo` (em fracao) ainda apareca.

    Duas casas e o padrao da casa, mas ele quebra justamente nas series que mais
    importam aqui: cota de accrual oscila na quinta casa decimal, e com duas o
    eixo inteiro sai "0,00%" cinco vezes e o tooltip diz que o retorno foi zero
    num dia em que ele nao foi. O numero de casas passa entao a sair da grandeza
    que precisa ser distinguida -- e nem uma casa a mais que isso.
    """
    passo = abs(passo) * 100
    if passo <= 0:
        return 2
    return int(min(6, max(2, math.ceil(-math.log10(passo)) + 1)))


def _pct_sig(valor, casas_min: int = 2, teto: int = 6) -> str:
    """Percentual com casas suficientes para o proprio valor aparecer.

    O eixo se calibra pela amplitude do desenho, mas um valor solto -- o VaR na
    ponta da linha, o retorno no tooltip -- precisa de casas calibradas por ele
    mesmo. Com as casas do eixo, o VaR de uma senior de accrual saia "0,000%":
    o numero que o grafico existe para mostrar, escrito como zero.

    Abaixo do que `teto` casas conseguem imprimir sai "< 0,000001%" em vez da
    fila de zeros -- os dois dizem que o limite e desprezivel, mas so o primeiro
    nao afirma que ele e exatamente zero.
    """
    if valor is None or pd.isna(valor):
        return "—"
    v = float(valor)
    limiar = 10 ** -(teto + 2)
    if v and abs(v) < limiar:
        return ("−" if v < 0 else "") + f"< {_pct(limiar, teto)}"
    escala = abs(v) * 100
    if escala <= 0:
        return _pct(v, casas_min)
    return _pct(v, int(max(casas_min, min(teto, math.ceil(-math.log10(escala)) + 1))))


def _svg_retorno_var(pontos: pd.DataFrame, largura: int = 720,
                     altura: int = 230) -> str:
    """Retorno realizado de cada dia contra o VaR que valia naquele dia.

    Barra e linha no MESMO eixo, e nao dois eixos: retorno e VaR sao a mesma
    unidade -- variacao diaria da cota -- entao a distancia entre a barra e a
    linha e exatamente a folga que sobrou do limite. Com escalas separadas essa
    distancia passaria a depender do enquadramento, e o grafico responderia a
    pergunta errada.

    So a linha de PERDA (-VaR) e desenhada. O parametrico e simetrico por
    construcao, entao a linha de cima seria o espelho da de baixo e nao
    acrescentaria informacao -- e o limite que interessa e o da perda. Dia que
    fura o limite muda de cor e ganha marcador: a excecao nao pode depender de
    o leitor medir a barra contra a linha.
    """
    if pontos is None or pontos.empty:
        return '<p class="vazio">Sem série de cotas com histórico suficiente.</p>'

    g = pontos.sort_values("data")
    datas = [pd.Timestamp(d).date().isoformat() for d in g["data"]]
    r = [float(v) for v in g["retorno"]]
    var = [None if pd.isna(v) else float(v) for v in g["var_1d_pct"]]
    n = len(r)

    # Mesma folga do grafico de serie, pelo mesmo motivo: rotulo de data a 45 graus.
    esquerda, direita, topo, base = 78, 104, 14, ALTURA_ROTULO_INCLINADO + 14
    altura = altura + (base - 34)
    plot_l = largura - esquerda - direita
    plot_a = altura - topo - base

    # O zero entra sempre: o eixo mede ganho e perda, e sem ele as barras
    # ficariam todas do mesmo lado de uma referencia invisivel.
    candidatos = list(r) + [0.0] + [-v for v in var if v is not None]
    minimo, maximo = min(candidatos), max(candidatos)
    if maximo == minimo:
        maximo = minimo + 1e-6
    folga = (maximo - minimo) * 0.10
    minimo, maximo = minimo - folga, maximo + folga
    faixa = maximo - minimo
    # O eixo se calibra pelo passo da grade. Valor solto -- o VaR na ponta, o
    # retorno no tooltip -- sai por `_pct_sig`, calibrado por si mesmo.
    casas = _casas_pct(faixa / 4)

    col = plot_l / n

    def x_de(i: int) -> float:
        return esquerda + i * col + col / 2

    def y_de(v: float) -> float:
        return topo + (maximo - v) / faixa * plot_a

    y0 = y_de(0.0)
    partes = [
        f'<svg viewBox="0 0 {largura} {altura}" width="100%" height="{altura}" '
        f'role="img" class="gr">'
    ]

    for k in range(5):
        v = minimo + (maximo - minimo) * k / 4
        y = y_de(v)
        partes.append(
            f'<line x1="{esquerda}" y1="{y:.1f}" x2="{esquerda + plot_l}" '
            f'y2="{y:.1f}" class="grade-h"/>'
        )
        partes.append(
            f'<text x="{esquerda - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'class="rot pequeno">{_e(_pct(v, casas))}</text>'
        )

    # Barras do retorno, ancoradas no zero. Largura minima de 1px: em 250 dias a
    # coluna tem ~2px, e arredondar para baixo apagaria dias inteiros do desenho.
    largura_barra = max(col * 0.72, 1.0)
    for i, valor in enumerate(r):
        y = y_de(valor)
        topo_barra, alt_barra = min(y0, y), max(abs(y - y0), 0.8)
        classe = "barra-r excede" if bool(g["excedeu"].iloc[i]) else "barra-r"
        partes.append(
            f'<rect x="{x_de(i) - largura_barra / 2:.2f}" y="{topo_barra:.2f}" '
            f'width="{largura_barra:.2f}" height="{alt_barra:.2f}" '
            f'class="{classe}"/>'
        )

    partes.append(
        f'<line x1="{esquerda}" y1="{y0:.1f}" x2="{esquerda + plot_l}" '
        f'y2="{y0:.1f}" class="zero"/>'
    )

    # A linha do -VaR quebra em segmentos: durante o aquecimento da EWMA nao ha
    # estimativa, e ligar o vazio faria a previsao existir antes de existir.
    segmento = []
    for i, v in enumerate(var):
        if v is None:
            if len(segmento) > 1:
                partes.append(f'<path d="{" ".join(segmento)}" class="var-l"/>')
            segmento = []
            continue
        comando = "M" if not segmento else "L"
        segmento.append(f"{comando}{x_de(i):.1f} {y_de(-v):.1f}")
    if len(segmento) > 1:
        partes.append(f'<path d="{" ".join(segmento)}" class="var-l"/>')

    ultimo = next((v for v in reversed(var) if v is not None), None)
    if ultimo is not None:
        partes.append(
            f'<text x="{esquerda + plot_l + 6}" y="{y_de(-ultimo) + 4:.1f}" '
            f'class="rot pequeno">−VaR {_e(_pct_sig(ultimo))}</text>'
        )

    # Marcador so nos dias que furaram: a barra tem ~2px e a cor sozinha se
    # perde, entao o furo ganha um ponto que se enxerga de longe.
    for i, valor in enumerate(r):
        if bool(g["excedeu"].iloc[i]):
            partes.append(
                f'<circle cx="{x_de(i):.1f}" cy="{y_de(valor):.1f}" r="3.2" '
                f'class="pt-furo"/>'
            )

    marcas = _marcas_x(n)
    for i, rotulo in zip(marcas, _rotulos_eixo(datas, marcas)):
        partes.append(_rotulo_inclinado(x_de(i), topo + plot_a + 14, rotulo))

    # Faixas de alvo CONTIGUAS, uma por dia, cobrindo a altura toda: a barra de
    # 2px seria impossivel de acertar com o mouse, e sem cobrir o plot inteiro
    # sobrariam faixas mortas onde o ponteiro nao devolve nada.
    for i in range(n):
        limite = "—" if var[i] is None else _pct_sig(var[i])
        furou = " · furou o limite" if bool(g["excedeu"].iloc[i]) else ""
        partes.append(
            f'<rect x="{esquerda + i * col:.2f}" y="{topo}" '
            f'width="{col:.2f}" height="{plot_a}" class="alvo">'
            f'<title>{_e(_data_br(datas[i]))} — retorno {_e(_pct_sig(r[i]))} · '
            f'VaR {_e(limite)}{furou}</title></rect>'
        )

    partes.append("</svg>")
    return "".join(partes)


def _leitura_aderencia(pontos: pd.DataFrame, linha: pd.Series | None) -> str:
    """A contagem de excecoes desta classe, com a leitura do desvio.

    A contagem so vira veredito sobre o MODELO quando o proprio paramétrico
    passou no teste de confiabilidade da tabela acima. Fora dai a frequencia de
    furos mede a serie, nao o modelo: numa cota que anda por accrual a vol
    tende a zero, o limite tende a zero junto, e qualquer centesimo de queda
    "fura" -- ha classe aqui que fura em 100% dos dias com perdas de 0,0006%.
    Anunciar aquilo como "o VaR subestima a perda" seria trocar um artefato de
    escala por um diagnostico de risco.
    """
    resumo = vc.aderencia(pontos)
    if not resumo:
        return ""
    piso, teto = resumo["banda"]

    estado, leitura = resumo["estado"], resumo["leitura"]
    if linha is not None:
        conf, motivo = vc.confiabilidade(linha)
        if conf != "bom":
            estado = "nd"
            leitura = f"a contagem não afere o modelo: {motivo}"

    return (
        f"<p class='nota aderencia'><b>{resumo['excecoes']}</b> dia(s) furaram o "
        f"VaR em {_n(resumo['obs_com_var'], 0)} observações "
        f"(<b>{_pct(resumo['taxa'], 1)}</b>), contra "
        f"{_pct(resumo['taxa_esperada'], 1)} previstos pelo nível de confiança "
        f"— tolerância de {_n(piso, 0)} a {_n(teto, 0)} furos. "
        f"<span class='tag {estado}'>{_e(leitura)}</span></p>"
    )


def _blocos_retorno_var(pontos: pd.DataFrame, met: pd.DataFrame) -> str:
    """Um grafico por classe, na ordem de senioridade.

    Uma classe por grafico, e nao todas empilhadas: a subordinada do MULTIPLICA
    oscila 180x mais que a senior, e no mesmo eixo a senior viraria uma reta
    colada no zero -- justamente a classe cujo furo de VaR mais importaria ver.
    """
    if pontos is None or pontos.empty:
        return ""
    por_classe = ({} if met is None or met.empty else
                  {c: l for c, l in zip(met["classe"], met.to_dict("records"))})
    classes = sorted(pontos["classe"].unique(), key=vc.ordem_classe)
    blocos = []
    for classe in classes:
        g = pontos[pontos["classe"] == classe].sort_values("data")
        linha = por_classe.get(classe)
        blocos.append(
            f"<h3>{_e(str(classe).title())}</h3>"
            f"<div class='cartao'>{_svg_retorno_var(g)}"
            "<div class='legenda'>"
            "<span><i class='ch' style='background:var(--cat1)'></i>"
            "Retorno do dia</span>"
            "<span><i class='ch' style='background:var(--critico)'></i>"
            "Dia que furou o VaR</span>"
            "<span><i class='ch linha'></i>−VaR previsto para o dia</span>"
            "</div>"
            f"{_leitura_aderencia(g, pd.Series(linha) if linha else None)}</div>"
        )
    return "".join(blocos)


def _cel_janela(linha, janela: str, prefixo: str = "ret") -> str:
    """Celula de retorno de janela fechada. Travessao quando a serie nao a cobre.

    Serie que comeca depois do inicio da janela nao tem 12 meses -- nem um ano
    corrente -- para medir. Exibir ali o que existiu, sob o rotulo "12m",
    repetiria exatamente o numero da coluna do periodo: em 31/07/2026 sao 17 dos
    112 fundos com serie iniciada em 2026, e neles as tres colunas saiam
    identicas. Tres numeros iguais lado a lado parecem defeito de calculo, e
    escondem o que de fato ha: falta de historico.

    O travessao diz isso sem fingir medida. A data e a contagem ficam no
    tooltip, e o retorno do que existiu continua na coluna do periodo.

    `prefixo` serve as duas familias de coluna -- `ret_*` do fundo e `cdi_*` do
    benchmark. O corte e o MESMO: o CDI e medido nas datas da serie, entao onde
    a serie nao alcanca a janela, o CDI dela tambem nao existe.
    """
    valor = linha.get(f"{prefixo}_{janela}")
    if valor is None or pd.isna(valor):
        return '<td data-v="-1e18">—</td>'
    if not bool(linha.get(f"cobre_{janela}", True)):
        de = linha.get(f"de_{janela}")
        desde = (f"Série começa em {_data_br(str(pd.Timestamp(de).date()))}"
                 if pd.notna(de) else "Série mais curta que a janela")
        return (f'<td data-v="-1e18" title="{desde} — sem a janela completa. '
                f'O retorno das {int(linha.get(f"obs_{janela}") or 0)} '
                f'observação(ões) que existem está na coluna do período.">'
                f'<span class="parcial">—</span></td>')
    return f'<td data-v="{_num(valor)}">{_pct(valor, 2)}</td>'


def _cel_inicio(linha) -> str:
    """Primeira observacao da cota daquela classe.

    Nao e a data de constituicao do fundo nem o inicio da janela: e o dia em que
    a serie de cotas DESTA classe comeca dentro dos 400 dias lidos. Fundo antigo
    com classe nova mostra as duas coisas na mesma pagina -- constituicao de
    2015 no topo, cota desde novembro de 2025 aqui -- e nao ha contradicao.

    Ordena pela data ISO, que ordena como texto; o `data-v` guarda ela crua para
    o clique no cabecalho nao ordenar por "dd/mm".
    """
    de = linha.get("de")
    if de is None or pd.isna(de):
        return '<td data-v="">—</td>'
    iso = str(pd.Timestamp(de).date())
    return f'<td data-v="{iso}">{_e(_data_br(iso))}</td>'


def _titulo_pct_cdi(linha) -> str:
    """A divisao do % do CDI, montada no tooltip.

    Os dois termos ja sao colunas -- `Ret período` e `CDI período` --, mas eles
    ficam a quatro colunas de distancia um do outro. O tooltip poupa o vaivem.
    """
    if pd.isna(linha.get("pct_cdi")):
        return ""
    return (f" title=\"{_pct(linha.get('ret_periodo'), 2)} do período ÷ "
            f"{_pct(linha.get('cdi_periodo'), 2)} de CDI no mesmo período\"")


def _titulo_sharpe(linha) -> str:
    """Os tres termos da divisao, no tooltip do Sharpe.

    O retorno ANUALIZADO saiu da tabela, mas e ele que entra no Sharpe. Numero
    que a pagina usa e nao mostra em lugar nenhum e numero que ninguem consegue
    conferir -- entao ele vive aqui, com a conta montada.
    """
    anual = linha.get("ret_anual")
    if anual is None or pd.isna(anual):
        return ""
    obs = int(linha.get("obs") or 0)
    projecao = ""
    if obs and obs < vc.BASE_ANO / 2:
        projecao = (f" O anualizado é projeção: {obs} observação(ões) "
                    f"elevadas a 252/{obs} = {vc.BASE_ANO / obs:.2f}.")
    return (f" title=\"({_pct(anual, 2)} anualizado − "
            f"{_pct(linha.get('cdi_anual'), 2)} CDI) ÷ "
            f"{_pct(linha.get('vol_anual'), 2)} vol.{projecao}\"")


def _tabela_desempenho(met: pd.DataFrame) -> str:
    """Retorno contra CDI e Sharpe, por classe.

    Separada do risco de proposito: sao perguntas diferentes -- quanto rendeu
    contra o custo do dinheiro, e quanto pode perder num dia. Numa tabela so
    dariam quatorze colunas que ninguem le ate o fim.
    """
    if met.empty:
        return "<p class='vazio'>Sem série de cotas com histórico suficiente.</p>"

    # "no período" e nao "12m": a janela e de ~13 meses e varia por classe, que
    # entra na serie quando e emitida. Rotular de 12m seria mentira arredondada.
    # "Ret a.a." e o ANO CALENDARIO -- de 1o de janeiro ate a data-base -- e
    # "Ret a.m." o mes calendario. Nenhum dos dois e anualizacao.
    #
    # O retorno ANUALIZADO saiu da tabela (decisao da area em 21/08/2026): ele
    # projetava para doze meses series de tres, e em 21 dos 112 fundos
    # multiplicava o retorno por mais de dois. Mas o Sharpe e calculado com ele,
    # entao ele nao pode simplesmente sumir -- foi para o tooltip da coluna
    # Sharpe, com os tres termos da divisao. Numero que a pagina usa e nao
    # mostra em lugar nenhum e numero que ninguem consegue conferir.
    # As janelas em ordem decrescente: periodo > 12m > ano > mes.
    #
    # "Início da cota" vem logo depois da classe porque qualifica a linha
    # inteira: e a primeira observacao da serie DAQUELA classe, e e ela que
    # explica por que "Ret período" cobre recortes diferentes na mesma tabela --
    # classe emitida em novembro nao tem os treze meses que a outra tem.
    #
    # `CDI período` vem imediatamente ANTES de `% do CDI` porque e o
    # denominador dele. Sem a coluna, quem tentasse conferir usaria o `CDI a.a.`
    # ao lado -- 23,20 / 15,76 = 147,2% contra os 141,7% exibidos -- e concluiria
    # que a tabela estava errada.
    #
    # O CDI ganha as MESMAS quatro janelas do fundo, na mesma ordem, para a
    # leitura ser por pares: Ret período contra CDI período, Ret 12m contra CDI
    # 12m, e assim por diante. O CDI ANUALIZADO saiu junto com o retorno
    # anualizado -- os dois so serviam ao Sharpe, e estao no tooltip dele.
    cabec = ["Classe", "Início da cota", "Obs.", "PL (R$ mil)",
             "Ret per.", "Ret 12m", "Ret a.a.", "Ret a.m.",
             "CDI per.", "CDI 12m", "CDI a.a.", "CDI a.m.", "% do CDI",
             "Vol. a.a.", "Sharpe", "Leitura"]
    linhas = ["<table class='ordenavel'><thead><tr>"
              + "".join(f"<th>{_e(c)}</th>" for c in cabec)
              + "</tr></thead><tbody>"]

    for _, f in met.iterrows():
        estado, motivo = vc.leitura_sharpe(f)
        rotulo = {"bom": "acima do CDI", "ruim": "abaixo do CDI",
                  "nd": "reservas"}[estado]
        sharpe = "—" if pd.isna(f["sharpe"]) else _n(f["sharpe"], 2)
        linhas.append(
            f"<tr><td data-v=\"{_e(f['classe'])}\">{_e(f['classe'].title())}</td>"
            f"{_cel_inicio(f)}"
            f"<td data-v=\"{f['obs']}\">{_n(f['obs'], 0)}</td>"
            f"<td data-v=\"{f['pl']}\">{_mil(f['pl'])}</td>"
            f"<td data-v=\"{_num(f['ret_periodo'])}\">{_pct(f['ret_periodo'], 2)}</td>"
            f"{_cel_janela(f, '12m')}"
            f"{_cel_janela(f, 'ano')}"
            f"{_cel_janela(f, 'mes')}"
            f"<td data-v=\"{_num(f['cdi_periodo'])}\" "
            f"title=\"CDI acumulado na janela desta classe, de "
            f"{_e(_data_br(str(pd.Timestamp(f['de']).date())))} a "
            f"{_e(_data_br(str(pd.Timestamp(f['ate']).date())))}.\">"
            f"{_pct(f['cdi_periodo'], 2)}</td>"
            f"{_cel_janela(f, '12m', 'cdi')}"
            f"{_cel_janela(f, 'ano', 'cdi')}"
            f"{_cel_janela(f, 'mes', 'cdi')}"
            f"<td data-v=\"{_num(f['pct_cdi'])}\"{_titulo_pct_cdi(f)}>"
            f"{_pct(f['pct_cdi'], 1)}</td>"
            f"<td data-v=\"{_num(f['vol_anual'])}\">{_pct(f['vol_anual'], 2)}</td>"
            f"<td data-v=\"{_num(f['sharpe'])}\"{_titulo_sharpe(f)}>"
            f"<b>{_e(sharpe)}</b></td>"
            f"<td data-v=\"{estado}\"><span class='tag {estado}' "
            f"title=\"{_e(motivo)}\">{_e(rotulo)}</span></td></tr>"
        )
    linhas.append("</tbody></table>")
    return "".join(linhas)


def _tabela_var(met: pd.DataFrame) -> str:
    """Volatilidade e VaR por classe, com a ressalva de leitura."""
    if met.empty:
        return "<p class='vazio'>Sem série de cotas com histórico suficiente.</p>"

    cabec = ["Classe", "Obs.", "PL (R$ mil)", "Vol. a.a.", "VaR 1d",
             "VaR 1d (R$ mil)",
             "VaR 21d", "VaR 21d (R$ mil)", "VaR hist. 1d", "Hist/Par.", "Pior dia",
             "Dias neg.", "Quebras", "Leitura"]
    linhas = ["<table class='ordenavel'><thead><tr>"
              + "".join(f"<th>{_e(c)}</th>" for c in cabec)
              + "</tr></thead><tbody>"]

    for _, f in met.iterrows():
        estado, motivo = vc.confiabilidade(f)
        rotulo = {"bom": "coerente", "ruim": "subestima", "nd": "reservas"}[estado]
        # A razao hist/parametrico torna auditavel a tag da ultima coluna: acima
        # de `BANDA_RAZAO` o parametrico esta abaixo do que ja se viu. Mas ela so
        # tem leitura quando HA perda no percentil -- com VaR historico <= 0, o
        # pior dia da janela ainda foi ganho, e a razao sai negativa sem
        # significar nada. Nesses casos vai travessao, nao "-7,65x".
        razao = f.get("razao_hist")
        tem_razao = f.get("var_hist_pct", 0) > 0 and pd.notna(razao)
        razao_txt = f"{_n(razao, 2)}x" if tem_razao else "—"
        razao_ord = _num(razao if tem_razao else None)
        quebras = int(f.get("quebras", 0) or 0)
        linhas.append(
            f"<tr><td data-v=\"{_e(f['classe'])}\">{_e(f['classe'].title())}</td>"
            f"<td data-v=\"{f['obs']}\">{_n(f['obs'], 0)}</td>"
            f"<td data-v=\"{f['pl']}\">{_mil(f['pl'])}</td>"
            f"<td data-v=\"{_num(f['vol_anual'])}\">{_pct(f['vol_anual'], 2)}</td>"
            f"<td data-v=\"{_num(f['var_1d_pct'])}\">{_pct(f['var_1d_pct'], 2)}</td>"
            f"<td data-v=\"{_num(f['var_1d_rs'])}\">{_mil(f['var_1d_rs'])}</td>"
            f"<td data-v=\"{_num(f['var_21d_pct'])}\">{_pct(f['var_21d_pct'], 2)}</td>"
            f"<td data-v=\"{_num(f['var_21d_rs'])}\">{_mil(f['var_21d_rs'])}</td>"
            f"<td data-v=\"{_num(f['var_hist_pct'])}\">{_pct(f['var_hist_pct'], 2)}</td>"
            f"<td data-v=\"{razao_ord}\">{razao_txt}</td>"
            f"<td data-v=\"{_num(f.get('pior_dia'))}\">{_pct(f.get('pior_dia'), 2)}</td>"
            f"<td data-v=\"{_num(f['dias_negativos'])}\">"
            f"{_pct(f['dias_negativos'], 0)}</td>"
            # Quebra descartada fica ao lado dos numeros da classe, nao so na
            # nota de rodape: quem le a linha precisa saber que ela existe.
            f"<td data-v=\"{quebras}\">{quebras if quebras else '—'}</td>"
            f"<td data-v=\"{estado}\"><span class='tag {estado}' "
            f"title=\"{_e(motivo)}\">{_e(rotulo)}</span></td></tr>"
        )
    linhas.append("</tbody></table>")
    return "".join(linhas)


def _notas_sharpe(met: pd.DataFrame) -> str:
    """Ressalvas do Sharpe deste fundo."""
    if met.empty:
        return ""
    avisos = []

    inflado = met[(met["sharpe"].abs() > vc.SHARPE_IMPLAUSIVEL) | met["accrual"]]
    inflado = inflado[inflado["sharpe"].notna()]
    if len(inflado):
        avisos.append(
            f"<li><b>{', '.join(c.title() for c in inflado['classe'])}</b>: o "
            f"Sharpe está inflado pelo denominador. A cota quase não oscila, "
            f"então a volatilidade tende a zero e o índice explode — o número "
            f"alto mede a ausência de marcação a mercado, <b>não</b> a "
            f"qualidade do retorno. Não compare com fundo marcado.</li>"
        )
    abaixo = met[met["sharpe"] < 0]
    if len(abaixo):
        avisos.append(
            f"<li><b>{', '.join(c.title() for c in abaixo['classe'])}</b>: "
            f"rendeu <b>abaixo do CDI</b> no período — o excedente médio é "
            f"negativo.</li>"
        )
    sem_cdi = met[met["sharpe"].isna()]
    if len(sem_cdi):
        avisos.append(
            f"<li><b>{', '.join(c.title() for c in sem_cdi['classe'])}</b>: sem "
            f"CDI suficiente na janela para o cálculo.</li>"
        )
    if not avisos:
        return ""
    return f"<ul class='ressalvas'>{''.join(avisos)}</ul>"


def _notas_var(met: pd.DataFrame) -> str:
    """As ressalvas que se aplicam a ESTE fundo, nao um aviso generico."""
    if met.empty:
        return ""
    avisos = []
    quebras = int(met["quebras"].sum())
    if quebras:
        avisos.append(
            f"<li><b>{quebras} observação(ões) descartada(s)</b> por variação "
            f"diária acima de {_pct(vc.CORTE_QUEBRA, 0)}. Salto desse tamanho é "
            f"virada de cadastro, não mercado.</li>"
        )
    acc = met[met["accrual"]]
    if len(acc):
        avisos.append(
            f"<li><b>{', '.join(c.title() for c in acc['classe'])}</b>: a cota "
            f"sobe por accrual e quase não oscila (autocorrelação acima de "
            f"{vc.LIMIAR_ACCRUAL:.1f}). O VaR sai perto de zero porque ela não é "
            f"marcada a mercado — o risco dela é de crédito, e a cota não o "
            f"mostra.</li>"
        )
    pesada = met[met["razao_hist"] > vc.BANDA_RAZAO]
    if len(pesada):
        avisos.append(
            f"<li><b>{', '.join(c.title() for c in pesada['classe'])}</b>: o VaR "
            f"histórico supera o paramétrico. A distribuição tem cauda mais "
            f"pesada que a normal, então o número paramétrico <b>subestima</b> "
            f"a perda.</li>"
        )
    sem_perda = met[met["var_hist_pct"] <= 0]
    if len(sem_perda):
        avisos.append(
            f"<li><b>{', '.join(c.title() for c in sem_perda['classe'])}</b>: no "
            f"período, mesmo o {_pct(1 - vc.NIVEL, 0)} pior dia ainda foi de "
            f"ganho — a série praticamente não cai, então não há perda observada "
            f"contra a qual aferir o número paramétrico.</li>"
        )
    dominada = met[(met["razao_hist"] < 1 / 10) & (met["var_hist_pct"] > 0)]
    if len(dominada):
        avisos.append(
            f"<li><b>{', '.join(c.title() for c in dominada['classe'])}</b>: o "
            f"paramétrico ficou dez vezes acima de qualquer perda observada no "
            f"período — a volatilidade está sendo puxada por um salto isolado, "
            f"não por oscilação recorrente.</li>"
        )
    if not avisos:
        return ""
    return f"<ul class='ressalvas'>{''.join(avisos)}</ul>"


# --- Estilo proprio ---------------------------------------------------------
# So o que a pagina do fundo tem e o painel nao. O resto vem de painel.ESTILO.
ESTILO_FUNDO = """
/* Rotulo de data do eixo do tempo. Menor que `.pequeno` (11px) de proposito: a
   data completa inclinada e o rotulo mais longo da pagina, e a 9px ela ocupa
   ~35px de largura contra ~42px -- o que deixa caber 13 marcas no eixo em vez de
   9. Classe propria para nao encolher os rotulos de valor, que sao curtos e
   precisam continuar legiveis. */
.gr .rot-data { font-size: 9px; }

/* Paleta categorica do relatorio do fundo. Matizes distintos em vez de uma
   rampa de azul: com quatro classes no mesmo grafico, passo de luminosidade
   sozinho obrigava a comparar tons vizinhos, e a leitura travava.

   Os matizes sao atribuidos em ORDEM FIXA de senioridade -- senior sempre
   azul, subordinada sempre magenta -- e nunca ciclados. Assim a cor segue a
   entidade: filtrar ou mudar de fundo nao repinta quem sobrou.

   Vermelho, ambar e verde ficam FORA de proposito: sao a paleta de status
   (critico/atencao/bom) e reusa-los como cor de serie faria uma linha
   qualquer parecer um alerta.

   Ressalva honesta: nao rodei o validador de contraste/daltonismo -- ele pede
   node, que nao esta instalado nesta maquina. Os matizes foram escolhidos
   distantes no circulo cromatico e com luminosidades diferentes, e toda serie
   tem legenda, entao a identidade nunca depende so da cor. */
.painel {
  --cat1: #0063c7;  /* azul da marca   */
  --cat2: #7b4fe0;  /* roxo            */
  --cat3: #00898f;  /* teal            */
  --cat4: #c43d8e;  /* magenta         */
  --cat5: #4a5d73;  /* cinza-azulado   */
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .painel {
    --cat1: #41a1ff; --cat2: #a98bff; --cat3: #3fc9d0;
    --cat4: #f080c4; --cat5: #9fb3c8;
  }
}
:root[data-theme="dark"] .painel {
  --cat1: #41a1ff; --cat2: #a98bff; --cat3: #3fc9d0;
  --cat4: #f080c4; --cat5: #9fb3c8;
}

.gr .ln { fill: none; stroke-width: 2; }
.gr .sr1 { stroke: var(--cat1); }
.gr .sr2 { stroke: var(--cat2); }
.gr .sn1 { stroke: var(--cat1); }
.gr .sn2 { stroke: var(--cat2); }
.gr .sn3 { stroke: var(--cat3); }
.gr .sn4 { stroke: var(--cat4); }
.gr .sn5 { stroke: var(--cat5); }

/* O CDI e referencia, nao uma classe do fundo -- por isso amarelo, fora da
   paleta categorica, e tracejado: o traco separa benchmark de serie sem
   depender da cor, e sobrevive a impressao em cinza.

   Amarelo puro sobre branco da 1,07:1 e some; o tom claro fica para o modo
   escuro, onde ele tem fundo navy por tras. */
.painel { --cdi: #b8860b; }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .painel { --cdi: #ffd24d; }
}
:root[data-theme="dark"] .painel { --cdi: #ffd24d; }
.gr .cdi { stroke: var(--cdi); stroke-dasharray: 6 4; }

/* As barras do painel sao azuis por `--serie`; aqui elas passam a usar a
   paleta categorica. A regra vive so no relatorio do fundo -- o painel le o
   seu proprio ESTILO e nao e afetado. */
.gr .barra { fill: var(--cat1); }

/* KPIs numa linha so. O painel usa `auto-fit` com minimo de 210px, que com
   cinco cartoes estoura os 1072px uteis e quebra em 4+1 -- e o cartao sozinho
   na segunda linha parece um adendo, nao um par dos outros.

   `grid-auto-flow: column` nao depende da contagem: acrescentar ou tirar um KPI
   continua dando uma linha. `minmax(0, 1fr)` e o que permite o cartao encolher
   abaixo do proprio conteudo -- sem o zero no minimo, o padrao `auto` usa a
   palavra mais larga como piso e a linha estoura de novo.

   Abaixo de 1000px cinco cartoes ficariam com ~170px e o numero encavalaria o
   rotulo, entao ali volta a quebrar em duas linhas. */
.kpis { grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr);
        grid-template-columns: none; gap: 10px; margin-top: 16px; }
.kpi { padding: 11px 13px; border-radius: 8px; }
.kpi .r { font-size: 11px; line-height: 1.3; }
.kpi .v { font-size: 21px; margin: 3px 0 2px; }
.kpi .n { font-size: 10px; line-height: 1.35; }
@media (max-width: 1000px) {
  .kpis { grid-auto-flow: row;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
  .kpi .v { font-size: 24px; }
}

/* Sem esta regra o h3 herda o padrao do navegador (1.17em, negrito) e sai
   MAIOR que o h2 de 15px acima dele -- a hierarquia inverteria. */
h3 { font-size: 13px; margin: 24px 0 4px; font-weight: 640;
     color: var(--tinta2); text-transform: uppercase; letter-spacing: .04em; }

.ressalvas { margin: 12px 0 0; padding-left: 20px; color: var(--tinta2);
             font-size: 13px; line-height: 1.6; }
.ressalvas b { color: var(--tinta); font-weight: 600; }
.ressalvas li { margin-bottom: 4px; }
/* O anel na superficie separa o marcador da linha que passa por baixo. */
.gr circle.pt.sr1 { fill: var(--cat1); stroke: var(--superficie); stroke-width: 2; }
.gr circle.pt.sr2 { fill: var(--cat2); stroke: var(--superficie); stroke-width: 2; }
.gr .alvo { fill: transparent; }
.gr .alvo:hover { fill: var(--cat1); opacity: .18; }
.gr .grade-h { stroke: var(--grade); stroke-width: 1; }
.gr .ref { stroke: var(--critico); stroke-width: 2; stroke-dasharray: 5 4; }
.gr text.sr1-t { fill: var(--tinta); }
.gr text.sr2-t { fill: var(--tinta); }

/* Retorno diario contra o VaR do dia.

   A barra usa a cor categorica -- e uma serie, nao um estado. Quem carrega
   estado e so o dia que furou o limite, e ai a cor de `critico` esta no seu
   lugar: aquilo E uma excecao. Reusar vermelho para a serie inteira faria todo
   dia parecer alerta, e o furo deixaria de saltar.

   A linha do VaR e tracejada pelo mesmo motivo da linha do CDI: e referencia,
   nao serie observada, e o traco separa as duas sem depender da cor -- o que
   sobrevive a impressao em cinza e ao daltonismo. */
.gr .barra-r { fill: var(--cat1); }
.gr .barra-r.excede { fill: var(--critico); }
.gr .zero { stroke: var(--eixo); stroke-width: 1.5; }
.gr .var-l { fill: none; stroke: var(--critico); stroke-width: 2;
             stroke-dasharray: 5 4; }
/* Anel na superficie: sem ele o marcador do furo se funde a propria barra. */
.gr .pt-furo { fill: var(--critico); stroke: var(--superficie); stroke-width: 1.5; }
/* Chip da legenda que representa uma LINHA, e nao um preenchimento. */
.legenda .ch.linha { height: 0; width: 16px; border-radius: 0;
                     border-top: 2px dashed var(--critico); }
.aderencia { margin: 8px 0 4px; display: flex; align-items: center;
             gap: 8px; flex-wrap: wrap; }

.navegar { display: flex; align-items: center; justify-content: space-between;
           gap: 12px; flex-wrap: wrap; margin: 0 0 18px;
           padding-bottom: 14px; border-bottom: 1px solid var(--grade); }
.navegar a { color: var(--serie); text-decoration: none; font-size: 13px; }
.navegar a:hover { text-decoration: underline; }
.navegar .vizinhos { display: flex; gap: 16px; }
.identidade { color: var(--tinta2); font-size: 13px; margin: 0 0 4px; }
.identidade b { color: var(--tinta); font-weight: 600; }
/* A contagem de dias e apoio da data, nao um dado ao lado dela: fica um tom
   abaixo para a data continuar sendo o que se le primeiro. */
.identidade .desde { color: var(--tinta3); }
/* Travessao de janela incompleta: o ambar separa "nao ha janela" de "nao ha
   dado", que na mesma tabela sairiam com o mesmo simbolo. */
.parcial { color: var(--atencao); font-weight: 700; }
.pares { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
         gap: 16px; }

/* Duration acima da janela de cotizacao: e o motivo de o Valor Liquido ser
   zero, entao fica marcada em vez de exigir que o leitor cruze as colunas. */
.fora-janela { color: var(--critico); font-weight: 600; }
tr.total td { border-top: 2px solid var(--eixo); font-weight: 650;
              background: color-mix(in srgb, var(--serie) 6%, transparent); }
"""


def _montar(ctx: dict) -> str:
    hist = ctx["hist"]
    atual = hist.iloc[-1]
    datas = list(hist["data"])

    kpis = "".join(
        f"<div class='kpi {k.get('estado','')}'><div class='r'>{_e(k['rotulo'])}</div>"
        f"<div class='v'>{_e(k['valor'])}</div><div class='n'>{_e(k['nota'])}</div></div>"
        for k in _kpis_do_fundo(hist)
    )

    serie_cpl = _svg_serie(
        datas,
        [{"nome": "Caixa Potencial / PL",
          "valores": [v if pd.notna(v) else None for v in hist["caixa_pl"]],
          "classe": "sr1"}],
        formato=lambda v: _pct(v, 1),
        limite=LIMITE_CAIXA_PL,
        rotulo_limite=f"limite {_pct(LIMITE_CAIXA_PL, 0)}",
    )

    serie_pl = _svg_serie(
        datas,
        [{"nome": "PL",
          "valores": [v if pd.notna(v) else None for v in hist["pl_mil"]],
          "classe": "sr1"},
         {"nome": "Caixa potencial",
          "valores": [v if pd.notna(v) else None for v in hist["caixa_potencial_mil"]],
          "classe": "sr2"}],
    )

    tiras = []
    for coluna, rotulo, texto_limite, _, _ in CRITERIOS:
        if coluna not in hist.columns:
            continue
        estados = [None if pd.isna(v) else bool(v) for v in hist[coluna]]
        tiras.append({"rotulo": f"{rotulo}  ({texto_limite})", "estados": estados})

    # Tooltip dos graficos na mesma notacao das tabelas -- R$ mil. O painel
    # segue com o R$ cheio dele; o `formato` e o que separa os dois.
    esc_rot, esc_val, esc_tit, esc_fora = _escada_liquidez(
        ctx["matriz"], ctx.get("relatorio"), ctx.get("fluxo"),
        prazo_cotizacao=ctx.get("prazo_cotizacao"), formato=_mil_rs)

    logo = pn._logo_embutido()
    marca = (
        "<div class='marca'><div class='limite'>"
        + (f"<img src='{logo}' alt='Multiplica'>" if logo else
           "<div class='titulo'>Multiplica</div>")
        + "<div><div class='titulo'>Relatório de Liquidez do Fundo</div>"
        + f"<div class='quando'>Data-base <b>{_data_br(ctx['data'])}</b>"
        + f" · {len(datas)} data(s) no histórico</div></div>"
        + "<button class='tema' id='tema' type='button' aria-live='polite'>"
          "<span class='simbolo' aria-hidden='true'>◐</span>"
          "<span class='rotulo'>Tema</span></button>"
        + "</div></div>"
    )

    anterior, proximo = ctx["anterior"], ctx["proximo"]
    vizinhos = []
    if anterior:
        vizinhos.append(
            f"<a href='{arquivo_do_fundo(anterior[0])}' title='{_e(anterior[1])}'>"
            f"← {_e(anterior[1][:26])}</a>")
    if proximo:
        vizinhos.append(
            f"<a href='{arquivo_do_fundo(proximo[0])}' title='{_e(proximo[1])}'>"
            f"{_e(proximo[1][:26])} →</a>")
    navegar = (
        "<div class='navegar'>"
        "<a href='../painel.html'>← Voltar ao painel</a>"
        f"<div class='vizinhos'>{''.join(vizinhos)}</div>"
        "</div>"
    )

    # A situacao da CVM explica o atraso quando ha um. Sem ela, a nota dizia que
    # o fundo sumiu da base e deixava o leitor procurar o motivo -- no MANI e no
    # KAPAMA a data em que sumiram E a data em que entraram em liquidacao.
    situacao = str(atual.get("situacao_cvm") or "").strip()
    anormal = bool(situacao) and situacao.upper() != SITUACAO_NORMAL
    desde = atual.get("data_inicio_situacao")
    desde_txt = f" desde {_data_br(str(desde)[:10])}" if desde else ""

    atraso = ""
    if ctx["data"] != ctx["data_painel"]:
        motivo = (f" O fundo está <b>{_e(situacao)}</b>{desde_txt} na CVM."
                  if anormal else "")
        atraso = (
            f"<p class='nota'>Este fundo não aparece na posição mais recente da base "
            f"({_data_br(ctx['data_painel'])}). A última em que aparece é "
            f"{_data_br(ctx['data'])}.{motivo}</p>"
        )
    elif anormal:
        # Situacao anormal sem atraso: o fundo ainda aparece na base, mas nao
        # esta operando normalmente. Precisa ser dito do mesmo jeito.
        atraso = (f"<p class='nota'>O fundo está <b>{_e(situacao)}</b>"
                  f"{desde_txt} na CVM.</p>")

    partes = [
        "<div class='painel'>", marca, "<div class='limite'>", navegar,
        f"<h1>{_e(ctx['nome'])}</h1>",
        f"<p class='identidade'>CNPJ <b>{_e(ctx['cnpj'])}</b> · "
        f"Administrador <b>{_e(atual.get('administrador') or 'não informado')}</b></p>",
        # Linha propria, abaixo do CNPJ: a forma de condominio e o que decide se
        # o Caixa Potencial responde alguma pergunta. Em fundo fechado nao ha
        # janela de resgate, e o indicador vira contexto, nao limite. A
        # constituicao vem ao lado porque responde a outra metade da pergunta
        # "que fundo e este": o que ele e, e ha quanto tempo.
        f"<p class='identidade'>Condomínio "
        f"<b>{_e(atual.get('forma_condominio') or 'não informado')}</b>"
        f"{_constituicao(atual.get('data_constituicao'), ctx['data'])}</p>",
        atraso,

        "<h2>Liquidez atual</h2>",
        f"<p class='nota'>Posição de {_data_br(ctx['data'])}.</p>",
        f"<div class='kpis'>{kpis}</div>",

        "<h2>Enquadramento na data</h2>",
        f"<div class='cartao'>{_tabela_criterios(atual)}</div>",

        "<h2>Resultado por ativo</h2>",
        f"<p class='nota'>O mesmo recorte do relatório em Excel de "
        f"{_data_br(ctx['data'])}. <b>Valor Líquido</b> é o que se converte "
        f"dentro da janela de cotização ({_n(ctx.get('prazo_cotizacao'), 0)} "
        f"dias); duration acima disso aparece destacada e zera o valor. "
        f"Valores em <b>R$ mil</b>, como no resto da página. "
        f"Clique no cabeçalho para ordenar.</p>",
        f"<div class='cartao'>"
        f"{_tabela_por_ativo(ctx['relatorio'], ctx.get('prazo_cotizacao'))}</div>",

        # A escada vem aqui, e nao no fim da pagina, porque le a mesma posicao da
        # tabela acima e o rodape dela fecha a conta contra o Valor Liquido dessa
        # tabela. Com as duas juntas a conferencia se faz olhando; separadas por
        # dez secoes de historico, exigia rolar para tras.
        "<h2>Escada de liquidez</h2>",
        f"<p class='nota'>O que o fundo tem <b>a receber</b> por faixa de "
        f"prazo em {_data_br(ctx['data'])}, em <b>R$ mil</b>: todo ativo que "
        f"não é cota de fundo — direitos creditórios, renda fixa, câmbio — "
        f"mais o estoque a vencer da investida. CPR e tesouraria entram no "
        f"<b>dia 0</b>, porque a matriz não lhes atribui prazo. Cotas de "
        f"fundos investidos ficam fora, e vêm no rodapé.</p>",
        f"<p class='nota'>Entra também o <b>fluxo projetado</b> das operações "
        f"que chegam na carteira como bullet mas pagam em parcelas mensais, cada "
        f"parcela na faixa do seu vencimento. Operação que vence dentro do prazo "
        f"que a matriz numera já está datada por ela e não é contada de novo — "
        f"cada operação aparece uma vez só.</p>",
        f"<div class='cartao'>"
        + (pn._svg_barras(esc_rot, esc_val, esc_tit, formato=_mil)
           if any(esc_val) else
           "<p class='vazio'>Nenhum ativo com prazo nesta data, fora cotas de "
           "fundos — não há escada a montar para este fundo.</p>")
        + _rodape_escada(esc_fora, ctx.get("relatorio"))
        + (pn._tabela_simples(esc_rot, esc_val, ('Faixa', 'R$ mil'), _mil)
           if any(esc_val) else "")
        + f"</div>",

        "<h2>Caixa Potencial sobre o PL ao longo do tempo</h2>",
        "<p class='nota'>A linha tracejada é o limite de "
        f"{_pct(LIMITE_CAIXA_PL, 0)}. Passe o mouse para a data e o valor.</p>",
        f"<div class='cartao'>{serie_cpl}</div>",

        "<h2>PL e caixa potencial</h2>",
        "<p class='nota'>As duas séries dividem o mesmo eixo — são a mesma "
        "unidade (R$ mil), então a distância entre elas significa alguma coisa.</p>",
        f"<div class='cartao'>{serie_pl}"
        "<div class='legenda'>"
        "<span><i class='ch' style='background:var(--cat1)'></i>PL</span>"
        "<span><i class='ch' style='background:var(--cat2)'></i>"
        "Caixa potencial</span></div></div>",
    ]

    # Cota, retorno e VaR ficam ao lado do historico de liquidez de proposito:
    # sao a mesma pergunta vista por outro lado -- o quanto o fundo pode perder,
    # contra o quanto ele consegue converter em caixa.
    cota_svg, cota_leg, cota_nota = _svg_cota(ctx["serie_cota"], ctx.get("cdi"))
    met = ctx["var_cotas"]
    if cota_svg or not met.empty:
        janela = ""
        if not met.empty:
            janela = (f" Janela de {_data_br(str(met['de'].min().date()))} a "
                      f"{_data_br(str(met['ate'].max().date()))}.")
        partes += [
            "<h2>Rentabilidade por classe</h2>",
            "<p class='nota'>Rentabilidade <b>acumulada</b> desde o início da "
            "janela, e não o nível da cota: a cota bruta das classes vive em "
            "ordens de grandeza diferentes e no mesmo eixo a menor viraria uma "
            "reta. A linha <b>amarela tracejada</b> é o CDI acumulado no mesmo "
            "período — classe abaixo dela rendeu menos que o custo do dinheiro. "
            f"A referência em <b>0%</b> é o ponto de partida.{janela}</p>",
            f"<div class='cartao'>{cota_svg}{cota_leg}{cota_nota}</div>",

            "<h2>Desempenho contra o CDI</h2>",
            f"<p class='nota'><b>Início da cota</b> é a primeira observação da "
            f"série <b>daquela classe</b> — não a constituição do fundo. É ela "
            f"que explica por que <b>Ret per.</b> cobre recortes diferentes "
            f"na mesma tabela. <b>Ret per.</b> é a janela inteira da série "
            f"(~13 meses, e varia por classe). <b>Ret 12m</b> são os doze "
            f"meses encerrados na data-base. <b>Ret a.a.</b> é o <b>ano "
            f"calendário</b>, de 1º de janeiro até a data-base, e <b>Ret a.m.</b> "
            f"o <b>mês calendário</b>, do dia 1º do mês dela — os dois são "
            f"calendário, então encolhem e reiniciam na virada, e nenhum é "
            f"anualização. Todos são retorno <b>realizado</b>, "
            f"nunca projeção, e saem do produto dos retornos diários no recorte "
            f"— não da razão entre as pontas da cota, que embutiria as quebras "
            f"de cadastro que o filtro tirou. Classe cuja série começa depois do "
            f"início da janela sai com <span class='parcial'>—</span>: ali não há "
            f"a janela para medir, e repetir o número do período sob outro "
            f"rótulo seria inventá-la.</p>",
            f"<p class='nota'>O <b>CDI</b> tem as <b>mesmas quatro janelas</b> "
            f"do fundo, medidas nas <b>mesmas datas</b> — a leitura é por pares: "
            f"Ret per. contra CDI per., Ret 12m contra CDI 12m, e assim "
            f"por diante. Aqui <b>a.a.</b> e <b>a.m.</b> também são ano e mês "
            f"<b>calendário</b>, não anualização. A cadeia: o <code>riscogc</code> "
            f"entrega a taxa em % ao ano na base 252, que vira fator diário por "
            f"<b>(1 + taxa/100)<sup>1/252</sup></b>; os fatores são acumulados "
            f"no intervalo <b>entre uma observação da cota e a anterior</b> — "
            f"assim feriado e fim de semana entram certo, porque a própria série "
            f"de CDI é o calendário de dias úteis; e o produto deles dentro de "
            f"cada janela dá a coluna. Como o CDI é medido nas datas da série, "
            f"onde a classe não alcança a janela ele sai com "
            f"<span class='parcial'>—</span> igual ao retorno.</p>",
            "<p class='nota'>O <b>Sharpe</b> é o retorno <b>anualizado</b> menos "
            "o <b>CDI anualizado</b>, dividido pela Vol. a.a. Nenhum dos dois "
            "anualizados é coluna — eles projetam para doze meses o que a janela "
            "tiver, e em série curta isso distorce —, mas os três termos da "
            "divisão estão no <b>tooltip do Sharpe</b>, para a conta se "
            "conferir. Rendimento abaixo do CDI dá Sharpe negativo, como "
            "deve.</p>",
            "<p class='nota'><b>% do CDI</b> é a divisão direta das duas "
            "colunas: <b>Ret per. ÷ CDI per.</b> — as duas estão na tabela, "
            "então a conta se confere a olho. O denominador é o do <b>período</b>, "
            "a janela inteira da série, e não o do ano ou o do mês. Cada classe "
            "é comparada com o CDI da <b>janela dela</b>: num fundo com classes "
            "de datas diferentes, o CDI per. muda de "
            "linha para linha, e é isso que torna a comparação justa. Acima de "
            "<b>100%</b> a classe rendeu mais que o custo do dinheiro no "
            "período; abaixo, rendeu menos.</p>",
            f"<div class='cartao'>{_tabela_desempenho(met)}"
            f"{_notas_sharpe(met)}</div>",
        ]

        ref = ctx.get("classe_ref")
        sh_dia = _svg_sharpe(ctx["sharpe_acum"])
        sh_mes = _svg_sharpe(ctx["sharpe_mensal"], mensal=True)
        if sh_dia or sh_mes:
            qual = (f"a classe <b>{_e(str(ref).title())}</b>" if ref else
                    "a classe de referência")
            partes += [
                "<h2>Sharpe acumulado</h2>",
                f"<p class='nota'>Calculado sobre {qual}. Num FIDC, sênior e "
                f"mezanino são remuneradas a <b>CDI + taxa por contrato</b> — o "
                f"retorno delas é quase determinístico e a volatilidade tende a "
                f"zero, então o Sharpe sai sempre alto e mede a existência do "
                f"contrato, não a gestão. Quem absorve resultado é a júnior, e é "
                f"o Sharpe dela que diz alguma coisa. Fundo sem estrutura de "
                f"classes usa a cota única.</p>",
            ]
            if sh_dia:
                partes += [
                    "<h3>Diário, acumulado desde o início da série</h3>",
                    "<p class='nota'>Janela que <b>cresce</b>, não que desliza: "
                    "cada ponto responde “considerando tudo o que se sabe até "
                    "aqui, quanto rendeu acima do CDI por unidade de risco”. O "
                    "último ponto é, por construção, o Sharpe da tabela acima. "
                    "Como o acumulado estabiliza, movimento perto do fim "
                    "significa mudança grande, não ruído.</p>",
                    f"<div class='cartao'>{sh_dia}</div>",
                ]
            if sh_mes:
                partes += [
                    "<h3>Mensal</h3>",
                    "<p class='nota'>O retorno de cada mês é a composição dos "
                    "retornos diários dele, e o Sharpe usa todos os meses até "
                    "ali, anualizado por √12. Retorno diário de cota de FIDC é "
                    "quase todo accrual com saltos ocasionais de provisão, e o "
                    "desvio diário acaba medindo o calendário de reconhecimento "
                    "mais que o risco; agregando em mês, o salto se dilui no "
                    "período a que pertence. Por isso os dois números não "
                    "coincidem — e o mensal costuma ser o mais alto.</p>",
                    f"<div class='cartao'>{sh_mes}</div>",
                ]

        partes += [
            "<h2>Volatilidade e VaR paramétrico</h2>",
            f"<p class='nota'>VaR de 1 dia a <b>{_pct(vc.NIVEL, 0)}</b> de "
            f"confiança, sobre volatilidade EWMA (λ={vc.LAMBDA}). A coluna "
            f"<b>VaR hist.</b> é o percentil {_pct(1 - vc.NIVEL, 0)} da amostra "
            f"e não entra no cálculo: serve para aferir se a premissa normal se "
            f"sustenta. Quando os dois se afastam, a coluna <b>Leitura</b> diz "
            f"por quê — passe o mouse.</p>",
            f"<div class='cartao'>{_tabela_var(met)}{_notas_var(met)}</div>",
        ]

        # O grafico vem DEPOIS da tabela de proposito: a tabela publica o
        # numero, e o grafico e a conferencia dele contra o que a serie fez.
        blocos_rv = _blocos_retorno_var(ctx.get("retorno_var"), met)
        if blocos_rv:
            partes += [
                "<h2>Retorno diário contra o VaR do dia</h2>",
                f"<p class='nota'>Cada barra é o retorno de um dia; a linha "
                f"<b>vermelha tracejada</b> é a perda limite que o VaR previa "
                f"<b>para aquele dia</b>, estimada só com o que se sabia até a "
                f"véspera — VaR calculado com o próprio dia dentro engorda junto "
                f"com a perda e esconde o furo. Barra <b>abaixo</b> da linha é "
                f"uma <b>exceção</b>: ela muda de cor e ganha marcador. A "
                f"{_pct(vc.NIVEL, 0)} de confiança, exceção não é defeito — "
                f"{_pct(1 - vc.NIVEL, 0)} dos dias devem furar. Defeito é a "
                f"contagem não bater com isso, e é o que a linha abaixo de cada "
                f"gráfico afere — mas ela só julga o modelo quando a tabela "
                f"acima deu o paramétrico como <b>coerente</b>; nas demais, o "
                f"número de furos descreve a série, não o modelo. Os primeiros "
                f"{vc.MIN_OBS} dias saem sem linha: é o aquecimento da "
                f"volatilidade EWMA. Só o lado da perda é desenhado — o VaR "
                f"paramétrico é simétrico, então a linha de cima seria o "
                f"espelho da de baixo. O <b>VaR 1d</b> da tabela acima não é o "
                f"último ponto desta linha, e sim o próximo: ele usa a janela "
                f"inteira, então é o limite que vale para o próximo dia útil.</p>",
                blocos_rv,
            ]

    partes += [
        "<h2>Enquadramento data a data</h2>",
        "<p class='nota'>Um quadradinho por data. Serve para achar quando o "
        "fundo cruzou cada limite.</p>",
        f"<div class='cartao'>{_svg_tira(datas, tiras)}"
        "<div class='legenda'>"
        "<span><i class='ch' style='background:var(--bom)'></i>Enquadrado</span>"
        "<span><i class='ch' style='background:var(--critico)'></i>Desenquadrado</span>"
        "<span><i class='ch' style='background:var(--neutro)'></i>Sem dado</span>"
        "</div></div>",
    ]

    # O grafico "Fluxo futuro por mes" saiu em 29/09/2026: o fluxo projetado passou
    # a compor a escada de liquidez, na faixa de prazo de cada parcela, e um
    # segundo grafico dos mesmos numeros em outro eixo so dividia a leitura.

    # A tabela "Historico completo" saiu em 29/09/2026: o mesmo historico ja esta
    # nos graficos de serie e na tira de enquadramento, e a tabela repetia linha
    # por linha o que os graficos mostram de relance.

    partes += [
        "<footer>Gerado por <code>python -m liquidez painel</code> em "
        f"{dt.datetime.now().strftime('%d/%m/%Y %H:%M')} a partir da camada de fatos. "
        "Não editar à mão: qualquer alteração se perde na próxima geração.</footer>",
        "</div></div>",
    ]

    return (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_e(ctx['nome'])} — Liquidez {_data_br(ctx['data'])}</title>"
        f"<script>{pn.SCRIPT_CEDO}</script>"
        f"<style>{pn.ESTILO}{ESTILO_FUNDO}</style></head><body>"
        + "".join(partes)
        + f"<script>{pn.SCRIPT}</script>"
        + "</body></html>"
    )


# --- Geracao ----------------------------------------------------------------


def gerar_todos(destino_bi: Path = ft.DIR_BI, saida: Path | None = None,
                somente: str | None = None) -> list[Path]:
    """Um relatorio por fundo presente em qualquer data da base.

    Fundo que saiu da carteira no meio do periodo tambem ganha pagina: o
    historico dele continua valendo, e a pagina diz em que data ele parou.

    `somente` (CNPJ) gera so aquele fundo, deixando os demais arquivos intactos.
    """
    destino_bi = Path(destino_bi)
    hist = carregar_historico(destino_bi)
    saida = Path(saida) if saida else destino_bi / PASTA_FUNDOS
    saida.mkdir(parents=True, exist_ok=True)

    data_painel = max(hist["data"])
    particoes = _Particoes(destino_bi)

    # A serie de cotas e o VaR sao calculados UMA vez para todos os fundos: sao
    # 30 mil linhas no total, e refazer o groupby por fundo custaria 100x mais
    # que fatiar um DataFrame ja pronto.
    serie = vc.carregar(destino_bi)
    cdi = vc.carregar_cdi(destino_bi)
    metricas = vc.metricas(serie, cdi) if not serie.empty else pd.DataFrame()
    # Ponto a ponto para o grafico de retorno contra VaR. Mesma serie e mesma
    # formula de vol da tabela, so que reestimada dia a dia e sempre com a
    # informacao da vespera -- o numero unico da tabela e o passo SEGUINTE desta
    # linha, o limite que vale para o proximo dia util.
    retorno_var = vc.retorno_vs_var(serie) if not serie.empty else pd.DataFrame()
    # O Sharpe do fundo e o da classe JUNIOR (ou da cota unica). Recortar a
    # serie uma vez aqui evita repetir a escolha em cada um dos dois graficos.
    if not serie.empty and not cdi.empty:
        ref = vc.serie_referencia(serie)
        classes_ref = ref.groupby("cnpj_fundo")["classe"].first().to_dict()
        acum = vc.sharpe_acumulado(ref, cdi)
        mensal = vc.sharpe_mensal(ref, cdi)
    else:
        ref, classes_ref = pd.DataFrame(), {}
        acum = mensal = pd.DataFrame()
    if not serie.empty and cdi.empty:
        log.info("Sem serie de CDI em %s; relatorios saem sem Sharpe.",
                 vc.caminho_cdi(destino_bi))
    if serie.empty:
        log.info("Sem serie de cotas em %s; relatorios saem sem retorno e VaR. "
                 "Rode 'python -m liquidez cotas serie' para popular.",
                 vc.caminho(destino_bi))

    # Ordem de navegacao: PL decrescente na data mais recente de cada fundo --
    # a mesma ordem da tabela do painel, para anterior/proximo nao surpreender.
    ultimos = (hist.sort_values("data").groupby("cnpj_fundo").tail(1)
               .sort_values("pl_mil", ascending=False))
    ordem = list(zip(ultimos["cnpj_fundo"], ultimos["nome_fundo"]))

    gerados = []
    for i, (cnpj, nome) in enumerate(ordem):
        # `somente` reescreve UM arquivo e deixa os outros como estao. Existe para
        # iterar layout sem refazer os 100. A navegacao anterior/proximo continua
        # correta porque `ordem` segue completa -- o filtro e so na escrita.
        if somente is not None and _so_digitos(cnpj) != _so_digitos(somente):
            continue
        do_fundo = hist[hist["cnpj_fundo"] == cnpj].sort_values("data")
        data = do_fundo["data"].iloc[-1]
        ctx = {
            "cnpj": cnpj,
            "nome": nome,
            "hist": do_fundo.reset_index(drop=True),
            "data": data,
            "data_painel": data_painel,
            "matriz": particoes.do_fundo("fato_matriz", data, cnpj),
            "fluxo": particoes.do_fundo("fato_fluxo", data, cnpj),
            "relatorio": particoes.do_fundo("fato_relatorio", data, cnpj),
            "prazo_cotizacao": do_fundo["prazo_cotizacao"].iloc[-1]
            if "prazo_cotizacao" in do_fundo.columns else None,
            "serie_cota": _serie_do_fundo(serie, cnpj),
            "var_cotas": (metricas[metricas["cnpj_fundo"] == _so_digitos(cnpj)]
                          if not metricas.empty else pd.DataFrame()),
            "retorno_var": (
                retorno_var[retorno_var["cnpj_fundo"] == _so_digitos(cnpj)]
                if not retorno_var.empty else pd.DataFrame()),
            "sharpe_acum": (acum[acum["cnpj_fundo"] == _so_digitos(cnpj)]
                            if not acum.empty else pd.DataFrame()),
            "sharpe_mensal": (mensal[mensal["cnpj_fundo"] == _so_digitos(cnpj)]
                              if not mensal.empty else pd.DataFrame()),
            "classe_ref": classes_ref.get(_so_digitos(cnpj)),
            "cdi": cdi,
            "anterior": ordem[i - 1] if i > 0 else None,
            "proximo": ordem[i + 1] if i + 1 < len(ordem) else None,
        }
        caminho = saida / arquivo_do_fundo(cnpj)
        caminho.write_text(_montar(ctx), encoding="utf-8")
        gerados.append(caminho)

    log.info("Relatorios individuais: %d fundo(s) em %s", len(gerados), saida)
    return gerados
