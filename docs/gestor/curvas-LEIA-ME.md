# Curvas de mercado

Pasta de entrada para os arquivos de curva baixados da B3, enquanto não há carga
automática. Mesma ideia de `config/planilhas/` e `config/cra/`: deposite aqui, o
programa lê.

## O que baixar

Arquivo **"Mercado de Derivativos – Taxas de Mercado para Swaps"**, em
[Pesquisa por pregão](https://www.b3.com.br/pt_br/market-data-e-indices/servicos-de-dados/market-data/historico/boletins-diarios/pesquisa-por-pregao/pesquisa-por-pregao/)
— escolha a data do pregão, marque o arquivo, baixe.

Disponível a partir de 12/2025. Antes disso a fonte era o portal
`www2.bmf.com.br`, que hoje não responde.

## A curva que interessa

**`DOC` — Cupom de Dólar Limpo.** É o `c` da fórmula do dólar futuro:

```
                    (1 + i_DI)^(du/252)
Fator_DOL(ndc) =  ─────────────────────────
                    1 + c × (dc/360)
```

O numerador já existe: é o `Fator_Spot` de `Mercadoria = 'DI1'` em
`TB_CURVA_JUROS_DIARIA`. Só falta o `c`.

Cuidado com **limpo × sujo**: o DDI negociado é o *sujo*, calculado contra a PTAX
do dia anterior, e carrega a variação cambial de um dia dentro dele. O que entra
na fórmula é o **limpo** (`DOC`). Puxar DDI direto deixa o primeiro vértice torto.

## Como nomear

Mantenha o nome original do arquivo. Se ele não trouxer a data, ponha na frente
em `AAAAMMDD`:

```
config/curvas/20260813_taxas_swaps.csv
```

## Para que serve

Destravar a projeção dos CRA em dólar da Multiagro — hoje `C47862` (R$ 24,3 mi) e
`C61049` (R$ 768 mil), ambos `CRADL-ECOAGRO-DOL`. Ver
`config/cra/MULTIAGRO FIDC/C47862/_LEITURA.md` para o que está bloqueado e por quê.

Sem a curva, esses papéis entram na projeção pela premissa bullet: valor da
carteira no vencimento, sem juros e sem os cupons anuais.

## Depois do primeiro arquivo

A leitura ainda não existe — vou escrever o leitor quando vir o layout real.
Se o download tiver URL previsível em `arquivos.b3.com.br`, dá para automatizar a
busca e esta pasta vira só o histórico.

O destino final é `TB_CURVA_JUROS_DIARIA` com `Mercadoria = 'DOC'`, alimentada
pelo mesmo processo que já carrega a `DI1` — este projeto só LÊ essa tabela, a
carga mora fora dele.
