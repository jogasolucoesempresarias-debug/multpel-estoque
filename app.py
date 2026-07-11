"""
Painel de Estoque Multpel — app standalone.
Dev: python -X utf8 app.py   ->   http://localhost:5001
Prod: waitress-serve --port=5001 app:app  (atrás do Traefik no Portainer)

Consome o dataset Power BI "Estoque" (+ RCA p/ comprador/venda). Senha única via .env.
"""

import io
import os
import re
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
    """{matricula: nome} — PCEMPR no dataset Estoque (fallback RCA)."""
    for runner, q in ((pbi.run_dax, Q.q_compradores_estoque()),
                      (pbi.run_dax_rca, Q.q_compradores_rca())):
        try:
            rows = runner(q)
            m = {int(core._n(r["MATRICULA"])): r["NOME"]
                 for r in rows if r.get("MATRICULA") not in (None, "") and r.get("NOME")}
            if m:
                return m
        except Exception:
            continue
    return {}


# ───────────────────────── snapshot (cache 30min por filial-set) ─────────────────────────
def _filiais_param():
    raw = request.args.get("filiais", "").strip()
    if not raw:
        return list(Q.FILIAIS_PADRAO)  # default: CDs 3 e 5 (estoque endereçado)
    return [f.strip() for f in raw.split(",") if f.strip()]


def _filiais_key(filiais):
    return ",".join(sorted(filiais)) if filiais else "ALL"


# ───────────────────────── unidades de negócio (estrutura da Multpel) ─────────────────────────
# Estoque físico (PCEST) e Venda (RCA faturamento) vivem em filiais DIFERENTES por unidade.
# Atacado: estoque nos CDs 3+5, mas fatura em 3+7+8 (5=depósito, 7/8=venda sem estoque).
# Lojas (A&M/AC) e JID são autossuficientes. Filiais 1,2,6,10-13,15 excluídas; 8 sem estoque.
NOMES_FILIAL = {"3": "Multpel Matriz", "4": "A&M", "5": "Deposito",
                "7": "Telemarketing", "8": "Atacado", "9": "JID", "14": "AC"}
UNIDADES = {
    "atacado": {"nome": "Atacado", "estoque": ["3", "5"],            "venda": ["3", "7", "8"]},
    "am":      {"nome": "A&M",     "estoque": ["4"],                 "venda": ["4"]},
    "ac":      {"nome": "AC",      "estoque": ["14"],                "venda": ["14"]},
    "jid":     {"nome": "JID",     "estoque": ["9"],                 "venda": ["9"]},
    "todas":   {"nome": "Todas",   "estoque": ["3", "5", "4", "14", "9"], "venda": ["3", "7", "8", "4", "14", "9"]},
}
UNIDADE_PADRAO = "atacado"

# Dados fixos do emitente (Multpel) p/ o cabeçalho do pedido de compra — estilo relatório 211.
MULTPEL_EMPRESA = {
    "razao": "MULTPEL COM. DE PAPEIS E EMBALAGENS LTDA",
    "cnpj": "02.262.785/0001-04",
    "ie": "081924950",
    "endereco": "Rua Antonio Pedro Carleto, 56",
    "bairro": "Vila Rica",
    "cep": "29301-200",
    "cidade": "Cachoeiro de Itapemirim",
    "uf": "ES",
    "tel": "(28) 3526-1450",
    "email": "fiscal@mutpelatacado.com.br",
}


def _unidade():
    u = (request.args.get("unidade") or UNIDADE_PADRAO).lower()
    return u if u in UNIDADES else UNIDADE_PADRAO


def _filiais_estoque():
    """Filiais de ESTOQUE físico (PCEST) da unidade atual."""
    return list(UNIDADES[_unidade()]["estoque"])


def _filiais_venda():
    """Filiais de VENDA/faturamento (RCA) da unidade atual."""
    return list(UNIDADES[_unidade()]["venda"])


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


# ───────────────────────── embalagem / pedido real (cache) ─────────────────────────
@pbi.cached(ttl=86400, key_fn=lambda: "embalagem")
def _embalagem_map():
    """{cod: {qtunit, volume, ...}} — caixa/cubagem do PCEMBALAGEM (Estoque)."""
    try:
        rows = pbi.run_dax(Q.q_embalagem())
        return {int(core._n(r["CODPROD"])): r for r in rows if r.get("CODPROD") not in (None, "")}
    except Exception as e:
        print(f"[embalagem] indisponível ({e}).")
        return {}


def _pedidos_data(filiais, hoje):
    """{'cab': [...PCPEDIDO], 'ja_pedida': {cod: qt_aberta}} — pedido de compra REAL (Winthor).
    Reutilizado pelo abastecimento (já-pedido) e pelo orçamento/acompanhamento (cabeçalho).
    Degrada p/ vazio se indisponível. Cache 30min por filial-set + data."""
    key = f"peddata:{_filiais_key(filiais)}:{hoje.isoformat()}"
    hit = pbi._CACHE.get(key)
    if hit is not None:
        return hit
    data = {"cab": [], "itens": [], "ja_pedida": {}}
    try:
        cab = pbi.run_dax(Q.q_pedido_cab(hoje - timedelta(days=180), filiais))
        if cab:
            numped_min = min(int(core._n(r["NUMPED"])) for r in cab)
            itens = pbi.run_dax(Q.q_pedido_itens(numped_min))
            data = {"cab": cab, "itens": itens,
                    "ja_pedida": core.montar_ja_pedida(cab, itens, hoje=hoje, dias=180)}
    except Exception as e:
        print(f"[pedidos] Winthor indisponível ({e}). Pedido real desabilitado.")
    pbi._CACHE.set(key, data, 1800)
    return data


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
    if periodo == "30d":
        return hoje - timedelta(days=30), hoje
    if periodo == "90d":
        return hoje - timedelta(days=90), hoje
    if periodo == "6m":
        return hoje - timedelta(days=180), hoje
    if periodo == "12m":
        return hoje - timedelta(days=365), hoje
    return hoje.replace(day=1), hoje  # mês atual (default)


