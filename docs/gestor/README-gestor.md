# Relatório de Liquidez — cadastro de fundos e extração em Python

Substitui as macros VBA de extração da planilha `Relatório de Liquidez_Multiplica_V2.xlsm`
(`Consultar_Estoque_Unificado`, `Consultar_Movimento`, `Consultar_Carteira`,
`Consultar_Beyond` e `CarregarAtivosPassivos`), carregando cada base em `DataFrame`
do pandas e rodando todos os fundos do cadastro em lote.

Cobre o caminho inteiro: extração das bases, tratamento (`Org_dados`), análise da
carteira, indicadores de liquidez e a geração do `Relatorio_Liquidez` em Excel,
um arquivo por fundo. Só a etapa 5 (`CalcDiasFluxos`) segue pendente — a origem
da aba `fluxo` ainda não foi identificada.

## Instalação

```powershell
python -m pip install -r requirements.txt
```

Conexão: SQL Server `10.201.1.6,1433`, banco `riscogc`, autenticação integrada
do Windows (mesmo acesso que o VBA usava). Confira com:

```powershell
python -m liquidez testar-conexao
```

## Cadastro de fundos

Uma ficha JSON por fundo em `config/fundos/<cnpj>.json`, com quatro blocos:

| Bloco | Conteúdo | Quem mantém |
|---|---|---|
| `cvm` | dados do Portal de Dados Abertos da CVM (fundo → classes → subclasses) | **sincronizado**, sobrescrito a cada `sync-cvm` |
| `liquidez` | `pz_resg`, `cc` | você — o sync **nunca** toca |
| `integracao` | `codinome_beyond` (Beyond), `nome_produto_mov` (MOV) | você |
| `controle` | `ativo`, `incluir_no_lote`, `observacoes` | você |

O administrador vigente vem da CVM (`cvm.administrador.nome`). O administrador
que estava na planilha fica guardado em `controle.administrador_planilha_origem`
apenas como histórico.

### Comandos

```powershell
python -m liquidez fundos importar          # carga inicial: Config_Fundos do .xlsm + CVM
python -m liquidez fundos sync-cvm          # atualiza o bloco cvm (mostra o diff e pergunta)
python -m liquidez fundos list --adm QI     # filtros: --adm --tipo --situacao --csv
python -m liquidez fundos show "AMADEUS FIDC"
python -m liquidez fundos add --cnpj 00.000.000/0001-00 --nome "NOVO FIDC" --pz-resg 29 --cc 29
python -m liquidez fundos edit "AMADEUS FIDC" --codinome Amadeus
python -m liquidez fundos remove "X FIDC" --motivo "encerrado"
python -m liquidez fundos validar           # CNPJ, duplicidade, campos, administrador
```

`validar` roda automaticamente antes de qualquer extração e bloqueia se houver erro.

## Regulamentos

`python -m liquidez regulamentos` baixa o regulamento **vigente** de cada fundo e
grava em `regulamentos/<NOME DO FUNDO>/Regulamento <data>.pdf`, com um
`regulamento.json` ao lado listando todas as versões conhecidas e a URL de
origem. Um `_indice.json` na raiz resume a rodada.

Duas fontes, porque nenhuma sozinha resolve:

| Fonte | Cobertura | Papel |
|---|---|---|
| `dados.cvm.gov.br` → `FI/DOC/EVENTUAL` (`TP_DOC = REGUL FDO`) | todos os tipos — FIDC, FII, FIAGRO, FIP e FI/555 | **primária**; traz o link direto do PDF |
| FNET (`fnet.bmfbovespa.com.br`) | só estruturados (56 dos 97 fundos) | confere as entregas dos últimos dias, que ainda não entraram no CSV |

O CSV da CVM é consolidado com alguns dias de atraso — em 27/08/2026 estava em
18/08 enquanto o FNET já mostrava a alteração do MOZART FIDC de 25/08. Para os
estruturados, o próprio `LINK_ARQ` do CSV aponta para o FNET com o mesmo id do
documento, então as duas fontes se deduplicam pela URL.

