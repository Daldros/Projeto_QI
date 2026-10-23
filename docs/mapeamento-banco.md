# Mapeamento do banco `riscogc` — o que o dashboard lê

Levantado do código em 19/10/2026, branch `alteracao-banco-dados`. Serve para
avaliar mudança nas tabelas que alimentam o painel: toda coluna listada é lida
por alguma consulta ou pelo tratamento que vem depois dela. O código só faz
`SELECT`; nada é gravado no banco.

Legenda da coluna **Uso**: `rotina` = `rodar_diario.ps1`; `config` = só no
`configurar` / `cotas sincronizar`; `avulso` = scripts da raiz, fora da rotina.

## 1. Carteira — `dbo.MUL_GLD_CARTEIRA_UNIFICADA` (rotina)

Fonte do PL, da matriz de prazos, do caixa potencial e da detecção de operações
novas. Lida em `liquidez/queries.py`, `configurar.py`, `beyond_cronograma.py`,
`cli.py`, `grafo_fundo.py`, `grafo_passivo.py`. Os apelidos à direita são os
nomes que o tratamento usa; o de-para vive só no SQL.

| Coluna | Apelido no código | Para que serve |
|---|---|---|
| `docFundo` | `CPF_CNPJ` | filtro do fundo |
| `dtReferencia` | `Data_Posicao` | filtro da data; base do cálculo de `Qtde_Dias` |
| `dsNomeFundo` | `Nome_Do_Fundo` | rótulo |
| `dsBaseOrigem` | `Base_Origem` | produto: DC, Outros Fundos, Tesouraria, Renda Fixa, CPR. Define a regra de valor líquido |
| `cdOp` | `Codigo_Op` | identidade da operação; reclassifica Renda Fixa `C######` como DC; chave do cronograma e do Beyond |
| `cdAtivo` | `Codigo_Ativo` | prefixo `VC` marca vencido (QI); `NCPX`/`CCBPX` entram no Beyond; LFT/LTN/NTN/CDB/IMOVE ficam fora do DC; mnemônico das cotas |
| `dsNomeAtivo` | `Nome_Ativo` | casamento das cotas com a tabela de prazos e com a CVM |
| `dsModalidade` | `Modalidade` | diagnóstico |
| `dsEmissor` | `Emissor` | carrego de vencidos: emissor com um título vencido tem todas as linhas fora do caixa |
| `dsStatus` | `Status` | veio vazio em 13/10/2026; se for preenchido, pode substituir a regra do prefixo |
| `dtAquisicao` | `Data_Aquisicao` | casamento com `Data_Operacao` do Beyond |
| `dtVencimento` | `Data_Vencimento` | `Qtde_Dias` em dias corridos; vencido por data (`data_anterior_a`) |
| `vlCustoTotal` | `Custo_Total` | `> 0` filtra linha zerada; desempate no casamento com o Beyond |
| `qtTotal` | `Qtd_Total` | `> 0` na regra de vencido da QI |
| `vlLiquido` | `Valor_Liquido` | **o valor de tudo**: PL, matriz, valor líquido, base do fluxo projetado |
| `vlBruto`, `vlPuMercado`, `txAno` | `Valor_Bruto`, `PU_Mercado`, `Taxa_Ano` | extraídos, não usados ainda |
| `cdFundo`, `dsOrigem`, `dtCarga` | — | extraídos, não usados |

Valores esperados em `dsBaseOrigem`: ver `config/parametros/produtos.json` e
`grafo_fundo.BASES_COTA`. Rótulo novo cai fora do caixa sem erro.

## 2. Estoque de recebíveis — `dbo.HOD_TB_ESTOQUE_UNIFICADO` (rotina)

`SELECT *` filtrado por `CNPJ_Fundo` e `Data_Posicao` (`queries.py`). Colunas
que o tratamento e a análise leem depois:

