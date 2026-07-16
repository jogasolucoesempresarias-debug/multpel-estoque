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


def q_venda_comprador_mensal_rca(data_ini, filiais=None):
    """Venda BRUTA por CODCOMPRADOR × mês (CALENDARIO[AnoMes]) desde data_ini.
    Base do % da aba Vencidos. Usa a measure oficial [VENDA BRUTA] (≠ SUM(VLVENDA),
    diferem ~0,5%) e o relacionamento DTSAIDA→CALENDARIO. Líquida = esta − devolução."""
    return f"""EVALUATE
FILTER(
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODCOMPRADOR],
        CALENDARIO[AnoMes],
        FILTER(FATURAMENTO_VENDAS,
            FATURAMENTO_VENDAS[DTSAIDA] >= {_d(data_ini)}{_fv_and('FATURAMENTO_VENDAS', filiais)}),
        "venda", [VENDA BRUTA]
    ),
    [venda] <> 0
)"""


def q_devol_comprador_mensal_rca(data_ini, filiais=None):
    """Devolução por CODCOMPRADOR × mês (CALENDARIO[AnoMes]) desde data_ini (DTENT).
    Espelha o q_devol_comprador_rca, mas mensal — p/ a venda líquida do % da aba Vencidos."""
    return f"""EVALUATE
FILTER(
    SUMMARIZECOLUMNS(
        FATURAMENTO_DEVOLUCAO[CODCOMPRADOR],
        CALENDARIO[AnoMes],
        FILTER(FATURAMENTO_DEVOLUCAO,
            FATURAMENTO_DEVOLUCAO[DTENT] >= {_d(data_ini)}{_fv_and('FATURAMENTO_DEVOLUCAO', filiais)}),
        "dev", [TOTAL DEVOLUCAO]
    ),
    [dev] <> 0
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


def q_prox_venc(codprods, hoje, filiais=None):
    """Próximo vencimento (menor DTVAL futuro) do estoque endereçado, por CODPROD, p/ uma
    lista de códigos. Usado na aba Vencidos: no painel "já venceu e ainda está em estoque",
    mostra QUANDO o estoque atual vence — transforma "já perdi" em "aja antes de perder de novo".
    Só lotes com qt>0, RUA<>99, DTVAL>=hoje (o que já venceu no endereço é outro problema)."""
    if not codprods:
        return None
    lista = _lista_filiais_dax(filiais)
    filtros = ["PCENDERECO[RUA] <> 99"]
    if lista:
        filtros.append(f"PCENDERECO[CODFILIAL] IN {lista}")
    cods = "{" + ",".join(str(int(c)) for c in codprods) + "}"
    inner = """ADDCOLUMNS(
        SUMMARIZE(PCESTENDERECO, PCESTENDERECO[CODPROD], PCESTENDERECO[DTVAL]),
        "qt", CALCULATE(SUM(PCESTENDERECO[QT]))
    )"""
    return f"""EVALUATE