Não existe marcador de "em vigor": cada alteração é entregue como um regulamento
inteiro e novo, e o vigente é a entrega mais recente por `DT_COMPTC` (a data do
instrumento), com a data de protocolo apenas desempatando. Quando as duas ordens
discordam — regulamento antigo protocolado depois de um mais novo — os **dois**
candidatos são gravados e a ficha é marcada `ambiguo`, em vez de escolher em
silêncio.

```powershell
python -m liquidez regulamentos                          # todos os fundos ativos
python -m liquidez regulamentos --fundo "AMADEUS FIDC"
python -m liquidez regulamentos --adm QI --sem-fnet      # só a CVM: mais rápido
python -m liquidez regulamentos --historico              # também as versões anteriores
```

Os CSVs anuais da CVM ficam em `.cache/cvm/`; o do ano corrente é rebaixado
quando o cache não é de hoje, os fechados ficam guardados. A varredura anda do
ano corrente para trás e para quando todo fundo tem ao menos uma entrega, sem
descer abaixo do ano de constituição de cada um.

## Tabela de prazos (aba `Fundos`)

`config/prazos_ativos.csv` — 180 ativos com o prazo de liquidez usado no
tratamento dos dados. Substitui a aba `Fundos`, que tinha duas tabelas lado a
lado (`A:D` e `K:L`) unificadas em uma só.

| Coluna | Origem | Para que serve |
|---|---|---|
| `codigo_op` | col. A (Base Origem) | casa com `Carteira.Codigo_Op` |
| `codigo_ativo` | col. B (Codigo Fundo) | **chave de busca** — casa com `Carteira.Codigo_Ativo` |
| `cnpj` | col. C | liga ao cadastro quando o ativo é cota de fundo |
| `prazo` | col. D | prazo de liquidez em dias; `999` = sem prazo (fechado) |
| `origem_prazo` | — | `cadastro` (era `VLOOKUP` no Config_Fundos) ou `fixo` (digitado) |
| `observacao` | — | divergências e duplicidades detectadas na importação |

```powershell
python -m liquidez prazos importar              # gera o CSV a partir do .xlsm
python -m liquidez prazos list --sem-prazo      # os 13 ativos sem prazo definido
python -m liquidez prazos show "APOLLO II FIDCNP MZ1"
python -m liquidez prazos set "NAPOLI FIDC" --prazo 29
python -m liquidez prazos sync-cadastro         # realinha os de origem 'cadastro'
python -m liquidez prazos validar
```

Consulta no processamento:

```python
from liquidez.prazos import TabelaPrazos
tabela = TabelaPrazos.carregar()
tabela.prazo_de("APOLLO II FIDCNP MZ1")     # 180
carteira["prazo"] = tabela.aplicar(carteira)  # Series alinhada ao DataFrame
```

`aplicar()` casa **só** pelo `Codigo_Ativo`, como o `Match` do VBA; sem
correspondência o prazo fica nulo e o cálculo cai no `DateDiff` das datas.
Existe o parâmetro `coluna_op="Codigo_Op"` para habilitar o fallback pelo
código da operação, mas ele **muda o resultado**: a coluna A tem códigos
genéricos como `"A"`, que casariam 715 linhas de CPR, CC, Tesouraria e PDD com
prazo 0 indevidamente.

### Decisões tomadas na unificação

- **`A:D` prevalece sobre `K:L`.** Nenhuma macro lia a `K:L`; onde as duas
  divergiam (FGR, LFT, NTNB → 0; TREAS6, TREAS7 → 2; ECOMHIY → 999), o valor
  antigo de `K:L` ficou registrado em `observacao`.
- **`9999` virou `999`** — os dois indicavam fundo fechado (9 registros).
- **13 ativos sem prazo** foram mantidos com o campo vazio e são apontados pelo
  `validar`, em vez de silenciosamente calcularem errado.
