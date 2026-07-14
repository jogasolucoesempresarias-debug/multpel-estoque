# Manual completo — Painel de Estoque Multpel (base de conhecimento do agente)

Documento único e **atualizado** para alimentar o agente de IA de dúvidas. Explica **todas as abas, funções, cálculos e contas** do painel. Cada seção é auto-contida (pensada para busca/RAG). Onde houver fórmula, ela está exatamente como o sistema calcula.

> Substitui, para fins do agente, os antigos `MANUAL.md` e `MANUAL_TECNICO.md` (que estavam defasados: falavam de "Minha Fila", ABC por valor de estoque e toggle Endereçado/Gerencial, que **não existem mais**).

---

## 1. Visão geral

Painel web de **gestão de estoque (compras × vendas)** sobre o Power BI da Multpel, focado no **comprador**: o que comprar, o que vai vencer, o que está parado, ruptura, cobertura/giro, orçamento e cruzamento compras × vendas.

- **Endereço:** estoque.jogasolucoes.com.br (login por senha única).
- **Stack:** Flask (Python) + Power BI (DAX via `executeQueries`) + Postgres (estado editável: orçamento/pedidos/planos) + front SPA em JS puro (Chart.js).
- **Fonte dos dados:** dataset Power BI **"Estoque"** (Winthor: PCEST, PCPRODUT, PCFORNEC, PCEMPR, PCEMBALAGEM, PCESTENDERECO, PCPEDIDO, PCITEM) + dataset **"RCA"** (faturamento/venda: FATURAMENTO_VENDAS/DEVOLUCAO).
- **Atualização:** o cabeçalho mostra "BI atualizado …" (data do último refresh do Power BI). O app cacheia dados pesados por 30 min.

---

## 2. Unidades de negócio e filiais

O seletor **UNIDADE** define quais filiais entram no estoque e na venda:

| Unidade | Filiais de ESTOQUE | Filiais de VENDA |
|---|---|---|
| **Atacado** (padrão) | 3, 5 | 3, 7, 8 |
| A&M | 4 | 4 |
| AC | 14 | 14 |
| JID | 9 | 9 |
| Todas | 3, 5, 4, 14, 9 | 3, 7, 8, 4, 14, 9 |

- **Estoque** (posição física) e **Venda** (faturamento) vivem em filiais diferentes por unidade — por isso os dois conjuntos.
- O rótulo do cabeçalho (ex.: "3,5,7,8 – Atacado") mostra a união de estoque+venda; o estoque em si é só **3,5** no Atacado.

---

## 3. Barra de filtros (vale para o painel inteiro)

Tudo aqui filtra **todas as telas** ao mesmo tempo. O painel **lembra** as escolhas no próximo acesso.

| Filtro | O que faz |
|---|---|
| **Curva** | Filtra por classe **ABC (de venda)**. Nas abas **Fornecedores** e **Compras×Vendas "por fornecedor"**, filtra pela ABC **do fornecedor** (não do produto). |
| **XYZ** | Filtra por previsibilidade da demanda (X/Y/Z). |
| **Fornecedor** | Por código ou razão social. |
| **Depto** | Por departamento. |
| **Buscar produto** | Por código ou descrição. |
| **Venda** | Período do faturamento usado em venda/lucro/margem: **Últimos 90d / 6 meses / 12 meses / mês**. |
| **⚙ Parâmetros** | Ajusta lead time, estoque de segurança, cobertura-alvo, horizonte de validade, base de giro (média3/forecast/sazonal) e "arredondar por caixa". |
| **✕ Limpar** | Zera os filtros. |

> **Importante:** a **curva ABC muda conforme o período de VENDA selecionado** (é o ABC do que vende naquele período). Já a **venda perdida NÃO muda** com o filtro (usa janelas fixas — ver §4.9).

---

## 4. Glossário de cálculos (as "contas" do sistema)

Estes conceitos aparecem em várias telas. Valores-padrão dos parâmetros entre parênteses.

