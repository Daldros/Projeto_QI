# Documentos de CRA

Deposite aqui os Termos de Securitização (PDF). Esta pasta é a fila de entrada;
`processados/` é o arquivo morto.

## Como nomear

O arquivo precisa dizer **de quem é** (fundo) e **qual operação é** (`Codigo_Op`
da carteira). O jeito mais direto é pôr os dois no nome:

```
32388135000162_B915955_TecPlante_204a.pdf
└── CNPJ ────┘ └ op ─┘ └─ descrição livre ─┘
```

O CNPJ aceita pontuação (`32.388.135-0001-62`) — barra não, que o Windows não
deixa. O código da operação é o `BXXXXXX` / `CXXXXXX` da carteira.

## Organizando por fundo

Se preferir uma pasta por fundo, o CNPJ pode ficar na pasta em vez do nome do
arquivo. O nome interno do fundo, como está no cadastro, também serve:

```
config/cra/
    32.388.135-0001-62/
        B915955_TecPlante.pdf
    MULTIAGRO FIDC/
        C123456_OutroCRA.pdf
```

A busca é em cascata: CNPJ no nome do arquivo, depois CNPJ na pasta, depois
`nome_interno` na pasta. O primeiro que casar vale. Se nada casar, o documento
aparece como `sem_fundo` — o programa não chuta o fundo.

## O que fazer com eles

```
python -m liquidez.cra inventario
```

Lista os documentos com o fundo, a operação e o estado de cada um:

| estado | o que significa |
|---|---|
| `sem_fundo` | não deu para identificar o fundo — renomeie o arquivo ou a pasta |
| `fundo_desconhecido` | o CNPJ não está no cadastro de fundos |
| `sem_codigo` | falta o `Codigo_Op` no nome |
| `sem_ficha` | ainda não foi lido — rode o agente `cra-lastro` neste PDF |
| `ficha_pronta` | a ficha existe, falta gerar o cadastro |
| `cadastrado` | pronto; o documento pode ser arquivado |

## Uma pasta por operação

```
python -m liquidez.cra organizar
```

Recolhe cada documento para `<fundo>/<codigo_op>/`. Uma operação raramente tem um
documento só — vêm o Termo de Securitização, o CDCA, o contrato de cessão
fiduciária, aditamentos —, e a pasta por código junta tudo o que fala do mesmo
papel:

```
config/cra/MULTIAGRO FIDC/
    B915955/
        32388135000162_B915955_Termo.pdf
        32388135000162_B915955_CDCA.pdf
    C999888/
        32388135000162_C999888_NovoCRA.pdf
```

Rodar de novo não mexe no que já está no lugar. Documento sem fundo ou sem código
**fica onde está**, na raiz, pedindo atenção — mover para uma pasta adivinhada é
pior do que deixar visível.

Dentro da pasta da operação o nome do arquivo é livre: o `Codigo_Op` vem da
pasta. Use a **data do documento na frente**, em `AAAAMMDD`, para a ordem
alfabética virar a ordem cronológica:

```
config/cra/MULTIAGRO FIDC/C47862/
    20240115_Termo_Securitizacao.pdf
    20240115_CDCA.pdf
    20240820_Aditamento_1.pdf
    20250310_Aditamento_2.pdf
```

Isso importa porque **aditamento altera o Termo**. Data, taxa e cronograma de
amortização podem mudar, e vale o mais recente. Ordenar por nome já entrega a
ordem de leitura, e nenhum aditamento passa despercebido no meio da pilha.

## Aviso de ativo novo

A coluna `novo` do inventário marca a operação que **o cronograma do fundo não
conhece**: não veio da planilha de conciliação nem tem cadastro manual. É
diferente de `sem_ficha` — ali o documento apenas não foi lido; aqui o pipeline
inteiro ignora que o ativo existe, e **a projeção de liquidez do fundo está sem
ele**.

`inventario`, `organizar` e `lote` terminam destacando esses casos:

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!! 2 ATIVO(S) NOVO(S) -- fora do cronograma do fundo
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
   MULTIAGRO FIDC       C777111    C777111_OutroNovo.pdf
   MULTIAGRO FIDC       C999888    32388135000162_C999888_NovoCRA.pdf
```

O aviso some sozinho quando a operação entra no cronograma — seja pelo cadastro
manual gerado aqui, seja porque a planilha de conciliação passou a trazê-la.

Para os `sem_ficha`, o agente `cra-lastro` lê o PDF e grava a ficha em
`config/cronograma/fichas/<codigo_op>.json`. Depois:

```
python -m liquidez.cra lote
```

gera o cadastro de toda ficha pronta em `config/cronograma/manuais/`, a planilha
de conferência em `saidas/cra/`, e **só então** move o PDF para `processados/`
com carimbo de data. Documento que ainda não virou cadastro fica na fila —
arquivar antes esconderia o PDF de quem ainda precisa lê-lo.

Use `--nao-arquivar` para gerar os cadastros sem mexer nos arquivos.