GROUPBY(
    FILTER(
        CALCULATETABLE({inner}, {", ".join(filtros)}),
        PCESTENDERECO[CODPROD] IN {cods} && [qt] > 0
        && PCESTENDERECO[DTVAL] >= {_date_dax(hoje)}
    ),
    PCESTENDERECO[CODPROD],
    "prox_venc", MINX(CURRENTGROUP(), PCESTENDERECO[DTVAL])
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


# ───────────────────────── ocupação / WMS ─────────────────────────
def q_produto_enderecos(codprod, filiais=None):
    """Posições WMS de 1 produto (drawer): RUA/PREDIO/NIVEL/APTO + TIPOENDER + QT.
    QT>0, RUA<>99. Endereço vem por LOOKUPVALUE em PCENDERECO[CODENDERECO]."""
    lista = _lista_filiais_dax(filiais)
    filtros = ["PCENDERECO[RUA] <> 99"]
    if lista:
        filtros.append(f"PCENDERECO[CODFILIAL] IN {lista}")
    inner = f"""SELECTCOLUMNS(
        FILTER(PCESTENDERECO, PCESTENDERECO[CODPROD] = {int(codprod)} && PCESTENDERECO[QT] > 0),
        "rua",    LOOKUPVALUE(PCENDERECO[RUA],       PCENDERECO[CODENDERECO], PCESTENDERECO[CODENDERECO]),
        "predio", LOOKUPVALUE(PCENDERECO[PREDIO],    PCENDERECO[CODENDERECO], PCESTENDERECO[CODENDERECO]),
        "nivel",  LOOKUPVALUE(PCENDERECO[NIVEL],     PCENDERECO[CODENDERECO], PCESTENDERECO[CODENDERECO]),
        "apto",   LOOKUPVALUE(PCENDERECO[APTO],      PCENDERECO[CODENDERECO], PCESTENDERECO[CODENDERECO]),
        "tipo",   LOOKUPVALUE(PCENDERECO[TIPOENDER], PCENDERECO[CODENDERECO], PCESTENDERECO[CODENDERECO]),
        "q",      PCESTENDERECO[QT],
        "dtval",  PCESTENDERECO[DTVAL],
        "numlote", PCESTENDERECO[NUMLOTE]
    )"""
    return f"""EVALUATE CALCULATETABLE({inner}, {", ".join(filtros)})"""


def q_ocupacao_kpis(filiais=None):
    """KPIs de ocupação na RÉGUA DO WMS (consulta 1772): exclui BLOQUEADO (BLOQUEIO="N") e
    inclui TODAS as ruas (a 99 conta como física no WMS). Bloqueados vão à parte.
    'com_estoque' = posições com estoque físico agora (QT>0) — leitura secundária."""
    lista = _lista_filiais_dax(filiais)
    fpos = ['PCENDERECO[ATIVO] = "S"', 'PCENDERECO[BLOQUEIO] = "N"']
    focc = ['PCESTENDERECO[QT] > 0', 'PCENDERECO[BLOQUEIO] = "N"']
    fbloq = ['PCENDERECO[ATIVO] = "S"', 'PCENDERECO[BLOQUEIO] = "S"']
    if lista:
        for f in (fpos, focc, fbloq):
            f.append(f"PCENDERECO[CODFILIAL] IN {lista}")
    focc_situ = fpos + ['PCENDERECO[SITUACAO] = "O"']
    return f"""EVALUATE ROW(
    "posicoes", CALCULATE(COUNTROWS(PCENDERECO), {", ".join(fpos)}),
    "ocupadas", CALCULATE(COUNTROWS(PCENDERECO), {", ".join(focc_situ)}),
    "bloqueados", CALCULATE(COUNTROWS(PCENDERECO), {", ".join(fbloq)}),
    "com_estoque", CALCULATE(DISTINCTCOUNT(PCESTENDERECO[CODENDERECO]), {", ".join(focc)}),
    "produtos", CALCULATE(DISTINCTCOUNT(PCESTENDERECO[CODPROD]), {", ".join(focc)}),
    "pares", CALCULATE(COUNTROWS(SUMMARIZE(PCESTENDERECO, PCESTENDERECO[CODPROD], PCESTENDERECO[CODENDERECO])), {", ".join(focc)})
)"""


def q_posicoes_por_produto(filiais=None):
    """Mapa CODPROD -> nº de posições ocupadas (DISTINCTCOUNT CODENDERECO, QT>0)."""
    lista = _lista_filiais_dax(filiais)
    filtros = ["PCENDERECO[RUA] <> 99"]
    if lista:
        filtros.append(f"PCENDERECO[CODFILIAL] IN {lista}")
    inner = """ADDCOLUMNS(
        SUMMARIZE(FILTER(PCESTENDERECO, PCESTENDERECO[QT] > 0), PCESTENDERECO[CODPROD]),
        "pos", CALCULATE(DISTINCTCOUNT(PCESTENDERECO[CODENDERECO]))
    )"""
    return f"""EVALUATE
FILTER(
    CALCULATETABLE({inner}, {", ".join(filtros)}),
    [pos] > 0
)"""


def q_ocupacao_por_rua(filiais=None):
    """Por RUA: posições (não bloqueadas) e ocupadas (SITUACAO="O") — régua do WMS."""
    lista = _lista_filiais_dax(filiais)
    fpos = ['PCENDERECO[ATIVO] = "S"', 'PCENDERECO[BLOQUEIO] = "N"']
    if lista:
        fpos.append(f"PCENDERECO[CODFILIAL] IN {lista}")
    inner = """ADDCOLUMNS(
        SUMMARIZE(PCENDERECO, PCENDERECO[RUA]),
        "posicoes", CALCULATE(COUNTROWS(PCENDERECO)),
        "ocupadas", CALCULATE(COUNTROWS(PCENDERECO), PCENDERECO[SITUACAO] = "O")
    )"""
    return f"""EVALUATE CALCULATETABLE({inner}, {", ".join(fpos)})"""


def q_ocupacao_por_tipo(filiais=None):
    """Por TIPOENDER (AP=picking / AE=pulmão): posições (não bloqueadas) e ocupadas — régua WMS."""
    lista = _lista_filiais_dax(filiais)
    fpos = ['PCENDERECO[ATIVO] = "S"', 'PCENDERECO[BLOQUEIO] = "N"']
    if lista:
        fpos.append(f"PCENDERECO[CODFILIAL] IN {lista}")
    inner = """ADDCOLUMNS(
        SUMMARIZE(PCENDERECO, PCENDERECO[TIPOENDER]),
        "posicoes", CALCULATE(COUNTROWS(PCENDERECO)),
        "ocupadas", CALCULATE(COUNTROWS(PCENDERECO), PCENDERECO[SITUACAO] = "O")
    )"""
    return f"""EVALUATE CALCULATETABLE({inner}, {", ".join(fpos)})"""


def q_rua_itens(rua, filiais=None):
    """Posições COM ESTOQUE de uma RUA (conferência): endereço + produto + qtd + validade."""
    lista = _lista_filiais_dax(filiais)
    filtros = [f"PCENDERECO[RUA] = {int(rua)}"]
    if lista:
        filtros.append(f"PCENDERECO[CODFILIAL] IN {lista}")
    inner = """SELECTCOLUMNS(
        FILTER(PCESTENDERECO, PCESTENDERECO[QT] > 0),
        "predio",  LOOKUPVALUE(PCENDERECO[PREDIO],    PCENDERECO[CODENDERECO], PCESTENDERECO[CODENDERECO]),
        "nivel",   LOOKUPVALUE(PCENDERECO[NIVEL],     PCENDERECO[CODENDERECO], PCESTENDERECO[CODENDERECO]),
        "apto",    LOOKUPVALUE(PCENDERECO[APTO],      PCENDERECO[CODENDERECO], PCESTENDERECO[CODENDERECO]),
        "tipo",    LOOKUPVALUE(PCENDERECO[TIPOENDER], PCENDERECO[CODENDERECO], PCESTENDERECO[CODENDERECO]),
        "codprod", PCESTENDERECO[CODPROD],
        "qt",      PCESTENDERECO[QT],
        "dtval",   PCESTENDERECO[DTVAL]
    )"""
    return f"""EVALUATE CALCULATETABLE({inner}, {", ".join(filtros)})"""


def q_ocupacao_vazias(filiais=None, rua=None):
    """Posições que o WMS marca Ocupada (SITUACAO="O") mas SEM estoque físico
    (nenhum registro com QT>0) — o 'reservado vazio' — + o produto alocado à vaga.
    `rua` opcional restringe a uma rua (usado na conferência)."""
    lista = _lista_filiais_dax(filiais)
    fbase = ['PCENDERECO[SITUACAO] = "O"', 'PCENDERECO[RUA] <> 99', 'PCENDERECO[ATIVO] = "S"', 'PCENDERECO[BLOQUEIO] = "N"']
    if rua is not None:
        fbase.append(f"PCENDERECO[RUA] = {int(rua)}")
    if lista:
        fbase.append(f"PCENDERECO[CODFILIAL] IN {lista}")
    cond = " && ".join(fbase) + ' && CALCULATE(COUNTROWS(PCESTENDERECO), PCESTENDERECO[QT] > 0) = 0'
    return f"""EVALUATE
SELECTCOLUMNS(
    FILTER(PCENDERECO, {cond}),
    "rua",     PCENDERECO[RUA],
    "predio",  PCENDERECO[PREDIO],
    "nivel",   PCENDERECO[NIVEL],
    "apto",    PCENDERECO[APTO],
    "tipo",    PCENDERECO[TIPOENDER],
    "codprod", CALCULATE(MAX(PCESTENDERECO[CODPROD])),
    "nprod",   CALCULATE(DISTINCTCOUNT(PCESTENDERECO[CODPROD]))
)"""


def q_desc_de(codprods):
    """DESCRICAO de uma lista de códigos direto do PCPRODUT (SEM filtro REVENDA/FL) —
    resolve nome de item zerado/fora-de-linha que não está no snapshot nem no cadastro."""
    lista = "{" + ",".join(str(int(c)) for c in codprods) + "}"
    return (f'EVALUATE SELECTCOLUMNS(FILTER(PCPRODUT, PCPRODUT[CODPROD] IN {lista}), '
            f'"CODPROD", PCPRODUT[CODPROD], "DESCRICAO", PCPRODUT[DESCRICAO])')


# ───────────────────────── vencidos (perda de validade) ─────────────────────────
def q_vencidos(filiais=None):
    """Itens baixados por PERDA VALIDADE (conta 200042). Grão = item da nota.

    PCMOV/PCNFSAID/PCLANC já vêm escopados na conta 200042 pela origem (Oracle),
    por isso PCMOV é o fato e não precisa filtrar conta aqui.

    ⚠️ O join é por NUMTRANSVENDA, **não** por NUMNOTA: NUMNOTA se repete ao longo
    dos anos (a nota 5548 aparece com 15 datas distintas) e juntar por ela infla o
    resultado ~3,5x (1.308 linhas vs. 377 reais). NUMTRANSVENDA é 1:1 com a nota.

    Como PCMOV/PCNFSAID/PCPRODUT/PCFORNEC/PCEMPR são ilhas (sem relacionamento no
    modelo), a costura é por LOOKUPVALUE — mesmo padrão do q_validade.
    """
    lista = _lista_filiais_dax(filiais)
    fato = f"FILTER(PCMOV, PCMOV[CODFILIAL] IN {lista})" if lista else "PCMOV"
    return f"""EVALUATE
VAR Base = ADDCOLUMNS({fato},
    "@dtsaida",   LOOKUPVALUE(PCNFSAID[DTSAIDA],   PCNFSAID[NUMTRANSVENDA], PCMOV[NUMTRANSVENDA]),
    "@descricao", LOOKUPVALUE(PCPRODUT[DESCRICAO], PCPRODUT[CODPROD], PCMOV[CODPROD]),
    "@codfornec", LOOKUPVALUE(PCPRODUT[CODFORNEC], PCPRODUT[CODPROD], PCMOV[CODPROD])
)
VAR ComForn = ADDCOLUMNS(Base,
    "@fornecedor", LOOKUPVALUE(PCFORNEC[FORNECEDOR],   PCFORNEC[CODFORNEC], [@codfornec]),
    "@codcompr",   LOOKUPVALUE(PCFORNEC[CODCOMPRADOR], PCFORNEC[CODFORNEC], [@codfornec])
)
VAR Full = ADDCOLUMNS(ComForn,
    "@comprador", LOOKUPVALUE(PCEMPR[NOME], PCEMPR[MATRICULA], [@codcompr])
)
RETURN
SELECTCOLUMNS(Full,
    "dtsaida",      [@dtsaida],
    "numnota",      PCMOV[NUMNOTA],
    "codprod",      PCMOV[CODPROD],
    "descricao",    [@descricao],
    "qt",           PCMOV[QT],
    "punit",        PCMOV[PUNIT],
    "total",        PCMOV[QT] * PCMOV[PUNIT],
    "codfornec",    [@codfornec],
    "fornecedor",   [@fornecedor],
    "codcomprador", [@codcompr],
    "comprador",    [@comprador],
    "codfilial",    PCMOV[CODFILIAL]
)"""
