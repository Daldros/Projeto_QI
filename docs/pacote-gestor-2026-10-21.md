# Pacote do gestor de 21/10/2026 — o que veio e o que entrou

Origem: `OneDrive_2026-10-21.zip`, pasta `Relatorio Liquidez - pacote`
(`_pacote.json`: gerado em 21/10/2026 10:19, 228 arquivos). Comparado com o
código original do gestor (`4951e2c`) e com a branch `alteracao-banco-dados`
(banco de produção, `MUL_GLD_CARTEIRA_UNIFICADA`).

**Conexão não mudou.** O pacote aponta para o HML (`10.201.1.6,1433`,
`riscogchml`). O projeto continua no riscogc de produção (`10.101.1.18,1433`).
`liquidez/db.py` e `liquidez/queries.py` não foram tocados, e o teste
`test_banco_padrao_e_o_riscogc_de_producao` continua guardando isso.

## 1. Código

Dos 25 módulos que já existiam, 24 estão **idênticos** ao original. O gestor
não mexeu em `cli`, `fluxo`, `tratamentos`, `db`, `queries` etc. desde 04/10,
e as rodadas 1 a 18 do projeto não conflitam com nada do pacote.

| Arquivo | O que é | Decisão |
|---|---|---|
| `liquidez/relatorio_excel.py` | `.xlsx` por fundo (passo `relatorio` da rotina, que falhava por falta dele) | **entrou** |
| `liquidez/documentacao.py` | gera `docs/tratamentos.html` (`python -m liquidez documentar`) | **entrou** |
| `liquidez/cra.py` | leitura de Termos de Securitização → `config/cronograma/fichas` → `manuais` (`python -m liquidez.cra ...`) | **entrou** |
| `liquidez/carregar_multiplica_fidc.py`, `filtrar_titulo_multiplica.py` | consultas avulsas da `TB_LIQUIDEZ_OPERACOES_4` (Multiplica LP/LT) | **entraram**, com `from . import db` (no pacote era `import db`, que só funciona rodando de dentro da pasta) |
| `liquidez/criar_planilha_alturas.py` | exemplo de planilha de alturas de alunos | **fora**: não é do projeto |
| `liquidez/relatorio_fundo.py` | única mudança: grafo de look-through retirado "a pedido em 21/10/2026" | **aplicada**. `grafo_fundo.py` continua na raiz, porque `analise_passivo.py`, `grafo_passivo.py` e `passivo_gestor.py` usam |
| `rodar_diario.ps1`, `rodar_periodo.ps1`, `requirements.txt` | idênticos ao original | nada a fazer |

Os três módulos principais são os mesmos que o Alyson tinha recuperado na
`develop` em 06/10 (`379c767`) e que saíram quando a `develop` foi restaurada
em 08/10.

## 2. Configuração versionada (`config/parametros/`)

Decisões da área, no nível do fundo ou do ativo, sem dado de operação:

| Arquivo | Mudança |
|---|---|
| `prazos_ativos_gestor.csv` | **novo**: a `config/prazos_ativos.csv` do gestor, 289 ativos revisados um a um. O `configurar` aplica nesta ordem: `prazos_fixos.csv` → tabela do gestor → casamento por nome com o cadastro do banco. Linha de origem `cadastro` segue o `pz_resg` da ficha (o mesmo que o `prazos sync-cadastro` dele fazia). Linha sem prazo (só `GREEN VALUE FIDC SR`) fica para o banco resolver |
| `ativos_excluidos.json` | **trocado** pela lista do gestor (colunas C e G da planilha): `padrao` = PDD, MEZAN, LFTO, PDCEX, PDCCE, SRP, PDDNC, LTNO, PDCCB (só com `Qtde_Dias <= 0`); PLANNER = SRP, MEZAN (sem condição). Antes o projeto tinha só LTNO/LFTO/NTN O para a QI. A chave da PLANNER virou `PLANNER`, para casar com qualquer grafia do cadastro |
| `fundos_overrides.csv` | 17 fundos: codinomes do Beyond (Multiplica LP, Multiplica LT, Multiagro, LIMAJ, GAD, Crown NP, Green, Persa NP, Milk, Soldi, Pettra, Murale), nome no MOV diferente do nome interno (ASPEN, HOD MULTIPLICA HIGH YIELD III) e 3 inativos (KAPAMA e MANI em liquidação, LOGIC saiu da gestão). `pz_resg`/`cc` já batiam com as 103 fichas do gestor. Codinome que o projeto já tinha (STRCTR II, Evolve, Avanti, Larca, Amadeus) foi mantido |
| `regras_vencidos.json` | só o `_estoque`: aceita `VENCIDO` e `VENCIDOS`, como no gestor. O resto do projeto já era mais completo (inclui o vencimento no passado, de 13/10) |
| `indicadores.json` | sem mudança: mesmos valores, só o comentário era diferente |

