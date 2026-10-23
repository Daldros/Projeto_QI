"""Casamento do nome truncado da carteira com a denominacao da CVM."""
from liquidez.classificar_cotas import Classificacao, Candidata, classificar, pontuar, tokens


def test_expande_abreviacoes_e_tira_ruido():
    assert tokens("ARC DEB I FIDC SR") == {"ARC", "DEB", "I", "DIREITOS", "CREDITORIOS", "SENIOR"}


def test_pontua_o_nome_oficial_apesar_do_truncamento():
    oficial = "ARC DEB I FUNDO DE INVESTIMENTO EM DIREITOS CREDITORIOS - CLASSE SENIOR"
    assert pontuar("ARC DEB I FIDC SR", oficial) == 1.0
    assert pontuar("ARC DEB I FIDC SR", "SORANO FIDC NP CLASSE SENIOR") < 0.6


CLASSES = [
    {"cnpj": "11.111.111/0001-11", "denominacao": "ARC DEB I FUNDO DE INVESTIMENTO EM DIREITOS CREDITORIOS - CLASSE SENIOR",
     "forma_condominio": "Fechado", "tipo": "FIDC", "situacao": "Em Funcionamento Normal"},
    {"cnpj": "22.222.222/0001-22", "denominacao": "APOLLO II FUNDO DE INVESTIMENTO EM DIREITOS CREDITORIOS NAO PADRONIZADOS MEZANINO",
     "forma_condominio": "Aberto", "tipo": "FIDC", "situacao": "Em Funcionamento Normal"},
    {"cnpj": "33.333.333/0001-33", "denominacao": "ARC DEB I FUNDO DE INVESTIMENTO EM DIREITOS CREDITORIOS - CLASSE SENIOR",
     "forma_condominio": "Fechado", "tipo": "FIDC", "situacao": "Cancelado"},
]


def test_classifica_fechado_e_aberto_e_prefere_o_ativo():
    saida = classificar([("ARCDEBSR", "ARC DEB I FIDC SR", 157.2e6),
                         ("APLOIIMZ", "APOLLO II FIDCNP MZ1", 18.5e6),
                         ("XPTO", "FUNDO INEXISTENTE ZZZ", 1.0)], CLASSES)
    arc, apollo, xpto = saida
    assert arc.melhor.cnpj == "11.111.111/0001-11" and arc.acao.startswith("FECHADO")
    assert apollo.melhor.cnpj == "22.222.222/0001-22" and apollo.acao.startswith("ABERTO")
    assert xpto.melhor is None and xpto.acao.startswith("conferir")


def test_candidata_fraca_pede_conferencia():
    c = Classificacao("X", "X", 0.0, [Candidata("1", "n", "Aberto", "FIDC", "ok", 0.4)])
    assert c.acao.startswith("conferir")
