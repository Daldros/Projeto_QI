"""`configurar` monta config/ a partir de DataFrames (sem banco): fichas que
passam no validar, feriados, prazos, copias dos parametros e idempotencia."""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez import configurar as cfg  # noqa: E402
from liquidez.cadastro import Cadastro  # noqa: E402
from liquidez.calendario import carregar as carregar_feriados, limpar_cache  # noqa: E402
from liquidez.cotas import MapaCotas  # noqa: E402
from liquidez.indicadores import carregar_config  # noqa: E402
from liquidez.prazos import TabelaPrazos  # noqa: E402
from liquidez.regras_vencidos import CatalogoRegras  # noqa: E402
from liquidez.tratamentos import carregar_excluidos, excluidos_do_adm  # noqa: E402

CNPJ_A = "23.216.398/0001-01"   # digito verificador valido
CNPJ_B = "32.510.577/0001-30"
CNPJ_C = "29.243.004/0001-82"   # NORMAL mas fora da carteira


def _cadastro():
    linhas = [
        # ORIGINAL + classe: o maior prazo entre as linhas vence ("D+1 ATE D+29" -> 29)
        (CNPJ_A, "MULTIPLICA FIDC", "ORIGINAL", "ABERTO", "D+1 ATÉ D+29", "QI TECH", "QI TECH"),
        (CNPJ_A, "MULTIPLICA FIDC", "SENIOR", "ABERTO", "D+1", "QI TECH", "QI TECH"),
        # FECHADO: sem prazo (999) mesmo com texto "D+1"
        (CNPJ_B, "MULTIPLICA LONG TERM FIDC", "ORIGINAL", "FECHADO", "D+1", "PLANNER", "PLANNER"),
        (CNPJ_C, "LIMAJ FIDC", "ORIGINAL", "ABERTO", "D+5", "PLANNER", "PLANNER"),
    ]
    df = pd.DataFrame(linhas, columns=["CNPJFundo", "nmFundoInterno", "nmCotaInterno", "Condominio",
                                       "LiquidacaoResgate", "Administrador", "Gestor"])
    df["CNPJAdministrador"] = "00.806.535/0001-54"
    df["CNPJGestor"] = "07.252.227/0001-73"
    df["TipoFundo"] = "FIDC"
    df["RazaoSocial"] = df["nmFundoInterno"]
    return df


def _dados():
    return {
        "cadastro": _cadastro(),
        "fundos_carteira": pd.DataFrame({"CPF_CNPJ": [CNPJ_A, CNPJ_B]}),
        "depara": pd.DataFrame({"dsNomeFundoAdm": ["MULTIPLICA FIDC"], "nmFundoByd": ["Multiplica LP"], "flAtivo": [True]}),
        "feriados": pd.DataFrame({"dtFeriado": [dt.date(2026, 1, 1), dt.date(2026, 4, 21), dt.date(2026, 1, 1)],
                                  "dsFeriado": ["Confraternizacao", "Tiradentes", "Confraternizacao"]}),
        "cotas_carteira": pd.DataFrame({"Codigo_Op": ["1", "2", "3", "4"],
                                        "Codigo_Ativo": ["MULTLT", "XPTO11", "LIMAJSR", "APLOIIMZ"],
                                        "Nome_Ativo": ["MULTIPLICA LONG TERM FIDC SUB", "FUNDO DE TERCEIRO",
                                                       "LIMAJ FIDC SR", "APOLLO II FIDCNP MZ1"]}),
    }


