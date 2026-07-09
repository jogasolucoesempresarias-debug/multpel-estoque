# Multpel · Painel de Estoque

App **standalone** de gestão de estoque (compras × vendas) sobre o Power BI da Multpel.
Foco no **comprador**: ruptura, reposição, validade/FEFO, cobertura/giro, orçamento, planos de ação, e cruzamento **compras × vendas** por produto / fornecedor / comprador.

> ⚠️ **Repositório SEPARADO do Multpel HTML de propósito.** O estoque é um produto à parte e **só vai pro cliente após negociação de pagamento**. Por isso vive em outro git e sobe em **outro servidor (JOGA)** — nunca dentro do repo/stack do Multpel que será entregue à Multpel.

---

## Stack
Flask + Waitress · HTML/JS + Chart.js · Power BI executeQueries (datasets **Estoque** + **RCA**) · Postgres (orçamento/planos de ação) · ReportLab (export PDF dos relatórios gerenciais).
> Código dividido em módulos: `app.py` (rotas), `core.py` (regra de negócio), `queries.py` (DAX), `pbi.py` (client Power BI), `store.py` (Postgres).

## Dados / metodologia (resumo) — v3
Consome **4 tabelas novas do Winthor** no dataset Estoque: **PCPEDIDO/PCITEM** (pedido de compra real),
**PCEMBALAGEM** (caixa/cubagem) e **PCEMPR** (comprador no próprio dataset).
- **Estoque (Disponível / QTDISP)** = **gerencial** líquido: `QTESTGER − avaria (QTBLOQUEADA) − reserva (QTRESERV)`, filiais 3+5 (decisão do diretor 07/2026 — itens em avaria/reservados não estão disponíveis p/ venda; substitui o "QTESTGER cru" anterior). **Endereçado** (`PCESTENDERECO`, RUA≠99) **só na validade/FEFO** (estoque por lote).
- **Giro** = média dos 3 últimos meses (`QTVENDMES1..3`/3); toggle p/ forecast (RCA). **Custo** = `CUSTOFIN`.
- **Comprador** = `PCFORNEC.CODCOMPRADOR → PCEMPR.NOME` (no próprio dataset Estoque; RCA só p/ venda).
- **Sugestão de compra** desconta o **pedido de compra REAL em aberto** (PCPEDIDO/PCITEM, qtd pedida − entregue, últimos 180 dias) e sai **em caixas** (`QTUNIT`/PCEMBALAGEM) — quando o item **não tem fator de caixa** cadastrado no Winthor, sai em **unidades** ("un"); normaliza sozinho quando o TI cadastrar o QTUNIT (itens pendentes em `itens_sem_fator_caixa.csv`). Prioriza sobre o estoque projetado (disponível + já pedido).
- **Orçamento** = meta `65% da venda líquida 30d` por comprador (RCA) × realizado lido **direto do Winthor**; acompanhamento de pedidos por previsão de entrega (híbrido `DTPREVENT`/emissão+lead); logística por cubagem.
- **Venda/lucro/margem** = `FATURAMENTO_VENDAS` (RCA), líquida (− devoluções), por período.
- **Unidades de negócio**: filtro por unidade (escopa a venda por filial, com código da filial no seletor).
- **Navegação** em 2 níveis: Visão · Comprar · Pedidos · Estoque · Análise. A aba **Visão** é dividida em **Cockpit** + **Painel gerencial** (5 pilares); o painel mostra "Venda perdida/mês" (não "Valor em ruptura"), coluna **Avaria** e colunas em **caixa** (Disp. cx, Giro cx, Sugerido cx). Filtros são por aba.
- Metodologia v3 completa (fórmulas decodificadas da planilha) em **`docs/planilha_v3.md`**.

---

## Rodar local (dev)
```bash
pip install -r requirements.txt
cp .env.example .env        # preencher credenciais
python -X utf8 app.py       # http://localhost:5001
```
- Sem `ESTOQUE_SENHA` no `.env` → sobe **sem login** (só dev local).
- Se o auto-reload do Flask entrar em loop observando o `.venv`, suba sem reloader:
  `python -c "import app; app.app.run(port=5001, use_reloader=False)"`

---

## Deploy (servidor JOGA — Portainer / Swarm / Traefik)

### Fluxo
```
git push main  →  GitHub Action (deploy.yml)  →  ghcr.io/jogasolucoesempresarias-debug/multpel-estoque:latest  →  Portainer (stack) no JOGA
```

