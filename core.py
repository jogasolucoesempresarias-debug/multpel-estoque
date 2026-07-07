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
    "base_estoque":     "gerencial",  # gerencial (QTESTGER cru, oficial v3) | endereco (WMS)
    "lead_time":        10,        # dias (fallback quando o fornecedor não tem prazo)
    "dias_seguranca":   25,        # dias de estoque de segurança
    "cobertura_total":  45,        # dias-alvo de cobertura p/ COMPRA (N2 da planilha = 45d)
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


# cobertura em dias (regra OFICIAL da planilha: ROUNDUP(QTDISP/(GIROMESUNID/30));
# giro<=0 -> 9999 não calculável; estoque<=0 com giro -> 0). Faixas fixas (independem de
# parâmetro) — espelham GRAFICO COBERTURA ESTOQUE / resumo_cobertura.
_FAIXAS_COB_LIM = [("0-30", 30), ("31-60", 60), ("61-90", 90), ("91-120", 120), ("121+", 10**9)]


def cobertura_dias_oficial(qtdisp, giro_dia):
    if giro_dia <= 0:
        return 9999
    if qtdisp <= 0:
        return 0
    return math.ceil(qtdisp / giro_dia)


def cobertura_faixa_de(cob_dias):
    for nome, hi in _FAIXAS_COB_LIM:
        if cob_dias <= hi:
            return nome
    return "121+"


# faixa de "dias parado" (dias sem venda) p/ o relatório de Estoque Parado — indicador.
# Partição inteira ≥ início (sem gap/overlap); nunca-vendeu (None) = pior (121+); <15 ou sem
# estoque = fora do parado (None).
def parado_faixa_de(dias_sem_venda, qtdisp):
    if qtdisp <= 0:
        return None
    d = dias_sem_venda if dias_sem_venda is not None else 10**9
    if d < 15:
        return None
    if d <= 30:
        return "15-30"
    if d <= 60:
        return "31-60"
    if d <= 90:
        return "61-90"
    if d <= 120:
        return "91-120"
    return "121+"


# ───────────────────────── pedido de compra real (Winthor) ─────────────────────────
def montar_ja_pedida(cab_rows, item_rows, hoje=None, dias=180):
    """Pedido de compra REAL em ABERTO por produto, a partir do Winthor (PCPEDIDO×PCITEM).
    Ativo = emitido nos últimos `dias` (DTEMISSAO) — regra v3 da planilha (validada).
    Aberto = max(0, QTPEDIDA − QTENTREGUE): o gerencial já reflete o recebido, então só o
    aberto entra na projeção (não duplica estoque). Retorna {cod: qt_aberta}."""
    hoje = hoje or date.today()
    corte = hoje - timedelta(days=int(dias))
    ativos = {int(_n(r.get("NUMPED"))) for r in cab_rows
              if (_parse_dt(r.get("DTEMISSAO")) or date.min) >= corte}
    out = {}
    for r in item_rows:
        if int(_n(r.get("NUMPED"))) not in ativos:
            continue
        aberto = _n(r.get("qtped")) - _n(r.get("qtentregue"))
        if aberto <= 0:
            continue
        cod = int(_n(r.get("CODPROD")))
        out[cod] = out.get(cod, 0.0) + aberto
    return out


