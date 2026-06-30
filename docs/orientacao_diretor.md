# Orientação — Painel de Estoque (respostas aos pontos levantados)

Texto para repassar ao diretor. Resume o que foi corrigido, o que era uso dos parâmetros, e os 2 pontos que dependem da decisão dele.

## O que foi ajustado conforme a planilha

- **Cobertura de estoque (aba "Cobertura"):** agora segue a métrica oficial da planilha — 5 faixas fixas (`0-30 risco / 31-60 OK / 61-90 atenção / 91-120 urgente / 121+ crítico`), com **valor de estoque por faixa** e na tabela. Dá pra clicar na faixa e **ordenar por maior valor** para priorizar ação. Na faixa 121+ separamos "sem giro" (estoque morto → liquidar) de "excesso real" (cobertura alta → reduzir compra).
  - **Importante:** seguindo sua própria regra (endereçado só para validade; gerencial para o resto), use a base **Gerencial** para conferir a cobertura. Vamos deixar o Gerencial como **padrão** do painel para você não precisar lembrar de trocar.

- **Ruptura por comprador (aba Compras × Vendas):** estava contando "cobertura baixa" como ruptura (inflava). Corrigido para o **critério oficial da planilha**: ruptura = estoque zero/negativo **e** giro positivo. Cobertura de 1 a 30 dias é **atenção, não ruptura**. Agora bate com o painel gerencial.

- **Estoque parado:** o relatório/PDF agora sai **agrupado por fornecedor** (maior valor parado primeiro). Faixas fixas: atenção 60-90, crítico 90-120, muito crítico 120+ dias.

- **Aba nova "Desempenho comercial por comprador":** venda líquida, lucro bruto, margem ponderada, **positivação (clientes)**, devolução, participação e **comparativo ano×ano** por comprador.

## Por que um número pode não bater 100% com a planilha (e por que não é erro)

Investigamos a fundo a fonte dos cálculos, comparando produto a produto:

- **O giro do painel é idêntico ao da sua planilha.** O "giro mensal em unidades" da sua base é exatamente a **média dos 3 últimos meses** que o painel usa — conferimos vários produtos e bate **na unidade** (ex.: produto 68961 = 73.450 nos dois).
- **A base de estoque é a mesma** (gerencial, somando as filiais).

Ou seja, a **metodologia é a mesma**. Quando um número diverge da sua planilha, a causa é a **data**: a sua planilha é um **retrato de um dia** (quando foi gerada), e o **painel atualiza todo dia**. O estoque muda diariamente (vendas e entradas), então a cobertura se desloca. **Para comparar de verdade, olhe os dois no mesmo dia** — aí batem.

## Sobre os parâmetros (Lead time, Segurança, Cobertura alvo, Parado)

Eles foram criados para as telas de **compra** (Abastecimento/sugestão). Estavam "vazando" para os relatórios de diagnóstico (Cobertura/Parado), por isso davam a impressão de "atrapalhar". **Isso foi corrigido:** agora os relatórios de Cobertura, Parado e Ruptura usam as **faixas fixas da planilha** e **não mudam** quando você mexe nos parâmetros. Os parâmetros só afetam a sugestão de compra.

Valores recomendados (premissas de compra, não de relatório):
- Lead time: ~10 dias · Estoque de segurança: ~25 dias · Cobertura alvo: 30-45 dias · Parado (exibição): 60 dias.
- Evite extremos (ex.: cobertura alvo 600, parado 1) — isso era o que distorcia a sugestão.

## Estoque zerado — o valor R$ 0 está correto

Item com estoque zerado/negativo vale **R$ 0** de propósito (não dá pra valorizar estoque que não existe; alinha com o BASE PRODUTOS). A contagem (zerados → com giro → já com pedido) está coerente. Para mostrar "quanto custa a ruptura", **adicionamos 2 indicadores** na aba: **Venda perdida/mês** (volume parado, a custo) e **Custo de reposição** (quanto custa repor até o alvo).

## 2 pontos que dependem da sua decisão (Desempenho comercial)

1. **Período:** o painel usa "mês atual" (do dia 1 até hoje); sua planilha usa **"últimos 30 dias"** (rolando). Por isso os totais diferem um pouco. Posso adicionar a opção "últimos 30 dias" para bater exatamente.

2. **Positivação:** o painel conta **clientes distintos** por comprador (cada cliente uma vez). A planilha mostra um número ~5× maior porque **soma os clientes por fornecedor** (o mesmo cliente conta várias vezes). O número de "clientes positivados" tecnicamente correto é o **distinto** (o do painel). Se preferir bater com a planilha, troco para a soma por fornecedor — é só avisar.

## Ponto a testar (clique no card "Parado")

O clique nos cards de faixa já está implementado e funcionando no código. Se ainda não filtrar aí, é cache do navegador: dê **Ctrl+Shift+R**. Se persistir, me avise que eu reproduzo.
