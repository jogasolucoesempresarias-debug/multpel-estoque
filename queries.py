"""
Builders das queries DAX para o dataset Estoque.

Espelham a metodologia OFICIAL do TI (query SQL fornecida pelo cliente):
- Universo: PCPRODUT REVENDA='S' e OBS2<>'FL'.
- QTDISP (endereçado): SUM(PCESTENDERECO[QT]) via PCENDERECO, RUA<>99, filiais selecionadas.
- Estoque gerencial: SUM(PCEST[QTESTGER]).
- Giro: ROUND((QTVENDMES1+QTVENDMES2+QTVENDMES3)/3) — média de 3 meses.
- Custo: PCEST[CUSTOFIN]. Validade: PCESTENDERECO[DTVAL].
- Comprador: PCFORNEC[CODCOMPRADOR] (nome vem do PCEMPR no dataset RCA).

Notas de modelagem:
- PCEST é tabela-ilha (sem relacionamento) -> merge por CODPROD em Python.
- PCESTENDERECO -> PCENDERECO TEM relacionamento -> filtro de filial/RUA propaga.
- CODFILIAL é TEXTO -> {"3","5"}. RUA é numérico -> <> 99.
- Filtro de filial via CALCULATETABLE (alcança os CALCULATE internos).
"""

FILIAIS_PADRAO = ["3", "5"]


def _lista_filiais_dax(filiais):
    """['3','5'] -> '{\"3\",\"5\"}'. Vazio/None -> None (sem filtro)."""
    if not filiais:
        return None
    itens = ", ".join(f'"{str(f).strip()}"' for f in filiais if str(f).strip())
    return "{" + itens + "}" if itens else None


# ───────────────────────── cadastro ─────────────────────────
def q_cadastro_produto():
    """Só produtos de revenda e não 'fora de linha' (espelha REVENDA='S' AND OBS2<>'FL')."""
    return """EVALUATE
SELECTCOLUMNS(
    FILTER(PCPRODUT, PCPRODUT[REVENDA] = "S" && PCPRODUT[OBS2] <> "FL"),
    "CODPROD",   PCPRODUT[CODPROD],
    "DESCRICAO", PCPRODUT[DESCRICAO],
    "CODFORNEC", PCPRODUT[CODFORNEC],
    "CODEPTO",   PCPRODUT[CODEPTO],
    "CODSEC",    PCPRODUT[CODSEC],
    "EMBALAGEM", PCPRODUT[EMBALAGEM],
    "QTUNITCX",  PCPRODUT[QTUNITCX],
    "NCM",       PCPRODUT[CLASSIFICFISCAL],
    "MARCA",     PCPRODUT[MARCA],
    "PRAZOVAL",  PCPRODUT[PRAZOVAL],
    "CTRL_VALIDADE", PCPRODUT[CONTROLAVALIDADEDOLOTE]
)"""


def q_cadastro_fornecedor():
    return """EVALUATE
SELECTCOLUMNS(PCFORNEC,
    "CODFORNEC",      PCFORNEC[CODFORNEC],
    "FORNECEDOR",     PCFORNEC[FORNECEDOR],
    "FANTASIA",       PCFORNEC[FANTASIA],
    "CODCOMPRADOR",   PCFORNEC[CODCOMPRADOR],
    "PRAZOENTREGA",   PCFORNEC[PRAZOENTREGA],
    "VLMINPEDCOMPRA", PCFORNEC[VLMINPEDCOMPRA]
)"""


def q_filiais():
    return "EVALUATE SUMMARIZE(PCEST, PCEST[CODFILIAL])"


# ───── PCEMPR (nome do comprador) — roda no dataset RCA ─────
def q_compradores_rca():
    return """EVALUATE
SELECTCOLUMNS(PCEMPR, "MATRICULA", PCEMPR[MATRICULA], "NOME", PCEMPR[NOME])"""


