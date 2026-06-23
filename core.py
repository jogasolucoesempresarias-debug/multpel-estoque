"""
Motor de cálculo do painel de estoque — metodologia OFICIAL (query do TI).

Giro = média de 3 meses (QTVENDMES1..3); QTDISP = estoque endereçado (default) ou
gerencial; custo = CUSTOFIN. Produz lista de produtos enriquecida + cockpit +
ranking de fornecedores + FEFO de validade.

Técnicas: Days of Supply, ABC (Pareto), XYZ (variabilidade), matriz ABC-XYZ,
ponto de reposição (ROP) com lead time por fornecedor, ruptura, dead stock, FEFO.
"""

import math
import statistics
from datetime import datetime, date, timedelta


# ───────────────────────── parâmetros (configuráveis) ─────────────────────────
DEFAULTS = {
    "giro_base":        "media3",  # media3 (oficial) | m1 (último mês)
    "base_estoque":     "endereco",  # endereco (WMS, oficial) | gerencial (QTESTGER)
    "lead_time":        10,        # dias (fallback quando o fornecedor não tem prazo)
    "dias_seguranca":   25,        # dias de estoque de segurança
    "cobertura_total":  45,        # dias-alvo de cobertura
    "ruptura_dias":     30,        # cobertura <= isso = ruptura
    "horizonte_val":    30,        # janela de risco de vencimento
    "parado_atencao":   60,        # dias sem venda
    "parado_critico":   90,
    "parado_mcritico":  120,
    "excesso_cob":      120,       # cobertura acima disso = excesso
    "abc_a":            80.0,      # % acumulado
    "abc_b":            95.0,
    "xyz_x":            0.5,       # coeficiente de variação
    "xyz_y":            1.0,
    "forecast":         0,         # 1 = giro vem do forecast (RCA mensal); 0 = média3 oficial
    "forecast_meses":   6,         # janela da média móvel simples do forecast bruto
    "forecast_sazonal": 0,         # 1 = aplica fator sazonal ano-a-ano (implica forecast on)
    "arredonda_cx":     1,         # 1 = arredonda sugestão/pedido p/ caixa fechada (QTUNITCX)
}
_STR_PARAMS = {"giro_base", "base_estoque"}


def merge_params(q):
    """Mescla querystring (dict) sobre os defaults, com cast numérico."""
    p = dict(DEFAULTS)
    for k, v in (q or {}).items():
        if k not in p or v in (None, ""):
            continue
        if k in _STR_PARAMS:
            p[k] = str(v)
        else:
            try:
                p[k] = float(v) if "." in str(v) else int(v)
            except (TypeError, ValueError):
                pass
    return p


# ───────────────────────── helpers ─────────────────────────
def _n(x):
    if x in (None, ""):
        return 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "")).date()
    except ValueError:
        return None


def _giro_mensal(row, base):
    g1, g2, g3 = _n(row.get("giro_m1")), _n(row.get("giro_m2")), _n(row.get("giro_m3"))
    if base == "m1":
        return g1
    return round((g1 + g2 + g3) / 3)  # media3 — oficial do TI


def _meses_anteriores(hoje, n):
    """Lista dos N AnoMes (YYYYMM) imediatamente anteriores ao mês de `hoje` (mais recente 1º)."""
    out, ano, mes = [], hoje.year, hoje.month
    for _ in range(n):
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1
        out.append(ano * 100 + mes)
    return out


def previsao_giro_mensal(serie_am, meses, hoje):
    """Forecast bruto: média móvel SIMPLES da QT vendida nos N meses fechados anteriores.
    serie_am: {AnoMes: qtd}. Retorna giro mensal previsto (qtd/mês) ou None se sem histórico."""
    if not serie_am:
        return None
    chaves = _meses_anteriores(hoje, int(meses))
    total = sum(_n(serie_am.get(am)) for am in chaves)
    return round(total / len(chaves)) if chaves else None