**Muda número**: a lista de excluídos e a tabela de prazos. Depois do
`configurar --aplicar`, vale comparar o caixa potencial de uma data já rodada.
MULTIPLICA, LT e Multiagro são da QI, que só exclui com `Qtde_Dias <= 0`:
nas faixas sai só o MEZAN/SRP/PDD já vencido, e o que está a vencer continua
entrando. A projeção do fluxo pelo estoque (rodada 18) não olha a marca de
excluído e não muda.

## 3. Configuração local (fora do git): `importar-pacote`

Dado por operação não vai para o git (mesma regra do `bi/`). O comando novo
copia do pacote para a `config/` da máquina, sem acessar o banco:

```bat
python -m liquidez importar-pacote --pacote "C:\...\Relatorio Liquidez - pacote"            :: simula
python -m liquidez importar-pacote --pacote "C:\...\Relatorio Liquidez - pacote" --aplicar
```

| O que copia | Por quê |
|---|---|
| `cronograma/<cnpj>.json` (MULTIPLICA FIDC, LT, Multiagro, Avanti, LIMAJ) | cronograma importado das planilhas de conciliação (`.xlsb`), que o projeto não tinha. O do MULTIPLICA FIDC tem 80 operações |
| `cronograma/manuais/` (C321654, B915955, B989830) | cadastros manuais: a debênture DEBSI-ELFAMEDI (um dos 12 bullets) e os CRAs do Multiagro |
| `cronograma/fichas/` (B915955, `_modelo`) | ficha do CRA lida do Termo (entrada do `cra.py`) |
| `cra/` | Termos do CRA C47862 (Multiagro) e a leitura deles |
| `curvas/`, `custo_de_carrego.json` | referência (nenhum código lê ainda) |
| decisões da fila de bullets | só `confirmado`/`planilha_pendente`: hoje o C276922 |

O arquivo que já existe na `config/` local com outro conteúdo é **mantido** e
listado (`--sobrescrever` troca). Uma decisão de bullet já tomada aqui vence a
do pacote.

**Não copia**, porque o projeto gera do riscogc de produção ou porque é
backup: `fundos/`, `calendario/`, `prazos_ativos.csv`, `isin_cotas.csv`,
`cronograma/beyond/` (foto do HML), `cronograma/_anterior/`, `*_antes_*`,
`*.antes-inativar`, `bullets.json.antes-planner` e o restante do
`bullets.json`.

## 4. Documentação

Em `docs/gestor/`: `README-gestor.md` (manual comando a comando),
`COMO-RODAR-gestor.md`, `processo_liquidez.html` (as nove etapas),
`tabelas_do_banco.html`, `cra-LEIA-ME.md` e `curvas-LEIA-ME.md`. **Atenção:**
nesses documentos o banco é o HML. Para o projeto vale o README da raiz e
`docs/mapeamento-banco.md`. `tratamentos.html` não entrou porque é gerado:
`python -m liquidez documentar`.

## 5. Pendências do gestor que o pacote responde

Ver `docs/pendencias-gestor.md`. Em resumo:

- **Cotas sem prazo**: APOLLO II MZ1 = 180 (ficha do Multiplica LT), ARC DEB I
  SR = 999, ARC SPECIAL I SR = 999, BB MULTCAP RF CP LP FIC = D+0, ECOAGMUL MEZ
  A = 999. O PLICAAVE não aparece pelo código de operação. A tabela casa pelo
  `Codigo_Ativo`, então só o `configurar` contra a produção vai dizer quantas
  das 13 ainda sobram.
- **Bullets**: C276922 (DIRPX, R$ 49,5 mi) confirmado como bullet pela gestão
  em 07/08/2026. O C321654 (DEBSI) agora tem cronograma manual (48 parcelas,
  CDI + 3,40%). C441587, C441588, C398115, C446014 e C459879 continuam na fila.
  C455801, C475055, C475057, C475063 e C471829 não aparecem no registro do
  gestor.
- **CRA C47862 (Multiagro)**: bullet no principal, com cupom anual. Está
  bloqueado por falta da curva de cupom cambial (`curvas-LEIA-ME.md`).
