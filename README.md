# Multpel · Painel de Estoque

App **standalone** de gestão de estoque (compras × vendas) sobre o Power BI da Multpel.
Repositório **separado do Multpel HTML** de propósito — só vai pro cliente após negociação.

## Rodar local (dev)
```bash
pip install -r requirements.txt
cp .env.example .env   # preencher credenciais
python -X utf8 app.py  # http://localhost:5001
```
Sem `ESTOQUE_SENHA` no `.env`, sobe sem login (só dev).

## Stack
Flask + Waitress · HTML/JS + Chart.js · Power BI executeQueries (datasets **Estoque** + **RCA**) · Postgres (orçamento/planos).

## Deploy (servidor JOGA — Portainer/Swarm/Traefik)
1. Push na `main` → GitHub Action publica `ghcr.io/jogasolucoesempresarias-debug/multpel-estoque:latest`.
2. No Portainer: **Add stack** com `docker-compose.prod.yml`, preencher as variáveis:
   - `SECRET_KEY`, `ESTOQUE_SENHA` (senha do sócio), `DB_PASSWORD`
   - `POWERBI_TENANT_ID/CLIENT_ID/CLIENT_SECRET/GROUP_ID` (mesmas do Multpel)
3. DNS Cloudflare: `estoque.jogasolucoes.com.br` → servidor (DNS only).
4. A stack sobe app + Postgres próprio (`estoque_db`); tabelas `estoque_*` criadas no 1º boot.

## Isolamento
Postgres dedicado da stack (não toca no `multpel_db`). Quando o estoque for adquirido, basta apontar `DB_*` para o `postgres_postgres` compartilhado e unificar o login.

Documentação de metodologia/cobertura em `docs/`.
