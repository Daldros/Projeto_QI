# Relatório de Liquidez — pacote para rodar

Esta pasta é uma **cópia** autossuficiente do que é preciso para gerar o
Relatório de Liquidez, o painel e os relatórios individuais por fundo.

Comece pelo documento do processo: **`docs/processo_liquidez.html`**. Ele mapeia
as nove etapas da rodada, o papel de cada módulo e onde as decisões de
modelagem estão escritas no código.

---

## 1. Pré-requisitos

**Python 3.11 ou superior**, com as dependências:

```
pip install -r requirements.txt
```

São `pandas`, `pyodbc`, `openpyxl`, `pyarrow` e `pypdf`.

**Driver ODBC para SQL Server.** O código aceita, por ordem de preferência,
o `ODBC Driver 18`, o `17`, o `SQL Server Native Client 11.0` e o legado
`SQL Server`. Qualquer um serve.

**Acesso ao banco.** O servidor é `10.201.1.6,1433`, catálogo `riscogchml`.
A autenticação é **integrada do Windows** — não há usuário nem senha no
código. Quem rodar precisa de rota de rede até o servidor e de permissão de
leitura com a própria conta de domínio.

Para apontar para outro catálogo sem mexer no código:

```powershell
$env:LIQUIDEZ_BANCO = "riscogc"
```

---

## 2. Verificar a instalação

Estes três comandos **não acessam o banco** e provam que o pacote está íntegro:

```powershell
python -m liquidez fundos validar      # deve listar 103 fundos, 0 erros
python -m liquidez prazos validar      # deve listar 289 ativos, 0 erros
python -m liquidez cronograma list --fundo "MULTIPLICA FIDC"
```

Avisos são normais; **erros não**. Em seguida, teste o banco:

```powershell
python -m liquidez testar-conexao
```

---

## 3. Rodar um dia

O caminho normal é o script da rodada, que executa as nove etapas na ordem e
aborta se a carga vier incompleta:

```powershell
.\rodar_diario.ps1 -Data 2026-09-09
```

Por padrão ele grava fora desta pasta, em `C:\Users\<você>\liquidez_*`. Para
manter tudo aqui dentro:

```powershell
.\rodar_diario.ps1 -Data 2026-09-09 `
    -Dados      .\dados `
    -Relatorios .\relatorios `
    -Fluxos     .\fluxo `
    -Bi         .\bi `
    -Logs       .\logs
```

**O código de saída diz o que aconteceu.** `0` é sucesso; `7`, `8` e `11` são
*pendência* — os relatórios saíram, mas há algo para conferir; os demais são
falha. A tabela completa está no documento do processo.

### Etapa por etapa

Para depurar, cada etapa roda sozinha. A ordem importa:

```powershell
python -m liquidez extrair --data 2026-09-09        # 1
python -m liquidez prazos sincronia                  # 4
python -m liquidez cronograma sincronizar            # 5
python -m liquidez cronograma beyond                 # 5b
python -m liquidez fluxo --salvar --fatos            # 6
python -m liquidez relatorio --saida-rel .\relatorios\2026-09-09   # 7
python -m liquidez fatos gerar                       # 8
python -m liquidez cotas serie --ate 2026-09-09      # 8b
python -m liquidez painel                            # 9
```

A captura do Beyond vem **antes** do fluxo de propósito: assim a operação nova
entra projetada em parcelas em vez de cair na premissa de bullet.

---

## 4. Onde sai o resultado

| O quê | Onde |
|---|---|
| Relatório por fundo, em Excel | `relatorios/<data>/Liquidez_<cnpj>_<data>.xlsx` |
| Painel diário | `bi/painel.html` |
| Relatório individual, em HTML | `bi/fundos/<cnpj>.html` |
| Camada de fatos (histórico) | `bi/fato_*/data=<data>/dados.parquet` |
| Fluxo projetado | `fluxo/<data>/*.csv` |

O painel e os relatórios individuais são **autocontidos**: não dependem de
internet nem de arquivo externo. Os links entre eles são relativos, então
funcionam em qualquer pasta — desde que `painel.html` e `fundos/` fiquem lado a
lado.

> Atenção ao publicar: numa biblioteca do SharePoint acessada pelo navegador,
> os links relativos **não** funcionam, porque cada arquivo recebe uma URL de
> visualizador em vez de um caminho. Funciona em pasta sincronizada pelo
> OneDrive, ou distribuindo um `.zip`.

---

## 5. O que é mantido à mão

Boa parte da inteligência do relatório está em `config/`, não no código. Estes
arquivos foram revisados um a um e **não** se regeneram sozinhos:

| Arquivo | O que é |
|---|---|
| `config/fundos/<cnpj>.json` | a ficha de cada fundo, com `pz_resg` e `cc` |
| `config/prazos_ativos.csv` | prazo de liquidez por ativo |
| `config/isin_cotas.csv` | de-para de cota: ISIN ou código do administrador → CNPJ |
| `config/cronograma/` | cronograma das operações (planilha, manuais e Beyond) |
| `config/regras_vencidos.json` | como marcar ativo vencido, por administrador |
| `config/ativos_excluidos.json` | linhas que não entram na exposição |

Duas colunas da ficha se confundem com facilidade: **`pz_resg`** é o prazo de
resgate das cotas *daquele* fundo, usado quando *outro* fundo o detém;
**`cc`** é a janela de cotização do próprio fundo, e é ela que define as faixas
da matriz de liquidez. Sem `cc`, a geração do `.xlsx` daquele fundo falha.

Os arquivos `config/*_antes_*.csv` são backups de alterações em massa. Nada os
lê; servem para comparar e voltar atrás.

---

## 6. O que NÃO está nesta pasta

Ficou de fora porque é gerado pela rodada ou não pertence ao relatório:

- as saídas de execução — `dados/`, `bi/`, `relatorios/`, `fluxo/`, `logs/`
  vêm vazias, com um arquivo `.manter` só para a pasta existir;
- `config/planilhas/processados/` — 131 MB de planilhas de conciliação já
  consumidas. A pasta `config/planilhas/` é a **caixa de entrada** e vem vazia:
  é onde se deposita um `.xlsb` novo, com o CNPJ do fundo no nome, para o
  `cronograma sincronizar` importar;
- a planilha original `Relatório de Liquidez_Multiplica_V2.xlsm`, de onde o
  processo foi migrado — fica no projeto de origem, como referência histórica;
- estudos e projetos vizinhos que compartilhavam a mesma pasta e não fazem
  parte deste processo.

---

## 7. Para ler mais

| Documento | O que cobre |
|---|---|
| `docs/processo_liquidez.html` | o processo ponta a ponta: etapas, módulos, precedências, armadilhas |
| `docs/tratamentos.html` | as nove etapas de transformação dos dados, geradas a partir do código |
| `docs/tabelas_do_banco.html` | as tabelas de origem no SQL Server |
| `README.md` | a operação comando a comando, com todas as opções |

O código é comentado com decisões datadas e explica o *porquê*, não só o *como*.
Quando este guia e o código divergirem, o código vale mais.