def fatores_sazonais(serie_am, hoje, janela=24, min_meses=12):
    """Índices sazonais ano-a-ano a partir da venda mensal (RCA).
    media_mensal = média dos últimos `janela` meses (naturalmente dessazonalizada);
    fator[m] = média do mês-calendário m ÷ media_mensal, clampado a [0.3, 3.0].
    Retorna {"media_mensal", "fatores": {1..12}} ou None se histórico < min_meses."""
    if not serie_am:
        return None
    chaves = _meses_anteriores(hoje, int(janela))
    com_dado = [am for am in chaves if am in serie_am]
    if len(com_dado) < int(min_meses):
        return None
    media_mensal = sum(_n(serie_am.get(am)) for am in chaves) / len(chaves)
    if media_mensal <= 0:
        return None
    fatores = {}
    for m in range(1, 13):
        obs = [_n(serie_am.get(am)) for am in chaves if am % 100 == m and am in serie_am]
        fatores[m] = max(0.3, min(3.0, (sum(obs) / len(obs)) / media_mensal)) if obs else 1.0
    return {"media_mensal": media_mensal, "fatores": fatores}


def previsao_giro_sazonal(saz, mes):
    """Giro mensal previsto p/ um mês-calendário: nível dessazonalizado × fator do mês."""
    return round(saz["media_mensal"] * saz["fatores"].get(mes, 1.0))


def arredonda_caixa(qt, qtunitcx):
    """Arredonda `qt` PRA CIMA em caixas fechadas. Retorna (qt_arredondado, n_caixas).
    No-op (qt, None) se qtunitcx<=1 ou qt<=0."""
    if not qtunitcx or qtunitcx <= 1 or qt <= 0:
        return qt, None
    cx = math.ceil(qt / qtunitcx)
    return cx * qtunitcx, cx


def _round(v, n=2):
    return round(v, n) if isinstance(v, (int, float)) else v