@pytest.fixture
def ambiente(tmp_path):
    param = tmp_path / "parametros"
    param.mkdir()
    for nome in cfg.COPIAS:
        (param / nome).write_bytes((RAIZ / "config" / "parametros" / nome).read_bytes())
    (param / "prazos_fixos.csv").write_text("codigo_op;codigo_ativo;cnpj;prazo;origem_prazo;observacao\n"
                                            ";Saldo Tesouraria;;0;fixo;caixa\n", encoding="utf-8-sig")
    (param / "cotas_depara.csv").write_text("codigo_ativo;cnpj;observacao\n"
                                            f"APLOIIMZ;{CNPJ_B};nome abreviado nao casa\n", encoding="utf-8-sig")
    (param / "fundos_overrides.csv").write_text("cnpj;pz_resg;cc;codinome_beyond;nome_produto_mov;ativo;observacao\n"
                                                f"{CNPJ_A};30;28;;;;regulamento\n", encoding="utf-8-sig")
    return tmp_path / "config", param


def test_simulacao_nao_grava(ambiente):
    raiz, param = ambiente
    rel = cfg.executar(_dados(), raiz=raiz, parametros=param, aplicar=False)
    assert rel.fichas_novas == 2 and not raiz.exists()


def test_fichas_feriados_prazos_e_parametros(ambiente):
    raiz, param = ambiente
    rel = cfg.executar(_dados(), raiz=raiz, parametros=param, aplicar=True, hoje=dt.date(2026, 10, 5))
    cad = Cadastro(raiz / "fundos").carregar()
    assert sorted(cad.fundos) == sorted(["23216398000101", "32510577000130"])   # LIMAJ fora da carteira
    a, b = cad.fundos["23216398000101"], cad.fundos["32510577000130"]
    assert a.pz_resg == 30 and a.cc == 28                 # override vence o banco (29)
    assert a.codinome_beyond == "Multiplica LP"           # de-para do Beyond
    assert a.administrador == "QI TECH"
    assert b.pz_resg == 999 and b.cc == 999               # FECHADO
    assert rel.overrides_aplicados == 1 and rel.sem_codinome == ["MULTIPLICA LONG TERM FIDC"]
    assert not [p for p in cad.validar() if p.startswith("ERRO")]

    limpar_cache()
    feriados = carregar_feriados(raiz / "calendario" / "feriados.csv", recarregar=True)
    assert feriados == {dt.date(2026, 1, 1), dt.date(2026, 4, 21)}   # duplicata removida
    limpar_cache()

    prazos = TabelaPrazos.carregar(raiz / "prazos_ativos.csv")
    por = {r.codigo_ativo: r for r in prazos.registros}
    assert por["Saldo Tesouraria"].prazo == 0
    assert por["MULTLT"].prazo == 999 and por["MULTLT"].origem_prazo == "cadastro"
    assert por["XPTO11"].prazo is None                      # fundo de terceiro: definir depois
    assert por["LIMAJSR"].prazo == 5 and por["LIMAJSR"].origem_prazo == "cadastro"   # fundo da casa SEM carteira
    assert por["APLOIIMZ"].prazo == 999 and "MULTIPLICA LONG TERM" in por["APLOIIMZ"].observacao  # de-para manual

    CatalogoRegras.carregar(raiz / "regras_vencidos.json")
    ind = carregar_config(raiz / "indicadores.json")
    assert ind["limites"]["cx_cotista"] == 0.15 and ind["fator_gdp"] == 2.33
    assert len(ind["anbima_horizontes_dias"]) == len(ind["anbima_percentil_95"]) == 9
    assert set(ind["resgate"]["tipos"]) == {"RESGATE_TOTAL", "RESGATE_PARCIAL"}
    listas = carregar_excluidos(raiz / "ativos_excluidos.json")
    assert {"LTNO", "LFTO", "PDD"} <= excluidos_do_adm("QI TECH", listas)          # QI cai no 'padrao'
    assert excluidos_do_adm("PLANNER CORRETORA DE VALORES S/A", listas) == {"SRP", "MEZAN"}


def test_segunda_execucao_mantem_fichas_e_e_identica(ambiente):
    raiz, param = ambiente
    cfg.executar(_dados(), raiz=raiz, parametros=param, aplicar=True, hoje=dt.date(2026, 10, 5))
    antes = {p.name: p.read_text(encoding="utf-8") for p in (raiz / "fundos").glob("*.json")}
    rel = cfg.executar(_dados(), raiz=raiz, parametros=param, aplicar=True, hoje=dt.date(2026, 10, 5))
    depois = {p.name: p.read_text(encoding="utf-8") for p in (raiz / "fundos").glob("*.json")}
    assert rel.fichas_mantidas == 2 and rel.fichas_novas == 0 and antes == depois


