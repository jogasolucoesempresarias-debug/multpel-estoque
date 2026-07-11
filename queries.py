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
    "CODFAB",    PCPRODUT[CODFAB],
    "PERCIPI",   PCPRODUT[PERCIPI],
    "CODFORNEC", PCPRODUT[CODFORNEC],
    "CODEPTO",   PCPRODUT[CODEPTO],
    "CODSEC",    PCPRODUT[CODSEC],
    "EMBALAGEM", PCPRODUT[EMBALAGEM],
    "QTUNITCX",  PCPRODUT[QTUNITCX],
    "NCM",       PCPRODUT[CLASSIFICFISCAL],
    "MARCA",     PCPRODUT[MARCA],
    "PRAZOVAL",  PCPRODUT[PRAZOVAL],
    "CTRL_VALIDADE", PCPRODUT[CONTROLAVALIDADEDOLOTE],
    "VOLUME",    PCPRODUT[VOLUME],
    "ALTURAM3",  PCPRODUT[ALTURAM3],
    "LARGURAM3", PCPRODUT[LARGURAM3],
    "COMPRIMENTOM3", PCPRODUT[COMPRIMENTOM3],
    "PESOBRUTO", PCPRODUT[PESOBRUTO]
)"""


def q_cadastro_fornecedor():
    return """EVALUATE
SELECTCOLUMNS(PCFORNEC,
    "CODFORNEC",      PCFORNEC[CODFORNEC],
    "FORNECEDOR",     PCFORNEC[FORNECEDOR],
    "FANTASIA",       PCFORNEC[FANTASIA],
    "CODCOMPRADOR",   PCFORNEC[CODCOMPRADOR],
    "PRAZOENTREGA",   PCFORNEC[PRAZOENTREGA],
    "VLMINPEDCOMPRA", PCFORNEC[VLMINPEDCOMPRA],
    "CGC",            PCFORNEC[CGC],
    "IE",             PCFORNEC[IE],
    "NUMEROEND",      PCFORNEC[NUMEROEND],
    "BAIRRO",         PCFORNEC[BAIRRO],
    "CEP",            PCFORNEC[CEP],
    "CIDADE",         PCFORNEC[CIDADE],
    "ESTADO",         PCFORNEC[ESTADO],
    "EMAIL",          PCFORNEC[EMAIL]
)"""


def q_filiais():
    return "EVALUATE SUMMARIZE(PCEST, PCEST[CODFILIAL])"


# ───── PCEMPR (nome do comprador) — roda no dataset RCA ─────
def q_compradores_rca():
    return """EVALUATE
SELECTCOLUMNS(PCEMPR, "MATRICULA", PCEMPR[MATRICULA], "NOME", PCEMPR[NOME])"""


# ───── PCEMPR no dataset Estoque (nome do comprador, sem cruzar RCA) ─────
def q_compradores_estoque():
    """{matricula: nome} direto do dataset Estoque (PCEMPR existe nos dois datasets)."""
    return """EVALUATE
SELECTCOLUMNS(PCEMPR, "MATRICULA", PCEMPR[MATRICULA], "NOME", PCEMPR[NOME])"""


# ───────────────────────── pedido de compra real (Winthor) ─────────────────────
# Modelo tem tabelas-ilha → o "join" PCPEDIDO×PCITEM é feito em Python (por NUMPED).
def q_pedido_cab(desde, filiais=None):
    """Cabeçalho dos pedidos (PCPEDIDO) emitidos a partir de `desde` (date).
    1 registro por NUMPED. VLTOTAL = valor do pedido; DTENTRADAESTOQUE vazio = ainda aberto
    (não recebido). Não há campo de cancelamento no modelo (alinha com a planilha v3)."""
    lista = _lista_filiais_dax(filiais)
    filtros = [f"PCPEDIDO[DTEMISSAO] >= {_date_dax(desde)}"]
    if lista:
        filtros.append(f"PCPEDIDO[CODFILIAL] IN {lista}")
    return f"""EVALUATE
SELECTCOLUMNS(
    FILTER(PCPEDIDO, {" && ".join(filtros)}),
    "NUMPED",           PCPEDIDO[NUMPED],
    "DTEMISSAO",        PCPEDIDO[DTEMISSAO],
    "CODFILIAL",        PCPEDIDO[CODFILIAL],
    "CODFORNEC",        PCPEDIDO[CODFORNEC],
    "CODCOMPRADOR",     PCPEDIDO[CODCOMPRADOR],
    "VLTOTAL",          PCPEDIDO[VLTOTAL],
    "VLENTREGUE",       PCPEDIDO[VLENTREGUE],
    "DTVENC",           PCPEDIDO[DTVENC],
    "DTENTRADAESTOQUE", PCPEDIDO[DTENTRADAESTOQUE],
    "DTPREVENT",        PCPEDIDO[DTPREVENT]
)"""


def q_pedido_itens(numped_min):
    """Itens (PCITEM) dos pedidos com NUMPED >= numped_min (limita o volume sem depender de
    relacionamento). Agrega por (NUMPED, CODPROD): qtd pedida e qtd já entregue."""
    return f"""EVALUATE