# ───────────────────────── produtos ─────────────────────────
def construir_produtos(snapshot, end_map, prod_map, forn_map, comprador_map, venda_map, params,
                       hoje=None, venda_mensal_map=None):
    """snapshot: linhas do PCEST; end_map: {cod: qt_end}; prod_map/forn_map: cadastro;
    comprador_map: {matricula: nome}; venda_map: {cod:{venda,custo,qtd}} líquido do RCA.
    venda_mensal_map: {cod:{AnoMes:qtd}} p/ forecast (opcional; só quando forecast ligado).
    Mantém só produtos do cadastro (revenda/não-FL)."""
    hoje = hoje or date.today()
    base = params["base_estoque"]
    forecast_on = bool(params.get("forecast"))
    sazonal_on = bool(params.get("forecast_sazonal")) and forecast_on
    fc_meses = int(params.get("forecast_meses", 6))
    arred_cx = bool(params.get("arredonda_cx"))
    out = []
    for r in snapshot:
        cod = int(_n(r.get("CODPROD")))
        cad = prod_map.get(cod)
        if cad is None:
            continue  # fora do universo (não-revenda / FL)

        qtestger   = _n(r.get("qtestger"))
        qtreserv   = _n(r.get("qtreserv"))
        qtbloq     = _n(r.get("qtbloq"))
        qtpend     = _n(r.get("qtpend"))
        qttransito = _n(r.get("qttransito"))
        custofin   = _n(r.get("custofin"))
        qt_end     = _n(end_map.get(cod))

        # QTDISP conforme a base escolhida
        if base == "gerencial":
            qtdisp = qtestger - qtreserv - qtbloq
        else:  # endereco (oficial)
            qtdisp = qt_end
        valor = qtdisp * custofin

        giro_media3 = _giro_mensal(r, params["giro_base"])
        serie_am = (venda_mensal_map or {}).get(cod)
        giro_forecast = previsao_giro_mensal(serie_am, fc_meses, hoje) if forecast_on else None
        saz = fatores_sazonais(serie_am, hoje) if sazonal_on else None
        nivel_base_dia = None
        if saz is not None:
            giro_mes, giro_fonte = previsao_giro_sazonal(saz, hoje.month), "sazonal"
            nivel_base_dia = saz["media_mensal"] / 30.0
        elif forecast_on and giro_forecast is not None:
            giro_mes, giro_fonte = giro_forecast, "forecast"
        else:
            giro_mes, giro_fonte = giro_media3, "media3"
        giro_dia = giro_mes / 30.0
        serie = [_n(r.get("giro_m1")), _n(r.get("giro_m2")), _n(r.get("giro_m3"))]
        # série mensal (até 12 últimos meses, ordem cronológica) p/ sparkline do 360°
        serie_mensal = ([_round(_n(serie_am.get(am))) for am in reversed(_meses_anteriores(hoje, 12))]
                        if serie_am else None)

        cobertura = (qtdisp / giro_dia) if giro_dia > 0 and qtdisp > 0 else None

        dt_saida = _parse_dt(r.get("dtultsaida"))
        dias_sem_venda = (hoje - dt_saida).days if dt_saida else None

        # fornecedor / comprador
        fornec_cod = cad.get("CODFORNEC")
        forn = forn_map.get(int(_n(fornec_cod))) if fornec_cod not in (None, "") else None
        codcomprador = int(_n((forn or {}).get("CODCOMPRADOR"))) if forn and (forn.get("CODCOMPRADOR") not in (None, "")) else None
        comprador = comprador_map.get(codcomprador) if codcomprador is not None else None

        # reposição / ROP — lead time real do fornecedor quando houver
        prazo_forn = _n((forn or {}).get("PRAZOENTREGA"))
        lead = prazo_forn if prazo_forn > 0 else params["lead_time"]
        est_seg = giro_dia * params["dias_seguranca"]
        rop = giro_dia * lead + est_seg
        est_alvo = giro_dia * params["cobertura_total"]
        # posição efetiva = disponível + o que já está a caminho (evita comprar de novo o já pedido)
        posicao = qtdisp + qttransito + qtpend
        sugestao = max(0.0, est_alvo - posicao)

        if giro_dia <= 0:
            status_abast = "sem_giro" if qtdisp > 0 else "ok"
        elif qtdisp <= 0:
            status_abast = "urgente"
        elif cobertura <= lead:
            status_abast = "urgente"
        elif cobertura <= lead + params["dias_seguranca"]:
            status_abast = "alta"
        elif cobertura <= params["cobertura_total"]:
            status_abast = "atencao"
        elif cobertura > params["excesso_cob"]:
            status_abast = "excesso"
        else:
            status_abast = "ok"

        # ruptura (cobertura <= ruptura_dias), faixas 0-15 / 16-30
        estoque_zero = qtdisp <= 0
        if giro_dia <= 0:
            status_ruptura = None
        else:
            cob_eff = cobertura if (cobertura is not None) else 0.0
            if cob_eff <= 15:
                status_ruptura = "0-15"
            elif cob_eff <= params["ruptura_dias"]:
                status_ruptura = "16-30"
            else:
                status_ruptura = None

        # estoque parado / dead stock — por dias sem venda.
        # Campo único "parado_atencao" (X) define o corte; níveis ancorados em X, X+30, X+60.
        sem_giro = giro_dia <= 0 and qtdisp > 0
        pa = params["parado_atencao"]
        if qtdisp <= 0:
            status_parado = None
        elif dias_sem_venda is None:
            status_parado = "muito_critico"
        elif dias_sem_venda >= pa + 60:
            status_parado = "muito_critico"
        elif dias_sem_venda >= pa + 30:
            status_parado = "critico"
        elif dias_sem_venda >= pa:
            status_parado = "atencao"
        else:
            status_parado = None

        if dt_saida is None:
            status_saida = "sem_saida"
        elif dias_sem_venda <= 30:
            status_saida = "recente"
        elif dias_sem_venda <= 90:
            status_saida = "media"
        else:
            status_saida = "antiga"

        # compra suspensa: tem giro (média 3m, defasada) mas parou de vender há tempo
        # → não sugerir comprar estoque morto (giro está "preso" no histórico)
        compra_suspensa = (giro_dia > 0 and dias_sem_venda is not None
                           and dias_sem_venda >= params["parado_atencao"])

        # venda real (RCA, líquida) do período
        vd = venda_map.get(cod) or {}
        venda = vd.get("venda", 0.0)
        custo_vendido = vd.get("custo", 0.0)
        qtd_vendida = vd.get("qtd", 0.0)
        lucro = venda - custo_vendido
        margem = (lucro / venda) if venda else None

        # XYZ — coeficiente de variação da série de 3 meses
        media = statistics.mean(serie) if serie else 0.0
        if media > 0:
            cv = statistics.pstdev(serie) / media
            xyz = "X" if cv < params["xyz_x"] else ("Y" if cv < params["xyz_y"] else "Z")
        else:
            cv, xyz = None, None

        qtunitcx = _n(cad.get("QTUNITCX"))
        # arredondamento da sugestão p/ caixa fechada (QTUNITCX), quando ligado
        sugestao_bruta = sugestao
        sugestao_un, sugestao_cx = (arredonda_caixa(sugestao, qtunitcx) if arred_cx else (sugestao, None))
        sugestao = sugestao_un
        out.append({
            "codprod": cod,
            "descricao": cad.get("DESCRICAO") or f"PRODUTO {cod}",
            "codfornec": int(_n(fornec_cod)) if fornec_cod not in (None, "") else None,
            "fornecedor": (forn or {}).get("FORNECEDOR") if forn else None,
            "codcomprador": codcomprador,
            "comprador": comprador,
            "codepto": cad.get("CODEPTO"),
            "ncm": cad.get("NCM"), "marca": cad.get("MARCA"),
            "embalagem": cad.get("EMBALAGEM"),
            "qtunitcx": qtunitcx or None,
            "qtdisp": _round(qtdisp), "disponivel": _round(qtdisp),
            "qtestger": _round(qtestger), "qt_end": _round(qt_end),
            "qtreserv": _round(qtreserv), "qtbloq": _round(qtbloq),
            "qttransito": _round(qttransito), "qtpend": _round(qtpend),
            "custo_unit": _round(custofin, 4),
            "valor": _round(valor),
            "giro_mes": _round(giro_mes), "giro_dia": _round(giro_dia, 3),
            "giro_media3": _round(giro_media3), "giro_forecast": _round(giro_forecast) if giro_forecast is not None else None,
            "giro_fonte": giro_fonte, "serie_mensal": serie_mensal,
            "nivel_base_dia": _round(nivel_base_dia, 3) if nivel_base_dia is not None else None,
            "fatores_sazonais": saz["fatores"] if saz else None,
            "giro_cx": _round(giro_mes / qtunitcx, 2) if qtunitcx else None,
            "venda": _round(venda), "lucro": _round(lucro), "qtd_vendida": _round(qtd_vendida),
            "margem": _round(margem * 100, 1) if margem is not None else None,
            "serie_giro": [_round(x) for x in serie],
            "cobertura": _round(cobertura, 1) if cobertura is not None else None,
            "dias_sem_venda": dias_sem_venda,
            "dtultsaida": dt_saida.isoformat() if dt_saida else None,
            "cv": _round(cv, 3) if cv is not None else None,
            "xyz": xyz,
            "lead_efetivo": _round(lead),
            "rop": _round(rop), "est_seguranca": _round(est_seg),
            "est_alvo": _round(est_alvo), "sugestao_compra": _round(sugestao),
            "sugestao_bruta": _round(sugestao_bruta), "sugestao_cx": sugestao_cx,
            "compra_suspensa": compra_suspensa,
            "status_abast": status_abast,
            "status_ruptura": status_ruptura, "estoque_zero": estoque_zero,
            "status_parado": status_parado,
            "status_saida": status_saida,
            "sem_giro": sem_giro,
            "curva_abc": None, "curva_giro": None, "abc_xyz": None,
        })

    _aplicar_curva(out, "valor", "curva_abc", params["abc_a"], params["abc_b"])
    _aplicar_curva(out, "giro_mes", "curva_giro", params["abc_a"], params["abc_b"])
    for p in out:
        if p["curva_abc"] and p["xyz"]:
            p["abc_xyz"] = p["curva_abc"] + p["xyz"]
    return out