def test_parametros_versionados_sao_validos():
    param = RAIZ / "config" / "parametros"
    CatalogoRegras.carregar(param / "regras_vencidos.json")
    ind = json.loads((param / "indicadores.json").read_text(encoding="utf-8"))
    assert len(ind["anbima_horizontes_dias"]) == len(ind["anbima_percentil_95"])
    overrides = cfg.carregar_overrides(param / "fundos_overrides.csv")
    assert len(overrides) >= 100 and all("pz_resg" in v for v in overrides.values())
    assert all(r.prazo is not None for r in cfg.carregar_prazos_fixos(param / "prazos_fixos.csv"))
    gestor = {r.codigo_ativo: r for r in cfg.carregar_prazos_fixos(param / "prazos_ativos_gestor.csv")}
    assert len(gestor) >= 280 and gestor["APOLLO II FIDCNP MZ1"].prazo == 180


def test_sql_do_modulo_so_le():
    for nome in dir(cfg):
        if nome.startswith("SQL_"):
            assert getattr(cfg, nome).lstrip().upper().startswith("SELECT"), nome


def test_mapa_cotas_vazio_nao_quebra(tmp_path):
    assert MapaCotas([]).gravar(tmp_path / "isin_cotas.csv").exists()


def test_tabela_do_gestor_resolve_antes_do_nome(ambiente):
    raiz, param = ambiente
    (param / "prazos_ativos_gestor.csv").write_text(
        "codigo_op;codigo_ativo;cnpj;prazo;origem_prazo;observacao\n"
        f"APLOIIMZ;APOLLO II FIDCNP MZ1;{CNPJ_B};180;cadastro;\n"      # segue a ficha (FECHADO -> 999)
        "ARCDEBSR;ARC DEB I FIDC SR;60.275.910/0001-09;999;fixo;fundo fechado\n"
        "GVSR;GREEN VALUE FIDC SR;;;;\n"                                # sem prazo: nao entra
        ";Saldo Tesouraria;;5;fixo;\n",                                 # a fixa do projeto vence
        encoding="utf-8-sig")
    dados = _dados()
    dados["cotas_carteira"] = pd.concat([dados["cotas_carteira"], pd.DataFrame(
        {"Codigo_Op": ["ARCDEBSR"], "Codigo_Ativo": ["ARC DEB I FIDC SR"], "Nome_Ativo": ["ARC DEB I FIDC SR"]})])
    rel = cfg.executar(dados, raiz=raiz, parametros=param, aplicar=True, hoje=dt.date(2026, 10, 21))
    por = {r.codigo_ativo: r for r in TabelaPrazos.carregar(raiz / "prazos_ativos.csv").registros}
    assert por["APOLLO II FIDCNP MZ1"].prazo == 999 and por["APOLLO II FIDCNP MZ1"].origem_prazo == "cadastro"
    assert por["ARC DEB I FIDC SR"].prazo == 999 and por["ARC DEB I FIDC SR"].origem_prazo == "fixo"
    assert "GREEN VALUE FIDC SR" not in por
    assert por["Saldo Tesouraria"].prazo == 0
    assert rel.prazos_do_gestor == 2
    assert [r.codigo_ativo for r in TabelaPrazos.carregar(raiz / "prazos_ativos.csv").registros
            ].count("ARC DEB I FIDC SR") == 1                           # a carteira nao duplica