### 4.1 Estoque disponível (QTDISP)
> **Disponível = QTESTGER − avaria (QTBLOQUEADA) − reserva (QTRESERV)**, somado nas filiais de estoque da unidade (ex.: 3 e 5 no Atacado).

- É o **gerencial líquido**: itens em avaria ou reservados **não** estão disponíveis para venda (decisão do diretor, 07/2026).
- Usado em **tudo**, exceto na **Validade/FEFO**, que usa o estoque **endereçado** (PCESTENDERECO, RUA≠99) por lote.

### 4.2 Giro (demanda)
> **Giro mensal = média dos 3 últimos meses** = (QTVENDMES1 + QTVENDMES2 + QTVENDMES3) ÷ 3.
> **Giro diário = giro mensal ÷ 30.**

- Opcional no ⚙ Parâmetros: **Forecast** (média móvel da venda real do RCA) ou **Forecast sazonal** (fator do mês, ano-a-ano). Padrão = **média 3m (oficial do TI)**.

### 4.3 Cobertura (dias)
> **Cobertura = ARREDONDA.CIMA(disponível ÷ giro diário).** Giro 0 → não calculável (cai na faixa 121+).

Faixas fixas (métrica oficial da planilha): **0-30 (risco ruptura) · 31-60 (OK) · 61-90 (atenção) · 91-120 (urgente) · 121+ (crítico)**.

### 4.4 Estoque-alvo e ROP
> **Estoque-alvo = giro diário × (lead + cobertura-alvo).**

- **lead** = prazo até a mercadoria chegar. **É o parâmetro "LEAD TIME (DIAS)" da tela** (padrão 10) — vale para todos os fornecedores (decisão 07/2026; antes usava o prazo cadastrado, mas o slider não afetava quem tinha prazo). O comprador controla manual.
- **cobertura-alvo** = parâmetro "COBERTURA ALVO" (padrão **45** dias).
- O lead entra na conta porque o estoque **continua caindo** até a mercadoria chegar. Ex.: lead 10 + cobertura 45 = alvo de **55 dias** de giro.
- **Estoque de segurança** (padrão 25 dias) e o ROP entram na **classificação de status** (urgente/alta), não na quantidade sugerida.

### 4.5 Já pedido (pedido de compra real em aberto)
> **Já pedido = Σ max(0, QTPEDIDA − QTENTREGUE)** dos pedidos **ativos** do Winthor (PCPEDIDO×PCITEM), últimos 180 dias.

- É o que **já foi comprado e ainda não chegou**. Como o gerencial já reflete o recebido, só o **aberto** entra na projeção (senão conta em dobro).

### 4.6 Estoque projetado
> **Projetado = disponível + já pedido.**

### 4.7 Sugestão de compra
> **Sugestão = max(0, estoque-alvo − estoque projetado).**

- Sai **em caixas** (arredonda pra cima pelo fator de caixa `QTUNIT` do PCEMBALAGEM; fallback `QTUNITCX`). Item **sem fator de caixa** cadastrado sai em **unidades ("un")**.
- Desconta o já-pedido → o "quanto comprar" fica menor do que o buraco do estoque-alvo (é melhoria, não divergência).
- **Compra suspensa:** item com giro no histórico mas **sem vender há ≥60 dias** → não sugere comprar (estoque morto).

### 4.8 Curva ABC (de VENDA)
> Pareto do **faturamento** do período selecionado. **A** = itens que somam até **80%** da venda; **B** = de 80% a **95%**; **C** = os 5% restantes.

- Classifica **o que mais vende** (leitura clássica). **Muda com o período de VENDA** selecionado.
- Também existe **ABC do fornecedor** (Pareto da venda por fornecedor) nas abas Fornecedores e Compras×Vendas "por fornecedor".
- No cockpit há um **toggle "Vendas | Estoque"**: "Estoque" mostra a mesma curva mas pela concentração de **capital em estoque** (segunda lente).

### 4.9 Venda perdida (na ruptura)
> **Venda perdida = dias em ruptura × giro diário × preço de venda.**