def _vendas_map(periodo, hoje, filiais=None):
    """{cod: {venda, custo, qtd}} líquido (venda − devoluções) do RCA, escopado por filiais de
    VENDA da unidade. Degrada se RCA indisponível."""
    ini, fim = _venda_datas(periodo, hoje)
    key = f"venda:{periodo}:{_filiais_key(filiais)}:{ini}:{fim}"
    hit = pbi._CACHE.get(key)
    if hit is not None:
        return hit
    m = {}
    try:
        for r in pbi.run_dax_rca(Q.q_vendas_rca(ini, fim, filiais)):
            c = int(core._n(r["CODPROD"]))
            m[c] = {"venda": core._n(r.get("venda")), "custo": core._n(r.get("custo")), "qtd": core._n(r.get("qtd"))}
        for r in pbi.run_dax_rca(Q.q_devol_rca(ini, fim, filiais)):
            c = int(core._n(r["CODPROD"]))
            if c in m:
                m[c]["venda"] -= core._n(r.get("dev")); m[c]["custo"] -= core._n(r.get("cdev"))
        for r in pbi.run_dax_rca(Q.q_devol_av_rca(ini, fim, filiais)):
            c = int(core._n(r["CODPROD"]))
            if c in m:
                m[c]["venda"] -= core._n(r.get("devav")); m[c]["custo"] -= core._n(r.get("cdevav"))
    except Exception as e:
        print(f"[venda] RCA indisponível ({e}). Camada de vendas desabilitada.")
        m = {}
    pbi._CACHE.set(key, m, 1800)
    return m


def _vendas_mensal_map(meses, hoje, profundo=False, filiais=None):
    """{cod: {AnoMes: qtd}} — venda mensal (QT) do RCA p/ o forecast, escopada por filiais de
    VENDA da unidade. Cache 12h. Degrada p/ {} se RCA indisponível. Só quando forecast ligado.
    profundo=True (sazonalidade) força ≥25 meses de histórico p/ o fator ano-a-ano."""
    fetch = max(25, int(meses)) if profundo else max(1, int(meses))
    ini = (hoje.replace(day=1) - timedelta(days=1)).replace(day=1)  # 1º dia do mês anterior
    for _ in range(fetch):
        ini = (ini - timedelta(days=1)).replace(day=1)
    key = f"vmes:{fetch}:{_filiais_key(filiais)}:{hoje.strftime('%Y-%m')}"
    hit = pbi._CACHE.get(key)
    if hit is not None:
        return hit
    m = {}
    try:
        for r in pbi.run_dax_rca(Q.q_vendas_mensal_rca(ini, filiais)):
            c = int(core._n(r["CODPROD"]))
            am = int(core._n(r.get("AM")))
            if am:
                m.setdefault(c, {})[am] = core._n(r.get("qtd"))
    except Exception as e:
        print(f"[forecast] RCA mensal indisponível ({e}). Forecast desabilitado.")
        m = {}
    pbi._CACHE.set(key, m, 43200)  # 12h
    return m


def _desempenho_data(periodo, hoje, filiais=None):
    """{resumo, compradores} — desempenho comercial por comprador (RCA), escopado por filiais de
    VENDA da unidade. Espelha RECEITA COMPRADOR + comparativo ano×ano. Cache 30min."""
    ini, fim = _venda_datas(periodo, hoje)
    key = f"desemp:{periodo}:{_filiais_key(filiais)}:{ini}:{fim}"
    hit = pbi._CACHE.get(key)
    if hit is not None:
        return hit
    res = {"resumo": {}, "compradores": []}
    try:
        receita = pbi.run_dax_rca(Q.q_receita_comprador_rca(ini, fim, filiais))
        devol, custo_dev = {}, {}
        for r in pbi.run_dax_rca(Q.q_devol_comprador_rca(ini, fim, filiais)):
            cc = r.get("CODCOMPRADOR")
            if cc not in (None, ""):
                k = int(core._n(cc))
                devol[k] = core._n(r.get("dev"))
                custo_dev[k] = core._n(r.get("cdev"))
        # mesmo período no ano anterior (comparativo YoY de venda E lucro por comprador)
        ini_ant = ini.replace(year=ini.year - 1)
        fim_ant = fim.replace(year=fim.year - 1)
        venda_ant, custo_ant = {}, {}
        for r in pbi.run_dax_rca(Q.q_venda_comprador_periodo_rca(ini_ant, fim_ant, filiais)):
            cc = r.get("CODCOMPRADOR")
            if cc not in (None, ""):
                venda_ant[int(core._n(cc))] = core._n(r.get("venda"))
                custo_ant[int(core._n(cc))] = core._n(r.get("custo"))
        res = core.desempenho_comprador(receita, devol, _compradores_map(), venda_ant, custo_ant,
                                        custo_dev)
    except Exception as e:
        print(f"[desempenho] RCA indisponível ({e}). Aba de desempenho desabilitada.")
        res = {"resumo": {}, "compradores": []}
    pbi._CACHE.set(key, res, 1800)
    return res


def _venda_comprador_30d(fil_estoque, fil_venda, hoje):
    """{nome_comprador: venda_liquida_30d} — base da meta de orçamento (65%). Estoque e venda
    escopados por filiais diferentes da unidade (ex.: Atacado = estoque 3+5, venda 3+7+8)."""
    key = f"vcomp30:{_filiais_key(fil_estoque)}:{_filiais_key(fil_venda)}:{hoje.isoformat()}"
    hit = pbi._CACHE.get(key)
    if hit is not None:
        return hit
    produtos = core.construir_produtos(
        _snapshot_rows(fil_estoque), _endereco_map(fil_estoque), _cadastro_produtos(),
        _cadastro_fornecedores(), _compradores_map(), _vendas_map("30d", hoje, fil_venda),
        dict(core.DEFAULTS), hoje=hoje)
    m = {g["comprador"]: g["venda"] for g in core.por_comprador(produtos) if g.get("comprador")}
    pbi._CACHE.set(key, m, 1800)
    return m