- **3 códigos repetidos** (`CROWN FIDC NP`, `FIC SOBERANO RF DI`,
  `GV CASH RF DI LP FI`): a busca usa o primeiro, igual ao `Match`.

## Cronograma das operações

`config/cronograma/<cnpj>_operacoes.csv` e `<cnpj>_parcelas.csv` — o cadastro
contratual de cada operação, lido da planilha de conciliação (`.xlsb`) do fundo:
vencimentos, amortizações, taxas e se é Pré ou CDI + Pré.

**Quem manda é a carteira, não o arquivo.** A lista de operações vem das linhas
do produto (`--produto`, padrão `DC`) com seu `Codigo_Op`, e só as abas
correspondentes são lidas. Aba que a carteira não pediu não é aberta e não entra
no cadastro; `Codigo_Op` sem aba é reportado como pendência. Por isso o
`importar` precisa de uma carga — use `--todas-as-abas` para ler a planilha
inteira quando não houver.

O prefixo da aba não importa — `NC_C227129` e `CCB_C204849` são reconhecidas
igualmente, porque o código é extraído pelo padrão `C` seguido de 3 ou mais
dígitos. A aba tem duas representações do mesmo contrato: o bloco da esquerda é a
grade diária, uma linha por dia, e o da direita é o cronograma, uma linha por
parcela. Usa-se o cronograma. Aba encontrada mas **sem nenhuma parcela** é
reportada e não entra — é cópia esquecida ou modelo em branco.

As colunas do cronograma são **localizadas pelos rótulos do cabeçalho**
(`Vencimento` e `Amortização`, na linha 7), não fixadas por posição: algumas abas
têm uma coluna a mais no bloco da esquerda e o da direita desloca junto. No
LIMAJ, `Vencimento` cai em `S` numa aba e em `T` noutra. A busca exige que
`Amortização` venha depois de `Vencimento`, porque o bloco da esquerda tem uma
coluna `Armotização` — com o erro de digitação da planilha — que não é a do
cronograma.

**Isto é cadastro, não carga**: muda pouco, é mantido por decisão sua e fica em
`config/`, não em `dados/` — que a extração apaga a cada rodada.

### A pasta de entrada

**O jeito normal de atualizar é depositar o arquivo.** Salve a planilha em
`config/planilhas/` com o **CNPJ do fundo no nome** — com ou sem pontuação:

```
config/planilhas/29242877000170 AVANTI.xlsb
config/planilhas/29.243.004-0001-82 Limaj Conciliação.xlsb
```

A rodada diária confere essa pasta antes de tudo. Achando arquivo, reimporta o
cronograma do fundo, guarda a versão anterior do cadastro em
`config/cronograma/_anterior/` e move o arquivo para
`config/planilhas/processados/` com carimbo de data. **Pasta vazia, nada é
tocado** — a planilha só é aberta quando você deposita.

Arquivo sem CNPJ no nome, ou com CNPJ fora do cadastro, **fica na pasta** com
aviso; nada é adivinhado. A rodada continua e termina com código 8.

Depositar uma cópia também resolve o arquivo aberto no Excel: o original
continua aberto, a cópia é lida sem conflito.

```powershell
python -m liquidez cronograma sincronizar --saida C:\Users\seu.usuario\liquidez_dados
python -m liquidez cronograma sincronizar --conferir    # mostra o que faria, não grava nem move
```

### Os demais comandos

```powershell
python -m liquidez cronograma importar --fundo "AVANTI MULTIPLICA FIDC" --planilha "...\AVANTI.xlsb" --saida ...\liquidez_dados
python -m liquidez cronograma importar --fundo AVANTI --conferir   # só mostra o diff
python -m liquidez cronograma list     --fundo AVANTI [--emissor CAPRETZ]
python -m liquidez cronograma show     --fundo AVANTI --codigo C227129
python -m liquidez cronograma validar  --fundo AVANTI --saida C:\Users\seu.usuario\liquidez_dados
```

