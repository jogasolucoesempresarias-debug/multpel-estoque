# Análise de cobertura — App de Estoque × Planilha do Diretor

> Comparação profunda entre `CONTROLE ESTOQUE.xlsx` (18 abas) e a aplicação `_estoque_app`.
> Base da planilha: dados de **15/06/2026**, filiais **3 e 5** (CDs), estoque **endereçado (WMS)**.

## 1. As 18 abas, em 3 grupos

**A. Painéis / análises (leitura — é o que o app faz):**
RELATORIO GERENCIAL (gestão à vista, 4 pilares: Orçamento · Rupturas · Cobertura · Validade), PERFORMANCE PRODUTOS, DESEMPENHO FORNECEDOR, CONTROLE ABASTECIMENTO, CONTROLE ESTOQUE PARADO, ANALISE RISCO VENCIMENTO, VALOR ESTOQUE PRODUTO, GRAFICO COBERTURA ESTOQUE, GRAFICO RISCO VENCIMENTO, RUPTURA_COMPRADOR, ORCAMENTO_COMPRAS.

**B. Workflow / estado editável (o app NÃO tem — é read-only):**
PLANO_ACAO_VENCIMENTO, PLANO_ACAO_COBERTURA_ESTOQUE, e as colunas RESPONSAVEL/ACAO/PRAZO/STATUS_ACAO dentro de cada aba.

**C. Navegação / base de dados:**
INDICE, MANUAL DA PLANILHA, DADOS (14 col, fonte crua por filial+lote), BASE PRODUTOS (23 col, agregado por produto), BASE LOOKER STUDIO (50 col, base totalmente calculada).

## 2. Matriz de cobertura

| Análise do diretor | App `_estoque_app` | Status |
|---|---|---|
| Performance / giro (ABC por giro, classificação) | view Produtos + curva_giro + ABC | ✅ cobre |
| Cobertura por faixa + gráfico pizza | Cockpit (donut faixas) | ✅ cobre |
| Valor de estoque por produto (ABC valor, Pareto) | curva_abc + valor + Cockpit | ✅ cobre |
| Controle de abastecimento (ROP, sugestão compra) | view Reposição (ROP, est. alvo, sugestão) | ✅ cobre |
| Estoque parado (dias sem venda, classes) | view Parado | ✅ cobre |
| Risco de vencimento / FEFO | view Validade | ✅ cobre |
| Desempenho por fornecedor (índice giro×estoque) | view Fornecedores | ✅ cobre |
| **Análise XYZ + matriz ABC-XYZ** | view ABC-XYZ | 🟢 **só o app tem** |
| **Drill produto 360° / filtros reativos / alertas clicáveis** | sim | 🟢 **só o app tem** |
| **Filtro por COMPRADOR** (transversal em tudo) | — | 🔴 falta |
| **Ruptura por comprador** (0-15 / 16-30 dias) | parcial (Reposição urgente) | 🟡 parcial |
| **Orçamento de compras** (pedidos + % consumido) | — | 🔴 falta |
| **Planos de ação** (responsável/ação/prazo/status) | — | 🔴 falta (precisa persistência) |
| Gráfico risco vencimento por faixa | tabela validade (sem gráfico) | 🟡 parcial |

## 3. Dois achados críticos de metodologia

Por produto os números batem (ex.: 68961 LÃ DE AÇO — disponível ≈ 291k = QTDISP do diretor). Mas os **totais** divergem por duas escolhas de base:

**(a) Estoque = `ESTQ_ENDEREÇO` (endereçado WMS), não `PCEST.QTEST` (gerencial).**
- Diretor: estoque endereçado, filiais 3+5, ~1.371 produtos, **R$ 2,98M**.
- App hoje: `PCEST.QTEST`, todas filiais, ~4.849 produtos, **R$ 9,9M**.
- Confirmado: PCESTENDERECO tem 2.752 produtos endereçados (⊃ os 1.371 de 3+5). Conceitos diferentes — "estoque pickável no CD" vs "estoque gerencial total". Ambos válidos.
- Diretor usa **CUSTOFIN** (não CUSTOREAL) e dedup por `MAX(QTDISP)` por filial.

