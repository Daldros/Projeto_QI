"""Cronograma contratual das operacoes, lido da planilha de conciliacao (.xlsb).

Quem manda e a CARTEIRA, nao o arquivo: a lista de operacoes vem das linhas do
produto (DC) com seu `Codigo_Op`, e so as abas correspondentes sao lidas. Aba que
a carteira nao pediu nao entra no cadastro; `Codigo_Op` sem aba e reportado.

Cada aba cujo nome contem um codigo de operacao (`BXXX` ou `CXXX`) e uma
operacao. Dali saem duas coisas:

  - os parametros do contrato (principal, indexador, taxas, Pre ou CDI+Pre);
  - o cronograma de vencimentos e amortizacoes (colunas S e V).

A aba tem duas representacoes do mesmo contrato: o bloco da esquerda e a grade
diaria, uma linha por dia; o da direita e o cronograma, uma linha por parcela.
Aqui se usa o cronograma -- e o contratual, que e o que interessa para projetar
o fluxo futuro.

As colunas do cronograma sao localizadas pelos rotulos do cabecalho, nao fixadas:
o bloco da esquerda tem uma coluna a mais em algumas abas e o da direita desloca
junto. No LIMAJ, `Vencimento` cai em S numa aba e em T noutra.

Le apenas as celulas necessarias, nao a grade inteira: as abas tem centenas de
linhas e montar o dicionario completo de todas custa minutos.

Isto e cadastro, nao dado de carga: muda pouco e e mantido por decisao do
usuario. Por isso fica em `config/cronograma/` em CSV -- inspecionavel e
versionavel, como o cadastro de fundos e a tabela de prazos -- e nao em
`dados/`, que a extracao apaga a cada carga.

A planilha so e lida quando o usuario manda (`cronograma importar`). Junto do
cadastro grava-se a impressao digital do arquivo usado, o que permite avisar
quando a planilha muda sem que a importacao tenha sido refeita.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

log = logging.getLogger(__name__)

DIR_CADASTRO = Path("config") / "cronograma"
DIR_MANUAIS = DIR_CADASTRO / "manuais"
DIR_BACKUP = DIR_CADASTRO / "_anterior"
DIR_PLANILHAS = Path("config") / "planilhas"
DIR_PROCESSADOS = DIR_PLANILHAS / "processados"
PADRAO_PLANILHA = "*.xls*"

# O nome do arquivo depositado tem que trazer o CNPJ do fundo -- com ou sem
# pontuacao. As bordas evitam que digitos vizinhos entrem no numero.
PADRAO_CNPJ = re.compile(
    r"(?<!\d)(\d{2})[.\-\s]?(\d{3})[.\-\s]?(\d{3})[/\-\s]?(\d{4})[-.\s]?(\d{2})(?!\d)"
)

# A numeracao das operacoes comecou em B; esgotado o B999999, passou para o C.
# Aceitar so o C descartava as operacoes antigas em silencio -- elas sumiam da
# projecao sem nem aparecer como pendencia.
PADRAO_CODIGO = re.compile(r"[BC]\d{3,}")

# --- Posicoes na aba (0-indexed) --------------------------------------------
# Metadados ficam no bloco superior esquerdo, em celulas fixas.
CELULAS_METADADOS: dict[str, tuple[int, int]] = {
    "emissor": (0, 1),
    "tp_instrumento": (1, 1),
    "vl_principal": (2, 1),
    "dt_emissao": (3, 1),
    "dt_vencimento": (4, 1),
    "nr_contrato": (5, 1),
    "codigo_op": (3, 2),
    "cd_papel_b3": (5, 2),
    "dt_inicio_adm": (1, 3),
    "nm_adm": (3, 3),
    "in_pre_com_pos": (5, 3),
    "indexador": (1, 4),
    "taxa_aa": (3, 4),
    "taxa_pre_ad": (4, 4),   # E5: taxa diaria usada quando e Pre com Pos
    "taxa_am": (3, 5),
    "taxa_ad": (4, 5),       # E6: taxa diaria usada quando e somente Pre
}
LINHA_MAX_METADADOS = max(linha for linha, _ in CELULAS_METADADOS.values())
COLUNA_MAX_METADADOS = max(coluna for _, coluna in CELULAS_METADADOS.values())

# O cronograma NAO fica em colunas fixas. O bloco da esquerda (grade diaria) tem
# uma coluna a mais em algumas abas -- p.ex. "Pagamento Parcela" --, e o bloco da
# direita desloca junto: no LIMAJ, Vencimento aparece em S numa aba e em T noutra.
# Por isso as colunas sao localizadas pelos rotulos da linha de cabecalho.
LINHA_CABECALHO = 6      # linha 7 do Excel
LINHA_CRONOGRAMA = 7     # linha 8 do Excel; a 1a data e a da operacao

ROTULO_VENCIMENTO = "vencimento"
ROTULO_AMORTIZACAO = "amortizacao"
# Usados so se o cabecalho nao for encontrado (layout do AVANTI).
COL_VENCIMENTO_PADRAO = 18   # S
COL_AMORTIZACAO_PADRAO = 21  # V

CAMPOS_DATA = ("dt_emissao", "dt_vencimento", "dt_inicio_adm")
CAMPOS_NUMERO = ("vl_principal", "taxa_aa", "taxa_am", "taxa_ad", "taxa_pre_ad")

COLUNAS_OPERACOES = [
    "cnpj_fundo", "codigo_op", "aba", "emissor", "tp_instrumento", "nr_contrato",
    "cd_papel_b3", "vl_principal", "dt_emissao", "dt_vencimento", "dt_inicio_adm",
    "dt_operacao", "nm_adm", "indexado", "indexador", "taxa_aa", "taxa_am",
    "taxa_ad", "taxa_pre_ad", "taxa_ad_efetiva", "base_taxa", "n_parcelas",
    "vl_amortizacao_total",
]
COLUNAS_PARCELAS = [
    "cnpj_fundo", "codigo_op", "nr_parcela", "dt_vencimento", "vl_amortizacao",
]


class ErroPlanilha(RuntimeError):
    pass


# --- Conversoes -------------------------------------------------------------

def _para_data(valor: Any) -> dt.date | None:
    """Serial do Excel -> date. Devolve None para vazio ou texto."""
    if valor is None or valor == "" or isinstance(valor, str):
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return (dt.datetime(1899, 12, 30) + dt.timedelta(days=float(valor))).date()
    return None


def _para_numero(valor: Any) -> float | None:
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _texto(valor: Any) -> str | None:
    if valor is None or valor == "":
        return None
    return str(valor).strip()


def codigo_da_aba(nome: str) -> str | None:
    """Codigo da operacao no nome da aba ('NC_C227129' -> 'C227129').

    Vale para os dois prefixos de numeracao, B (antigo) e C.
    """
    achado = PADRAO_CODIGO.search(nome or "")
    return achado.group(0) if achado else None


def normalizar_codigo(valor: Any) -> str | None:
    """Mesmo tratamento para o Codigo_Op da carteira, para as chaves casarem."""
    if valor is None:
        return None
    return codigo_da_aba(str(valor).strip().upper())


# --- Leitura da planilha ----------------------------------------------------

def localizar_planilha(diretorio: Path = DIR_PLANILHAS) -> Path | None:
    """Planilha mais recente do diretorio de planilhas, se houver."""
    candidatos = sorted(
        (c for c in Path(diretorio).glob(PADRAO_PLANILHA) if not c.name.startswith("~$")),
        key=lambda caminho: caminho.stat().st_mtime,
        reverse=True,
    )
    return candidatos[0] if candidatos else None


# --- Pasta de entrada -------------------------------------------------------

def cnpj_do_arquivo(caminho: Path | str) -> str | None:
    """CNPJ (so digitos) achado no nome do arquivo; None se nao houver."""
    achado = PADRAO_CNPJ.search(Path(caminho).stem)
    return "".join(achado.groups()) if achado else None


def planilhas_na_entrada(diretorio: Path = DIR_PLANILHAS) -> list[Path]:
    """Arquivos depositados na pasta de entrada, do mais antigo ao mais novo.

    Ignora os temporarios do Excel (`~$...`) e a subpasta de processados.
    """
    base = Path(diretorio)
    if not base.exists():
        return []
    return sorted(
        (c for c in base.glob(PADRAO_PLANILHA)
         if c.is_file() and not c.name.startswith("~$")),
        key=lambda caminho: caminho.stat().st_mtime,
    )


def salvar_backup(cnpj_fundo: str, diretorio: Path = DIR_CADASTRO) -> Path | None:
    """Guarda a versao atual do cadastro antes de sobrescrever. None se nao havia."""
    import shutil

    caminho = arquivo(cnpj_fundo, diretorio)
    if not caminho.exists():
        return None
    destino = Path(DIR_BACKUP)
    destino.mkdir(parents=True, exist_ok=True)
    shutil.copy2(caminho, destino / caminho.name)
    return destino


def arquivar(caminho: Path, destino: Path = DIR_PROCESSADOS) -> Path:
    """Move o arquivo processado para a subpasta de processados, com carimbo."""
    import shutil

    caminho = Path(caminho)
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    carimbo = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    alvo = destino / f"{caminho.stem}_{carimbo}{caminho.suffix}"
    shutil.move(str(caminho), str(alvo))
    return alvo


def _abrir(caminho: Path):
    from pyxlsb import open_workbook

    try:
        return open_workbook(str(caminho))
    except PermissionError as exc:
        raise ErroPlanilha(
            f"{Path(caminho).name} esta aberto no Excel ou bloqueado. Feche e rode de novo."
        ) from exc


def indexar_abas(caminho: Path) -> dict[str, str]:
    """Mapa codigo -> nome da aba. So le a lista de abas, nao o conteudo."""
    with _abrir(caminho) as wb:
        nomes = list(wb.sheets)
    indice: dict[str, str] = {}
    for nome in nomes:
        codigo = codigo_da_aba(nome)
        if codigo is None:
            continue
        if codigo in indice:
            log.warning("Codigo %s aparece em mais de uma aba: %s e %s (usando a 1a)",
                        codigo, indice[codigo], nome)
            continue
        indice[codigo] = nome
    return indice


def _normalizar_rotulo(valor: Any) -> str:
    import unicodedata

    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = texto.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", texto).strip().lower()


def _colunas_do_cronograma(cabecalho: dict[int, Any]) -> tuple[int, int] | None:
    """Colunas de Vencimento e Amortizacao pelos rotulos do cabecalho.

    A de amortizacao tem que vir DEPOIS da de vencimento: o bloco da esquerda tem
    uma coluna 'Armotizacao' (com o erro de digitacao da planilha) que nao e a do
    cronograma, e nao se pode confiar so na grafia para distinguir.

    Devolve None quando os rotulos nao estao la -- e sinal de que a aba usa outro
    modelo (um CRA, por exemplo, tem 'PMT'/'Saldo Bruto' e nenhum dos dois). Nao
    se chuta posicao: ler a coluna errada produz data em 1900 e taxa de 100% ao
    dia, que e pior do que nao ler.
    """
    rotulos = {coluna: _normalizar_rotulo(valor) for coluna, valor in cabecalho.items()}
    col_venc = next(
        (c for c, r in sorted(rotulos.items()) if r == ROTULO_VENCIMENTO), None
    )
    if col_venc is None:
        return None
    col_amort = next(
        (c for c, r in sorted(rotulos.items())
         if r == ROTULO_AMORTIZACAO and c > col_venc),
        None,
    )
    return None if col_amort is None else (col_venc, col_amort)


def _varrer_aba(sheet) -> tuple[dict[tuple[int, int], Any], list[tuple[Any, Any]], bool]:
    """Celulas de metadados, linhas do cronograma e se o layout foi reconhecido.

    Evita montar o dicionario da aba inteira -- sao centenas de linhas por
    centenas de colunas em cada uma das dezenas de abas. As colunas do cronograma
    saem do cabecalho, que vem antes das linhas de dados na mesma varredura.
    """
    metadados: dict[tuple[int, int], Any] = {}
    cronograma: list[tuple[Any, Any]] = []
    colunas: tuple[int, int] | None = None

    for i, linha in enumerate(sheet.rows()):
        if i <= LINHA_MAX_METADADOS:
            for celula in linha:
                if celula.c <= COLUNA_MAX_METADADOS and celula.v is not None:
                    metadados[(i, celula.c)] = celula.v
        if i == LINHA_CABECALHO:
            cabecalho = {celula.c: celula.v for celula in linha if celula.v is not None}
            colunas = _colunas_do_cronograma(cabecalho)
            if colunas is None:
                return metadados, [], False
        if i < LINHA_CRONOGRAMA or colunas is None:
            continue
        col_venc, col_amort = colunas
        vencimento = amortizacao = None
        for celula in linha:
            if celula.c == col_venc:
                vencimento = celula.v
            elif celula.c == col_amort:
                amortizacao = celula.v
        if vencimento is not None or amortizacao is not None:
            cronograma.append((vencimento, amortizacao))
    return metadados, cronograma, colunas is not None


def _metadados(celulas: dict[tuple[int, int], Any]) -> dict[str, Any]:
    registro: dict[str, Any] = {}
    for campo, posicao in CELULAS_METADADOS.items():
        valor = celulas.get(posicao)
        if campo in CAMPOS_DATA:
            registro[campo] = _para_data(valor)
        elif campo in CAMPOS_NUMERO:
            registro[campo] = _para_numero(valor)
        else:
            registro[campo] = _texto(valor)
    indexado = (registro.get("in_pre_com_pos") or "").strip().lower() == "sim"
    registro["indexado"] = indexado
    registro["taxa_ad_efetiva"] = registro["taxa_pre_ad"] if indexado else registro["taxa_ad"]
    return registro


def _cronograma(linhas: list[tuple[Any, Any]]) -> tuple[dt.date | None, list[dict[str, Any]]]:
    """Datas da coluna S e amortizacoes da coluna V.

    A 1a data e a da operacao, nao uma parcela. Amortizacoes sao somadas por
    data, porque a mesma data pode aparecer em mais de uma linha.
    """
    datas: list[dt.date] = []
    por_data: dict[dt.date, float] = {}
    for bruto_venc, bruto_amort in linhas:
        data = _para_data(bruto_venc)
        if data is None:
            continue
        if data not in por_data:
            datas.append(data)
            por_data[data] = 0.0
        valor = _para_numero(bruto_amort)
        if valor:
            por_data[data] += valor

    if not datas:
        return None, []

    datas.sort()
    dt_operacao = datas[0]
    parcelas = [
        {"nr_parcela": i, "dt_vencimento": data, "vl_amortizacao": por_data[data]}
        for i, data in enumerate(datas[1:], start=1)
    ]
    return dt_operacao, parcelas


def _abas_alvo(indice: dict[str, str], codigos: set[str] | None) -> Iterator[tuple[str, str]]:
    for codigo, aba in indice.items():
        if codigos is None or codigo in codigos:
            yield codigo, aba


@dataclass
class ResultadoImportacao:
    """O que a leitura da planilha produziu, e o que ficou pelo caminho."""

    operacoes: pd.DataFrame
    parcelas: pd.DataFrame
    sem_aba: list[str]                 # pedido pela carteira, sem aba no arquivo
    aba_vazia: list[tuple[str, str]]   # aba existe mas nao tem cronograma
    layout_desconhecido: list[tuple[str, str]]  # aba com outro modelo (CRA etc.)
    fora_da_carteira: list[str]        # aba no arquivo que a carteira nao pediu


def importar(
    caminho: Path,
    cnpj_fundo: str,
    codigos: set[str] | None = None,
) -> ResultadoImportacao:
    """Le a planilha e devolve o cadastro das operacoes pedidas.

    `codigos` e a lista vinda da carteira -- so essas abas sao lidas. Com None,
    le todas as abas com codigo (util quando nao ha carga disponivel).
    """
    caminho = Path(caminho)
    indice = indexar_abas(caminho)
    if not indice:
        raise ErroPlanilha(f"Nenhuma aba com codigo CXXX em {caminho.name}.")

    operacoes: list[dict[str, Any]] = []
    parcelas: list[dict[str, Any]] = []
    ignoradas: list[tuple[str, str]] = []
    nao_reconhecidas: list[tuple[str, str]] = []

    with _abrir(caminho) as wb:
        for codigo, aba in _abas_alvo(indice, codigos):
            with wb.get_sheet(aba) as sheet:
                celulas, linhas, layout_ok = _varrer_aba(sheet)

            # Layout desconhecido (p.ex. um CRA, que tem PMT/Saldo Bruto no lugar
            # de Vencimento/Amortizacao): nao da para extrair cronograma dali.
            if not layout_ok:
                nao_reconhecidas.append((codigo, aba))
                continue

            registro = _metadados(celulas)
            dt_operacao, cronograma = _cronograma(linhas)

            # Aba sem cronograma: copia esquecida ou modelo em branco. Nao entra
            # no cadastro, mas e reportada -- se o codigo veio da carteira, e
            # problema a resolver na planilha, nao algo a engolir em silencio.
            if not cronograma:
                ignoradas.append((codigo, aba))
                continue

            codigo_meta = normalizar_codigo(registro.get("codigo_op"))
            if codigo_meta and codigo_meta != codigo:
                log.warning("Aba %s: codigo do nome (%s) difere do da celula (%s); usando o do nome.",
                            aba, codigo, codigo_meta)

            registro.update(
                cnpj_fundo=cnpj_fundo, codigo_op=codigo, aba=aba,
                dt_operacao=dt_operacao, n_parcelas=len(cronograma),
                vl_amortizacao_total=sum(p["vl_amortizacao"] for p in cronograma),
            )
            operacoes.append(registro)
            for p in cronograma:
                parcelas.append({"cnpj_fundo": cnpj_fundo, "codigo_op": codigo, **p})

    df_ops = pd.DataFrame(operacoes)
    if not df_ops.empty:
        df_ops = df_ops[[c for c in COLUNAS_OPERACOES if c in df_ops.columns]]
        df_ops = df_ops.sort_values("codigo_op").reset_index(drop=True)
    df_parcelas = pd.DataFrame(parcelas)
    if not df_parcelas.empty:
        df_parcelas = df_parcelas[COLUNAS_PARCELAS]
        df_parcelas = df_parcelas.sort_values(["codigo_op", "nr_parcela"]).reset_index(drop=True)

    sem_aba = sorted(codigos - set(indice)) if codigos is not None else []
    fora_da_carteira = sorted(set(indice) - codigos) if codigos is not None else []

    log.info("Cronograma lido: %d operacao(oes), %d parcela(s), de %s",
             len(df_ops), len(df_parcelas), caminho.name)
    if fora_da_carteira:
        log.info("%d aba(s) fora da carteira, nao lidas: %s",
                 len(fora_da_carteira), ", ".join(fora_da_carteira))

    return ResultadoImportacao(
        operacoes=df_ops,
        parcelas=df_parcelas,
        sem_aba=sem_aba,
        aba_vazia=ignoradas,
        layout_desconhecido=nao_reconhecidas,
        fora_da_carteira=fora_da_carteira,
    )


# --- Persistencia -----------------------------------------------------------

def arquivo(cnpj_fundo: str, diretorio: Path = DIR_CADASTRO) -> Path:
    """Cadastro do fundo: um JSON com as operacoes e suas parcelas aninhadas.

    JSON e nao CSV por tres motivos: usa ponto decimal sempre, entao abrir no
    Excel pt-BR nao desmancha os numeros; as parcelas ficam dentro da operacao,
    sem dois arquivos para manter em sincronia; e e o mesmo formato do cadastro
    manual, entao ha um leitor so.
    """
    numerico = re.sub(r"\D", "", str(cnpj_fundo))
    return Path(diretorio) / f"{numerico}.json"


def _limpar(valor):
    """Valor pronto para JSON: NaN vira null, numpy vira tipo nativo."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, (pd.Timestamp, dt.date)):
        return str(valor)[:10]
    if hasattr(valor, "item"):
        try:
            return valor.item()
        except (AttributeError, ValueError):
            pass
    return valor if not pd.isna(valor) else None


