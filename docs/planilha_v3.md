# Metodologia v3 — integração das 4 tabelas Winthor (pedido real, caixa, cubagem, comprador)

Referência da reformulação que o diretor fez na planilha `CONTROLE ESTOQUE (v3)` e de como a
app replica isso. Tudo aqui foi **decodificado das fórmulas da planilha** e **validado ao vivo**
contra o dataset Power BI "Estoque".

## Tabelas novas consumidas (dataset Estoque)
| Tabela | Usada para |
|---|---|
| **PCPEDIDO** (cabeçalho do pedido de compra) | já-pedido, orçamento realizado, acompanhamento, logística |
| **PCITEM** (item do pedido) | quantidade pedida × entregue por produto |
| **PCEMBALAGEM** | caixa (`QTUNIT`) — cubagem do PCEMBALAGEM vem zerada, não usar |
| **PCEMPR** | nome do comprador direto no dataset Estoque (antes cruzava no RCA) |
| **PCPRODUT.VOLUME / *M3** | cubagem (volume unitário m³) — fonte real da logística |

## Decisões oficiais (diretor)
1. **Pedido = só real (Winthor).** Realizado e já-pedido vêm de PCPEDIDO/PCITEM. Pedido manual da
   nossa plataforma fica à parte (pendente de envio) e **não** soma no realizado.
2. **Estoque = GERENCIAL para tudo; ENDEREÇADO só na validade/FEFO.**
3. **Pedido ATIVO** = `QTPEDIDA>0` e `DTEMISSAO ≥ hoje−180`.
4. **Caixa** = `QTUNIT` (PCEMBALAGEM), fallback `QTUNITCX` (PCPRODUT).
5. **Cobertura-alvo = 30 dias** (N2 da planilha), lead 10, segurança 25.
6. **Meta orçamento = 65% da venda líquida 30d** por comprador; **capacidade veículo = 60 m³**.

## Fórmulas-chave (origem: `CONTROLE ABASTECIMENTO!B6`)
- `qtdisp` (gerencial) = `Σ_filial MAX(ESTQ_GERENCIAL)` — **cru**, sem subtrair reserva/bloqueio.
- `qtd_ja_pedida` (aberto) = `Σ max(0, QTPEDIDA − QTENTREGUE)` dos pedidos ativos (180d).
  > A planilha não desconta `QTENTREGUE` explicitamente, mas o resultado dela equivale ao aberto
  > (o join limitado do DADOS mascarava recebidos). Descontar é correto: o gerencial já reflete o
  > recebido, então só o **aberto** entra na projeção (senão conta em dobro).
- `estoque_projetado` = `qtdisp + qtd_ja_pedida`; `cobertura_proj` = `projetado / (giro/30)`.
- `est_alvo` = `(giro/30) × 30`.
- `sugestao_liquida` = `max(0, est_alvo − estoque_projetado)`.
- `sugestao_cx` = `ROUNDUP(sugestao_liquida / qtunit)`; `valor_sugerido_liq` = `cx × qtunit × custo`.
- `prioridade`: URGENTE `proj ≤ giro/30×lead` · ALTA `≤ giro/30×(lead+seg)` · ATENÇÃO `proj<alvo` · OK.
- `status_exec`: ruptura_sem_pedido / ruptura_pedido_parcial / ruptura_pedido_cobre /
  compra_urgente / compra_alta / compra_complementar / programar_compra / pedido_cobre / estoque_ok.

### Orçamento (`ORCAMENTO_COMPRAS`)
- `meta` = `Σ(venda_liq_30d por comprador) × 65%` (override manual opcional via Postgres).
- `comprado` (realizado) = `Σ VLTOTAL` dos pedidos do mês.
- `comprometido aberto` = `Σ max(0, VLTOTAL − VLENTREGUE)` dos pedidos do mês.
  > `DTENTRADAESTOQUE` vem **vazio** no dataset → usar `VLENTREGUE` p/ saber recebido (tolerância
  > de 0,5%/R$1 p/ resíduo de centavos).
- Acompanhamento por **previsão de entrega** (`DTPREVENT`, preenchido p/ todos): atrasado /
  chega ≤7d / no prazo.

### Logística (`LOGISTICA_PEDIDOS`) — estimativa
- `cubagem_pedido` = `Σ (qtd_aberta × volume_unitário)` (o `qtunit` se cancela).
- `volume_unitário` = `PCPRODUT.VOLUME` (m³), fallback `ALTURAM3×LARGURAM3×COMPRIMENTOM3 / 1e6`.
- `ocupação` = `cubagem ÷ 60 m³`. Baixa ocupação (≤10%) = candidato a consolidação.

## Paridade validada (comprador TODOS, 26/06/2026)
| Métrica | Match vs planilha |
|---|---|
| Giro | 100% |
| QTDISP gerencial | 98,1% |
| Caixa (QTUNIT) | 100% |
| Já-pedido (aberto) | 96,5% |
| Estoque projetado | 94,6% |
| Cobertura projetada | 95,5% |
| Sugestão em caixas | 91,3% |
| Comprometido aberto (orçamento) | 2,1M ≈ 2,23M da planilha |

Resíduos vêm de pedidos abertos que o BI enxerga e o join da planilha perdia → nossa sugestão
fica mais completa/correta (não é divergência, é melhoria — mesma lógica do trânsito já aceita).

## Mapa de arquivos
- `queries.py`: `q_pedido_cab`, `q_pedido_itens`, `q_embalagem`, `q_compradores_estoque`; cadastro
  ganhou VOLUME/dims.
- `core.py`: base gerencial cru, `montar_ja_pedida`, sugestão/projeção/caixas/status_exec em
  `construir_produtos`; `orcamento_winthor`, `logistica_pedidos`, `vol_unitario`.
- `app.py`: `_pedidos_data`, `_embalagem_map`, `_venda_comprador_30d`; `/api/orcamento` (Winthor),
  `/api/logistica`; comprador via PCEMPR-Estoque.
- `store.py`: colunas `origem/sincronizado_winthor/numped_winthor`; `meta_get`, `pedidos_pendentes`.
- `index.html` / `static/estoque.js` / `estoque.css`: navegação 2 níveis (Visão/Comprar/Pedidos/
  Estoque/Análise), abas **Estoque zerado** e **Logística**, Abastecimento e Orçamento reformulados.
