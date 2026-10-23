# Mapeamento dos bancos `riscogc` e `datalake` — o que o dashboard lê

Levantado do código em 19/10/2026, branch `alteracao-banco-dados`; atualizado
em 23/10/2026, quando carteira, passivo e série de cotas passaram a vir direto
do banco `datalake`. Serve para avaliar mudança nas tabelas que alimentam o
painel: toda coluna listada é lida por alguma consulta ou pelo tratamento que
vem depois dela. O código só faz `SELECT`; nada é gravado no banco.

Os dois bancos estão no mesmo servidor. A conexão é no `riscogc` e o datalake é
lido pelo nome em três partes (`datalake.dbo.TABELA`). Os nomes ficam em
`liquidez/tabelas.py` e o banco vem de `LIQUIDEZ_BANCO_DATALAKE` (padrão
`datalake`).

| Base | Tabela | Banco |
|---|---|---|
| carteira | `MUL_GLD_CARTEIRA_UNIFICADA` | datalake (até 23/10, clone no riscogc) |
| passivo | `MUL_GLD_PASSIVO_CONSOLIDADO` | datalake (de 21 a 23/10, clone no riscogc) |
| série de cotas | `MUL_GLD_MEC_CONSOLIDADA` + `DLK_DEPARA_FUNDOS` | datalake (até 23/10, `HOD_TB_COTAS_FUNDOS`) |
| estoque | `MUL_GLD_ESTOQUE` | datalake (até 23/10, `HOD_TB_ESTOQUE_UNIFICADO` no riscogc) |
| cadastro de fundos | `HOD_TB_CADASTRO_FUNDOS` | riscogc; `DLK_DEPARA_FUNDOS` tem CNPJ e cota, mas não prazo de resgate nem ISIN |
| movimentação | `HOD_MCT_REU_MOVIMENTACAO_PROCESSADA` | riscogc; no datalake só há tabelas por administrador |
| Beyond, curva, CDI, distribuidores, feriados, de-para do Beyond | `TB_*`, `AUD_DIM_*` | riscogc; candidatas a feriados (`DLK_CALENDARIO_B3`) e CDI (`CDH_GLD_INDICES_MERCADO`) em conferência |

O que ainda vem do riscogc é procurado no datalake por
`python -m liquidez datalake mapear` e conferido contra o que se lê hoje por
`python -m liquidez datalake comparar --data AAAA-MM-DD` (item 11).

Legenda da coluna **Uso**: `rotina` = `rodar_diario.ps1`; `config` = só no
`configurar` / `cotas sincronizar`; `avulso` = scripts da raiz, fora da rotina.

## 1. Carteira — `datalake.dbo.MUL_GLD_CARTEIRA_UNIFICADA` (rotina)

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
| `cdFundo`, `dsOrigem` | — | extraídos, não usados (`dtCarga` só existia no clone do riscogc) |

Valores esperados em `dsBaseOrigem`: ver `config/parametros/produtos.json` e
`grafo_fundo.BASES_COTA`. Rótulo novo cai fora do caixa sem erro.

## 2. Estoque de recebíveis — `datalake.dbo.MUL_GLD_ESTOQUE` (rotina)

Desde 23/10/2026 (`queries.estoque_unificado`); antes, `SELECT *` na
`HOD_TB_ESTOQUE_UNIFICADO` do riscogc, que fica só no `datalake comparar`. A MUL
junta o estoque dos administradores (Singulare, Planner, QiTech) de onde a rotina
do Datalake remonta a HOD (`usp_Carregar_HOD_TB_ESTOQUE_UNIFICADO`, validada
contra o robô em 10/09: 22 de 22 fundos). Tem índice em `dtReferencia` (~125 mil
linhas por dia). O de-para e as regras são os daquela procedure, e os apelidos
devolvem os nomes da HOD:

| HOD (o que o código lê) | MUL_GLD_ESTOQUE | Para que serve |
|---|---|---|
| `CNPJ_Fundo`, `Data_Posicao` | `noDocFundo` (com e sem máscara no `IN`), `dtReferencia` | filtros |
| `ID_Recebivel` | `dsSeuNumero + '_' + dsNuDocumento` | casa com `Codigo_Op` da carteira (estoque próprio do fundo sai da etapa 8) |
| `Valor_Presente` | `vlPresente`; **0** com `dsFaixaPdd` WOM/WOP (write-off, como o robô) | valor do recebível; cota "A Vencer" e pool do próprio fundo |
| `Status_Recebivel` | `dsSituacaoRecebivel` | `VENCIDO` / `A VENCER` sem diferenciar maiúsculas (regra `_estoque` em `regras_vencidos.json`) |
| `Prazo_Atual_Dias_Corridos` | dias de `dtReferencia` a `dtVencimentoOriginal` | faixa de prazo do recebível |
| `CNPJ_Sacado`, `Nome_Sacado` | `noDocSacado`, `dsNomeSacado` | carrego: sacado com título vencido tira todos os seus a vencer do caixa |
| `Tipo_Recebivel`, `FAIXA_PDD` | `dsTipoRecebivel`, `dsFaixaPdd` | diagnóstico; `FAIXA_PDD` deixou de ser filtro em 10/08/2026 |
| `Sistema_Origem` | `dsSistemaOrigem` | auditoria (coluna nova) |