**(b) Giro = `GIROMESUNID` → RESOLVIDO: é `SUM(QTGIRODIA) das filiais 3+5 × 30`.**
- Match EXATO (unidade) em 4 produtos testados: 68961=73.450 · 42253=82.198 · 42248=32.459 · 42325=31.233.
- Eu tinha olhado a coluna errada (QTVENDMES). A coluna certa é **`PCEST[QTGIRODIA]`** (giro/dia que o Winthor mantém) × 30, no escopo de filiais 3+5.
- ⚠️ Conclusão revisada: o giro **É reproduzível** dos dados crus. Não dependemos da base do TI pra isso.

## 4. Colunas do TI que NÃO temos (resposta direta)

Depois de investigar, **a lista é mínima** — quase tudo que a base DADOS/BASE LOOKER tem nós temos ou calculamos:

| Campo do TI | Temos? | Fonte nos dados crus |
|---|---|---|
| ESTQ_GERENCIAL | ✅ | `PCEST[QTEST]` |
| ESTQ_ENDEREÇO (QTDISP) | ✅ | `PCESTENDERECO[QT]` (filiais 3+5) |
| GIROMESUNID | ✅ | **`PCEST[QTGIRODIA] × 30` (filiais 3+5)** — match exato |
| CUSTOFIN | ✅ | `PCEST[CUSTOFIN]` |
| DTVALIDADE / DTULTSAIDA / DTULTENT | ✅ | PCESTENDERECO / PCEST |
| CODFORNEC / FORNECEDOR | ✅ | PCPRODUT → PCFORNEC |
| CODCOMPRADOR | ✅ (7 códigos) | `PCPRODFILIAL[CODCOMPRADOR]` |
| **NOME do comprador** | ❌ | precisa de **PCEMPR** (não publicado) — mapear 7 códigos→nome ou pedir ao TI |

As **50 colunas** da BASE LOOKER STUDIO são, na maioria, **derivadas** (cobertura, curvas, classificações, flags) — ou seja, **cálculos** que já fazemos no `core.py`, não dados-fonte que falte.

**Conclusão:** dá pra bater **número-a-número** com o diretor usando só os dados crus (basta calibrar: giro=`QTGIRODIA×30`, estoque=`ESTQ_ENDEREÇO` filiais 3+5, custo=`CUSTOFIN`). O único furo real é o **nome do comprador** (PCEMPR). Não é obrigatório depender da base do TI.

## 5. Lacunas a implementar (priorizado)

1. **Filtro por comprador** — dimensão transversal em toda a planilha. Temos CODCOMPRADOR (7) no PCPRODFILIAL; nome vem do PCEMPR (não publicado) → mapear 7 códigos→nome ou pedir ao TI.
2. **Orçamento de compras** — entrada manual de pedidos + acompanhamento vs planejado (% consumido, saldo). Requer **persistência** (estado editável).
3. **Ruptura por comprador** — view dedicada com faixas 0-15 / 16-30 dias e estoque zero/negativo.
4. **Planos de ação** — responsável/ação/prazo/status por item de validade e cobertura. Requer **persistência**.
5. **Gráfico de risco de vencimento por faixa** (pizza) — temos a tabela, falta o gráfico.
6. **Toggle estoque gerencial × endereçado** — oferecer as duas lentes.

## 6. Onde o app já é superior

- **Análise XYZ + matriz ABC-XYZ 9 quadrantes** — a planilha não tem variabilidade de demanda.
- **UX dinâmica**: filtros instantâneos, alertas clicáveis, drill produto 360°, sem 18 abas.
- **Dado vivo** direto do Power BI (a planilha depende de colar/atualizar a base).

## 7. Implicação de arquitetura

As lacunas 2 e 4 (orçamento e planos de ação) são **estado editável** → exigem persistência. Hoje o app é stateless/read-only. Opções: SQLite local no `_estoque_app` (mantém isolado e fácil) agora; Postgres quando integrar ao Multpel HTML.

## Próximos passos sugeridos

1. **Confirmar com o TI** se a `BASE LOOKER STUDIO`/`DADOS` pode ser publicada como dataset (define Opção A vs B).
2. Adicionar **filtro de comprador** (rápido, alto valor).
3. Adicionar **toggle estoque endereçado** + custo CUSTOFIN para casar com o diretor.
4. Planejar **orçamento de compras** e **planos de ação** com SQLite local.
5. Adicionar **gráfico de validade por faixa**.