# ───── Venda real por produto — roda no dataset RCA (medidas nativas) ─────
def _d(d):
    return f"DATE({d.year},{d.month},{d.day})"


def q_vendas_rca(data_ini, data_fim):
    """Venda bruta + custo + qtd por CODPROD no período (DTSAIDA). Usa as medidas do RCA."""
    return f"""EVALUATE
FILTER(
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODPROD],
        FILTER(FATURAMENTO_VENDAS,
            FATURAMENTO_VENDAS[DTSAIDA] >= {_d(data_ini)} && FATURAMENTO_VENDAS[DTSAIDA] <= {_d(data_fim)}),
        "venda", [VENDA BRUTA],
        "custo", [CUSTO TOTAL],
        "qtd",   SUM(FATURAMENTO_VENDAS[QT])
    ),
    [venda] <> 0 || [qtd] <> 0
)"""


def q_devol_rca(data_ini, data_fim):
    """Devolução (valor+custo) por CODPROD no período (DTENT) — alinha receita líquida do RCA."""
    return f"""EVALUATE
FILTER(
    SUMMARIZECOLUMNS(
        FATURAMENTO_DEVOLUCAO[CODPROD],
        FILTER(FATURAMENTO_DEVOLUCAO,
            FATURAMENTO_DEVOLUCAO[DTENT] >= {_d(data_ini)} && FATURAMENTO_DEVOLUCAO[DTENT] <= {_d(data_fim)}),
        "dev",  [TOTAL DEVOLUCAO],
        "cdev", [CUSTO TOTAL DEVOLUCAO]
    ),
    [dev] <> 0
)"""


def q_vendas_mensal_rca(data_ini):
    """Venda (QT) por CODPROD × mês (AnoMes YYYYMM) desde data_ini. Base do forecast.
    GROUPBY autossuficiente (não depende de relacionamento com CALENDARIO)."""
    return f"""EVALUATE
VAR base =
    ADDCOLUMNS(
        FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= {_d(data_ini)}),
        "AM", YEAR(FATURAMENTO_VENDAS[DTSAIDA]) * 100 + MONTH(FATURAMENTO_VENDAS[DTSAIDA])
    )
RETURN
GROUPBY(
    base,
    FATURAMENTO_VENDAS[CODPROD], [AM],
    "qtd", SUMX(CURRENTGROUP(), FATURAMENTO_VENDAS[QT])
)"""


def q_devol_av_rca(data_ini, data_fim):
    """Devolução avulsa (valor+custo) por CODPROD no período (DTENT)."""
    return f"""EVALUATE
FILTER(
    SUMMARIZECOLUMNS(
        FATURAMENTO_DEVOLUCAO_AVULSA[CODPROD],
        FILTER(FATURAMENTO_DEVOLUCAO_AVULSA,
            FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= {_d(data_ini)} && FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] <= {_d(data_fim)}),
        "devav",  [TOTAL DEVOLUCAO AVULSA],
        "cdevav", [CUSTO TOTAL DEVOLUCAO AVULSA]
    ),
    [devav] <> 0
)"""


