# Multpel · Painel de Estoque

App **standalone** de gestão de estoque (compras × vendas) sobre o Power BI da Multpel.
Foco no **comprador**: ruptura, reposição, validade/FEFO, cobertura/giro, orçamento, planos de ação, e cruzamento **compras × vendas** por produto / fornecedor / comprador.

> ⚠️ **Repositório SEPARADO do Multpel HTML de propósito.** O estoque é um produto à parte e **só vai pro cliente após negociação de pagamento**. Por isso vive em outro git e sobe em **outro servidor (JOGA)** — nunca dentro do repo/stack do Multpel que será entregue à Multpel.

---

## Stack
Flask + Waitress · HTML/JS + Chart.js · Power BI executeQueries (datasets **Estoque** + **RCA**) · Postgres (orçamento/planos de ação).

## Dados / metodologia (resumo)
- **Giro** = média dos 3 últimos meses (`QTVENDMES1..3`/3) — bate exato com a planilha do diretor.
- **Estoque (QTDISP)** = endereçado WMS (`PCESTENDERECO`, RUA≠99, filiais 3+5); toggle p/ gerencial (`QTESTGER`).
- **Custo** = `CUSTOFIN`. **Comprador** = `PCFORNEC.CODCOMPRADOR → PCEMPR.NOME` (dataset RCA).
- **Venda/lucro/margem** = `FATURAMENTO_VENDAS` (RCA), líquida (− devoluções), por período.
- **Sugestão de compra** desconta **trânsito + pendente** (não duplica o que já foi pedido).
- Detalhes em `docs/estoque.md` e `docs/analise_cobertura.md`.

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
docker stack services multpel-estoque                          # status
docker service logs -f --tail 200 multpel-estoque_estoque-app  # logs do app
docker service logs --tail 50 multpel-estoque_estoque-postgres # logs do banco
curl -I https://estoque.jogasolucoes.com.br/health       # liveness
```

---

## Cuidados (IMPORTANTE)
- 🚫 **Nunca** mover este código para dentro do repo do Multpel HTML que será entregue à Multpel. É outro git, outro servidor — separação física, não confiar só em `.gitignore`.
- 🔒 **Sempre** com `ESTOQUE_SENHA` em produção (URL pública expõe estoque + vendas).
- 🔑 Segredos (`.env`, secrets do Portainer) **não** vão pro git (`.gitignore`/`.dockerignore` já cobrem `.env`, `*.xlsx`, etc.).
- 🗄️ Postgres **dedicado** da stack — não toca no `multpel_db`. Quando o estoque for adquirido: apontar `DB_*` para o `postgres_postgres` compartilhado e **unificar o login** com o Multpel (mesmo usuário/senha).
- 🧮 A **sugestão de compra** considera trânsito/pendente → alguns números de "quanto comprar" ficam menores que a planilha do diretor (é melhoria, não divergência).
