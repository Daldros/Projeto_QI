# Prompt para o Cursor — projeto Risco de Liquidez

Mesmo fluxo do Datalake: o Claude trabalha na branch `claude/epic-lamport-knours`
e empurra; o Cursor mergeia na `main`, roda na máquina do Mateus e relata.

> **Regra fixa: o Cursor NÃO altera código.** Problema = PARAR e devolver a
> saída inteira. O código roda SÓ por `python -m liquidez ...`. Rode no **cmd**.

## Rodada 1 (05/10/2026) — primeira execução: ambiente, conexão e `configurar` (já preenchido)

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus
(C:\Users\mateus.castagnet\OneDrive - PERPETUO CONSULTORIA ESPECIALIZADA LTDA\Documentos\Projetos GIT\Risco-de-Liquidez---Multiplica-Fidc).

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. Nenhum passo grava no banco: o projeto só LÊ o riscogc.
O passo 9 grava arquivos na pasta config/ do repositório (eles ficam fora do
git, é esperado que `git status` mostre a pasta como ignorada).

REGRA ABSOLUTA: você NÃO pode alterar código. O único commit permitido é o
merge do passo 2. Qualquer erro: PARE e me devolva a saída inteira. Um passo
de cada vez. Rode no cmd (não PowerShell).

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git push origin main
4. python -m venv .venv            (ignore se já existir)
5. .venv\Scripts\pip install -r requirements.txt
6. .venv\Scripts\python -m pytest -q          (esperado: 33 passed)
7. .venv\Scripts\python -m liquidez testar-conexao
   (tem de mostrar 10.101.1.18 e o banco riscogc; se mostrar riscogchml, PARE)
8. .venv\Scripts\python -m liquidez configurar > "%USERPROFILE%\mapa_banco\liq_configurar_simulacao.txt" 2>&1
   (só simula: nada gravado)
9. .venv\Scripts\python -m liquidez configurar --aplicar > "%USERPROFILE%\mapa_banco\liq_configurar.txt" 2>&1
10. .venv\Scripts\python -m liquidez cotas sincronizar > "%USERPROFILE%\mapa_banco\liq_cotas.txt" 2>&1
11. .venv\Scripts\python -m liquidez fundos validar > "%USERPROFILE%\mapa_banco\liq_fundos_validar.txt" 2>&1
12. .venv\Scripts\python -m liquidez prazos validar > "%USERPROFILE%\mapa_banco\liq_prazos_validar.txt" 2>&1
13. dir config /s /b > "%USERPROFILE%\mapa_banco\liq_config_dir.txt"

Me devolva o hash da main, o pytest e os 6 arquivos .txt ANEXADOS INTEIROS
(não filtre, não resuma). Se algum passo falhar, mande o que apareceu e PARE.
```

## Rodada 2 (05/10/2026) — primeiro cálculo de ponta a ponta: MULTIPLICA FIDC em 30/09 (já preenchido; parou no passo 6: datetimeoffset)

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus
(C:\Users\mateus.castagnet\OneDrive - PERPETUO CONSULTORIA ESPECIALIZADA LTDA\Documentos\Projetos GIT\Risco-de-Liquidez---Multiplica-Fidc).

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. O projeto só LÊ o riscogc; grava apenas arquivos locais
(config/, dados/, fluxo/, bi/, painel.html — todos fora do git).

REGRA ABSOLUTA: você NÃO pode alterar código. O único commit permitido é o
merge do passo 2. Se um passo falhar, PARE ali, me devolva a saída inteira
(inclusive o traceback) e NÃO tente os seguintes. Um passo de cada vez; não
repita comando que ainda está rodando. Rode no cmd (não PowerShell).

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git push origin main
4. .venv\Scripts\python -m pytest -q          (esperado: 33 passed)
5. .venv\Scripts\python -m liquidez configurar --aplicar > "%USERPROFILE%\mapa_banco\liq2_configurar.txt" 2>&1
   (refaz as fichas com os codinomes do Beyond; deve dizer "Fichas mantidas 16")
6. .venv\Scripts\python -m liquidez extrair --data 2026-09-30 --fundo "MULTIPLICA FIDC" --salvar > "%USERPROFILE%\mapa_banco\liq2_extrair.txt" 2>&1
7. .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA FIDC" --salvar > "%USERPROFILE%\mapa_banco\liq2_tratar.txt" 2>&1
8. .venv\Scripts\python -m liquidez cronograma beyond --detalhar > "%USERPROFILE%\mapa_banco\liq2_beyond.txt" 2>&1
9. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --salvar --fatos --detalhar > "%USERPROFILE%\mapa_banco\liq2_fluxo.txt" 2>&1
10. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA FIDC" --verboso > "%USERPROFILE%\mapa_banco\liq2_fatos.txt" 2>&1
11. .venv\Scripts\python -m liquidez cotas serie --ate 2026-09-30 > "%USERPROFILE%\mapa_banco\liq2_cotas_serie.txt" 2>&1
12. .venv\Scripts\python -m liquidez painel > "%USERPROFILE%\mapa_banco\liq2_painel.txt" 2>&1
13. dir bi /s /b > "%USERPROFILE%\mapa_banco\liq2_bi_dir.txt"  &  dir *.html /s /b >> "%USERPROFILE%\mapa_banco\liq2_bi_dir.txt"

Me devolva o hash da main, o pytest e o CONTEÚDO dos arquivos
%USERPROFILE%\mapa_banco\liq2_*.txt colados na resposta (não resuma; se um
for grande demais, mande as 60 primeiras e as 60 últimas linhas). NÃO envie
painel.html nem os arquivos de bi/ (têm dados de cotistas).
```

## Rodada 3 (05/10/2026) — repete a rodada 2 com o conversor de datetimeoffset (já preenchido; parou no passo 6: parquet da matriz)

A rodada 2 parou no `extrair` ("ODBC SQL type -155 is not yet supported": a
coluna data_processamento da movimentação). Corrigido em liquidez/db.py.
Mesmos passos; os passos 1–5 já feitos não precisam ser repetidos além do merge.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. O projeto só LÊ o riscogc; grava apenas arquivos locais
(fora do git).

REGRA ABSOLUTA: você NÃO pode alterar código. O único commit permitido é o
merge do passo 2. Se um passo falhar, PARE ali, me devolva a saída inteira
(inclusive o traceback) e NÃO tente os seguintes. Um passo de cada vez; não
repita comando que ainda está rodando. Rode no cmd (não PowerShell).

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git push origin main
4. .venv\Scripts\python -m pytest -q          (esperado: 34 passed)
5. .venv\Scripts\python -m liquidez extrair --data 2026-09-30 --fundo "MULTIPLICA FIDC" --salvar --forcar > "%USERPROFILE%\mapa_banco\liq3_extrair.txt" 2>&1
6. .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA FIDC" --salvar > "%USERPROFILE%\mapa_banco\liq3_tratar.txt" 2>&1
7. .venv\Scripts\python -m liquidez cronograma beyond --detalhar > "%USERPROFILE%\mapa_banco\liq3_beyond.txt" 2>&1
8. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --salvar --fatos --detalhar > "%USERPROFILE%\mapa_banco\liq3_fluxo.txt" 2>&1
9. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA FIDC" --verboso > "%USERPROFILE%\mapa_banco\liq3_fatos.txt" 2>&1
10. .venv\Scripts\python -m liquidez cotas serie --ate 2026-09-30 > "%USERPROFILE%\mapa_banco\liq3_cotas_serie.txt" 2>&1
11. .venv\Scripts\python -m liquidez painel > "%USERPROFILE%\mapa_banco\liq3_painel.txt" 2>&1
12. dir bi /s /b > "%USERPROFILE%\mapa_banco\liq3_bi_dir.txt"  &  dir *.html /s /b >> "%USERPROFILE%\mapa_banco\liq3_bi_dir.txt"