| Coluna | Para que serve |
|---|---|
| `CNPJ_Fundo`, `Data_Posicao` | filtros |
| `Valor_Presente` | valor do recebível; cota "A Vencer" e pool do próprio fundo |
| `Status_Recebivel` | `VENCIDO` / `A VENCER` (regra `_estoque` em `regras_vencidos.json`) |
| `Prazo_Atual_Dias_Corridos` | faixa de prazo do recebível |
| `CNPJ_Sacado` | carrego: sacado com título vencido tira todos os seus a vencer do caixa |
| `ID_Recebivel` | casa com `Codigo_Op` da carteira para não contar o DC duas vezes |
| `Tipo_Recebivel`, `Nome_Sacado`, `FAIXA_PDD` | diagnóstico; `FAIXA_PDD` deixou de ser filtro em 10/08/2026 |

## 3. Passivo (rotina)

### `dbo.HOD_PAS_POSICAO_NOTA` + `dbo.HOD_PAS_CAD_COTISTA` + `dbo.HOD_TB_CADASTRO_FUNDOS` + `dbo.TB_PASSIVO_DISTRIBUIDORES`

Desde 10/09/2026 é montada pela rotina do Datalake a partir da
`MUL_GLD_PASSIVO_CONSOLIDADO`. Na comparação de 15/10 parecia parada em 07/10,
mas era a rotina que não tinha rodado de 10 a 14/10. Em 21/10 estava em 19/10,
igual à consolidada. **Desde 21/10/2026 o projeto lê a consolidada direto**
(subseção abaixo). A consulta antiga ficou só em `queries.db_passivo_hod`, para
o `passivo comparar`. A tabela abaixo descreve a fonte antiga, como
referência.

| Tabela.coluna | Para que serve |
|---|---|
| `POSICAO_NOTA.dataPosicao` | filtro da data (o grafo recua para a última ≤ pedida) |
| `POSICAO_NOTA.cdCotistaAdm` | junção com o cotista |
| `POSICAO_NOTA.cdClasseAdm` | junção com `CADASTRO_FUNDOS.idFundoAdm` → classe |
| `POSICAO_NOTA.vlCorrigido` | saldo; soma por cotista e classe |
| `CAD_COTISTA.nome`, `cnpj`, `cpf` | identidade do cotista (`COALESCE(cnpj, cpf)`) |
| `CADASTRO_FUNDOS.CNPJFundo`, `nmFundoInterno`, `nmCotaInterno`, `Situacao` | fundo, classe; filtros `Situacao = 'NORMAL'` e `nmCotaInterno <> 'ORIGINAL'` |
| `DISTRIBUIDORES.DocumentoDistribuidor`, `nmDistribuidora` | distribuidor do cotista (`LEFT JOIN`) |

### `dbo.MUL_GLD_PASSIVO_CONSOLIDADO` (fonte desde 21/10/2026)

Base comum em `queries.PASSIVO_NOTAS`, usada por `db_passivo` e pelos quatro
scripts de passivo da raiz. Reproduz a carga que o Datalake fazia da HOD
(`usp_Carregar_HOD_PAS_POSICAO_NOTA`), com os mesmos códigos:

| Consolidada | HOD | Regra |
|---|---|---|
| `dtReferencia` | `dataPosicao` | filtro da data, dentro da base de notas |
| `cdFundo` | `cdClasseAdm` | junção com `HOD_TB_CADASTRO_FUNDOS.idFundoAdm` (`COLLATE DATABASE_DEFAULT`) |
| `cdCotista`, `nrNota` | `cdCotistaAdm`, `idNotaAdm` | chave da nota com a data e a classe |
| `dsNomeCotista`, `docCotista` | `HOD_PAS_CAD_COTISTA.nome`, `COALESCE(cnpj, cpf)` | identidade do cotista, da própria linha |
| `vlCorrigido` | `vlCorrigido` | saldo |
| `dsAdministrador` | — | só `SINGULARE` e `PLANNER`, como na HOD |
| `dhAlteracao`, `id` | — | nota repetida: fica a de alteração mais recente (`ROW_NUMBER`) |

