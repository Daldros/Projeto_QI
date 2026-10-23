# Plano — apresentação de risco de liquidez (Multiplica LP, Multiplica LT, Multiagro)

Data: 05/10/2026. Escopo pedido pela área: só os três fundos abaixo, uma peça
que mostre o que importa e que siga a identidade visual da Multiplica.

| Fundo (cadastro) | Codinome Beyond | CNPJ | Situação dos dados em 05/10 |
|---|---|---|---|
| MULTIPLICA FIDC | Multiplica LP | 23.216.398/0001-01 | pipeline completo, posição 30/09 |
| MULTIPLICA LONG TERM FIDC | Multiplica LT | 32.510.577/0001-30 | ficha pronta; falta rodar (rodada 16) |
| MULTIAGRO FIDC | Multiagro | (cadastro) | ficha pronta; falta rodar (rodada 16) |

Protótipo navegável: artefato "Liquidez Multiplica" (link na conversa).
Versão gerada pelo código: `python -m liquidez apresentacao --fundos "..."` →
`apresentacao.html`. O material antigo continua em `python -m liquidez painel`. Ele usa os números reais do MULTIPLICA FIDC e deixa os outros dois
marcados como "aguardando rodada 16" — nada inventado.

## 1. O que está errado na apresentação atual (painel do gestor)

1. **Fala de 1 fundo e de 75 ao mesmo tempo.** O painel abre com KPIs de todo o
   universo e o leitor tem de descer até uma tabela para achar o seu fundo.
2. **Caixa Potencial sem o prazo do passivo ao lado.** 26% do PL não diz nada
   sem saber que o fundo cotiza em D+29 e quanto resgata em 29 dias.
3. **Horizontes em dias corridos, um número só.** A ANBIMA trabalha com vértices
   em dias úteis (1, 2, 3, 4, 5, 10, 21, 42, 63) e o painel aplica o mesmo
   percentil em todos.
4. **Ativo e passivo em páginas separadas.** A pergunta de liquidez é uma só:
   *em cada vértice, o que vence cobre o que pode sair?* Hoje o leitor faz a
   conta de cabeça.
5. **Sem estresse.** Não há cenário de resgate acima do histórico nem de atraso
   nos recebíveis.
6. **Premissas escondidas.** Bullet, cotas sem prazo, operações sem cronograma —
   nada disso aparece na tela, e é o que mais mexe no número.
7. **Identidade visual própria do gestor** (navy/ciano arbitrários), não a da
   Multiplica.

## 2. A versão melhorada — estrutura por página

Uma página por fundo, mesma estrutura nas três, e uma capa comparativa. A
ordem segue a pergunta do comitê: *está enquadrado? → quanto tenho? → quanto
pode sair? → cobre? → e se piorar? → em que premissas isso se apoia?*

### Capa — os três fundos numa olhada
- Semáforo por fundo (enquadrado / atenção / desenquadrado) e a razão.
- Três âncoras por fundo: PL, caixa potencial (% PL), prazo de cotização.
- Data-base e hora da carga, por insumo (carteira, estoque, Beyond, passivo).

### Bloco A — Âncoras do fundo
PL, caixa potencial no prazo de cotização, maior cotista, GDP (2,33 × desvio
dos resgates), resgate máximo histórico. Cada um com o limite da política e o
estado. Igual ao que o gestor já calcula; muda a disposição e os limites ao lado.

### Bloco B — De onde vem a liquidez (ativo)
- Composição do PL por produto (DC, cotas de outros fundos, tesouraria, renda
  fixa, outros) — barra única, direto no gráfico.
- **Escada por vértice em dias úteis** (1, 2, 3, 4, 5, 10, 21, 42, 63): quanto
  vence acumulado em cada vértice. Fonte: fluxo projetado das operações de DC
  (Beyond + cronogramas + bullet), prazos fixos (tesouraria D+0, título público
  D+0, compromissada no vencimento), cotas de outros fundos pelo prazo de
  cotização do fundo investido.
- Vencidos à parte (não entram em nenhum vértice).

### Bloco C — O que pode sair (passivo)
- Concentração: top 1, top 3, top 5, HHI, por distribuidor.
- **Resgate potencial por vértice** pela matriz ANBIMA de probabilidade de
  resgate, por segmento de cotista (institucional, private, varejo, fundo da
  casa) — esse é o ponto que o painel atual não tem. Enquanto a segmentação do
  cotista não estiver comprovada no banco, usa-se o percentil 95 do histórico
  do próprio fundo por vértice (o que já existe), e a tela diz qual método foi
  usado.
- Fundo fechado (cc = 999): passivo potencial = amortizações programadas.