A planilha **só é aberta quando você manda**. O `importar` compara com o cadastro
gravado e mostra novas operações, removidas, cronograma alterado e taxas
alteradas, gravando apenas após confirmação (`--forcar` pula, `--conferir` nunca
grava). Junto do cadastro fica um `_origem.json` com o SHA-256 do arquivo usado:
quando a planilha muda sem reimportação, os comandos avisam.

`validar` cruza com a carteira e aplica a regra: **toda operação precisa ter aba
na planilha**. Ele separa as duas naturezas de linha, que não podem se
confundir:

| | O que é | Precisa de aba |
|---|---|---|
| `NC*` | a operação | **sim** — sem aba é pendência |
| `VC*` | a parcela que venceu e virou ativo próprio, com `Codigo_Op` novo | não — não é operação |

A distinção vem da regra declarativa do administrador em
`config/regras_vencidos.json`, então vale igual para a PLANNER (`*VENC*`).

## Fluxo futuro dos ativos

```powershell
python -m liquidez fluxo --fundo "AVANTI MULTIPLICA FIDC" --saida C:\Users\seu.usuario\liquidez_dados --por-mes
python -m liquidez fluxo --fundo AVANTI --somente-nao-vencidos --detalhar --salvar
```

Projeta quanto cada operação vai pagar em cada data futura. O ponto de partida é
o **`Valor_Liquido` da carteira na data-base**: ali o ativo já vem precificado
pelo acrual, então esse número *é* o saldo devedor, com os juros incorridos
dentro. Não há passado a reconstruir.

```
dias[i]    = (vencimento[i] − data-base), corridos
fator[i]   = Fator_Spot(dias[i]) × (1 + taxa_ad)^dias[i]
juros[i]   = saldo × (fator[i] / fator[i−1] − 1)
amort[i]   = valor da carteira × peso[i]
saldo     -= amort[i]
```

O peso vem da amortização contratual futura, normalizada — assim a soma das
amortizações **fecha exatamente com o valor da carteira** e o saldo termina em
zero. Os juros entram por cima, então o total das parcelas supera o valor da
carteira: cada uma carrega o juro que ainda vai correr até ela.

A projeção usa só a curva futura (`TB_CURVA_JUROS_DIARIA`, `DI1`, NDC em dias
corridos a partir da data-base). O CDI histórico não entra — o passado já está
no valor da carteira.

Parcela vencida não entra: isto é fluxo **futuro a receber**. A parcela que
venceu já virou ativo próprio na carteira (`VC*`) e é contada lá. A que vence na
**própria data-base entra** — dinheiro chegando no dia é a liquidez máxima, e
ela ainda não virou ativo vencido; com `qt_dias = 0` o fator é 1, então
contribui só com amortização, sem juros.

Dois recortes:

- **sem `--somente-nao-vencidos`** — todas as operações com aba. É a visão de
  vencimentos do ativo, informativa; não altera regra nenhuma.
- **com `--somente-nao-vencidos`** — só as linhas com `Ativo Vencido = NAO`, que
  é o recorte que pode alimentar as faixas de prazo das etapas 3 e 6. Essas
  etapas **não mudam**.

Na carga de 02/07/2026 do AVANTI, as 35 operações de DC projetam R$ 292,05
milhões de amortização e R$ 64,29 milhões de juros. Mas a propagação da marca de
vencido por emissor deixa só 3 operações no segundo recorte, e as três vencem em
abril de 2028 — nada dentro dos 29 dias de cotização.

## Extração

```powershell
# lote de todos os fundos do cadastro
python -m liquidez extrair --data 2026-07-02 --workers 4 --saida C:\Users\seu.usuario\liquidez_dados

# um fundo (DataFrames em memória; --salvar grava em disco)
python -m liquidez extrair --data 2026-07-02 --fundo "AVANTI MULTIPLICA FIDC" --salvar

# recortes
python -m liquidez extrair --data 2026-07-02 --adm PLANNER --bases bd,carteira
```