Me devolva o hash da main, o pytest e o CONTEÚDO dos arquivos
%USERPROFILE%\mapa_banco\liq3_*.txt colados na resposta (não resuma; se um
for grande demais, mande as 60 primeiras e as 60 últimas linhas). NÃO envie
painel.html nem os arquivos de bi/ (têm dados de cotistas).
```

## Rodada 4 (05/10/2026) — continua do `tratar` (já preenchido; parou no passo 7: escopo bullet)

A rodada 3 extraiu e calculou tudo (indicadores saíram), mas o `tratar
--salvar` falhou ao gravar a matriz de faixas em parquet (coluna com texto e
números). Corrigido em liquidez/extrator.py. A extração já está em dados/.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. O projeto só LÊ o riscogc; grava apenas arquivos locais
(fora do git).

REGRA ABSOLUTA: você NÃO pode alterar código. O único commit permitido é o
merge do passo 2. Se um passo falhar, PARE ali, me devolva a saída inteira
(inclusive o traceback) e NÃO tente os seguintes. Um passo de cada vez; não
repita comando que ainda está rodando. Rode no cmd (não PowerShell).

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git push origin main
4. .venv\Scripts\python -m pytest -q          (esperado: 35 passed)
5. .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA FIDC" --salvar > "%USERPROFILE%\mapa_banco\liq4_tratar.txt" 2>&1
6. .venv\Scripts\python -m liquidez cronograma beyond --detalhar > "%USERPROFILE%\mapa_banco\liq4_beyond.txt" 2>&1
7. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --salvar --fatos --detalhar > "%USERPROFILE%\mapa_banco\liq4_fluxo.txt" 2>&1
8. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA FIDC" --verboso > "%USERPROFILE%\mapa_banco\liq4_fatos.txt" 2>&1
9. .venv\Scripts\python -m liquidez cotas serie --ate 2026-09-30 > "%USERPROFILE%\mapa_banco\liq4_cotas_serie.txt" 2>&1
10. .venv\Scripts\python -m liquidez painel > "%USERPROFILE%\mapa_banco\liq4_painel.txt" 2>&1
11. dir bi /s /b > "%USERPROFILE%\mapa_banco\liq4_bi_dir.txt"  &  dir *.html /s /b >> "%USERPROFILE%\mapa_banco\liq4_bi_dir.txt"

Me devolva o hash da main, o pytest e o CONTEÚDO dos arquivos
%USERPROFILE%\mapa_banco\liq4_*.txt colados na resposta (do liq4_tratar.txt
basta o fim, a partir de "gravado:"; dos outros, tudo; se um for grande
demais, as 60 primeiras e as 60 últimas linhas). NÃO envie painel.html nem os
arquivos de bi/ (têm dados de cotistas).
```

## Rodada 5 (05/10/2026) — continua do `fluxo` (já preenchido)

A rodada 4 parou no fluxo: o administrador do MULTIPLICA FIDC vem do cadastro
do riscogc como "SINGULARE" e o escopo bullet só reconhecia "QI CORRETORA" (nome
da CVM); o fundo caía fora e o fluxo exigia planilha. Corrigido (alias).

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. O projeto só LÊ o riscogc; grava apenas arquivos locais
(fora do git).

REGRA ABSOLUTA: você NÃO pode alterar código. O único commit permitido é o
merge do passo 2. Se um passo falhar, PARE ali, me devolva a saída inteira
(inclusive o traceback) e NÃO tente os seguintes. Um passo de cada vez; não
repita comando que ainda está rodando. Rode no cmd (não PowerShell).

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git push origin main
4. .venv\Scripts\python -m pytest -q          (esperado: 36 passed)
5. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --salvar --fatos --detalhar > "%USERPROFILE%\mapa_banco\liq5_fluxo.txt" 2>&1
   (pode terminar com código 7 ou 11 = "concluído com pendência": NÃO é erro, siga)
6. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA FIDC" --verboso > "%USERPROFILE%\mapa_banco\liq5_fatos.txt" 2>&1
7. .venv\Scripts\python -m liquidez cotas serie --ate 2026-09-30 > "%USERPROFILE%\mapa_banco\liq5_cotas_serie.txt" 2>&1
8. .venv\Scripts\python -m liquidez painel > "%USERPROFILE%\mapa_banco\liq5_painel.txt" 2>&1
9. dir bi /s /b > "%USERPROFILE%\mapa_banco\liq5_bi_dir.txt"  &  dir *.html /s /b >> "%USERPROFILE%\mapa_banco\liq5_bi_dir.txt"

Me devolva o hash da main, o pytest e o CONTEÚDO dos arquivos
%USERPROFILE%\mapa_banco\liq5_*.txt colados na resposta (se um for grande
demais, as 60 primeiras e as 60 últimas linhas). NÃO envie painel.html nem os
arquivos de bi/ (têm dados de cotistas).
```

## Rodada 6 (05/10/2026) — diagnóstico: o que a carteira do riscogc chama de DC

A rodada 5 rodou o pipeline inteiro (fluxo → fatos → cotas → painel), mas o
`fluxo` não projetou nada: ele seleciona as operações por `Base_Origem == "DC"`
e, no riscogc, a `HOD_TB_CARTEIRA_UNIFICADA` só traz "Renda Fixa" e
"TESOURARIA". Sem operação de DC, o Beyond também não tem código para casar
(0 operações). Antes de trocar o filtro, o comando novo `carteira resumo` lê a
carteira já extraída (arquivo local, sem banco) e mostra Base_Origem x
Modalidade com exemplos de código, valor e status.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. Nenhum passo acessa o banco; só lê arquivos locais.

REGRA ABSOLUTA: você NÃO pode alterar código. O único commit permitido é o
merge do passo 2. Se um passo falhar, PARE ali, me devolva a saída inteira
(inclusive o traceback) e NÃO tente os seguintes. Um passo de cada vez; não
repita comando que ainda está rodando. Rode no cmd (não PowerShell).

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git push origin main
4. .venv\Scripts\python -m pytest -q          (esperado: 40 passed)
5. .venv\Scripts\python -m liquidez carteira resumo --fundo "MULTIPLICA FIDC" --exemplos 5 > "%USERPROFILE%\mapa_banco\liq6_carteira.txt" 2>&1

Me devolva o hash da main, o pytest e o CONTEÚDO de
%USERPROFILE%\mapa_banco\liq6_carteira.txt colado inteiro na resposta (não tem
dado de cotista: é composição da carteira por tipo de ativo).
```

## Rodada 7 (05/10/2026) — de-para de produto: a "Renda Fixa" do riscogc é o "DC"

O `carteira resumo` (rodada 6) mostrou o que o riscogc faz com as operações de
direitos creditórios do MULTIPLICA FIDC: 384 linhas, R$ 864 mi, todas com
`Codigo_Op` C###### (MEZAN, SRP, NCPX, PDDNC, VCNC, CCEXP…), gravadas como
`Base_Origem = "Renda Fixa"` (modalidades CDI, PRE, DOL MTM). O código do gestor
procura essas linhas como "DC". Entrou um de-para parametrizado em
`config/parametros/produtos.json`, aplicado no início do `tratar_fundo` (todos
os comandos passam por ele): "Renda Fixa" + Codigo_Op casando `^C\d{4,}$` → "DC";
o rótulo original fica em `Base_Origem_Fonte`. Título público ou qualquer linha
sem código de operação continua "Renda Fixa". Com isso o fluxo volta a ter o que
projetar e o Beyond passa a ter códigos para casar.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. O projeto só LÊ o riscogc (passos 6 e 7); grava apenas
arquivos locais (fora do git).

REGRA ABSOLUTA: você NÃO pode alterar código. O único commit permitido é o
merge do passo 2. Se um passo falhar, PARE ali, me devolva a saída inteira
(inclusive o traceback) e NÃO tente os seguintes. Um passo de cada vez; não
repita comando que ainda está rodando. Rode no cmd (não PowerShell). Use
caminho absoluto na redireção (C:\Users\mateus.castagnet\mapa_banco\...).
Só passe ao passo 5 se o passo 4 mostrar 44 passed.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git push origin main
4. .venv\Scripts\python -m pytest -q          (esperado: 44 passed)
5. .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq7_tratar.txt 2>&1
6. .venv\Scripts\python -m liquidez cronograma beyond --detalhar > C:\Users\mateus.castagnet\mapa_banco\liq7_beyond.txt 2>&1
7. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --salvar --fatos --detalhar > C:\Users\mateus.castagnet\mapa_banco\liq7_fluxo.txt 2>&1
   (pode terminar com código 7 ou 11 = "concluído com pendência": NÃO é erro, siga)
8. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq7_fatos.txt 2>&1
9. .venv\Scripts\python -m liquidez painel > C:\Users\mateus.castagnet\mapa_banco\liq7_painel.txt 2>&1

Me devolva o hash da main, o pytest e o CONTEÚDO dos arquivos
C:\Users\mateus.castagnet\mapa_banco\liq7_*.txt colados na resposta (do
liq7_tratar.txt basta o fim, a partir de "Fundo ....."; do liq7_beyond.txt e do
liq7_fluxo.txt, se forem grandes, as 80 primeiras e as 80 últimas linhas; dos
outros, tudo). NÃO envie painel.html nem os arquivos de bi/ (têm dados de
cotistas).
```

## Rodada 8 (05/10/2026) — por que o Beyond não casa nada

A rodada 7 destravou o fluxo (328 operações de DC projetadas, R$ 769,8 mi),
mas `cronograma beyond` terminou com "0 capturadas; 0 na fila" sem imprimir
linha de fundo — ou seja, a consulta de candidatas na carteira (Codigo_Ativo
NCPX/CCBPX com Custo_Total > 0) voltou vazia, antes mesmo de tentar o Beyond.
O comando agora imprime uma linha por fundo e, com `--detalhar`, conta as duas
pontas quando não há candidata: carteira (NCPX/CCBPX, Custo_Total nulo/zero,
Data_Aquisicao nula, 5 exemplos) e Beyond (linhas, operações, Grupo_Produto,
formato de Data_Operacao). Só SELECT.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. O passo 5 só LÊ o riscogc (SELECT); grava apenas arquivo
local (fora do git).

REGRA ABSOLUTA: você NÃO pode alterar código. O único commit permitido é o
merge do passo 2. Se um passo falhar, PARE ali, me devolva a saída inteira
(inclusive o traceback). Um passo de cada vez. Rode no cmd (não PowerShell),
com o padrão de aspas que funcionou na rodada 7 e caminho absoluto na
redireção. Só passe ao passo 5 se o passo 4 mostrar 44 passed.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git push origin main
4. .venv\Scripts\python -m pytest -q          (esperado: 44 passed)
5. .venv\Scripts\python -m liquidez cronograma beyond --fundo "MULTIPLICA FIDC" --detalhar > C:\Users\mateus.castagnet\mapa_banco\liq8_beyond.txt 2>&1
   (código de saída 2 = "sobrou fila": NÃO é erro)

Me devolva o hash da main, o pytest e o CONTEÚDO de
C:\Users\mateus.castagnet\mapa_banco\liq8_beyond.txt colado inteiro (se passar
de 150 linhas, as 80 primeiras e as 60 últimas). Não tem dado de cotista.
```

## Rodada 9 (05/10/2026) — Beyond: diagnóstico na posição certa

A rodada 8 mostrou duas coisas: (1) um erro meu no diagnóstico (`not linhas` em
DataFrame → ValueError), corrigido; (2) o comando usa a posição MAIS RECENTE da
carteira (02/10), não a que extraímos (30/09) — e em 02/10 o MULTIPLICA FIDC
teve 0 candidatas NCPX/CCBPX. Agora rodamos com `--data 2026-09-30` (a posição
do fluxo) e sem data, para comparar. Só SELECT.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. Os passos 6 e 7 só LEEM o riscogc (SELECT); gravam apenas
arquivo local (fora do git). Nenhum passo altera código; o único commit
permitido é o merge do passo 2. Rode no cmd, com o padrão de aspas da rodada 7
e caminho absoluto na redireção.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git log --oneline -8 | findstr "beyond: diagnostico"   (se não mostrar nada, PARE)
4. .venv\Scripts\python -m pytest -q          (esperado: 44 passed)
5. git push origin main
6. .venv\Scripts\python -m liquidez cronograma beyond --fundo "MULTIPLICA FIDC" --data 2026-09-30 --detalhar > C:\Users\mateus.castagnet\mapa_banco\liq9_beyond_3009.txt 2>&1
7. .venv\Scripts\python -m liquidez cronograma beyond --fundo "MULTIPLICA FIDC" --detalhar > C:\Users\mateus.castagnet\mapa_banco\liq9_beyond_atual.txt 2>&1
   (código de saída 2 em 6 ou 7 = "sobrou fila": NÃO é erro)

Me devolva a saída literal do passo 3 e do passo 4 e o CONTEÚDO inteiro dos
dois arquivos liq9_*.txt (se um passar de 150 linhas, as 100 primeiras e as 40
últimas). Não tem dado de cotista.
```

## Rodada 10 (05/10/2026) — Beyond casou; fluxo com cronograma real

A rodada 9 (ainda com o código antigo — o merge de b78e442 não entrou) provou
a ponte: com `--data 2026-09-30`, 57 candidatas NC/CCB, **50 capturadas** do
Beyond ("Multiplica LP"), 7 na fila; gravou `config/cronograma/beyond/23216398000101.json`.
Na posição mais recente do banco (02/10) a carteira está incompleta (0 NC/CCB),
por isso o comando passa a usar por padrão a data da carga local — a mesma do
fluxo. O `--detalhar` corrigido mostra o motivo das 7 da fila.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. O passo 6 só LÊ o riscogc (SELECT); os demais leem arquivos
locais. Nenhum passo altera código; o único commit permitido é o merge do
passo 2. Rode no cmd, com o padrão de aspas da rodada 7 e caminho absoluto na
redireção. Se o passo 3 não mostrar a linha pedida, PARE.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git log --oneline -10 | findstr "carga local"   (precisa mostrar 1 linha)
4. .venv\Scripts\python -m pytest -q          (esperado: 44 passed)
5. git push origin main
6. .venv\Scripts\python -m liquidez cronograma beyond --fundo "MULTIPLICA FIDC" --detalhar > C:\Users\mateus.castagnet\mapa_banco\liq10_beyond.txt 2>&1
   (código de saída 2 = "sobrou fila": NÃO é erro; as 50 já capturadas não são refeitas)
7. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --salvar --fatos --detalhar > C:\Users\mateus.castagnet\mapa_banco\liq10_fluxo.txt 2>&1
   (código 2, 7 ou 11 = "concluído com pendência": NÃO é erro)
8. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq10_fatos.txt 2>&1
9. .venv\Scripts\python -m liquidez painel > C:\Users\mateus.castagnet\mapa_banco\liq10_painel.txt 2>&1

Me devolva a saída literal dos passos 3 e 4 e o CONTEÚDO dos arquivos
liq10_*.txt: beyond e fatos inteiros; do fluxo, as 60 primeiras linhas e as 40
últimas; do painel, tudo. NÃO envie painel.html nem bi/ (dados de cotistas).
```

## Rodada 11 (05/10/2026) — divergências eram dia útil; inventário do Beyond

Rodada 10: 50 cronogramas do Beyond no fluxo, "a conferir" 306 → 256, VL de DC
R$ 129,48 mi. As 13 "divergências de vencimento" eram todas o Beyond rolando a
data para o próximo dia útil (carnaval 08–09/02/2027, 12/10, Finados, sábados)
enquanto a carteira guarda a data do documento — o fluxo agora reconhece isso e
não acusa. Para decidir se a captura pode ir além de NC/CCB (57 de 328
operações), o comando ganhou `--inventario`: lista família x produto que o
Beyond tem para o fundo. Só SELECT.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. O passo 6 só LÊ o riscogc (SELECT); o 7 lê arquivos locais.
Nenhum passo altera código; o único commit permitido é o merge do passo 2.
Rode no cmd, com o padrão de aspas da rodada 7 e caminho absoluto na
redireção. Se o passo 3 não mostrar a linha pedida, PARE.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git log --oneline -10 | findstr "inventario"   (precisa mostrar 1 linha)
4. .venv\Scripts\python -m pytest -q          (esperado: 46 passed)
5. git push origin main
6. .venv\Scripts\python -m liquidez cronograma beyond --fundo "MULTIPLICA FIDC" --inventario > C:\Users\mateus.castagnet\mapa_banco\liq11_inventario.txt 2>&1
7. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --salvar --fatos > C:\Users\mateus.castagnet\mapa_banco\liq11_fluxo.txt 2>&1
   (código 2, 7 ou 11 = "concluído com pendência": NÃO é erro)

