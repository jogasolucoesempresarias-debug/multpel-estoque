"""
Persistência (Postgres) do painel de estoque — estado editável que o Power BI não guarda:
orçamento de compras, pedidos lançados e planos de ação (validade/cobertura).

Reutiliza o Postgres do projeto principal (mesmas vars DB_* do .env). Tabelas com
prefixo `estoque_` para não colidir com o app Multpel.
"""

import os
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")


def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "multpel_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


_DDL = """
CREATE TABLE IF NOT EXISTS estoque_orcamento (
    mes         TEXT NOT NULL,
    comprador   TEXT NOT NULL DEFAULT 'TODOS',
    meta_valor  NUMERIC NOT NULL DEFAULT 0,
    PRIMARY KEY (mes, comprador)
);
CREATE TABLE IF NOT EXISTS estoque_pedidos (
    id            SERIAL PRIMARY KEY,
    data_pedido   DATE,
    mes           TEXT,
    comprador     TEXT,
    codfornec     INTEGER,
    fornecedor    TEXT,
    n_pedido      TEXT,
    valor         NUMERIC NOT NULL DEFAULT 0,
    prazo_dias    INTEGER,
    dt_vencimento DATE,
    status        TEXT DEFAULT 'ABERTO',
    forma_pgto    TEXT,
    obs           TEXT,
    criado_em     TIMESTAMP DEFAULT now()
);
CREATE TABLE IF NOT EXISTS estoque_pedido_itens (
    id          SERIAL PRIMARY KEY,
    pedido_id   INTEGER NOT NULL REFERENCES estoque_pedidos(id) ON DELETE CASCADE,
    codprod     INTEGER,
    descricao   TEXT,
    qtdisp      NUMERIC,
    cobertura   NUMERIC,
    giro_mes    NUMERIC,
    qtunitcx    NUMERIC,
    qtd         NUMERIC,
    custo_unit  NUMERIC,
    valor       NUMERIC
);
CREATE TABLE IF NOT EXISTS estoque_planos_acao (
    chave         TEXT PRIMARY KEY,
    tipo          TEXT NOT NULL,
    codprod       INTEGER,
    dtvalidade    DATE,
    descricao     TEXT,
    responsavel   TEXT,
    acao          TEXT,
    prazo         DATE,
    status        TEXT DEFAULT 'PENDENTE',
    obs           TEXT,
    criado_em     TIMESTAMP DEFAULT now(),
    atualizado_em TIMESTAMP DEFAULT now()
);
-- migração: pedido manual vira "ordem gerada na nossa plataforma", pendente de envio ao
-- Winthor. Quando sincronizado, sai do orçamento (o realizado passa a vir do Winthor real).
ALTER TABLE estoque_pedidos ADD COLUMN IF NOT EXISTS origem TEXT DEFAULT 'NOSSO_SISTEMA';
ALTER TABLE estoque_pedidos ADD COLUMN IF NOT EXISTS sincronizado_winthor BOOLEAN DEFAULT false;
ALTER TABLE estoque_pedidos ADD COLUMN IF NOT EXISTS numped_winthor TEXT;
"""

_disponivel = None  # cache do teste de conexão (True/False)


def init():
    """Cria as tabelas (idempotente). Marca se o Postgres está acessível."""
    global _disponivel
    try:
        conn = get_db()
        with conn, conn.cursor() as cur:
            cur.execute(_DDL)
        conn.close()
        _disponivel = True
    except Exception as e:
        _disponivel = False
        print(f"[store] Postgres indisponível ({e}). Orçamento/planos ficam desabilitados.")
    return _disponivel


def disponivel():
    return bool(_disponivel)


def ensure():
    """Tenta (re)conectar se ainda não está disponível — robusto a Postgres que sobe depois."""
    return True if _disponivel else init()


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


# ───────────────────────── orçamento + pedidos ─────────────────────────
def orcamento_resumo(mes, comprador="TODOS"):
    """Meta do mês + total comprado (soma de pedidos) + saldo + % consumido."""
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT meta_valor FROM estoque_orcamento WHERE mes=%s AND comprador=%s",
                    (mes, comprador))
        row = cur.fetchone()
        meta = float(row["meta_valor"]) if row else 0.0
        if comprador and comprador != "TODOS":
            cur.execute("SELECT COALESCE(SUM(valor),0) s, COUNT(*) n FROM estoque_pedidos WHERE mes=%s AND comprador=%s",
                        (mes, comprador))
        else:
            cur.execute("SELECT COALESCE(SUM(valor),0) s, COUNT(*) n FROM estoque_pedidos WHERE mes=%s", (mes,))
        agg = cur.fetchone()
    conn.close()
    comprado = float(agg["s"]); n = int(agg["n"])
    saldo = meta - comprado
    pct = (comprado / meta) if meta > 0 else None
    return {"mes": mes, "comprador": comprador, "meta": meta, "comprado": comprado,
            "saldo": saldo, "pct": pct, "n_pedidos": n}


def meta_get(mes, comprador="TODOS"):
    """Meta manual lançada (override do 65% automático), ou None se não houver."""
    try:
        conn = get_db()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT meta_valor FROM estoque_orcamento WHERE mes=%s AND comprador=%s",
                        (mes, comprador or "TODOS"))
            row = cur.fetchone()
        conn.close()
        return float(row[0]) if row and row[0] else None
    except Exception:
        return None


