"""Interface de linha de comando.

    python -m liquidez fundos importar
    python -m liquidez fundos sync-cvm
    python -m liquidez fundos list --adm QI
    python -m liquidez extrair --data 2026-07-02 --fundo "AVANTI MULTIPLICA FIDC"
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import logging
import os
import sys
from pathlib import Path

from . import cvm as cvm_mod
from . import regulamentos as reg_mod
from . import configurar as cfg_mod
from .cadastro import (
    DIR_CADASTRO,
    Cadastro,
    Fundo,
    ler_codinomes,
    ler_config_fundos,
    montar_ficha,
    validar_cnpj,
)
from .cvm import SITUACAO_CANCELADO, formatar_cnpj, so_digitos
from .regras_adm import obter as obter_regra
from .tabelas import CARTEIRA

log = logging.getLogger("liquidez")

DIR_CACHE = Path(".cache") / "cvm"
SAIDA_PADRAO = Path("dados")


def configurar_log(verboso: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def python_da_store() -> bool:
    """O Python da Microsoft Store redireciona escritas em %LOCALAPPDATA%."""
    return "windowsapps" in sys.prefix.lower()


def caminho_efetivo(destino: Path) -> Path:
    """Caminho onde o arquivo realmente foi parar.

    Com o Python da Store, gravar em %LOCALAPPDATA%\\x acaba em
    %LOCALAPPDATA%\\Packages\\PythonSoftwareFoundation.Python...\\LocalCache\\Local\\x.
    """
    if destino.exists():
        return destino
    local = os.environ.get("LOCALAPPDATA")
    if not local or not str(destino).lower().startswith(local.lower()):
        return destino
    relativo = str(destino)[len(local):].lstrip("\\/")
    for pacote in sorted(Path(local, "Packages").glob("PythonSoftwareFoundation.Python*")):
        candidato = pacote / "LocalCache" / "Local" / relativo
        if candidato.exists():
            return candidato
    return destino


def avisar_saida(destino: Path) -> None:
    """Avisa sobre OneDrive e sobre o redirecionamento do Python da Store."""
    texto = str(destino).lower()
    if "onedrive" in texto:
        log.warning(
            "A saida esta dentro do OneDrive (%s). Sao centenas de arquivos e a "
            "sincronizacao fica lenta -- considere outra pasta.", destino,
        )
    local = os.environ.get("LOCALAPPDATA", "")
    if python_da_store() and local and texto.startswith(local.lower()) and "packages" not in texto:
        log.warning(
            "Este Python e o da Microsoft Store: gravacoes em %%LOCALAPPDATA%% sao "
            "redirecionadas para o LocalCache do pacote e a pasta nao aparece onde "
            "voce espera. Prefira um caminho fora de AppData (ex.: D:\\liquidez_dados)."
        )


def localizar_xlsm(informado: str | None) -> Path:
    if informado:
        return Path(informado)
    candidatos = sorted(glob.glob("*.xlsm"))
    if not candidatos:
        raise SystemExit("Nenhum .xlsm encontrado na pasta atual. Use --arquivo.")
    return Path(candidatos[0])


# ---------------------------------------------------------------------------
# fundos importar
# ---------------------------------------------------------------------------

def cmd_importar(args) -> int:
    caminho = localizar_xlsm(args.arquivo)
    log.info("Lendo Config_Fundos de %s", caminho.name)
    registros = ler_config_fundos(caminho)
    codinomes = ler_codinomes(caminho)
    log.info("%d fundos na planilha, %d codinomes no Painel", len(registros), len(codinomes))

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    base_cvm = cvm_mod.carregar(DIR_CACHE, forcar=args.forcar_cvm)

    criados = atualizados = sem_cvm = 0
    cancelados: list[str] = []

    for registro in registros:
        chave = so_digitos(registro["cnpj"])
        bloco = base_cvm.bloco(registro["cnpj"])
        if bloco is None:
            sem_cvm += 1
            log.warning("Sem cadastro na CVM: %s (%s)", registro["nome"], registro["cnpj"])
        elif bloco.get("situacao") == SITUACAO_CANCELADO:
            cancelados.append(f"{registro['nome']} ({registro['cnpj']})")
            continue

        existente = cadastro.fundos.get(chave)
        if existente and not args.recriar:
            # Preserva os blocos mantidos por nos; atualiza apenas a CVM.
            existente.cvm = bloco or existente.cvm
            existente.marcar_atualizado()
            cadastro.gravar(existente)
            atualizados += 1
            continue

        ficha = montar_ficha(registro, bloco, codinomes.get(chave))
        if existente and args.recriar:
            ficha.liquidez = existente.liquidez or ficha.liquidez
            ficha.integracao = {**ficha.integracao, **{k: v for k, v in existente.integracao.items() if v}}
        cadastro.gravar(ficha)
        criados += 1

    print()
    print(f"Fichas criadas .......... {criados}")
    print(f"Fichas atualizadas ...... {atualizados}")
    print(f"Sem cadastro na CVM ..... {sem_cvm}")
    print(f"Cancelados (descartados)  {len(cancelados)}")
    for nome in cancelados:
        print(f"   - {nome}")
    print(f"\nTotal no cadastro: {len(cadastro.fundos)} fundos em {cadastro.diretorio}")
    return 0


# ---------------------------------------------------------------------------
# fundos sync-cvm
# ---------------------------------------------------------------------------

def _achatar(valor, prefixo: str = "") -> dict[str, object]:
    """Achata um dicionario aninhado em {caminho: valor} para comparar."""
    plano: dict[str, object] = {}
    if isinstance(valor, dict):
        for chave, sub in valor.items():
            plano.update(_achatar(sub, f"{prefixo}.{chave}" if prefixo else str(chave)))
    elif isinstance(valor, list):
        for i, sub in enumerate(valor):
            plano.update(_achatar(sub, f"{prefixo}[{i}]"))
    else:
        plano[prefixo] = valor
    return plano


def _diferencas(antigo: dict, novo: dict) -> list[tuple[str, object, object]]:
    a, b = _achatar(antigo), _achatar(novo)
    a.pop("sincronizado_em", None)
    b.pop("sincronizado_em", None)
    mudancas = []
    for chave in sorted(set(a) | set(b)):
        if a.get(chave) != b.get(chave):
            mudancas.append((chave, a.get(chave), b.get(chave)))
    return mudancas


def cmd_sync_cvm(args) -> int:
    cadastro = Cadastro(Path(args.diretorio)).carregar()
    if not cadastro.fundos:
        raise SystemExit("Cadastro vazio. Rode 'fundos importar' primeiro.")

    base_cvm = cvm_mod.carregar(DIR_CACHE, forcar=args.forcar_cvm)

    pendentes: list[tuple[Fundo, dict, list]] = []
    cancelados: list[Fundo] = []
    for fundo in cadastro.fundos.values():
        bloco = base_cvm.bloco(fundo.cnpj)
        if bloco is None:
            log.warning("%s: sem cadastro na CVM", fundo.nome_interno)
            continue
        if bloco.get("situacao") == SITUACAO_CANCELADO:
            cancelados.append(fundo)
        mudancas = _diferencas(fundo.cvm, bloco)
        if mudancas:
            pendentes.append((fundo, bloco, mudancas))

    if not pendentes and not cancelados:
        print("Nada mudou na CVM.")
        return 0

    for fundo, _, mudancas in pendentes:
        print(f"\n{fundo.nome_interno} ({fundo.cnpj})")
        for chave, antes, depois in mudancas[: args.max_linhas]:
            print(f"   {chave}: {antes!r} -> {depois!r}")
        if len(mudancas) > args.max_linhas:
            print(f"   ... mais {len(mudancas) - args.max_linhas} alteracoes")

    if cancelados:
        print("\nCancelados na CVM (serao movidos para _removidos/):")
        for fundo in cancelados:
            print(f"   - {fundo.nome_interno} ({fundo.cnpj})")

    print(f"\n{len(pendentes)} fundo(s) com alteracao, {len(cancelados)} cancelado(s).")
    if not args.aplicar:
        resposta = input("Aplicar as alteracoes? [s/N] ").strip().lower()
        if resposta not in ("s", "sim", "y"):
            print("Nada gravado.")
            return 0

    for fundo, bloco, _ in pendentes:
        fundo.cvm = bloco
        fundo.marcar_atualizado()
        cadastro.gravar(fundo)
    for fundo in cancelados:
        cadastro.remover(fundo.cnpj, motivo="Cancelado na CVM")

    print(f"Gravado: {len(pendentes)} atualizados, {len(cancelados)} removidos.")
    return 0


# ---------------------------------------------------------------------------
# fundos add / edit / list / show / remove / validar
# ---------------------------------------------------------------------------

def cmd_add(args) -> int:
    cnpj = formatar_cnpj(args.cnpj)
    if not validar_cnpj(cnpj):
        raise SystemExit(f"CNPJ invalido: {args.cnpj}")

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    if so_digitos(cnpj) in cadastro.fundos:
        raise SystemExit(f"Ja existe ficha para {cnpj}.")

    base_cvm = cvm_mod.carregar(DIR_CACHE)
    bloco = base_cvm.bloco(cnpj)
    if bloco is None:
        log.warning("CNPJ %s nao encontrado na CVM; a ficha sera criada sem o bloco cvm.", cnpj)
    elif bloco.get("situacao") == SITUACAO_CANCELADO and not args.permitir_cancelado:
        raise SystemExit(f"{cnpj} esta cancelado na CVM. Use --permitir-cancelado se for intencional.")

    registro = {
        "nome": args.nome,
        "cnpj": cnpj,
        "pz_resg": args.pz_resg,
        "cc": args.cc,
        "administrador_planilha": None,
    }
    ficha = montar_ficha(registro, bloco, args.codinome)
    if args.nome_produto:
        ficha.integracao["nome_produto_mov"] = args.nome_produto
    caminho = cadastro.gravar(ficha)
    print(f"Ficha criada: {caminho}")
    print(f"   Administrador (CVM): {ficha.administrador}")
    return 0


def cmd_edit(args) -> int:
    cadastro = Cadastro(Path(args.diretorio)).carregar()
    fundo = cadastro.obter(args.fundo)
    if fundo is None:
        raise SystemExit(f"Fundo nao encontrado: {args.fundo}")

    alteracoes = []
    if args.pz_resg is not None:
        fundo.liquidez["pz_resg"] = args.pz_resg
        alteracoes.append(f"liquidez.pz_resg={args.pz_resg}")
    if args.cc is not None:
        fundo.liquidez["cc"] = args.cc
        alteracoes.append(f"liquidez.cc={args.cc}")
    if args.codinome is not None:
        fundo.integracao["codinome_beyond"] = args.codinome
        alteracoes.append(f"integracao.codinome_beyond={args.codinome}")
    if args.nome_produto is not None:
        fundo.integracao["nome_produto_mov"] = args.nome_produto
        alteracoes.append(f"integracao.nome_produto_mov={args.nome_produto}")
    if args.ativo is not None:
        fundo.controle["ativo"] = args.ativo
        alteracoes.append(f"controle.ativo={args.ativo}")
    if args.obs is not None:
        fundo.controle["observacoes"] = args.obs
        alteracoes.append("controle.observacoes")

    if not alteracoes:
        raise SystemExit("Nada para alterar. Veja as opcoes com --help.")

    fundo.marcar_atualizado()
    cadastro.gravar(fundo)
    print(f"{fundo.nome_interno}: " + ", ".join(alteracoes))

    # Mexeu no prazo do fundo? A tabela de prazos por ativo tem linhas derivadas
    # deste cadastro (origem 'cadastro') e ficaria divergente. Realinhar nao e
    # decisao, e manutencao de valor derivado -- por isso sai junto, sem pedir.
    if args.pz_resg is not None or args.cc is not None:
        _sincronizar_prazos(cadastro, args)
    return 0


def _sincronizar_prazos(cadastro, args, silencioso_se_em_dia: bool = True) -> int:
    """Realinha os prazos de origem 'cadastro'. Devolve quantos mudaram."""
    from .prazos import ARQUIVO_PADRAO, TabelaPrazos

    caminho = Path(getattr(args, "arquivo_prazos", None) or ARQUIVO_PADRAO)
    if not caminho.exists():
        return 0
    tabela = TabelaPrazos.carregar(caminho)
    fora = tabela.divergentes_do_cadastro(cadastro)
    if not fora:
        if not silencioso_se_em_dia:
            print("Tabela de prazos por ativo: em dia.")
        return 0

    print(f"\nTabela de prazos por ativo: {len(fora)} linha(s) realinhada(s) ao cadastro")
    for registro, novo in fora:
        print(f"   {registro.codigo_ativo:<24} {registro.prazo} -> {novo}")
        registro.prazo = novo
    tabela.gravar(caminho)
    return len(fora)


def cmd_prazos_sincronia(args) -> int:
    """Confere (ou realinha) os prazos derivados do cadastro. Para a rodada."""
    cadastro = Cadastro(Path(args.diretorio)).carregar()
    if args.conferir:
        from .prazos import ARQUIVO_PADRAO, TabelaPrazos

        caminho = Path(args.arquivo_prazos or ARQUIVO_PADRAO)
        fora = TabelaPrazos.carregar(caminho).divergentes_do_cadastro(cadastro)
        if not fora:
            print("Tabela de prazos por ativo: em dia com o cadastro.")
            return 0
        print(f"{len(fora)} linha(s) fora de sincronia com o cadastro:")
        for registro, novo in fora:
            print(f"   {registro.codigo_ativo:<24} {registro.prazo} -> {novo}")
        return 2
    alterados = _sincronizar_prazos(cadastro, args, silencioso_se_em_dia=False)
    return 0 if alterados == 0 else 0


def cmd_list(args) -> int:
    cadastro = Cadastro(Path(args.diretorio)).carregar()
    fundos = cadastro.filtrar(
        adm=args.adm, tipo=args.tipo, situacao=args.situacao,
        incluir_inativos=args.incluir_inativos,
    )
    if args.csv:
        print("nome_interno;cnpj;tipo;situacao;administrador;pz_resg;cc;codinome;ativo")
        for f in fundos:
            print(
                f"{f.nome_interno};{f.cnpj};{f.tipo_fundo};{f.situacao_cvm};"
                f"{f.administrador};{f.pz_resg};{f.cc};{f.codinome_beyond or ''};{f.ativo}"
            )
        return 0

    print(f"{'FUNDO':<38} {'CNPJ':<20} {'TIPO':<6} {'PZ':>4} {'CC':>4}  ADMINISTRADOR")
    print("-" * 120)
    for f in fundos:
        print(
            f"{f.nome_interno[:37]:<38} {f.cnpj:<20} {(f.tipo_fundo or '-'):<6} "
            f"{(f.pz_resg if f.pz_resg is not None else '-'):>4} "
            f"{(f.cc if f.cc is not None else '-'):>4}  {(f.administrador or '-')[:45]}"
        )
    print(f"\n{len(fundos)} fundo(s).")
    return 0


def cmd_show(args) -> int:
    cadastro = Cadastro(Path(args.diretorio)).carregar()
    fundo = cadastro.obter(args.fundo)
    if fundo is None:
        raise SystemExit(f"Fundo nao encontrado: {args.fundo}")
    print(json.dumps(fundo.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_remove(args) -> int:
    cadastro = Cadastro(Path(args.diretorio)).carregar()
    fundo = cadastro.obter(args.fundo)
    if fundo is None:
        raise SystemExit(f"Fundo nao encontrado: {args.fundo}")
    destino = cadastro.remover(fundo.cnpj, motivo=args.motivo)
    print(f"{fundo.nome_interno} movido para {destino}")
    return 0


def cmd_validar(args) -> int:
    cadastro = Cadastro(Path(args.diretorio)).carregar()
    problemas = cadastro.validar()
    erros = [p for p in problemas if p.startswith("ERRO")]
    for problema in problemas:
        print(problema)
    print(f"\n{len(cadastro.fundos)} fundo(s) | {len(erros)} erro(s) | {len(problemas) - len(erros)} aviso(s)")
    return 1 if erros else 0


# ---------------------------------------------------------------------------
# prazos
# ---------------------------------------------------------------------------

def cmd_prazos_importar(args) -> int:
    from .prazos import ARQUIVO_PADRAO, importar_da_planilha

    caminho = localizar_xlsm(args.arquivo)
    destino = Path(args.arquivo_prazos or ARQUIVO_PADRAO)
    if destino.exists() and not args.forcar:
        raise SystemExit(f"{destino} ja existe. Use --forcar para regerar.")

    log.info("Lendo a aba %s de %s", args.aba, caminho.name)
    tabela, relatorio = importar_da_planilha(caminho, aba=args.aba)
    gravado = tabela.gravar(destino)

    print()
    print(f"Registros importados ........ {relatorio['lidos']}")
    print(f"Sem prazo definido .......... {relatorio['sem_prazo']}")
    print(f"Prazo 9999 unificado em 999 . {relatorio['unificados_9999']}")
    print(f"Divergencias com a tabela K:L {len(relatorio['divergencias_kl'])} (prevaleceu A:D)")
    for codigo, kl, ad in relatorio["divergencias_kl"]:
        print(f"   {codigo:<16} K:L={kl:<6} adotado={ad}")
    if relatorio["duplicados"]:
        print(f"Codigos repetidos ........... {len(relatorio['duplicados'])}")
        for codigo in relatorio["duplicados"]:
            print(f"   {codigo}")
    print(f"\nTabela gravada em: {gravado.resolve()}")
    return 0


def _carregar_tabela(args):
    from .prazos import ARQUIVO_PADRAO, TabelaPrazos

    return TabelaPrazos.carregar(Path(args.arquivo_prazos or ARQUIVO_PADRAO))


def cmd_prazos_list(args) -> int:
    tabela = _carregar_tabela(args)
    registros = tabela.registros
    if args.sem_prazo:
        registros = [r for r in registros if r.prazo is None]
    if args.busca:
        alvo = args.busca.upper()
        registros = [
            r for r in registros
            if alvo in r.codigo_ativo.upper() or alvo in r.codigo_op.upper()
            or alvo in (r.cnpj or "")
        ]
    registros = sorted(registros, key=lambda r: r.codigo_ativo.upper())

    if args.csv:
        print(";".join(("codigo_op", "codigo_ativo", "cnpj", "prazo", "origem_prazo")))
        for r in registros:
            print(f"{r.codigo_op};{r.codigo_ativo};{r.cnpj or ''};{'' if r.prazo is None else r.prazo};{r.origem_prazo}")
        return 0

    print(f"{'CODIGO_ATIVO':<24} {'CODIGO_OP':<14} {'CNPJ':<20} {'PRAZO':>6}  ORIGEM")
    print("-" * 82)
    for r in registros:
        print(
            f"{r.codigo_ativo[:23]:<24} {r.codigo_op[:13]:<14} {(r.cnpj or '-'):<20} "
            f"{('-' if r.prazo is None else r.prazo):>6}  {r.origem_prazo or '-'}"
        )
    print(f"\n{len(registros)} ativo(s).")
    return 0


def cmd_prazos_show(args) -> int:
    tabela = _carregar_tabela(args)
    registro = tabela.obter(args.codigo)
    if registro is None:
        raise SystemExit(f"Ativo nao encontrado: {args.codigo}")
    for campo, valor in registro.to_row().items():
        print(f"{campo:<14} {valor}")
    return 0


def cmd_prazos_set(args) -> int:
    from .prazos import ARQUIVO_PADRAO, Prazo

    tabela = _carregar_tabela(args)
    registro = tabela.obter(args.codigo)
    if registro is None:
        if not args.criar:
            raise SystemExit(f"Ativo nao encontrado: {args.codigo}. Use --criar para incluir.")
        registro = Prazo(codigo_op=args.codigo_op or "", codigo_ativo=args.codigo)
        tabela.registros.append(registro)

    if args.prazo is not None:
        registro.prazo = args.prazo
        registro.origem_prazo = "fixo"
    if args.cnpj:
        from .cvm import formatar_cnpj as _fmt

        registro.cnpj = _fmt(args.cnpj)
    if args.codigo_op:
        registro.codigo_op = args.codigo_op
    if args.obs is not None:
        registro.observacao = args.obs

    tabela._indexar()
    caminho = tabela.gravar(Path(args.arquivo_prazos or ARQUIVO_PADRAO))
    print(f"{registro.codigo_ativo}: prazo={registro.prazo} origem={registro.origem_prazo}")
    print(f"gravado em {caminho}")
    return 0


def cmd_prazos_sync_cadastro(args) -> int:
    """Atualiza os prazos que vieram do cadastro e sairam de sincronia."""
    from .prazos import ARQUIVO_PADRAO

    tabela = _carregar_tabela(args)
    cadastro = Cadastro(Path(args.diretorio)).carregar()
    fora = tabela.divergentes_do_cadastro(cadastro)
    if not fora:
        print("Todos os prazos de origem 'cadastro' estao em dia.")
        return 0

    for registro, novo in fora:
        print(f"   {registro.codigo_ativo:<24} {registro.prazo} -> {novo}   ({registro.cnpj})")
    print(f"\n{len(fora)} ativo(s) fora de sincronia.")
    if not args.aplicar:
        resposta = input("Atualizar? [s/N] ").strip().lower()
        if resposta not in ("s", "sim", "y"):
            print("Nada gravado.")
            return 0
    for registro, novo in fora:
        registro.prazo = novo
    caminho = tabela.gravar(Path(args.arquivo_prazos or ARQUIVO_PADRAO))
    print(f"{len(fora)} atualizado(s) em {caminho}")
    return 0


def cmd_prazos_validar(args) -> int:
    tabela = _carregar_tabela(args)
    cadastro = Cadastro(Path(args.diretorio)).carregar()
    problemas = tabela.validar(cadastro)
    erros = [p for p in problemas if p.startswith("ERRO")]
    for problema in problemas:
        print(problema)
    print(
        f"\n{len(tabela.registros)} ativo(s) | {len(erros)} erro(s) | "
        f"{len(problemas) - len(erros)} aviso(s)"
    )
    return 1 if erros else 0


# ---------------------------------------------------------------------------
# cronograma (cadastro das operacoes, lido da planilha de conciliacao)
# ---------------------------------------------------------------------------

def _cr_dirs() -> tuple[Path, Path]:
    """Pastas de entrada e de processados, sem importar cronograma no topo."""
    from . import cronograma as cr

    return cr.DIR_PLANILHAS, cr.DIR_PROCESSADOS


def _fundo_do_args(args):
    cadastro = Cadastro(Path(args.diretorio)).carregar()
    fundo = cadastro.obter(args.fundo)
    if fundo is None:
        raise SystemExit(f"Fundo nao encontrado: {args.fundo}")
    return fundo


def cmd_cronograma_importar(args) -> int:
    from . import cronograma as cr

    fundo = _fundo_do_args(args)

    if args.planilha:
        planilha = Path(args.planilha)
        if not planilha.exists():
            raise SystemExit(f"Planilha nao encontrada: {planilha}")
    else:
        planilha = cr.localizar_planilha()
        if planilha is None:
            raise SystemExit(
                f"Nenhuma planilha em {cr.DIR_PLANILHAS}. "
                f"Informe o caminho com --planilha."
            )

    print(f"Fundo ....... {fundo.nome_interno} ({fundo.cnpj})")
    print(f"Planilha .... {planilha.name}")

    # A carteira e quem define quais operacoes importam. So as abas dos
    # Codigo_Op presentes nela sao lidas.
    codigos = None
    if not args.todas_as_abas:
        from . import fluxo as fx
        from .extrator import carregar_base
        from .prazos import TabelaPrazos
        from .tratamentos import tratar_fundo

        saida = Path(args.saida)
        carteira = carregar_base(saida, "carteira", fundo.cnpj)
        if carteira.empty:
            raise SystemExit(
                f"Sem carteira na carga em {saida.resolve()} para {fundo.nome_interno}. "
                f"Extraia a carga, ou use --todas-as-abas para ler a planilha inteira."
            )
        bd = carregar_base(saida, "bd", fundo.cnpj)
        resultado = tratar_fundo(fundo, carteira, bd, TabelaPrazos.carregar())
        posicoes = fx.posicoes_do_produto(
            resultado.carteira, args.produto, regra_vencidos=resultado.regra_vencidos
        )
        codigos = set(posicoes["codigo_op"].astype(str))
        print(f"Carteira .... {saida.resolve()}")
        print(f"Operacoes ... {len(codigos)} de {args.produto} na carteira")
    else:
        print("Carteira .... (ignorada: --todas-as-abas)")

    try:
        leitura = cr.importar(planilha, fundo.cnpj, codigos)
    except cr.ErroPlanilha as exc:
        raise SystemExit(str(exc))

    ops, parcelas = leitura.operacoes, leitura.parcelas
    atual = cr.Cronograma.carregar_se_houver(fundo.cnpj)
    diferencas = cr.comparar(atual, ops, parcelas)

    print(f"\nLido: {len(ops)} operacao(oes), {len(parcelas)} parcela(s).\n")
    for linha in diferencas.resumir():
        print(f"  {linha}")

    if leitura.sem_aba:
        print(f"\nSEM ABA NA PLANILHA: {len(leitura.sem_aba)} Codigo_Op da carteira "
              f"sem aba correspondente.")
        for codigo in leitura.sem_aba[:20]:
            print(f"   {codigo}")
    if leitura.aba_vazia:
        print(f"\nABA SEM CRONOGRAMA: {len(leitura.aba_vazia)} aba(s) encontradas mas vazias.")
        for codigo, aba in leitura.aba_vazia[:20]:
            print(f"   {codigo:<12} {aba}")
    if leitura.layout_desconhecido:
        print(f"\nLAYOUT NAO RECONHECIDO: {len(leitura.layout_desconhecido)} aba(s) sem as "
              f"colunas 'Vencimento'/'Amortizacao' -- outro modelo de papel, nao importadas.")
        for codigo, aba in leitura.layout_desconhecido[:20]:
            print(f"   {codigo:<12} {aba}")
    if leitura.fora_da_carteira:
        print(f"\n{len(leitura.fora_da_carteira)} aba(s) da planilha fora da carteira, "
              f"nao lidas: {', '.join(leitura.fora_da_carteira[:12])}"
              + (" ..." if len(leitura.fora_da_carteira) > 12 else ""))

    if not diferencas.houve:
        print("\nNada mudou em relacao ao cadastro atual.")
        if args.conferir:
            return 0

    if args.conferir:
        print("\n(--conferir: nada foi gravado)")
        return 0

    if atual is not None and diferencas.houve and not args.forcar:
        resposta = input("\nConfirma a atualizacao do cadastro? [s/N] ").strip().lower()
        if resposta not in ("s", "sim"):
            print("Cancelado; nada foi gravado.")
            return 1

    # `gravar` devolve um caminho unico (o JSON do fundo), nao uma tupla: o
    # cadastro deixou de ser dois CSVs mais um de origem quando virou JSON.
    caminho = cr.gravar(ops, parcelas, fundo.cnpj, planilha)
    print(f"\nGravado: {Path(caminho).resolve()}")
    return 0


def _codigos_da_carteira(fundo, saida: Path, produto: str) -> set[str]:
    """Codigo_Op do produto na carteira -- e a carteira que define o que ler."""
    from . import fluxo as fx
    from .extrator import carregar_base
    from .prazos import TabelaPrazos
    from .tratamentos import tratar_fundo

    carteira = carregar_base(saida, "carteira", fundo.cnpj)
    if carteira.empty:
        raise SystemExit(
            f"Sem carteira na carga em {saida.resolve()} para {fundo.nome_interno}."
        )
    bd = carregar_base(saida, "bd", fundo.cnpj)
    resultado = tratar_fundo(fundo, carteira, bd, TabelaPrazos.carregar())
    posicoes = fx.posicoes_do_produto(
        resultado.carteira, produto, regra_vencidos=resultado.regra_vencidos
    )
    return set(posicoes["codigo_op"].astype(str))


def cmd_cronograma_sincronizar(args) -> int:
    """Consome a pasta de entrada: arquivo depositado = ordem de reimportar.

    O CNPJ tem que estar no nome do arquivo. Arquivo que nao identificar o fundo
    fica na pasta com aviso -- nada e adivinhado. Pasta vazia, nada acontece.

    Codigo de saida: 0 nada a fazer ou tudo processado, 2 sobrou arquivo sem
    identificar, 1 falha na leitura de algum arquivo.
    """
    from . import cronograma as cr

    entrada = Path(args.entrada)
    arquivos = cr.planilhas_na_entrada(entrada)
    if not arquivos:
        print(f"Pasta de entrada vazia ({entrada}); cadastro mantido.")
        return 0

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    saida = Path(args.saida)
    print(f"{len(arquivos)} arquivo(s) em {entrada.resolve()}\n")

    processados, nao_identificados, com_erro = 0, [], []

    for arquivo in arquivos:
        cnpj = cr.cnpj_do_arquivo(arquivo)
        if cnpj is None:
            nao_identificados.append((arquivo.name, "sem CNPJ no nome"))
            print(f"IGNORADO  {arquivo.name}: sem CNPJ no nome do arquivo.")
            continue
        fundo = cadastro.fundos.get(cnpj)
        if fundo is None:
            nao_identificados.append((arquivo.name, f"CNPJ {cnpj} fora do cadastro"))
            print(f"IGNORADO  {arquivo.name}: CNPJ {cnpj} nao esta no cadastro de fundos.")
            continue

        print(f"--- {arquivo.name}  ->  {fundo.nome_interno}")
        try:
            codigos = _codigos_da_carteira(fundo, saida, args.produto)
            leitura = cr.importar(arquivo, fundo.cnpj, codigos)
        except (cr.ErroPlanilha, SystemExit) as exc:
            com_erro.append((arquivo.name, str(exc)))
            print(f"ERRO      {arquivo.name}: {exc}")
            continue

        atual = cr.Cronograma.carregar_se_houver(fundo.cnpj)
        diferencas = cr.comparar(atual, leitura.operacoes, leitura.parcelas)
        print(f"   carteira: {len(codigos)} operacao(oes) de {args.produto} | "
              f"lido: {len(leitura.operacoes)} operacao(oes), {len(leitura.parcelas)} parcela(s)")
        for linha in diferencas.resumir():
            print(f"   {linha}")
        if leitura.sem_aba:
            print(f"   SEM ABA: {', '.join(leitura.sem_aba[:10])}")
        if leitura.aba_vazia:
            print(f"   ABA VAZIA: {', '.join(c for c, _ in leitura.aba_vazia[:10])}")

        if args.conferir:
            print("   (--conferir: nada gravado, arquivo mantido na pasta)")
            continue

        backup = cr.salvar_backup(fundo.cnpj)
        cr.gravar(leitura.operacoes, leitura.parcelas, fundo.cnpj, arquivo)
        destino = cr.arquivar(arquivo, Path(args.processados))
        processados += 1
        if backup:
            print(f"   backup do cadastro anterior em {backup}")
        print(f"   arquivado em {destino}")

    print(f"\n{processados} arquivo(s) processado(s).")
    if nao_identificados:
        print(f"{len(nao_identificados)} nao identificado(s), mantido(s) na pasta:")
        for nome, motivo in nao_identificados:
            print(f"   {nome}  ({motivo})")
    if com_erro:
        print(f"{len(com_erro)} com erro:")
        for nome, mensagem in com_erro:
            print(f"   {nome}: {str(mensagem)[:70]}")

    if com_erro:
        return 1
    return 2 if nao_identificados else 0


def cmd_cronograma_list(args) -> int:
    from . import cronograma as cr

    fundo = _fundo_do_args(args)
    try:
        crono = cr.Cronograma.carregar(fundo.cnpj)
    except cr.ErroPlanilha as exc:
        raise SystemExit(str(exc))

    ops = crono.operacoes
    if args.emissor:
        alvo = args.emissor.strip().upper()
        ops = ops[ops["emissor"].astype(str).str.upper().str.contains(alvo, na=False)]
    if ops.empty:
        print("Nenhuma operacao para o filtro informado.")
        return 0

    print(f"{'CODIGO':<10} {'EMISSOR':<28} {'PRINCIPAL':>16} {'OPERACAO':<11} "
          f"{'IDX':<5} {'TAXA A.D.':>10} {'PARC':>5}")
    for _, linha in ops.iterrows():
        idx = linha["indexador"] if linha["indexado"] else "PRE"
        print(f"{linha['codigo_op']:<10} {str(linha['emissor'])[:28]:<28} "
              f"{float(linha['vl_principal'] or 0):>16,.2f} {str(linha['dt_operacao']):<11} "
              f"{str(idx):<5} {float(linha['taxa_ad_efetiva'] or 0):>10.6f} "
              f"{int(linha['n_parcelas']):>5}")

    origem = cr.ler_origem(fundo.cnpj)
    if origem:
        print(f"\n{len(ops)} operacao(oes). Importado em {origem['importado_em']} "
              f"de {origem['planilha']['arquivo']}.")
    aviso = cr.planilha_mudou(fundo.cnpj)
    if aviso:
        print(f"\nAVISO  {aviso}")
    return 0


def cmd_cronograma_show(args) -> int:
    import pandas as pd

    from . import cronograma as cr

    fundo = _fundo_do_args(args)
    try:
        crono = cr.Cronograma.carregar(fundo.cnpj)
    except cr.ErroPlanilha as exc:
        raise SystemExit(str(exc))

    operacao = crono.operacao(args.codigo)
    if operacao is None:
        raise SystemExit(f"Operacao nao encontrada no cronograma: {args.codigo}")

    for campo in cr.COLUNAS_OPERACOES:
        if campo in operacao.index:
            valor = operacao[campo]
            print(f"{campo:<22} {'' if pd.isna(valor) else valor}")

    parcelas = crono.parcelas_de(args.codigo)
    print(f"\n{'PARC':>5} {'VENCIMENTO':<12} {'AMORTIZACAO':>16}")
    for _, linha in parcelas.iterrows():
        print(f"{int(linha['nr_parcela']):>5} {str(linha['dt_vencimento']):<12} "
              f"{float(linha['vl_amortizacao'] or 0):>16,.2f}")
    total = float(pd.to_numeric(parcelas["vl_amortizacao"], errors="coerce").sum())
    print(f"{'':>5} {'TOTAL':<12} {total:>16,.2f}")
    principal = float(operacao["vl_principal"] or 0)
    if abs(total - principal) > 1.0:
        print(f"\nAVISO  amortizacao total difere do principal em {total - principal:,.2f}")
    return 0


def cmd_cronograma_beyond(args) -> int:
    """Captura do Beyond o cronograma das NC/CCB que nao tem fluxo cadastrado.

    Roda sobre a posicao mais recente da carteira e sobre a leitura mais recente
    do Beyond -- que e semanal, nao diaria. Operacao ja capturada nao e
    reescrita: a captura e unica por construcao, e recapturar exige apagar a
    operacao do arquivo. Devolve codigo 2 se sobrou alguem na fila.
    """
    from . import beyond_cronograma as bc
    from . import cronograma as cr
    from . import db

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    fundos = ([cadastro.obter(args.fundo)] if args.fundo
              else [f for f in cadastro.filtrar(adm=args.adm) if f.codinome_beyond])
    fundos = [f for f in fundos if f is not None]
    if not fundos:
        print("Nenhum fundo com codinome do Beyond no filtro.")
        return 0

    snapshot = bc.snapshot_atual()
    if snapshot is None:
        print("Beyond sem nenhuma leitura disponivel.")
        return 1
    print(f"Leitura do Beyond: {snapshot}\n")

    if args.inventario:
        for fundo in fundos:
            if fundo.codinome_beyond:
                _inventario_beyond(fundo, snapshot)
        return 0

    total_novas = total_fila = 0
    for fundo in fundos:
        if not fundo.codinome_beyond:
            print(f"AVISO  {fundo.nome_interno}: sem codinome do Beyond; pulado.")
            continue
        # A posicao e a da CARGA local (a mesma que o fluxo projeta), nao a mais
        # recente do banco: em 05/10/2026 a carteira de 02/10 estava no riscogc
        # pela metade (0 NC/CCB do MULTIPLICA FIDC) e a captura voltava vazia.
        data = args.data or _data_da_carga_local(Path(args.saida), fundo.cnpj) or db.consultar(
            f"SELECT MAX(dtReferencia) AS d FROM {CARTEIRA}"
            " WHERE docFundo = ?", (fundo.cnpj,)).iloc[0]["d"]
        if data is None or (hasattr(data, "__class__") and str(data) in ("NaT", "None")):
            print(f"AVISO  {fundo.nome_interno}: nenhuma posicao na MUL_GLD_CARTEIRA_UNIFICADA"
                  f" para docFundo = '{fundo.cnpj}'; pulado.")
            continue

        cadastrado = cr.Cronograma.carregar_se_houver(fundo.cnpj)
        ja = cadastrado.codigos if cadastrado is not None else set()

        linhas = db.consultar(
            "SELECT cdOp AS Codigo_Op, dsEmissor AS Emissor, dtAquisicao AS Data_Aquisicao,"
            " vlCustoTotal AS Custo_Total"
            f" FROM {CARTEIRA}"
            " WHERE docFundo = ? AND dtReferencia = ? AND vlCustoTotal > 0"
            "   AND (cdAtivo LIKE 'NCPX%' OR cdAtivo LIKE 'CCBPX%')"
            " ORDER BY vlCustoTotal DESC", (fundo.cnpj, str(data)))

        capturados, fila = [], []
        for _, linha in linhas.iterrows():
            codigo = cr.normalizar_codigo(linha["Codigo_Op"])
            if codigo is None or codigo in ja:
                continue
            crono, motivo = bc.montar(
                fundo.cnpj, codigo, fundo.codinome_beyond, None,
                linha["Data_Aquisicao"], float(linha["Custo_Total"]),
                str(linha["Emissor"] or ""))
            (capturados if crono else fila).append(crono or (codigo, motivo,
                                                             float(linha["Custo_Total"])))
        if capturados:
            bc.gravar(fundo.cnpj, capturados, snapshot=snapshot)
        total_novas += len(capturados)
        total_fila += len(fila)
        print(f"{fundo.nome_interno:<40} posicao {data} | candidatas {len(linhas):>3}"
              f" | ja cadastradas {len(ja):>3} | capturadas {len(capturados):>3} | fila {len(fila):>3}")
        if args.detalhar and linhas.empty:
            # Sem candidata nao ha fila nem motivo: e preciso olhar as duas pontas
            # (carteira e Beyond) para saber onde o casamento morre.
            _diagnosticar_beyond(fundo, data, snapshot)
        if fila and args.detalhar:
            for codigo, motivo, valor in fila:
                print(f"     {codigo:<10} {valor:>16,.2f}  {motivo}")

    print(f"\n{total_novas} operacao(oes) capturada(s); {total_fila} na fila.")
    if total_fila:
        print("Use --detalhar para ver o motivo de cada uma.")
    return 2 if total_fila else 0


def _data_da_carga_local(saida: Path, cnpj: str) -> str | None:
    """Data de posicao da carteira extraida em `saida` para o fundo, se houver."""
    from .extrator import carregar_base
    from .tratamentos import _data_da_carga

    try:
        carteira = carregar_base(saida, "carteira", cnpj)
    except FileNotFoundError:
        return None
    if carteira.empty:
        return None
    return _data_da_carga(carteira, carteira) or None


def _inventario_beyond(fundo, snapshot) -> None:
    """O que o Beyond tem para o fundo na leitura: familias x produto.

    Serve para decidir ate onde a captura pode ir alem de NC/CCB -- hoje ela so
    procura `Grupo_Produto LIKE 'CCB/NC%'`, e no MULTIPLICA FIDC isso cobre 57
    das 328 operacoes de DC. So SELECT.
    """
    from . import db

    df = db.consultar(
        "SELECT Grupo_Produto, Produto, Situacao_Titulo,"
        " COUNT(DISTINCT Operacao) AS operacoes, COUNT(*) AS titulos,"
        " SUM(Valor_Aplicado) AS aplicado, MIN(Data_Operacao) AS op_min, MAX(Data_Operacao) AS op_max"
        " FROM dbo.TB_LIQUIDEZ_OPERACOES_4 WHERE Fundo = ? AND Data = ?"
        " GROUP BY Grupo_Produto, Produto, Situacao_Titulo"
        " ORDER BY Grupo_Produto, Produto, Situacao_Titulo",
        (fundo.codinome_beyond, str(snapshot)))
    print(f"{fundo.nome_interno} -> Beyond '{fundo.codinome_beyond}' em {snapshot}: {len(df)} grupo(s)")
    if df.empty:
        return
    print(f"   {'Grupo_Produto':<22} {'Produto':<22} {'Situacao':<14} {'ops':>5} {'titulos':>7} {'aplicado':>18}  Data_Operacao")
    for _, r in df.iterrows():
        print(f"   {str(r['Grupo_Produto']):<22} {str(r['Produto']):<22} {str(r['Situacao_Titulo']):<14}"
              f" {int(r['operacoes'] or 0):>5} {int(r['titulos']):>7} {float(r['aplicado'] or 0):>18,.2f}"
              f"  {r['op_min']!r} .. {r['op_max']!r}")


def _diagnosticar_beyond(fundo, data, snapshot) -> None:
    """Por que a captura do Beyond nao achou candidata: conta as duas pontas.

    Lado carteira: quantas linhas NCPX/CCBPX existem na posicao, quantas tem
    Custo_Total > 0 (exigencia da captura) e como vem o Codigo_Ativo. Lado
    Beyond: quantas operacoes CCB/NC a leitura tem para o codinome e em que
    formato esta `Data_Operacao` (varchar no banco) -- a chave do casamento e
    `Data_Aquisicao == Data_Operacao`, e um formato diferente zera tudo.
    So SELECT.
    """
    from . import db

    cart = db.consultar(
        "SELECT COUNT(*) AS linhas,"
        " SUM(CASE WHEN cdAtivo LIKE 'NCPX%' OR cdAtivo LIKE 'CCBPX%' THEN 1 ELSE 0 END) AS ncpx,"
        " SUM(CASE WHEN (cdAtivo LIKE 'NCPX%' OR cdAtivo LIKE 'CCBPX%')"
        "           AND vlCustoTotal > 0 THEN 1 ELSE 0 END) AS ncpx_com_custo,"
        " SUM(CASE WHEN vlCustoTotal IS NULL THEN 1 ELSE 0 END) AS custo_nulo,"
        " SUM(CASE WHEN vlCustoTotal = 0 THEN 1 ELSE 0 END) AS custo_zero,"
        " SUM(CASE WHEN dtAquisicao IS NULL THEN 1 ELSE 0 END) AS aquisicao_nula"
        f" FROM {CARTEIRA} WHERE docFundo = ? AND dtReferencia = ?",
        (fundo.cnpj, str(data)))
    c = cart.iloc[0]
    print(f"     carteira {data}: {int(c['linhas'])} linhas | NCPX/CCBPX {int(c['ncpx'] or 0)}"
          f" (com Custo_Total>0: {int(c['ncpx_com_custo'] or 0)}) | Custo_Total nulo {int(c['custo_nulo'] or 0)},"
          f" zero {int(c['custo_zero'] or 0)} | Data_Aquisicao nula {int(c['aquisicao_nula'] or 0)}")
    exemplos = db.consultar(
        "SELECT TOP (5) cdOp AS Codigo_Op, cdAtivo AS Codigo_Ativo, dtAquisicao AS Data_Aquisicao,"
        " vlCustoTotal AS Custo_Total, vlLiquido AS Valor_Liquido"
        f" FROM {CARTEIRA}"
        " WHERE docFundo = ? AND dtReferencia = ? AND cdOp LIKE 'C%'"
        " ORDER BY vlLiquido DESC", (fundo.cnpj, str(data)))
    for _, e in exemplos.iterrows():
        print(f"        {e['Codigo_Op']:<10} {str(e['Codigo_Ativo']):<8} aquis {e['Data_Aquisicao']}"
              f"  custo {e['Custo_Total']}  liquido {e['Valor_Liquido']}")

    byd = db.consultar(
        "SELECT COUNT(*) AS linhas, COUNT(DISTINCT Operacao) AS operacoes,"
        " SUM(CASE WHEN Grupo_Produto LIKE 'CCB/NC%' THEN 1 ELSE 0 END) AS ccb_nc,"
        " MIN(Data_Operacao) AS dt_min, MAX(Data_Operacao) AS dt_max"
        " FROM dbo.TB_LIQUIDEZ_OPERACOES_4 WHERE Fundo = ? AND Data = ?",
        (fundo.codinome_beyond, str(snapshot)))
    b = byd.iloc[0]
    print(f"     beyond '{fundo.codinome_beyond}' em {snapshot}: {int(b['linhas'])} linhas,"
          f" {int(b['operacoes'] or 0)} operacoes, CCB/NC {int(b['ccb_nc'] or 0)}"
          f" | Data_Operacao de {b['dt_min']!r} a {b['dt_max']!r}")
    grupos = db.consultar(
        "SELECT Grupo_Produto, COUNT(*) AS linhas FROM dbo.TB_LIQUIDEZ_OPERACOES_4"
        " WHERE Fundo = ? AND Data = ? GROUP BY Grupo_Produto ORDER BY linhas DESC",
        (fundo.codinome_beyond, str(snapshot)))
    if not grupos.empty:
        print("     Grupo_Produto: " + " | ".join(
            f"{g['Grupo_Produto']}={int(g['linhas'])}" for _, g in grupos.iterrows()))


def cmd_cronograma_validar(args) -> int:
    """Cruza a carteira tratada com o cronograma.

    Regra: linha do produto alvo com `Ativo Vencido = NAO` precisa ter aba na
    planilha. Sem aba, e pendencia a reportar -- nao da para projetar o fluxo
    dela. Linha vencida nao precisa de aba: nao entra no fluxo futuro, e as
    linhas `VC*` sao a parcela ja vencida virada ativo, nao uma operacao.
    """
    import pandas as pd

    from . import cronograma as cr
    from .analise import COL_PRODUTO
    from .extrator import carregar_base
    from .prazos import TabelaPrazos
    from .tratamentos import COL_ATIVO_VENCIDO, selecao_vencidos_carteira, tratar_fundo

    fundo = _fundo_do_args(args)
    try:
        crono = cr.Cronograma.carregar(fundo.cnpj)
    except cr.ErroPlanilha as exc:
        raise SystemExit(str(exc))

    saida = Path(args.saida)
    carteira = carregar_base(saida, "carteira", fundo.cnpj)
    if carteira.empty:
        raise SystemExit(f"Sem carteira na carga em {saida.resolve()} para {fundo.nome_interno}.")
    bd = carregar_base(saida, "bd", fundo.cnpj)
    resultado = tratar_fundo(fundo, carteira, bd, TabelaPrazos.carregar())
    tratada = resultado.carteira

    produto = str(args.produto).strip().upper()
    e_produto = tratada[COL_PRODUTO].fillna("").astype(str).str.strip().str.upper() == produto
    if not e_produto.any():
        disponiveis = sorted(tratada[COL_PRODUTO].dropna().astype(str).unique())
        raise SystemExit(f"Nenhuma linha do produto '{produto}'. Presentes: {disponiveis}")

    # Duas coisas diferentes, que o relatorio nao pode misturar:
    #   e_ativo_vencido -- a linha E um ativo vencido (a parcela que venceu virou
    #     ativo proprio, com Codigo_Op novo). Nao e operacao, nao tem aba.
    #   COL_ATIVO_VENCIDO -- marca propagada por emissor na etapa 3: vale para
    #     todas as linhas do emissor, inclusive operacoes intactas.
    e_ativo_vencido = selecao_vencidos_carteira(tratada, resultado.regra_vencidos)

    alvo = tratada[e_produto]
    vencido_proprio = e_ativo_vencido[e_produto].fillna(False)
    e_operacao = ~vencido_proprio
    marcado = alvo[COL_ATIVO_VENCIDO].fillna("").astype(str).str.strip().str.upper() == "SIM"
    codigos = alvo["Codigo_Op"].map(cr.normalizar_codigo)
    tem_aba = codigos.map(lambda c: c is not None and c in crono.codigos)
    valor = pd.to_numeric(alvo["Valor_Liquido"], errors="coerce").fillna(0.0)

    print(f"Fundo ........ {fundo.nome_interno} ({fundo.cnpj})")
    print(f"Produto ...... {produto}")
    print(f"Carga ........ {saida.resolve()}")
    print(f"Cronograma ... {len(crono.codigos)} operacao(oes) no cadastro")
    print(f"Regra ........ vencido = {' E '.join(resultado.regra_vencidos.descrever())}\n")

    # Operacao sem valor na carteira nao tem fluxo a projetar: nao se cobra
    # cronograma dela. Costuma ser parcela vencida zerada, que tem codigo VC*
    # mas escapa da regra do administrador por exigir valor positivo.
    sem_valor = valor == 0
    cobravel = e_operacao & ~sem_valor

    print(f"{'':<34} {'LINHAS':>7} {'VALOR LIQUIDO':>18}")
    for rotulo, recorte in (
        ("operacoes", e_operacao),
        ("   com aba na planilha", e_operacao & tem_aba),
        ("   SEM ABA  <- pendencia", cobravel & ~tem_aba),
        ("   sem aba, mas valor zero", e_operacao & sem_valor & ~tem_aba),
        ("ativos vencidos (parcela vencida)", vencido_proprio),
    ):
        print(f"{rotulo:<34} {int(recorte.sum()):>7} {valor[recorte].sum():>18,.2f}")
    print(f"{'TOTAL':<34} {len(alvo):>7} {valor.sum():>18,.2f}")

    n_ops = int(e_operacao.sum())
    if n_ops:
        print(f"\nMarca 'Ativo Vencido' nas {n_ops} operacoes (propagada por emissor):")
        for rotulo, recorte, destino in (
            ("NAO", e_operacao & ~marcado, "entram nas faixas de prazo"),
            ("SIM", e_operacao & marcado, "vao para a linha Vencidos"),
        ):
            print(f"   {rotulo} -> {int(recorte.sum()):>3} {valor[recorte].sum():>18,.2f}   {destino}")

    pendentes = alvo[(cobravel & ~tem_aba).values]
    if not pendentes.empty:
        print(f"\nPENDENCIAS -- {len(pendentes)} linha(s) sem aba na planilha:")
        colunas = [c for c in ("Codigo_Op", "Codigo_Ativo", "Emissor", "Valor_Liquido")
                   if c in pendentes.columns]
        agrupado = (
            pendentes.assign(_v=pd.to_numeric(pendentes["Valor_Liquido"], errors="coerce"))
            .groupby([pendentes["Codigo_Op"].astype(str)], dropna=False)["_v"]
            .agg(["count", "sum"])
            .sort_values("sum", ascending=False)
        )
        print(f"  {'CODIGO_OP':<20} {'LINHAS':>7} {'VALOR LIQUIDO':>18}")
        for codigo, linha in agrupado.head(args.limite).iterrows():
            print(f"  {str(codigo)[:20]:<20} {int(linha['count']):>7} {linha['sum']:>18,.2f}")
        if len(agrupado) > args.limite:
            print(f"  ... mais {len(agrupado) - args.limite} codigo(s) (use --limite)")
        if args.detalhar:
            print("\n  detalhe das linhas:")
            print(pendentes[colunas].to_string(index=False, max_colwidth=34))

    # Abas do cadastro que nao aparecem na carteira: operacao liquidada ou de
    # outro fundo. Nao e erro, mas explica diferenca de contagem.
    codigos_carteira = {c for c in codigos[e_operacao.values].dropna().unique()}
    orfas = sorted(crono.codigos - codigos_carteira)
    if orfas:
        print(f"\n{len(orfas)} operacao(oes) no cadastro sem linha na carteira "
              f"(liquidada ou de outro fundo):")
        print("  " + ", ".join(orfas[:args.limite])
              + (f" ... +{len(orfas) - args.limite}" if len(orfas) > args.limite else ""))

    aviso = cr.planilha_mudou(fundo.cnpj)
    if aviso:
        print(f"\nAVISO  {aviso}")

    if not pendentes.empty:
        print(f"\n{len(pendentes)} pendencia(s) a resolver.")
        return 1
    print("\nOK: toda operacao tem aba correspondente na planilha.")
    return 0


def _projetar_um_fundo(fundo, args) -> dict:
    """Projeta um fundo, imprime o bloco dele e devolve o que precisa de atencao.

    Nos fundos da QI Tech, operacao DC sem cronograma nao e descartada: e
    projetada como bullet a partir da carteira. As que aparecem pela primeira
    vez entram na fila de conferencia -- e o que sai daqui como `novas`.
    """
    import pandas as pd

    from . import bullets as bl
    from . import cronograma as cr
    from . import fluxo as fx
    from .extrator import carregar_base
    from .prazos import TabelaPrazos
    from .tratamentos import _data_da_carga, tratar_fundo

    # Fundo no escopo bullet nao precisa de planilha: o fluxo dos DC vem da
    # carteira. Fora do escopo, cronograma ausente segue sendo erro.
    no_escopo = bl.e_administrador_bullet(fundo.administrador) and not args.sem_bullet
    crono = cr.Cronograma.carregar(fundo.cnpj, exigir=not no_escopo)
    saida = Path(args.saida)
    carteira = carregar_base(saida, "carteira", fundo.cnpj)
    if carteira.empty:
        # Fundo sem ponta na carga do dia. Pedido explicitamente, e erro; no
        # lote e so um fundo que nao tem posicao -- acontece e nao e problema.
        if args.fundo:
            raise SystemExit(
                f"Sem carteira na carga em {saida.resolve()} para {fundo.nome_interno}."
            )
        print(f"\n{fundo.nome_interno}: sem carteira nesta carga; pulado.")
        return {"novas": [], "sem_cronograma": [], "total": 0.0}
    bd = carregar_base(saida, "bd", fundo.cnpj)
    resultado = tratar_fundo(fundo, carteira, bd, TabelaPrazos.carregar())

    data_base = (
        dt.date.fromisoformat(args.data) if args.data
        else dt.date.fromisoformat(_data_da_carga(carteira, bd))
    )

    posicoes = fx.posicoes_do_produto(
        resultado.carteira, args.produto,
        somente_nao_vencidos=args.somente_nao_vencidos,
        regra_vencidos=resultado.regra_vencidos,
    )
    vazio = {"novas": [], "sem_cronograma": [], "total": 0.0}
    if posicoes.empty:
        print(f"\n{fundo.nome_interno}: nenhuma operacao de {args.produto} para projetar.")
        return vazio

    # A premissa bullet vale no recorte do administrador e so no produto dele.
    usar_bullet = (
        bl.e_administrador_bullet(fundo.administrador)
        and args.produto.upper() == bl.PRODUTO_BULLET
        and not args.sem_bullet
    )
    # Familias como o CRA entram na premissa em qualquer fundo, entao o estado
    # de conferencia e sempre necessario -- le um JSON pequeno, nao custa.
    familias = posicoes["codigo_ativo"] if "codigo_ativo" in posicoes else []
    tem_presumida = any(bl.e_familia_presumida(c) for c in familias)
    estados = ({} if args.sem_bullet
               else bl.estados_de(posicoes["codigo_op"]))

    recorte = "nao vencidas" if args.somente_nao_vencidos else "todas"
    print(f"Fundo ........ {fundo.nome_interno} ({fundo.cnpj})")
    print(f"Produto ...... {args.produto}   (recorte: {recorte})")
    print(f"Data-base .... {data_base:%d/%m/%Y}")
    print(f"Operacoes .... {len(posicoes)}   valor base R$ {posicoes['valor_base'].sum():,.2f}")
    escopo = "sim" if usar_bullet else "nao"
    if tem_presumida and not args.sem_bullet:
        escopo += f" (+ familia {'/'.join(bl.FAMILIAS_PRESUMIDAS)})"
    print(f"Bullet ....... {escopo}   "
          f"(administrador: {fundo.administrador or 'nao informado'})")

    # Perfil de vencimento do estoque do fundo, para as linhas agregadas
    # (MEZAN/SRP) que nao tem cronograma nem Beyond -- ver bullets.FAMILIAS_ESTOQUE.
    pesos_estoque = fx.pesos_do_estoque(resultado.bd, data_base)
    projecao = fx.projetar(posicoes, crono, data_base, cnpj_fundo=fundo.cnpj,
                           mercadoria=args.mercadoria,
                           bullet=usar_bullet, estados_bullet=estados,
                           decompor_acrual=getattr(args, "acrual", False),
                           pesos_estoque=pesos_estoque)
    if projecao.pelo_estoque:
        total_est = sum(e["valor"] for e in projecao.pelo_estoque)
        print(f"\nESTOQUE  {len(projecao.pelo_estoque)} operacao(oes) agregada(s) projetada(s) pelo "
              f"estoque do fundo (R$ {total_est:,.2f}): {len(pesos_estoque)} data(s) de vencimento, "
              f"VP a vencer R$ {float(pesos_estoque['valor_presente'].sum()):,.2f}")
        for e in projecao.pelo_estoque[:args.limite]:
            print(f"   {e['codigo_op']:<12} {e['codigo_ativo']:<8} {e['valor']:>18,.2f}  {e['n_parcelas']} parcela(s)")

    # Registrar antes de qualquer saida antecipada: o que apareceu hoje precisa
    # ficar anotado mesmo que nao haja parcela futura nenhuma.
    # Coberta pela regra da familia nao entra no registro: nao ha conferencia a
    # pedir, e registra-la so faria a fila parecer maior do que e.
    # Sem `usar_bullet` na condicao: familia presumida (CRA) entra na fila em
    # qualquer fundo, e nesses casos `usar_bullet` e falso.
    novas = []
    if projecao.bullets and args.salvar:
        a_registrar = [(b["codigo_op"], fundo.cnpj) for b in projecao.bullets
                       if b["estado"] != bl.POR_REGRA]
        if a_registrar:
            novas = bl.registrar_vistas(a_registrar, data_base)

    fluxo = projecao.fluxo
    if fluxo.empty:
        print("\nNenhuma parcela futura para projetar.")
        return {"novas": [(fundo.nome_interno, c, 0.0) for c in novas],
                "sem_cronograma": projecao.sem_cronograma, "total": 0.0}

    print(f"\n{'amortizacao projetada':<26} {fluxo['vl_amortizacao'].sum():>18,.2f}")
    print(f"{'juros projetados':<26} {fluxo['vl_juros'].sum():>18,.2f}")
    if projecao.decomposicoes:
        print(f"{'acrual (juro corrido)':<26} {fluxo['vl_acrual'].sum():>18,.2f}")
    print(f"{'total das parcelas':<26} {fluxo['vl_parcela'].sum():>18,.2f}")
    # Sem decomposicao, a amortizacao sozinha fecha com a carteira. Com ela, o
    # juro corrido saiu do principal e virou linha propria: a ancora passa a ser
    # `principal + acrual = posicao`.
    conferido = fluxo["vl_amortizacao"].sum() + fluxo["vl_acrual"].sum()
    rotulo = "principal+acrual - carteira" if projecao.decomposicoes else "amortizacao - carteira"
    print(f"{rotulo:<26} {conferido - posicoes['valor_base'].sum():>18,.6f}")

    if projecao.decomposicoes:
        duvidosas = [d for d in projecao.decomposicoes if not d.confiavel]
        print(f"\nACRUAL  {len(projecao.decomposicoes)} de {len(posicoes)} operacao(oes) "
              f"decomposta(s); R$ {projecao.total_acrual:,.2f} de juro corrido "
              f"separado do principal.")
        if duvidosas:
            print(f"        AVISO  {len(duvidosas)} com dia util sem CDI na serie -- "
                  f"acrual subestimado nessas:")
            for d in duvidosas[:args.limite]:
                faltam = ", ".join(f"{x:%d/%m/%Y}" for x in d.dias_sem_cdi[:3])
                print(f"           {d.codigo_op:<12} {len(d.dias_sem_cdi)} dia(s): {faltam}")

    prazo = fundo.cc
    if prazo is not None:
        dentro = fluxo[fluxo["qt_dias"] <= prazo]
        print(f"\nPrazo de cotizacao: {prazo} dias")
        print(f"  vencendo ate o prazo ..... {len(dentro):>4} parcela(s)  "
              f"R$ {dentro['vl_parcela'].sum():>16,.2f}")
        fora = fluxo[fluxo["qt_dias"] > prazo]
        print(f"  vencendo depois .......... {len(fora):>4} parcela(s)  "
              f"R$ {fora['vl_parcela'].sum():>16,.2f}")

    if args.por_mes:
        meses = fluxo.assign(
            mes=pd.to_datetime(fluxo["dt_vencimento"]).dt.to_period("M").astype(str)
        ).groupby("mes")["vl_parcela"].agg(["size", "sum"])
        print(f"\n{'MES':<10} {'PARCELAS':>9} {'VALOR':>18}")
        for mes, linha in meses.head(args.limite).iterrows():
            print(f"{mes:<10} {int(linha['size']):>9} {linha['sum']:>18,.2f}")
        if len(meses) > args.limite:
            print(f"... mais {len(meses) - args.limite} mes(es) (use --limite)")

    if projecao.bullets:
        por_estado = {}
        for b in projecao.bullets:
            por_estado.setdefault(b["estado"], []).append(b)
        total_bullet = sum(b["valor"] for b in projecao.bullets)
        print(f"\nBULLET  {len(projecao.bullets)} operacao(oes) projetada(s) da carteira "
              f"(R$ {total_bullet:,.2f}):")
        rotulos = {
            bl.POR_REGRA: "por regra da familia (nao pedem conferencia)",
            bl.CONFIRMADO: "conferidas, sao bullet",
            bl.PLANILHA_PENDENTE: "conferidas, TEM fluxo -- planilha pendente",
            bl.ESTOQUE: "estoque do marco zero, a conferir",
            bl.PENDENTE: "a conferir",
        }
        for estado, rotulo in rotulos.items():
            lista = por_estado.get(estado)
            if not lista:
                continue
            soma = sum(b["valor"] for b in lista)
            print(f"   {len(lista):>4} {rotulo:<44} R$ {soma:>16,.2f}")

        # A fila do dia e so o que apareceu agora. O estoque vira contagem --
        # uma lista que nasce com centenas de linhas ninguem trabalha.
        if novas:
            valores = {b["codigo_op"]: b for b in projecao.bullets}
            print(f"\n   NOVAS HOJE ({len(novas)}) -- confira se sao bullet:")
            for codigo in novas[:args.limite]:
                b = valores.get(codigo, {})
                print(f"      {codigo:<12} {b.get('valor', 0):>16,.2f}  "
                      f"venc {b.get('dt_vencimento')}  ({b.get('qt_dias')} dias)")
            print("      Confirme:  python -m liquidez bullet confirmar --op <codigo>")
            print("      Tem fluxo: python -m liquidez bullet planilha-pendente --op <codigo>")

        # Quem ja foi visto nao e "novo", mas continua pendente ate alguem
        # decidir. Com a fila curta (apos as familias sem conferencia) a lista
        # nominal cabe na tela e e o que o gestor precisa para responder.
        pendentes = por_estado.get(bl.PENDENTE, []) + por_estado.get(bl.ESTOQUE, [])
        pendentes = [b for b in pendentes if b["codigo_op"] not in set(novas)]
        if pendentes and (args.detalhar or len(pendentes) <= args.limite):
            pendentes.sort(key=lambda b: -abs(b["valor"]))
            print(f"\n   A CONFERIR ({len(pendentes)}) -- ja vistas, sem decisao:")
            for b in pendentes[:args.limite]:
                print(f"      {b['codigo_op']:<12} {b.get('codigo_ativo', ''):<8} {b['valor']:>16,.2f}  "
                      f"venc {b.get('dt_vencimento')}  ({b.get('qt_dias')} dias)  [{b['estado']}]")
            if len(pendentes) > args.limite:
                print(f"      ... mais {len(pendentes) - args.limite} (use --limite)")

    if projecao.bullet_impossivel:
        print(f"\nAVISO  {len(projecao.bullet_impossivel)} operacao(oes) fora da premissa "
              f"bullet -- ficaram sem fluxo:")
        for b in projecao.bullet_impossivel[:args.limite]:
            print(f"   {b['codigo_op']:<12} {b['valor']:>18,.2f}  {b['motivo']}")

    if projecao.vencimento_divergente:
        print(f"\nDIVERGENCIA  {len(projecao.vencimento_divergente)} operacao(oes) com "
              f"vencimento diferente entre cadastro e carteira.")
        print("             A projecao seguiu pelo CADASTRO. Confira qual esta certo: "
              "leitura do documento, aditamento, ou a data do administrador.")
        for d in projecao.vencimento_divergente[:args.limite]:
            print(f"   {d['codigo_op']:<12} {d['valor']:>18,.2f}  "
                  f"cadastro {d['dt_cadastro']:%d/%m/%Y}  "
                  f"carteira {d['dt_carteira']:%d/%m/%Y}  ({d['dias']:+d} dias)")

    if projecao.sem_cronograma:
        total = sum(valor for _, valor in projecao.sem_cronograma)
        print(f"\nOPERACAO NOVA  {len(projecao.sem_cronograma)} Codigo_Op na carteira sem aba "
              f"na planilha (R$ {total:,.2f}) -- ficaram fora da projecao.")
        print("               Reimporte o cronograma: "
              "python -m liquidez cronograma importar --fundo ...")
        # O Codigo_Ativo vai junto: e por ele que se descobre familia que
        # deveria entrar na premissa bullet e ainda nao entra.
        for codigo, valor in projecao.sem_cronograma[:args.limite]:
            familia = projecao.familia_sem_cronograma.get(codigo, "")
            print(f"   {codigo:<12} {valor:>18,.2f}  {familia}")
    if projecao.sem_parcela_futura:
        total = sum(valor for _, valor in projecao.sem_parcela_futura)
        print(f"\nAVISO  {len(projecao.sem_parcela_futura)} operacao(oes) sem parcela futura "
              f"(R$ {total:,.2f}) -- ficaram fora da projecao:")
        for codigo, valor in projecao.sem_parcela_futura[:args.limite]:
            print(f"   {codigo:<12} {valor:>18,.2f}")
    if projecao.alem_da_curva:
        print(f"\nAVISO  {len(projecao.alem_da_curva)} operacao(oes) com prazo alem do "
              f"ultimo vertice da curva; o fator do vertice final foi mantido:")
        for codigo, dias in projecao.alem_da_curva[:args.limite]:
            print(f"   {codigo:<12} {dias:>6} dias")

    if args.detalhar:
        print("\nResumo por operacao:")
        print(projecao.resumo.to_string(index=False))

    if args.salvar:
        sufixo = "_nao_vencidas" if args.somente_nao_vencidos else ""
        destino = Path(args.destino) / (
            f"fluxo_{fundo.cnpj_numerico}_{data_base:%Y%m%d}{sufixo}.csv"
        )
        # CSV aberto no Excel trava a gravacao. Isso nao pode derrubar o fundo
        # nem o lote -- a projecao ja esta pronta e a camada de fatos, que e o
        # que alimenta o BI, nao depende deste arquivo.
        try:
            fx.gravar(projecao, destino)
            print(f"\nGravado: {destino.resolve()}")
        except OSError as exc:
            print(f"\nAVISO  nao foi possivel gravar {destino.name}: {exc.strerror or exc}. "
                  f"O arquivo esta aberto? A projecao seguiu para a camada de fatos.")

    if args.fatos and not args.somente_nao_vencidos:
        from . import fatos as ft

        # gravar_tabela preserva os demais fundos da mesma data.
        caminho = ft.gravar_tabela(
            ft.de_fluxo(fluxo, data_base), "fato_fluxo", data_base, Path(args.destino_bi)
        )
        print(f"Fatos:   {caminho}")

    aviso = cr.planilha_mudou(fundo.cnpj)
    if aviso:
        print(f"\nAVISO  {aviso}")

    valores = {b["codigo_op"]: b["valor"] for b in projecao.bullets}
    return {
        "novas": [(fundo.nome_interno, c, valores.get(c, 0.0)) for c in novas],
        "sem_cronograma": projecao.sem_cronograma,
        "total": float(fluxo["vl_parcela"].sum()),
    }


def cmd_fatos(args) -> int:
    """Popula a camada de fatos do BI a partir do pipeline ja existente.

    Nao recalcula nada por fora: usa as mesmas etapas 6, 8, 9 e indicadores que
    alimentam o .xlsx. Se a regra mudar no Python, o fato muda junto.
    """
    import pandas as pd

    from . import cronograma as cr
    from . import fatos as ft

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    saida = Path(args.saida)
    destino = Path(args.destino)

    if args.fundo:
        fundo = cadastro.obter(args.fundo)
        if fundo is None:
            raise SystemExit(f"Fundo nao encontrado: {args.fundo}")
        fundos = [fundo]
    else:
        fundos = cadastro.filtrar(adm=args.adm, tipo=args.tipo,
                                  incluir_inativos=args.incluir_inativos)
        if not fundos:
            raise SystemExit("Nenhum fundo selecionado.")

    coletado: dict[str, list[pd.DataFrame]] = {t: [] for t in ft.TABELAS_POR_DATA}
    data_posicao = None
    processados, pulados, erros = 0, [], []

    for fundo in fundos:
        try:
            dados = _processar_fundo(cadastro, fundo, saida)
        except SemPontaAtiva:
            pulados.append(fundo.nome_interno)
            continue
        except Exception as exc:  # noqa: BLE001
            erros.append((fundo.nome_interno, str(exc)))
            log.error("%s: %s", fundo.nome_interno, exc)
            continue

        data_posicao = data_posicao or dados["data_posicao"]
        df_ind, df_anbima = ft.de_indicadores(dados["indicadores"], fundo, dados["data_posicao"])
        coletado["fato_indicadores"].append(df_ind)
        coletado["fato_anbima"].append(df_anbima)
        coletado["fato_relatorio"].append(
            ft.de_relatorio(dados["relatorio"], fundo, dados["data_posicao"])
        )
        coletado["fato_matriz"].append(
            ft.de_matriz(dados["matriz"], fundo, dados["data_posicao"], "carteira")
        )
        coletado["fato_matriz"].append(
            ft.de_matriz(dados["estoque"], fundo, dados["data_posicao"], "estoque")
        )
        processados += 1
        if args.verboso:
            print(f"OK   {fundo.nome_interno}")

    if not processados:
        raise SystemExit("Nenhum fundo processado; nada a gravar.")

    print(f"Data-base .... {data_posicao}")
    print(f"Processados .. {processados} fundo(s)"
          + (f", {len(pulados)} sem ponta ativa" if pulados else "")
          + (f", {len(erros)} com erro" if erros else ""))
    print()

    for tabela, partes in coletado.items():
        partes = [p for p in partes if p is not None and not p.empty]
        if not partes:
            continue
        df = pd.concat(partes, ignore_index=True)
        caminho = ft.gravar_tabela(df, tabela, data_posicao, destino)
        print(f"{tabela:<20} {len(df):>8,} linha(s)  ->  {caminho}")

    # Dimensoes: retrato do agora, sobrescritas a cada rodada.
    caminho = ft.gravar_dimensao(ft.dim_fundo(cadastro.fundos.values()), "dim_fundo", destino)
    print(f"{'dim_fundo':<20} {len(cadastro.fundos):>8,} linha(s)  ->  {caminho}")

    cronogramas = {}
    for numerico in cr.fundos_com_cadastro():
        fundo = cadastro.fundos.get(numerico)
        if fundo is None:
            continue
        crono = cr.Cronograma.carregar_se_houver(fundo.cnpj)
        if crono is not None:
            cronogramas[numerico] = crono
    if cronogramas:
        df_op = ft.dim_operacao(cronogramas)
        caminho = ft.gravar_dimensao(df_op, "dim_operacao", destino)
        print(f"{'dim_operacao':<20} {len(df_op):>8,} linha(s)  ->  {caminho}")

    if erros:
        print(f"\n{len(erros)} com erro:")
        for nome, mensagem in erros[:10]:
            print(f"   {nome[:34]:<34} {str(mensagem)[:50]}")

    print(f"\nCamada de fatos em {destino.resolve()}")
    return 1 if erros else 0


def _operacoes_bullet_vivas(args) -> list[dict]:
    """As operacoes DC dos fundos no escopo, com o que identifica cada uma."""
    import pandas as pd

    from . import bullets as bl
    from . import fluxo as fx
    from .cronograma import normalizar_codigo
    from .extrator import carregar_base
    from .prazos import TabelaPrazos
    from .tratamentos import tratar_fundo

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    saida = Path(args.saida)
    prazos = TabelaPrazos.carregar()
    achadas = []
    for fundo in bl.fundos_no_escopo(cadastro.fundos.values()):
        carteira = carregar_base(saida, "carteira", fundo.cnpj)
        if carteira.empty:
            continue
        bd = carregar_base(saida, "bd", fundo.cnpj)
        resultado = tratar_fundo(fundo, carteira, bd, prazos)
        posicoes = fx.posicoes_do_produto(
            resultado.carteira, bl.PRODUTO_BULLET,
            regra_vencidos=resultado.regra_vencidos,
        )
        if posicoes.empty:
            continue
        # O codigo do ativo nao vem em `posicoes` -- vem da carteira, e e o que
        # permite tratar familia inteira de papel de uma vez.
        ativos = (
            resultado.carteira.assign(
                _c=resultado.carteira["Codigo_Op"].map(normalizar_codigo))
            .dropna(subset=["_c"])
            .groupby("_c")["Codigo_Ativo"].first()
        )
        for _, p in posicoes.iterrows():
            codigo = str(p["codigo_op"])
            achadas.append({
                "codigo_op": codigo,
                "cnpj_fundo": fundo.cnpj,
                "nome_fundo": fundo.nome_interno,
                "codigo_ativo": str(ativos.get(codigo, "") or ""),
                "valor": float(p["valor_base"] or 0.0),
                "dt_vencimento": p.get("dt_vencimento"),
            })
    return achadas


def cmd_bullet_marco(args) -> int:
    """Fotografa o estoque inicial. Roda uma vez."""
    from . import bullets as bl

    vivas = _operacoes_bullet_vivas(args)
    if not vivas:
        print(f"Nenhuma operacao {bl.PRODUTO_BULLET} encontrada na carga em "
              f"{Path(args.saida).resolve()}.")
        return 1
    try:
        registro = bl.gravar_marco(
            [(o["codigo_op"], o["cnpj_fundo"]) for o in vivas],
            args.data, refazer=args.refazer,
        )
    except ValueError as exc:
        raise SystemExit(str(exc))

    fundos = {o["nome_fundo"] for o in vivas}
    print(f"Marco zero gravado em {registro['marco_zero']}.")
    print(f"{len(registro['operacoes'])} operacao(oes) de {len(fundos)} fundo(s) "
          f"entraram como estoque conhecido.")
    print(f"Registro: {bl.ARQUIVO.resolve()}")
    print("\nA partir da proxima data, o que aparecer sera sinalizado como novo.")
    return 0


def cmd_bullet_listar(args) -> int:
    from . import bullets as bl

    registro = bl.carregar()
    operacoes = registro["operacoes"]
    if not operacoes:
        print("Registro vazio. Grave o marco zero: python -m liquidez bullet marco --data ...")
        return 0

    print(f"Marco zero: {registro['marco_zero'] or '(nao gravado)'}")
    contagem: dict[str, int] = {}
    for dados in operacoes.values():
        estado = dados.get("estado", bl.PENDENTE)
        contagem[estado] = contagem.get(estado, 0) + 1
    print(f"{len(operacoes)} operacao(oes) no registro:")
    for estado, n in sorted(contagem.items()):
        print(f"   {n:>5}  {estado}")

    filtro = args.estado
    linhas = [(c, d) for c, d in sorted(operacoes.items())
              if not filtro or d.get("estado") == filtro]
    if filtro and not linhas:
        print(f"\nNenhuma no estado '{filtro}'.")
        return 0
    print(f"\n{'CODIGO':<12} {'ESTADO':<20} {'VISTO EM':<12} {'CONFERIDO':<12} NOTA")
    for codigo, d in linhas[:args.limite]:
        print(f"{codigo:<12} {d.get('estado',''):<20} {str(d.get('visto_em') or ''):<12} "
              f"{str(d.get('conferido_em') or ''):<12} {str(d.get('nota') or '')[:36]}")
    if len(linhas) > args.limite:
        print(f"... mais {len(linhas) - args.limite} (use --limite)")
    return 0


def cmd_bullet_confirmar(args) -> int:
    from . import bullets as bl

    if not args.op and not args.ativo:
        raise SystemExit("Informe --op <codigo> ou --ativo <prefixo>.")

    if args.op:
        try:
            registro = bl.confirmar(args.op, args.nota)
        except ValueError as exc:
            raise SystemExit(str(exc))
        codigo = next(iter(registro))
        print(f"{codigo}: confirmada como bullet. Sai da fila e segue projetada igual.")
        return 0

    # Lote por familia de papel: o codigo do ativo carrega o tipo do papel, e
    # tipo e propriedade do instrumento, nao do lancamento.
    prefixo = args.ativo.strip().upper()
    vivas = _operacoes_bullet_vivas(args)
    alvo = [o for o in vivas if o["codigo_ativo"].upper().startswith(prefixo)]
    if not alvo:
        print(f"Nenhuma operacao com Codigo_Ativo comecando em '{prefixo}' "
              f"na carga em {Path(args.saida).resolve()}.")
        return 1

    estados = bl.estados_de([o["codigo_op"] for o in alvo])
    pendentes = [o for o in alvo
                 if estados.get(o["codigo_op"]) in bl.NAO_CONFERIDOS]
    total = sum(o["valor"] for o in alvo)
    fundos = {o["nome_fundo"] for o in alvo}
    print(f"'{prefixo}': {len(alvo)} operacao(oes) em {len(fundos)} fundo(s), "
          f"R$ {total:,.2f}")
    print(f"   ja conferidas ...... {len(alvo) - len(pendentes)}")
    print(f"   a confirmar agora .. {len(pendentes)}")
    if not pendentes:
        print("\nNada a fazer.")
        return 0

    nota = args.nota or f"familia {prefixo}: bullet por construcao"
    mexidos = bl.confirmar_varias([o["codigo_op"] for o in pendentes], nota)
    soma = sum(o["valor"] for o in pendentes)
    print(f"\n{len(mexidos)} confirmada(s), R$ {soma:,.2f}. Saem da fila e "
          f"seguem projetadas igual.")
    for nome in sorted(fundos):
        n = sum(1 for o in pendentes if o["nome_fundo"] == nome)
        if n:
            print(f"   {nome[:36]:<38} {n:>4}")
    return 0


def cmd_bullet_planilha_pendente(args) -> int:
    from . import bullets as bl

    try:
        registro = bl.marcar_planilha_pendente(args.op, args.nota)
    except ValueError as exc:
        raise SystemExit(str(exc))
    codigo = next(iter(registro))
    print(f"{codigo}: marcada como TEM fluxo, planilha pendente.")
    print("Segue projetada como bullet -- e sinalizada como incorreta -- ate a "
          "planilha chegar.")
    print("Quando chegar: python -m liquidez cronograma importar --fundo ... --planilha ...")
    return 0


def cmd_bullet_remover(args) -> int:
    from . import bullets as bl

    if bl.remover(args.op):
        print(f"{args.op}: registro removido; volta a ser tratada como nunca vista.")
        return 0
    print(f"{args.op}: nao estava no registro.")
    return 1


def cmd_bullet_limpar(args) -> int:
    from . import bullets as bl

    vivas = _operacoes_bullet_vivas(args)
    mortos = bl.limpar({o["codigo_op"] for o in vivas})
    if not mortos:
        print("Nada a limpar: todo o registro esta na carteira atual.")
        return 0
    print(f"{len(mortos)} operacao(oes) removida(s) do registro por nao estarem "
          f"mais na carteira:")
    for codigo in mortos[:args.limite]:
        print(f"   {codigo}")
    if len(mortos) > args.limite:
        print(f"   ... mais {len(mortos) - args.limite}")
    return 0


def cmd_cotas_sincronizar(args) -> int:
    """Puxa o de-para ISIN -> CNPJ do cadastro de cotas do riscogc.

    Passa o cadastro de fundos junto: onde ha ficha, o prazo gravado e o
    `pz_resg` dela -- levantado regulamento por regulamento -- e nao o que o
    riscogc deduz do texto de liquidacao.
    """
    from . import cotas as ct

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    registros, relatorio = ct.baixar(cadastro)
    if not registros:
        print("Nenhuma cota com chave utilizavel no cadastro. Nada gravado.")
        return 1

    caminho = ct.MapaCotas(registros).gravar(Path(args.saida))
    print(f"Lidos ................. {relatorio['lidos']} linha(s) do cadastro")
    print(f"Chaves ISIN ........... {relatorio['isin']}")
    print(f"Chaves codigo do adm .. {relatorio['codigo_adm']}")
    print(f"Prazo vindo da ficha .. {relatorio['da_ficha']} (riscogc divergia)")
    print(f"Gravados .............. {relatorio['gravados']}")
    print(f"Arquivo ............... {caminho.resolve()}")

    com = sum(1 for r in registros if r.prazo is not None)
    print(f"\nCom prazo no cadastro . {com} de {len(registros)}")
    print(f"Sem prazo ............. {len(registros) - com} "
          f"(nem LiquidacaoResgate nem Condominio)")
    return 0


def cmd_cotas_serie(args) -> int:
    """Baixa a serie de cotas da MEC (datalake) e grava na camada de fatos."""
    import datetime as _dt

    from . import fatos as ft
    from . import varcota as vc

    ate = args.ate or _dt.date.today().isoformat()
    destino = Path(args.destino) if args.destino else ft.DIR_BI
    dias = args.dias or vc.JANELA_DIAS

    serie = vc.extrair(ate, dias)
    if serie.empty:
        print("Nenhuma serie de cota na janela. Nada gravado.")
        return 1

    caminho = vc.gravar(serie, destino)
    cdi = vc.extrair_cdi(ate, dias)
    caminho_cdi = vc.gravar_cdi(cdi, destino) if not cdi.empty else None
    met = vc.metricas(serie, cdi)

    print(f"Janela ................ {dias} dia(s) ate {ate}")
    print(f"Linhas ................ {len(serie):,}".replace(",", "."))
    print(f"Fundos ................ {serie['cnpj_fundo'].nunique()}")
    print(f"Series (fundo x classe) {serie.groupby(['cnpj_fundo','classe']).ngroups}")
    print(f"Arquivo ............... {caminho.resolve()}")
    if caminho_cdi is not None:
        print(f"CDI ................... {len(cdi)} dia(s) util(eis), "
              f"{cdi['cdi_aa'].iloc[-1]:.2f}% a.a. na ponta")
        print(f"Arquivo CDI ........... {caminho_cdi.resolve()}")
    else:
        print("CDI ................... AUSENTE -- Sharpe nao sera calculado")

    if met.empty:
        print(f"\nNenhuma serie com {vc.MIN_OBS}+ retornos: sem VaR a calcular.")
        return 0

    print(f"\nCom {vc.MIN_OBS}+ retornos .... {len(met)} serie(s)")
    print(f"Quebras descartadas ... {int(met['quebras'].sum())} "
          f"(|retorno| > {vc.CORTE_QUEBRA:.0%})")
    print(f"Series accrual ........ {int(met['accrual'].sum())} "
          f"(cota nao marcada a mercado)")
    coerentes = int((met["razao_hist"].between(1 / vc.BANDA_RAZAO, vc.BANDA_RAZAO)).sum())
    print(f"Parametrico coerente .. {coerentes} de {len(met)} "
          f"(historico dentro de {vc.BANDA_RAZAO:g}x)")
    subestima = int((met["razao_hist"] > vc.BANDA_RAZAO).sum())
    if subestima:
        print(f"  ATENCAO: em {subestima} serie(s) o historico SUPERA o "
              f"parametrico -- cauda pesada, o VaR subestima a perda.")

    # Senior e mezanino sao remuneradas a CDI + taxa pre: a cota indexada delas
    # sobe todo dia util. Queda ali nao e oscilacao -- e PDD comendo a
    # remuneracao, erro de cadastro, ou a serie patrimonial no lugar da
    # indexada. Sempre listar, nunca resumir a um numero.
    neg = vc.retornos_negativos(serie)
    print(f"\nRetorno negativo em senior/mezanino ... "
          f"{len(neg)} dia(s)" + ("" if len(neg) else "  (nenhum, como esperado)"))
    if not neg.empty:
        nomes = Cadastro(Path(args.diretorio)).carregar().fundos
        print("  ATENCAO: classe remunerada por contrato nao deveria cair.")
        por_serie = neg.groupby(["cnpj_fundo", "classe"]).agg(
            dias=("data", "size"), pior=("retorno", "min"),
            fonte=("fonte_cota", "first"))
        for (cnpj, classe), linha in por_serie.sort_values("pior").iterrows():
            fundo = nomes.get(cnpj)
            nome = (fundo.nome_interno if fundo else cnpj)[:30]
            print(f"   {nome:<30} {classe:<14} {int(linha['dias']):>3} dia(s)  "
                  f"pior {linha['pior']:>8.2%}  cota {linha['fonte']}")

    com_sharpe = met["sharpe"].notna()
    if com_sharpe.any():
        crivel = met[com_sharpe & (met["sharpe"].abs() <= vc.SHARPE_IMPLAUSIVEL)
                     & ~met["accrual"]]
        print(f"\nSharpe calculado ...... {int(com_sharpe.sum())} serie(s)")
        print(f"  Interpretavel ....... {len(crivel)} "
              f"(|Sharpe| <= {vc.SHARPE_IMPLAUSIVEL:g} e cota marcada)")
        print(f"  Inflado pela cota ... {int(com_sharpe.sum()) - len(crivel)} "
              f"(denominador perto de zero)")
        abaixo = int((met["sharpe"] < 0).sum())
        print(f"  Abaixo do CDI ....... {abaixo} serie(s)")
    return 0


def cmd_cotas_classificar(args) -> int:
    """Para cada cota de outro fundo: aberto (prazo) ou fechado (cronograma)?"""
    from . import classificar_cotas as cc
    from . import cvm
    from .analise import outros_fundos
    from .extrator import carregar_base
    from .prazos import TabelaPrazos
    from .tratamentos import tratar_fundo

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    fundo = cadastro.obter(args.fundo)
    if fundo is None:
        raise SystemExit(f"Fundo nao encontrado: {args.fundo}")
    saida = Path(args.saida)
    carteira = carregar_base(saida, "carteira", fundo.cnpj)
    if carteira.empty:
        raise SystemExit(f"Sem carteira de {fundo.nome_interno} em {saida.resolve()}.")
    bd = carregar_base(saida, "bd", fundo.cnpj)
    res = tratar_fundo(fundo, carteira, bd, TabelaPrazos.carregar())
    tabela_of = outros_fundos(res.carteira, fundo, res.regra)
    if tabela_of.empty:
        print("Nenhuma cota de outro fundo na carteira.")
        return 0
    # Uma linha por cota: a carteira traz uma por aplicacao.
    agrupado = (tabela_of.groupby(["cod_fundo", "fundo"], as_index=False)["exposicao"].sum()
                .sort_values("exposicao", ascending=False))
    cotas = [(str(r["cod_fundo"]), str(r["fundo"]), float(r["exposicao"])) for _, r in agrupado.iterrows()]

    print(f"Baixando o registro da CVM (cache em {DIR_CACHE}) ...")
    classes = cc.ler_classes_cvm(cvm.baixar(DIR_CACHE, forcar=args.forcar_cvm))
    linhas = cc.classificar(cotas, classes, top=args.top)

    print(f"\n{fundo.nome_interno}: {len(linhas)} cota(s) de outros fundos\n")
    for l in linhas:
        print(f"{l.codigo_ativo:<10} {l.nome_carteira:<24} R$ {l.valor/1e6:>9,.1f} mi  -> {l.acao}")
        for c in l.candidatas:
            print(f"    {c.pontuacao:4.0%}  {c.cnpj:<20} {c.forma_condominio:<8} {c.situacao:<12} {c.denominacao[:70]}")
    if args.csv:
        print(f"\nCSV: {cc.escrever_csv(linhas, Path(args.csv)).resolve()}")
    return 0


def cmd_cotas_pendentes(args) -> int:
    """Cotas da carteira cujo prazo nao se resolve nem por mnemonico nem por ISIN."""
    from . import cotas as ct
    from .analise import outros_fundos
    from .extrator import carregar_base
    from .prazos import TabelaPrazos
    from .tratamentos import tratar_fundo

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    saida = Path(args.saida)
    tp = TabelaPrazos.carregar()
    mapa = ct.MapaCotas.carregar()
    print(f"De-para de cotas: {len(mapa)} chave(s)\n")

    resolvidas, pendentes, total = [], [], 0
    for fundo in cadastro.fundos.values():
        carteira = carregar_base(saida, "carteira", fundo.cnpj)
        if carteira.empty:
            continue
        bd = carregar_base(saida, "bd", fundo.cnpj)
        res = tratar_fundo(fundo, carteira, bd, tp)
        tabela_of = outros_fundos(res.carteira, fundo, res.regra)
        if tabela_of.empty:
            continue
        r = ct.ResolvedorCotas(tp, mapa, cadastro)
        for _, linha in tabela_of.iterrows():
            # Mesmo desvio do relatorio: "A Vencer" e "Vencidos" nao consultam a
            # tabela de prazos -- a primeira usa o estoque, a segunda e ignorada.
            # Sem reproduzir isso aqui, o diagnostico acusaria como pendente algo
            # que nunca passa pela busca.
            cod = str(linha["cod_fundo"]).strip().upper()
            nome_cota = str(linha["fundo"]).strip().upper()
            if (cod.endswith(("AV", "VEN"))
                    or nome_cota.endswith(("A VENCER", "A VENC", "VENCIDOS"))):
                continue
            total += 1
            antes_p, antes_r = len(r.pendentes), len(r.resolvidos)
            prazo = r.prazo(linha["fundo"])
            if len(r.resolvidos) > antes_r:
                d = dict(r.resolvidos[-1], fundo=fundo.nome_interno,
                         exposicao=float(linha["exposicao"]))
                resolvidas.append(d)
            elif len(r.pendentes) > antes_p:
                d = dict(r.pendentes[-1], fundo=fundo.nome_interno,
                         exposicao=float(linha["exposicao"]))
                pendentes.append(d)
            elif prazo is None:
                pendentes.append({"codigo": str(linha["fundo"]),
                                  "motivo": "sem prazo na tabela (mnemonico)",
                                  "fundo": fundo.nome_interno,
                                  "exposicao": float(linha["exposicao"])})

    print(f"cotas na carteira ............ {total}")
    print(f"resolvidas pelo cadastro ..... {len(resolvidas)}  "
          f"R$ {sum(d['exposicao'] for d in resolvidas):,.2f}")
    print(f"sem prazo .................... {len(pendentes)}")

    if resolvidas:
        print("\nRESOLVIDAS PELO CADASTRO:")
        for d in sorted(resolvidas, key=lambda x: -x["exposicao"])[:args.limite]:
            print(f"   {d['codigo']:<14} {d['nome'][:26]:<28} "
                  f"{d.get('tipo_chave','')[:11]:<12} prazo {d['prazo']:>4}  "
                  f"R$ {d['exposicao']:>16,.2f}  ({d.get('fonte','')})")

    if pendentes:
        soma = sum(d["exposicao"] for d in pendentes)
        print(f"\nSEM PRAZO ({len(pendentes)}, R$ {soma:,.2f}) -- zeram no Valor Liquido:")
        for d in pendentes[:args.limite]:
            print(f"   {d['codigo'][:22]:<24} {d['fundo'][:24]:<26} "
                  f"R$ {d['exposicao']:>16,.2f}  {d['motivo']}")
        if len(pendentes) > args.limite:
            print(f"   ... mais {len(pendentes) - args.limite} (use --limite)")
    return 2 if pendentes else 0


def cmd_painel(args) -> int:
    from .painel import gerar

    try:
        destino = gerar(Path(args.destino), Path(args.saida_html),
                        com_fundos=not args.sem_fundos)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc))
    print(f"Painel gerado: {destino.resolve()}")
    if not args.sem_fundos:
        pasta = destino.parent / "fundos"
        print(f"Relatorios por fundo: {pasta.resolve()}")
    return 0


def cmd_apresentacao(args) -> int:
    """Apresentacao para o comite (versao melhorada). O `painel` segue gerando o antigo."""
    from . import apresentacao as ap

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    nomes = [n.strip() for n in args.fundos.split(",") if n.strip()]
    fundos = []
    for nome in nomes:
        f = cadastro.obter(nome)
        if f is None:
            raise SystemExit(f"Fundo nao encontrado: {nome}")
        fundos.append(f)
    caminho = ap.gerar(fundos, destino_bi=Path(args.destino), saida_dados=Path(args.saida),
                       saida=Path(args.saida_html), data=args.data)
    print(f"Apresentacao gerada: {caminho_efetivo(caminho.resolve())}")
    return 0


def cmd_fatos_resumo(args) -> int:
    from . import fatos as ft

    resumo = ft.resumir(Path(args.destino))
    if resumo["particoes"].sum() == 0:
        print(f"Camada de fatos vazia em {Path(args.destino).resolve()}.")
        return 0
    print(resumo.to_string(index=False))
    return 0


def cmd_fluxo(args) -> int:
    """Projeta o fluxo futuro. Sem --fundo, roda todos os que tem cronograma.

    Codigo de saida: 0 tudo certo, 2 ha operacao nova sem aba na planilha,
    1 falha. O 2 e aviso, nao erro: a projecao rodou, mas ficou incompleta.
    """
    from . import bullets as bl
    from . import cronograma as cr
    from . import fluxo as fx

    cadastro = Cadastro(Path(args.diretorio)).carregar()

    if args.fundo:
        fundo = cadastro.obter(args.fundo)
        if fundo is None:
            raise SystemExit(f"Fundo nao encontrado: {args.fundo}")
        fundos = [fundo]
    else:
        # Dois motivos para um fundo entrar: ter cronograma importado, ou estar
        # no recorte da premissa bullet. Sao conjuntos diferentes -- um fundo da
        # QI Tech sem planilha nenhuma agora tem fluxo, e e o ponto da mudanca.
        com_crono = [
            cadastro.fundos[numerico]
            for numerico in cr.fundos_com_cadastro()
            if numerico in cadastro.fundos
        ]
        no_escopo = [] if args.sem_bullet else bl.fundos_no_escopo(cadastro.fundos.values())
        vistos, fundos = set(), []
        for f in list(com_crono) + list(no_escopo):
            if f.cnpj_numerico not in vistos:
                vistos.add(f.cnpj_numerico)
                fundos.append(f)
        if not fundos:
            print(f"Nenhum fundo com cronograma importado em {cr.DIR_CADASTRO},")
            print("nem fundo no escopo da premissa bullet.")
            return 0
        print(f"{len(fundos)} fundo(s): {len(com_crono)} com cronograma, "
              f"{len(no_escopo)} no escopo bullet.\n")

    novos_total: list[tuple[str, str, float]] = []
    sem_crono_total: list[tuple[str, str, float]] = []
    erros: list[tuple[str, str]] = []
    processados = 0

    for indice, fundo in enumerate(fundos):
        if indice:
            print("\n" + "-" * 70)
        try:
            saida = _projetar_um_fundo(fundo, args)
            processados += 1
            novos_total.extend(saida["novas"])
            sem_crono_total.extend(
                (fundo.nome_interno, c, v) for c, v in saida["sem_cronograma"]
            )
        except (cr.ErroPlanilha, fx.ErroProjecao, OSError, SystemExit) as exc:
            # Um fundo que falha nao pode levar o lote junto.
            erros.append((fundo.nome_interno, str(exc)))
            print(f"ERRO  {fundo.nome_interno}: {exc}")

    if len(fundos) > 1 or novos_total or sem_crono_total or erros:
        print("\n" + "=" * 70)
        print(f"{processados} fundo(s) projetado(s).")
        if novos_total:
            print(f"\nCONFERIR: {len(novos_total)} operacao(oes) bullet nova(s), "
                  f"projetadas pela premissa.")
            for nome, codigo, valor in novos_total[:args.limite]:
                print(f"   {nome[:30]:<30} {codigo:<12} {valor:>16,.2f}")
            if len(novos_total) > args.limite:
                print(f"   ... mais {len(novos_total) - args.limite} (use --limite)")
        if sem_crono_total:
            print(f"\nSEM FLUXO: {len(sem_crono_total)} Codigo_Op sem cronograma e fora "
                  f"da premissa bullet.")
            for nome, codigo, valor in sem_crono_total[:args.limite]:
                print(f"   {nome[:30]:<30} {codigo:<12} {valor:>16,.2f}")
        if erros:
            print(f"\n{len(erros)} com erro:")
            for nome, mensagem in erros:
                print(f"   {nome[:30]:<30} {str(mensagem)[:60]}")

    if erros:
        return 1
    return 2 if (novos_total or sem_crono_total) else 0


# ---------------------------------------------------------------------------
# extrair / testar-conexao
# ---------------------------------------------------------------------------

def cmd_testar_conexao(args) -> int:
    from . import db

    info = db.testar_conexao()
    for chave, valor in info.items():
        print(f"{chave:<10} {valor}")
    return 0


def cmd_documentar(args) -> int:
    from .documentacao import ARQUIVO_PADRAO, gerar_html

    destino = gerar_html(Path(args.saida_doc or ARQUIVO_PADRAO))
    print(f"Documento gerado: {destino.resolve()}")
    return 0


class SemPontaAtiva(Exception):
    """A carga nao tem carteira nem estoque do fundo -- nao da para montar o relatorio.

    Acontece com FI/FIP/FII cuja posicao nao esta nas tabelas do riscogc. Nao e
    falha de extracao: o passivo (cotistas, MOV) vem normalmente, mas sem a ponta
    ativa o Caixa Potencial seria zero e todo indicador sairia "Desenquadrado".
    """


def _fluxo_do_fundo(fundo, data_posicao, carteira_tratada, prazo_cot):
    """(fluxo projetado do fundo, valor liquido do DC que o fluxo nao cobre).

    O fluxo vem da camada de BI, ja gravado por `liquidez fluxo --fatos` -- e nao
    projetado aqui. Projetar custaria uma ida ao banco pelo CDI por fundo, e o
    relatorio roda para os 100; a dependencia de ordem (fluxo antes de relatorio)
    e o preco disso e esta dita no comando.

    O segundo valor fecha o buraco que a troca de fonte abriria: operacao de DC
    sem cronograma e que nao da bullet nao esta no fluxo, e se o Valor Liquido
    passasse a sair SO do fluxo ela desapareceria. Em 31/08/2026 isso era zero em
    todos os fundos (as sem cronograma estavam todas vencidas), mas zero medido e
    diferente de zero presumido -- por isso a conta existe e avisa quando aparece.
    """
    import pandas as pd

    from . import fatos as ft
    from .analise import COL_PRODUTO, PRODUTO_DO_FLUXO
    from .cronograma import normalizar_codigo
    from .tratamentos import COL_ATIVO_VENCIDO, COL_QTDE_DIAS

    try:
        caminho = ft.caminho_particao("fato_fluxo", data_posicao, ft.DIR_BI)
        todos = pd.read_parquet(caminho)
    except (FileNotFoundError, OSError):
        return None, 0.0

    alvo = so_digitos(fundo.cnpj)
    fluxo = todos[todos["cnpj_fundo"].map(so_digitos) == alvo]
    if fluxo.empty:
        return None, 0.0

    dc = carteira_tratada[
        carteira_tratada[COL_PRODUTO].fillna("").astype(str).str.strip().str.upper()
        == PRODUTO_DO_FLUXO
    ]
    if dc.empty:
        return fluxo, 0.0

    vencida = dc[COL_ATIVO_VENCIDO].fillna("").astype(str).str.strip().str.upper() == "SIM"
    dias = pd.to_numeric(dc[COL_QTDE_DIAS], errors="coerce")
    no_fluxo = dc["Codigo_Op"].map(normalizar_codigo).astype(str).isin(
        fluxo["codigo_op"].astype(str))
    sobra = dc[~no_fluxo & ~vencida & dias.notna() & (dias <= prazo_cot)]
    resto = float(pd.to_numeric(sobra["Valor_Liquido"], errors="coerce").fillna(0.0).sum())
    if resto:
        log.warning(
            "%s: R$ %.2f de %s dentro da janela sem fluxo projetado (%d linha(s)) "
            "-- contado pela carteira. Falta cronograma?",
            fundo.nome_interno, resto, PRODUTO_DO_FLUXO, len(sobra))
    return fluxo, resto


def _processar_fundo(cadastro, fundo, saida, data_posicao=None):
    """Roda o pipeline completo de um fundo e devolve tudo o que o relatorio usa."""
    from .analise import analisar_carteira, exposicao_estoque, montar_relatorio, outros_fundos
    from .cotas import MapaCotas, ResolvedorCotas
    from .extrator import carregar_base
    from .indicadores import calcular
    from .prazos import TabelaPrazos
    from .tratamentos import tratar_fundo

    carteira = carregar_base(saida, "carteira", fundo.cnpj)
    bd = carregar_base(saida, "bd", fundo.cnpj)
    if carteira.empty and bd.empty:
        raise SemPontaAtiva("sem carteira e sem estoque na carga")
    dbp = carregar_base(saida, "db_passivo", fundo.cnpj)
    mov = carregar_base(saida, "mov", fundo.cnpj)

    tp = TabelaPrazos.carregar()
    res = tratar_fundo(fundo, carteira, bd, tp)
    prazo_cot = fundo.cc
    if prazo_cot is None:
        raise ValueError(f"{fundo.nome_interno}: sem cc (prazo de cotizacao) no cadastro.")

    # A data sobe para ca porque o fluxo e particionado por ela -- antes ela era
    # resolvida depois de `montar_relatorio`, que agora precisa do fluxo.
    if data_posicao is None:
        from .tratamentos import _data_da_carga
        data_posicao = _data_da_carga(carteira, bd)

    matriz = analisar_carteira(res.carteira, prazo_cot, res.regra)
    tabela_of = outros_fundos(res.carteira, fundo, res.regra)
    # A carteira entra para o estoque saber o que e DC proprio do fundo.
    tabela_est = exposicao_estoque(res.bd, prazo_cot, res.carteira)
    # O resolvedor so alcanca o laco de cotas; os ativos seguem pelo mnemonico.
    resolvedor = ResolvedorCotas(tp, MapaCotas.carregar(), cadastro)
    fluxo, resto = _fluxo_do_fundo(fundo, data_posicao, res.carteira, prazo_cot)
    relatorio, pl_mil = montar_relatorio(matriz, tabela_of, tabela_est, fundo,
                                         prazo_cot, tp, resolvedor,
                                         fluxo=fluxo, liquido_fora_do_fluxo=resto)
    indicadores = calcular(fundo, relatorio, pl_mil, dbp, mov, prazo_cot)

    return {
        "tratamento": res, "matriz": matriz, "outros_fundos": tabela_of,
        "estoque": tabela_est, "relatorio": relatorio, "pl_mil": pl_mil,
        "indicadores": indicadores, "prazo_cotizacao": prazo_cot,
        "data_posicao": data_posicao,
    }


def cmd_relatorio(args) -> int:
    from .relatorio_excel import gerar_excel

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    saida = Path(args.saida)
    destino_dir = Path(args.saida_rel)

    if args.fundo:
        fundos = [cadastro.obter(args.fundo)]
        if fundos[0] is None:
            raise SystemExit(f"Fundo nao encontrado: {args.fundo}")
    else:
        fundos = cadastro.filtrar(adm=args.adm, tipo=args.tipo, incluir_inativos=args.incluir_inativos)
        if not fundos:
            raise SystemExit("Nenhum fundo selecionado.")

    gerados, pulados, erros = [], [], []
    for fundo in fundos:
        try:
            dados = _processar_fundo(cadastro, fundo, saida)
            data_rotulo = str(dados["data_posicao"]).replace("-", "")
            arquivo = destino_dir / f"Liquidez_{fundo.cnpj_numerico}_{data_rotulo}.xlsx"
            gerar_excel(fundo, dados["data_posicao"], dados["relatorio"], dados["pl_mil"],
                       dados["indicadores"], arquivo)
            gerados.append(arquivo)
            print(f"OK    {fundo.nome_interno:<38} -> {arquivo.name}")
        except SemPontaAtiva as exc:
            pulados.append((fundo.nome_interno, str(exc)))
            print(f"PULA  {fundo.nome_interno:<38} {exc}")
        except Exception as exc:  # noqa: BLE001
            log.error("%s: %s", fundo.nome_interno, exc)
            erros.append((fundo.nome_interno, str(exc)))
            print(f"ERRO  {fundo.nome_interno:<38} {str(exc)[:60]}")

    print(f"\n{len(gerados)} relatorio(s) gerado(s) em {destino_dir.resolve()}")
    if pulados:
        print(f"{len(pulados)} pulado(s) por falta de carteira/estoque na carga.")
    if erros:
        print(f"{len(erros)} com erro.")
    return 1 if erros else 0


def cmd_regulamentos(args) -> int:
    cadastro = Cadastro(Path(args.diretorio)).carregar()

    if args.fundo:
        fundo = cadastro.obter(args.fundo)
        if fundo is None:
            raise SystemExit(f"Fundo nao encontrado: {args.fundo}")
        fundos = [fundo]
    else:
        fundos = cadastro.filtrar(adm=args.adm, tipo=args.tipo,
                                  incluir_inativos=args.incluir_inativos)
        if not fundos:
            raise SystemExit("Nenhum fundo selecionado.")

    destino = Path(args.destino)
    avisar_saida(destino)
    print(f"Baixando regulamentos de {len(fundos)} fundo(s)...\n")

    resultados = reg_mod.baixar_todos(
        fundos, destino, cache=DIR_CACHE, usar_fnet=not args.sem_fnet,
        historico=args.historico, refazer=args.refazer)

    por_situacao: dict[str, list] = {}
    for r in resultados:
        por_situacao.setdefault(r.situacao, []).append(r)

    print(f"\nbaixado         {len(por_situacao.get('baixado', [])):>4}")
    print(f"ja tinha        {len(por_situacao.get('ja_tinha', [])):>4}")
    print(f"sem regulamento {len(por_situacao.get('sem_regulamento', [])):>4}")
    print(f"erro            {len(por_situacao.get('erro', [])):>4}")

    ambiguos = [r for r in resultados if r.ambiguo]
    if ambiguos:
        print(f"\n{len(ambiguos)} fundo(s) com data de instrumento e de protocolo em "
              f"ordens diferentes -- os dois candidatos foram gravados:")
        for r in sorted(ambiguos, key=lambda x: x.fundo):
            print(f"   {r.fundo:<44} {', '.join(r.arquivos)}")

    for rotulo, chave in (("Sem regulamento publicado", "sem_regulamento"), ("Com erro", "erro")):
        itens = por_situacao.get(chave) or []
        if not itens:
            continue
        print(f"\n{rotulo}:")
        for r in sorted(itens, key=lambda x: x.fundo):
            print(f"   {r.fundo:<44} {r.cnpj}"
                  + (f"  {str(r.detalhe)[:60]}" if r.detalhe else ""))

    print(f"\nPastas em {caminho_efetivo(destino).resolve()}")
    print(f"Indice: {reg_mod.ARQUIVO_INDICE}")
    return 1 if por_situacao.get("erro") else 0


def cmd_tratar(args) -> int:
    from .extrator import carregar_base
    from .prazos import TabelaPrazos
    from .tratamentos import (
        COL_ATIVO_VENCIDO,
        COL_ATIVOS_EXCLUIDOS,
        COL_QTDE_DIAS,
        COL_VENCIDOS_BD,
        tratar_fundo,
    )

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    fundo = cadastro.obter(args.fundo)
    if fundo is None:
        raise SystemExit(f"Fundo nao encontrado: {args.fundo}")

    saida = Path(args.saida)
    carteira = carregar_base(saida, "carteira", fundo.cnpj)
    bd = carregar_base(saida, "bd", fundo.cnpj)

    resultado = tratar_fundo(fundo, carteira, bd, TabelaPrazos.carregar())
    r = resultado.resumo()

    print(f"\nFundo ............ {resultado.fundo} ({resultado.cnpj})")
    print(f"Administrador .... {resultado.administrador}")
    print(f"Regra ............ {resultado.regra.chave}"
          f"{'' if resultado.regra.implementado else '  [SEM TRATAMENTO DEFINIDO]'}")
    print("Vencidos ......... " + (
        " E ".join(resultado.regra_vencidos.descrever())
        if resultado.regra_vencidos.definida else "[SEM REGRA DEFINIDA]"
    ))
    print(f"\nCarteira ......... {r['carteira_linhas']} linhas")
    print(f"   prazo definido    {r['carteira_prazo_da_tabela']}")
    print(f"   Ativo Vencido=SIM {r['carteira_ativo_vencido_sim']}")
    print(f"   Excluidos=SIM     {r['carteira_excluidos_sim']}")
    print(f"BD ............... {r['bd_linhas']} linhas")
    print(f"   Vencidos=SIM      {r['bd_vencidos_sim']}")
    from .analise import (
        analisar_carteira,
        exposicao_estoque,
        montar_relatorio,
        outros_fundos,
        resumo_analise,
    )
    from .prazos import TabelaPrazos

    matriz = tabela_of = tabela_est = relatorio = None
    # O corte das faixas e do Caixa Potencial usa o prazo de cotizacao (cc),
    # nao o PzResg -- decisao de projeto confirmada com a area de risco.
    prazo_cot = fundo.cc
    if prazo_cot is None:
        log.warning("Fundo sem cc (prazo de cotizacao) no cadastro: etapas 6-9 puladas.")
    else:
        matriz = analisar_carteira(resultado.carteira, prazo_cot, resultado.regra)
        tabela_of = outros_fundos(resultado.carteira, fundo, resultado.regra)
        tabela_est = exposicao_estoque(resultado.bd, prazo_cot, resultado.carteira)
        relatorio, pl_mil = montar_relatorio(
            matriz, tabela_of, tabela_est, fundo, prazo_cot, TabelaPrazos.carregar()
        )
        ra = resumo_analise(matriz)
        print(f"\nAnalise da carteira (prazo de cotizacao {prazo_cot} dias):")
        print(f"   exposicao total .. R$ {ra['exposicao_total']:,.2f}")
        for prod, valor in ra["por_produto"].items():
            print(f"   {prod:<24} R$ {valor:,.2f}")

        print(f"\nOutros fundos (etapa 7): {len(tabela_of)} posicao(oes)")
        for _, linha in tabela_of.iterrows():
            print(f"   {linha['cod_fundo']:<10} {linha['fundo'][:26]:<26} R$ {linha['exposicao']:,.2f}")

        print("\nExposicao do estoque (etapa 8) - totais:")
        for col in tabela_est.columns:
            valor = tabela_est.loc["Exposição", col]
            print(f"   {col:<24} R$ {float(valor or 0):,.2f}")

        print(f"\nRelatorio de liquidez (etapa 9) - PL R$ {pl_mil:,.2f} mil:")
        print(f"   {'CODIGO ATIVO':<24} {'EXPOSICAO':>16} {'DUR':>4} {'VALOR LIQUIDO':>16}")
        for _, linha in relatorio.iterrows():
            dur = "-" if linha["liq_duration"] is None else linha["liq_duration"]
            print(f"   {str(linha['codigo_ativo'])[:24]:<24} {linha['exposicao']:>16,.2f} "
                  f"{dur:>4} {linha['valor_liquido']:>16,.2f}")

        from .indicadores import calcular

        dbp = carregar_base(saida, "db_passivo", fundo.cnpj)
        mov = carregar_base(saida, "mov", fundo.cnpj)
        indicadores = calcular(fundo, relatorio, pl_mil, dbp, mov, prazo_cot)

        def _pct(v):
            return "-" if v is None else f"{v*100:,.2f}%"

        def _enq(v):
            return "-" if v is None else ("Enquadrado" if v else "Desenquadrado")

        print("\nIndicadores de liquidez:")
        print(f"   Caixa Potencial ... R$ {indicadores.caixa_potencial_mil:,.2f} mil"
              f"  ({_pct(indicadores.caixa_pl)} do PL)")
        print(f"   Maior cotista ..... R$ {indicadores.maior_cotista_mil:,.2f} mil"
              f"   Cx/Cotista {_pct(indicadores.cx_cotista)}  {_enq(indicadores.enq_cotista)}")
        print(f"   GDP ............... R$ {indicadores.gdp_mil:,.2f} mil"
              f"   Cx/GDP {_pct(indicadores.cx_gdp)}  {_enq(indicadores.enq_gdp)}")
        print(f"   Resgate maximo .... R$ {indicadores.resgate_max_mil:,.2f} mil"
              f"   Caixa/Resg {_pct(indicadores.caixa_resgate)}  {_enq(indicadores.enq_rco)}")
        enq_anbima = [f"{h}d" for h, _, e in indicadores.anbima if e]
        print(f"   ANBIMA enquadrado ate: {', '.join(enq_anbima) if enq_anbima else '(nenhum horizonte)'}")

    print(f"\nTabela vencidos ({len(resultado.vencidos)} registros):")
    if resultado.vencidos.empty:
        print("   (vazia)")
    else:
        colunas = ["tipo", "codigo", "descricao", "linhas", "valor"]
        exibir = resultado.vencidos if args.detalhar else resultado.vencidos.head(15)
        print(exibir[colunas].to_string(index=False, max_colwidth=34))
        if not args.detalhar and len(resultado.vencidos) > 15:
            print(f"   ... mais {len(resultado.vencidos) - 15} registros (use --detalhar)")

    if args.detalhar:
        colunas = [c for c in ("Codigo_Ativo", "Emissor", "Base_Origem", "Valor_Liquido",
                               COL_QTDE_DIAS, COL_ATIVO_VENCIDO, COL_ATIVOS_EXCLUIDOS)
                   if c in resultado.carteira.columns]
        print("\nCarteira tratada (20 primeiras linhas):")
        print(resultado.carteira[colunas].head(20).to_string(index=False))

    if args.salvar:
        from .extrator import _gravar

        destino = saida / "tratado"
        c1 = _gravar(resultado.carteira, destino / f"carteira_{fundo.cnpj_numerico}", args.formato)
        c2 = _gravar(resultado.bd, destino / f"bd_{fundo.cnpj_numerico}", args.formato)
        print(f"\ngravado: {c1}")
        print(f"gravado: {c2}")
        if matriz is not None:
            c3 = _gravar(
                matriz.reset_index(names="Prazos"),
                destino / f"analise_carteira_{fundo.cnpj_numerico}", args.formato,
            )
            c4 = _gravar(tabela_of, destino / f"outros_fundos_{fundo.cnpj_numerico}", args.formato)
            c5 = _gravar(
                tabela_est.reset_index(names="Prazos"),
                destino / f"exposicao_estoque_{fundo.cnpj_numerico}", args.formato,
            )
            c6 = _gravar(relatorio, destino / f"relatorio_liquidez_{fundo.cnpj_numerico}", args.formato)
            print(f"gravado: {c3}")
            print(f"gravado: {c4}")
            print(f"gravado: {c5}")
            print(f"gravado: {c6}")

    if COL_VENCIDOS_BD not in resultado.bd.columns:
        log.warning("BD sem a coluna %s", COL_VENCIDOS_BD)
    return 0


def cmd_atualidade(args) -> int:
    """Ultima data de cada base; consulta que falhar vira linha, nao erro."""
    import pandas as pd

    from . import db, queries

    print(f"{'BASE':<28} ULTIMA DATA")
    for rotulo, sql in queries.SQL_ATUALIDADE:
        try:
            df = db.consultar(sql)
            u = df["u"].iloc[0] if not df.empty else None
            texto = "(vazia)" if u is None or pd.isna(u) else str(u)[:10]
        except Exception as exc:  # noqa: BLE001 - sem acesso ao datalake, por exemplo
            texto = f"(falhou: {str(exc).splitlines()[0][:70]})"
        print(f"{rotulo:<28} {texto}")
    return 0


def cmd_datalake_mapear(args) -> int:
    """Procura no datalake o que ainda vem do riscogc (so SELECT; sem cotista)."""
    from .mapa_datalake import mapear

    fichas = Cadastro(Path(args.diretorio)).carregar().filtrar()
    for linha in mapear(fichas, args.ate):
        print(linha)
    return 0


def cmd_carteira_resumo(args) -> int:
    """Composicao da carteira extraida (base local, sem banco)."""
    from .diagnostico import formatar, resumir_carteira
    from .extrator import carregar_base

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    fundo = cadastro.obter(args.fundo)
    if fundo is None:
        raise SystemExit(f"Fundo nao encontrado: {args.fundo}")
    carteira = carregar_base(Path(args.saida), "carteira", fundo.cnpj)
    print(f"\nFundo ......... {fundo.nome_interno} ({fundo.cnpj})")
    print(formatar(resumir_carteira(carteira, exemplos=args.exemplos), carteira))
    return 0


def cmd_carteira_cobertura(args) -> int:
    """Quanto do DC da carteira tem recebivel no estoque (base local, sem banco)."""
    from .diagnostico import cobertura_estoque, formatar_cobertura
    from .extrator import carregar_base

    cadastro = Cadastro(Path(args.diretorio)).carregar()
    fundo = cadastro.obter(args.fundo)
    if fundo is None:
        raise SystemExit(f"Fundo nao encontrado: {args.fundo}")
    saida = Path(args.saida)
    carteira = carregar_base(saida, "carteira", fundo.cnpj)
    bd = carregar_base(saida, "bd", fundo.cnpj)
    print(f"\nFundo ......... {fundo.nome_interno} ({fundo.cnpj})")
    print(formatar_cobertura(cobertura_estoque(carteira, bd, args.produto)))
    return 0


def cmd_extrair(args) -> int:
    from . import db  # noqa: F401 - importa aqui para 'fundos' nao exigir o driver
    from .extrator import BASES, extrair_fundo, extrair_lote

    data = dt.date.fromisoformat(args.data)
    cadastro = Cadastro(Path(args.diretorio)).carregar()
    if not cadastro.fundos:
        raise SystemExit("Cadastro vazio. Rode 'fundos importar' primeiro.")

    problemas = [p for p in cadastro.validar() if p.startswith("ERRO")]
    if problemas:
        for problema in problemas:
            print(problema)
        raise SystemExit("Cadastro com erro. Corrija antes de extrair.")

    bases = tuple(b.strip() for b in args.bases.split(",")) if args.bases else BASES
    desconhecidas = set(bases) - set(BASES)
    if desconhecidas:
        raise SystemExit(f"Base(s) desconhecida(s): {sorted(desconhecidas)}. Validas: {list(BASES)}")

    if args.fundo:
        fundo = cadastro.obter(args.fundo)
        if fundo is None:
            raise SystemExit(f"Fundo nao encontrado: {args.fundo}")
        regra = obter_regra(fundo.administrador)
        if not regra.implementado:
            log.warning(
                "%s: administrador sem tratamento de liquidez (%s)",
                fundo.nome_interno, fundo.administrador,
            )
        resultado = extrair_fundo(fundo, data, bases)
        print()
        for base, df in resultado.items():
            print(f"{base:<12} {len(df):>7} linhas x {len(df.columns):>3} colunas")
        if args.salvar:
            from .extrator import _gravar, identificar

            destino_base = Path(args.saida)
            avisar_saida(destino_base.resolve())
            log.warning(
                "Cada carga substitui a anterior: a saida ficara so com %s.",
                fundo.nome_interno,
            )
            for base, df in resultado.items():
                caminho = _gravar(identificar(df, fundo, args.data), destino_base / base, args.formato)
                print(f"gravado: {caminho.name}")
            print(f"\nDados salvos em: {caminho_efetivo(destino_base.resolve())}")
        return 0

    fundos = cadastro.filtrar(
        adm=args.adm, tipo=args.tipo, incluir_inativos=args.incluir_inativos
    )
    if not fundos:
        raise SystemExit("Nenhum fundo selecionado pelos filtros informados.")

    avisar_saida(Path(args.saida).resolve())

    destino = Path(args.saida).resolve()
    print(f"Gravando em: {destino}   (esta carga substitui a anterior)\n")

    resumo, por_base = extrair_lote(
        fundos, data, Path(args.saida), bases=bases,
        workers=args.workers, formato=args.formato, forcar=args.forcar,
    )
    ok = sum(1 for r in resumo if r["status"] == "ok")
    erros = [r for r in resumo if r["status"] == "erro"]
    pulados = sum(1 for r in resumo if r["status"] == "pulado")
    sem_codinome = sum(1 for r in resumo if r["status"] == "sem_codinome")
    print()
    print(f"Extraidas ............ {ok}")
    print(f"Puladas (ja existiam)  {pulados}")
    print(f"Sem codinome (Beyond)  {sem_codinome}")
    print(f"Erros ................ {len(erros)}")
    for r in erros[:20]:
        print(f"   {r['fundo']} | {r['base']}: {r['erro'][:120]}")

    print(f"\n{'ARQUIVO':<20} {'LINHAS':>9} {'FUNDOS':>7} {'KB':>9}")
    total_kb = 0.0
    for base, info in por_base.items():
        total_kb += info["kb"]
        print(f"{info['arquivo']:<20} {info['linhas']:>9,} {info['fundos']:>7} {info['kb']:>9,.0f}")
    print(f"{'TOTAL':<20} {'':>9} {'':>7} {total_kb:>9,.0f}")

    real = caminho_efetivo(destino)
    print(f"\nDados salvos em: {real}")
    print(f"Resumo ..........: {real / '_resumo.csv'}")
    return 1 if erros else 0


# ---------------------------------------------------------------------------

def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m liquidez", description=__doc__)
    parser.add_argument("-v", "--verboso", action="store_true", help="log em nivel DEBUG")
    parser.add_argument(
        "--diretorio", default=str(DIR_CADASTRO),
        help=f"pasta das fichas de fundos (padrao: {DIR_CADASTRO})",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    # --- fundos ---
    p_fundos = sub.add_parser("fundos", help="cadastro de fundos")
    sf = p_fundos.add_subparsers(dest="subcomando", required=True)

    p = sf.add_parser("importar", help="carga inicial a partir do .xlsm + CVM")
    p.add_argument("--arquivo", help="caminho do .xlsm (padrao: primeiro da pasta)")
    p.add_argument("--recriar", action="store_true", help="recria fichas existentes")
    p.add_argument("--forcar-cvm", action="store_true", help="ignora o cache diario da CVM")
    p.set_defaults(func=cmd_importar)

    p = sf.add_parser("sync-cvm", help="sincroniza o bloco cvm das fichas")
    p.add_argument("--aplicar", action="store_true", help="grava sem perguntar")
    p.add_argument("--forcar-cvm", action="store_true", help="ignora o cache diario da CVM")
    p.add_argument("--max-linhas", type=int, default=12, help="alteracoes exibidas por fundo")
    p.set_defaults(func=cmd_sync_cvm)

    p = sf.add_parser("add", help="cadastra um fundo novo")
    p.add_argument("--cnpj", required=True)
    p.add_argument("--nome", required=True, help="nome interno (usado no filtro do MOV)")
    p.add_argument("--pz-resg", type=int, dest="pz_resg")
    p.add_argument("--cc", type=int)
    p.add_argument("--codinome", help="codinome do Beyond")
    p.add_argument("--nome-produto", dest="nome_produto")
    p.add_argument("--permitir-cancelado", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sf.add_parser("edit", help="edita os blocos liquidez/integracao/controle")
    p.add_argument("fundo", help="CNPJ ou nome interno")
    p.add_argument("--pz-resg", type=int, dest="pz_resg")
    p.add_argument("--cc", type=int)
    p.add_argument("--codinome")
    p.add_argument("--nome-produto", dest="nome_produto")
    p.add_argument("--ativo", dest="ativo", action="store_true", default=None)
    p.add_argument("--inativo", dest="ativo", action="store_false")
    p.add_argument("--obs")
    p.set_defaults(func=cmd_edit)

    p = sf.add_parser("list", help="lista os fundos do cadastro")
    p.add_argument("--adm")
    p.add_argument("--tipo")
    p.add_argument("--situacao")
    p.add_argument("--incluir-inativos", action="store_true")
    p.add_argument("--csv", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sf.add_parser("show", help="exibe a ficha completa")
    p.add_argument("fundo")
    p.set_defaults(func=cmd_show)

    p = sf.add_parser("remove", help="move a ficha para _removidos/")
    p.add_argument("fundo")
    p.add_argument("--motivo", default="removido manualmente")
    p.set_defaults(func=cmd_remove)

    p = sf.add_parser("validar", help="valida o cadastro")
    p.set_defaults(func=cmd_validar)

    # --- prazos ---
    p_prazos = sub.add_parser("prazos", help="tabela de prazos de liquidez por ativo")
    p_prazos.add_argument(
        "--arquivo-prazos", dest="arquivo_prazos",
        help="caminho do CSV (padrao: config/prazos_ativos.csv)",
    )
    sp = p_prazos.add_subparsers(dest="subcomando", required=True)

    p = sp.add_parser("importar", help="gera a tabela a partir da aba Fundos do .xlsm")
    p.add_argument("--arquivo", help="caminho do .xlsm (padrao: primeiro da pasta)")
    p.add_argument("--aba", default="Fundos")
    p.add_argument("--forcar", action="store_true", help="regera mesmo se o CSV existir")
    p.set_defaults(func=cmd_prazos_importar)

    p = sp.add_parser("list", help="lista os ativos e prazos")
    p.add_argument("--busca", help="filtra por codigo ou CNPJ")
    p.add_argument("--sem-prazo", action="store_true", help="so os que estao sem prazo")
    p.add_argument("--csv", action="store_true")
    p.set_defaults(func=cmd_prazos_list)

    p = sp.add_parser("show", help="exibe um ativo")
    p.add_argument("codigo")
    p.set_defaults(func=cmd_prazos_show)

    p = sp.add_parser("set", help="define o prazo de um ativo")
    p.add_argument("codigo", help="codigo_ativo (ou codigo_op)")
    p.add_argument("--prazo", type=int)
    p.add_argument("--cnpj")
    p.add_argument("--codigo-op", dest="codigo_op")
    p.add_argument("--obs")
    p.add_argument("--criar", action="store_true", help="inclui se nao existir")
    p.set_defaults(func=cmd_prazos_set)

    p = sp.add_parser("sync-cadastro", help="realinha os prazos de origem 'cadastro'")
    p.add_argument("--aplicar", action="store_true", help="grava sem perguntar")
    p.set_defaults(func=cmd_prazos_sync_cadastro)

    p = sp.add_parser(
        "sincronia",
        help="confere/realinha os prazos derivados do cadastro (usado na rodada)",
    )
    p.add_argument("--conferir", action="store_true",
                   help="so aponta a divergencia (codigo 2), nao grava")
    p.set_defaults(func=cmd_prazos_sincronia)

    p = sp.add_parser("validar", help="valida a tabela de prazos")
    p.set_defaults(func=cmd_prazos_validar)

    # --- extrair ---
    # --- cronograma ---
    p_crono = sub.add_parser(
        "cronograma", help="cadastro das operacoes (vencimentos, amortizacoes, taxas)"
    )
    sc = p_crono.add_subparsers(dest="subcomando", required=True)

    p = sc.add_parser("importar", help="le a planilha de conciliacao e atualiza o cadastro")
    p.add_argument("--fundo", required=True, help="CNPJ ou nome interno")
    p.add_argument("--planilha", help="caminho do .xlsb (padrao: o mais recente de config/planilhas)")
    p.add_argument("--saida", default=str(SAIDA_PADRAO),
                   help="pasta da carga; a carteira define quais abas ler")
    p.add_argument("--produto", default="DC", help="Base_Origem das operacoes (padrao: DC)")
    p.add_argument("--todas-as-abas", dest="todas_as_abas", action="store_true",
                   help="le a planilha inteira, sem consultar a carteira")
    p.add_argument("--conferir", action="store_true", help="so mostra o diff, nao grava")
    p.add_argument("--forcar", action="store_true", help="grava sem pedir confirmacao")
    p.set_defaults(func=cmd_cronograma_importar)

    p = sc.add_parser(
        "sincronizar",
        help="processa as planilhas da pasta de entrada (nome do arquivo traz o CNPJ)",
    )
    p.add_argument("--entrada", default=str(_cr_dirs()[0]),
                   help="pasta onde as planilhas sao depositadas")
    p.add_argument("--processados", default=str(_cr_dirs()[1]),
                   help="para onde vai o arquivo depois de lido")
    p.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga")
    p.add_argument("--produto", default="DC", help="Base_Origem das operacoes (padrao: DC)")
    p.add_argument("--conferir", action="store_true",
                   help="so mostra o que faria; nao grava nem move")
    p.set_defaults(func=cmd_cronograma_sincronizar)

    p = sc.add_parser("list", help="lista as operacoes do cadastro")
    p.add_argument("--fundo", required=True, help="CNPJ ou nome interno")
    p.add_argument("--emissor", help="filtra por trecho do nome do emissor")
    p.set_defaults(func=cmd_cronograma_list)

    p = sc.add_parser("show", help="exibe uma operacao e seu cronograma")
    p.add_argument("--fundo", required=True, help="CNPJ ou nome interno")
    p.add_argument("--codigo", required=True, help="codigo da operacao (ex.: C227129)")
    p.set_defaults(func=cmd_cronograma_show)

    p = sc.add_parser("validar", help="cruza a carteira com o cronograma e aponta pendencias")
    p.add_argument("--fundo", required=True, help="CNPJ ou nome interno")
    p.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga extraida")
    p.add_argument("--produto", default="DC", help="Base_Origem a validar (padrao: DC)")
    p.add_argument("--limite", type=int, default=15, help="quantos codigos listar")
    p.add_argument("--detalhar", action="store_true", help="mostra as linhas pendentes")
    p.set_defaults(func=cmd_cronograma_validar)

    p = sc.add_parser("beyond",
                      help="captura do Beyond o cronograma das NC/CCB sem fluxo cadastrado")
    p.add_argument("--fundo", help="CNPJ ou nome interno; sem isso roda os com codinome")
    p.add_argument("--adm", help="filtra o lote por administrador")
    p.add_argument("--data", help="data de posicao da carteira (padrao: a da carga local; sem carga, a mais recente do banco)")
    p.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga extraida")
    p.add_argument("--detalhar", action="store_true", help="mostra o motivo de cada linha na fila")
    p.add_argument("--inventario", action="store_true",
                   help="so lista o que o Beyond tem para o fundo (familia x produto); nao captura")
    p.set_defaults(func=cmd_cronograma_beyond)

    # --- fatos (camada de BI) ---
    p_fatos = sub.add_parser("fatos", help="camada de fatos para BI, particionada por data")
    sfa = p_fatos.add_subparsers(dest="subcomando", required=True)

    p = sfa.add_parser("gerar", help="grava as tabelas da data da carga")
    p.add_argument("--fundo", help="CNPJ ou nome interno; sem isso, todos")
    p.add_argument("--adm", help="filtra por administrador")
    p.add_argument("--tipo", help="filtra por tipo")
    p.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga")
    p.add_argument("--destino", default="bi", help="raiz da camada de fatos (padrao: bi)")
    p.add_argument("--incluir-inativos", action="store_true")
    p.add_argument("--verboso", action="store_true", help="lista fundo a fundo")
    p.set_defaults(func=cmd_fatos)

    p = sfa.add_parser("resumo", help="o que ja existe na camada de fatos")
    p.add_argument("--destino", default="bi")
    p.set_defaults(func=cmd_fatos_resumo)

    # --- painel ---
    p = sub.add_parser("painel", help="gera o painel de liquidez em HTML autocontido")
    p.add_argument("--destino", default="bi", help="raiz da camada de fatos")
    p.add_argument("--saida-html", dest="saida_html", default="painel.html",
                   help="caminho do HTML (padrao: painel.html)")
    p.add_argument("--sem-fundos", dest="sem_fundos", action="store_true",
                   help="nao gera os relatorios individuais (os links do painel "
                        "ficam quebrados)")
    p.set_defaults(func=cmd_painel)

    # --- apresentacao (versao melhorada; o painel antigo continua em 'painel') ---
    p = sub.add_parser("apresentacao",
                       help="apresentacao para o comite: capa + blocos A-F por fundo (docs/plano-apresentacao.md)")
    p.add_argument("--fundos", required=True,
                   help='lista separada por virgula, CNPJ ou nome interno (ex.: "MULTIPLICA FIDC,MULTIPLICA LONG TERM FIDC,MULTIAGRO FIDC")')
    p.add_argument("--destino", default="bi", help="raiz da camada de fatos")
    p.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga (dados/, com tratado/ e db_passivo)")
    p.add_argument("--saida-html", dest="saida_html", default="apresentacao.html", help="arquivo HTML de saida")
    p.add_argument("--data", help="data dos fatos (AAAA-MM-DD); padrao: a mais recente")
    p.set_defaults(func=cmd_apresentacao)

    # --- fluxo ---
    p = sub.add_parser("fluxo", help="projeta o fluxo futuro das operacoes")
    p.add_argument("--fundo", help="CNPJ ou nome interno; sem isso, todos com cronograma")
    p.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga extraida")
    p.add_argument("--data", help="data-base (AAAA-MM-DD); padrao: a da carga")
    p.add_argument("--produto", default="DC", help="Base_Origem a projetar (padrao: DC)")
    p.add_argument("--somente-nao-vencidos", dest="somente_nao_vencidos",
                   action="store_true",
                   help="so as operacoes que entram nas faixas de prazo")
    p.add_argument("--mercadoria", default="DI1", help="curva usada (padrao: DI1)")
    p.add_argument("--por-mes", dest="por_mes", action="store_true",
                   help="abre o vencimento por mes")
    p.add_argument("--detalhar", action="store_true", help="resumo por operacao")
    p.add_argument("--salvar", action="store_true", help="grava o fluxo em CSV")
    # Fora de 'dados/': aquela pasta e a carga, que a rodada diaria substitui inteira.
    p.add_argument("--destino", default="fluxo", help="pasta do CSV (com --salvar)")
    p.add_argument("--fatos", action="store_true",
                   help="grava tambem fato_fluxo na camada de BI")
    p.add_argument("--destino-bi", dest="destino_bi", default="bi",
                   help="raiz da camada de fatos (com --fatos)")
    p.add_argument("--limite", type=int, default=15)
    p.add_argument("--sem-bullet", dest="sem_bullet", action="store_true",
                   help="desliga a premissa bullet; so projeta o que tem cronograma")
    p.add_argument("--acrual", action="store_true",
                   help="separa o juro corrido do principal e o coloca na primeira "
                        "parcela futura (o Valor_Liquido da carteira e preco sujo)")
    p.set_defaults(func=cmd_fluxo)

    # --- cotas ---
    p = sub.add_parser(
        "cotas",
        help="de-para de ISIN para CNPJ das cotas de fundo (nao vale para ativos)",
    )
    sc = p.add_subparsers(dest="acao", required=True)

    q = sc.add_parser("sincronizar", help="puxa o de-para do cadastro do riscogc")
    q.add_argument("--saida", default="config/isin_cotas.csv", help="CSV do de-para")
    q.add_argument("--limite", type=int, default=15)
    q.set_defaults(func=cmd_cotas_sincronizar)

    q = sc.add_parser("pendentes", help="cotas da carteira que seguem sem prazo")
    q.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga extraida")
    q.add_argument("--limite", type=int, default=25)
    q.set_defaults(func=cmd_cotas_pendentes)

    q = sc.add_parser("classificar",
                      help="aberto ou fechado? casa as cotas de outros fundos com o registro da CVM")
    q.add_argument("--fundo", required=True, help="CNPJ ou nome interno")
    q.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga extraida")
    q.add_argument("--csv", help="grava o resultado neste CSV (alem de imprimir)")
    q.add_argument("--top", type=int, default=3, help="candidatas por cota (padrao 3)")
    q.add_argument("--forcar-cvm", dest="forcar_cvm", action="store_true",
                   help="rebaixa o registro da CVM mesmo com cache")
    q.set_defaults(func=cmd_cotas_classificar)

    q = sc.add_parser("serie",
                      help="baixa a serie historica de cotas para retorno e VaR")
    q.add_argument("--ate", help="ultima data da janela, AAAA-MM-DD (padrao: hoje)")
    q.add_argument("--dias", type=int, default=None,
                   help=f"tamanho da janela em dias corridos "
                        f"(padrao {'%d' % 400})")
    q.add_argument("--destino", default=None, help="pasta da camada de fatos")
    q.set_defaults(func=cmd_cotas_serie)

    # --- bullet ---
    p = sub.add_parser(
        "bullet",
        help="controle das operacoes DC projetadas como bullet",
    )
    sb = p.add_subparsers(dest="acao", required=True)

    q = sb.add_parser("marco", help="grava o estoque inicial (roda uma vez)")
    q.add_argument("--data", required=True, help="data do marco, AAAA-MM-DD")
    q.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga extraida")
    q.add_argument("--refazer", action="store_true",
                   help="substitui o marco existente (reclassifica como estoque "
                        "tudo que apareceu depois)")
    q.set_defaults(func=cmd_bullet_marco)

    q = sb.add_parser("listar", help="mostra o registro e os estados")
    q.add_argument("--estado", choices=["estoque", "pendente", "confirmado",
                                        "planilha_pendente"],
                   help="filtra por estado")
    q.add_argument("--limite", type=int, default=40)
    q.set_defaults(func=cmd_bullet_listar)

    q = sb.add_parser("confirmar", help="conferida: e bullet mesmo")
    q.add_argument("--op", help="Codigo_Op (uma operacao)")
    q.add_argument("--ativo", help="prefixo do Codigo_Ativo (familia inteira, "
                                   "ex.: CCEXP)")
    q.add_argument("--saida", default=str(SAIDA_PADRAO),
                   help="pasta da carga extraida (necessaria com --ativo)")
    q.add_argument("--nota", default="", help="observacao livre")
    q.set_defaults(func=cmd_bullet_confirmar)

    q = sb.add_parser("planilha-pendente",
                      help="conferida: TEM fluxo, aguarda a planilha")
    q.add_argument("--op", required=True, help="Codigo_Op")
    q.add_argument("--nota", default="", help="observacao livre")
    q.set_defaults(func=cmd_bullet_planilha_pendente)

    q = sb.add_parser("remover", help="apaga o registro de uma operacao")
    q.add_argument("--op", required=True, help="Codigo_Op")
    q.set_defaults(func=cmd_bullet_remover)

    q = sb.add_parser("limpar", help="tira do registro quem saiu da carteira")
    q.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga extraida")
    q.add_argument("--limite", type=int, default=20)
    q.set_defaults(func=cmd_bullet_limpar)

    p = sub.add_parser("extrair", help="extrai as bases do SQL Server")
    p.add_argument("--data", required=True, help="data de posicao (AAAA-MM-DD)")
    p.add_argument("--fundo", help="CNPJ ou nome interno; sem isso roda o lote")
    p.add_argument("--adm", help="filtra o lote por administrador")
    p.add_argument("--tipo", help="filtra o lote por tipo (FIDC, FI, FIP, FII)")
    p.add_argument("--bases", help="lista separada por virgula (padrao: todas)")
    p.add_argument("--saida", default=str(SAIDA_PADRAO))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--formato", choices=("parquet", "csv"), default="parquet")
    p.add_argument("--forcar", action="store_true", help="reprocessa o que ja foi gravado")
    p.add_argument("--salvar", action="store_true", help="com --fundo, grava o resultado")
    p.add_argument("--incluir-inativos", action="store_true")
    p.set_defaults(func=cmd_extrair)

    # --- tratar ---
    p = sub.add_parser("tratar", help="aplica os tratamentos (Org_dados) sobre a carga")
    p.add_argument("--fundo", required=True, help="CNPJ ou nome interno")
    p.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga extraida")
    p.add_argument("--detalhar", action="store_true", help="mostra as linhas tratadas")
    p.add_argument("--salvar", action="store_true", help="grava carteira e BD tratados")
    p.add_argument("--formato", choices=("parquet", "csv"), default="parquet")
    p.set_defaults(func=cmd_tratar)

    # --- carteira ---
    # --- atualidade ---
    p = sub.add_parser("atualidade",
                       help="ultima data de cada base que a liquidez le (datalake e riscogc); so SELECT")
    p.set_defaults(func=cmd_atualidade)

    # --- datalake ---
    p_dl = sub.add_parser("datalake", help="diagnosticos do banco datalake (so SELECT)")
    sdl = p_dl.add_subparsers(dest="subcomando", required=True)
    p = sdl.add_parser("mapear",
                       help="procura no datalake as tabelas que ainda vem do riscogc; "
                            "so metadados e agregados, sem cotista")
    p.add_argument("--ate", help="fim da janela da serie de cotas, AAAA-MM-DD (padrao: hoje)")
    p.set_defaults(func=cmd_datalake_mapear)

    p_cart = sub.add_parser("carteira", help="diagnosticos sobre a carteira extraida")
    scr = p_cart.add_subparsers(dest="subcomando", required=True)
    p = scr.add_parser("resumo", help="Base_Origem x Modalidade, com exemplos de codigo")
    p.add_argument("--fundo", required=True, help="CNPJ ou nome interno")
    p.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga extraida")
    p.add_argument("--exemplos", type=int, default=3, help="exemplos por grupo (padrao 3)")
    p.set_defaults(func=cmd_carteira_resumo)
    p = scr.add_parser("cobertura-estoque", help="DC da carteira x recebiveis do BD (Codigo_Op = ID_Recebivel)")
    p.add_argument("--fundo", required=True, help="CNPJ ou nome interno")
    p.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta da carga extraida")
    p.add_argument("--produto", default="DC", help="Base_Origem das operacoes (padrao: DC)")
    p.set_defaults(func=cmd_carteira_cobertura)

    # --- relatorio ---
    p = sub.add_parser("relatorio", help="gera o Relatorio de Liquidez em Excel, por fundo")
    p.add_argument("--fundo", help="CNPJ ou nome interno; sem isso gera para o filtro/todos")
    p.add_argument("--adm", help="filtra por administrador")
    p.add_argument("--tipo", help="filtra por tipo (FIDC, FI, FIP, FII)")
    p.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta das bases extraidas")
    p.add_argument("--saida-rel", dest="saida_rel", default="relatorios",
                   help="pasta de saida dos .xlsx (padrao: relatorios)")
    p.add_argument("--incluir-inativos", action="store_true")
    p.set_defaults(func=cmd_relatorio)

    # --- documentar ---
    p = sub.add_parser("documentar", help="gera o documento tecnico dos tratamentos em HTML")
    p.add_argument("--saida-doc", dest="saida_doc", help="caminho do HTML (padrao: docs/tratamentos.html)")
    p.set_defaults(func=cmd_documentar)

    # --- regulamentos ---
    p = sub.add_parser("regulamentos",
                       help="baixa da CVM o regulamento vigente, uma pasta por fundo")
    p.add_argument("--fundo", help="CNPJ ou nome interno; sem isso baixa do filtro/todos")
    p.add_argument("--adm", help="filtra por administrador")
    p.add_argument("--tipo", help="filtra por tipo (FIDC, FI, FIP, FII)")
    p.add_argument("--diretorio", default=str(DIR_CADASTRO), help="pasta do cadastro")
    p.add_argument("--destino", default=str(reg_mod.DIR_REGULAMENTOS),
                   help="pasta raiz dos regulamentos (padrao: regulamentos)")
    p.add_argument("--sem-fnet", dest="sem_fnet", action="store_true",
                   help="usa so o dataset da CVM; mais rapido, mas perde entrega dos "
                        "ultimos dias em fundo estruturado")
    p.add_argument("--historico", action="store_true",
                   help="baixa tambem as versoes anteriores, nao so a vigente")
    p.add_argument("--refazer", action="store_true",
                   help="rebaixa mesmo o que ja esta em disco")
    p.add_argument("--incluir-inativos", action="store_true")
    p.set_defaults(func=cmd_regulamentos)

    # --- testar-conexao ---
    p = sub.add_parser("testar-conexao", help="confere driver ODBC e acesso ao riscogc")
    p.set_defaults(func=cmd_testar_conexao)

    # --- configurar ---
    p = sub.add_parser("configurar",
                       help="monta config/ a partir do riscogc + config/parametros (substitui a planilha .xlsm)")
    p.add_argument("--aplicar", action="store_true", help="grava; sem isso so relata o que faria")
    p.add_argument("--todos-os-fundos", dest="todos", action="store_true",
                   help="fichas de todos os fundos NORMAL, nao so dos que tem carteira")
    p.add_argument("--raiz", default=str(cfg_mod.DIR_CONFIG), help="pasta config (padrao: config)")
    p.add_argument("--parametros", default=str(cfg_mod.DIR_PARAMETROS),
                   help="pasta dos parametros da area (padrao: config/parametros)")
    p.add_argument("--data", help="posicao da carteira para listar as cotas (AAAA-MM-DD); "
                                  "padrao: a mais recente do banco, que pode estar incompleta")
    p.set_defaults(func=cmd_configurar)

    # --- importar-pacote ---
    p = sub.add_parser("importar-pacote",
                       help="copia para a config local o que o pacote do gestor traz e nao vai para o git "
                            "(cronogramas, cadastros manuais, CRA, decisoes de bullet); nao acessa o banco")
    p.add_argument("--pacote", required=True,
                   help="pasta do pacote do gestor (ou a config/ dele), ex.: \"...\\Relatorio Liquidez - pacote\"")
    p.add_argument("--aplicar", action="store_true", help="grava; sem isso so relata o que faria")
    p.add_argument("--sobrescrever", action="store_true",
                   help="troca o arquivo local que diverge do pacote (padrao: mantem o local)")
    p.add_argument("--raiz", default=str(cfg_mod.DIR_CONFIG), help="pasta config (padrao: config)")
    p.set_defaults(func=cmd_importar_pacote)

    return parser


def cmd_configurar(args) -> int:
    """Monta a configuracao a partir do banco; nada e gravado sem --aplicar."""
    dados = cfg_mod.consultar_tudo(args.data)
    rel = cfg_mod.executar(dados, raiz=Path(args.raiz), parametros=Path(args.parametros),
                           so_carteira=not args.todos, aplicar=args.aplicar)
    modo = "GRAVADO" if args.aplicar else "SIMULACAO (nada gravado; use --aplicar)"
    print(f"configurar: {modo}")
    print(f"Fichas novas .......... {rel.fichas_novas}")
    print(f"Fichas mantidas ....... {rel.fichas_mantidas}")
    print(f"Fichas sem pz_resg .... {rel.fichas_sem_prazo}")
    print(f"Overrides aplicados ... {rel.overrides_aplicados}")
    print(f"Sem codinome Beyond ... {len(rel.sem_codinome)}"
          + (": " + ", ".join(rel.sem_codinome[:15]) if rel.sem_codinome else ""))
    print(f"Feriados .............. {rel.feriados}")
    print(f"Prazos de ativos ...... {rel.prazos} ({rel.prazos_sem_prazo} sem prazo; "
          f"{rel.prazos_do_gestor} da tabela do gestor)")
    for a in rel.arquivos:
        print(f"   gravado {a}")
    if args.aplicar:
        print("\nProximos: 'cotas sincronizar' (isin_cotas.csv), depois 'fundos validar' e 'prazos validar'.")
    return 0


def cmd_importar_pacote(args) -> int:
    """Arquivos do pacote do gestor para a config local; nada e gravado sem --aplicar."""
    raiz = Path(args.raiz)
    rel = cfg_mod.importar_pacote(Path(args.pacote), raiz=raiz, aplicar=args.aplicar,
                                  sobrescrever=args.sobrescrever)
    modo = "GRAVADO" if args.aplicar else "SIMULACAO (nada gravado; use --aplicar)"
    print(f"importar-pacote: {modo}")
    print(f"Copiados .............. {len(rel.copiados)}")
    for a in rel.copiados:
        print(f"   {a}")
    print(f"Iguais (nada a fazer) . {len(rel.iguais)}")
    print(f"Divergentes (mantidos)  {len(rel.divergentes)}")
    for a in rel.divergentes:
        print(f"   {a}  (local difere do pacote; --sobrescrever troca)")
    print(f"Decisoes de bullet .... {len(rel.bullets)}"
          + (": " + ", ".join(rel.bullets) if rel.bullets else ""))
    if args.aplicar:
        print("\nProximos: 'cronograma list --fundo ...' e 'python -m liquidez.cra inventario' para conferir.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = construir_parser()
    args = parser.parse_args(argv)
    configurar_log(args.verboso)
    return args.func(args)