def _build_produtos():
    """Constrói a lista enriquecida de produtos para a unidade/params atuais.
    Estoque (snapshot/endereço/pedido) usa as filiais de ESTOQUE; venda/forecast usam as de VENDA."""
    filiais_e = _filiais_estoque()
    filiais_v = _filiais_venda()
    params = core.merge_params(request.args.to_dict())
    snap = _snapshot_rows(filiais_e)
    end_map = _endereco_map(filiais_e)
    prod_map = _cadastro_produtos()
    forn_map = _cadastro_fornecedores()
    comp_map = _compradores_map()
    venda_map = _vendas_map(request.args.get("venda_periodo", "mes"), _hoje(), filiais_v)
    venda_mensal = (_vendas_mensal_map(params["forecast_meses"], _hoje(), profundo=bool(params.get("forecast_sazonal")), filiais=filiais_v)
                    if params.get("forecast") else None)
    ja_pedida = _pedidos_data(filiais_e, _hoje())["ja_pedida"]
    embalagem = _embalagem_map()
    produtos = core.construir_produtos(snap, end_map, prod_map, forn_map, comp_map, venda_map, params,
                                       hoje=_hoje(), venda_mensal_map=venda_mensal,
                                       ja_pedida_map=ja_pedida, embalagem_map=embalagem)
    return produtos, params, filiais_e


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
    # compradores responsáveis por COMPRA P/ REVENDA: só os ligados a fornecedores
    # que têm produto revenda (deriva da base, sem nome chumbado). Remove PAGAR, etc.
    comp_map = _compradores_map()
    forns_revenda = {int(core._n(p.get("CODFORNEC"))) for p in prod_map.values()
                     if p.get("CODFORNEC") not in (None, "")}
    cods = {int(core._n(forn_map[cf].get("CODCOMPRADOR"))) for cf in forns_revenda
            if cf in forn_map and forn_map[cf].get("CODCOMPRADOR") not in (None, "")}
    compradores = sorted(
        [{"codcomprador": c, "comprador": comp_map.get(c, f"COMPRADOR {c}")} for c in cods],
        key=lambda x: x["comprador"] or "")
    return jsonify({
        "ok": True,
        "filiais": _filiais_disponiveis(),
        "filiais_padrao": list(Q.FILIAIS_PADRAO),
        "unidades": [{"id": uid, "nome": u["nome"],
                      "cod": "" if uid == "todas" else ",".join(sorted(set(u["estoque"] + u["venda"]), key=int))}
                     for uid, u in UNIDADES.items()],
        "unidade_padrao": UNIDADE_PADRAO,
        "nomes_filial": NOMES_FILIAL,
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
        "bi_refresh": pbi.get_dataset_refresh(),
        "filiais": filiais or "ALL",
        "unidade": _unidade(),
        "unidade_nome": UNIDADES[_unidade()]["nome"],
        "params": params,
        "n": len(produtos),
        "produtos": produtos,
        "cockpit": core.cockpit(produtos),
        "fornecedores": core.fornecedores(produtos, params),
        "compradores": core.por_comprador(produtos),
    })


@app.route("/api/desempenho")
def api_desempenho():
    """Desempenho comercial por comprador: venda líquida, lucro, margem ponderada, positivação
    (clientes distintos), devolução e comparativo ano×ano. Período via ?venda_periodo=."""
    periodo = request.args.get("venda_periodo", "mes")
    d = _desempenho_data(periodo, _hoje(), _filiais_venda())
    return jsonify({"ok": True, "periodo": periodo,
                    "resumo": d["resumo"], "compradores": d["compradores"]})


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


@app.route("/api/resumos")
def api_resumos():
    """Painel gerencial do diretor: 2 blocos-resumo (itens a vencer por faixa de validade +
    cobertura de estoque por faixa de dias). Validade busca a janela inteira (não só 30d)."""
    produtos, params, filiais = _build_produtos()
    hoje = _hoje()
    comprador = request.args.get("comprador") or "TODOS"
    todos = comprador in ("", "TODOS")
    # respeita o filtro de comprador do topo (painéis recalculam por comprador)
    prod_f = produtos if todos else [p for p in produtos if (p.get("comprador") or "") == comprador]
    idx = {p["codprod"]: p for p in prod_f}
    lotes = pbi.run_dax(Q.q_validade(hoje, hoje + timedelta(days=3650), filiais))
    if not todos:
        cods = set(idx)
        lotes = [l for l in lotes if int(core._n(l.get("CODPROD"))) in cods]
    cab = _pedidos_data(filiais, hoje)["cab"]
    venda_comp = _venda_comprador_30d(filiais, _filiais_venda(), hoje)
    orc = core.orcamento_winthor(cab, venda_comp, _compradores_map(), _cadastro_fornecedores(),
                                 _mes_atual(), comprador, pct=0.65, hoje=hoje, meta_override=None)
    return jsonify({
        "ok": True,
        "gerado_em": hoje.isoformat(),
        "validade": core.resumo_validade(lotes, idx, hoje=hoje),
        "cobertura": core.resumo_cobertura(prod_f),
        "orcamento": orc["resumo"],
        "ruptura": core.resumo_ruptura(prod_f),
    })


@app.route("/api/produto/<int:codprod>")
def api_produto(codprod):
    produtos, params, filiais = _build_produtos()
    idx = {p["codprod"]: p for p in produtos}
    p = idx.get(codprod)
    lotes_raw = pbi.run_dax(Q.q_lotes_produto(codprod, filiais))
    lotes = core.validade_fefo(lotes_raw, idx, params, hoje=_hoje()) if p else []
    if p:
        p = {**p, "plano": core.plano_reposicao(p, params, hoje=_hoje())}
    return jsonify({"ok": bool(p), "produto": p, "lotes": lotes})


@app.route("/api/plano_reposicao")
def api_plano_reposicao():
    """Plano DRP de todos os produtos com giro>0 e sugestão>0 — alimenta a aba 'Plano reposição'."""
    produtos, params, _ = _build_produtos()
    hoje = _hoje()
    itens = []
    for p in produtos:
        if (p.get("giro_dia") or 0) <= 0 or (p.get("sugestao_compra") or 0) <= 0:
            continue
        plano = core.plano_reposicao(p, params, hoje=hoje)
        if not plano["liberacoes"]:
            continue
        itens.append({
            "codprod": p["codprod"], "descricao": p["descricao"],
            "codfornec": p["codfornec"], "fornecedor": p["fornecedor"],
            "comprador": p.get("comprador"), "qtdisp": p["qtdisp"],
            "cobertura": p["cobertura"], "giro_mes": p["giro_mes"],
            "custo_unit": p["custo_unit"], "lead_efetivo": p["lead_efetivo"],
            "qtunitcx": p.get("qtunitcx"), "giro_fonte": p.get("giro_fonte"),
            "liberacoes": plano["liberacoes"],
        })
    return jsonify({"ok": True, "gerado_em": hoje.isoformat(), "n": len(itens), "itens": itens})