Uma linha por (data, fundo, `ID_Recebivel`): na repetição vence a de
`dhAlteracao` mais recente. As linhas que o robô montava a partir da carteira
(`Origem_Importacao` EstoqueCarteira) e as digitadas (Pré Trade) não existem na
MUL; as primeiras já saíam da etapa 8 como estoque próprio do fundo. A parte 1
do `datalake comparar` mostra, fundo a fundo, o que só uma das fontes tem.

## 3. Passivo (rotina)

### `dbo.HOD_PAS_POSICAO_NOTA` + `dbo.HOD_PAS_CAD_COTISTA` + `dbo.HOD_TB_CADASTRO_FUNDOS` + `dbo.TB_PASSIVO_DISTRIBUIDORES`

Desde 10/09/2026 é montada pela rotina do Datalake a partir da
`MUL_GLD_PASSIVO_CONSOLIDADO`. Na comparação de 15/10 parecia parada em 07/10,
mas era a rotina que não tinha rodado de 10 a 14/10. Em 21/10 estava em 19/10,
igual à consolidada. **Desde 21/10/2026 o projeto lê a consolidada direto**
(subseção abaixo), e desde 23/10 nenhuma consulta lê a `HOD_PAS_*`: o
`passivo comparar`, que punha as duas lado a lado, saiu depois de validar a
troca (rodada 21: consolidada = HOD em todos os fundos em 30/09). A tabela
abaixo descreve a fonte antiga, como referência.

| Tabela.coluna | Para que serve |
|---|---|
| `POSICAO_NOTA.dataPosicao` | filtro da data (o grafo recua para a última ≤ pedida) |
| `POSICAO_NOTA.cdCotistaAdm` | junção com o cotista |
| `POSICAO_NOTA.cdClasseAdm` | junção com `CADASTRO_FUNDOS.idFundoAdm` → classe |
| `POSICAO_NOTA.vlCorrigido` | saldo; soma por cotista e classe |
| `CAD_COTISTA.nome`, `cnpj`, `cpf` | identidade do cotista (`COALESCE(cnpj, cpf)`) |
| `CADASTRO_FUNDOS.CNPJFundo`, `nmFundoInterno`, `nmCotaInterno`, `Situacao` | fundo, classe; filtros `Situacao = 'NORMAL'` e `nmCotaInterno <> 'ORIGINAL'` |
| `DISTRIBUIDORES.DocumentoDistribuidor`, `nmDistribuidora` | distribuidor do cotista (`LEFT JOIN`) |

### `datalake.dbo.MUL_GLD_PASSIVO_CONSOLIDADO` (fonte desde 21/10/2026; na origem do datalake desde 23/10)

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
na carga da HOD. A consolidada tem a collation do datalake: as junções com o
cadastro e os distribuidores (riscogc) usam `COLLATE DATABASE_DEFAULT`. A parte
8 do `datalake mapear` confere se `docFundo` e `dsClasse` da própria
consolidada dariam conta do que hoje vem do cadastro (só agregados por classe).

## 4. Cadastro de fundos e cotas — `dbo.HOD_TB_CADASTRO_FUNDOS` (config + rotina)

`configurar.SQL_CADASTRO`, `queries.cadastro_cotas`, `varcota.CONSULTA_CADASTRO`
(CNPJ e classe da série de cotas). Segue no riscogc: o datalake não tem
cadastro de fundos conhecido (`DLK_DEPARA_FUNDOS` só liga códigos).

| Coluna | Para que serve |
|---|---|
| `CNPJFundo`, `idFundoInterno`, `idFundoAdm` | chaves; `idFundoAdm` casa com `cdFundo` do passivo e com o código de cota da PLANNER; `idFundoInterno` casa com `DLK_DEPARA_FUNDOS.cdFundoInterno` (série de cotas) |
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

## 8. Série de cotas — `datalake.dbo.MUL_GLD_MEC_CONSOLIDADA` (rotina, etapa 8b)

Desde 23/10/2026 (`varcota.CONSULTA`). Até então vinha da `HOD_TB_COTAS_FUNDOS`,
que desde 10/09 só recebia a própria MEC, carregada pela rotina do Datalake
(`usp_Carregar_HOD_TB_COTAS_FUNDOS`) pelo mesmo caminho:
`MEC.idCliente = DLK_DEPARA_FUNDOS.cdCarteira → cdFundoInterno`. CNPJ e classe
saem do cadastro de fundos (item 4) por `cdFundoInterno = idFundoInterno`.

