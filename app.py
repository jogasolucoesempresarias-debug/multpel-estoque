"""
Painel de Estoque Multpel — app standalone.
Dev: python -X utf8 app.py   ->   http://localhost:5001
Prod: waitress-serve --port=5001 app:app  (atrás do Traefik no Portainer)

Consome o dataset Power BI "Estoque" (+ RCA p/ comprador/venda). Senha única via .env.
"""

import io
import os
import csv
import secrets
from datetime import date, timedelta

from flask import Flask, jsonify, request, send_from_directory, Response, session, redirect
from flask_cors import CORS

import pbi
import queries as Q
import core
import store

app = Flask(__name__, static_folder=None)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(16)
CORS(app, supports_credentials=True)

PORT = int(os.getenv("PORT", "5001"))
ESTOQUE_SENHA = os.getenv("ESTOQUE_SENHA", "")  # vazio = sem login (dev local)
store.init()  # cria tabelas estoque_* no Postgres (idempotente; degrada se indisponível)


# ───────────────────────── login (senha única compartilhada) ─────────────────────────
_LOGIN_HTML = """<!doctype html><html lang=pt-BR><head><meta charset=utf-8>
<title>Multpel · Estoque</title><meta name=viewport content="width=device-width,initial-scale=1">
<style>body{margin:0;height:100vh;display:grid;place-items:center;background:#0a0e17;color:#e2e8f0;font-family:system-ui,sans-serif}
.box{background:#111827;border:1px solid #1e293b;border-radius:14px;padding:30px;width:300px}
h1{font-size:1.1rem;margin:0 0 4px}.s{color:#64748b;font-size:.8rem;margin-bottom:18px}
input{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #1e293b;background:#1a2235;color:#e2e8f0;margin-bottom:12px}
button{width:100%;padding:10px;border:0;border-radius:8px;background:linear-gradient(135deg,#38bdf8,#818cf8);color:#0a0e17;font-weight:700;cursor:pointer}
.e{color:#f87171;font-size:.8rem;margin-bottom:10px}</style></head>
<body><form class=box method=post action=/login><h1>Multpel · Estoque</h1><div class=s>Acesso restrito</div>
{erro}<input type=password name=senha placeholder=Senha autofocus><button>Entrar</button></form></body></html>"""


@app.before_request
def _guard():
    if not ESTOQUE_SENHA:
        return  # sem senha configurada = aberto (dev)
    if session.get("auth"):
        return
    if request.path in ("/login", "/health") or request.path.startswith("/static/"):
        return
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "não autenticado"}), 401
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (request.form.get("senha") or "") == ESTOQUE_SENHA:
            session["auth"] = True
            return redirect("/")
        return _LOGIN_HTML.replace("{erro}", '<div class=e>Senha incorreta</div>'), 401
    if session.get("auth") or not ESTOQUE_SENHA:
        return redirect("/")
    return _LOGIN_HTML.replace("{erro}", "")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


def _mes_atual():
    return request.args.get("mes") or date.today().strftime("%Y-%m")


def _mes_atual():
    return request.args.get("mes") or date.today().strftime("%Y-%m")


# ───────────────────────── cadastro (cache 24h) ─────────────────────────
@pbi.cached(ttl=86400, key_fn=lambda: "cad:prod")
def _cadastro_produtos():
    rows = pbi.run_dax(Q.q_cadastro_produto())
    return {int(core._n(r["CODPROD"])): r for r in rows}


@pbi.cached(ttl=86400, key_fn=lambda: "cad:forn")
def _cadastro_fornecedores():
    rows = pbi.run_dax(Q.q_cadastro_fornecedor())
    return {int(core._n(r["CODFORNEC"])): r for r in rows}


@pbi.cached(ttl=86400, key_fn=lambda: "filiais")
def _filiais_disponiveis():
    rows = pbi.run_dax(Q.q_filiais())
    fs = sorted({str(r["CODFILIAL"]).strip() for r in rows if r.get("CODFILIAL") not in (None, "")},
                key=lambda x: (len(x), x))
    return fs


@pbi.cached(ttl=86400, key_fn=lambda: "compradores")
def _compradores_map():
    """{matricula: nome} — vem do dataset RCA (PCEMPR)."""
    try:
        rows = pbi.run_dax_rca(Q.q_compradores_rca())
        return {int(core._n(r["MATRICULA"])): r["NOME"]
                for r in rows if r.get("MATRICULA") not in (None, "") and r.get("NOME")}
    except Exception:
        return {}