### Layout da saída

Cada carga gera **6 arquivos, um por base**, com todos os fundos juntos e as
colunas `cnpj_fundo`, `nome_fundo` e `data_posicao_carga` na frente:

```
<saida>/
  bd.parquet  carteira.parquet  db_ativo.parquet
  db_passivo.parquet  mov.parquet  beyond.parquet
  _carga.json     data de posição, linhas e tamanho por base
  _resumo.csv     log por fundo/base: linhas, tempo, status, erro
```

**Uma carga substitui a anterior** — o disco fica fixo em ~13 MB em vez de crescer.
O banco continua sendo a fonte para reprocessar qualquer data.

Para ler uma base já gravada:

```python
from liquidez.extrator import carregar_base
bd = carregar_base(r"C:\Users\seu.usuario\liquidez_dados", "bd")
avanti = carregar_base(r"C:\Users\seu.usuario\liquidez_dados", "bd", "29.242.877/0001-70")
```

Durante o lote a extração grava um arquivo por fundo em `_tmp/`, o que permite
**retomar** se a execução cair no meio; terminando sem erro, tudo é consolidado
e o `_tmp/` é apagado. Se houver erro, o `_tmp/` é mantido e a próxima execução
continua de onde parou (`--forcar` ignora e refaz).

Escolha uma pasta **fora do OneDrive** (sincronização lenta) e **fora de
`AppData\Local`** — este Python é o da Microsoft Store e redireciona escritas
ali para o `LocalCache` do pacote. O comando avisa nos dois casos e imprime o
caminho real ao final.

### Bases e origem

| Base | Aba equivalente | Tabela |
|---|---|---|
| `bd` | BD | `HOD_TB_ESTOQUE_UNIFICADO` |
| `carteira` | Carteira | `HOD_TB_CARTEIRA_UNIFICADA` |
| `db_ativo` | DB_Ativo | `HOD_TB_CARTEIRA_UNIFICADA` (7 colunas) |
| `db_passivo` | DB_Passivo | `HOD_PAS_POSICAO_NOTA` + cotista + cadastro + distribuidores |
| `mov` | MOV | `HOD_MCT_REU_MOVIMENTACAO_PROCESSADA` |
| `beyond` | Beyond | `TB_LIQUIDEZ_OPERACOES_4` |

A base `beyond` filtra pelo **codinome** do fundo e só roda para quem tem
`integracao.codinome_beyond` preenchido — hoje 3 fundos (Multiplica LP, GAD,
Avanti). Os demais são pulados com aviso; conforme for preenchendo o codinome,
passam a ser extraídos.

## Tratamento e análise

```powershell
python -m liquidez tratar --fundo "AVANTI MULTIPLICA FIDC" --saida C:\Users\seu.usuario\liquidez_dados
python -m liquidez tratar --fundo "AVANTI MULTIPLICA FIDC" --detalhar --salvar
```

Roda as etapas do `Org_dados` sobre a carga já extraída e imprime tudo em tela:
carteira e BD tratados, análise da carteira (etapa 6), cotas de outros fundos
(etapa 7), exposição do estoque (etapa 8), o relatório por ativo (etapa 9) e os
indicadores. Com `--salvar`, cada uma dessas tabelas vai para `<saida>/tratado/`.

O corte das faixas e do Caixa Potencial usa o **prazo de cotização** (`cc` do
cadastro), não o `PzResg` da fórmula original — correção confirmada com a área de
risco, que muda o resultado nos 14 fundos em que os dois diferem.

## Relatório em Excel

```powershell
python -m liquidez relatorio --fundo "AVANTI MULTIPLICA FIDC" --saida C:\Users\seu.usuario\liquidez_dados
python -m liquidez relatorio --adm QI --saida-rel C:\Users\seu.usuario\liquidez_relatorios
```