# ───────────────────────── export CSV ─────────────────────────
_CSV_COLS = {
    "produtos": ["codprod", "descricao", "fornecedor", "comprador", "curva_abc", "xyz", "abc_xyz",
                 "qtdisp", "qtbloq", "giro_mes", "cobertura", "dias_sem_venda",
                 "valor", "venda", "lucro", "margem", "status_abast", "status_parado"],
    "comprasvendas": ["codprod", "descricao", "fornecedor", "comprador", "valor", "venda",
                      "lucro", "margem", "giro_mes", "cobertura", "dias_sem_venda"],
    "reposicao": ["codprod", "descricao", "fornecedor", "comprador", "curva_abc", "giro_mes",
                  "qtdisp", "cobertura", "rop", "est_alvo", "sugestao_compra", "status_abast"],
    "parado": ["codprod", "descricao", "fornecedor", "comprador", "dtultsaida", "dias_sem_venda", "qtdisp",
               "valor", "cobertura", "parado_faixa"],
    "ruptura": ["codprod", "descricao", "fornecedor", "comprador", "qtdisp", "valor", "cobertura_dias",
                "cobertura_faixa", "qtd_ja_pedida", "giro_mes", "sugestao_compra"],
    "estoque_zero": ["codprod", "descricao", "fornecedor", "comprador", "qtdisp", "dias_sem_venda",
                     "qtd_ja_pedida", "giro_mes", "sugestao_cx", "status_exec"],
}


def _aplicar_filtros_cliente(produtos):
    """Aplica os filtros ativos da UI (mesma lógica do filtered() do front) p/ que os exports
    respeitem o que está na tela. Lê os params da querystring (enviados pelo exportQS)."""
    a = request.args

    def g(k):
        v = a.get(k)
        return v if v not in (None, "") else None

    out = produtos
    cc = g("comprador_cod")
    if cc:
        out = [p for p in out if str(p.get("codcomprador")) == cc]
    if g("curva"):
        out = [p for p in out if p.get("curva_abc") == g("curva")]
    if g("xyz"):
        out = [p for p in out if p.get("xyz") == g("xyz")]
    if g("fornec"):
        out = [p for p in out if str(p.get("codfornec")) == g("fornec")]
    if g("depto"):
        out = [p for p in out if str(p.get("codepto")) == g("depto")]
    if g("abast"):
        _ab = {v for v in g("abast").split(",") if v}
        out = [p for p in out if p.get("status_abast") in _ab]
    bs = g("busca")
    if bs:
        bs = bs.lower()
        out = [p for p in out if bs in str(p.get("codprod")) or bs in (p.get("descricao") or "").lower()]
    # filtros específicos de aba
    if g("ez_status"):
        out = [p for p in out if p.get("status_exec") == g("ez_status")]
    if g("cob_faixa"):
        _cf = {x for x in g("cob_faixa").split(",") if x}   # multi-seleção de faixas
        out = [p for p in out if p.get("cobertura_faixa") in _cf]
    if g("cob_sub") == "semgiro":
        out = [p for p in out if p.get("sem_giro")]
    elif g("cob_sub") == "excesso":
        out = [p for p in out if p.get("excesso_real")]
    cp = g("cob_ped")
    if cp == "com":
        out = [p for p in out if (p.get("qtd_ja_pedida") or 0) > 0]
    elif cp == "sem":
        out = [p for p in out if (p.get("qtd_ja_pedida") or 0) <= 0]
    if g("par_classe"):
        out = [p for p in out if p.get("status_parado") == g("par_classe")]
    return out


def _export_data(view):
    """Devolve (cols, linhas) para a view, reaproveitado por CSV e XLSX. Respeita os filtros da UI."""
    if view == "validade":
        produtos, params, filiais = _build_produtos()
        idx = {p["codprod"]: p for p in produtos}
        hoje = _hoje()
        cods = {p["codprod"] for p in _aplicar_filtros_cliente(produtos)}
        lotes = pbi.run_dax(Q.q_validade(hoje, hoje + timedelta(days=int(params["horizonte_val"])), filiais))
        linhas = [l for l in core.validade_fefo(lotes, idx, params, hoje=hoje) if l["codprod"] in cods]
        _vd = request.args.get("val_dias")
        if _vd:
            try:
                linhas = [l for l in linhas if l["dias_para_vencer"] <= int(_vd)]
            except ValueError:
                pass
        # faixa de validade clicada no gráfico/cards (mesmo range da tela) — senão o export sai com tudo
        _flo, _fhi = request.args.get("val_faixa_lo"), request.args.get("val_faixa_hi")
        if _flo and _fhi:
            try:
                lo_i, hi_i = int(float(_flo)), int(float(_fhi))
                linhas = [l for l in linhas if lo_i <= l["dias_para_vencer"] <= hi_i]
            except ValueError:
                pass
        cols = ["codprod", "descricao", "comprador", "fornecedor", "numlote", "dtval",
                "dias_para_vencer", "qt", "saldo_proj", "valor_risco", "classificacao", "risco"]
    elif view == "fornecedores":
        produtos, params, _ = _build_produtos()
        linhas = core.fornecedores(_aplicar_filtros_cliente(produtos), params)
        fc = request.args.get("forn_classe")
        if fc:
            linhas = [r for r in linhas if r.get("classificacao") == fc]
        cols = ["codfornec", "fornecedor", "comprador", "n_produtos", "valor", "giro", "cobertura",
                "venda", "lucro", "margem", "perc_venda", "perc_estoque", "indice", "classificacao"]
    elif view == "compradores":
        produtos, _, _ = _build_produtos()
        linhas = core.por_comprador(_aplicar_filtros_cliente(produtos))
        cols = ["codcomprador", "comprador", "n_produtos", "estoque", "venda", "lucro",
                "margem", "n_ruptura", "valor_parado", "sugestao_valor"]
    elif view == "ruptura_comprador":
        produtos, _, _ = _build_produtos()
        linhas = core.ruptura_por_comprador(_aplicar_filtros_cliente(produtos))
        cols = ["codcomprador", "comprador", "n_produtos", "n_ruptura", "pct_ruptura",
                "n_sem_pedido", "venda_perdida", "custo_reposicao"]
    elif view == "desempenho":
        linhas = _desempenho_data(request.args.get("venda_periodo", "mes"), _hoje(), _filiais_venda())["compradores"]
        cols = ["ranking", "comprador", "fornecedores", "clientes_pos", "venda_liquida",
                "lucro_bruto", "margem", "devolucao", "part_receita", "part_lucro",
                "yoy", "yoy_lucro", "status_lucro"]
    else:
        produtos, _, _ = _build_produtos()
        produtos = _aplicar_filtros_cliente(produtos)
        cols = _CSV_COLS.get(view, _CSV_COLS["produtos"])
        if view == "reposicao":
            linhas = [p for p in produtos if (p["sugestao_compra"] or 0) > 0 and (p["giro_dia"] or 0) > 0]
        elif view == "parado":
            # universo do parado = parado_faixa != None (≥15d, com estoque); filtro opcional por faixa
            # (mesmo critério da tela, p/ o export bater com o que aparece). Agrupa por fornecedor.
            _pf = request.args.get("par_faixa")
            faixas_sel = {x for x in _pf.split(",") if x} if _pf else None
            linhas = sorted((p for p in produtos if p.get("parado_faixa") and (faixas_sel is None or p["parado_faixa"] in faixas_sel)),
                            key=lambda p: ((p.get("fornecedor") or "").upper(), p.get("codprod") or 0))
        elif view == "ruptura":
            # cobertura de estoque por faixa (base inteira, métrica da planilha) — maior valor 1º
            linhas = sorted(produtos, key=lambda p: -(p.get("valor") or 0))
        elif view == "estoque_zero":
            linhas = [p for p in produtos if (p.get("qtdisp") or 0) <= 0]
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


