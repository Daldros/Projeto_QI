# Diagnóstico inicial — código do gestor de risco (04/10/2026)

Base: commit `8a53141` ("Codigo original do gestor de risco (sem alteracoes)"),
arquivo `pacote_geracao_bi.zip` (11 MB). Leitura completa, sem executar contra o
banco. Referências de análise: `Guia_de_Leitura_Risco_Fundos_Credito.pdf`.

## 1. O que veio

| Parte | Linhas | O que faz |
|---|---|---|
| `liquidez/` (pacote) | ~14 mil | Extração do SQL Server, tratamento da carteira, fluxo projetado das operações, matriz de liquidez, indicadores, VaR/Sharpe da cota, fatos parquet para BI, painel HTML e relatório HTML por fundo. CLI `python -m liquidez` |
| `analise_passivo.py`, `grafo_fundo.py`, `grafo_passivo.py`, `passivo_gestor.py` | ~4,6 mil | Concentração do passivo (HHI, top 1/3/5), rede fundo↔cotista, exposição de um fundo, passivo por gestor/decisor. Rodam à mão |
| `rodar_diario.ps1`, `rodar_periodo.ps1` | — | Rotina: extrair → prazos → cronogramas → fluxo → relatório → fatos → cotas → painel |
| `bi/` | 65 dias (01/06–31/08/2026) | Saídas parquet (fatos e dimensões) para Power BI |

**Pontos fortes**: código legível, comentários que explicam cada decisão, SQL
parametrizado, HTML escapado, painel autocontido (sem CDN), códigos de saída
pensados na rotina.

## 2. Fontes de dados

Servidor `10.201.1.6`, banco `riscogchml` (o Datalake usa `10.101.1.18` /
`riscogc` — **confirmar qual vale**). Tabelas: `HOD_TB_ESTOQUE_UNIFICADO`,
`HOD_TB_CARTEIRA_UNIFICADA`, `HOD_PAS_POSICAO_NOTA`, `HOD_PAS_CAD_COTISTA`,
`HOD_TB_CADASTRO_FUNDOS`, `TB_PASSIVO_DISTRIBUIDORES`,
`HOD_MCT_REU_MOVIMENTACAO_PROCESSADA`, `TB_CURVA_JUROS_DIARIA`, `TB_Taxas`
(CDI), `TB_LIQUIDEZ_OPERACOES_4` (Beyond), `VW_BI_LIQUIDEZ_PL`. Web: feriados
ANBIMA, cadastro e eventuais da CVM, FNET.

## 3. O que falta para rodar (não veio no pacote)

- `config/`: `fundos/*.json`, `indicadores.json`, `regras_vencidos.json`,
  `ativos_excluidos.json`, `prazos_ativos.csv`, `isin_cotas.csv`, `calendario/`.
- Planilha `Relatorio de Liquidez_Multiplica_V2.xlsm` (importação inicial).
- Módulos citados e ausentes: `liquidez/relatorio_excel.py` (o passo
  `relatorio` da rotina **sempre falha**), `liquidez/documentacao.py`,
  `liquidez/cra.py`.
- `../cvm_gestao/indice/mercado_*.csv.gz` (usado por `passivo_gestor.py`).
- Dependências fora do `requirements.txt`: `pyxlsb`, `xlrd`.

## 4. Análises do guia × o que existe

| Análise | Situação |
|---|---|
| Fluxo do ativo × prazo de resgate | **Parcial**: faixas em dias corridos, sem vértices 1–63 du |
| Matriz ANBIMA de probabilidade de resgate | **Ausente** (hoje: Caixa/PL × percentis fixos, o mesmo valor em todos os horizontes) |
| Concentração do passivo | **Existe** (top 1/3, distribuidor, GDP, HHI) |
| Estresse (ESMA) | **Ausente** |
| VaR | **Existe** (EWMA paramétrico, histórico, backtest) |
| Duration / DV01 | **Ausente** |
| Cota suavizada / Sharpe corrigido (GLM, Lo) | **Ausente** (só alerta AC1 > 0,5) |
| Enquadramento CVM 175 | **Ausente** (há limites internos em `config/indicadores.json`) |

## 5. Problemas — quebram a execução