# ───────────────────────── produtos ─────────────────────────
def construir_produtos(snapshot, end_map, prod_map, forn_map, comprador_map, venda_map, params,
                       hoje=None, venda_mensal_map=None, ja_pedida_map=None, embalagem_map=None):
    """snapshot: linhas do PCEST; end_map: {cod: qt_end}; prod_map/forn_map: cadastro;
    comprador_map: {matricula: nome}; venda_map: {cod:{venda,custo,qtd}} líquido do RCA.
    venda_mensal_map: {cod:{AnoMes:qtd}} p/ forecast (opcional; só quando forecast ligado).
    ja_pedida_map: {cod: qt} pedido de compra REAL em ABERTO (Winthor, qtped−entregue, 180d).
    embalagem_map: {cod: {qtunit, volume, ...}} caixa/cubagem do PCEMBALAGEM.
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
        # gerencial = QTESTGER cru (oficial v3 — bate com a planilha do diretor; NÃO subtrai
        # reserva/bloqueio). endereco = estoque WMS endereçado (usado só na validade/FEFO).
        if base == "endereco":
            qtdisp = qt_end
        else:  # gerencial (default v3)
            qtdisp = qtestger
        # valor financeiro com piso em zero: estoque negativo é erro de saldo, não vale R$ negativo
        # (alinha o total ao BASE PRODUTOS; mantém qtdisp negativo visível na tela)
        valor = max(0.0, qtdisp) * custofin

        # pedido de compra REAL em aberto (Winthor) — já descontado o que foi entregue
        qtd_ja_pedida = _n((ja_pedida_map or {}).get(cod))
        # caixa: QTUNIT do PCEMBALAGEM (oficial v3), fallback QTUNITCX do cadastro
        emb = (embalagem_map or {}).get(cod) or {}
        qtunit_emb = _n(emb.get("qtunit"))

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
        # cobertura em dias inteiros + faixa (regra oficial da planilha; faixa fixa)
        cobertura_dias = cobertura_dias_oficial(qtdisp, giro_dia)
        cobertura_faixa = cobertura_faixa_de(cobertura_dias)
        # excesso real só quando a cobertura é CALCULÁVEL e alta (separa de "sem giro" no 121+)
        excesso_real = giro_dia > 0 and cobertura_dias > 120

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
        # posição efetiva = disponível + pedido de compra REAL em aberto (Winthor).
        # Como o gerencial já reflete o que foi recebido, o "já pedido" é só o ABERTO
        # (qtped−entregue) — evita comprar de novo o que já está pedido e não duplica estoque.
        estoque_projetado = qtdisp + qtd_ja_pedida
        cobertura_proj = (estoque_projetado / giro_dia) if giro_dia > 0 and estoque_projetado > 0 else None
        sugestao = max(0.0, est_alvo - estoque_projetado)

        # prioridade de abastecimento sobre o ESTOQUE PROJETADO (metodologia v3)
        lead_un = giro_dia * lead
        seg_un = giro_dia * params["dias_seguranca"]
        if giro_dia <= 0:
            status_abast = "sem_giro" if qtdisp > 0 else "ok"
        elif estoque_projetado <= lead_un:
            status_abast = "urgente"
        elif estoque_projetado <= lead_un + seg_un:
            status_abast = "alta"
        elif estoque_projetado < est_alvo:
            status_abast = "atencao"
        elif cobertura_proj is not None and cobertura_proj > params["excesso_cob"]:
            status_abast = "excesso"
        else:
            status_abast = "ok"

        # cobertura crítica / atenção de abastecimento — bandas FIXAS (manual: cobertura até 30
        # dias = atenção, dividida em 0-15 urgente / 16-30). Não depende de parâmetro de compra.
        estoque_zero = qtdisp <= 0
        if giro_dia <= 0:
            status_ruptura = None
        else:
            cob_eff = cobertura if (cobertura is not None) else 0.0
            if cob_eff <= 15:
                status_ruptura = "0-15"
            elif cob_eff <= 30:
                status_ruptura = "16-30"
            else:
                status_ruptura = None

        # estoque parado / dead stock — bandas FIXAS por dias sem venda (manual da planilha:
        # ATENCAO 60-90, CRITICO 90-120, MUITO CRITICO 120+). Independe de parâmetro — o campo
        # "parado_atencao" vira só filtro de exibição (mín. dias) na tela/export, não desloca faixa.
        sem_giro = giro_dia <= 0 and qtdisp > 0
        if qtdisp <= 0:
            status_parado = None
        elif dias_sem_venda is None:
            status_parado = "muito_critico"
        elif dias_sem_venda >= 120:
            status_parado = "muito_critico"
        elif dias_sem_venda >= 90:
            status_parado = "critico"
        elif dias_sem_venda >= 60:
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
        # → não sugerir comprar estoque morto (giro está "preso" no histórico). Limiar FIXO 60d
        # (alinha com a faixa "atenção" do parado; não depende mais do parâmetro de exibição).
        compra_suspensa = (giro_dia > 0 and dias_sem_venda is not None
                           and dias_sem_venda >= 60)

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
        # caixa oficial v3 = QTUNIT do PCEMBALAGEM; fallback no QTUNITCX do cadastro
        caixa = qtunit_emb if qtunit_emb > 1 else qtunitcx
        # sugestão sempre disponível em CAIXAS (arredondada p/ cima) — metodologia v3
        sugestao_bruta = sugestao
        sugestao_cx = math.ceil(sugestao / caixa) if (caixa > 1 and sugestao > 0) else (1 if sugestao > 0 else 0)
        if arred_cx and caixa > 1 and sugestao > 0:
            sugestao = sugestao_cx * caixa  # sugestão em unidades, arredondada p/ caixa fechada
        # valor da compra líquida sugerida (sobre caixa fechada × custo)
        valor_sugerido_liq = (sugestao_cx * caixa * custofin) if caixa > 1 else (sugestao * custofin)

        # status executivo + ação recomendada (taxonomia v3 — clareza pro comprador)
        tem_compra = sugestao_cx > 0
        if qtdisp <= 0:
            if qtd_ja_pedida <= 0:
                status_exec = "ruptura_sem_pedido"
            else:
                status_exec = "ruptura_pedido_parcial" if tem_compra else "ruptura_pedido_cobre"
        elif tem_compra:
            if qtd_ja_pedida > 0:
                status_exec = "compra_complementar"
            else:
                status_exec = {"urgente": "compra_urgente", "alta": "compra_alta"}.get(status_abast, "programar_compra")
        else:
            status_exec = "pedido_cobre" if qtd_ja_pedida > 0 else "estoque_ok"
        if not tem_compra:
            acao_rec = "acompanhar_entrega" if qtd_ja_pedida > 0 else "sem_compra"
        elif estoque_projetado <= lead_un:
            acao_rec = "comprar_imediato"
        elif estoque_projetado <= lead_un + seg_un:
            acao_rec = "negociar_pedido"
        else:
            acao_rec = "programar_compra"
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
            "cobertura_dias": cobertura_dias, "cobertura_faixa": cobertura_faixa,
            "excesso_real": excesso_real,
            "dias_sem_venda": dias_sem_venda,
            "dtultsaida": dt_saida.isoformat() if dt_saida else None,
            "cv": _round(cv, 3) if cv is not None else None,
            "xyz": xyz,
            "lead_efetivo": _round(lead),
            "rop": _round(rop), "est_seguranca": _round(est_seg),
            "est_alvo": _round(est_alvo), "sugestao_compra": _round(sugestao),
            "sugestao_bruta": _round(sugestao_bruta), "sugestao_cx": sugestao_cx,
            "caixa": _round(caixa) if caixa else None,
            "embalagem_caixa": emb.get("embalagem"),
            "qtd_ja_pedida": _round(qtd_ja_pedida),
            "estoque_projetado": _round(estoque_projetado),
            "cobertura_proj": _round(cobertura_proj, 1) if cobertura_proj is not None else None,
            "valor_sugerido_liq": _round(valor_sugerido_liq),
            "status_exec": status_exec, "acao_rec": acao_rec,
            "cubagem_caixa_m3": _round(_n(emb.get("volume")), 5) if emb.get("volume") else None,
            "compra_suspensa": compra_suspensa,
            "status_abast": status_abast,
            "status_ruptura": status_ruptura, "estoque_zero": estoque_zero,
            "status_parado": status_parado,
            "status_saida": status_saida,
            "sem_giro": sem_giro,
            "parado_faixa": parado_faixa_de(dias_sem_venda, qtdisp),
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
    total_venda = sum(p["venda"] or 0 for p in produtos) or 1
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
        perc_venda = g["venda"] / total_venda * 100
        perc_est = g["valor"] / total_valor * 100
        # índice = participação na VENDA (R$) ÷ participação no ESTOQUE (R$) — "vende mais do que
        # pesa em estoque". Antes usava giro em UNIDADES, o que distorcia fornecedores de alto
        # valor/baixo volume (ex.: embalagem cara vendendo bem virava "estoque alto").
        indice = (perc_venda / perc_est) if perc_est > 0 else (999.0 if perc_venda > 0 else 0.0)
        # cobertura média do fornecedor (dias) — distingue eficiência real de desabastecimento
        cobertura = (g["disponivel"] / g["giro_dia"]) if g["giro_dia"] > 0 else None
        if g["giro"] <= 0 and g["venda"] <= 0:
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
            "perc_giro": _round(perc_giro, 2), "perc_venda": _round(perc_venda, 2),
            "perc_estoque": _round(perc_est, 2),
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
        # ruptura = critério OFICIAL (estoque <= 0 E giro > 0); cobertura baixa é atenção, não ruptura
        if (p.get("qtdisp") or 0) <= 0 and (p.get("giro_dia") or 0) > 0:
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


def ruptura_por_comprador(produtos):
    """Ruptura agregada por comprador (a mais rica). Ruptura = estoque ≤ 0 e giro > 0.
    n_sem_pedido = ruptura ainda sem pedido de compra em aberto (risco real);
    venda_perdida = Σ giro_mes × custo (venda potencial/mês não atendida);
    custo_reposicao = Σ sugestao_compra × custo (o que custa repor até o alvo)."""
    grupos = {}
    for p in produtos:
        cc = p.get("codcomprador")
        g = grupos.setdefault(cc if cc is not None else 0, {
            "codcomprador": cc, "comprador": p.get("comprador") or "Sem comprador",
            "n_produtos": 0, "n_ruptura": 0, "n_sem_pedido": 0,
            "venda_perdida": 0.0, "custo_reposicao": 0.0,
        })
        g["n_produtos"] += 1
        if (p.get("qtdisp") or 0) <= 0 and (p.get("giro_dia") or 0) > 0:
            g["n_ruptura"] += 1
            if (p.get("qtd_ja_pedida") or 0) <= 0:
                g["n_sem_pedido"] += 1
            g["venda_perdida"] += (p.get("giro_mes") or 0) * (p.get("custo_unit") or 0)
            g["custo_reposicao"] += (p.get("sugestao_compra") or 0) * (p.get("custo_unit") or 0)
    saida = []
    for g in grupos.values():
        saida.append({
            **g,
            "pct_ruptura": _round(g["n_ruptura"] / g["n_produtos"] * 100, 1) if g["n_produtos"] else 0,
            "venda_perdida": _round(g["venda_perdida"]),
            "custo_reposicao": _round(g["custo_reposicao"]),
        })
    saida.sort(key=lambda x: x["n_ruptura"], reverse=True)
    return saida


# ───────────────────────── desempenho comercial por comprador (RCA) ─────────────────────────
def desempenho_comprador(receita_rows, devol_map, comp_map, venda_ant_map=None, custo_ant_map=None,
                         custo_dev_map=None):
    """Espelha a aba RECEITA COMPRADOR: venda líquida, lucro bruto, margem ponderada,
    positivação (clientes distintos), devolução e comparativo ano×ano (venda E lucro) por comprador.
    receita_rows: [{CODCOMPRADOR, venda, custo, qtd, clientes_pos, fornecedores}];
    devol_map: {codcomprador: valor_devolvido}; custo_dev_map: {cc: custo_da_devolução} (RCA);
    venda_ant_map/custo_ant_map: {cc: valor} do ano ant."""
    venda_ant_map = venda_ant_map or {}
    custo_ant_map = custo_ant_map or {}
    custo_dev_map = custo_dev_map or {}
    linhas = []
    for r in receita_rows:
        cc = int(_n(r.get("CODCOMPRADOR"))) if r.get("CODCOMPRADOR") not in (None, "") else None
        if cc is None:
            continue
        venda_bruta = _n(r.get("venda"))
        custo = _n(r.get("custo"))
        dev = _n(devol_map.get(cc))
        cdev = _n(custo_dev_map.get(cc))
        venda_liq = venda_bruta - dev
        # Alinhamento RCA: a devolução tira o valor de venda da receita E devolve o
        # custo da mercadoria devolvida ao lucro (senão o custo é contado em dobro).
        lucro = venda_liq - (custo - cdev)
        margem = (lucro / venda_liq) if venda_liq else None
        venda_ant = _n(venda_ant_map.get(cc))
        yoy = ((venda_bruta - venda_ant) / venda_ant) if venda_ant > 0 else None
        # lucro do ano anterior (bruto = venda_ant − custo_ant) p/ o ano×ano do lucro
        lucro_ant = venda_ant - _n(custo_ant_map.get(cc))
        yoy_lucro = ((lucro - lucro_ant) / abs(lucro_ant)) if lucro_ant > 0 else None
        linhas.append({
            "codcomprador": cc,
            "comprador": comp_map.get(cc) or f"COMPRADOR {cc}",
            "fornecedores": int(_n(r.get("fornecedores"))),
            "clientes_pos": int(_n(r.get("clientes_pos"))),
            "qtd": _round(_n(r.get("qtd"))),
            "venda_bruta": _round(venda_bruta),
            "devolucao": _round(dev),
            "venda_liquida": _round(venda_liq),
            "lucro_bruto": _round(lucro),
            "margem": _round(margem * 100, 1) if margem is not None else None,
            "venda_ano_ant": _round(venda_ant) if venda_ant else None,
            "yoy": _round(yoy * 100, 1) if yoy is not None else None,
            "lucro_ano_ant": _round(lucro_ant) if lucro_ant else None,
            "yoy_lucro": _round(yoy_lucro * 100, 1) if yoy_lucro is not None else None,
        })
    tot_v = sum(l["venda_liquida"] for l in linhas) or 0
    tot_l = sum(l["lucro_bruto"] for l in linhas) or 0
    for l in linhas:
        l["part_receita"] = _round(l["venda_liquida"] / tot_v * 100, 1) if tot_v else 0
        l["part_lucro"] = _round(l["lucro_bruto"] / tot_l * 100, 1) if tot_l else 0
        if l["lucro_bruto"] < 0:
            l["status_lucro"] = "negativo"
        elif (l["part_lucro"] or 0) >= 30:
            l["status_lucro"] = "alta"
        elif (l["part_lucro"] or 0) >= 8:
            l["status_lucro"] = "boa"
        else:
            l["status_lucro"] = "baixa"
    linhas.sort(key=lambda x: x["lucro_bruto"], reverse=True)
    for i, l in enumerate(linhas, 1):
        l["ranking"] = i
    resumo = {
        "venda_liquida": _round(tot_v), "lucro_bruto": _round(tot_l),
        "margem": _round(tot_l / tot_v * 100, 1) if tot_v else None,
        "clientes_pos": sum(l["clientes_pos"] for l in linhas),
        "devolucao": _round(sum(l["devolucao"] for l in linhas)),
        "n_compradores": len(linhas),
    }
    return {"resumo": resumo, "compradores": linhas}


# ───────────────────────── orçamento de compras (pedido real Winthor) ─────────────────────────
def orcamento_winthor(cab, venda_comp, comp_map, forn_map, mes, comprador="TODOS",
                      pct=0.65, hoje=None, meta_override=None, lead_padrao=10):
    """Orçamento de compras a partir do pedido de compra REAL (PCPEDIDO).
    cab: linhas do cabeçalho; venda_comp: {nome_comprador: venda_liq_30d} (p/ a meta);
    comp_map: {matricula:nome}; forn_map: {codfornec: row}; mes: 'YYYY-MM'.
    meta = pct × venda_liq (override manual opcional); realizado = Σ VLTOTAL dos pedidos do mês;
    aberto = pedidos do mês ainda não recebidos (sem DTENTRADAESTOQUE)."""
    hoje = hoje or date.today()
    todos = (not comprador or comprador == "TODOS")
    pedidos = []
    realizado = aberto = 0.0
    for r in cab:
        nome = comp_map.get(int(_n(r.get("CODCOMPRADOR"))))
        if not todos and nome != comprador:
            continue
        dtem = _parse_dt(r.get("DTEMISSAO"))
        forn = forn_map.get(int(_n(r.get("CODFORNEC"))))
        vlt = _n(r.get("VLTOTAL"))
        vle = _n(r.get("VLENTREGUE"))            # valor já entregue (DTENTRADAESTOQUE é vazio aqui)
        aberto_val = max(0.0, vlt - vle)
        # recebido se o que falta entregar é desprezível (tolera resíduo de centavos)
        recebido = vlt > 0 and aberto_val <= max(1.0, vlt * 0.005)
        if recebido:
            aberto_val = 0.0
        no_mes = bool(dtem) and dtem.strftime("%Y-%m") == mes
        if no_mes:
            realizado += vlt                     # comprado válido = tudo que foi pedido no mês
            aberto += aberto_val                 # comprometido aberto = ainda não entregue
        # previsão de entrega (HÍBRIDO): usa a DTPREVENT do Winthor quando é previsão REAL
        # (posterior à emissão); senão = data do pedido + lead time do fornecedor (PRAZOENTREGA,
        # ou padrão). Evita marcar como atrasado pedido em que o Winthor só repetiu a emissão.
        dtprev_raw = _parse_dt(r.get("DTPREVENT"))
        lead = _n((forn or {}).get("PRAZOENTREGA"))
        lead = int(lead) if lead > 0 else int(lead_padrao)
        if dtprev_raw and dtem and dtprev_raw > dtem:
            dtprev = dtprev_raw
        elif dtem:
            dtprev = dtem + timedelta(days=lead)
        else:
            dtprev = dtprev_raw
        dias_prev = (dtprev - hoje).days if (dtprev and not recebido) else None
        if recebido:
            status_prazo = "recebido"
        elif dias_prev is None:
            status_prazo = "sem_prev"
        elif dias_prev < 0:
            status_prazo = "atrasado"
        elif dias_prev <= 7:
            status_prazo = "chega_7"
        else:
            status_prazo = "no_prazo"
        pedidos.append({
            "numped": int(_n(r.get("NUMPED"))),
            "data_pedido": dtem.isoformat() if dtem else None,
            "mes": dtem.strftime("%Y-%m") if dtem else None,
            "codfornec": int(_n(r.get("CODFORNEC"))),
            "fornecedor": (forn or {}).get("FORNECEDOR") if forn else None,
            "comprador": nome,
            "valor": _round(vlt),
            "valor_aberto": _round(aberto_val),
            "dt_previsao": dtprev.isoformat() if dtprev else None,
            "dias_para_chegar": dias_prev,
            "status_prazo": status_prazo,
            "recebido": recebido,
        })
    if meta_override is not None and _n(meta_override) > 0:
        meta = _n(meta_override)
    elif todos:
        meta = sum(_n(v) for v in venda_comp.values()) * pct
    else:
        meta = _n(venda_comp.get(comprador)) * pct
    saldo = meta - realizado
    abertos = [p for p in pedidos if not p["recebido"]]
    abertos.sort(key=lambda p: (p["dias_para_chegar"] if p["dias_para_chegar"] is not None else 9999))
    pedidos.sort(key=lambda p: (p["data_pedido"] or ""), reverse=True)
    resumo = {
        "mes": mes, "comprador": comprador, "pct": pct,
        "meta": _round(meta), "comprado": _round(realizado), "aberto": _round(aberto),
        "saldo": _round(saldo),
        "pct_consumido": _round(realizado / meta, 4) if meta > 0 else None,
        "n_pedidos": sum(1 for p in pedidos if p["mes"] == mes),
        "n_abertos": len(abertos),
        "n_atrasados": sum(1 for p in abertos if p["status_prazo"] == "atrasado"),
        "n_chega7": sum(1 for p in abertos if p["status_prazo"] == "chega_7"),
        "valor_aberto": _round(sum(p["valor_aberto"] for p in abertos)),
        "meta_auto": meta_override is None,
    }
    return {"resumo": resumo, "pedidos": pedidos, "abertos": abertos}


# ───────────────────────── resumos gerenciais (painel do diretor) ─────────────────────────
_FX_VALIDADE = [("0 a 15 dias", 0, 15, "URGENTE"), ("16 a 30 dias", 16, 30, "ALTO"),
                ("31 a 60 dias", 31, 60, "ATENCAO"), ("61 a 90 dias", 61, 90, "BAIXO"),
                ("Acima de 90 dias", 91, 10**9, "OK")]
_FX_COBERTURA = [("0 a 30 dias", 0, 30, "RISCO RUPTURA"), ("31 a 60 dias", 31, 60, "OK"),
                 ("61 a 90 dias", 61, 90, "ATENCAO"), ("91 a 120 dias", 91, 120, "URGENTE"),
                 ("Acima de 120 dias", 121, 10**9, "CRITICO")]


def resumo_validade(lotes, produtos_idx, hoje=None):
    """Bloco 'Itens a vencer por faixa de validade' (RELATORIO GERENCIAL do diretor).
    Consolida os lotes por (CODPROD, DTVAL) e classifica por dias até vencer.
    valor = qt consolidada × custo do produto. Devolve {faixas:[...], total:{...}}."""
    hoje = hoje or date.today()
    agg = {}
    for r in lotes:
        cod = int(_n(r.get("CODPROD")))
        dtval = _parse_dt(r.get("DTVAL"))
        if not dtval:
            continue
        agg[(cod, dtval)] = agg.get((cod, dtval), 0.0) + _n(r.get("qt"))
    faixas = []
    tot_itens = 0
    tot_valor = 0.0
    buckets = {nome: [0, 0.0] for nome, *_ in _FX_VALIDADE}
    for (cod, dtval), qt in agg.items():
        dias = (dtval - hoje).days
        custo = (produtos_idx.get(cod) or {}).get("custo_unit") or 0
        for nome, lo, hi, _status in _FX_VALIDADE:
            if lo <= dias <= hi:
                buckets[nome][0] += 1
                buckets[nome][1] += qt * custo
                break
    for nome, lo, hi, status in _FX_VALIDADE:
        n, v = buckets[nome]
        tot_itens += n
        tot_valor += v
        faixas.append({"faixa": nome, "itens": n, "valor": _round(v), "status": status})
    for f in faixas:
        f["perc"] = _round(f["itens"] / tot_itens, 4) if tot_itens else 0
    return {"faixas": faixas,
            "total": {"itens": tot_itens, "valor": _round(tot_valor)}}


def resumo_cobertura(produtos):
    """Bloco 'Cobertura de estoque por faixa de dias' (RELATORIO GERENCIAL do diretor).
    Cobertura no critério dele: giro<=0 → 9999; senão ceil(qtdisp/giro_dia); qtdisp<=0 → 0.
    valor = p['valor'] (já com piso zero). Devolve {faixas:[...], total:{...}}."""
    buckets = {nome: [0, 0.0] for nome, *_ in _FX_COBERTURA}
    tot_prod = 0
    tot_valor = 0.0
    for p in produtos:
        giro_dia = p.get("giro_dia") or 0
        qtdisp = p.get("qtdisp") or 0
        if giro_dia <= 0:
            cob = 9999
        elif qtdisp <= 0:
            cob = 0
        else:
            cob = math.ceil(qtdisp / giro_dia)
        for nome, lo, hi, _status in _FX_COBERTURA:
            if lo <= cob <= hi:
                buckets[nome][0] += 1
                buckets[nome][1] += (p.get("valor") or 0)
                break
    faixas = []
    for nome, lo, hi, status in _FX_COBERTURA:
        n, v = buckets[nome]
        tot_prod += n
        tot_valor += v
        faixas.append({"faixa": nome, "produtos": n, "valor": _round(v), "status": status})
    for f in faixas:
        f["perc"] = _round(f["produtos"] / tot_prod, 4) if tot_prod else 0
    return {"faixas": faixas,
            "total": {"produtos": tot_prod, "valor": _round(tot_valor)}}


def resumo_ruptura(produtos):
    """Bloco 'Ruptura de produtos' (RELATORIO GERENCIAL). Critério oficial do diretor:
    estoque ≤ 0 e giro mensal > 0. % sobre o universo construído (revenda com posição)."""
    total = len(produtos)
    itens = sum(1 for p in produtos if (p.get("qtdisp") or 0) <= 0 and (p.get("giro_dia") or 0) > 0)
    return {
        "itens": itens,
        "total": total,
        "perc": _round(itens / total, 4) if total else 0,
        "valor": _round(sum(p.get("valor") or 0 for p in produtos
                            if (p.get("qtdisp") or 0) <= 0 and (p.get("giro_dia") or 0) > 0)),
        "criterio": "ESTOQUE <= 0 E GIRO MENSAL > 0",
    }


# ───────────────────────── logística / cubagem (pedido real) ─────────────────────────
def vol_unitario(cad):
    """Volume unitário em m³ (PCPRODUT.VOLUME; fallback dims/1e6). 0 se sem cadastro."""
    v = _n((cad or {}).get("VOLUME"))
    if v > 0:
        return v
    a, l, c = _n(cad.get("ALTURAM3")), _n(cad.get("LARGURAM3")), _n(cad.get("COMPRIMENTOM3"))
    return (a * l * c) / 1e6 if (a > 0 and l > 0 and c > 0) else 0.0


def logistica_pedidos(cab, itens, prod_map, embalagem_map, comp_map, forn_map, hoje=None,
                      capacidade_m3=60.0, baixa_ate=0.1, dias=180):
    """Cubagem/ocupação por pedido em ABERTO (o que ainda vai chegar).
    cubagem = Σ (qtd_aberta × volume_unitário); ocupação = cubagem ÷ capacidade do veículo."""
    hoje = hoje or date.today()
    corte = hoje - timedelta(days=int(dias))
    cab_by = {}
    for r in cab:
        dtem = _parse_dt(r.get("DTEMISSAO"))
        if not dtem or dtem < corte:
            continue
        vlt, vle = _n(r.get("VLTOTAL")), _n(r.get("VLENTREGUE"))
        if max(0.0, vlt - vle) <= max(1.0, vlt * 0.005):
            continue  # já recebido
        cab_by[int(_n(r.get("NUMPED")))] = r
    ped = {}
    for r in itens:
        np_ = int(_n(r.get("NUMPED")))
        if np_ not in cab_by:
            continue
        oq = _n(r.get("qtped")) - _n(r.get("qtentregue"))
        if oq <= 0:
            continue
        cod = int(_n(r.get("CODPROD")))
        cad = prod_map.get(cod) or {}
        uv = vol_unitario(cad)
        cx = _n((embalagem_map or {}).get(cod, {}).get("qtunit")) or _n(cad.get("QTUNITCX")) or 1
        d = ped.setdefault(np_, {"cubagem": 0.0, "skus": 0, "caixas": 0.0, "unid": 0.0, "sem_vol": 0})
        d["cubagem"] += oq * uv
        d["unid"] += oq
        d["caixas"] += (oq / cx if cx > 1 else oq)
        d["skus"] += 1
        if uv <= 0:
            d["sem_vol"] += 1
    out = []
    for np_, d in ped.items():
        r = cab_by[np_]
        valor_aberto = max(0.0, _n(r.get("VLTOTAL")) - _n(r.get("VLENTREGUE")))
        cub = d["cubagem"]
        ocup = (cub / capacidade_m3) if capacidade_m3 > 0 else 0.0
        if cub <= 0:
            status = "sem_cubagem"
        elif ocup <= baixa_ate:
            status = "baixa"
        elif ocup <= 0.3:
            status = "media"
        else:
            status = "ok"
        forn = forn_map.get(int(_n(r.get("CODFORNEC"))))
        dtprev = _parse_dt(r.get("DTPREVENT"))
        out.append({
            "numped": np_,
            "data_pedido": (_parse_dt(r.get("DTEMISSAO")) or date.min).isoformat(),
            "fornecedor": (forn or {}).get("FORNECEDOR") if forn else None,
            "comprador": comp_map.get(int(_n(r.get("CODCOMPRADOR")))),
            "skus": d["skus"], "caixas": _round(d["caixas"]), "unidades": _round(d["unid"]),
            "cubagem_m3": _round(cub, 3), "valor_aberto": _round(valor_aberto),
            "valor_m3": _round(valor_aberto / cub) if cub > 0 else None,
            "ocupacao": _round(ocup, 3), "status": status,
            "sem_cubagem_itens": d["sem_vol"],
            "dt_previsao": dtprev.isoformat() if dtprev else None,
        })
    out.sort(key=lambda x: x["cubagem_m3"], reverse=True)
    resumo = {
        "n_pedidos": len(out),
        "cubagem_total": _round(sum(p["cubagem_m3"] for p in out), 2),
        "valor_total": _round(sum(p["valor_aberto"] for p in out)),
        "n_baixa": sum(1 for p in out if p["status"] == "baixa"),
        "capacidade_m3": capacidade_m3, "baixa_ate": baixa_ate,
    }
    return {"resumo": resumo, "pedidos": out}


# ───────────────────────── validade / FEFO ─────────────────────────
def validade_fefo(lotes, produtos_idx, params, hoje=None):
    hoje = hoje or date.today()
    # unifica lotes do MESMO produto + validade (soma a qtd) — evita linhas repetidas do mesmo
    # item e deixa o saldo/risco correto (consumo projetado incide sobre o total, não por lote).
    agg = {}
    for r in lotes:
        cod = int(_n(r.get("CODPROD")))
        dtval = _parse_dt(r.get("DTVAL"))
        if not dtval:
            continue
        a = agg.setdefault((cod, dtval), {"qt": 0.0, "n_lotes": 0, "lote": None})
        a["qt"] += _n(r.get("qt"))
        a["n_lotes"] += 1
        a["lote"] = r.get("NUMLOTE") or a["lote"]

    out = []
    for (cod, dtval), a in agg.items():
        qt = a["qt"]
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

        # rótulo do lote: mostra o nº quando é um só; senão indica quantos foram unificados
        numlote = (a["lote"] or "—") if a["n_lotes"] == 1 else f"{a['n_lotes']} lotes"

        out.append({
            "codprod": cod,
            "descricao": p.get("descricao") or f"PRODUTO {cod}",
            "fornecedor": p.get("fornecedor"),
            "comprador": p.get("comprador"),
            "numlote": numlote,
            "n_lotes": a["n_lotes"],
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
    out.sort(key=lambda x: (x["dias_para_vencer"], -x["valor_risco"]))
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