- **dias em ruptura** = `dias_sem_venda` (dias desde a última venda), com **teto de 60 dias**.
- **preço de venda** = **realizado médio dos últimos 3 meses** (janela FIXA — não muda com o filtro de período; alinhada com a janela do giro). O preço de tabela do BI (PCPRODUT[PVENDA]) está vazio, então usa o realizado 3m; **fallback no custo** se o item não teve venda em 3m.
- Só para item **em ruptura** (estoque ≤ 0 e giro > 0). É o **valor acumulado** que se deixou de vender no período parado (não é mais "por mês"/30 dias fixos).
- Mostrada nas abas **Estoque zerado** e **Ruptura por comprador**.

### 4.10 Custo de reposição
> **Custo de reposição = sugestão de compra × custo unitário (CUSTOFIN).**

- É "o que **falta comprar** até o estoque-alvo", **a custo**. Diferente da venda perdida (que é a preço de venda). Por isso os dois totais **não batem**: um é a perda no período parado (a preço de venda), o outro é o custo do que falta comprar (a custo, descontando o já-pedido).

### 4.11 XYZ (previsibilidade da demanda)
> Coeficiente de variação (CV) da série de venda dos 3 meses. **X** = CV < 0,5 (estável) · **Y** = 0,5 a 1,0 (variável) · **Z** = ≥ 1,0 (errático).

### 4.12 Cubagem (m³) e Peso (kg)
- **Cubagem da caixa** = `PCEMBALAGEM[VOLUME]`; se vazio, deriva de `PCPRODUT[VOLUME]` × fator de caixa.
- **Cubagem do pedido** = Σ (caixas sugeridas × volume da caixa).
- **Peso da caixa (kg)** = `PCEMBALAGEM[PESOBRUTO]`. **Peso do pedido** = Σ (caixas × peso da caixa).

### 4.13 Cód. de fábrica e IPI
- **Cód. de fábrica** = `PCPRODUT[CODFAB]` (o código do fabricante; ~85% preenchido).
- **IPI %** = `PCPRODUT[PERCIPI]`.

### 4.14 Orçamento de compras
> **Meta = 65% da venda líquida de 30 dias** por comprador (RCA).
> **Comprado (realizado) = Σ VLTOTAL** dos pedidos **do Winthor** (PCPEDIDO) emitidos no mês.
> **Saldo = meta − comprado.** **% consumido = comprado ÷ meta.**

- **Pedidos da nossa plataforma** (criados no app, pendentes de envio ao Winthor) **NÃO** somam no realizado — só contam quando forem lançados no Winthor e voltarem pela base oficial (evita contagem dupla).

### 4.15 Ruptura, Parado e Dias sem venda
- **Ruptura (real)** = estoque ≤ 0 **e** giro > 0 (vende mas acabou).
- **Dias sem venda** = dias desde a última saída (DTULTSAIDA).
- **Parado** = com estoque (qtdisp > 0) e **≥15 dias** sem venda. Faixas: **15-30, 31-60, 61-90, 91-120, 121+** (nunca-vendeu cai em 121+).

---

## 5. Navegação (5 grupos)

**Visão** · **Comprar** · **Pedidos** · **Estoque** · **Análise** — cada um com sub-abas.

---

## 6. VISÃO

### 6.1 Cockpit
Visão executiva do dia.
- **KPIs:** Valor em estoque · Venda (período) · Margem · Em ruptura (qtd) · A comprar (qtd + valor sugerido) · Capital parado (valor + % do estoque).
- **Alertas de ação (cards clicáveis):** Em ruptura (estoque ≤ 0) · Cobertura crítica (≤15d) · Comprar (cobertura baixa) · **Vencimento ≤7 dias** (mostra o valor de risco **só dos lotes ≤7d**) · Parado 120+ dias. Cada card leva à tela filtrada.
- **Curva ABC (vendas):** gráfico + tabela (A/B/C com nº de itens, valor, % dos itens, % da venda) e **toggle Vendas|Estoque**.
- **Maiores ofensores:** capital parado e risco de vencimento (top 6 cada).

