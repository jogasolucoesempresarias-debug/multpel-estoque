# Manual Técnico — Painel de Estoque Multpel

Documentação completa da aplicação: arquitetura, fontes de dados, **todos os cálculos**, telas, parâmetros, persistência, limitações e deploy.
Para o guia rápido do usuário (comprador), ver [MANUAL.md](MANUAL.md).

> Stack: Flask (Python) + Power BI (DAX via `executeQueries`) + Postgres (estado editável) + frontend SPA vanilla JS (Chart.js). Sem framework no front.

---

## 1. Arquitetura

| Arquivo | Papel |
|---|---|
| `app.py` | Flask: rotas/endpoints, montagem dos produtos, export CSV/XLSX, cache helpers |
| `pbi.py` | Infra Power BI: token OAuth, `executeQueries`, cache TTL em memória, retry |
| `queries.py` | Builders das queries DAX (espelham a metodologia oficial do TI) |
| `core.py` | **Motor de cálculo puro** (sem I/O): giro, forecast, cobertura, ABC/XYZ, ROP, sugestão, FEFO, plano DRP, cockpit, fornecedores, compradores |
| `store.py` | Persistência Postgres (orçamento, pedidos, planos de ação) |
| `index.html` + `static/estoque.js` + `static/estoque.css` | Frontend (página única, várias views, tema dark) |

**Fluxo:** o front pede `/api/snapshot` (e outros) → `app._build_produtos()` busca os dados no Power BI (com cache), faz o **join em Python por `CODPROD`** e chama `core.construir_produtos()` → devolve a lista enriquecida + agregações. O front deriva quase tudo client-side a partir desse snapshot.

---

## 2. Fontes de dados (Power BI)

Dois datasets no mesmo workspace (`group_id` no `.env`):

- **Estoque** (`POWERBI_DATASET_ID_ESTOQUE`): posição, WMS/endereço, validade.
  - `PCEST` — posição por filial×produto (QTESTGER, QTRESERV, QTBLOQUEADA, QTPENDENTE, QTTRANSITO, QTVENDMES1..3, CUSTOFIN, DTULTSAIDA…).
  - `PCESTENDERECO` + `PCENDERECO` — estoque endereçado por lote/validade (QT, DTVAL, NUMLOTE; filtro RUA≠99).
  - `PCPRODUT` — cadastro de produto (filtrado a **REVENDA="S" e OBS2≠"FL"**).
  - `PCFORNEC` — cadastro de fornecedor (FORNECEDOR, CODCOMPRADOR, PRAZOENTREGA, QTUNITCX via produto…).
- **RCA** (`POWERBI_DATASET_ID_RCA`): faturamento real e nomes de comprador.
  - `FATURAMENTO_VENDAS` / `FATURAMENTO_DEVOLUCAO` / `FATURAMENTO_DEVOLUCAO_AVULSA` — venda líquida por produto.
  - `PCEMPR` — nome do comprador (matrícula → nome).
  - `CALENDARIO` — tem `ANO`/`MES`/`AnoMes`; histórico desde jan/2024 (≥24 meses).

**Notas de modelagem:** `PCEST` é tabela-ilha (sem relacionamento) → merge por `CODPROD` em Python. `CODFILIAL` é texto. Filtro de filial via `CALCULATETABLE`. `INFO.TABLES()` é bloqueada no tenant → usar `INFO.VIEW.*` (ver `test_novas_tabelas.py`).

### Cache (em memória, `pbi.TTLCache`)
| Dado | TTL |
|---|---|
| Token Power BI | ~50 min |
| Cadastro produto/fornecedor, filiais, compradores | 24 h |
| Snapshot PCEST, estoque endereçado | 30 min |
| Venda líquida (RCA) por período | 30 min |
| **Venda mensal (RCA) p/ forecast** | 12 h |

---

## 3. Parâmetros (configuráveis em ⚙ Parâmetros)

`core.DEFAULTS` — o front envia via querystring; `core.merge_params()` faz o cast.

