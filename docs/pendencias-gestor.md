# Pendências com o gestor — MULTIPLICA FIDC, posição 30/09/2026

> **21/10/2026, depois das rodadas 19 e 20** (posição 30/09, riscogc de
> produção, config do pacote do gestor; detalhes em
> `docs/pacote-gestor-2026-10-21.md`):
> - **Item 1, resolvido exceto o TRIPOLI FIDC** (R$ 24,7 mi no MULTIPLICA
>   FIDC), que continua sem prazo. PLICAAVE e PLICAVEN ("MULTIPLICA A VENC" e
>   "MULTIPLICA VENCID") não são cota de outro fundo. São o pool de recebíveis
>   do próprio fundo, batem com o estoque (A Vencer R$ 792,5 mi, Vencidos
>   R$ 19,9 mi) e já entram pelo estoque.
> - **Item 2**: o C276922 (DIRPX) foi confirmado bullet pela gestão em
>   07/08/2026. O C321654 (DEBSI) tem cronograma manual e o C398115 saiu da
>   fila. Restam a conferir:
>   - MULTIPLICA FIDC (9, R$ 100,5 mi): C455801 CRDC, C475057, C441588,
>     C441587, C475055, C475063 e C446014 NCPX, C471829 CONFD e C459879 DIR;
>   - LONG TERM (7, R$ 32,0 mi): C448660, C457835, C468229, C452599, C456989,
>     C466565 e C472475, todos NCPX;
>   - MULTIAGRO (20, R$ 160,1 mi): lista em `fluxo --fundo "MULTIAGRO FIDC"`.
> - **Novo, vencimento divergente** entre o cronograma da planilha e a
>   carteira: LONG TERM C184002, C218965 e C391443, e 5 operações no MULTIAGRO.
>   Pode ser aditamento ou erro de leitura.
> - **CRA C47862 (MULTIAGRO)**: continua sem cronograma, porque depende da
>   curva de cupom cambial.
> - Itens 3 e 4: sem resposta no pacote. **Item 5**: PL em 30/09 de
>   R$ 2.123.626.196,25 e maior cotista de R$ 372,9 mi (17,6% do PL). É o HOD
>   II FIC FIDC, que o próprio gestor anotou como cerca de 17% do fundo. A
>   rodada 21 deu o mesmo valor nas duas fontes de passivo. Os R$ 107,85 mi da
>   rodada 15 vieram do passivo de 30/09 ainda incompleto em 05/10, porque a
>   carga do Datalake completa os dias atrasados nas rodadas seguintes. Resta
>   só confirmar com o administrador.

Atualizado em 05/10/2026, após a rodada 15. Tudo o que falta para fechar os
números do MULTIPLICA FIDC depende de resposta humana, não de código.

## 1. Prazo de cotização das 13 cotas de outros fundos — R$ 1.203,9 mi (56,7% do PL)

Hoje entram como liquidez zero por falta de prazo. É o único item capaz de
mover o caixa potencial (26,21%) de patamar. Basta "D+N" por fundo, mesmo que
venha só do regulamento.

| Codigo_Op | Ativo | Valor 30/09 |
|---|---|---|
| APLOIIMZ | APOLLO II FIDCNP MZ1 | R$ 18,5 mi |
| ARCDEBSR | ARC DEB I FIDC SR | R$ 157,2 mi |
| ARCSPCSR | ARC SPECIAL I FID SR | … |
| BBMULTFI | BBMULTCAP RFCPLP FIC | … |
| ECOAGMZA | ECOAGMUL MEZ A | … |
| PLICAAVE | (maior posição) | R$ 792,5 mi |
| + 7 | (ver `tratar --detalhar`, etapa 7) | |

## 2. Os 12 bullets a conferir — R$ 166,4 mi

Operações de DC sem cronograma cadastrado nem casamento no Beyond, projetadas
como pagamento único no vencimento da carteira. Para cada uma: é bullet mesmo,
ou tem parcelas? Se tiver, qual operação do Beyond corresponde (ou a planilha).

| Codigo_Op | Ativo | Valor | Venc. carteira | Situação no Beyond |
|---|---|---|---|---|
| C276922 | DIRPX | R$ 49,47 mi | 11/10/2027 | fora do escopo NC/CCB |
| C455801 | CRDC | R$ 20,63 mi | 06/09/2028 | fora do escopo |
| C475057 | NCPX | R$ 18,67 mi | 26/02/2027 | 4 candidatas na data, nenhuma casa em valor |
| C441588 | NCPX | R$ 15,90 mi | 15/10/2026 | gêmea de C441587 com cronograma diferente |
| C441587 | NCPX | R$ 15,90 mi | 16/10/2026 | gêmea de C441588 com cronograma diferente |
| C398115 | NCPX | R$ 11,67 mi | 30/12/2026 | sem operação na Data_Aquisicao |
| C475055 | NCPX | R$ 10,10 mi | 23/12/2027 | 2 candidatas com o mesmo valor |
| C475063 | NCPX | R$ 10,00 mi | 23/12/2027 | 4 candidatas na data, nenhuma casa em valor |
| C446014 | NCPX | R$ 7,41 mi | 30/01/2027 | 3 candidatas na data, nenhuma casa em valor |
| C321654 | DEBSI | R$ 4,65 mi | 15/12/2031 | fora do escopo |
| C471829 | CONFD | R$ 1,79 mi | 30/11/2032 | fora do escopo |
| C459879 | DIR | R$ 0,17 mi | 31/08/2049 | fora do escopo (data placeholder) |

Peso: DIRPX e CRDC (R$ 70 mi) — se amortizam, o caixa potencial está
subestimado. C441587/88 vencem dentro dos 29 dias: bullet ou não, já contam.

## 3. IMOVE — 61 linhas, R$ 17,7 mi

Imóvel (garantia/dação?) veio sob "Renda Fixa" com código de operação. Ficou
fora do DC. Como tratar na liquidez: excluído, ou prazo 999?

## 4. Regra de vencidos do Singulare

`Status` vem nulo nas 453 linhas da carteira. Estamos identificando vencido por
`Codigo_Ativo` começando com `VC` e `Qtd_Total > 0` (56 operações, R$ 94,7 mi
em 30/09). Confirmar que é a convenção.

## 5. Sanidade dos âncoras

PL R$ 2.124.084.954,08 e maior cotista R$ 107,85 mi (30/09): batem com o
administrador?

## Para o projeto do Datalake (não é do gestor)

- Carteira de 02/10/2026 veio parcial no riscogc (475 linhas em 16 fundos; o
  MULTIPLICA sozinho tinha 453 em 30/09; 0 NC/CCB na posição). Verificar a
  carga de `HOD_TB_CARTEIRA_UNIFICADA` daquele dia.
