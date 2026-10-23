"""Identificacao dos ativos vencidos na carteira, por administrador.

A regra fica em `config/regras_vencidos.json`, declarativa, para que incluir um
administrador novo seja uma alteracao de configuracao e nao de codigo.

Cada regra e uma lista de condicoes combinadas com E. As linhas que passam em
todas identificam os ativos vencidos; delas se extrai o `Emissor`, e depois
TODAS as linhas daquele emissor sao marcadas como vencidas -- e o que a
planilha fazia ao montar a coluna A da aba `Vencidos` e cruzar de volta.

Uma regra pode trazer ainda `alternativas`: grupos de condicoes que valem
cada um por si, combinados com OU entre si e com o grupo principal. Serve
para somar ao criterio do administrador (o prefixo `VC` da QI, que so marca o
que ele ja RECLASSIFICOU) um criterio economico: a data de vencimento no
passado, via o operador `data_anterior_a`. Sem isso, titulo vencido ontem e
ainda nao reclassificado cai na faixa D+0 e conta como caixa de hoje.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .regras_adm import normalizar

log = logging.getLogger(__name__)

ARQUIVO_PADRAO = Path("config") / "regras_vencidos.json"


class ErroRegra(ValueError):
    pass


@dataclass(frozen=True)
class Condicao:
    coluna: str
    operador: str
    valor: object = None

    def descrever(self) -> str:
        rotulos = {
            "comeca_com": "começa com", "termina_com": "termina com", "contem": "contém",
            "igual": "é igual a", "diferente_de": "é diferente de", "em": "está em",
            "maior_que": ">", "maior_ou_igual": "≥", "menor_que": "<", "menor_ou_igual": "≤",
            "regex": "casa com a expressão", "nao_vazio": "está preenchida", "vazio": "está vazia",
            "data_anterior_a": "é anterior à coluna",
        }
        rotulo = rotulos.get(self.operador, self.operador)
        if self.operador in ("nao_vazio", "vazio"):
            return f"{self.coluna} {rotulo}"
        if self.operador == "data_anterior_a":
            return f"{self.coluna} {rotulo} {self.valor}"
        return f"{self.coluna} {rotulo} {self.valor!r}"

    def avaliar(self, df: pd.DataFrame) -> pd.Series:
        if self.coluna not in df.columns:
            log.warning("Coluna %s ausente na carteira; condicao ignorada.", self.coluna)
            return pd.Series(False, index=df.index)

        coluna = df[self.coluna]

        # Compara duas colunas de data da mesma linha: `coluna` < `valor`.
        # Data ausente em qualquer das duas nao decide nada (False).
        if self.operador == "data_anterior_a":
            outra = str(self.valor)
            if outra not in df.columns:
                log.warning("Coluna %s ausente na carteira; condicao ignorada.", outra)
                return pd.Series(False, index=df.index)
            esta = pd.to_datetime(coluna, errors="coerce")
            ref = pd.to_datetime(df[outra], errors="coerce")
            return (esta < ref).fillna(False)

        texto = coluna.fillna("").astype(str).str.strip().str.upper()

        if self.operador == "comeca_com":
            return texto.str.startswith(str(self.valor).upper())
        if self.operador == "termina_com":
            return texto.str.endswith(str(self.valor).upper())
        if self.operador == "contem":
            return texto.str.contains(str(self.valor).upper(), regex=False, na=False)
        if self.operador == "igual":
            return texto == str(self.valor).upper()
        if self.operador == "diferente_de":
            return texto != str(self.valor).upper()
        if self.operador == "em":
            alvos = {str(v).upper() for v in (self.valor or [])}
            return texto.isin(alvos)
        if self.operador == "regex":
            return texto.str.contains(str(self.valor), regex=True, na=False)
        if self.operador == "nao_vazio":
            return texto != ""
        if self.operador == "vazio":
            return texto == ""

        numero = pd.to_numeric(coluna, errors="coerce")
        if self.operador == "maior_que":
            return (numero > float(self.valor)).fillna(False)
        if self.operador == "maior_ou_igual":
            return (numero >= float(self.valor)).fillna(False)
        if self.operador == "menor_que":
            return (numero < float(self.valor)).fillna(False)
        if self.operador == "menor_ou_igual":
            return (numero <= float(self.valor)).fillna(False)

        raise ErroRegra(f"Operador desconhecido: {self.operador!r}")


@dataclass(frozen=True)
class RegraVencidos:
    administrador: str
    descricao: str
    coluna_emissor: str
    condicoes: tuple[Condicao, ...]
    # Grupos alternativos: cada grupo e um E; entre grupos, e com `condicoes`, OU.
    alternativas: tuple[tuple[Condicao, ...], ...] = ()

    @property
    def definida(self) -> bool:
        return bool(self.condicoes) or bool(self.alternativas)

    def grupos(self) -> tuple[tuple[Condicao, ...], ...]:
        principal = (self.condicoes,) if self.condicoes else ()
        return principal + tuple(g for g in self.alternativas if g)

    def descrever(self) -> list[str]:
        linhas: list[str] = []
        for i, grupo in enumerate(self.grupos()):
            if i:
                linhas.append("ou")
            linhas.extend(c.descrever() for c in grupo)
        return linhas

    def selecionar(self, carteira: pd.DataFrame) -> pd.Series:
        """Mascara das linhas que sao ativos vencidos."""
        if carteira.empty:
            return pd.Series(dtype=bool)
        grupos = self.grupos()
        if not grupos:
            return pd.Series(False, index=carteira.index)
        selecao = pd.Series(False, index=carteira.index)
        for grupo in grupos:
            parcial = pd.Series(True, index=carteira.index)
            for condicao in grupo:
                parcial &= condicao.avaliar(carteira)
            selecao |= parcial
        return selecao

    def emissores(self, carteira: pd.DataFrame) -> set[str]:
        if carteira.empty:
            return set()
        if self.coluna_emissor not in carteira.columns:
            log.warning("Coluna %s ausente na carteira.", self.coluna_emissor)
            return set()
        valores = carteira.loc[self.selecionar(carteira), self.coluna_emissor]
        return {v for v in valores.fillna("").astype(str).str.strip() if v}


SEM_REGRA = RegraVencidos(
    administrador="(sem regra definida)",
    descricao="Criterio de identificacao ainda nao definido para este administrador.",
    coluna_emissor="Emissor",
    condicoes=(),
)


def _lista(itens: list, rotulo: str) -> tuple[Condicao, ...]:
    return tuple(
        Condicao(coluna=c["coluna"], operador=c["operador"], valor=c.get("valor"))
        for c in itens or []
    )


def _condicoes(bloco: dict, rotulo: str) -> tuple[Condicao, ...]:
    condicoes = _lista(bloco.get("condicoes", []), rotulo)
    if not condicoes and not bloco.get("alternativas"):
        raise ErroRegra(f"Regra de {rotulo!r} sem condicoes.")
    return condicoes


def _alternativas(bloco: dict, rotulo: str) -> tuple[tuple[Condicao, ...], ...]:
    grupos = tuple(_lista(g, rotulo) for g in bloco.get("alternativas", []) or [])
    if any(not g for g in grupos):
        raise ErroRegra(f"Regra de {rotulo!r} com grupo alternativo vazio.")
    return grupos


class CatalogoRegras:
    """Regras de identificacao carregadas do arquivo de configuracao."""

    def __init__(
        self,
        regras: dict[str, RegraVencidos],
        pendentes: list[str],
        estoque: RegraVencidos,
    ):
        self.regras = regras
        self.pendentes = pendentes
        self.estoque = estoque

    @classmethod
    def carregar(cls, caminho: Path = ARQUIVO_PADRAO) -> "CatalogoRegras":
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
        pendentes = list((dados.get("_pendentes") or {}).get("administradores", []))

        regras: dict[str, RegraVencidos] = {}
        for chave, bloco in dados.items():
            if chave.startswith("_"):
                continue
            regras[chave] = RegraVencidos(
                administrador=chave,
                descricao=bloco.get("descricao", ""),
                coluna_emissor=bloco.get("coluna_emissor", "Emissor"),
                condicoes=_condicoes(bloco, chave),
                alternativas=_alternativas(bloco, chave),
            )

        bloco_estoque = dados.get("_estoque")
        if bloco_estoque:
            estoque = RegraVencidos(
                administrador="(estoque)",
                descricao=bloco_estoque.get("descricao", ""),
                coluna_emissor=bloco_estoque.get("coluna_sacado", "CNPJ_Sacado"),
                condicoes=_condicoes(bloco_estoque, "_estoque"),
            )
        else:
            estoque = SEM_REGRA

        log.info(
            "Regras de vencidos carregadas: %d administradores, %d pendentes, estoque %s",
            len(regras), len(pendentes), "definido" if estoque.definida else "sem regra",
        )
        return cls(regras, pendentes, estoque)

    def obter(self, administrador: str | None) -> RegraVencidos:
        """Regra do administrador; SEM_REGRA quando nao houver."""
        alvo = normalizar(administrador)
        if not alvo:
            return SEM_REGRA
        for chave, regra in self.regras.items():
            if normalizar(chave) in alvo:
                return regra
        return SEM_REGRA