### 6.2 Painel gerencial
Réplica dos blocos-resumo do relatório gerencial do diretor (cobertura por faixa, itens a vencer por faixa, ruptura), com placares e percentuais.

---

## 7. COMPRAR

### 7.1 Abastecimento — "o que comprar (por fornecedor)"
A tela principal de compra. Lista, **agrupada por fornecedor**, os itens com sugestão de compra > 0.
- **Cabeçalho de cada fornecedor:** nº de itens · **m³** (cubagem do pedido) · **kg** (peso do pedido) · **valor total** · botão **"Gerar pedido"** (abre o construtor de pedido já preenchido).
- **Colunas:** Cód · Produto · Embalagem · Disp. · Já ped. · Cob.proj · Giro/mês · Sugerido (cx) · m³ · Valor sug. · Status.
- **Fórmula na tela:** Sugestão líquida = estoque-alvo (giro/dia × (lead + Nd)) − estoque projetado (disponível + pedido real em aberto), em caixas.

### 7.2 Estoque zerado (e negativo)
Todos os produtos com estoque gerencial ≤ 0.
- **KPIs:** Zerados/negativos · Com giro (ruptura real) · Já com pedido · **Venda perdida (ruptura)** · Custo de reposição.
- **Colunas:** Cód · Produto · Fornecedor · **ABC** · Comprador · Estoque · **Dias s/ venda** · Já ped. · Giro/mês · Sugerido (cx) · Status. Filtro por status.

### 7.3 Plano reposição (DRP)
Grade semanal de um produto: projeta o saldo semana a semana, gera **pedidos planejados** quando cruza o estoque de segurança, e calcula **quando o pedido precisa sair** (= recebimento − lead time). Sem dados de trânsito no BI → reabastecimento é planejado.

---

## 8. PEDIDOS

### 8.1 Orçamento
Meta de compras do mês × realizado (ver §4.14).
- **KPIs:** Meta do mês · Comprado (Winthor) · Saldo · Consumido (%), com barra de progresso.
- **Alertas:** Entregas atrasadas · Chegam em ≤7 dias.
- **Orçamento por comprador:** tabela com Meta/Comprado/Aberto/Saldo/Consumido por comprador (só na visão "Empresa toda"). Ordenável.
- **Acompanhamento de pedidos em aberto:** pedidos reais do Winthor ainda não recebidos, com previsão de entrega e status (Atrasado / Chega ≤7d / No prazo). **Clicar num pedido abre os itens comprados** (Pedida/Entregue/A entregar). Ordenável.
- **Pedidos da nossa plataforma:** pedidos criados no app (pendentes de envio ao Winthor). **Não somam na meta.** Cada um tem PDF e remover (✕).
- **Previsão de entrega (híbrida):** usa a `DTPREVENT` do Winthor quando é previsão real (posterior à emissão); senão = data do pedido + lead do fornecedor.

### 8.2 Logística — cubagem & ocupação (aba OCULTA)
> Está **oculta do menu** a pedido do diretor (não usava para análise). Continua no código, reversível.

Calculava, dos pedidos **em aberto**, a **cubagem** (Σ qtd × volume unitário) e a **ocupação** (cubagem ÷ 60 m³/veículo), marcando **baixa ocupação** como candidato a consolidar carga.

---

## 9. ESTOQUE

### 9.1 Cobertura de estoque por faixa
Distribuição do capital por faixa de cobertura (métrica oficial).
- **Cards por faixa** (0-30 … 121+) com valor de estoque + gráfico + "por comprador".
- **Filtro de faixa = multi-seleção** (marque várias). No **121+** há sub-filtro "sem giro × excesso real".
- **Colunas:** Cód · Produto · Fornecedor · **ABC** · Comprador · Disp. · Disp. cx · Valor estoque · Cob. · Já ped. · Giro/mês · Giro cx · Sugerido · Faixa.