| Parâmetro | Default | O que controla |
|---|---|---|
| `base_estoque` | `endereco` | QTDISP: endereçado (WMS) ou gerencial |
| `giro_base` | `media3` | Base do giro oficial: média 3m ou último mês (`m1`) |
| `lead_time` | 10 | Lead time fallback (dias) quando o fornecedor não tem prazo |
| `dias_seguranca` | 25 | Estoque de segurança (dias) |
| `cobertura_total` | 45 | Cobertura-alvo (dias) → define o tamanho do pedido |
| `ruptura_dias` | 30 | Cobertura ≤ isso = ruptura |
| `horizonte_val` | 30 | Janela de risco de vencimento (FEFO) |
| `parado_atencao` | 60 | **"Parado: dias sem venda" (X)** — corte do estoque parado |
| `excesso_cob` | 120 | Cobertura acima disso = excesso |
| `abc_a` / `abc_b` | 80 / 95 | Cortes da curva ABC (% acumulado) |
| `xyz_x` / `xyz_y` | 0.5 / 1.0 | Cortes do coeficiente de variação (XYZ) |
| `forecast` | 0 | Liga o forecast (RCA) no lugar da média 3m |
| `forecast_sazonal` | 0 | Liga o fator sazonal (implica forecast on) |
| `forecast_meses` | 6 | Janela da média móvel do forecast simples |
| `arredonda_cx` | 1 | Arredonda sugestão/pedido para caixa fechada (QTUNITCX) |

> `parado_critico`/`parado_mcritico` existem no DEFAULTS mas **não são mais usados**: os níveis são derivados de `parado_atencao` (X, X+30, X+60).

---

## 4. Cálculos (core.py) — definições exatas

### 4.1 Estoque e valor
- **QTDISP**: se `gerencial` → `QTESTGER − QTRESERV − QTBLOQUEADA`; senão (`endereco`, oficial) → `SUM(PCESTENDERECO[QT])` com RUA≠99 nas filiais.
- **valor** = `QTDISP × CUSTOFIN`.

### 4.2 Giro (3 modos) — é o insumo de quase tudo
- **Média 3m (oficial):** `round((QTVENDMES1 + QTVENDMES2 + QTVENDMES3) / 3)`.
- **Forecast (RCA):** média móvel **simples** da QT vendida real nos últimos `forecast_meses` meses fechados (`previsao_giro_mensal`). Cai para média 3m se o produto não tiver histórico.
- **Forecast sazonal:** `previsao_giro_sazonal` = `nível × fator[mês]`, onde:
  - `nível (media_mensal)` = média dos últimos 24 meses (naturalmente dessazonalizada);
  - `fator[m]` = média do mês-calendário *m* ÷ nível, **limitado a [0,3 ; 3,0]**;
  - o giro de capa usa o fator do **mês corrente**. Exige ≥12 meses; senão cai no forecast simples.
- **giro_dia** = `giro_mes / 30`.

### 4.3 Cobertura, segurança, ponto de pedido, alvo
- **cobertura (dias)** = `QTDISP / giro_dia` (só se `giro_dia>0` e `QTDISP>0`; senão `∞`/—).
- **estoque de segurança** = `giro_dia × dias_seguranca`.
- **ROP (ponto de pedido)** = `giro_dia × lead + estoque_segurança`.
- **estoque-alvo** = `giro_dia × cobertura_total`.
- **lead efetivo** = `PRAZOENTREGA` do fornecedor se > 0; senão `lead_time` default.

### 4.4 Sugestão de compra
- **posição efetiva** = `QTDISP + QTTRANSITO + QTPENDENTE` (não recompra o que já vem).
- **sugestão** = `max(0, estoque_alvo − posição)`.
- **caixa fechada** (se `arredonda_cx` e `QTUNITCX>1`): arredonda **para cima** ao múltiplo de `QTUNITCX` → exibe "X cx · Y un". Guarda `sugestao_bruta` (sem arredondar) para referência.
- **compra suspensa**: `giro_dia>0 E dias_sem_venda ≥ parado_atencao`. Item que "parou" mas ainda mostra giro (histórico defasado) → **sai da compra** e vai para a seção "Rever" da Reposição.

### 4.5 Status de abastecimento (`status_abast`)
Avaliado em ordem: `sem_giro` (giro≤0 com estoque) · `urgente` (sem estoque, ou cobertura ≤ lead) · `alta` (cobertura ≤ lead + segurança) · `atencao` (cobertura ≤ cobertura_total) · `excesso` (cobertura > excesso_cob) · senão `ok`.

### 4.6 Ruptura (`status_ruptura`)
Só para itens com giro. Por cobertura: `0-15` (≤15 dias), `16-30` (≤ `ruptura_dias`), senão sem ruptura. `estoque_zero` = QTDISP ≤ 0.