def _aplicar_curva(produtos, chave_valor, campo, a, b):
    """Classifica curva ABC por Pareto (% acumulado) sobre `chave_valor`."""
    total = sum(p[chave_valor] or 0 for p in produtos)
    if total <= 0:
        for p in produtos:
            p[campo] = "C"
        return
    acum = 0.0
    for p in sorted(produtos, key=lambda x: x[chave_valor] or 0, reverse=True):
        acum += (p[chave_valor] or 0)
        pct = acum / total * 100
        p[campo] = "A" if pct <= a else ("B" if pct <= b else "C")


# ───────────────────────── cockpit ─────────────────────────
FAIXAS_COB = [
    ("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90),
    ("91-120", 91, 120), ("121+", 121, float("inf")),
]


def cockpit(produtos):
    valor_total = sum(p["valor"] or 0 for p in produtos)
    venda_total = sum(p["venda"] or 0 for p in produtos)
    lucro_total = sum(p["lucro"] or 0 for p in produtos)
    com_estoque = [p for p in produtos if (p["qtdisp"] or 0) > 0]
    com_giro = [p for p in produtos if (p["giro_dia"] or 0) > 0]
    sem_giro = [p for p in com_estoque if (p["giro_dia"] or 0) <= 0]

    valor_parado = sum(p["valor"] or 0 for p in produtos if p["status_parado"])
    valor_sem_giro = sum(p["valor"] or 0 for p in sem_giro)

    faixas = []
    for nome, lo, hi in FAIXAS_COB:
        itens = [p for p in com_giro if p["cobertura"] is not None and lo <= p["cobertura"] <= hi]
        faixas.append({"faixa": nome, "qt": len(itens),
                       "valor": _round(sum(p["valor"] or 0 for p in itens))})
    faixas.append({"faixa": "sem giro", "qt": len(sem_giro), "valor": _round(valor_sem_giro)})

    abc = {}
    for c in ("A", "B", "C"):
        itens = [p for p in produtos if p["curva_abc"] == c]
        abc[c] = {"qt": len(itens), "valor": _round(sum(p["valor"] or 0 for p in itens))}

    matriz = {}
    for p in produtos:
        if p["abc_xyz"]:
            cell = matriz.setdefault(p["abc_xyz"], {"qt": 0, "valor": 0.0})
            cell["qt"] += 1
            cell["valor"] += (p["valor"] or 0)
    for v in matriz.values():
        v["valor"] = _round(v["valor"])

    def _cont(field, val):
        itens = [p for p in produtos if p[field] == val]
        return {"qt": len(itens), "valor": _round(sum(p["valor"] or 0 for p in itens))}

    repor = [p for p in produtos if (p["sugestao_compra"] or 0) > 0
             and (p["giro_dia"] or 0) > 0 and not p.get("compra_suspensa")]
    suspensos = [p for p in produtos if p.get("compra_suspensa")]
    em_ruptura = [p for p in produtos if p["status_ruptura"]]

    return {
        "valor_total": _round(valor_total),
        "venda_total": _round(venda_total),
        "lucro_total": _round(lucro_total),
        "margem_total": _round(lucro_total / venda_total * 100, 1) if venda_total else None,
        "n_total": len(produtos),
        "n_com_estoque": len(com_estoque),
        "n_com_giro": len(com_giro),
        "n_sem_giro": len(sem_giro),
        "valor_parado": _round(valor_parado),
        "pct_capital_parado": _round(valor_parado / valor_total * 100, 1) if valor_total else 0,
        "valor_sem_giro": _round(valor_sem_giro),
        "faixas_cobertura": faixas,
        "abc": abc,
        "matriz_abc_xyz": matriz,
        "parado": {
            "atencao": _cont("status_parado", "atencao"),
            "critico": _cont("status_parado", "critico"),
            "muito_critico": _cont("status_parado", "muito_critico"),
            "sem_giro": {"qt": len(sem_giro), "valor": _round(valor_sem_giro)},
        },
        "ruptura": {
            "f0_15": _cont("status_ruptura", "0-15"),
            "f16_30": _cont("status_ruptura", "16-30"),
            "estoque_zero": sum(1 for p in produtos if p["estoque_zero"] and (p["giro_dia"] or 0) > 0),
            "total": len(em_ruptura),
            "valor": _round(sum(p["valor"] or 0 for p in em_ruptura)),
        },
        "abastecimento": {
            "urgente": _cont("status_abast", "urgente"),
            "alta": _cont("status_abast", "alta"),
            "atencao": _cont("status_abast", "atencao"),
            "excesso": _cont("status_abast", "excesso"),
            "n_repor": len(repor),
            "qt_sugerida": _round(sum(p["sugestao_compra"] or 0 for p in repor)),
            "valor_sugerido": _round(sum((p["sugestao_compra"] or 0) * (p["custo_unit"] or 0) for p in repor)),
            "n_suspensos": len(suspensos),
            "valor_suspenso": _round(sum((p["sugestao_compra"] or 0) * (p["custo_unit"] or 0) for p in suspensos)),
        },
        "valor_risco_venc": None,  # preenchido pelo app a partir do FEFO
    }


# ───────────────────────── fornecedores ─────────────────────────
def fornecedores(produtos, params=None):
    total_valor = sum(p["valor"] or 0 for p in produtos) or 1
    total_giro = sum(p["giro_mes"] or 0 for p in produtos) or 1
    grupos = {}
    for p in produtos:
        cf = p["codfornec"]
        if cf is None:
            continue
        g = grupos.setdefault(cf, {
            "codfornec": cf, "fornecedor": p["fornecedor"] or f"FORN {cf}",
            "comprador": p.get("comprador"),
            "n_produtos": 0, "valor": 0.0, "giro": 0.0, "venda": 0.0, "lucro": 0.0,
            "disponivel": 0.0, "giro_dia": 0.0, "n_sem_giro": 0,
        })
        g["n_produtos"] += 1
        g["valor"] += (p["valor"] or 0)
        g["giro"] += (p["giro_mes"] or 0)
        g["venda"] += (p["venda"] or 0)
        g["lucro"] += (p["lucro"] or 0)
        g["disponivel"] += (p["qtdisp"] or 0)
        g["giro_dia"] += (p["giro_dia"] or 0)
        if (p["giro_dia"] or 0) <= 0 and (p["qtdisp"] or 0) > 0:
            g["n_sem_giro"] += 1

    lead = params["lead_time"] if params else DEFAULTS["lead_time"]
    saida = []
    for g in grupos.values():
        perc_giro = g["giro"] / total_giro * 100
        perc_est = g["valor"] / total_valor * 100
        indice = (perc_giro / perc_est) if perc_est > 0 else (999.0 if perc_giro > 0 else 0.0)
        # cobertura média do fornecedor (dias) — distingue eficiência real de desabastecimento
        cobertura = (g["disponivel"] / g["giro_dia"]) if g["giro_dia"] > 0 else None
        if g["giro"] <= 0:
            classif = "critico_sem_giro"
        elif cobertura is not None and cobertura < lead:
            classif = "ruptura"            # gira mas quase sem estoque (não é performance)
        elif indice >= 1.2:
            classif = "alta_performance"
        elif indice >= 0.8:
            classif = "equilibrado"
        else:
            classif = "estoque_alto"
        saida.append({
            **g,
            "valor": _round(g["valor"]), "giro": _round(g["giro"]),
            "venda": _round(g["venda"]), "lucro": _round(g["lucro"]),
            "margem": _round(g["lucro"] / g["venda"] * 100, 1) if g["venda"] else None,
            "cobertura": _round(cobertura, 1) if cobertura is not None else None,
            "perc_giro": _round(perc_giro, 2), "perc_estoque": _round(perc_est, 2),
            "indice": _round(indice, 2), "classificacao": classif,
        })
    saida.sort(key=lambda x: x["valor"], reverse=True)
    return saida


# ───────────────────────── compras × vendas por comprador ─────────────────────────
def por_comprador(produtos):
    """Agrega compras (estoque/custo) × vendas (faturamento) por comprador."""
    grupos = {}
    for p in produtos:
        cc = p.get("codcomprador")
        chave = cc if cc is not None else 0
        g = grupos.setdefault(chave, {
            "codcomprador": cc, "comprador": p.get("comprador") or "Sem comprador",
            "n_produtos": 0, "estoque": 0.0, "venda": 0.0, "lucro": 0.0,
            "n_ruptura": 0, "valor_parado": 0.0, "sugestao_valor": 0.0,
        })
        g["n_produtos"] += 1
        g["estoque"] += (p["valor"] or 0)
        g["venda"] += (p["venda"] or 0)
        g["lucro"] += (p["lucro"] or 0)
        if p["status_ruptura"]:
            g["n_ruptura"] += 1
        if p["status_parado"]:
            g["valor_parado"] += (p["valor"] or 0)
        if (p["sugestao_compra"] or 0) > 0 and (p["giro_dia"] or 0) > 0 and not p.get("compra_suspensa"):
            g["sugestao_valor"] += (p["sugestao_compra"] or 0) * (p["custo_unit"] or 0)
    saida = []
    for g in grupos.values():
        saida.append({
            **g,
            "estoque": _round(g["estoque"]), "venda": _round(g["venda"]), "lucro": _round(g["lucro"]),
            "margem": _round(g["lucro"] / g["venda"] * 100, 1) if g["venda"] else None,
            "giro_estoque": _round(g["venda"] / g["estoque"], 2) if g["estoque"] else None,  # venda/estoque (turn)
            "valor_parado": _round(g["valor_parado"]), "sugestao_valor": _round(g["sugestao_valor"]),
        })
    saida.sort(key=lambda x: x["venda"], reverse=True)
    return saida


# ───────────────────────── validade / FEFO ─────────────────────────
def validade_fefo(lotes, produtos_idx, params, hoje=None):
    hoje = hoje or date.today()
    out = []
    for r in lotes:
        cod = int(_n(r.get("CODPROD")))
        dtval = _parse_dt(r.get("DTVAL"))
        if not dtval:
            continue
        qt = _n(r.get("qt"))
        p = produtos_idx.get(cod, {})
        giro_dia = p.get("giro_dia") or 0
        custo_unit = p.get("custo_unit") or 0

        dias = (dtval - hoje).days
        consumo_proj = giro_dia * max(dias, 0)
        saldo_proj = qt - consumo_proj
        valor_risco = max(0.0, saldo_proj) * custo_unit

        if dias <= 7:
            classif = "critico"
        elif dias <= 15:
            classif = "atencao"
        else:
            classif = "planejar"
        if giro_dia <= 0:
            risco = "giro_zero"
        elif saldo_proj > 0:
            risco = "alto" if dias <= 15 else "medio"
        else:
            risco = "baixo"

        out.append({
            "codprod": cod,
            "descricao": p.get("descricao") or f"PRODUTO {cod}",
            "fornecedor": p.get("fornecedor"),
            "comprador": p.get("comprador"),
            "numlote": r.get("NUMLOTE") or "—",
            "dtval": dtval.isoformat(),
            "dias_para_vencer": dias,
            "qt": _round(qt),
            "giro_dia": _round(giro_dia, 3),
            "consumo_proj": _round(consumo_proj),
            "saldo_proj": _round(saldo_proj),
            "custo_unit": _round(custo_unit, 4),
            "valor_risco": _round(valor_risco),
            "classificacao": classif,
            "risco": risco,
        })
    out.sort(key=lambda x: x["dias_para_vencer"])
    return out


# ───────────────────────── plano de reposição (time-phased / DRP) ─────────────────────────
def plano_reposicao(p, params, hoje=None, semanas=12):
    """Grade DRP semanal de um produto: projeta o saldo semana a semana, gera pedidos
    planejados quando cruza o estoque de segurança e calcula QUANDO o pedido precisa SAIR
    (liberação = recebimento − lead time).

    Ressalva: sem dados de trânsito no BI (QTTRANSITO=0) → inbound só pelo pendente (raro).
    Demanda herda o giro escolhido (média3/forecast); no modo sazonal varia por mês na grade."""
    hoje = hoje or date.today()
    giro_dia = p.get("giro_dia") or 0
    seg = p.get("est_seguranca") or 0
    alvo = p.get("est_alvo") or 0
    custo = p.get("custo_unit") or 0
    lead = p.get("lead_efetivo") or params.get("lead_time", 10)
    lead_sem = max(1, math.ceil(lead / 7.0))
    receb_prog_total = (p.get("qttransito") or 0) + (p.get("qtpend") or 0)
    # sazonalidade: demanda da semana varia pelo mês quando há fatores; senão constante
    nivel_base_dia = p.get("nivel_base_dia")
    fatores = p.get("fatores_sazonais")
    # caixa fechada: arredonda o pedido planejado p/ múltiplo de QTUNITCX
    arred = bool(params.get("arredonda_cx")) and (p.get("qtunitcx") or 0) > 1
    qtcx = p.get("qtunitcx") or 0

    if giro_dia <= 0:
        return {"semanas": [], "liberacoes": [], "inbound_zero": receb_prog_total <= 0,
                "lead_semanas": lead_sem, "sem_giro": True}

    def _dem_sem(data_ini):
        if nivel_base_dia and fatores:
            return nivel_base_dia * (fatores.get(data_ini.month) or fatores.get(str(data_ini.month)) or 1.0) * 7.0
        return giro_dia * 7.0

    saldo = p.get("qtdisp") or 0
    grade, liberacoes = [], []
    for s in range(1, semanas + 1):
        data_ini = hoje + timedelta(days=(s - 1) * 7)
        dem_sem = _dem_sem(data_ini)
        receb_prog = receb_prog_total if s == lead_sem else 0.0
        saldo = saldo - dem_sem + receb_prog
        receb_plan = 0.0
        if saldo < seg:
            receb_plan = max(0.0, round(alvo - saldo))
            n_cx = None
            if arred and receb_plan > 0:
                receb_plan, n_cx = arredonda_caixa(receb_plan, qtcx)
            saldo += receb_plan
            sem_lib = max(0, s - lead_sem)
            liberacoes.append({
                "semana": sem_lib,
                "data": (hoje + timedelta(days=sem_lib * 7)).isoformat(),
                "qt": _round(receb_plan),
                "qt_cx": n_cx,
                "valor": _round(receb_plan * custo),
            })
        grade.append({
            "semana": s,
            "data_ini": data_ini.isoformat(),
            "demanda": _round(dem_sem),
            "receb_prog": _round(receb_prog),
            "receb_plan": _round(receb_plan),
            "saldo_proj": _round(saldo),
            "abaixo_seg": saldo < seg,
        })
    return {
        "semanas": grade,
        "liberacoes": liberacoes,
        "estoque_seguranca": _round(seg),
        "estoque_alvo": _round(alvo),
        "lead_semanas": lead_sem,
        "inbound_zero": receb_prog_total <= 0,
        "sem_giro": False,
    }