Me devolva a saída literal dos passos 3 e 4, o liq11_inventario.txt inteiro e,
do liq11_fluxo.txt, as 40 primeiras linhas. Não tem dado de cotista.
```

## Rodada 12 (05/10/2026) — o estoque (BD) cobre as operações que estão em bullet?

O inventário do Beyond (rodada 11) mostrou que "Multiplica LP" tem 60 operações
NC (já capturadas 50) e o resto é recebível pulverizado (BOLETO/Duplicata 361
ops, CLEAN 300+, ANCORA, ESCROW, EXPORTAÇÃO) — famílias que na carteira do
administrador aparecem agregadas por cedente (DIRPX, SRP, MEZAN, CONFD), uma
operação por contrato, e não casam por data+valor. Mas o gestor já tinha
medido no GAD que `Codigo_Op` da carteira = `ID_Recebivel` do estoque: o
cronograma dessas operações pode vir do próprio BD (Data_Vencimento,
Valor_Presente de cada recebível). Antes de ligar isso ao fluxo, o comando novo
`carteira cobertura-estoque` mede, nos arquivos locais, quanto do DC casa com o
BD por família. Sem banco.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. Nenhum passo acessa o banco; o passo 6 lê arquivos locais.
Nenhum passo altera código; o único commit permitido é o merge do passo 2.
Rode no cmd, com o padrão de aspas da rodada 7 e caminho absoluto na
redireção. Se o passo 3 não mostrar a linha pedida, PARE.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git log --oneline -10 | findstr "cobertura-estoque"   (precisa mostrar 1 linha)
4. .venv\Scripts\python -m pytest -q          (esperado: 48 passed)
5. git push origin main
6. .venv\Scripts\python -m liquidez carteira cobertura-estoque --fundo "MULTIPLICA FIDC" > C:\Users\mateus.castagnet\mapa_banco\liq12_cobertura.txt 2>&1

Me devolva a saída literal dos passos 3 e 4 e o liq12_cobertura.txt inteiro.
Não tem dado de cotista (é carteira x recebíveis por família).
```

## Rodada 13 (05/10/2026) — cobertura do estoque, agora com o recorte certo

A rodada 12 saiu vazia por erro meu: o diagnóstico lia a carga crua, onde o DC
ainda se chama "Renda Fixa" (o de-para roda no tratamento). Corrigido — o
comando aplica o mesmo de-para. De quebra, o BD mostrou 140 recebíveis com
Tipo_Recebivel PRE/CDI/DOL MTM (as modalidades da carteira): pode ser que as
próprias operações NC/CCB estejam no estoque com outra chave; o comando agora
imprime dois ID_Recebivel por tipo para ver o formato.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. Nenhum passo acessa o banco; o passo 6 lê arquivos locais.
Nenhum passo altera código; o único commit permitido é o merge do passo 2.
Rode no cmd, com o padrão de aspas da rodada 7 e caminho absoluto na
redireção. Se o passo 3 não mostrar a linha pedida, PARE.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git log --oneline -10 | findstr "recorte certo"   (precisa mostrar 1 linha)
4. .venv\Scripts\python -m pytest -q          (esperado: 48 passed)
5. git push origin main
6. .venv\Scripts\python -m liquidez carteira cobertura-estoque --fundo "MULTIPLICA FIDC" > C:\Users\mateus.castagnet\mapa_banco\liq13_cobertura.txt 2>&1

Me devolva a saída literal dos passos 3 e 4 e o liq13_cobertura.txt inteiro.
```

## Rodada 14 (05/10/2026) — fila de bullet limpa do que não é decisão

A cobertura (rodada 13) mostrou a composição real do DC do MULTIPLICA FIDC em
30/09: NCPX 57 ops/R$ 606 mi (Beyond cobre 50), VC* 56 ops/R$ 95 mi (vencidos,
fora da projeção), CCEXP 22 (bullet por família), e o resto da fila de 256 era
ruído: MEZAN/SRP 170 ops somando R$ 0,00 (pares de contrapartida), PDD* 9 ops
negativas com vencimento 2049/2050, e 65 linhas que não são direito creditório
(LFTO, NTN O, CDBDI, IMOVE — R$ 52 mi) que o de-para tinha mandado para DC. Os
140 casamentos com o BD são as próprias operações (cópia EstoqueCarteira), não
cronograma — a fonte de cronograma de NC continua sendo o Beyond.
Mudanças: PDD*/PDC*/MEZAN/SRP viram família sem conferência; LFT/LTN/NTN/CDB/
IMOVE ficam fora do DC. Fila esperada: ~16 operações (7 NC da fila do Beyond +
DIRPX, CRDC, DEBSI, CONFD, DIR e afins).

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. Nenhum passo acessa o banco (todos leem arquivos locais).
Nenhum passo altera código; o único commit permitido é o merge do passo 2.
Rode no cmd, com o padrão de aspas da rodada 7 e caminho absoluto na
redireção. Se o passo 3 não mostrar a linha pedida, PARE.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git log --oneline -10 | findstr "fila de bullet"   (precisa mostrar 1 linha)
4. .venv\Scripts\python -m pytest -q          (esperado: 50 passed)
5. git push origin main
6. .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq14_tratar.txt 2>&1
7. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --salvar --fatos --detalhar --limite 40 > C:\Users\mateus.castagnet\mapa_banco\liq14_fluxo.txt 2>&1
   (código 2, 7 ou 11 = "concluído com pendência": NÃO é erro)
8. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq14_fatos.txt 2>&1
9. .venv\Scripts\python -m liquidez painel > C:\Users\mateus.castagnet\mapa_banco\liq14_painel.txt 2>&1

Me devolva a saída literal dos passos 3 e 4 e o CONTEÚDO dos arquivos: do
liq14_tratar.txt, da linha "Analise da carteira" até "gravado:"; do
liq14_fluxo.txt, da primeira linha até o fim do bloco "CONFERIR" (pule o
"Resumo por operacao"); fatos e painel inteiros. NÃO envie painel.html nem bi/.
```

## Rodada 15 (05/10/2026) — a lista nominal dos 12 bullets a conferir

Rodada 14: fila 256 → 12 (201 por regra de família), DC R$ 812,4 mi, Renda
Fixa R$ 52,1 mi, VL de DC pelo fluxo R$ 100,7 mi, caixa potencial 26,21%. O
fluxo não listou os 12 porque o registro de bullets já os tinha "visto" na
rodada 10 (só lista NOVAS HOJE). Agora imprime também "A CONFERIR" com código,
ativo, valor e vencimento — é a lista para o gestor.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. Nenhum passo acessa o banco. Nenhum passo altera código; o
único commit permitido é o merge do passo 2. Rode no cmd, com o padrão de
aspas da rodada 7 e caminho absoluto na redireção. Se o passo 3 não mostrar a
linha pedida, PARE.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git log --oneline -10 | findstr "lista nominal"   (precisa mostrar 1 linha)
4. .venv\Scripts\python -m pytest -q          (esperado: 50 passed)
5. git push origin main
6. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --detalhar --limite 40 > C:\Users\mateus.castagnet\mapa_banco\liq15_fluxo.txt 2>&1
   (código 2, 7 ou 11 = "concluído com pendência": NÃO é erro)