### 4.7 Estoque parado (`status_parado`) — campo X único
Só para itens **com estoque**. Por `dias_sem_venda = hoje − DTULTSAIDA`, com `X = parado_atencao`:
- nunca vendeu (sem DTULTSAIDA) → `muito_critico`;
- `≥ X+60` → `muito_critico`; `≥ X+30` → `critico`; `≥ X` → `atencao`; senão não é parado.

`status_saida` (idade da última saída): `recente` ≤30d · `media` ≤90d · `antiga` >90d · `sem_saida`.

### 4.8 Curvas ABC e XYZ
- **ABC (Pareto):** ordena por `valor` desc, acumula %; A ≤ `abc_a`, B ≤ `abc_b`, C resto. (Também calcula `curva_giro` por `giro_mes`.) A coluna CURVA do BI vem vazia → é recalculada.
- **XYZ (variabilidade):** `cv = desvio_padrão / média` da série de 3 meses; `X` se cv < `xyz_x`, `Y` se < `xyz_y`, senão `Z`. Sem média → sem XYZ.
- **Matriz ABC-XYZ:** `curva_abc + xyz` (ex.: AX, CZ).

### 4.9 Venda, lucro, margem (RCA, líquido)
- **venda** = VENDA BRUTA − devoluções − devoluções avulsas (período do filtro "Venda").
- **lucro** = venda − custo vendido (líquido); **margem** = `lucro / venda`.

### 4.10 Validade / FEFO (`validade_fefo`)
Por lote (produto+lote+DTVAL) na janela `horizonte_val`:
- `dias_para_vencer` = DTVAL − hoje;
- `consumo_proj` = `giro_dia × max(dias,0)`; `saldo_proj` = `qt − consumo_proj`;
- **valor em risco** = `max(0, saldo_proj) × CUSTOFIN`;
- classificação: `critico` ≤7d · `atencao` ≤15d · `planejar`; risco: `giro_zero` (sem giro) · `alto`/`medio` (saldo sobra) · `baixo`.

### 4.11 Plano de reposição no tempo (DRP — `plano_reposicao`)
Grade **semanal** (12 semanas) por produto:
- demanda/semana = `giro_dia × 7` (no modo **sazonal**, varia por mês: `nível_dia × fator[mês da semana] × 7`);
- saldo projetado = saldo anterior − demanda + recebimentos;
- **recebimento programado** = `QTTRANSITO + QTPENDENTE` na semana do lead (na prática ~0, ver Limitações);
- quando o saldo cruza o **estoque de segurança** → gera **pedido planejado** = `alvo − saldo` (arredondado a caixa);
- **liberação** (quando o pedido precisa SAIR) = semana do recebimento − lead (em semanas). "Sair agora" se ≤ 0.

### 4.12 Agregações
- **Fornecedores** (`fornecedores`): por fornecedor soma valor/giro/venda/lucro + cobertura média. `índice = %giro ÷ %estoque`. Classes: `critico_sem_giro` (giro≤0) · `ruptura` (cobertura < lead — gira mas quase sem estoque) · `alta_performance` (índice ≥1,2) · `equilibrado` (≥0,8) · `estoque_alto` (resto).
- **Compradores** (`por_comprador`): estoque×venda×lucro×margem, nº ruptura, valor parado, valor sugerido (exclui compra suspensa), e `giro_estoque` = venda÷estoque (giros do capital no período).
- **Cockpit** (`cockpit`): KPIs globais, faixas de cobertura, ABC, matriz, blocos de parado/ruptura/abastecimento.

---

## 5. Endpoints (app.py)

| Rota | Devolve |
|---|---|
| `GET /api/filtros` | filiais, deptos, fornecedores, **compradores (só de revenda)**, defaults |
| `GET /api/snapshot` | produtos enriquecidos + cockpit + fornecedores + compradores |
| `GET /api/validade` | lotes FEFO + resumo de risco |
| `GET /api/produto/<cod>` | produto 360° + lotes + **plano DRP** |
| `GET /api/plano_reposicao` | itens com liberações (alimenta a aba Plano) |
| `GET /api/export/<view>.csv|.xlsx` | export da view |
| `GET/POST/PUT/DELETE /api/orcamento, /api/pedidos, /api/planos` | persistência (Postgres) |