### Primeira vez
1. **GitHub**: repo `jogasolucoesempresarias-debug/multpel-estoque` (privado). Push na `main` dispara a Action que builda e publica a imagem no GHCR.
2. **DNS Cloudflare**: `estoque.jogasolucoes.com.br` → IP do servidor (DNS only).
3. **Portainer → Add stack** com `docker-compose.prod.yml`, preenchendo as variáveis:
   - `SECRET_KEY` (hex aleatório — assina o cookie de sessão; fixar, não trocar a cada deploy)
   - `ESTOQUE_SENHA` (senha única que você passa pro usuário/diretor)
   - `DB_PASSWORD` (senha do Postgres da stack — **igual** em `estoque-app` e `estoque-postgres`)
   - `POWERBI_TENANT_ID/CLIENT_ID/CLIENT_SECRET/GROUP_ID` (mesmas credenciais do Multpel)
4. A stack sobe **app + Postgres próprio** (`estoque_db`); as tabelas `estoque_*` são criadas no 1º boot.
5. Como a imagem GHCR é **privada**, o Portainer precisa da credencial do GHCR (a mesma org/registry do `multpelhtlm` — já configurada).

### 🚀 Como subir uma atualização (ROTINA — guardar este passo a passo)

**1) No PC (pasta `MultpelEstoque`)** — commitar e enviar:
```bash
git add -A
git commit -m "descreva a mudança"
git push origin main
```

**2) Esperar a GitHub Action** ficar verde (aba **Actions** do repo, ~2-3 min). Ela rebuilda e republica a imagem `:latest` no GHCR.

**3) No servidor (JOGA)** — forçar o redeploy puxando a imagem nova. **Este é o comando de sempre** (`--with-registry-auth` é obrigatório porque a imagem é privada):
```bash
docker service update \
  --image ghcr.io/jogasolucoesempresarias-debug/multpel-estoque:latest \
  --with-registry-auth --force multpel-estoque_estoque-app
```
> Alternativa pelo Portainer: **Stacks → multpel-estoque → Pull and redeploy** (marcar "re-pull image").

**4) Conferir:** `curl -I https://estoque.jogasolucoes.com.br/health` (espera `200`). Se for mudança de tela, peça pro usuário dar **Ctrl+Shift+R** (limpa o cache do CSS/JS).

### Diagnóstico
```bash
docker stack services multpel-estoque                          # status (postgres deve ser 1/1)
docker service logs -f --tail 200 multpel-estoque_estoque-app  # logs do app
docker service logs --tail 50 multpel-estoque_estoque-postgres # logs do banco
curl -I https://estoque.jogasolucoes.com.br/health       # liveness
```

### Troubleshooting: "Orçamento indisponível (Postgres off): HTTP 503"
O app inteiro funciona, **só** Orçamento/Planos (que usam Postgres) caem. O app loga o motivo exato:
```bash
docker service logs --tail 60 multpel-estoque_estoque-app | grep -i store
```
| Mensagem | Causa | Correção |
|---|---|---|
| `could not translate host name "estoque-postgres"` | app e banco em redes diferentes / serviço do banco não subiu | os 2 serviços precisam compartilhar a rede `estoque-internal` |
| `password authentication failed` | `DB_PASSWORD` diferente entre `estoque-app` e `estoque-postgres` | mesma senha nos dois (a var `DB_PASSWORD` da stack alimenta ambos) |
| `Connection refused` | postgres subindo/caiu | conferir réplica 1/1 e logs do `estoque-postgres` |
| `estoque-postgres` ausente / `0/1` | stack subiu sem o serviço do banco | re-subir a stack com o `docker-compose.prod.yml` completo |

Não precisa reiniciar o app depois de arrumar o banco: ele reconecta sozinho na próxima chamada (`store.ensure()`).

---

## Cuidados (IMPORTANTE)
- 🚫 **Nunca** mover este código para dentro do repo do Multpel HTML que será entregue à Multpel. É outro git, outro servidor — separação física, não confiar só em `.gitignore`.
- 🔒 **Sempre** com `ESTOQUE_SENHA` em produção (URL pública expõe estoque + vendas).
- 🔑 Segredos (`.env`, secrets do Portainer) **não** vão pro git (`.gitignore`/`.dockerignore` já cobrem `.env`, `*.xlsx`, etc.).
- 🗄️ Postgres **dedicado** da stack — não toca no `multpel_db`. Quando o estoque for adquirido: apontar `DB_*` para o `postgres_postgres` compartilhado e **unificar o login** com o Multpel (mesmo usuário/senha).
- 🧮 A **sugestão de compra** desconta o **pedido de compra REAL em aberto** (Winthor) → alguns números de "quanto comprar" ficam menores (é melhoria, não divergência).
- 🕐 O container roda em **`TZ=America/Sao_Paulo`** (fixo no `docker-compose.prod.yml`) — importante p/ `date.today()`, a janela de 180 dias do pedido, o "mês" do orçamento e as previsões de entrega.