def _pacote(tmp_path):
    cfgp = tmp_path / "Relatorio Liquidez - pacote" / "config"
    arquivos = {
        "cronograma/23216398000101.json": "{}",
        "cronograma/manuais/C321654.json": '{"codigo_op": "C321654"}',
        "cronograma/fichas/B915955.json": "{}",
        "cronograma/beyond/23216398000101.json": "{}",          # foto do HML: fica
        "cronograma/_anterior/23216398000101.json": "{}",       # backup: fica
        "cra/MULTIAGRO FIDC/C47862/termo.pdf": "pdf",
        "cra/processados/velho.pdf": "pdf",                     # arquivo morto: fica
        "fundos/23216398000101.json": "{}",                     # o projeto gera do banco
        "custo_de_carrego.json": "{}",
    }
    for rel, txt in arquivos.items():
        (cfgp / rel).parent.mkdir(parents=True, exist_ok=True)
        (cfgp / rel).write_text(txt, encoding="utf-8")
    ops = {"C276922": {"estado": "confirmado", "cnpj_fundo": CNPJ_A, "visto_em": "2026-06-01",
                       "conferido_em": "2026-08-07", "nota": "bullet pela gestao"},
           "C398115": {"estado": "estoque", "cnpj_fundo": CNPJ_A, "visto_em": "2026-06-30",
                       "conferido_em": None, "nota": ""}}
    (cfgp / "cronograma" / "bullets.json").write_text(
        json.dumps({"marco_zero": "2026-06-30", "operacoes": ops}), encoding="utf-8")
    return cfgp.parent


def test_importar_pacote_simula_copia_e_preserva_o_local(tmp_path):
    pacote, raiz = _pacote(tmp_path), tmp_path / "config"
    (raiz / "cronograma" / "manuais").mkdir(parents=True)
    (raiz / "cronograma" / "manuais" / "C321654.json").write_text('{"local": true}', encoding="utf-8")

    rel = cfg.importar_pacote(pacote, raiz=raiz)
    assert not (raiz / "cronograma" / "23216398000101.json").exists()      # simulacao
    assert rel.bullets == ["C276922"]

    rel = cfg.importar_pacote(pacote, raiz=raiz, aplicar=True)
    copiados = {p.relative_to(raiz).as_posix() for p in rel.copiados}
    assert copiados == {"cronograma/23216398000101.json", "cronograma/fichas/B915955.json",
                        "cra/MULTIAGRO FIDC/C47862/termo.pdf", "custo_de_carrego.json"}
    assert [p.name for p in rel.divergentes] == ["C321654.json"]
    assert json.loads((raiz / "cronograma" / "manuais" / "C321654.json").read_text()) == {"local": True}
    bul = json.loads((raiz / "cronograma" / "bullets.json").read_text(encoding="utf-8"))["operacoes"]
    assert list(bul) == ["C276922"] and bul["C276922"]["estado"] == "confirmado"

    rel = cfg.importar_pacote(pacote, raiz=raiz, aplicar=True, sobrescrever=True)
    assert json.loads((raiz / "cronograma" / "manuais" / "C321654.json").read_text())["codigo_op"] == "C321654"
    assert len(rel.iguais) == 4


def test_importar_pacote_nao_troca_decisao_local_de_bullet(tmp_path):
    pacote, raiz = _pacote(tmp_path), tmp_path / "config"
    (raiz / "cronograma").mkdir(parents=True)
    (raiz / "cronograma" / "bullets.json").write_text(json.dumps({"marco_zero": "2026-09-30", "operacoes": {
        "C276922": {"estado": "planilha_pendente", "nota": "decidido aqui"}}}), encoding="utf-8")
    assert cfg.importar_pacote(pacote, raiz=raiz, aplicar=True).bullets == []
    bul = json.loads((raiz / "cronograma" / "bullets.json").read_text(encoding="utf-8"))
    assert bul["operacoes"]["C276922"]["nota"] == "decidido aqui" and bul["marco_zero"] == "2026-09-30"


def test_importar_pacote_recusa_pasta_errada(tmp_path):
    with pytest.raises(FileNotFoundError):
        cfg.importar_pacote(tmp_path, raiz=tmp_path / "config")
