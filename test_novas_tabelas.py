"""
Teste/descoberta de tabelas novas no dataset Power BI.

Uso:
    # 1) Listar TODAS as tabelas + nº de colunas (pra achar as novas)
    python -X utf8 test_novas_tabelas.py

    # 2) Amostrar as primeiras N linhas de uma ou mais tabelas
    python -X utf8 test_novas_tabelas.py PCPRODUT PCFORNEC
    python -X utf8 test_novas_tabelas.py "PCPRODUT" --linhas 20

Roda standalone (não importa server.py / não sobe Flask).
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

load_dotenv()

CONFIG = {
    'tenant_id':     os.getenv('POWERBI_TENANT_ID', ''),
    'client_id':     os.getenv('POWERBI_CLIENT_ID', ''),
    'client_secret': os.getenv('POWERBI_CLIENT_SECRET', ''),
    'dataset_id':    os.getenv('POWERBI_DATASET_ID', ''),
    'group_id':      os.getenv('POWERBI_GROUP_ID', ''),
}


def get_token():
    url = f"https://login.microsoftonline.com/{CONFIG['tenant_id']}/oauth2/v2.0/token"
    resp = requests.post(url, data={
        'grant_type': 'client_credentials',
        'client_id': CONFIG['client_id'],
        'client_secret': CONFIG['client_secret'],
        'scope': 'https://analysis.windows.net/powerbi/api/.default',
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()['access_token']


def execute_dax(token, query):
    url = (
        f"https://api.powerbi.com/v1.0/myorg/groups/"
        f"{CONFIG['group_id']}/datasets/{CONFIG['dataset_id']}/executeQueries"
    )
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    body = {'queries': [{'query': query}], 'serializerSettings': {'includeNulls': True}}
    resp = requests.post(url, json=body, headers=headers, timeout=120)
    if resp.status_code >= 400:
        print(f"\n[ERRO {resp.status_code}] {resp.text[:1000]}\n")
        resp.raise_for_status()
    return resp.json()


def rows_of(result):
    return result['results'][0]['tables'][0]['rows']


def short(k):
    return k.split('[')[-1].rstrip(']') if '[' in k else k


def _eh_ruido(nome):
    return (not nome or nome.startswith('LocalDateTable_')
            or nome.startswith('DateTableTemplate_'))


def listar_tabelas(token):
    # INFO.VIEW.* passa onde INFO.TABLES/COLUMNS são bloqueadas neste dataset.
    print("=" * 70)
    print("TABELAS DO MODELO  (INFO.VIEW.TABLES, sem LocalDateTable)")
    print("=" * 70)
    tab_rows = rows_of(execute_dax(token, "EVALUATE INFO.VIEW.TABLES()"))
    tabelas = []
    for r in tab_rows:
        r = {short(k): v for k, v in r.items()}
        nome = r.get('Name')
        if not _eh_ruido(nome):
            tabelas.append(nome)
    for n in sorted(tabelas, key=str.lower):
        print(f"  - {n}")

    print("\n" + "=" * 70)
    print("COLUNAS POR TABELA  (INFO.VIEW.COLUMNS)")
    print("=" * 70)
    col_rows = rows_of(execute_dax(token, "EVALUATE INFO.VIEW.COLUMNS()"))
    por_tabela = {}
    for r in col_rows:
        r = {short(k): v for k, v in r.items()}
        tab = r.get('Table')
        col = r.get('Name')
        if _eh_ruido(tab) or not col or col.startswith('RowNumber'):
            continue
        por_tabela.setdefault(tab, []).append(col)
    for nome in sorted(por_tabela, key=str.lower):
        nomes = por_tabela[nome]
        print(f"\n  {nome}  ({len(nomes)} colunas)")
        print("    " + ", ".join(nomes))


def amostrar(token, tabela, linhas):
    print("\n" + "=" * 70)
    print(f"AMOSTRA: '{tabela}'  (TOPN {linhas})")
    print("=" * 70)
    q = f"EVALUATE TOPN({linhas}, '{tabela}')"
    try:
        rows = rows_of(execute_dax(token, q))
    except requests.HTTPError:
        print("  -> Falhou. Confere o nome exato da tabela na listagem acima.")
        return
    if not rows:
        print("  (tabela vazia)")
        return
    rows = [{short(k): v for k, v in r.items()} for r in rows]
    print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))


def main():
    linhas = 10
    raw = sys.argv[1:]
    args = []
    i = 0
    while i < len(raw):
        if raw[i] == '--linhas':
            linhas = int(raw[i + 1])
            i += 2
        else:
            args.append(raw[i])
            i += 1

    token = get_token()
    print("Token OK.\n")

    if not args:
        listar_tabelas(token)
        print("\n\nPra amostrar dados: python -X utf8 test_novas_tabelas.py <NomeTabela> [<outra> ...]")
    else:
        for t in args:
            amostrar(token, t, linhas)


if __name__ == '__main__':
    main()