Distribuidor: `TB_PASSIVO_DISTRIBUIDORES` agrupada em uma linha por documento e
casada só pelos dígitos (o `LEFT JOIN` direto duplicava a nota quando o
documento se repetia). `dsStatusConta` e `flSemDepara` seguem sem filtro, como
na carga da HOD. Conferência entre as duas fontes, só com agregados:
`python -m liquidez passivo comparar --data AAAA-MM-DD`.

## 4. Cadastro de fundos e cotas — `dbo.HOD_TB_CADASTRO_FUNDOS` (config + rotina)

`configurar.SQL_CADASTRO`, `queries.cadastro_cotas`, `varcota.CONSULTA`.

| Coluna | Para que serve |
|---|---|
| `CNPJFundo`, `idFundoInterno`, `idFundoAdm` | chaves; `idFundoAdm` casa com `cdClasseAdm` do passivo e com o código de cota da PLANNER; `idFundoInterno` casa com `HOD_TB_COTAS_FUNDOS.idFundo` |
| `nmFundoInterno`, `nmCotaInterno` | nome e classe; `ORIGINAL` é rótulo de procedência, não classe |
| `TipoFundo`, `TipoCota`, `Condominio` | ficha; `Condominio = FECHADO` → cota sem prazo (999) |
| `Situacao` | só `NORMAL` |
| `LiquidacaoResgate`, `CarenciaResgate`, `PrazoDeDuracao` | prazo de resgate das cotas ("D+1 ATE D+29" → 29) |
| `ISIN` | chave das cotas da VORTX |
| `Administrador`, `CNPJAdministrador`, `Gestor`, `CNPJGestor`, `RazaoSocial`, `DataPrimeiroAporte`, `RegistroCVM` | ficha do fundo |

## 5. Movimentação de cotistas — `dbo.HOD_MCT_REU_MOVIMENTACAO_PROCESSADA` (rotina)

`SELECT *` por `nome_produto LIKE ?`, sem filtro de data. Alimenta só o RCO
(resgate máximo histórico).

| Coluna | Para que serve |
|---|---|
| `nome_produto` | filtro pelo nome do produto da ficha |
| `tipo_movimentacao` | `RESGATE_TOTAL`, `RESGATE_PARCIAL` (`indicadores.json`) |
| `valor_movimentacao`, `data_movimentacao` | soma por dia → maior resgate diário |
| `data_processamento` | `datetimeoffset`; tem conversor próprio em `db.py` |

## 6. Beyond — `dbo.TB_LIQUIDEZ_OPERACOES_4` (rotina)

`beyond_cronograma.py` (cronograma de NC/CCB) e `queries.beyond` (extração
bruta por codinome). Tabela semanal: usa-se sempre `MAX(Data)`.

| Coluna | Para que serve |
|---|---|
| `Fundo` | codinome Beyond da ficha (`AUD_DIM_DEPARA_FUNDOS`) |
| `Data` | data da leitura (snapshot), não da posição |
| `Operacao`, `Titulo`, `Documento` | identidade da operação e de cada parcela |
| `Data_Operacao` | casa com `Data_Aquisicao` da carteira |
| `Data_Vencimento`, `Valor_Aplicado` | cronograma: data e valor de cada parcela; `SUM(Valor_Aplicado)` desempata pelo `Custo_Total` |
| `Situacao_Titulo` | só `A Vencer` |
| `Grupo_Produto` | só `CCB/NC%` |
| `Cedente`, `Indice`, `Taxa_Final`, `Produto` | taxa e indexador da projeção |
| `Valor_Vencimento`, `Taxa_Efetiva` | **não** usados (projeção própria com a curva) |

## 7. Curvas e taxas (rotina)