### 9.2 Parado — "o que liquidar"
Itens parados (com estoque, ≥15 dias sem venda). **Reconciliado como a Cobertura:** as faixas **somam o total** (usa o mesmo particionamento `parado_faixa`).
- **Cards por faixa** (15-30 … 121+) + gráfico + "por comprador".
- **Filtro de faixa = multi-seleção** (substituiu o antigo slider "Dias parados").
- **Colunas:** Cód · Produto · Fornecedor · **ABC** · Última venda · Dias parado · Disp. · Disp. cx · Valor · Saída · Faixa · Ação (plano).

### 9.3 Validade / FEFO
Controle de vencimento por lote (estoque **endereçado**, PCESTENDERECO, RUA≠99), horizonte configurável (⚙ Parâmetros).
- **Valor em risco por lote** = max(0, saldo projetado) × custo, onde saldo projetado = qtd do lote − (giro/dia × dias até vencer).
- **Cards por faixa de validade** (0-15, 16-30, 31-60, 61-90, 90+) + gráfico + "Vencimento por comprador".
- **Colunas:** Cód · Produto · **ABC** · Lote · Validade · Dias · Qtd · Saldo proj. · Valor risco · Classe · Ação. Classes: crítico (≤7d) · atenção (≤15d) · planejar.
- **Nome do produto:** vem do próprio lote (LOOKUPVALUE no PCPRODUT), então itens zerados no gerencial (que só têm lote) aparecem com o nome certo, não "PRODUTO {código}".

### 9.4 Ruptura por comprador
Ruptura agregada, em **duas tabelas**: **por comprador** e **por curva ABC** (de venda).
- **KPIs:** Itens em ruptura (+ sem pedido) · Venda perdida/mês · Custo de reposição · Compradores.
- **Colunas:** Produtos · Em ruptura · % Rupt. · **Dias rupt. méd** (média de dias sem venda dos itens em ruptura — quem demora mais a reagir) · Sem pedido · **Venda perdida** (a preço de venda, §4.9) · Custo reposição. **Linha de TOTAL** em cada tabela.
- **Clicar numa curva (A/B/C)** abre os itens daquela curva (Estoque zerado filtrado) — mostra quanto da ruptura está em cada curva de venda (A = campeões).

---

## 10. ANÁLISE

### 10.1 Desempenho comercial (por comprador)
Ranking dos compradores por venda líquida, lucro, margem, positivação (clientes distintos), devolução, participação e comparativo ano-a-ano (YoY).

### 10.2 Compras × Vendas
Cruzamento **estoque (compras) × venda × lucro × margem**, em 3 visões: **por comprador · por fornecedor · por produto**.
- **Por produto:** tabela de itens com coluna **ABC**, Estoque R$, Venda R$, Lucro R$, Margem, Giro/mês, Cob.
- **Por fornecedor:** agrega por fornecedor (Itens, Estoque, Venda, Lucro, Margem, **Venda/Estoque** = quantas vezes o capital girou, Ruptura, % Rupt., Parado) + coluna **ABC do fornecedor**. Aqui o filtro Curva age pela ABC **do fornecedor**.

### 10.3 Fornecedores — giro × estoque
Desempenho por fornecedor.
- **Índice = % na venda (R$) ÷ % no estoque (R$)** (>1 = vende mais do que pesa em estoque).
- **Classificação:** alta_performance (índice ≥1,2) · equilibrado (≥0,8) · estoque_alto (<0,8) · ruptura (gira mas cobertura < lead) · crítico s/ giro.
- Coluna **ABC do fornecedor** (Pareto da venda por fornecedor). O filtro Curva age pela ABC do fornecedor.

### 10.4 ABC-XYZ (matriz)
Cruza **curva de vendas (ABC)** × **variabilidade da demanda (XYZ)** — 9 células, cada uma com nº de itens e **venda** do período. Estratégias: **AX** = campeão previsível (controle rígido, estoque enxuto); **AZ** = alto valor + imprevisível (foco do comprador); **CZ** = candidato a descontinuar. Clique numa célula lista os produtos.