**Compradores (revenda):** a lista vem só dos `CODCOMPRADOR` ligados a fornecedores que têm produto revenda (deriva de `prod_map` → `CODFORNEC` → `CODCOMPRADOR`). Remove "PAGAR"/buscadores sem revenda sem nome chumbado.

---

## 6. Telas (frontend)

`⚡ Minha Fila` · `Cockpit` · `Ruptura` · `Reposição` · `Plano reposição` · `Validade` · `Parado` · `Orçamento` · `Compras × Vendas` · `ABC-XYZ` · `Fornecedores` · `Produtos` · **Produto 360°** (drawer ao clicar).

### Filtros (barra do topo) — valem para tudo
Comprador, Filiais (padrão CDs 3 e 5), Estoque (endereçado/gerencial), Curva, XYZ, **Fornecedor (campo digitável com autocomplete)**, Depto, Buscar produto, Venda (período da margem), ⚙ Parâmetros, ✕ Limpar. As escolhas são lembradas via `localStorage`.

### Detalhes de UX recentes
- **Cabeçalho de tabela congelado:** `.tbl-wrap` é caixa de rolagem (`max-height` reservando o topo) e `thead` é `sticky top:0`; vale para todas as tabelas.
- **Ordenação por clique** no cabeçalho (asc/desc) em todas as tabelas, incluindo a Validade.
- **Aba Parado:** coluna **Última venda** (DTULTSAIDA) + **filtro rápido** client-side "sem venda ≥ X dias" (instantâneo, sem reload) — distinto do parâmetro global de classificação.
- **Plano reposição:** aplica **todos** os filtros (fornecedor/comprador/curva/XYZ/depto/busca) via `filtered()`.
- **Sugestão em caixas:** "X cx · Y un" na Reposição, Plano e 360° quando `QTUNITCX>1`.
- **Forecast no 360°:** mostra forecast/sazonal × média 3m e o fator do mês; sparkline de 12 meses.

---

## 7. Persistência (Postgres — `store.py`)
Tabelas `estoque_*` (prefixo, isoladas) no Postgres próprio:
- `estoque_orcamento` — meta de compras por mês×comprador.
- `estoque_pedidos` — pedidos lançados (abatem a meta).
- `estoque_planos_acao` — planos de ação (validade/parado): responsável, ação, prazo, status.

Degrada com elegância: se o Postgres cair, o resto do painel continua (só orçamento/planos ficam indisponíveis).

---

## 8. Limitações conhecidas (transparência)
- **Sem visibilidade de inbound:** `PCEST[QTTRANSITO]` vem zerado no BI e `QTPENDENTE` é raro → o plano DRP trata todo reabastecimento como planejado (avisado na tela).
- **MOQ/múltiplo de compra oficial não usado:** `MULTIPLOCOMPRAS`/`QTMINSUGCOMPRA`/`VLMINPEDCOMPRA` estão ~0% preenchidos no WINTHOR. Usa-se `QTUNITCX` (100% preenchido) para a caixa fechada.
- **Forecast sazonal** exige ≥12 meses; o giro de capa usa o fator do mês corrente, e a variação fina mês a mês aparece na **grade DRP**.
- **COMPRADOR CONSUMO** ainda aparece no filtro porque há 32 produtos revenda amarrados a ele no cadastro (correção é no WINTHOR — reatribuir os 11 fornecedores).
- O painel é **ao vivo**; pequenas diferenças para a planilha antiga são esperadas (a planilha é uma foto manual).

---

## 9. Deploy
- **Local:** `python -X utf8 app.py` → http://localhost:5001 (lê `.env` próprio).
- **Produção:** push em `main` dispara o CI **`build-push`** (GitHub Actions) → publica `ghcr.io/jogasolucoesempresarias-debug/multpel-estoque:latest`. Em seguida, no servidor (stack `multpel-estoque`, serviço `estoque-app`):
  ```bash
  docker service update --force multpel-estoque_estoque-app
  ```
  (ou Portainer → Update / Pull latest image). Acesso em **estoque.jogasolucoes.com.br** (Traefik/TLS).

---

## 10. Utilitários
- `test_novas_tabelas.py` — lista tabelas/colunas do dataset (via `INFO.VIEW.*`) e amostra linhas. Útil para validar campos no BI antes de usá-los (foi assim que descobrimos QTTRANSITO=0 e os campos de MOQ vazios).
