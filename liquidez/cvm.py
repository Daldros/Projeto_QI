"""Cadastro de fundos da CVM (base pos-RCVM 175).

Le `registro_fundo_classe.zip` do Portal de Dados Abertos, que traz o cadastro
em tres niveis: fundo -> classe -> subclasse. O arquivo legado `cad_fi.csv` nao
e usado: nenhum dos fundos da carteira consta nele.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

log = logging.getLogger(__name__)

URL_REGISTRO = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/registro_fundo_classe.zip"

ARQ_FUNDO = "registro_fundo.csv"
ARQ_CLASSE = "registro_classe.csv"
ARQ_SUBCLASSE = "registro_subclasse.csv"

SITUACAO_CANCELADO = "Cancelado"


def so_digitos(valor: str | None) -> str:
    """Mantem apenas os digitos de um documento (CNPJ/CPF)."""
    return "".join(c for c in (valor or "") if c.isdigit())


def formatar_cnpj(valor: str | None) -> str | None:
    """Formata um CNPJ como 00.000.000/0000-00. Devolve o original se nao tiver 14 digitos."""
    d = so_digitos(valor)
    if len(d) != 14:
        return (valor or None) or None
    return f"{d[0:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"


def _texto(valor: str | None) -> str | None:
    valor = (valor or "").strip()
    return valor or None


def _numero(valor: str | None) -> float | None:
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        return float(valor.replace(",", "."))
    except ValueError:
        return None


def _entidade(cnpj: str | None, nome: str | None) -> dict | None:
    cnpj, nome = _texto(cnpj), _texto(nome)
    if not cnpj and not nome:
        return None
    return {"cnpj": formatar_cnpj(cnpj), "nome": nome}


def baixar(cache_dir: Path, forcar: bool = False, url: str = URL_REGISTRO) -> Path:
    """Baixa o zip da CVM com cache diario. Devolve o caminho do arquivo local."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    hoje = dt.date.today().isoformat()
    destino = cache_dir / f"registro_fundo_classe_{hoje}.zip"
    if destino.exists() and not forcar:
        log.info("Usando cache da CVM de hoje: %s", destino.name)
        return destino
    log.info("Baixando cadastro da CVM: %s", url)
    with urllib.request.urlopen(url, timeout=300) as resp:
        conteudo = resp.read()
    destino.write_bytes(conteudo)
    log.info("Cadastro da CVM baixado (%.1f MB)", len(conteudo) / 1024 / 1024)
    return destino


def _ler_csv(pacote: zipfile.ZipFile, nome: str) -> list[dict]:
    texto = pacote.read(nome).decode("latin-1")
    return list(csv.DictReader(io.StringIO(texto), delimiter=";"))