def impressao_digital(caminho: Path) -> dict:
    """Identidade do arquivo usado na importacao, para detectar mudanca depois."""
    caminho = Path(caminho)
    dados = caminho.read_bytes()
    info = caminho.stat()
    return {
        "arquivo": caminho.name,
        "caminho": str(caminho),
        "tamanho": info.st_size,
        "modificado_em": dt.datetime.fromtimestamp(info.st_mtime).isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(dados).hexdigest(),
    }


def gravar(
    df_ops: pd.DataFrame,
    df_parcelas: pd.DataFrame,
    cnpj_fundo: str,
    planilha: Path,
    diretorio: Path = DIR_CADASTRO,
) -> Path:
    """Grava o cadastro do fundo num JSON, com as parcelas dentro da operacao."""
    caminho = arquivo(cnpj_fundo, diretorio)
    caminho.parent.mkdir(parents=True, exist_ok=True)

    por_operacao: dict[str, list[dict]] = {}
    if not df_parcelas.empty:
        for registro in df_parcelas.sort_values(["codigo_op", "nr_parcela"]).to_dict("records"):
            por_operacao.setdefault(str(registro["codigo_op"]), []).append({
                "dt_vencimento": _limpar(registro["dt_vencimento"]),
                "vl_amortizacao": _limpar(registro["vl_amortizacao"]),
            })

    operacoes = []
    for registro in df_ops.to_dict("records"):
        item = {c: _limpar(registro.get(c)) for c in COLUNAS_OPERACOES if c in registro}
        item["parcelas"] = por_operacao.get(str(registro.get("codigo_op")), [])
        operacoes.append(item)

    conteudo = {
        "cnpj_fundo": cnpj_fundo,
        "importado_em": dt.datetime.now().isoformat(timespec="seconds"),
        "planilha": impressao_digital(planilha),
        "total_operacoes": len(operacoes),
        "total_parcelas": int(len(df_parcelas)),
        "operacoes": operacoes,
    }
    caminho.write_text(json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return caminho


def fundos_com_cadastro(diretorio: Path = DIR_CADASTRO) -> list[str]:
    """CNPJ (so digitos) dos fundos que ja tem cronograma importado."""
    base = Path(diretorio)
    if not base.exists():
        return []
    # glob nao recursivo: as subpastas 'manuais' e '_anterior' ficam de fora.
    return sorted(c.stem for c in base.glob("*.json") if c.stem.isdigit())


def ler_cadastro(cnpj_fundo: str, diretorio: Path = DIR_CADASTRO) -> dict | None:
    caminho = arquivo(cnpj_fundo, diretorio)
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


def ler_origem(cnpj_fundo: str, diretorio: Path = DIR_CADASTRO) -> dict | None:
    """Metadados da importacao: quando, de qual planilha, com que impressao digital."""
    dados = ler_cadastro(cnpj_fundo, diretorio)
    if dados is None:
        return None
    return {
        "cnpj_fundo": dados.get("cnpj_fundo"),
        "importado_em": dados.get("importado_em"),
        "operacoes": dados.get("total_operacoes"),
        "parcelas": dados.get("total_parcelas"),
        "planilha": dados.get("planilha", {}),
    }


def planilha_mudou(
    cnpj_fundo: str,
    planilha: Path | None = None,
    diretorio: Path = DIR_CADASTRO,
) -> str | None:
    """Aviso quando a planilha mudou desde a importacao; None quando esta em dia.

    Nao acessa a rede nem levanta erro se a planilha nao estiver ao alcance --
    o objetivo e avisar, nunca atravancar o processamento.
    """
    origem = ler_origem(cnpj_fundo, diretorio)
    if origem is None:
        return None
    registrada = origem.get("planilha", {})
    caminho = Path(planilha) if planilha else Path(registrada.get("caminho", ""))
    try:
        if not caminho.exists():
            return None
        atual = impressao_digital(caminho)
    except OSError:
        return None
    if atual["sha256"] == registrada.get("sha256"):
        return None
    return (
        f"A planilha mudou desde a ultima importacao "
        f"(importado em {origem.get('importado_em', '?')[:10]}, "
        f"arquivo alterado em {atual['modificado_em'][:10]}). "
        f"Rode 'python -m liquidez cronograma importar' para atualizar."
    )


# --- Operacoes cadastradas a mao --------------------------------------------

def carregar_manuais(
    cnpj_fundo: str,
    diretorio: Path = DIR_MANUAIS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Le os JSON de operacoes cadastradas a mao para o fundo.

    Existem para o papel cujo layout a planilha nao expoe -- um CRA, por
    exemplo, tem PMT/Saldo Bruto no lugar de Vencimento/Amortizacao. Ficam em
    pasta propria por dois motivos: o `importar` reescreve os CSV inteiros a
    partir da planilha e apagaria o que foi digitado; e JSON usa ponto decimal
    sempre, entao abrir e salvar no Excel pt-BR nao desmancha os numeros.
    """
    base = Path(diretorio)
    if not base.exists():
        return pd.DataFrame(), pd.DataFrame()

    numerico = re.sub(r"\D", "", cnpj_fundo)
    operacoes: list[dict[str, Any]] = []
    parcelas: list[dict[str, Any]] = []

    for caminho in sorted(base.glob("*.json")):
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Cadastro manual %s ignorado: %s", caminho.name, exc)
            continue
        if re.sub(r"\D", "", str(dados.get("cnpj_fundo", ""))) != numerico:
            continue

        codigo = normalizar_codigo(dados.get("codigo_op"))
        if codigo is None:
            log.warning("Cadastro manual %s sem codigo_op valido; ignorado.", caminho.name)
            continue

        linhas = dados.get("parcelas") or []
        registro = {campo: dados.get(campo) for campo in CELULAS_METADADOS}
        registro.update(
            cnpj_fundo=cnpj_fundo,
            codigo_op=codigo,
            aba=f"(manual: {caminho.name})",
            dt_operacao=dados.get("dt_operacao"),
            indexado=bool(dados.get("indexado", False)),
            indexador=dados.get("indexador"),
            taxa_ad_efetiva=dados.get("taxa_ad_efetiva"),
            base_taxa=dados.get("base_taxa", 360),
            n_parcelas=len(linhas),
            vl_amortizacao_total=sum(float(p.get("vl_amortizacao") or 0) for p in linhas),
        )
        operacoes.append(registro)
        for numero, parcela in enumerate(linhas, start=1):
            parcelas.append({
                "cnpj_fundo": cnpj_fundo,
                "codigo_op": codigo,
                "nr_parcela": numero,
                "dt_vencimento": parcela.get("dt_vencimento"),
                "vl_amortizacao": float(parcela.get("vl_amortizacao") or 0),
            })

    df_ops = pd.DataFrame(operacoes)
    if not df_ops.empty:
        df_ops = df_ops[[c for c in COLUNAS_OPERACOES if c in df_ops.columns]]
    df_parcelas = pd.DataFrame(parcelas, columns=COLUNAS_PARCELAS) if parcelas else pd.DataFrame()
    if operacoes:
        log.info("%d operacao(oes) cadastrada(s) a mao para %s", len(operacoes), cnpj_fundo)
    return df_ops, df_parcelas


def carregar_beyond(
    cnpj_fundo: str,
    diretorio: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Le o cronograma capturado do Beyond para o fundo.

    Um arquivo por fundo, mesmo desenho do que vem da planilha, gravado por
    `beyond_cronograma.gravar`. Fica em pasta propria pelo mesmo motivo dos
    manuais: o `importar` reescreve o arquivo da planilha e apagaria isto.

    E PREMISSA, nao leitura -- entra so onde nao ha planilha nem cadastro
    manual. Ver a hierarquia no cabecalho de `beyond_cronograma.py`.
    """
    from .beyond_cronograma import DIR_BEYOND

    base = Path(diretorio or DIR_BEYOND)
    caminho = base / (re.sub(r"\D", "", str(cnpj_fundo)) + ".json")
    if not caminho.exists():
        return pd.DataFrame(), pd.DataFrame()
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Cronograma do Beyond %s ignorado: %s", caminho.name, exc)
        return pd.DataFrame(), pd.DataFrame()

    operacoes: list[dict[str, Any]] = []
    parcelas: list[dict[str, Any]] = []
    for item in dados.get("operacoes", []):
        codigo = normalizar_codigo(item.get("codigo_op"))
        if codigo is None:
            continue
        registro = {c: item.get(c) for c in COLUNAS_OPERACOES}
        registro["cnpj_fundo"] = cnpj_fundo
        registro["codigo_op"] = codigo
        operacoes.append(registro)
        for numero, parcela in enumerate(item.get("parcelas") or [], start=1):
            parcelas.append({
                "cnpj_fundo": cnpj_fundo,
                "codigo_op": codigo,
                "nr_parcela": numero,
                "dt_vencimento": parcela.get("dt_vencimento"),
                "vl_amortizacao": float(parcela.get("vl_amortizacao") or 0),
            })

    df_ops = pd.DataFrame(operacoes)
    if not df_ops.empty:
        df_ops = df_ops[[c for c in COLUNAS_OPERACOES if c in df_ops.columns]]
    df_parcelas = pd.DataFrame(parcelas, columns=COLUNAS_PARCELAS) if parcelas else pd.DataFrame()
    if operacoes:
        log.info("%d operacao(oes) do Beyond para %s", len(operacoes), cnpj_fundo)
    return df_ops, df_parcelas


@dataclass
class Cronograma:
    """Cronograma de um fundo, pronto para consulta por codigo de operacao."""

    operacoes: pd.DataFrame
    parcelas: pd.DataFrame

    @classmethod
    def vazio(cls) -> "Cronograma":
        """Cronograma sem nenhuma operacao.

        Serve ao fundo que nao tem planilha e nao precisa de uma: nos fundos da
        QI Tech o fluxo dos DC bullet vem da propria carteira. Ausencia de
        cronograma deixou de ser impedimento para projetar.
        """
        return cls(
            pd.DataFrame(columns=COLUNAS_OPERACOES),
            pd.DataFrame(columns=COLUNAS_PARCELAS),
        )

    @classmethod
    def carregar(
        cls,
        cnpj_fundo: str,
        diretorio: Path = DIR_CADASTRO,
        exigir: bool = True,
    ) -> "Cronograma":
        dados = ler_cadastro(cnpj_fundo, diretorio)
        manuais_ops, manuais_parcelas = carregar_manuais(cnpj_fundo)
        beyond_ops, beyond_parcelas = carregar_beyond(cnpj_fundo)

        if dados is None:
            if manuais_ops.empty and beyond_ops.empty:
                if not exigir:
                    return cls.vazio()
                raise ErroPlanilha(
                    f"Cronograma nao importado para {cnpj_fundo}. "
                    f"Rode 'python -m liquidez cronograma importar' primeiro."
                )
            # Fundo sem planilha -- e o caso de EVOLVE, PETTRA, MURALE e outros,
            # em que todo o fluxo de NC/CCB vem do Beyond.
            ops = pd.concat(
                [df for df in (manuais_ops.assign(origem="manual") if not manuais_ops.empty else None,
                               beyond_ops.assign(origem="beyond") if not beyond_ops.empty else None)
                 if df is not None], ignore_index=True)
            parcelas = pd.concat(
                [df for df in (manuais_parcelas, beyond_parcelas) if not df.empty],
                ignore_index=True)
            return cls(ops, parcelas)

        registros, linhas_parcelas = [], []
        for item in dados.get("operacoes", []):
            registros.append({c: item.get(c) for c in COLUNAS_OPERACOES})
            for numero, parcela in enumerate(item.get("parcelas") or [], start=1):
                linhas_parcelas.append({
                    "cnpj_fundo": item.get("cnpj_fundo") or dados.get("cnpj_fundo"),
                    "codigo_op": item.get("codigo_op"),
                    "nr_parcela": numero,
                    "dt_vencimento": parcela.get("dt_vencimento"),
                    "vl_amortizacao": parcela.get("vl_amortizacao"),
                })
        ops = pd.DataFrame(registros, columns=COLUNAS_OPERACOES).assign(origem="planilha")
        parcelas = pd.DataFrame(linhas_parcelas, columns=COLUNAS_PARCELAS)
        if not manuais_ops.empty:
            # O manual prevalece: se a planilha um dia expuser a aba, basta
            # apagar o JSON que ela volta a mandar.
            codigos = set(manuais_ops["codigo_op"])
            ops = ops[~ops["codigo_op"].isin(codigos)]
            parcelas = parcelas[~parcelas["codigo_op"].isin(codigos)]
            ops = pd.concat([ops, manuais_ops.assign(origem="manual")], ignore_index=True)
            parcelas = pd.concat([parcelas, manuais_parcelas], ignore_index=True)
        if not beyond_ops.empty:
            # O Beyond e o ultimo da fila: so entra onde nao ha planilha nem
            # manual. Quando a planilha da operacao chegar, ela sobrescreve
            # sozinha -- por isso o filtro e por codigo ja presente, e nao o
            # contrario.
            ja = set(ops["codigo_op"]) if not ops.empty else set()
            novas = beyond_ops[~beyond_ops["codigo_op"].isin(ja)]
            if not novas.empty:
                ops = pd.concat([ops, novas.assign(origem="beyond")], ignore_index=True)
                parcelas = pd.concat(
                    [parcelas,
                     beyond_parcelas[beyond_parcelas["codigo_op"].isin(set(novas["codigo_op"]))]],
                    ignore_index=True)
        return cls(ops, parcelas)

    @classmethod
    def carregar_se_houver(
        cls, cnpj_fundo: str, diretorio: Path = DIR_CADASTRO
    ) -> "Cronograma | None":
        try:
            return cls.carregar(cnpj_fundo, diretorio)
        except ErroPlanilha:
            return None

    @property
    def codigos(self) -> set[str]:
        if self.operacoes.empty:
            return set()
        return set(self.operacoes["codigo_op"].astype(str))

    def tem(self, codigo_op: Any) -> bool:
        codigo = normalizar_codigo(codigo_op)
        return codigo is not None and codigo in self.codigos

    def operacao(self, codigo_op: Any) -> pd.Series | None:
        codigo = normalizar_codigo(codigo_op)
        if codigo is None or self.operacoes.empty:
            return None
        achado = self.operacoes[self.operacoes["codigo_op"] == codigo]
        return None if achado.empty else achado.iloc[0]

    def parcelas_de(self, codigo_op: Any, desde: dt.date | None = None) -> pd.DataFrame:
        """Parcelas da operacao; com `desde`, as que vencem nessa data ou depois.

        A data-base entra: dinheiro que chega no proprio dia e a liquidez
        maxima, e a parcela ainda nao virou ativo vencido na carteira.
        """
        codigo = normalizar_codigo(codigo_op)
        if codigo is None or self.parcelas.empty:
            return self.parcelas.iloc[0:0]
        recorte = self.parcelas[self.parcelas["codigo_op"] == codigo]
        if desde is not None:
            datas = pd.to_datetime(recorte["dt_vencimento"], errors="coerce").dt.date
            recorte = recorte[datas >= desde]
        return recorte


# --- Diferencas entre o cadastro gravado e uma leitura nova ------------------

CAMPOS_COMPARADOS = (
    "vl_principal", "dt_operacao", "indexado", "indexador",
    "taxa_aa", "taxa_am", "taxa_ad", "taxa_pre_ad", "taxa_ad_efetiva",
)
TOLERANCIA = 1e-9


def _difere(antigo: Any, novo: Any) -> bool:
    if pd.isna(antigo) and pd.isna(novo):
        return False
    if isinstance(antigo, (int, float)) and isinstance(novo, (int, float)):
        return abs(float(antigo) - float(novo)) > TOLERANCIA
    return str(antigo).strip() != str(novo).strip()


def _assinatura_cronograma(parcelas: pd.DataFrame, codigo: str) -> list[tuple[str, float]]:
    recorte = parcelas[parcelas["codigo_op"] == codigo]
    return [
        (str(linha["dt_vencimento"]), round(float(linha["vl_amortizacao"] or 0), 2))
        for _, linha in recorte.sort_values("nr_parcela").iterrows()
    ]


@dataclass
class Diferencas:
    novas: list[str]
    removidas: list[str]
    cronograma_alterado: list[tuple[str, str]]
    campos_alterados: list[tuple[str, str, Any, Any]]

    @property
    def houve(self) -> bool:
        return bool(self.novas or self.removidas or self.cronograma_alterado
                    or self.campos_alterados)

    def resumir(self) -> list[str]:
        def amostra(itens, n=6):
            recorte = list(itens)[:n]
            resto = len(itens) - len(recorte)
            texto = ", ".join(recorte)
            return f"({texto}{f', +{resto}' if resto > 0 else ''})" if recorte else ""

        linhas = [
            f"novas operacoes ......... {len(self.novas):>3}  {amostra(self.novas)}",
            f"operacoes removidas ..... {len(self.removidas):>3}  {amostra(self.removidas)}",
            f"cronograma alterado ..... {len(self.cronograma_alterado):>3}",
        ]
        for codigo, detalhe in self.cronograma_alterado[:8]:
            linhas.append(f"   {codigo}: {detalhe}")
        linhas.append(f"campos alterados ........ {len(self.campos_alterados):>3}")
        for codigo, campo, antes, depois in self.campos_alterados[:8]:
            linhas.append(f"   {codigo}.{campo}: {antes} -> {depois}")
        return linhas


def comparar(atual: "Cronograma | None", novo_ops: pd.DataFrame,
             novas_parcelas: pd.DataFrame) -> Diferencas:
    """Compara o cadastro gravado com uma leitura nova da planilha."""
    if atual is None or atual.operacoes.empty:
        return Diferencas(sorted(novo_ops["codigo_op"].astype(str)), [], [], [])

    antigos = atual.operacoes.set_index("codigo_op")
    novos = novo_ops.set_index("codigo_op")
    cod_antigos, cod_novos = set(antigos.index.astype(str)), set(novos.index.astype(str))

    cronograma_alterado: list[tuple[str, str]] = []
    campos_alterados: list[tuple[str, str, Any, Any]] = []

    for codigo in sorted(cod_antigos & cod_novos):
        antes, depois = antigos.loc[codigo], novos.loc[codigo]
        for campo in CAMPOS_COMPARADOS:
            if campo in antigos.columns and campo in novos.columns:
                if _difere(antes[campo], depois[campo]):
                    campos_alterados.append((codigo, campo, antes[campo], depois[campo]))

        assin_antes = _assinatura_cronograma(atual.parcelas, codigo)
        assin_depois = _assinatura_cronograma(novas_parcelas, codigo)
        if assin_antes != assin_depois:
            if len(assin_antes) != len(assin_depois):
                detalhe = f"{len(assin_antes)} -> {len(assin_depois)} parcelas"
            else:
                mudou = sum(1 for a, b in zip(assin_antes, assin_depois) if a != b)
                detalhe = f"{mudou} parcela(s) com data ou amortizacao diferente"
            cronograma_alterado.append((codigo, detalhe))

    return Diferencas(
        novas=sorted(cod_novos - cod_antigos),
        removidas=sorted(cod_antigos - cod_novos),
        cronograma_alterado=cronograma_alterado,
        campos_alterados=campos_alterados,
    )