### Bloco D — Cobertura ativo × passivo por vértice (o coração)
Uma tabela/gráfico só: para cada vértice, caixa disponível acumulado, resgate
potencial acumulado e a razão. Verde ≥ 1,0; amarelo entre 0,8 e 1,0; vermelho
abaixo. É a leitura da ANBIMA e da CVM 175 (art. de liquidez) num quadro.

### Bloco E — Estresse
Três cenários combinados passivo × ativo, no vértice de cotização:
1. **Resgate**: saída do maior cotista + percentil 99 dos demais.
2. **Ativo**: atraso de 30 dias em 100% dos recebíveis pulverizados; NC/CCB
   pagando só juros.
3. **Combinado**: 1 + 2.
Mostra o caixa que sobra (ou falta) em cada cenário. Parâmetros em
`config/parametros/indicadores.json`, aprovados pela área.

### Bloco F — Premissas e qualidade do dado
- Cobertura do Beyond (ex.: 50 de 57 NC com cronograma real).
- Bullets a conferir (lista nominal, valor, vencimento).
- Cotas de outros fundos sem prazo (quantas, quanto, tratadas como zero).
- Operações vencidas (fora da liquidez) e PDD.
- Data efetiva de cada insumo e se a posição do dia estava completa.

### Bloco G — Histórico (quando houver mais de uma data)
Caixa potencial / PL e cobertura no vértice de cotização ao longo do tempo;
rentabilidade por classe contra o CDI (o gestor já tem a série de cotas).

## 3. Identidade visual (guia da marca 2026)

- Fundo branco, texto Azul `#002346`, destaque Blue 3 `#215EFF`; cabeçalho e
  capa em Azul com texto branco e Neon `#00E1FF` só como acento. Logo oficial de
  `assets/logo/`, nunca redesenhado.
- Fonte Wix Madefor Display (Google Fonts), Aptos como fallback. Títulos leves,
  caixa baixa, alinhados à esquerda.
- **Gráficos de uma série só**, na cor da marca, com rótulo direto. A paleta da
  Multiplica é um azul único: ela não serve para quatro séries lado a lado (a
  validação de daltonismo falha). Quando há três fundos, são **três gráficos
  pequenos iguais**, não três cores no mesmo gráfico.
- Estado (enquadrado / atenção / desenquadrado) em Green 1 `#679940`, Yellow 1
  `#C6B261` e Red 2 `#C52225`, sempre com texto, nunca só cor.
- Cantos 12 px, cards com borda Blue 9, zebra Blue 11. Um grafismo de pontos por
  página, nunca atrás de texto.

## 4. Ordem de implementação no código

| Passo | O que | Depende de | Estado |
|---|---|---|---|
| 16 | Rodar o pipeline para LT e Multiagro; `configurar --data 30/09` para as cotas | — | rodada 16 pronta |
| 17 | `apresentacao --fundos` (novo comando; o `painel` antigo continua): capa + blocos A–F por fundo | 16 | **feito 05/10** (`liquidez/apresentacao.py`) |
| 18 | Escada por vértice em dias úteis (fluxo + carteira tratada, calendário ANBIMA) | 17 | **feito 05/10** |
| 19 | Resgate potencial por vértice: percentil 95 × PL (fato_anbima); depois matriz ANBIMA por segmento quando a segmentação do cotista estiver comprovada no banco | 18 | 1ª versão feita; matriz por segmento pendente |
| 20 | Bloco D (cobertura) e bloco E (estresse, 3 cenários aprovados pela área em 05/10) | 19 | **feito 05/10**; parâmetros em código, a externar em `indicadores.json` |
| 20b | Look-through das cotas de FIDC: cota com cotização D+30 não é liquidez garantida — o fundo investido tem o mesmo passivo; medir pelo estoque dele (tabela 3 do gestor) em vez do prazo nominal | 16 | planejado (pedido da área, 05/10) |
| 21 | Histórico com mais datas (reprocessar setembro) | 17 | janela de execução |
| 22 | Rotina `python -m liquidez rodar` encadeando tudo, depois da carga do Datalake | 17 | — |

## 5. Decisões da área registradas em 05/10/2026

1. Cotas de outros fundos: prazo pelo cadastro do fundo investido (todo o
   cadastro, não só os com carteira) ou pelo de-para manual
   `config/parametros/cotas_depara.csv`; sem prazo conhecido, liquidez zero
   (conservador), e a tela mostra quanto está nessa condição.
2. Bullets sem cronograma: seguem como pagamento único no vencimento (premissa
   conservadora para recebível pulverizado e CRDC), listados nominalmente.
3. IMOVE: prazo 999 (fica no PL, fora da liquidez).
4. Vencidos no Singulare: `Codigo_Ativo` começando com `VC` e `Qtd_Total > 0`.
5. PL e maior cotista do MULTIPLICA FIDC em 30/09 conferidos pela área.