Me devolva a saída literal dos passos 3 e 4 e, do liq15_fluxo.txt, do início
até o fim do bloco "A CONFERIR" (pule o "Resumo por operacao").
```

## Rodada 16 (05/10/2026) — os três fundos da apresentação + decisões da área

Decisões da área (05/10): cota de outro fundo resolve o prazo pelo cadastro
inteiro (ou de-para manual); sem prazo, zero. IMOVE prazo 999. Vencidos VC*.
Bullets ficam como pagamento único. Agora o pipeline roda para os três fundos
da apresentação: MULTIPLICA FIDC (já), MULTIPLICA LONG TERM FIDC e MULTIAGRO
FIDC. Passos 6, 8 e 11 leem o riscogc (SELECT); os demais são locais.
`configurar --aplicar` reescreve config/ (fichas, prazos, cópias) — é a
configuração reconstruída do banco, não altera código.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. Passos 6, 8 e 11 só LEEM o riscogc (SELECT); os demais
leem/gravam arquivos locais fora do git (config/ gerado é ignorado pelo git).
Nenhum passo altera código; o único commit permitido é o merge do passo 2.
Rode no cmd, com o padrão de aspas da rodada 7 e caminho absoluto na
redireção. Se o passo 3 não mostrar a linha pedida, PARE. Se um passo falhar,
PARE e me devolva a saída inteira.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git log --oneline -12 | findstr "cotas_depara"   (precisa mostrar 1 linha)
4. .venv\Scripts\python -m pytest -q          (esperado: 50 passed)
5. git push origin main
6. .venv\Scripts\python -m liquidez configurar --aplicar --data 2026-09-30 > C:\Users\mateus.castagnet\mapa_banco\liq16_configurar.txt 2>&1
7. .venv\Scripts\python -m liquidez cotas pendentes > C:\Users\mateus.castagnet\mapa_banco\liq16_cotas_pendentes.txt 2>&1
   (se o comando pedir --fundo, rode com --fundo "MULTIPLICA FIDC")
8. .venv\Scripts\python -m liquidez extrair --data 2026-09-30 > C:\Users\mateus.castagnet\mapa_banco\liq16_extrair.txt 2>&1
   (LOTE dos 16 fundos com carteira, 4 workers; pode levar alguns minutos. NÃO use --fundo: com --salvar ele apaga a carga dos outros fundos)
9. (reservado)
10. .venv\Scripts\python -m liquidez carteira resumo --fundo "MULTIPLICA LONG TERM FIDC" --exemplos 4 > C:\Users\mateus.castagnet\mapa_banco\liq16_resumo_lt.txt 2>&1
    .venv\Scripts\python -m liquidez carteira resumo --fundo "MULTIAGRO FIDC" --exemplos 4 > C:\Users\mateus.castagnet\mapa_banco\liq16_resumo_ma.txt 2>&1
11. .venv\Scripts\python -m liquidez cronograma beyond --fundo "MULTIPLICA LONG TERM FIDC" --detalhar > C:\Users\mateus.castagnet\mapa_banco\liq16_beyond_lt.txt 2>&1
    .venv\Scripts\python -m liquidez cronograma beyond --fundo "MULTIAGRO FIDC" --detalhar > C:\Users\mateus.castagnet\mapa_banco\liq16_beyond_ma.txt 2>&1
    (código 2 = fila: NÃO é erro)
12. .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA LONG TERM FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq16_tratar_lt.txt 2>&1
    .venv\Scripts\python -m liquidez tratar --fundo "MULTIAGRO FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq16_tratar_ma.txt 2>&1

Me devolva a saída literal dos passos 3 e 4 e o CONTEÚDO dos arquivos liq16_*:
configurar, cotas_pendentes, resumo_*, beyond_* inteiros; do extrair, as 40 últimas linhas; dos
tratar_*, da linha "Fundo ....." até "gravado:". Nenhum deles tem dado de
cotista.
```

## Rodada 17 (05/10/2026) — a apresentação dos três fundos (depois da 16)

Novo comando `apresentacao` (capa + blocos A–F por fundo, marca Multiplica). O
`painel` antigo continua existindo e é gerado no passo 7 para provar que o
material antigo segue disponível. Só roda depois da rodada 16 (extração em
lote e tratamento dos dois fundos novos). Passos 5 e 6 leem só arquivos
locais; nenhum passo acessa o banco.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Pré-requisito: a rodada 16 concluída (liq16_tratar_lt.txt e liq16_tratar_ma.txt
gerados). Traga para a main o último commit da branch
claude/epic-lamport-knours e rode os passos abaixo. Nenhum passo acessa o
banco. Nenhum passo altera código; o único commit permitido é o merge do
passo 2. Rode no cmd, com o padrão de aspas da rodada 7 e caminho absoluto na
redireção. Se o passo 3 não mostrar a linha pedida, PARE.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git log --oneline -12 | findstr "apresentacao"   (precisa mostrar pelo menos 1 linha)
4. .venv\Scripts\python -m pytest -q          (esperado: 56 passed)
5. git push origin main
6. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA LONG TERM FIDC" --salvar --fatos > C:\Users\mateus.castagnet\mapa_banco\liq17_fluxo_lt.txt 2>&1
   .venv\Scripts\python -m liquidez fluxo --fundo "MULTIAGRO FIDC" --salvar --fatos > C:\Users\mateus.castagnet\mapa_banco\liq17_fluxo_ma.txt 2>&1
   (código 2, 7 ou 11 = pendência, NÃO é erro; se o fluxo disser "exige planilha"/"fora do escopo bullet", NÃO é erro: siga)
   .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA LONG TERM FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq17_fatos_lt.txt 2>&1
   .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIAGRO FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq17_fatos_ma.txt 2>&1
7. .venv\Scripts\python -m liquidez painel > C:\Users\mateus.castagnet\mapa_banco\liq17_painel.txt 2>&1
8. .venv\Scripts\python -m liquidez apresentacao --fundos "MULTIPLICA FIDC,MULTIPLICA LONG TERM FIDC,MULTIAGRO FIDC" > C:\Users\mateus.castagnet\mapa_banco\liq17_apresentacao.txt 2>&1

Me devolva a saída literal dos passos 3 e 4 e o CONTEÚDO dos arquivos liq17_*:
dos fluxo_*, as 40 primeiras linhas; fatos_*, painel e apresentacao inteiros.
NÃO envie apresentacao.html, painel.html nem bi/ (dados de cotistas).
```

## Rodada 18 (05/10/2026) — LT e Multiagro: fluxo pelo estoque do fundo

As rodadas 16/17 mostraram que no LONG TERM (R$ 391 mi) e no MULTIAGRO
(R$ 1,15 bi) a carteira de DC é toda MEZAN/SRP — linhas agregadas do pool,
sem Codigo_Op NCPX/CCBPX para o Beyond casar — e por isso o caixa saiu 0%.
Agora essas famílias são projetadas pelo ESTOQUE do próprio fundo: o valor da
carteira é repartido pelos vencimentos dos recebíveis a vencer
(HOD_TB_ESTOQUE_UNIFICADO já extraído em dados/bd.parquet), pro rata ao valor
presente. Nada acessa o banco nesta rodada.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/epic-lamport-knours e rode
os passos abaixo. Nenhum passo acessa o banco. Nenhum passo altera código; o
único commit permitido é o merge do passo 2. Rode no cmd, com o padrão de
aspas da rodada 7 e caminho absoluto na redireção. Se o passo 3 não mostrar a
linha pedida, PARE.

1. git fetch origin && git checkout main && git pull
2. git merge --no-ff origin/claude/epic-lamport-knours   (conflito = PARE)
3. git log --oneline -12 | findstr "pelo estoque"   (precisa mostrar 1 linha)
4. .venv\Scripts\python -m pytest -q          (esperado: 59 passed)
5. git push origin main
6. findstr /C:"MULTIAGRO" /C:"LONG TERM" C:\Users\mateus.castagnet\mapa_banco\liq16_extrair.txt > C:\Users\mateus.castagnet\mapa_banco\liq18_extrair_2fundos.txt 2>&1
7. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA LONG TERM FIDC" --salvar --fatos --detalhar --limite 20 > C:\Users\mateus.castagnet\mapa_banco\liq18_fluxo_lt.txt 2>&1
   .venv\Scripts\python -m liquidez fluxo --fundo "MULTIAGRO FIDC" --salvar --fatos --detalhar --limite 20 > C:\Users\mateus.castagnet\mapa_banco\liq18_fluxo_ma.txt 2>&1
   .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --salvar --fatos > C:\Users\mateus.castagnet\mapa_banco\liq18_fluxo_lp.txt 2>&1
   (código 2, 7 ou 11 = pendência: NÃO é erro)
8. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA LONG TERM FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq18_fatos_lt.txt 2>&1
   .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIAGRO FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq18_fatos_ma.txt 2>&1
   .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq18_fatos_lp.txt 2>&1
9. .venv\Scripts\python -m liquidez painel > C:\Users\mateus.castagnet\mapa_banco\liq18_painel.txt 2>&1
10. .venv\Scripts\python -m liquidez apresentacao --fundos "MULTIPLICA FIDC,MULTIPLICA LONG TERM FIDC,MULTIAGRO FIDC" > C:\Users\mateus.castagnet\mapa_banco\liq18_apresentacao.txt 2>&1

Me devolva a saída literal dos passos 3 e 4 e o CONTEÚDO dos arquivos liq18_*:
extrair_2fundos inteiro; dos fluxo_*, do início até o fim do bloco "ESTOQUE"
(ou "BULLET"), pulando o "Resumo por operacao"; fatos_*, painel e
apresentacao inteiros. NÃO envie apresentacao.html, painel.html nem bi/.
```