| Tabela | Colunas | Para que serve |
|---|---|---|
| `dbo.TB_CURVA_JUROS_DIARIA` | `Mercadoria` (`DI1`), `Data`, `NDC`, `NDU`, `Fator_Spot` | CDI futuro do fluxo projetado; `NDU` para taxa base 252 |
| `dbo.TB_TAXAS` (grafada `TB_Taxas` em `acrual.py`) | `Ativo` (`CDI_Over`), `Data`, `Valor` (varchar, % a.a.) | CDI realizado: acrual e Sharpe |

## 8. Série de cotas — `HOD_TB_COTAS_FUNDOS` (rotina, etapa 8b)

Sem prefixo `dbo.` no código. Junta com `HOD_TB_CADASTRO_FUNDOS` por
`idFundoInterno`.

| Coluna | Para que serve |
|---|---|
| `Data`, `idFundo` | série por classe; variantes `_GER`/`_IND` do mesmo fundo |
| `qtCotas`, `valorCota`, `valorPL` | a série válida é a que fecha `qtCotas × valorCota = valorPL` |

A view `VW_BI_LIQUIDEZ_PL` é citada no código mas **não** é lida.

## 9. Configuração (config)

| Tabela | Colunas |
|---|---|
| `dbo.AUD_DIM_FERIADOS` | `dtFeriado`, `dsFeriado` |
| `dbo.AUD_DIM_DEPARA_FUNDOS` | `dsNomeFundoAdm`, `nmFundoByd`, `flAtivo` |

## 10. Scripts avulsos (fora da rotina)

`analise_passivo.py`, `passivo_gestor.py`, `grafo_passivo.py` reusam as
tabelas de passivo (item 3), o cadastro e a carteira (`docFundo`,
`dsNomeFundo`, `cdAtivo`, `dsNomeAtivo`, `vlLiquido`, `dsBaseOrigem`).

## Pontos de atenção para mudança de banco

- **`SELECT *`** em estoque, movimentação e Beyond bruto: coluna nova entra
  sem erro; coluna renomeada quebra no tratamento, não na consulta.
- **Rótulos com significado**: `dsBaseOrigem`, `tipo_movimentacao`,
  `Status_Recebivel`, `Situacao_Titulo`, `Grupo_Produto`, `Situacao`,
  `nmCotaInterno = 'ORIGINAL'`, `Ativo = 'CDI_Over'`, `Mercadoria = 'DI1'`.
  Mudar o conteúdo muda o número sem erro.
- **Formato do CNPJ**: a carteira usa `docFundo` com pontuação; o código
  compara só dígitos, então os dois formatos servem.
- **Datas**: carteira e estoque filtram por data exata; passivo e Beyond
  recuam para a última disponível. `data_processamento` é `datetimeoffset`.
- **Passivo**: migrado para `MUL_GLD_PASSIVO_CONSOLIDADO` em 21/10/2026; ver item 3.
- **Os `MUL_GLD_*` do riscogc são clones do banco `datalake`**, refeitos pela
  rotina do projeto do Datalake (`python main.py --rodar-clones`, tarefa
  "DLK Clonar TODOS"). A mesma rotina recarrega a HOD de passivo,
  movimentação, cotas e estoque. Se ela não roda, carteira, passivo e o resto
  ficam parados juntos. A tarefa roda às 18:30 só com o usuário logado
  ("Interativo apenas"); os logs têm buracos (10 a 14/10, 17 e 18/10).
- **Defasagem de cada base** (`python -m liquidez atualidade`, em 21/10/2026):
  carteira e cotas no dia (21/10), estoque em D-1 (20/10), passivo,
  movimentação e curva DI1 em D-2 (19/10), Beyond em 15/10. O passivo atrasa
  já na origem do datalake, não no clone. **A data-base que tem todas as bases
  é a menor delas** (19/10 neste dia), e o Beyond defasado só faz a operação
  nova cair na premissa de bullet.