Gera um `.xlsx` por fundo, `Liquidez_<cnpj>_<AAAAMMDD>.xlsx`, com o layout da aba
`Relatorio_Liquidez`: parâmetros, tabela por ativo, Caixa Potencial, ANBIMA,
concentração de cotistas, GDP, distribuidores e RCO, com o enquadramento
colorido. Sem `--fundo`, roda o filtro (`--adm`, `--tipo`) ou o cadastro inteiro,
seguindo com os demais fundos quando um falha e devolvendo código 1 no final.

Fundos **sem carteira e sem estoque na carga são pulados** (`PULA`), não geram
arquivo e não contam como erro. A extração deles volta `ok` com 0 linhas e o
passivo (cotistas, MOV) vem normalmente; sem a ponta ativa o Caixa Potencial
seria zero e todos os indicadores sairiam “Desenquadrado”, o que seria pior do
que não emitir o relatório. Na reextração de 02/07/2026 feita em 29/07 são 2 dos
100 (KAPAMA FIM CP e MANI FIM CP).

Quantos caem nessa situação **depende de quando a carga foi feita**: a mesma data
extraída em 24/07 trazia 3.524 linhas de carteira e deixava 30 fundos de fora;
extraída em 29/07, traz 5.378 e deixa 2. O `riscogc` continua sendo carregado
depois da data de posição, então uma carga velha não é só desatualizada — ela
some com fundos inteiros do relatório.

Os parâmetros dos indicadores ficam em `config/indicadores.json` — percentis
ANBIMA, fator do GDP (2,33), limites de enquadramento e os tipos de movimentação
contados como resgate.

Validado contra a planilha para o AVANTI (02/07/2026): PL, Caixa Potencial,
Caixa/PL, cotistas, GDP, RCO e os 9 horizontes ANBIMA idênticos. A única
diferença é de R$ 0,08 no Caixa Potencial, vinda da truncagem do `Val()` do VBA
em CPR e Tesouraria — o valor do Python é o correto. Os distribuidores, que na
planilha davam `#DIV/0!`, ficam em branco.

## Rodada diária

```powershell
.\rodar_diario.ps1 -Data 2026-07-31
```

Faz a rodada inteira para a data informada: **extrai as bases, processa a pasta
de entrada de planilhas, projeta o fluxo futuro pela curva daquela data e gera os
`.xlsx`**. A data é **obrigatória** — não há cálculo automático de dia útil nem
calendário de feriados.

Fundos e prazos não são refeitos. O cronograma das operações só é reimportado se
você tiver depositado a planilha em `config/planilhas/` (ver acima). Se aparecer
`Codigo_Op` novo na carteira sem aba, a rodada avisa e termina com código 7.

| Parâmetro | Padrão | Para que serve |
|---|---|---|
| `-Data` | — | data de posição, `AAAA-MM-DD` (obrigatória) |
| `-Dados` | `C:\Users\<você>\liquidez_dados` | pasta da carga |
| `-Relatorios` | `C:\Users\<você>\liquidez_relatorios` | os `.xlsx` vão para uma subpasta por data |
| `-Fluxos` | `C:\Users\<você>\liquidez_fluxos` | os CSV de fluxo, também em subpasta por data |
| `-Bi` | `C:\Users\<você>\liquidez_bi` | raiz da camada de fatos |
| `-Logs` | `C:\Users\<você>\liquidez_logs` | um log por execução |
| `-Workers` | 4 | conexões simultâneas na extração |
| `-SomenteRelatorio` | — | reaproveita a carga que já está lá e só refaz fluxo e `.xlsx` |
| `-PularFluxo` | — | não projeta o fluxo |
| `-PularFatos` | — | não grava a camada de BI |

A camada de fatos é gravada **depois** dos relatórios, de propósito: o `.xlsx` é
o que se mostra hoje, então não espera pelo BI. Se a gravação dos fatos falhar,
os relatórios já saíram e a rodada termina com código 9.