## Rodada 19 (21/10/2026) — pacote do gestor: integrar tudo na main

Branch nova: `claude/compassionate-wright-cs8e2r`. Ela traz o pacote do gestor
de 21/10 (`docs/pacote-gestor-2026-10-21.md`) **e também a branch
`alteracao-banco-dados`**, que ainda não estava na main:
- troca para `MUL_GLD_CARTEIRA_UNIFICADA`;
- vencido também pela data no passado;
- `cotas classificar`;
- `docs/mapeamento-banco.md`.

A conexão continua no riscogc de produção. O merge é limpo: a main é
ancestral da branch.

Antes de colar: descompacte `OneDrive_2026-10-21.zip` em
`C:\Users\mateus.castagnet\pacote_gestor`, de modo que exista
`C:\Users\mateus.castagnet\pacote_gestor\Relatorio Liquidez - pacote\config\cronograma`.
O passo 11 confere isso.

O "antes" (passo 10) roda com a config antiga e o "depois" (passo 18) com a
nova, sobre a mesma carga. A diferença entre eles é o efeito da lista de
excluídos e da tabela de prazos do gestor. Passos 7, 9 e 14 leem o riscogc
(SELECT); os demais são locais.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus
(C:\Users\mateus.castagnet\OneDrive - PERPETUO CONSULTORIA ESPECIALIZADA LTDA\Documentos\Projetos GIT\Risco-de-Liquidez---Multiplica-Fidc).

Traga para a main o último commit da branch claude/compassionate-wright-cs8e2r
e rode os passos abaixo. Nenhum passo grava no banco: o projeto só LÊ o
riscogc. Os passos 12 a 15 gravam em config/, que é ignorada pelo git (é
esperado). Nenhum passo altera código; o único commit permitido é o merge do
passo 3. Rode no cmd (não PowerShell), com o padrão de aspas da rodada 7 e
caminho absoluto na redireção. Um passo de cada vez. Se um passo falhar ou não
mostrar o esperado, PARE e me devolva a saída inteira.

REGRA ABSOLUTA: você NÃO pode alterar código, nem db.py, nem a conexão. Se o
passo 7 mostrar 10.201.1.6 ou riscogchml, PARE.

1. git status --short            (pode listar SÓ "?? config/produtos.json" e "?? fluxo/", que são
   saídas geradas e passam a ser ignoradas depois do merge; NÃO apague. Qualquer outra linha, PARE)
2. git fetch origin && git checkout main && git pull
3. git merge --no-ff origin/claude/compassionate-wright-cs8e2r   (conflito = PARE)
4. git log --oneline -15 | findstr /C:"Pacote do gestor" /C:"MUL_GLD"   (precisa mostrar pelo menos 2 linhas)
5. .venv\Scripts\pip install -r requirements.txt
6. .venv\Scripts\python -m pytest -q          (esperado: 83 passed)
7. .venv\Scripts\python -m liquidez testar-conexao   (tem de mostrar 10.101.1.18 e riscogc)
8. git push origin main
9. .venv\Scripts\python -m liquidez extrair --data 2026-09-30 > C:\Users\mateus.castagnet\mapa_banco\liq19_extrair.txt 2>&1
   (LOTE, sem --fundo; pode levar alguns minutos)
10. ANTES (config antiga):
    .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA FIDC" > C:\Users\mateus.castagnet\mapa_banco\liq19_tratar_antes_lp.txt 2>&1
    .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA LONG TERM FIDC" > C:\Users\mateus.castagnet\mapa_banco\liq19_tratar_antes_lt.txt 2>&1
    .venv\Scripts\python -m liquidez tratar --fundo "MULTIAGRO FIDC" > C:\Users\mateus.castagnet\mapa_banco\liq19_tratar_antes_ma.txt 2>&1
11. dir "C:\Users\mateus.castagnet\pacote_gestor\Relatorio Liquidez - pacote\config\cronograma"   (não existe = PARE)
12. .venv\Scripts\python -m liquidez importar-pacote --pacote "C:\Users\mateus.castagnet\pacote_gestor\Relatorio Liquidez - pacote" > C:\Users\mateus.castagnet\mapa_banco\liq19_importar_simulacao.txt 2>&1
13. .venv\Scripts\python -m liquidez importar-pacote --pacote "C:\Users\mateus.castagnet\pacote_gestor\Relatorio Liquidez - pacote" --aplicar > C:\Users\mateus.castagnet\mapa_banco\liq19_importar.txt 2>&1
    (NÃO use --sobrescrever: arquivo "divergente" fica como está e eu decido)
14. .venv\Scripts\python -m liquidez configurar --data 2026-09-30 --aplicar > C:\Users\mateus.castagnet\mapa_banco\liq19_configurar.txt 2>&1
15. .venv\Scripts\python -m liquidez fundos validar > C:\Users\mateus.castagnet\mapa_banco\liq19_fundos_validar.txt 2>&1
    .venv\Scripts\python -m liquidez prazos validar > C:\Users\mateus.castagnet\mapa_banco\liq19_prazos_validar.txt 2>&1
16. .venv\Scripts\python -m liquidez cronograma list --fundo "MULTIPLICA FIDC" > C:\Users\mateus.castagnet\mapa_banco\liq19_cronograma_lp.txt 2>&1
17. .venv\Scripts\python -m liquidez.cra inventario > C:\Users\mateus.castagnet\mapa_banco\liq19_cra.txt 2>&1
18. DEPOIS (config nova):
    .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq19_tratar_depois_lp.txt 2>&1
    .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA LONG TERM FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq19_tratar_depois_lt.txt 2>&1
    .venv\Scripts\python -m liquidez tratar --fundo "MULTIAGRO FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq19_tratar_depois_ma.txt 2>&1
19. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --salvar --fatos > C:\Users\mateus.castagnet\mapa_banco\liq19_fluxo_lp.txt 2>&1
    .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA LONG TERM FIDC" --salvar --fatos > C:\Users\mateus.castagnet\mapa_banco\liq19_fluxo_lt.txt 2>&1
    .venv\Scripts\python -m liquidez fluxo --fundo "MULTIAGRO FIDC" --salvar --fatos > C:\Users\mateus.castagnet\mapa_banco\liq19_fluxo_ma.txt 2>&1
    (código 2, 7 ou 11 = pendência: NÃO é erro, siga)
20. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq19_fatos_lp.txt 2>&1
    .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA LONG TERM FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq19_fatos_lt.txt 2>&1
    .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIAGRO FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq19_fatos_ma.txt 2>&1
21. .venv\Scripts\python -m liquidez relatorio --fundo "MULTIPLICA FIDC" --saida-rel C:\Users\mateus.castagnet\liquidez_relatorios\2026-09-30 > C:\Users\mateus.castagnet\mapa_banco\liq19_relatorio.txt 2>&1
22. .venv\Scripts\python -m liquidez painel > C:\Users\mateus.castagnet\mapa_banco\liq19_painel.txt 2>&1
    .venv\Scripts\python -m liquidez apresentacao --fundos "MULTIPLICA FIDC,MULTIPLICA LONG TERM FIDC,MULTIAGRO FIDC" > C:\Users\mateus.castagnet\mapa_banco\liq19_apresentacao.txt 2>&1
23. .venv\Scripts\python -m liquidez documentar > C:\Users\mateus.castagnet\mapa_banco\liq19_documentar.txt 2>&1

Me devolva a saída literal dos passos 1, 4, 6 e 7 e o CONTEÚDO dos arquivos
liq19_*:
- inteiros: importar_simulacao, importar, configurar, cronograma_lp, cra,
  relatorio, painel, apresentacao e documentar;
- do extrair, as 40 últimas linhas;
- do fundos_validar e do prazos_validar, só as linhas ERRO e AVISO que falam
  de prazo e a linha final de resumo;
- dos tratar_antes_* e tratar_depois_*, da linha "Fundo ....." até a linha
  "ANBIMA enquadrado ate" (inclusive). NÃO cole a "Tabela vencidos": ela traz
  CPF e nome de sacado pessoa física;