### 10.5 Produtos (Explorador)
Tabela completa de todos os produtos (Cód, Produto, Fornecedor, ABC, XYZ, Disp., Disp. cx, Avaria, Giro/mês, Giro cx, Cob., Dias s/V, Venda, Lucro, Margem, Estoque…). **Colunas Cód + Produto congeladas** ao rolar lateralmente. Filtro de "Abastecimento" (status) multi-seleção.

### 10.6 Qualidade da base
Lista itens com problema de cadastro (sem custo, sem giro, sem fornecedor, etc.) para o TI/diretor corrigir.

---

## 11. Exportações e PDF do pedido

- **Todas as tabelas** exportam **Excel** e **PDF** (respeitam os filtros da tela).
- **PDF de Produtos (Explorador):** sai **agrupado por fornecedor**, com cabeçalho por grupo (nº de itens · Σ estoque · Σ já pedido) e coluna **"Já ped."**.
- **PDF do Pedido de compra (estilo relatório 211 do Winthor):** logo Multpel + bloco **Emitente** (Multpel) + bloco **Fornecedor** (CNPJ/IE/endereço do PCFORNEC) + tabela **Cód · Cód fábrica · Produto · Un · Qtde · Custo un. · IPI% · Vlr. Total**, em **retrato**, ordenado por código, com **total do pedido + peso total (kg)**. Arquivo salvo com o **nome do fornecedor**.
- **Drills:** clicar num pedido em aberto (Orçamento) → itens comprados; clicar numa curva (Ruptura por curva) → itens da curva; clicar num item → 360° do produto.

---

## 12. Parâmetros (⚙) — valores padrão

| Parâmetro | Padrão | Onde entra |
|---|---|---|
| Lead time (dias) | 10 | estoque-alvo, status |
| Estoque de segurança (dias) | 25 | status de abastecimento |
| Cobertura-alvo (dias) | 45 | estoque-alvo |
| Horizonte de validade (dias) | 30 (o diretor usa 120) | aba Validade |
| Base de giro | média 3m | giro (ou forecast/sazonal) |
| Arredondar por caixa | sim | sugestão em caixas fechadas |
| Curva ABC (A / B) | 80% / 95% | classificação ABC |
| XYZ (X / Y) | CV 0,5 / 1,0 | classificação XYZ |

---

## 13. Perguntas frequentes (para o agente)

- **A curva ABC muda quando troco o período de venda?** Sim — o ABC é do faturamento do período selecionado. Já a **venda perdida não muda** (usa preço realizado de 3m, janela fixa).
- **Por que a venda perdida e o custo de reposição têm totais diferentes se ambos são valores?** São coisas diferentes: venda perdida = o que se deixou de vender no período parado, **a preço de venda**; custo de reposição = o que falta comprar até o alvo, **a custo** (e desconta o já-pedido).
- **O pedido da "nossa plataforma" conta na meta do comprador?** Não — só quando for lançado no Winthor e voltar pela base oficial (PCPEDIDO). Evita contar duas vezes.
- **Por que um pedido do Winthor não aparece no acompanhamento?** Provável cache (30 min) ou o BI do cliente estava sem atualizar. Um redeploy/refresh resolve.
- **O estoque é o total do sistema?** É o **gerencial líquido** (QTESTGER − avaria − reserva) das filiais da unidade. A Validade usa o **endereçado** por lote.
- **Existe curva ABC por fornecedor?** Sim — coluna ABC nas abas Fornecedores e Compras×Vendas "por fornecedor" (Pareto da venda por fornecedor).
- **O que é "Dias rupt. méd"?** A média de dias sem venda dos itens em ruptura daquele comprador/curva — indica quem demora mais a reagir à falta.
- **De onde vem o preço de venda usado na venda perdida?** Do realizado médio dos últimos 3 meses (o preço de tabela do Winthor está vazio). Quando o TI preencher o `PVENDA`, dá para trocar a fonte.

---

*Última revisão: 07/2026. Fonte da verdade: código do app (`core.py` = motor de cálculo, `app.py` = rotas/dados, `queries.py` = DAX, `static/estoque.js` = telas). Quando o comportamento divergir deste manual, o código manda.*