# ───────────────────────── snapshot PCEST (giro, gerencial, custo, datas) ───────
def q_snapshot_estoque(filiais=None):
    """Agrega PCEST por CODPROD nas filiais dadas. Giro = média 3 meses; custo = CUSTOFIN."""
    inner = """ADDCOLUMNS(
        SUMMARIZE(PCEST, PCEST[CODPROD]),
        "qtestger",   CALCULATE(SUM(PCEST[QTESTGER])),
        "qtreserv",   CALCULATE(SUM(PCEST[QTRESERV])),
        "qtbloq",     CALCULATE(SUM(PCEST[QTBLOQUEADA])),
        "qtpend",     CALCULATE(SUM(PCEST[QTPENDENTE])),
        "qttransito", CALCULATE(SUM(PCEST[QTTRANSITO])),
        "giro_m1",    CALCULATE(SUM(PCEST[QTVENDMES1])),
        "giro_m2",    CALCULATE(SUM(PCEST[QTVENDMES2])),
        "giro_m3",    CALCULATE(SUM(PCEST[QTVENDMES3])),
        "custofin",   CALCULATE(MAX(PCEST[CUSTOFIN])),
        "dtultsaida", CALCULATE(MAX(PCEST[DTULTSAIDA])),
        "dtultent",   CALCULATE(MAX(PCEST[DTULTENT]))
    )"""
    lista = _lista_filiais_dax(filiais)
    tabela = f"CALCULATETABLE({inner}, PCEST[CODFILIAL] IN {lista})" if lista else inner
    return f"""EVALUATE
FILTER(
    {tabela},
    [qtestger] <> 0 || [giro_m1] <> 0 || [giro_m2] <> 0 || [giro_m3] <> 0
)"""


# ───────────────────────── estoque endereçado (QTDISP oficial) ─────────────────
def q_estoque_endereco(filiais=None):
    """SUM(PCESTENDERECO[QT]) por CODPROD, RUA<>99, filiais dadas (= ESTQ_ENDEREÇO do TI)."""
    inner = """ADDCOLUMNS(
        SUMMARIZE(PCESTENDERECO, PCESTENDERECO[CODPROD]),
        "qt_end", CALCULATE(SUM(PCESTENDERECO[QT]))
    )"""
    lista = _lista_filiais_dax(filiais)
    filtros = ["PCENDERECO[RUA] <> 99"]
    if lista:
        filtros.append(f"PCENDERECO[CODFILIAL] IN {lista}")
    return f"""EVALUATE
FILTER(
    CALCULATETABLE({inner}, {", ".join(filtros)}),
    [qt_end] <> 0
)"""


# ───────────────────────── validade / FEFO ─────────────────────────
def _date_dax(d):
    return f"DATE({d.year},{d.month},{d.day})"


def q_validade(data_ini, data_fim, filiais=None):
    """Lotes vencendo na janela, estoque endereçado (RUA<>99, filiais). Grão produto+lote+validade."""
    lista = _lista_filiais_dax(filiais)
    filtros = ["PCENDERECO[RUA] <> 99"]
    if lista:
        filtros.append(f"PCENDERECO[CODFILIAL] IN {lista}")
    inner = """ADDCOLUMNS(
        SUMMARIZE(PCESTENDERECO,
            PCESTENDERECO[CODPROD], PCESTENDERECO[NUMLOTE], PCESTENDERECO[DTVAL]),
        "qt", CALCULATE(SUM(PCESTENDERECO[QT]))
    )"""
    return f"""EVALUATE
FILTER(
    CALCULATETABLE({inner}, {", ".join(filtros)}),
    PCESTENDERECO[DTVAL] >= {_date_dax(data_ini)}
    && PCESTENDERECO[DTVAL] <= {_date_dax(data_fim)}
    && [qt] > 0
)"""


def q_lotes_produto(codprod, filiais=None):
    """Todos os lotes/validades de um produto (drill 360°), estoque endereçado."""
    lista = _lista_filiais_dax(filiais)
    filtros = ["PCENDERECO[RUA] <> 99"]
    if lista:
        filtros.append(f"PCENDERECO[CODFILIAL] IN {lista}")
    inner = """ADDCOLUMNS(
        SUMMARIZE(PCESTENDERECO,
            PCESTENDERECO[CODPROD], PCESTENDERECO[NUMLOTE], PCESTENDERECO[DTVAL]),
        "qt", CALCULATE(SUM(PCESTENDERECO[QT]))
    )"""
    return f"""EVALUATE
FILTER(
    CALCULATETABLE({inner}, {", ".join(filtros)}),
    PCESTENDERECO[CODPROD] = {int(codprod)} && [qt] > 0
)"""