| Coluna | Para que serve |
|---|---|
| `dtReferencia`, `idCliente` | série por carteira; uma linha por data e fundo (vence a de `dhAlteracao` mais recente, como na carga da HOD) |
| `vlCota`, `qtCotas`, `vlPatrimonio` | a linha válida é a que fecha `qtCotas × vlCota = vlPatrimonio` |
| `DLK_DEPARA_FUNDOS.cdCarteira`, `cdFundoInterno` | de-para do datalake (234 linhas; carteira sem par fica de fora e o log conta) |

O que muda em relação à HOD:
- a MEC começa em 13/02/2026 e cobre ~150 carteiras (Singulare, Amplis,
  Minicom); a HOD tinha ~275 fundos/dia até 11/09, com série desde antes;
- só há a cota **patrimonial**: a variante `_IND` (cota indexada), que não cai
  com amortização, parou em 11/09/2026 e não existe no datalake. Sênior e
  mezanino que amortizam voltam a mostrar a amortização como queda;
  `cotas serie` lista esses dias em "Retorno negativo em senior/mezanino".

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
  "DLK Clonar TODOS"). Desde 23/10/2026 a liquidez lê a origem e não depende
  mais deles. A mesma rotina recarrega a HOD de movimentação, que a liquidez
  ainda lê: se ela não roda, a movimentação para. A tarefa roda às 18:30 só
  com o usuário logado ("Interativo apenas"); os logs têm buracos (10 a 14/10,
  17 e 18/10).
- **Collation**: o datalake e o riscogc podem ter collations diferentes. Junção
  de coluna de texto entre os dois bancos precisa de `COLLATE DATABASE_DEFAULT`
  nos dois lados (como em `queries.PASSIVO_JOIN_FUNDO`); junção dentro do mesmo
  banco, não.
- **Defasagem de cada base** (`python -m liquidez atualidade`, em 21/10/2026):
  carteira e cotas no dia (21/10), estoque em D-1 (20/10), passivo,
  movimentação e curva DI1 em D-2 (19/10), Beyond em 15/10. O passivo atrasa
  já na origem do datalake, não no clone. **A data-base que tem todas as bases
  é a menor delas** (19/10 neste dia), e o Beyond defasado só faz a operação
  nova cair na premissa de bullet.

## 11. O que falta trazer para o datalake

`python -m liquidez datalake mapear` levantou o datalake em 23/10/2026 (456
objetos; o mapa completo está na rodada 24). `python -m liquidez datalake
comparar --data AAAA-MM-DD` confere as candidatas contra o que se lê hoje, só
com agregados:

| Ainda no riscogc | Candidata no datalake | O que o `comparar` responde |
|---|---|---|
| `HOD_TB_ESTOQUE_UNIFICADO` | `MUL_GLD_ESTOQUE` (**já em uso**) | parte 1: linhas, valor presente por situação, recebíveis só de um lado, origem e sistema, chave repetida |
| `HOD_TB_CADASTRO_FUNDOS` no passivo | `docFundo`/`dsClasse` do próprio passivo (CNPJ igual em 122 de 122 classes) | parte 2: classes que o filtro do cadastro (`Situacao` NORMAL e `nmCotaInterno` diferente de ORIGINAL) tira da consulta, e o saldo delas |
| `HOD_TB_CADASTRO_FUNDOS` na série de cotas | `DLK_DEPARA_FUNDOS` (`noCnpjDigitos`, `dsCota`) | parte 3: CNPJ e classe do de-para x cadastro; fundos do cadastro local com e sem série |
| `AUD_DIM_FERIADOS` | `DLK_CALENDARIO_B3` (2020–2030, `flDiaUtil`, `flDiaUtilB3`) | parte 4: dias de semana que só um dos dois tem |
| `TB_TAXAS` (CDI) | `CDH_GLD_INDICES_MERCADO` | parte 5: colunas, índices e últimas linhas do CDI x `TB_TAXAS` |
| `HOD_MCT_REU_MOVIMENTACAO_PROCESSADA` | nenhuma MUL; `QIT_GLD_MOVIMENTACAO_FUNDOS_SINGULARE` e as de QiTech, Planner (JCOT), BTG e Minicom | parte 6: RCO de hoje x maior resgate de um dia na Singulare, datas por mês do passivo (de onde a HOD é calculada) e o perfil das outras fontes |
| `TB_LIQUIDEZ_OPERACOES_4`, `TB_CURVA_JUROS_DIARIA`, `TB_PASSIVO_DISTRIBUIDORES`, `AUD_DIM_DEPARA_FUNDOS` | nenhuma | — |

Prazo de resgate, condomínio e ISIN dos fundos (o resto do cadastro) não existem
no datalake, exceto para fundos BTG (`BTG_GLD_FUNDOS_BTG`).