class CadastroCVM:
    """Indexa o cadastro da CVM e monta o bloco `cvm` da ficha de cada fundo."""

    def __init__(self, caminho_zip: Path):
        with zipfile.ZipFile(caminho_zip) as pacote:
            fundos = _ler_csv(pacote, ARQ_FUNDO)
            classes = _ler_csv(pacote, ARQ_CLASSE)
            subclasses = _ler_csv(pacote, ARQ_SUBCLASSE)

        self.baixado_em = dt.date.today().isoformat()

        self._fundos_por_cnpj: dict[str, list[dict]] = defaultdict(list)
        for linha in fundos:
            self._fundos_por_cnpj[so_digitos(linha.get("CNPJ_Fundo"))].append(linha)

        self._classes_por_fundo: dict[str, list[dict]] = defaultdict(list)
        self._fundo_por_cnpj_classe: dict[str, str] = {}
        for linha in classes:
            self._classes_por_fundo[linha.get("ID_Registro_Fundo", "")].append(linha)
            chave = so_digitos(linha.get("CNPJ_Classe"))
            if chave and chave not in self._fundo_por_cnpj_classe:
                self._fundo_por_cnpj_classe[chave] = linha.get("ID_Registro_Fundo", "")

        self._subclasses_por_classe: dict[str, list[dict]] = defaultdict(list)
        for linha in subclasses:
            self._subclasses_por_classe[linha.get("ID_Registro_Classe", "")].append(linha)

        self._por_id_fundo = {f.get("ID_Registro_Fundo", ""): f for f in fundos}

        log.info(
            "Cadastro CVM carregado: %d fundos, %d classes, %d subclasses",
            len(fundos), len(classes), len(subclasses),
        )

    def _localizar(self, cnpj: str) -> dict | None:
        """Acha o registro do fundo pelo CNPJ; se nao houver, tenta pelo CNPJ da classe."""
        chave = so_digitos(cnpj)
        candidatos = self._fundos_por_cnpj.get(chave, [])
        if not candidatos:
            id_fundo = self._fundo_por_cnpj_classe.get(chave)
            if id_fundo and id_fundo in self._por_id_fundo:
                candidatos = [self._por_id_fundo[id_fundo]]
        if not candidatos:
            return None
        if len(candidatos) > 1:
            # Prioriza o registro que nao esta cancelado.
            ativos = [c for c in candidatos if (c.get("Situacao") or "").strip() != SITUACAO_CANCELADO]
            log.warning("CNPJ %s tem %d registros na CVM; usando o primeiro nao cancelado", cnpj, len(candidatos))
            candidatos = ativos or candidatos
        return candidatos[0]

    def bloco(self, cnpj: str) -> dict | None:
        """Monta o bloco `cvm` da ficha (fundo + classes + subclasses) ou None."""
        fundo = self._localizar(cnpj)
        if fundo is None:
            return None

        id_fundo = fundo.get("ID_Registro_Fundo", "")
        classes = []
        for c in self._classes_por_fundo.get(id_fundo, []):
            subs = [
                {
                    "id_subclasse": _texto(s.get("ID_Subclasse")),
                    "codigo_cvm": _texto(s.get("Codigo_CVM")),
                    "denominacao_social": _texto(s.get("Denominacao_Social")),
                    "situacao": _texto(s.get("Situacao")),
                    "data_inicio": _texto(s.get("Data_Inicio")),
                    "forma_condominio": _texto(s.get("Forma_Condominio")),
                    "exclusivo": _texto(s.get("Exclusivo")),
                    "publico_alvo": _texto(s.get("Publico_Alvo")),
                }
                for s in self._subclasses_por_classe.get(c.get("ID_Registro_Classe", ""), [])
            ]
            classes.append(
                {
                    "id_registro_classe": _texto(c.get("ID_Registro_Classe")),
                    "cnpj": formatar_cnpj(c.get("CNPJ_Classe")),
                    "codigo_cvm": _texto(c.get("Codigo_CVM")),
                    "tipo_classe": _texto(c.get("Tipo_Classe")),
                    "denominacao_social": _texto(c.get("Denominacao_Social")),
                    "situacao": _texto(c.get("Situacao")),
                    "data_registro": _texto(c.get("Data_Registro")),
                    "data_inicio": _texto(c.get("Data_Inicio")),
                    "classificacao": _texto(c.get("Classificacao")),
                    "classificacao_anbima": _texto(c.get("Classificacao_Anbima")),
                    "forma_condominio": _texto(c.get("Forma_Condominio")),
                    "exclusivo": _texto(c.get("Exclusivo")),
                    "publico_alvo": _texto(c.get("Publico_Alvo")),
                    "patrimonio_liquido": _numero(c.get("Patrimonio_Liquido")),
                    "data_patrimonio_liquido": _texto(c.get("Data_Patrimonio_Liquido")),
                    "auditor": _entidade(c.get("CNPJ_Auditor"), c.get("Auditor")),
                    "custodiante": _entidade(c.get("CNPJ_Custodiante"), c.get("Custodiante")),
                    "controlador": _entidade(c.get("CNPJ_Controlador"), c.get("Controlador")),
                    "subclasses": subs,
                }
            )

        return {
            "id_registro_fundo": _texto(fundo.get("ID_Registro_Fundo")),
            "codigo_cvm": _texto(fundo.get("Codigo_CVM")),
            "tipo_fundo": _texto(fundo.get("Tipo_Fundo")),
            "denominacao_social": _texto(fundo.get("Denominacao_Social")),
            "situacao": _texto(fundo.get("Situacao")),
            "data_registro": _texto(fundo.get("Data_Registro")),
            "data_constituicao": _texto(fundo.get("Data_Constituicao")),
            "data_inicio_situacao": _texto(fundo.get("Data_Inicio_Situacao")),
            "data_cancelamento": _texto(fundo.get("Data_Cancelamento")),
            "data_adaptacao_rcvm175": _texto(fundo.get("Data_Adaptacao_RCVM175")),
            "patrimonio_liquido": _numero(fundo.get("Patrimonio_Liquido")),
            "data_patrimonio_liquido": _texto(fundo.get("Data_Patrimonio_Liquido")),
            "diretor": _texto(fundo.get("Diretor")),
            "administrador": _entidade(fundo.get("CNPJ_Administrador"), fundo.get("Administrador")),
            "gestor": _entidade(fundo.get("CPF_CNPJ_Gestor"), fundo.get("Gestor")),
            "classes": classes,
            "sincronizado_em": self.baixado_em,
        }


def carregar(cache_dir: Path, forcar: bool = False) -> CadastroCVM:
    """Atalho: baixa (ou reaproveita o cache) e devolve o cadastro indexado."""
    return CadastroCVM(baixar(cache_dir, forcar=forcar))