# ───────────────────────── export PDF (mesma estética do Multpel HTML) ─────────────
# colunas principais por tela: (chave, rótulo, tipo[, maxlen])
_PDF_COLS = {
    "produtos": [("codprod", "Cód", "text"), ("descricao", "Produto", "text", 40), ("fornecedor", "Fornecedor", "text", 26),
                 ("curva_abc", "ABC", "text"), ("qtdisp", "Disp.", "int"), ("giro_mes", "Giro/mês", "int"),
                 ("cobertura", "Cob.(d)", "int"), ("valor", "Valor", "money"), ("status_abast", "Abast.", "text")],
    "comprasvendas": [("codprod", "Cód", "text"), ("descricao", "Produto", "text", 40), ("fornecedor", "Fornecedor", "text", 26),
                      ("valor", "Estoque", "money"), ("venda", "Venda", "money"), ("lucro", "Lucro", "money"),
                      ("margem", "Margem", "pct"), ("cobertura", "Cob.(d)", "int")],
    "reposicao": [("codprod", "Cód", "text"), ("descricao", "Produto", "text", 40), ("fornecedor", "Fornecedor", "text", 26),
                  ("qtdisp", "Disp.", "int"), ("cobertura", "Cob.(d)", "int"), ("giro_mes", "Giro/mês", "int"),
                  ("sugestao_compra", "Sugerido", "int")],
    "parado": [("codprod", "Cód", "text"), ("descricao", "Produto", "text", 38), ("fornecedor", "Fornecedor", "text", 24),
               ("dtultsaida", "Últ. venda", "date"), ("dias_sem_venda", "Dias s/v", "int"), ("qtdisp", "Disp.", "int"),
               ("valor", "Valor", "money"), ("parado_faixa", "Faixa", "text")],
    "ruptura": [("codprod", "Cód", "text"), ("descricao", "Produto", "text", 36), ("fornecedor", "Fornecedor", "text", 22),
                ("qtdisp", "Disp.", "int"), ("valor", "Valor", "money"), ("cobertura_dias", "Cob.(d)", "int"),
                ("cobertura_faixa", "Faixa", "text"), ("qtd_ja_pedida", "Já ped.", "int"), ("giro_mes", "Giro/mês", "int"),
                ("sugestao_compra", "Sugerido", "int")],
    "estoque_zero": [("codprod", "Cód", "text"), ("descricao", "Produto", "text", 40), ("fornecedor", "Fornecedor", "text", 26),
                     ("qtdisp", "Estoque", "int"), ("dias_sem_venda", "Dias s/ venda", "int"), ("qtd_ja_pedida", "Já ped.", "int"),
                     ("giro_mes", "Giro/mês", "int"), ("sugestao_cx", "Sug.(cx)", "int"), ("status_exec", "Status", "text")],
    "validade": [("codprod", "Cód", "text"), ("descricao", "Produto", "text", 34), ("comprador", "Comprador", "text", 18),
                 ("fornecedor", "Fornecedor", "text", 22),
                 ("dtval", "Validade", "date"), ("dias_para_vencer", "Dias", "int"), ("qt", "Qtd", "int"),
                 ("valor_risco", "Valor risco", "money"), ("classificacao", "Classe", "text")],
    "fornecedores": [("codfornec", "Cód", "text"), ("fornecedor", "Fornecedor", "text", 34), ("n_produtos", "Itens", "int"),
                     ("valor", "Estoque", "money"), ("venda", "Venda", "money"), ("margem", "Margem", "pct"),
                     ("indice", "Índice", "num"), ("classificacao", "Classe", "text")],
    "compradores": [("codcomprador", "Cód", "text"), ("comprador", "Comprador", "text", 30), ("n_produtos", "Itens", "int"),
                    ("estoque", "Estoque", "money"), ("venda", "Venda", "money"), ("lucro", "Lucro", "money"),
                    ("margem", "Margem", "pct")],
    "ruptura_comprador": [("comprador", "Comprador", "text", 30), ("n_produtos", "Produtos", "int"),
                          ("n_ruptura", "Em ruptura", "int"), ("pct_ruptura", "% Rupt.", "num"),
                          ("n_sem_pedido", "Sem pedido", "int"), ("venda_perdida", "Venda perdida/mês", "money"),
                          ("custo_reposicao", "Custo reposição", "money")],
    "desempenho": [("ranking", "#", "int"), ("comprador", "Comprador", "text", 28),
                   ("clientes_pos", "Positivação", "int"), ("venda_liquida", "Venda líq.", "money"),
                   ("lucro_bruto", "Lucro bruto", "money"), ("margem", "Margem", "pct"),
                   ("devolucao", "Devolução", "money"), ("part_lucro", "% Lucro", "num"),
                   ("yoy", "AA Venda", "pct"), ("yoy_lucro", "AA Lucro", "pct")],
}
_PDF_TITULO = {"produtos": "Produtos", "comprasvendas": "Compras × Vendas", "reposicao": "Reposição",
               "parado": "Estoque parado", "ruptura": "Cobertura de estoque", "validade": "Validade / FEFO",
               "fornecedores": "Fornecedores", "compradores": "Compradores", "estoque_zero": "Estoque zerado",
               "ruptura_comprador": "Ruptura por comprador", "desempenho": "Desempenho comercial"}