def pedidos_pendentes(mes, comprador=None):
    """Pedidos manuais (gerados na nossa plataforma) ainda NÃO sincronizados com o Winthor.
    Ficam à parte do realizado p/ não duplicar quando voltarem da base oficial."""
    try:
        conn = get_db()
        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            base = ("SELECT * FROM estoque_pedidos WHERE mes=%s "
                    "AND COALESCE(sincronizado_winthor,false)=false")
            if comprador and comprador != "TODOS":
                cur.execute(base + " AND comprador=%s ORDER BY id DESC", (mes, comprador))
            else:
                cur.execute(base + " ORDER BY id DESC", (mes,))
            out = _rows(cur)
        conn.close()
        return out
    except Exception:
        return []


def orcamento_set(mes, comprador, meta_valor):
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO estoque_orcamento (mes, comprador, meta_valor)
                       VALUES (%s,%s,%s)
                       ON CONFLICT (mes, comprador) DO UPDATE SET meta_valor=EXCLUDED.meta_valor""",
                    (mes, comprador or "TODOS", meta_valor))
    conn.close()


def pedidos_list(mes, comprador=None):
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        if comprador and comprador != "TODOS":
            cur.execute("SELECT * FROM estoque_pedidos WHERE mes=%s AND comprador=%s ORDER BY data_pedido DESC, id DESC",
                        (mes, comprador))
        else:
            cur.execute("SELECT * FROM estoque_pedidos WHERE mes=%s ORDER BY data_pedido DESC, id DESC", (mes,))
        out = _rows(cur)
    conn.close()
    return out


def pedido_get(pid):
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM estoque_pedidos WHERE id=%s", (pid,))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def pedido_itens(pid):
    """Itens (snapshot) de um pedido, em ordem de inclusão."""
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM estoque_pedido_itens WHERE pedido_id=%s ORDER BY id", (pid,))
        out = _rows(cur)
    conn.close()
    return out


def pedido_add(d):
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO estoque_pedidos
            (data_pedido, mes, comprador, codfornec, fornecedor, n_pedido, valor, prazo_dias, dt_vencimento, status, forma_pgto, obs)
            VALUES (%(data_pedido)s,%(mes)s,%(comprador)s,%(codfornec)s,%(fornecedor)s,%(n_pedido)s,%(valor)s,%(prazo_dias)s,%(dt_vencimento)s,%(status)s,%(forma_pgto)s,%(obs)s)
            RETURNING id""", _ped_defaults(d))
        new_id = cur.fetchone()[0]
        for it in (d.get("itens") or []):
            cur.execute("""INSERT INTO estoque_pedido_itens
                (pedido_id, codprod, descricao, qtdisp, cobertura, giro_mes, qtunitcx, qtd, custo_unit, valor)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (new_id, it.get("codprod"), it.get("descricao"), it.get("qtdisp"), it.get("cobertura"),
                 it.get("giro_mes"), it.get("qtunitcx"), it.get("qtd"), it.get("custo_unit"), it.get("valor")))
    conn.close()
    return new_id


def pedido_update(pid, d):
    campos = [k for k in ("data_pedido", "comprador", "codfornec", "fornecedor", "n_pedido",
                          "valor", "prazo_dias", "dt_vencimento", "status", "forma_pgto", "obs") if k in d]
    if not campos:
        return
    sets = ", ".join(f"{c}=%s" for c in campos)
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute(f"UPDATE estoque_pedidos SET {sets} WHERE id=%s", [d[c] for c in campos] + [pid])
    conn.close()


def pedido_delete(pid):
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM estoque_pedidos WHERE id=%s", (pid,))
    conn.close()


def _ped_defaults(d):
    return {
        "data_pedido": d.get("data_pedido"), "mes": d.get("mes"), "comprador": d.get("comprador"),
        "codfornec": d.get("codfornec"), "fornecedor": d.get("fornecedor"), "n_pedido": d.get("n_pedido"),
        "valor": d.get("valor") or 0, "prazo_dias": d.get("prazo_dias"),
        "dt_vencimento": d.get("dt_vencimento"), "status": d.get("status") or "ABERTO",
        "forma_pgto": d.get("forma_pgto"), "obs": d.get("obs"),
    }


# ───────────────────────── planos de ação ─────────────────────────
def planos_map(tipo=None):
    """Retorna {chave: plano} para merge no payload das views."""
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        if tipo:
            cur.execute("SELECT * FROM estoque_planos_acao WHERE tipo=%s", (tipo,))
        else:
            cur.execute("SELECT * FROM estoque_planos_acao")
        out = {r["chave"]: dict(r) for r in cur.fetchall()}
    conn.close()
    return out


def plano_upsert(d):
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO estoque_planos_acao
            (chave, tipo, codprod, dtvalidade, descricao, responsavel, acao, prazo, status, obs, atualizado_em)
            VALUES (%(chave)s,%(tipo)s,%(codprod)s,%(dtvalidade)s,%(descricao)s,%(responsavel)s,%(acao)s,%(prazo)s,%(status)s,%(obs)s, now())
            ON CONFLICT (chave) DO UPDATE SET
              responsavel=EXCLUDED.responsavel, acao=EXCLUDED.acao, prazo=EXCLUDED.prazo,
              status=EXCLUDED.status, obs=EXCLUDED.obs, atualizado_em=now()""",
            {"chave": d["chave"], "tipo": d.get("tipo", "geral"), "codprod": d.get("codprod"),
             "dtvalidade": d.get("dtvalidade"), "descricao": d.get("descricao"),
             "responsavel": d.get("responsavel"), "acao": d.get("acao"), "prazo": d.get("prazo"),
             "status": d.get("status") or "PENDENTE", "obs": d.get("obs")})
    conn.close()


def plano_delete(chave):
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM estoque_planos_acao WHERE chave=%s", (chave,))
    conn.close()
