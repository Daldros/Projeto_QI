# Risco de Liquidez — Multiplica FIDCs

Código do gestor de risco (pacote `liquidez/` e scripts de passivo), versionado
e em reorganização. Saídas em **HTML**: painel (`painel.html`) e um relatório
por fundo (`fundos/<cnpj>.html`).

## Situação (23/10/2026)

- Bancos: **riscogc** e **datalake**, no mesmo servidor de produção (srvSqlPrd01,
  `10.101.1.18,1433`), autenticação Windows. A conexão é no riscogc e o datalake
  é lido pelo nome em três partes (`datalake.dbo.TABELA`). Outro ambiente:
  variáveis `LIQUIDEZ_SERVIDOR`, `LIQUIDEZ_BANCO` e `LIQUIDEZ_BANCO_DATALAKE`.
  O gestor roda no HML (`10.201.1.6`, `riscogchml`). A documentação dele em
  `docs/gestor/` fala desse banco, e isso **não** vale aqui.
- Desde 23/10/2026, carteira, passivo e série de cotas vêm direto do datalake
  (`liquidez/tabelas.py`). Estoque, cadastro de fundos e movimentação (`HOD_*`)
  ainda vêm do riscogc: `python -m liquidez datalake mapear` levanta as
  equivalentes no datalake. Mapa completo em `docs/mapeamento-banco.md`.
- Pacote do gestor de 21/10/2026 integrado (`docs/pacote-gestor-2026-10-21.md`):
  - voltaram os módulos `relatorio_excel`, `documentacao` e `cra`;
  - a tabela de prazos, a lista de excluídos e as decisões por fundo entraram
    em `config/parametros/`;
  - cronogramas e cadastros manuais vão para a `config/` local por
    `importar-pacote`.
- A planilha `Relatorio de Liquidez_Multiplica_V2.xlsm` não veio e o resto da
  `config/` continua sendo **reconstruído a partir do banco** (plano em
  `docs/diagnostico-inicial.md` e no projeto de mapeamento do banco, repo
  Projeto-Datalake---Risco-GC, `docs/projeto-mapeamento-banco.md`).
- Dados gerados (`bi/`, parquet, HTML) **não são versionados** (`.gitignore`):
  contêm carteira, operações e cotistas.

## Configuração (reconstruída do banco)

A planilha `.xlsm` e a pasta `config/` originais não existem mais. O que a
área decide fica versionado em `config/parametros/`:
- limites dos indicadores, regras de vencidos e ativos excluídos;
- prazos fixos e `prazos_ativos_gestor.csv`, a tabela de prazos por ativo do
  gestor;
- `fundos_overrides.csv`, com pz_resg/cc/codinome por fundo.

O resto sai do banco (cadastro de fundos do riscogc, carteira do datalake):

```bat
python -m liquidez importar-pacote --pacote "...\Relatorio Liquidez - pacote" --aplicar  :: so na 1a vez (sem banco)
python -m liquidez configurar            :: simula e relata
python -m liquidez configurar --aplicar  :: grava config/fundos, feriados, prazos e copia os parametros
python -m liquidez cotas sincronizar     :: config/isin_cotas.csv
python -m liquidez fundos validar
python -m liquidez prazos validar
```

`config/` gerada fica fora do git (só `config/parametros/` e `config/marca/`
entram).

## Uso

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest -q          :: testes sem banco
.venv\Scripts\python -m liquidez testar-conexao
.venv\Scripts\python -m liquidez --help
```

Rotina: `rodar_diario.ps1 -Data AAAA-MM-DD` (um dia) e `rodar_periodo.ps1`
(faixa de dias).

## Estrutura

| Caminho | O que é |
|---|---|
| `liquidez/` | pacote: extração, tratamento, fluxo, matriz, indicadores, VaR/Sharpe, fatos, painel e relatórios |
| `analise_passivo.py`, `grafo_fundo.py`, `grafo_passivo.py`, `passivo_gestor.py` | concentração e rede do passivo (rodam à mão) |
| `rodar_diario.ps1`, `rodar_periodo.ps1` | rotina |
| `docs/` | diagnóstico e decisões |
| `tests/` | testes sem banco (CI no GitHub) |

## Histórico

- `8a53141` — zip original do gestor, sem alteração.
- `4951e2c` — o mesmo código extraído para o repositório (sem `bi/`).
- Mudanças seguintes: uma por commit, descritas na mensagem.