def _fmt_pdf(v, kind, maxlen=None):
    if v is None or v == "":
        return "—"
    try:
        if kind == "money":
            return ("R$ %s" % f"{float(v):,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")
        if kind == "int":
            return f"{int(round(float(v))):,}".replace(",", ".")
        if kind == "num":
            return f"{float(v):.2f}".replace(".", ",")
        if kind == "pct":
            return f"{float(v):.1f}%"
        if kind == "date":
            s = str(v)[:10].split("-")
            return f"{s[2]}/{s[1]}/{s[0]}" if len(s) == 3 else str(v)
    except (ValueError, TypeError):
        pass
    s = str(v)
    return (s[:maxlen - 1] + "…") if (maxlen and len(s) > maxlen) else s


def _gerar_pdf(view, linhas):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT

    spec = _PDF_COLS.get(view) or _PDF_COLS["produtos"]
    titulo = _PDF_TITULO.get(view, view.capitalize())
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1.2 * cm, bottomMargin=1.5 * cm,
                            title=f"Estoque — {titulo}")
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('t', parent=styles['Heading1'], fontSize=14, alignment=TA_LEFT, textColor=colors.HexColor('#0a0e17'))
    sub_style = ParagraphStyle('s', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#475569'))

    story = [Paragraph(f'<b>Multpel · Estoque</b> — {titulo}', titulo_style),
             Paragraph(f"Gerado em {date.today().strftime('%d/%m/%Y')} · {len(linhas)} registros", sub_style),
             Spacer(1, 0.3 * cm)]

    header = [c[1] for c in spec]
    data = [header]
    for r in linhas:
        data.append([_fmt_pdf(r.get(c[0]), c[2], c[3] if len(c) > 3 else None) for c in spec])

    # larguras proporcionais (pesos por tipo; texto largo p/ descrição/fornecedor)
    def _peso(c):
        if len(c) > 3:
            return c[3] / 7.0
        return {"money": 2.2, "date": 1.8, "pct": 1.3, "int": 1.3, "num": 1.4}.get(c[2], 1.5)
    pesos = [_peso(c) for c in spec]
    usable = landscape(A4)[0] - 2.4 * cm
    soma = sum(pesos) or 1
    col_w = [usable * p / soma for p in pesos]

    estilo = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]
    for i, c in enumerate(spec):
        al = 'RIGHT' if c[2] in ("int", "money", "pct", "num") else ('CENTER' if c[2] == "date" else 'LEFT')
        if al != 'LEFT':
            estilo.append(('ALIGN', (i, 0), (i, -1), al))
    tbl = Table(data, repeatRows=1, colWidths=col_w)
    tbl.setStyle(TableStyle(estilo))
    story.append(tbl)

    def _rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(doc.pagesize[0] - 1.2 * cm, 0.8 * cm, f"Página {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_rodape, onLaterPages=_rodape)
    return buf.getvalue()


@app.route("/api/export/<view>.pdf")
def api_export_pdf(view):
    _, linhas = _export_data(view)
    pdf = _gerar_pdf(view, linhas)
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="estoque_{view}.pdf"'})


# ───────────────────────── orçamento / pedidos (Postgres) ─────────────────────────
@app.route("/api/orcamento")
def api_orcamento():
    """Orçamento de compras: meta = 65% da venda líq. 30d por comprador; realizado/aberto vêm
    do pedido REAL (Winthor). Pedidos manuais (nossa plataforma) entram à parte, pendentes de
    envio — não somam no realizado (evita dupla contagem quando voltarem do Winthor)."""
    mes = _mes_atual()
    comprador = request.args.get("comprador") or "TODOS"
    filiais = _filiais_estoque()
    hoje = _hoje()
    pct = float(request.args.get("pct") or 0.65)
    cab = _pedidos_data(filiais, hoje)["cab"]
    venda_comp = _venda_comprador_30d(filiais, _filiais_venda(), hoje)
    # meta sempre automática (65% da venda líquida 30d por comprador) — sem override manual
    res = core.orcamento_winthor(cab, venda_comp, _compradores_map(), _cadastro_fornecedores(),
                                 mes, comprador, pct=pct, hoje=hoje, meta_override=None)
    manuais = store.pedidos_pendentes(mes, comprador) if store.disponivel() else []
    return jsonify({"ok": True, "resumo": res["resumo"], "pedidos": res["pedidos"],
                    "abertos": res["abertos"], "por_comprador": res.get("por_comprador", []),
                    "manuais": manuais})


@app.route("/api/logistica")
def api_logistica():
    """Cubagem/ocupação por pedido em aberto (o que ainda vai chegar). Capacidade do veículo
    e limite de baixa ocupação são parâmetros (cap_m3, baixa_ate)."""
    filiais = _filiais_estoque()
    hoje = _hoje()
    pdata = _pedidos_data(filiais, hoje)
    cap = float(request.args.get("cap_m3") or 60.0)
    baixa = float(request.args.get("baixa_ate") or 0.1)
    res = core.logistica_pedidos(pdata["cab"], pdata["itens"], _cadastro_produtos(),
                                 _embalagem_map(), _compradores_map(), _cadastro_fornecedores(),
                                 hoje=hoje, capacidade_m3=cap, baixa_ate=baixa)
    return jsonify({"ok": True, **res})


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


def _gerar_pdf_pedido(pe, itens=None, forn=None):
    """Documento PDF de UM pedido de compra. Com itens → ordem de compra no estilo do
    relatório 211 do Winthor (logo + emitente + fornecedor + itens c/ IPI, retrato);
    sem itens → cabeçalho/valor (retrato). `forn` = cadastro do fornecedor (PCFORNEC)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.pdfgen import canvas as _rlcanvas
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    import math

    def _d(v):
        if not v:
            return "—"
        s = str(v)[:10].split("-")
        return f"{s[2]}/{s[1]}/{s[0]}" if len(s) == 3 else str(v)

    def _m(v):
        try:
            return ("R$ %s" % f"{float(v):,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")
        except (ValueError, TypeError):
            return "—"

    def _e(v):
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _i(v):
        try:
            return f"{int(round(float(v))):,}".replace(",", ".")
        except (ValueError, TypeError):
            return "—"

    def _sug(it):
        q = core._n(it.get("qtd")); cx = core._n(it.get("qtunitcx"))
        if cx > 1 and q > 0:
            return f"{int(math.ceil(q / cx))} cx · {int(q)} un"
        return f"{int(q)} un" if q else "—"

    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('t', parent=styles['Heading1'], fontSize=14, alignment=TA_LEFT, textColor=colors.HexColor('#0a0e17'))
    sub_style = ParagraphStyle('s', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#475569'))
    info_style = ParagraphStyle('i', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#0a0e17'), leading=14)
    buf = io.BytesIO()

    def _rodape(canvas, doc):
        canvas.saveState(); canvas.setFont('Helvetica', 7); canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(doc.pagesize[0] - 1.2 * cm, 0.8 * cm, f"Página {doc.page}")
        canvas.restoreState()

    cab = (f"Nº pedido: <b>{_e(pe.get('n_pedido') or '—')}</b> · Data: <b>{_d(pe.get('data_pedido'))}</b> · "
           f"Fornecedor: <b>{_e(pe.get('fornecedor') or '—')}</b> · Comprador: <b>{_e(pe.get('comprador') or '—')}</b><br/>"
           f"Prazo: <b>{(str(pe.get('prazo_dias'))+'d') if pe.get('prazo_dias') else '—'}</b> · "
           f"Vencimento: <b>{_d(pe.get('dt_vencimento'))}</b> · Status: <b>{_e(pe.get('status') or '—')}</b>"
           + (f" · Forma pgto: <b>{_e(pe.get('forma_pgto'))}</b>" if pe.get('forma_pgto') else ""))

    if itens:
        # ordenado por código p/ facilitar a digitação no sistema interno (retrato, economiza folha).
        itens = sorted(itens, key=lambda it: core._n(it.get("codprod")))
        azul = colors.HexColor('#0f2a5c')

        # rodapé com "Página X de Y" (2 passadas: guarda estados e conta o total no save)
        class _NumCanvas(_rlcanvas.Canvas):
            def __init__(self, *a, **k):
                super().__init__(*a, **k); self._saved = []
            def showPage(self):
                self._saved.append(dict(self.__dict__)); self._startPage()
            def save(self):
                n = len(self._saved)
                for st in self._saved:
                    self.__dict__.update(st)
                    self.setFont('Helvetica', 7); self.setFillColor(colors.HexColor('#94a3b8'))
                    self.drawString(1.2 * cm, 0.8 * cm, "Multpel · Estoque — documento interno de compra")
                    self.drawRightString(A4[0] - 1.2 * cm, 0.8 * cm, f"Página {self._pageNumber} de {n}")
                    _rlcanvas.Canvas.showPage(self)
                _rlcanvas.Canvas.save(self)

        tit_bloco = ParagraphStyle('tb', parent=styles['Normal'], fontSize=8, fontName='Helvetica-Bold', textColor=colors.white)
        corpo = ParagraphStyle('cb', parent=styles['Normal'], fontSize=7.6, textColor=colors.HexColor('#0a0e17'), leading=11.5)
        cel_desc = ParagraphStyle('cd', parent=styles['Normal'], fontSize=6.3, leading=7.3, textColor=colors.HexColor('#0a0e17'))

        def _bloco(titulo, corpo_html):
            t = Table([[Paragraph(titulo, tit_bloco)], [Paragraph(corpo_html, corpo)]], colWidths=[18.6 * cm])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, 0), azul),
                ('BOX', (0, 0), (-1, -1), 0.4, colors.HexColor('#94a3b8')),
                ('LINEBELOW', (0, 0), (0, 0), 0.4, colors.HexColor('#94a3b8')),
                ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            return t

        E, F = MULTPEL_EMPRESA, (forn or {})
        # cabeçalho: logo Multpel + título/nº do pedido
        head_dir = Paragraph(
            f"<b>Pedido de Compra</b><br/><font size=9>Nº <b>{_e(pe.get('n_pedido') or pe.get('id') or '—')}</b> · "
            f"Emissão <b>{_d(pe.get('data_pedido'))}</b></font><br/>"
            f"<font size=7 color='#64748b'>Gerado em {date.today().strftime('%d/%m/%Y %H:%M')}</font>", titulo_style)
        logo_path = os.path.join(os.path.dirname(__file__), "static", "logo-multpel-trofeu.png")
        try:
            head_row = Table([[Image(logo_path, width=2.3 * cm, height=2.3 * cm), head_dir]], colWidths=[2.7 * cm, 15.9 * cm])
        except Exception:
            head_row = Table([[head_dir]], colWidths=[18.6 * cm])
        head_row.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                      ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))

        emp_html = (f"<b>{_e(E['razao'])}</b> &nbsp;·&nbsp; CNPJ {_e(E['cnpj'])} &nbsp;·&nbsp; IE {_e(E['ie'])}<br/>"
                    f"{_e(E['endereco'])} · {_e(E['bairro'])} · CEP {_e(E['cep'])} · {_e(E['cidade'])}/{_e(E['uf'])}<br/>"
                    f"Tel {_e(E['tel'])} &nbsp;·&nbsp; {_e(E['email'])}")
        cidade_f = (f"{_e(F.get('CIDADE'))}/{_e(F.get('ESTADO'))}" if F.get('CIDADE') else '—')
        forn_html = (f"<b>{_i(pe.get('codfornec') or F.get('CODFORNEC'))} · {_e(pe.get('fornecedor') or F.get('FORNECEDOR') or '—')}</b><br/>"
                     f"CNPJ {_e(F.get('CGC') or '—')} &nbsp;·&nbsp; IE {_e(F.get('IE') or '—')}<br/>"
                     f"Nº {_e(F.get('NUMEROEND') or '—')} · Bairro {_e(F.get('BAIRRO') or '—')} · CEP {_e(F.get('CEP') or '—')} · {cidade_f}"
                     + (f" &nbsp;·&nbsp; {_e(F.get('EMAIL'))}" if F.get('EMAIL') else ""))
        ped_html = (f"Comprador: <b>{_e(pe.get('comprador') or '—')}</b> &nbsp;·&nbsp; "
                    f"Prazo pgto: <b>{(str(pe.get('prazo_dias'))+' dias') if pe.get('prazo_dias') else '—'}</b> &nbsp;·&nbsp; "
                    f"Vencimento: <b>{_d(pe.get('dt_vencimento'))}</b> &nbsp;·&nbsp; "
                    f"Forma pgto: <b>{_e(pe.get('forma_pgto') or '—')}</b> &nbsp;·&nbsp; "
                    f"Status: <b>{_e(pe.get('status') or '—')}</b>"
                    + (f"<br/>Obs: {_e(pe.get('obs'))}" if pe.get('obs') else ""))

        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                                topMargin=1.0 * cm, bottomMargin=1.4 * cm, title=f"Pedido {pe.get('n_pedido') or pe.get('id')}")
        story = [head_row, Spacer(1, 0.2 * cm),
                 _bloco("EMITENTE", emp_html), Spacer(1, 0.12 * cm),
                 _bloco("FORNECEDOR", forn_html), Spacer(1, 0.12 * cm),
                 _bloco("DADOS DO PEDIDO", ped_html), Spacer(1, 0.28 * cm)]

        header = ["Cód", "Descrição", "Embalagem", "Un", "Cód.Fab", "Qtde", "Custo un.", "IPI %", "Vlr. Total"]
        data = [header]
        total = 0.0
        for it in itens:
            total += core._n(it.get("valor"))
            cx = core._n(it.get("qtunitcx")); q = core._n(it.get("qtd"))
            if cx > 1 and q > 0:
                qtde, un = f"{int(math.ceil(q / cx))}", "CX"
            else:
                qtde, un = (f"{int(q)}" if q else "—"), "UN"
            ipi = core._n(it.get("percipi"))
            data.append([_i(it.get("codprod")), Paragraph(_e(str(it.get("descricao") or "")[:52]), cel_desc),
                         _e(it.get("embalagem") or "—"), un, _e(it.get("codfab") or "—"),
                         qtde, _m(it.get("custo_unit")),
                         (f"{ipi:.1f}".replace('.', ',') + "%" if ipi > 0 else "—"), _m(it.get("valor"))])
        data.append(["", "", "", "", "", "", "", "TOTAL", _m(total)])
        col_w = [1.3 * cm, 5.0 * cm, 2.4 * cm, 0.9 * cm, 1.8 * cm, 1.3 * cm, 2.0 * cm, 1.2 * cm, 2.7 * cm]
        tbl = Table(data, repeatRows=1, colWidths=col_w)
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), azul),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 6.5),
            ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f4f7fb')]),
            ('ALIGN', (5, 0), (-1, -1), 'RIGHT'),
            ('ALIGN', (3, 0), (3, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e2e8f0')),
            ('SPAN', (0, -1), (6, -1)),
        ]))
        story.append(tbl)
        doc.build(story, canvasmaker=_NumCanvas)
    else:
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
                                topMargin=1.4 * cm, bottomMargin=1.5 * cm, title=f"Pedido {pe.get('n_pedido') or pe.get('id')}")
        lbl = ParagraphStyle('l', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#475569'))
        val = ParagraphStyle('v', parent=styles['Normal'], fontSize=11, textColor=colors.HexColor('#0a0e17'))
        story = [Paragraph('<b>Multpel · Estoque</b> — Pedido de Compra', titulo_style),
                 Paragraph(f"Gerado em {date.today().strftime('%d/%m/%Y')}", sub_style), Spacer(1, 0.5 * cm)]
        linhas = [("Nº do pedido", pe.get('n_pedido') or '—'), ("Data do pedido", _d(pe.get('data_pedido'))),
                  ("Fornecedor", pe.get('fornecedor') or '—'), ("Comprador", pe.get('comprador') or '—'),
                  ("Valor", _m(pe.get('valor'))), ("Prazo de pagamento", f"{pe.get('prazo_dias')} dias" if pe.get('prazo_dias') else '—'),
                  ("Vencimento", _d(pe.get('dt_vencimento'))), ("Forma de pagamento", pe.get('forma_pgto') or '—'),
                  ("Status", pe.get('status') or '—'), ("Observações", pe.get('obs') or '—')]
        data = [[Paragraph(k, lbl), Paragraph(_e(v), val)] for k, v in linhas]
        tbl = Table(data, colWidths=[5 * cm, 11.8 * cm])
        tbl.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(tbl)
        doc.build(story)
    return buf.getvalue()


@app.route("/api/pedidos/<int:pid>.pdf")
def api_pedido_pdf(pid):
    if not store.disponivel():
        return jsonify({"ok": False, "error": "Postgres indisponível"}), 503
    pe = store.pedido_get(pid)
    if not pe:
        return jsonify({"ok": False, "error": "Pedido não encontrado"}), 404
    itens = store.pedido_itens(pid)
    # enriquece cada item com cód. de fábrica, % IPI e embalagem (do cadastro) p/ o PDF estilo 211
    if itens:
        cad = _cadastro_produtos()
        emb_map = _embalagem_map()
        for it in itens:
            cod = int(core._n(it.get("codprod")))
            c = cad.get(cod) or {}
            it["codfab"] = c.get("CODFAB")
            it["percipi"] = c.get("PERCIPI")
            # embalagem = a da CAIXA (PCEMBALAGEM, igual à tela do Abastecimento); fallback no cadastro
            it["embalagem"] = (emb_map.get(cod) or {}).get("embalagem") or c.get("EMBALAGEM")
    # dados do fornecedor (PCFORNEC) p/ o bloco do cabeçalho
    forn = None
    if pe.get("codfornec") not in (None, ""):
        forn = _cadastro_fornecedores().get(int(core._n(pe.get("codfornec"))))
    # arquivo com o nome do fornecedor (sanitizado); fallback no nº do pedido
    base = re.sub(r'[^A-Za-z0-9 ._-]', '', str(pe.get("fornecedor") or "").strip()) or f"pedido_{pe.get('n_pedido') or pid}"
    nome = f"{base}.pdf"
    return Response(_gerar_pdf_pedido(pe, itens, forn=forn), mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


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