- `analise_passivo.py:262,266` chama `gf.desenhar`, que não existe.
- `relatorio` (passo 8 da rotina) importa `liquidez.relatorio_excel`, ausente.
- Fluxo gravado em `C:\Users\...\liquidez_bi` pela rotina, mas lido de
  `<projeto>\bi` (`cli.py:1950`, `grafo_fundo.py:54`): relatório e fatos voltam
  calados para a matriz.
- `rodar_periodo.ps1:144-157`: código 11 (pendência do Beyond) tratado como
  falha, interrompe o período.

## 6. Problemas — podem distorcer números

1. Movimentação sem filtro de data e por prefixo do nome (`queries.py:94-101`):
   o RCO usa resgates **posteriores** à data-base e pode misturar "FUNDO X II".
2. Cronograma com valor vazio → todo o principal na última parcela, sem aviso
   (`fluxo.py:210,246`); taxa ausente vira 0 % (`fluxo.py:249`).
3. Curva DI em degrau, sem interpolação (`fluxo.py:170,182`).
4. Beyond: filtro pelo snapshot mais recente ao reprocessar data passada
   (`beyond_cronograma.py:299`); `NOT IN` com NULL devolve vazio (`:458`).
5. Coluna renomeada na carteira → nenhum ativo vencido, liquidez superestimada,
   só com warning (`regras_vencidos.py:52-54`).
6. Join de distribuidor pode duplicar linhas e inflar saldo
   (`analise_passivo.py:48`, `grafo_passivo.py:87`).
7. Sharpe e razão hist/param comparam janela de ~13 meses com vol EWMA de
   meia-vida ~11 dias; VaR 21d = √21·VaR1d subestima com cota suavizada
   (`varcota.py:695,722-725`).
8. Retomada da extração pode consolidar `_tmp/` de outra data
   (`extrator.py:201-215`); partição 11/08 sem `fato_indicadores`.
9. Fila de bullets apaga registros de fundos sem carga no dia
   (`bullets.py:355-369`).
10. Regras divergentes entre scripts (casamento carteira→fundo por R$ 1 × 1 %;
    senioridade por regex × `startswith`); duas "escadas de liquidez"
    diferentes (painel × relatório).

## 7. Segurança e LGPD

- Sem senhas no código (autenticação Windows). SSL desligado nos downloads
  ANBIMA/CVM (`calendario.py:50`, `regulamentos.py:107`).
- `bi/` não tem dados de cotista, mas `dim_operacao` tem emissores (alguns
  parecem pessoa física), contratos e valores → **dados não devem ficar no
  repositório**.
- `grafo_passivo.html`, `analise_passivo.html` e `passivo_gestor.html` saem com
  CPF/CNPJ, nomes e saldos de cotistas → controle de acesso/mascaramento.

## 8. Qualidade

Nenhum teste; `cli.py` com 2.735 linhas; o bloco "carregar carteira + prazos +
tratar" repetido ~8 vezes; SQL de passivo repetido 5 vezes; paleta/tema/logo
copiados em 4 arquivos; caminhos relativos ao diretório atual; HTML/JS gigantes
em f-strings; números de negócio fixos no código (λ 0,94, 95 %, D+2, 999 =
sem prazo, tolerâncias).

## 9. Ordem de trabalho proposta

**Fase 0 — Estrutura (nenhum número muda)**: descompactar o código no
repositório e tirar `bi/` (dados) do versionamento; `.gitignore`;
`requirements.txt` completo e com versões; configuração única (servidor, banco,
pastas de saída) em `.env`; README; teste de import e testes de fumaça; corrigir
o que impede rodar (`gf.desenhar`, caminho do `bi`, código 11 do período).

**Fase 1 — Correções que mudam números** (uma por vez, com comparação
antes/depois numa data de referência): itens 1 a 9 da seção 6.

**Fase 2 — Consolidação**: módulo comum de SQL/estilo/HTML; uma única escada
de liquidez; quebrar `cli.py`; scripts de passivo dentro do pacote e da rotina;
mascaramento de documentos nos HTMLs.

**Fase 3 — Análises novas do guia**: vértices em dias úteis + matriz ANBIMA de
resgate por tipo de cotista; estresse combinado passivo × ativo (ESMA);
duration/DV01 por vértice; Sharpe e VaR corrigidos para cota suavizada;
enquadramento CVM 175.

**Fase 4 — Rotina**: atualização diária num comando, encadeada depois da rotina
do Datalake (18:30), com e-mail de status.