# ───────────────────────── snapshot (cache 30min por filial-set) ─────────────────────────
def _filiais_param():
    raw = request.args.get("filiais", "").strip()
    if not raw:
        return list(Q.FILIAIS_PADRAO)  # default: CDs 3 e 5 (estoque endereçado)
    return [f.strip() for f in raw.split(",") if f.strip()]


def _filiais_key(filiais):
    return ",".join(sorted(filiais)) if filiais else "ALL"


def _snapshot_rows(filiais):
    key = f"snap:{_filiais_key(filiais)}"
    hit = pbi._CACHE.get(key)
    if hit is not None:
        return hit
    rows = pbi.run_dax(Q.q_snapshot_estoque(filiais))
    pbi._CACHE.set(key, rows, 1800)
    return rows


def _endereco_map(filiais):
    """{cod: qt_end} — estoque endereçado (RUA<>99) nas filiais."""
    key = f"end:{_filiais_key(filiais)}"
    hit = pbi._CACHE.get(key)
    if hit is not None:
        return hit
    rows = pbi.run_dax(Q.q_estoque_endereco(filiais))
    m = {int(core._n(r["CODPROD"])): core._n(r.get("qt_end")) for r in rows}
    pbi._CACHE.set(key, m, 1800)
    return m


def _hoje():
    h = request.args.get("hoje")
    if h:
        try:
            return date.fromisoformat(h)
        except ValueError:
            pass
    return date.today()


# ───────────────────────── venda real (dataset RCA, cache 30min) ─────────────────────────
def _venda_datas(periodo, hoje):
    if periodo == "90d":
        return hoje - timedelta(days=90), hoje
    if periodo == "12m":
        return hoje - timedelta(days=365), hoje
    return hoje.replace(day=1), hoje  # mês atual (default)


def _vendas_map(periodo, hoje):
    """{cod: {venda, custo, qtd}} líquido (venda − devoluções) do RCA. Degrada se RCA indisponível."""
    ini, fim = _venda_datas(periodo, hoje)
    key = f"venda:{periodo}:{ini}:{fim}"
    hit = pbi._CACHE.get(key)
    if hit is not None:
        return hit
    m = {}
    try:
        for r in pbi.run_dax_rca(Q.q_vendas_rca(ini, fim)):
            c = int(core._n(r["CODPROD"]))
            m[c] = {"venda": core._n(r.get("venda")), "custo": core._n(r.get("custo")), "qtd": core._n(r.get("qtd"))}
        for r in pbi.run_dax_rca(Q.q_devol_rca(ini, fim)):
            c = int(core._n(r["CODPROD"]))
            if c in m:
                m[c]["venda"] -= core._n(r.get("dev")); m[c]["custo"] -= core._n(r.get("cdev"))
        for r in pbi.run_dax_rca(Q.q_devol_av_rca(ini, fim)):
            c = int(core._n(r["CODPROD"]))
            if c in m:
                m[c]["venda"] -= core._n(r.get("devav")); m[c]["custo"] -= core._n(r.get("cdevav"))
    except Exception as e:
        print(f"[venda] RCA indisponível ({e}). Camada de vendas desabilitada.")
        m = {}
    pbi._CACHE.set(key, m, 1800)
    return m


def _build_produtos():
    """Constrói a lista enriquecida de produtos para os filtros/params atuais."""
    filiais = _filiais_param()
    params = core.merge_params(request.args.to_dict())
    snap = _snapshot_rows(filiais)
    end_map = _endereco_map(filiais)
    prod_map = _cadastro_produtos()
    forn_map = _cadastro_fornecedores()
    comp_map = _compradores_map()
    venda_map = _vendas_map(request.args.get("venda_periodo", "mes"), _hoje())
    produtos = core.construir_produtos(snap, end_map, prod_map, forn_map, comp_map, venda_map, params, hoje=_hoje())
    return produtos, params, filiais


# ───────────────────────── páginas ─────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/static/<path:filename>")
def static_assets(filename):
    return send_from_directory("static", filename)


@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "multpel-estoque"}), 200