- dos fluxo_*, do início até o fim do bloco "ESTOQUE" (ou "BULLET"), pulando
  o "Resumo por operacao";
- fatos_* inteiros.
NÃO envie o .xlsx, apresentacao.html, painel.html, docs/tratamentos.html nem
bi/ (dados de cotistas).
```

## Rodada 20 (21/10/2026) — MEZAN/SRP: as duas pernas pelo estoque

A rodada 19 mostrou o fluxo do MULTIPLICA FIDC com ESTOQUE de R$ 1,75 bi e
BULLET de R$ -1,57 bi, e o DC no fluxo em R$ 624 mi contra R$ 38,8 mi da
matriz. MEZAN/SRP vem em pares de contrapartida (+/-). Só a perna positiva ia
pelo estoque (e caía na janela); a negativa ficava bullet no vencimento, fora
dela. Agora as duas seguem o mesmo perfil e se anulam data a data. Não muda o
Caixa Potencial do `tratar` (que vem da matriz); muda o fluxo, os fatos, a
escada da apresentação e o painel. Só o fluxo lê o riscogc (curva DI, SELECT).

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/compassionate-wright-cs8e2r
e rode os passos abaixo. Nenhum passo grava no banco; nenhum passo altera
código; o único commit permitido é o merge do passo 3. Não rode extrair,
configurar nem importar-pacote (a carga e a config da rodada 19 valem). Rode
no cmd, caminho absoluto na redireção, um passo de cada vez. NÃO encadeie com
&&: o fluxo devolve código 2 quando há bullet nova, e isso não é erro. Qualquer
outra falha, PARE e devolva a saída inteira.

1. git status --short            (precisa sair vazio; senão PARE)
2. git fetch origin && git checkout main && git pull
3. git merge --no-ff origin/claude/compassionate-wright-cs8e2r   (conflito = PARE)
4. git log --oneline -6 | findstr /C:"duas pernas"   (precisa mostrar 1 linha)
5. .venv\Scripts\python -m pytest -q          (esperado: 84 passed)
6. git push origin main
7. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA FIDC" --salvar --fatos > C:\Users\mateus.castagnet\mapa_banco\liq20_fluxo_lp.txt 2>&1
8. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIPLICA LONG TERM FIDC" --salvar --fatos > C:\Users\mateus.castagnet\mapa_banco\liq20_fluxo_lt.txt 2>&1
9. .venv\Scripts\python -m liquidez fluxo --fundo "MULTIAGRO FIDC" --salvar --fatos > C:\Users\mateus.castagnet\mapa_banco\liq20_fluxo_ma.txt 2>&1
10. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq20_fatos_lp.txt 2>&1
11. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIPLICA LONG TERM FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq20_fatos_lt.txt 2>&1
12. .venv\Scripts\python -m liquidez fatos gerar --fundo "MULTIAGRO FIDC" --verboso > C:\Users\mateus.castagnet\mapa_banco\liq20_fatos_ma.txt 2>&1
13. .venv\Scripts\python -m liquidez painel > C:\Users\mateus.castagnet\mapa_banco\liq20_painel.txt 2>&1
14. .venv\Scripts\python -m liquidez apresentacao --fundos "MULTIPLICA FIDC,MULTIPLICA LONG TERM FIDC,MULTIAGRO FIDC" > C:\Users\mateus.castagnet\mapa_banco\liq20_apresentacao.txt 2>&1

Me devolva a saída literal dos passos 1, 4 e 5 e, SEM resumir:
- dos liq20_fluxo_*: do início até o fim do bloco "ESTOQUE" e o bloco
  "BULLET" inteiro (com as listas A CONFERIR / NOVAS HOJE), pulando o
  "Resumo por operacao";
- liq20_fatos_*, liq20_painel e liq20_apresentacao inteiros.
NÃO envie .html, .xlsx nem bi\ (dados de cotistas).
```

## Rodada 21 (21/10/2026) — passivo migrado para a MUL_GLD_PASSIVO_CONSOLIDADO

A HOD_PAS_POSICAO_NOTA / HOD_PAS_CAD_COTISTA pararam em 07/10/2026. O passivo
(pacote e os quatro scripts da raiz) passa a ler a `MUL_GLD_PASSIVO_CONSOLIDADO`.
As regras são as da carga do Datalake: uma linha por nota (a repetida mais
recente vence), só SINGULARE e PLANNER, e distribuidor uma vez por documento.
`passivo comparar` põe as duas fontes lado a lado numa data, só com agregados
(sem nome nem documento de cotista). Tudo é SELECT.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/compassionate-wright-cs8e2r
e rode os passos abaixo. Nenhum passo grava no banco; nenhum passo altera
código; o único commit permitido é o merge do passo 3. Rode no cmd, caminho
absoluto na redireção, um passo de cada vez, sem encadear com &&. Qualquer
falha, PARE e devolva a saída inteira.

1. git status --short            (precisa sair vazio; senão PARE)
2. git fetch origin && git checkout main && git pull
3. git merge --no-ff origin/claude/compassionate-wright-cs8e2r   (conflito = PARE)
4. git log --oneline -6 | findstr /C:"MUL_GLD_PASSIVO"   (precisa mostrar 1 linha)
5. .venv\Scripts\python -m pytest -q          (esperado: 88 passed)
6. git push origin main
7. .venv\Scripts\python -m liquidez passivo comparar --data 2026-09-30 > C:\Users\mateus.castagnet\mapa_banco\liq21_passivo_0930.txt 2>&1
8. .venv\Scripts\python -m liquidez passivo comparar --data 2026-10-06 > C:\Users\mateus.castagnet\mapa_banco\liq21_passivo_1006.txt 2>&1
9. .venv\Scripts\python -m liquidez passivo comparar --data 2026-10-20 > C:\Users\mateus.castagnet\mapa_banco\liq21_passivo_1020.txt 2>&1
   (aqui a coluna HOD deve vir zerada: ela parou em 07/10)
10. .venv\Scripts\python -m liquidez extrair --data 2026-09-30 > C:\Users\mateus.castagnet\mapa_banco\liq21_extrair.txt 2>&1
    (LOTE, sem --fundo; refaz a carga de 30/09 com o passivo novo)
11. .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq21_tratar_lp.txt 2>&1
    .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA LONG TERM FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq21_tratar_lt.txt 2>&1
    .venv\Scripts\python -m liquidez tratar --fundo "MULTIAGRO FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq21_tratar_ma.txt 2>&1
12. .venv\Scripts\python -m liquidez fatos gerar > C:\Users\mateus.castagnet\mapa_banco\liq21_fatos.txt 2>&1
13. .venv\Scripts\python -m liquidez painel > C:\Users\mateus.castagnet\mapa_banco\liq21_painel.txt 2>&1
    .venv\Scripts\python -m liquidez apresentacao --fundos "MULTIPLICA FIDC,MULTIPLICA LONG TERM FIDC,MULTIAGRO FIDC" > C:\Users\mateus.castagnet\mapa_banco\liq21_apresentacao.txt 2>&1

Me devolva a saída literal dos passos 1, 4 e 5 e, SEM resumir:
- liq21_passivo_0930, liq21_passivo_1006 e liq21_passivo_1020 inteiros (só
  têm agregados, sem cotista);
- do liq21_extrair, as 40 últimas linhas;
- dos liq21_tratar_*, da linha "Fundo ....." até a linha "ANBIMA enquadrado
  ate" (inclusive). NÃO cole a "Tabela vencidos" (CPF e nome de sacado PF);
- liq21_fatos, liq21_painel e liq21_apresentacao inteiros.
NÃO rode os scripts da raiz (analise_passivo, grafo_passivo, passivo_gestor):
o HTML deles traz CPF e nome de cotista. NÃO envie .html, .xlsx nem bi\.
```

## Rodada 22 (21/10/2026) — atualidade das bases (SÓ LEITURA)

A rodada 21 validou a migração do passivo (30/09: consolidada = HOD em todos os
fundos, 0 nota repetida). Mas 20/10 veio vazio nas DUAS fontes. A
`MUL_GLD_PASSIVO_CONSOLIDADO` do riscogc é um clone do banco datalake, refeito
pela rotina do Datalake (`python main.py --rodar-clones`). Essa mesma rotina
recarrega a HOD de passivo, movimentação, cotas e estoque e o clone da
carteira. Esta rodada só lê: a última data de cada base, clone ao lado da
origem, e o estado da tarefa agendada.

```text
Duas partes, na máquina do Mateus. Nenhum passo grava em banco nem altera
código; o único commit permitido é o merge do passo 3. Rode no cmd, caminho
absoluto na redireção, um passo de cada vez, sem encadear com &&. Qualquer
falha, PARE e devolva a saída inteira. NÃO rode --rodar-clones nem nenhum
--exec nesta rodada.

