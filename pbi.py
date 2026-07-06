"""
Infra Power BI / DAX para o painel de estoque (isolado).

- Lê credenciais do .env do projeto pai (../.env) — as mesmas do Multpel HTML.
- Aponta para o dataset "Estoque" (executeQueries via Service Principal).
- Cache em memória (TTL por dict) — zero dependência de Redis.
"""

import os
import time
import threading
import functools
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv

# .env local (repo standalone)
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# Datasets do workspace (default; sobrescrevíveis via env)
DATASET_ESTOQUE_DEFAULT = "32fb60e1-5ff1-47ae-9472-b7ce7049f2ce"
DATASET_RCA_DEFAULT = "f2fbf288-611a-4b17-aeb3-a6f77ef04e3b"  # tem PCEMPR (nome do comprador)

CONFIG = {
    "tenant_id":     os.getenv("POWERBI_TENANT_ID", ""),
    "client_id":     os.getenv("POWERBI_CLIENT_ID", ""),
    "client_secret": os.getenv("POWERBI_CLIENT_SECRET", ""),
    "group_id":      os.getenv("POWERBI_GROUP_ID", ""),
    "dataset_id":    os.getenv("POWERBI_DATASET_ID_ESTOQUE", DATASET_ESTOQUE_DEFAULT),
    "dataset_rca":   os.getenv("POWERBI_DATASET_ID_RCA", DATASET_RCA_DEFAULT),
}


# ───────────────────────── cache TTL em memória ─────────────────────────
class TTLCache:
    """Cache simples thread-safe com expiração por chave."""

    def __init__(self):
        self._store = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            value, expira_em = item
            if time.time() > expira_em:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key, value, ttl):
        with self._lock:
            self._store[key] = (value, time.time() + ttl)

    def clear(self):
        with self._lock:
            self._store.clear()


_CACHE = TTLCache()


def cached(ttl, key_fn):
    """Decorator: memoiza o retorno da função no TTLCache. key_fn(*args, **kwargs) -> str."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs)
            hit = _CACHE.get(key)
            if hit is not None:
                return hit
            val = fn(*args, **kwargs)
            _CACHE.set(key, val, ttl)
            return val
        return wrapper
    return deco


# ───────────────────────── token Power BI ─────────────────────────
def get_token():
    cached_tok = _CACHE.get("pbi:token")
    if cached_tok:
        return cached_tok
    url = f"https://login.microsoftonline.com/{CONFIG['tenant_id']}/oauth2/v2.0/token"
    resp = requests.post(url, data={
        "grant_type": "client_credentials",
        "client_id": CONFIG["client_id"],
        "client_secret": CONFIG["client_secret"],
        "scope": "https://analysis.windows.net/powerbi/api/.default",
    }, timeout=30)
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _CACHE.set("pbi:token", token, 3000)  # ~50min
    return token


# ───────────────────────── executeQueries ─────────────────────────
def _execute(token, query, dataset_id=None):
    ds = dataset_id or CONFIG["dataset_id"]
    url = (
        f"https://api.powerbi.com/v1.0/myorg/groups/"
        f"{CONFIG['group_id']}/datasets/{ds}/executeQueries"
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"queries": [{"query": query}], "serializerSettings": {"includeNulls": True}}
    resp = requests.post(url, json=body, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()


def _retry(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ultima = None
        for tent in range(3):
            try:
                return fn(*args, **kwargs)
            except requests.HTTPError as e:
                ultima = e
                code = e.response.status_code
                msg = (e.response.text or "").lower()
                if "refresh" in msg or "processing" in msg:
                    time.sleep(20)
                elif code in (401, 429, 502, 503, 504):
                    if code == 401:
                        _CACHE.set("pbi:token", None, 0)  # força renovar
                    time.sleep(2 ** tent)
                else:
                    raise
        raise ultima
    return wrapper


def _short(k):
    """'PCEST[QTEST]' -> 'QTEST'; '[Value1]' -> 'Value1'."""
    return k.split("[")[-1].rstrip("]") if "[" in k else k


def _rows(result):
    raw = result["results"][0]["tables"][0]["rows"]
    return [{_short(k): v for k, v in row.items()} for row in raw]


@_retry
def run_dax(query, dataset_id=None):
    """Roda uma query DAX e devolve lista de dicts com chaves curtas."""
    token = get_token()
    return _rows(_execute(token, query, dataset_id))


def run_dax_rca(query):
    """Roda uma query DAX no dataset RCA (ex.: PCEMPR p/ nome do comprador)."""
    return run_dax(query, dataset_id=CONFIG["dataset_rca"])


def run_dax_paralelo(queries: dict, max_workers: int = 4) -> dict:
    """Roda várias queries em paralelo. {nome: query} -> {nome: rows}."""
    token = get_token()

    @_retry
    def _one(q):
        return _rows(_execute(token, q))

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {nome: ex.submit(_one, q) for nome, q in queries.items()}
        return {nome: f.result() for nome, f in futs.items()}


# ───────────────────────── última atualização do dataset (refresh history) ─────────────────────────
def _para_brasilia(iso_utc):
    """'2026-07-06T09:24:30.147Z' (UTC) -> datetime em America/Sao_Paulo (ou None)."""
    s = (iso_utc or "").replace("Z", "").split(".")[0]  # descarta fração de segundos
    try:
        dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("America/Sao_Paulo"))
    except Exception:
        return dt.astimezone(timezone(timedelta(hours=-3)))  # Brasil é UTC-3 o ano todo


def get_dataset_refresh(dataset_id=None):
    """Última atualização concluída do dataset via API REST do Power BI (refresh history).
    Retorna {'end','end_fmt','in_progress'} ou None (degrada se a API não responder)."""
    ds = dataset_id or CONFIG["dataset_id"]
    key = f"pbi:refresh:{ds}"
    hit = _CACHE.get(key)
    if hit is not None:
        return hit or None
    out = None
    try:
        token = get_token()
        url = (f"https://api.powerbi.com/v1.0/myorg/groups/{CONFIG['group_id']}"
               f"/datasets/{ds}/refreshes?$top=10")
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        resp.raise_for_status()
        rows = resp.json().get("value", [])
        # refresh rodando agora: status não-final e sem endTime
        in_progress = any(not r.get("endTime")
                          and (r.get("status") or "").lower() in ("unknown", "inprogress", "notstarted")
                          for r in rows)
        last = next((r for r in rows if r.get("status") == "Completed" and r.get("endTime")), None)
        dtloc = _para_brasilia(last["endTime"]) if last else None
        if dtloc:
            out = {"end": dtloc.isoformat(), "end_fmt": dtloc.strftime("%d/%m/%Y %H:%M"),
                   "in_progress": in_progress}
    except Exception as e:
        print(f"[pbi] refresh history indisponível ({e}).")
    _CACHE.set(key, out or False, 300)  # cache 5min; False = 'consultado, sem dado'
    return out