CALCULATETABLE(
    ADDCOLUMNS(
        SUMMARIZE(PCITEM, PCITEM[NUMPED], PCITEM[CODPROD]),
        "qtped",      CALCULATE(SUM(PCITEM[QTPEDIDA])),
        "qtentregue", CALCULATE(SUM(PCITEM[QTENTREGUE]))
    ),
    PCITEM[NUMPED] >= {int(numped_min)}
)"""


def q_pedido_itens_um(numped):
    """Itens (PCITEM) de UM pedido específico — drill 'ver itens comprados' no Orçamento."""
    return f"""EVALUATE
FILTER(
    ADDCOLUMNS(
        SUMMARIZE(PCITEM, PCITEM[NUMPED], PCITEM[CODPROD]),
        "qtped",      CALCULATE(SUM(PCITEM[QTPEDIDA])),
        "qtentregue", CALCULATE(SUM(PCITEM[QTENTREGUE]))
    ),
    PCITEM[NUMPED] = {int(numped)}
)"""


# ───────────────────────── embalagem / cubagem (PCEMBALAGEM) ────────────────────
def q_embalagem():
    """Por CODPROD: caixa (MAX QTUNIT) + cubagem (VOLUME/dimensões) + peso.
    Agregado no servidor p/ não esbarrar no teto de 100k linhas do executeQueries."""
    return """EVALUATE
ADDCOLUMNS(
    SUMMARIZE(PCEMBALAGEM, PCEMBALAGEM[CODPROD]),
    "qtunit",      CALCULATE(MAX(PCEMBALAGEM[QTUNIT])),
    "embalagem",   CALCULATE(MAXX(TOPN(1,
                       SUMMARIZE(PCEMBALAGEM, PCEMBALAGEM[EMBALAGEM], PCEMBALAGEM[QTUNIT]),
                       PCEMBALAGEM[QTUNIT], DESC), PCEMBALAGEM[EMBALAGEM])),
    "volume",      CALCULATE(MAX(PCEMBALAGEM[VOLUME])),
    "altura",      CALCULATE(MAX(PCEMBALAGEM[ALTURA])),
    "largura",     CALCULATE(MAX(PCEMBALAGEM[LARGURA])),
    "comprimento", CALCULATE(MAX(PCEMBALAGEM[COMPRIMENTO])),
    "pesobruto",   CALCULATE(MAX(PCEMBALAGEM[PESOBRUTO]))
)"""


# ───── Venda real por produto — roda no dataset RCA (medidas nativas) ─────
def _d(d):
    return f"DATE({d.year},{d.month},{d.day})"


def _filiais_txt_dax(filiais):
    """[3,7,8] -> '{"3", "7", "8"}' p/ FATURAMENTO_*[CODFILIAL] (TEXTO no RCA). Vazio/None -> None."""
    if not filiais:
        return None
    itens = ", ".join(f'"{str(f).strip()}"' for f in filiais if str(f).strip())
    return "{" + itens + "}" if itens else None


def _fv_and(tab, filiais):
    """Cláusula ' && TAB[CODFILIAL] IN {..}' (ou '' se sem filial) p/ escopar a venda por unidade."""
    lst = _filiais_txt_dax(filiais)
    return f" && {tab}[CODFILIAL] IN {lst}" if lst else ""


def q_vendas_rca(data_ini, data_fim, filiais=None):
    """Venda bruta + custo + qtd por CODPROD no período (DTSAIDA). Usa as medidas do RCA."""
    return f"""EVALUATE
FILTER(
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODPROD],
        FILTER(FATURAMENTO_VENDAS,
            FATURAMENTO_VENDAS[DTSAIDA] >= {_d(data_ini)} && FATURAMENTO_VENDAS[DTSAIDA] <= {_d(data_fim)}{_fv_and('FATURAMENTO_VENDAS', filiais)}),
        "venda", [VENDA BRUTA],
        "custo", [CUSTO TOTAL],
        "qtd",   SUM(FATURAMENTO_VENDAS[QT])
    ),
    [venda] <> 0 || [qtd] <> 0
)"""


def q_devol_rca(data_ini, data_fim, filiais=None):
    """Devolução (valor+custo) por CODPROD no período (DTENT) — alinha receita líquida do RCA."""
    return f"""EVALUATE
FILTER(
    SUMMARIZECOLUMNS(
        FATURAMENTO_DEVOLUCAO[CODPROD],
        FILTER(FATURAMENTO_DEVOLUCAO,
            FATURAMENTO_DEVOLUCAO[DTENT] >= {_d(data_ini)} && FATURAMENTO_DEVOLUCAO[DTENT] <= {_d(data_fim)}{_fv_and('FATURAMENTO_DEVOLUCAO', filiais)}),
        "dev",  [TOTAL DEVOLUCAO],
        "cdev", [CUSTO TOTAL DEVOLUCAO]
    ),
    [dev] <> 0
)"""


def q_vendas_mensal_rca(data_ini, filiais=None):
    """Venda (QT) por CODPROD × mês (AnoMes YYYYMM) desde data_ini. Base do forecast.
    GROUPBY autossuficiente (não depende de relacionamento com CALENDARIO)."""
    return f"""EVALUATE