# ───────────────────────── API ─────────────────────────
@app.route("/api/filtros")
def api_filtros():
    prod_map = _cadastro_produtos()
    forn_map = _cadastro_fornecedores()
    deptos = sorted({str(p.get("CODEPTO")) for p in prod_map.values()
                     if p.get("CODEPTO") not in (None, "")})
    fornecedores = sorted(
        [{"codfornec": cf, "fornecedor": f.get("FORNECEDOR") or f"FORN {cf}"}
         for cf, f in forn_map.items()],
        key=lambda x: x["fornecedor"] or "")
    # compradores que de fato têm fornecedores na base
    comp_map = _compradores_map()
    cods = {int(core._n(f.get("CODCOMPRADOR"))) for f in forn_map.values()
            if f.get("CODCOMPRADOR") not in (None, "")}
    compradores = sorted(
        [{"codcomprador": c, "comprador": comp_map.get(c, f"COMPRADOR {c}")} for c in cods],
        key=lambda x: x["comprador"] or "")
    return jsonify({
        "ok": True,
        "filiais": _filiais_disponiveis(),
        "filiais_padrao": list(Q.FILIAIS_PADRAO),
        "deptos": deptos,
        "fornecedores": fornecedores,
        "compradores": compradores,
        "defaults": core.DEFAULTS,
    })


@app.route("/api/snapshot")
def api_snapshot():
    produtos, params, filiais = _build_produtos()
    return jsonify({
        "ok": True,
        "gerado_em": date.today().isoformat(),
        "filiais": filiais or "ALL",
        "params": params,
        "n": len(produtos),
        "produtos": produtos,
        "cockpit": core.cockpit(produtos),
        "fornecedores": core.fornecedores(produtos, params),
        "compradores": core.por_comprador(produtos),
    })


@app.route("/api/validade")
def api_validade():
    produtos, params, filiais = _build_produtos()
    idx = {p["codprod"]: p for p in produtos}
    hoje = _hoje()
    dias = int(params["horizonte_val"])
    lotes = pbi.run_dax(Q.q_validade(hoje, hoje + timedelta(days=dias), filiais))
    fefo = core.validade_fefo(lotes, idx, params, hoje=hoje)
    resumo = {
        "n": len(fefo),
        "valor_risco": core._round(sum(l["valor_risco"] or 0 for l in fefo)),
        "critico": sum(1 for l in fefo if l["classificacao"] == "critico"),
        "atencao": sum(1 for l in fefo if l["classificacao"] == "atencao"),
        "planejar": sum(1 for l in fefo if l["classificacao"] == "planejar"),
        "giro_zero": sum(1 for l in fefo if l["risco"] == "giro_zero"),
    }
    return jsonify({"ok": True, "horizonte": dias, "resumo": resumo, "lotes": fefo})


@app.route("/api/produto/<int:codprod>")
def api_produto(codprod):
    produtos, params, filiais = _build_produtos()
    idx = {p["codprod"]: p for p in produtos}
    p = idx.get(codprod)
    lotes_raw = pbi.run_dax(Q.q_lotes_produto(codprod, filiais))
    lotes = core.validade_fefo(lotes_raw, idx, params, hoje=_hoje()) if p else []
    return jsonify({"ok": bool(p), "produto": p, "lotes": lotes})


# ───────────────────────── export CSV ─────────────────────────
_CSV_COLS = {
    "produtos": ["codprod", "descricao", "fornecedor", "comprador", "curva_abc", "xyz", "abc_xyz",
                 "qtdisp", "giro_mes", "cobertura", "dias_sem_venda",
                 "valor", "venda", "lucro", "margem", "status_abast", "status_parado"],
    "comprasvendas": ["codprod", "descricao", "fornecedor", "comprador", "valor", "venda",
                      "lucro", "margem", "giro_mes", "cobertura", "dias_sem_venda"],
    "reposicao": ["codprod", "descricao", "fornecedor", "comprador", "curva_abc", "giro_mes",
                  "qtdisp", "cobertura", "rop", "est_alvo", "sugestao_compra", "status_abast"],
    "parado": ["codprod", "descricao", "fornecedor", "comprador", "dias_sem_venda", "qtdisp",
               "valor", "cobertura", "status_parado"],
    "ruptura": ["codprod", "descricao", "fornecedor", "comprador", "qtdisp", "cobertura",
                "giro_mes", "sugestao_compra", "status_ruptura", "estoque_zero"],
}