**A carga boa é protegida.** O `extrair` apaga a carga anterior antes de
consolidar a nova, então uma rodada em feriado ou dia sem posição deixaria a
pasta vazia. O script extrai para `<Dados>.novo`, confere (data confere, zero
erros, carteira ou BD com linhas) e só então substitui — guardando a carga
trocada em `<Dados>.anterior`. Se a conferência falhar, nada é substituído.

Códigos de saída: `0` ok · `2` falha na extração · `3` carga inconsistente ·
`4` sem dado para a data (feriado) · `5` algum fundo falhou na geração ·
`6` falha na projeção do fluxo · **`7` rodou tudo, mas há `Codigo_Op` novo sem
aba na planilha** — deposite a planilha atualizada · `8` sobrou arquivo na pasta
de entrada sem o CNPJ no nome.

Os códigos 6, 7 e 8 não interrompem a rodada: os relatórios saem do mesmo jeito.
Nem a projeção do fluxo nem a pasta de entrada derrubam a execução se falharem —
sem acesso ao banco, por exemplo, o fluxo reporta o erro por fundo e a rodada
segue para os relatórios.

Uma rodada completa dos 100 fundos leva cerca de 4 minutos; a projeção do fluxo
acrescenta poucos segundos.

### Agendar no Windows

Como a data é sempre informada, a tarefa agendada carrega uma data fixa que
precisa ser editada — ou você chama o script à mão. Para registrar:

```powershell
schtasks /Create /TN "Liquidez diario" /SC DAILY /ST 07:00 /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:\caminho\Python\rodar_diario.ps1\" -Data 2026-07-31"
```

A conexão com o `riscogc` é por autenticação integrada do Windows, então a tarefa
precisa rodar **com o seu usuário e com “executar somente se conectado”** — na
opção “executar estando o usuário conectado ou não” a credencial de rede não vai
junto e a extração falha.

## Camada de fatos para BI

```powershell
python -m liquidez fatos gerar  --saida C:\Users\seu.usuario\liquidez_dados --destino C:\Users\seu.usuario\liquidez_bi
python -m liquidez fatos resumo --destino C:\Users\seu.usuario\liquidez_bi
```

Os `.xlsx` são apresentação e a carga é substituída a cada rodada — **nada
acumula**. Sem histórico não dá para perguntar como o Caixa Potencial de um fundo
evoluiu. Esta camada grava, a cada rodada, as mesmas grandezas já calculadas, em
formato tidy particionado por data:

```
<bi>/fato_indicadores/data=2026-07-28/dados.parquet
<bi>/fato_fluxo/data=2026-07-28/dados.parquet
<bi>/dim_fundo/dados.parquet
```

| Tabela | Grão | Origem |
|---|---|---|
| `fato_indicadores` | fundo × data | indicadores (PL, caixa, cotistas, GDP, RCO) |
| `fato_anbima` | fundo × data × horizonte | enquadramento por horizonte |
| `fato_relatorio` | fundo × data × ativo | etapa 9 |
| `fato_matriz` | fundo × data × produto × faixa | etapas 6 e 8 — a escada de liquidez |
| `fato_fluxo` | fundo × data × operação × parcela | projeção do fluxo futuro |
| `dim_fundo` | fundo | cadastro |
| `dim_operacao` | operação | cronograma |

**Nada é recalculado aqui.** Tudo vem das mesmas etapas que alimentam o `.xlsx`,
então mudança de regra no Python aparece no BI junto — é o motivo de o BI não
reimplementar as etapas em DAX.

Particionar por pasta é o formato que o Power BI lê nativamente e permite
recarregar uma data sem tocar nas demais. Volume é pequeno: **~120 KB por dia**,
algo como 30 MB por ano.

Dois cuidados embutidos no dado:

- `fato_matriz` **não grava zeros**. A matriz nasce preenchida (uma faixa por dia
  até o prazo, por produto) e guardá-la inteira daria 301 mil linhas por dia
  contra 2.958 úteis. Na escada de liquidez, ausência é zero.