VAR base =
    ADDCOLUMNS(
        FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= {_d(data_ini)}{_fv_and('FATURAMENTO_VENDAS', filiais)}),
        "AM", YEAR(FATURAMENTO_VENDAS[DTSAIDA]) * 100 + MONTH(FATURAMENTO_VENDAS[DTSAIDA])
    )
RETURN
GROUPBY(
    base,
    FATURAMENTO_VENDAS[CODPROD], [AM],
    "qtd", SUMX(CURRENTGROUP(), FATURAMENTO_VENDAS[QT])
)"""


# ───── Desempenho comercial por COMPRADOR (espelha aba RECEITA COMPRADOR) ─────
def q_receita_comprador_rca(data_ini, data_fim, filiais=None):
    """Venda bruta + custo + qtd + positivação (clientes distintos) + nº fornecedores,
    agrupado por CODCOMPRADOR no período (DTSAIDA). CODCOMPRADOR está no próprio fato."""
    return f"""EVALUATE
FILTER(
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODCOMPRADOR],
        FILTER(FATURAMENTO_VENDAS,
            FATURAMENTO_VENDAS[DTSAIDA] >= {_d(data_ini)} && FATURAMENTO_VENDAS[DTSAIDA] <= {_d(data_fim)}{_fv_and('FATURAMENTO_VENDAS', filiais)}),
        "venda",        [VENDA BRUTA],
        "custo",        [CUSTO TOTAL],
        "qtd",          SUM(FATURAMENTO_VENDAS[QT]),
        "clientes_pos", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]),
        "fornecedores", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODFORNEC])
    ),
    [venda] <> 0 || [qtd] <> 0
)"""


def q_devol_comprador_rca(data_ini, data_fim, filiais=None):
    """Devolução (valor + custo) por CODCOMPRADOR no período (DTENT). O valor entra na
    venda líquida; o custo (cdev) é devolvido ao lucro (RCA: lucro = líq − (custo − cdev))."""
    return f"""EVALUATE
FILTER(
    SUMMARIZECOLUMNS(
        FATURAMENTO_DEVOLUCAO[CODCOMPRADOR],
        FILTER(FATURAMENTO_DEVOLUCAO,
            FATURAMENTO_DEVOLUCAO[DTENT] >= {_d(data_ini)} && FATURAMENTO_DEVOLUCAO[DTENT] <= {_d(data_fim)}{_fv_and('FATURAMENTO_DEVOLUCAO', filiais)}),
        "dev",  [TOTAL DEVOLUCAO],
        "cdev", [CUSTO TOTAL DEVOLUCAO]
    ),
    [dev] <> 0
)"""


def q_venda_comprador_periodo_rca(data_ini, data_fim, filiais=None):
    """Venda bruta + custo por CODCOMPRADOR num período (p/ comparativo ano×ano de venda E lucro)."""
    return f"""EVALUATE
FILTER(
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODCOMPRADOR],
        FILTER(FATURAMENTO_VENDAS,
            FATURAMENTO_VENDAS[DTSAIDA] >= {_d(data_ini)} && FATURAMENTO_VENDAS[DTSAIDA] <= {_d(data_fim)}{_fv_and('FATURAMENTO_VENDAS', filiais)}),
        "venda", [VENDA BRUTA],
        "custo", [CUSTO TOTAL]
    ),
    [venda] <> 0
)"""


def q_devol_av_rca(data_ini, data_fim, filiais=None):
    """Devolução avulsa (valor+custo) por CODPROD no período (DTENT)."""
    return f"""EVALUATE
FILTER(
    SUMMARIZECOLUMNS(
        FATURAMENTO_DEVOLUCAO_AVULSA[CODPROD],
        FILTER(FATURAMENTO_DEVOLUCAO_AVULSA,
            FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= {_d(data_ini)} && FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] <= {_d(data_fim)}{_fv_and('FATURAMENTO_DEVOLUCAO_AVULSA', filiais)}),
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
    # DESCRICAO via LOOKUPVALUE (PCESTENDERECO e PCPRODUT são ilhas, sem relacionamento):
    # garante o nome mesmo p/ item zerado no gerencial que não entra na lista principal.
    inner = """ADDCOLUMNS(
        SUMMARIZE(PCESTENDERECO,
            PCESTENDERECO[CODPROD], PCESTENDERECO[NUMLOTE], PCESTENDERECO[DTVAL]),
        "qt", CALCULATE(SUM(PCESTENDERECO[QT])),
        "DESCRICAO", LOOKUPVALUE(PCPRODUT[DESCRICAO], PCPRODUT[CODPROD], PCESTENDERECO[CODPROD])
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