def _export_data(view):
    """Devolve (cols, linhas) para a view, reaproveitado por CSV e XLSX."""
    if view == "validade":
        produtos, params, filiais = _build_produtos()
        idx = {p["codprod"]: p for p in produtos}
        hoje = _hoje()
        lotes = pbi.run_dax(Q.q_validade(hoje, hoje + timedelta(days=int(params["horizonte_val"])), filiais))
        linhas = core.validade_fefo(lotes, idx, params, hoje=hoje)
        cols = ["codprod", "descricao", "fornecedor", "comprador", "numlote", "dtval",
                "dias_para_vencer", "qt", "saldo_proj", "valor_risco", "classificacao", "risco"]
    elif view == "fornecedores":
        produtos, params, _ = _build_produtos()
        linhas = core.fornecedores(produtos, params)
        cols = ["codfornec", "fornecedor", "comprador", "n_produtos", "valor", "giro", "cobertura",
                "venda", "lucro", "margem", "perc_giro", "perc_estoque", "indice", "classificacao"]
    elif view == "compradores":
        produtos, _, _ = _build_produtos()
        linhas = core.por_comprador(produtos)
        cols = ["codcomprador", "comprador", "n_produtos", "estoque", "venda", "lucro",
                "margem", "n_ruptura", "valor_parado", "sugestao_valor"]
    else:
        produtos, _, _ = _build_produtos()
        cols = _CSV_COLS.get(view, _CSV_COLS["produtos"])
        if view == "reposicao":
            linhas = [p for p in produtos if (p["sugestao_compra"] or 0) > 0 and (p["giro_dia"] or 0) > 0]
        elif view == "parado":
            linhas = [p for p in produtos if p["status_parado"]]
        elif view == "ruptura":
            linhas = [p for p in produtos if p["status_ruptura"]]
        else:
            linhas = produtos
    return cols, linhas


@app.route("/api/export/<view>.csv")
def api_export_csv(view):
    cols, linhas = _export_data(view)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(cols)
    for r in linhas:
        w.writerow([r.get(c, "") for c in cols])
    return Response(
        "﻿" + buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="estoque_{view}.csv"'},
    )


@app.route("/api/export/<view>.xlsx")
def api_export_xlsx(view):
    from openpyxl import Workbook
    cols, linhas = _export_data(view)
    wb = Workbook(); ws = wb.active; ws.title = view[:31]
    ws.append([c.upper() for c in cols])
    for r in linhas:
        ws.append([r.get(c, "") for c in cols])
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return Response(
        bio.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="estoque_{view}.xlsx"'},
    )


# ───────────────────────── orçamento / pedidos (Postgres) ─────────────────────────
@app.route("/api/orcamento")
def api_orcamento():
    if not store.ensure():
        return jsonify({"ok": False, "error": "Postgres indisponível"}), 503
    mes = _mes_atual()
    comprador = request.args.get("comprador") or "TODOS"
    return jsonify({"ok": True, "resumo": store.orcamento_resumo(mes, comprador),
                    "pedidos": store.pedidos_list(mes, comprador)})


@app.route("/api/orcamento/meta", methods=["POST"])
def api_orcamento_meta():
    d = request.get_json() or {}
    store.orcamento_set(d.get("mes") or date.today().strftime("%Y-%m"),
                        d.get("comprador") or "TODOS", d.get("meta_valor") or 0)
    return jsonify({"ok": True})


@app.route("/api/pedidos", methods=["POST"])
def api_pedido_add():
    d = request.get_json() or {}
    d.setdefault("mes", d.get("data_pedido", date.today().isoformat())[:7])
    return jsonify({"ok": True, "id": store.pedido_add(d)})


@app.route("/api/pedidos/<int:pid>", methods=["PUT", "DELETE"])
def api_pedido_edit(pid):
    if request.method == "DELETE":
        store.pedido_delete(pid)
    else:
        store.pedido_update(pid, request.get_json() or {})
    return jsonify({"ok": True})


# ───────────────────────── planos de ação (Postgres) ─────────────────────────
@app.route("/api/planos")
def api_planos():
    if not store.ensure():
        return jsonify({"ok": True, "planos": {}})
    return jsonify({"ok": True, "planos": store.planos_map(request.args.get("tipo"))})


@app.route("/api/planos", methods=["POST"])
def api_plano_upsert():
    d = request.get_json() or {}
    if not d.get("chave"):
        return jsonify({"ok": False, "error": "chave obrigatória"}), 400
    store.plano_upsert(d)
    return jsonify({"ok": True})


@app.route("/api/planos/<path:chave>", methods=["DELETE"])
def api_plano_delete(chave):
    store.plano_delete(chave)
    return jsonify({"ok": True})


if __name__ == "__main__":
    print(f"\n  Painel de Estoque  →  http://localhost:{PORT}\n")
    app.run(host="0.0.0.0", port=PORT, debug=True)