- A matriz traz totais como se fossem mais um produto e mais uma faixa. A coluna
  **`e_total`** marca essas linhas — sem filtrá-la, um `SUM(valor)` por fundo
  conta em dobro.

As dimensões são retrato do agora, sobrescritas a cada rodada; os fatos são série.

### Reprocessamento

Rodar a mesma data de novo **substitui a partição daquela data** — não duplica.

Reprocessar **um fundo só** (`--fundo`) troca apenas as linhas dele e **preserva
os demais** daquela data:

```
fato_indicadores/2026-07-28: 96 linha(s) de outros fundos preservadas
```

Isso vale para todas as tabelas de fato, inclusive `fato_fluxo`. A chave é
`cnpj_fundo`.

Uma ressalva: se um fundo deixar de ser processado — sai do cadastro, ou passa a
não ter carteira —, as linhas dele **continuam** na partição das datas antigas.
É o correto para histórico, mas quer dizer que uma partição pode conter fundo que
o `dim_fundo` de hoje não tem.

## Documento técnico dos tratamentos

```powershell
python -m liquidez documentar          # gera docs/tratamentos.html
```

`docs/tratamentos.html` descreve as 9 etapas do tratamento (as que a rotina VBA
`GeraRel` executava) e os indicadores de liquidez: o que cada um faz, a regra por
administrador, as diferenças conscientes em relação ao VBA e o estado de
implementação de cada etapa.

O documento é **gerado a partir do próprio código** e dos arquivos de
configuração — registro de regras, listas de ativos excluídos, tabela de prazos e
cadastro de fundos. Não editar à mão: qualquer alteração se perde na próxima
geração. **Ao criar ou mudar qualquer regra de tratamento, regere o documento na
mesma entrega**, para ele não sair de sincronia.

É um HTML autocontido (sem dependência externa), que se adapta ao tema claro ou
escuro do navegador.

## Conferência com a planilha

```powershell
python conferir.py --data 2026-07-02 --fundo "AVANTI MULTIPLICA FIDC" --saida C:\Users\seu.usuario\liquidez_dados
```

Compara linhas, nomes de colunas e a soma da coluna-chave de cada base contra as
abas do `.xlsm`. As abas guardam o resultado da última execução das macros, então
divergência costuma significar planilha desatualizada, não erro de extração.

## Diferenças em relação ao VBA

- Consultas com **parâmetros vinculados** no lugar da concatenação de string
  (o VBA quebrava com apóstrofo no nome do fundo).
- `MsgBox` virou log, permitindo execução automática.
- `MOV` continua sem filtro de data, igual ao VBA (filtra só por `nome_produto`).
- O administrador passa a vir da CVM: os fundos que a planilha marcava como
  SINGULARE aparecem como **QI CORRETORA**. Em `liquidez/regras_adm.py` a regra
  está registrada sob QI com `SINGULARE` como alias — o tratamento em si não mudou.

## Estrutura

```
liquidez/
  cadastro.py        fichas JSON, modelo Fundo, validação
  cvm.py             download e parse do cadastro da CVM
  db.py              conexão pyodbc (uma por thread)
  queries.py         as 6 consultas
  regras_adm.py      regras por administrador
  regras_vencidos.py identificação dos ativos vencidos (declarativa)
  prazos.py          tabela de prazos de liquidez por ativo
  extrator.py        extração por fundo e em lote
  tratamentos.py     etapas 1–4 do Org_dados
  analise.py         etapas 6–9: análise, outros fundos, estoque, relatório
  indicadores.py     Caixa Potencial, cotistas, GDP, distribuidores, RCO, ANBIMA
  relatorio_excel.py geração do .xlsx por fundo
  documentacao.py    gerador do docs/tratamentos.html
  cli.py             linha de comando
conferir.py          conferência contra a planilha
config/fundos/       as fichas
config/*.json|csv    prazos, ativos excluídos, regras de vencidos, indicadores
```