PARTE A — repositório Risco-de-Liquidez---Multiplica-Fidc
1. git status --short            (precisa sair vazio; senão PARE)
2. git fetch origin && git checkout main && git pull
3. git merge --no-ff origin/claude/compassionate-wright-cs8e2r   (conflito = PARE)
4. .venv\Scripts\python -m pytest -q          (esperado: 89 passed)
5. git push origin main
6. .venv\Scripts\python -m liquidez atualidade > C:\Users\mateus.castagnet\mapa_banco\liq22_atualidade.txt 2>&1

PARTE B — repositório Projeto-Datalake---Risco-GC (só leitura)
7. schtasks /query /tn "DLK Clonar TODOS" /v /fo LIST
   (se disser que a tarefa não existe, só registre; NÃO crie)
8. dir /o-d logs\clones_*.log
9. Do log mais recente do passo 8: as 30 últimas linhas
   (powershell -Command "Get-Content logs\<arquivo> -Tail 30")

Me devolva a saída literal dos passos 1, 4, 6, 7, 8 e 9, sem resumir.
```

## Rodada 23 (21/10/2026) — levar as correções da documentação para a main

Só merge e push: os comentários e a documentação deixam de dizer que o passivo
parou em 07/10 (ele atrasa D-2 já na origem), e o mapeamento ganha a defasagem
de cada base. Nada roda contra o banco.

```text
Repositório Risco-de-Liquidez---Multiplica-Fidc, na máquina do Mateus.

Traga para a main o último commit da branch claude/compassionate-wright-cs8e2r.
Nenhum passo acessa o banco nem altera código; o único commit permitido é o
merge do passo 3. Rode no cmd, um passo de cada vez, sem encadear com &&.
Qualquer falha, PARE e devolva a saída inteira.

1. git status --short            (precisa sair vazio; senão PARE)
2. git fetch origin && git checkout main && git pull
3. git merge --no-ff origin/claude/compassionate-wright-cs8e2r   (conflito = PARE)
4. git log --oneline -4 | findstr /C:"D-2"   (precisa mostrar 1 linha)
5. .venv\Scripts\python -m pytest -q          (esperado: 89 passed)
6. git push origin main
7. git log --oneline -1

Me devolva a saída literal dos passos 1, 4, 5, 6 e 7.
```

## Rodada 24 (23/10/2026) — carteira, passivo e cotas direto do datalake + mapa do que falta

Carteira (`MUL_GLD_CARTEIRA_UNIFICADA`) e passivo (`MUL_GLD_PASSIVO_CONSOLIDADO`)
passam a ser lidos na origem do banco datalake, não mais nos clones do riscogc.
A série de cotas do VaR troca a `HOD_TB_COTAS_FUNDOS` pela
`MUL_GLD_MEC_CONSOLIDADA`, ligada ao fundo pelo `DLK_DEPARA_FUNDOS` (o mesmo
caminho da carga da HOD desde 10/09). O `passivo comparar` saiu: nada mais lê
`HOD_PAS_*`. Estoque, cadastro de fundos e movimentação seguem nas `HOD_*`
porque nenhum repositório tem as colunas das MUL equivalentes; o comando novo
`datalake mapear` as levanta (só metadados e agregados, sem cotista). O passo 3
roda ANTES do merge, com a fonte antiga, para comparar a série de cotas. Tudo é
SELECT.

```text
Repositório Projeto_QI, na máquina do Mateus
(C:\Users\mateus.castagnet\OneDrive - PERPETUO CONSULTORIA ESPECIALIZADA LTDA\Documentos\Projetos GIT\Projeto_QI).

Traga para a main o último commit da branch claude/optimistic-allen-cpugky e
rode os passos abaixo. Nenhum passo grava no banco; nenhum passo altera
código; o único commit permitido é o merge do passo 5. Rode no cmd, caminho
absoluto na redireção, um passo de cada vez, sem encadear com &&. Qualquer
falha, PARE e devolva a saída inteira.

1. git status --short            (precisa sair vazio; senão PARE)
2. git fetch origin && git checkout main && git pull
3. dir .venv\Scripts\python.exe
   (se não existir: python -m venv .venv e depois
    .venv\Scripts\pip install -r requirements.txt)
4. ANTES do merge, a série de cotas pela fonte antiga (HOD):
   .venv\Scripts\python -m liquidez cotas serie --ate 2026-10-21 --destino C:\Users\mateus.castagnet\mapa_banco\liq24_bi_antes > C:\Users\mateus.castagnet\mapa_banco\liq24_cotas_antes.txt 2>&1
5. git merge --no-ff origin/claude/optimistic-allen-cpugky   (conflito = PARE)
6. git log --oneline -4 | findstr /C:"datalake"   (precisa mostrar 1 linha)
7. .venv\Scripts\python -m pytest -q          (esperado: 101 passed)
8. git push origin main
9. .venv\Scripts\python -m liquidez atualidade > C:\Users\mateus.castagnet\mapa_banco\liq24_atualidade.txt 2>&1
   (abra o arquivo: se a linha "carteira (datalake)", "passivo (datalake)" ou
    "cotas MEC (datalake)" disser "falhou", PARE: o login não está lendo o
    datalake e o resto depende disso)
10. .venv\Scripts\python -m liquidez datalake mapear --ate 2026-10-21 > C:\Users\mateus.castagnet\mapa_banco\liq24_mapa_datalake.txt 2>&1
11. .venv\Scripts\python -m liquidez cotas serie --ate 2026-10-21 --destino C:\Users\mateus.castagnet\mapa_banco\liq24_bi_depois > C:\Users\mateus.castagnet\mapa_banco\liq24_cotas_depois.txt 2>&1
12. dir /b config\fundos\*.json | find /c ".json"
    (se der 0: .venv\Scripts\python -m liquidez configurar --aplicar > C:\Users\mateus.castagnet\mapa_banco\liq24_configurar.txt 2>&1
     -- grava só a pasta config\ local -- e siga)
13. .venv\Scripts\python -m liquidez extrair --data 2026-10-21 > C:\Users\mateus.castagnet\mapa_banco\liq24_extrair.txt 2>&1
    (LOTE, sem --fundo: carteira e passivo agora vêm do datalake)
14. .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq24_tratar_lp.txt 2>&1
    .venv\Scripts\python -m liquidez tratar --fundo "MULTIPLICA LONG TERM FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq24_tratar_lt.txt 2>&1
    .venv\Scripts\python -m liquidez tratar --fundo "MULTIAGRO FIDC" --salvar > C:\Users\mateus.castagnet\mapa_banco\liq24_tratar_ma.txt 2>&1
15. .venv\Scripts\python -m liquidez fatos gerar > C:\Users\mateus.castagnet\mapa_banco\liq24_fatos.txt 2>&1
    .venv\Scripts\python -m liquidez painel > C:\Users\mateus.castagnet\mapa_banco\liq24_painel.txt 2>&1

Me devolva a saída literal dos passos 1, 3, 6, 7, 8 e 12 e, SEM resumir:
- liq24_atualidade, liq24_mapa_datalake, liq24_cotas_antes e
  liq24_cotas_depois inteiros (só metadados, contagens, fundos e classes; sem
  cotista);
- do liq24_extrair, as 40 últimas linhas;
- dos liq24_tratar_*, da linha "Fundo ....." até a linha "ANBIMA enquadrado
  ate" (inclusive). NÃO cole a "Tabela vencidos" (CPF e nome de sacado PF);
- liq24_fatos e liq24_painel inteiros (e liq24_configurar, se o passo 12
  precisou dele).
NÃO rode os scripts da raiz (analise_passivo, grafo_passivo, passivo_gestor):
o HTML deles traz CPF e nome de cotista. NÃO envie .html, .xlsx nem bi\.
```
