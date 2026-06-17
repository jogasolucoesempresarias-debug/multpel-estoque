# Manual — Painel de Estoque Multpel

Guia rápido de como usar a ferramenta. Pensada para o **comprador**: ver o que comprar, o que vai vencer, o que está parado e como está a margem.

---

## Acesso
- Endereço: **estoque.jogasolucoes.com.br**
- Entre com a **senha** que foi passada para você.

---

## A barra do topo (filtros) — vale para o painel inteiro
Tudo que você seleciona aqui filtra todas as telas ao mesmo tempo.

| Filtro | O que faz |
|---|---|
| **Comprador** | Mostra só os produtos daquele comprador. Em "Empresa toda" vê tudo. |
| **Filiais** | Quais filiais entram no estoque (padrão: CDs **3 e 5**). |
| **Estoque** | **Endereçado** (estoque físico no WMS, igual à planilha) ou **Gerencial** (estoque total do sistema). |
| **Curva / XYZ** | Filtra por classe ABC (valor) e por previsibilidade da demanda (XYZ). |
| **Fornecedor / Depto** | Recorta por fornecedor ou departamento. |
| **Buscar produto** | Por código ou descrição. |
| **Venda** | Período da venda usada nos cálculos de margem (Mês / 90 dias / 12 meses). |
| **⚙ Parâmetros** | Lead time, estoque de segurança, cobertura-alvo e horizonte de validade. |

> O painel **lembra** suas escolhas (comprador, filtros) no próximo acesso.

---

## As telas

### ⚡ Minha Fila
A tela do dia a dia. Junta numa **lista priorizada** o que precisa de ação: comprar (ruptura), vencimento próximo e parado crítico. Cada item tem botão de **1 clique** (registrar pedido / criar plano de ação / abrir 360°).

### Cockpit
Visão executiva: KPIs (valor em estoque, venda, margem, em ruptura, a comprar, capital parado), **cards de alerta clicáveis**, gráfico de **cobertura** (onde está o capital) e **curva ABC**, e os **maiores ofensores** (mais capital parado / mais risco de vencimento).

### Ruptura
Produtos com **cobertura ≤ 30 dias** (risco de faltar), separados em 0-15 e 16-30 dias, com a **sugestão de compra**.

### Reposição
**O que comprar, agrupado por fornecedor.** Mostra a quantidade sugerida e o valor; botão **"Gerar pedido"** lança direto no Orçamento.

### Validade
Controle de vencimento (**FEFO**): lotes que vencem no horizonte, com **valor em risco** e gráfico por faixa de dias. Dá para registrar **plano de ação** por lote.

### Parado
Itens **sem giro / parados** por dias sem venda (atenção / crítico / muito crítico), ordenados por valor — o que precisa girar ou liquidar.

### Orçamento
Meta de compras do mês × **comprado** (pedidos lançados), com **% consumido** e saldo. Lance pedidos manualmente ou pelo "Gerar pedido" da Reposição.

### Compras × Vendas
O cruzamento completo: **estoque (compras) × venda × lucro × margem**, com botão para ver **por comprador, por fornecedor ou por produto**.

### ABC-XYZ
Matriz que cruza **valor (ABC)** com **previsibilidade da demanda (XYZ)** — ajuda a definir estratégia (ex.: AX = controle rígido, CZ = candidato a descontinuar).

### Fornecedores
Desempenho por fornecedor: estoque, giro, **cobertura média**, venda, margem e o **índice giro × estoque**. Classes: **alta performance** (gira mais do que pesa), **equilibrado**, **estoque alto** (capital empatado), **ruptura** (gira mas quase sem estoque — não é performance, é desabastecimento) e **crítico/sem giro**.

### Produtos
Explorador com todos os indicadores por produto, filtrável e ordenável.

### Produto 360° (clique em qualquer produto)
Abre um painel lateral com a história completa do item: estoque, giro, cobertura, **ponto de pedido**, sugestão de compra, **venda/lucro/margem**, reservas/trânsito e **lotes/validade**.

---

## Conceitos rápidos
- **Giro/mês**: média de venda dos últimos 3 meses (em unidades).
- **Cobertura (dias)**: por quantos dias o estoque atual dura, dado o giro. Baixa = risco de ruptura; muito alta = excesso.
- **Sugestão de compra**: quanto comprar para atingir a cobertura-alvo — **já descontando o que está em trânsito/pedido** (não manda comprar de novo o que já vem a caminho). Itens que **pararam de vender há ≥60 dias** não entram na compra: vão para a seção **"Rever"** da Reposição (o giro de 3 meses está defasado).
- **Margem**: lucro ÷ venda no período.
- **Curva ABC**: A = poucos itens que somam a maior parte do valor; C = muitos itens de pouco valor.
- **XYZ**: X = demanda estável/previsível; Z = demanda errática.

---

## Exportar e registrar
- **⬇ Excel / CSV** no topo de cada tela exporta o que está na tela.
- **Planos de ação** (validade/parado): defina responsável, ação, prazo e status — ficam **salvos**.
- **Pedidos** (Orçamento): ficam salvos e abatem a meta do mês.

> Dica: o painel é **ao vivo** (dado atual do sistema). Pequenas diferenças para a planilha antiga são esperadas — a planilha é uma foto manual; aqui é o dado do momento.
