"""Read-only Tushare transport definitions and private, verified source cache.

Credentials are loaded at the transport boundary, never in request snapshots.
"""

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

from ..contracts import digest

URL = "https://api.tushare.pro"
FIELDS = {
    "suspend_d": "ts_code,trade_date,suspend_timing,suspend_type",
    "moneyflow": (
        "ts_code,trade_date,buy_lg_amount,sell_lg_amount,"
        "buy_elg_amount,sell_elg_amount,net_mf_amount"
    ),
    "stock_company": "ts_code,exchange,introduction,main_business,business_scope",
    "stock_basic": "ts_code,name,exchange,list_date,delist_date,market,list_status",
    "trade_cal": "exchange,cal_date,is_open,pretrade_date",
    "index_classify": "index_code,industry_name,parent_code,level,industry_code,src",
    "index_member_all": (
        "l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name,in_date,out_date,is_new"
    ),
    "daily": "ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
    "adj_factor": "ts_code,trade_date,adj_factor",
    "index_daily": "ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
    "daily_basic": "ts_code,trade_date,close,pe_ttm,pb,total_mv,circ_mv,total_share",
    "margin_detail": "trade_date,ts_code,rzye,rzmre,rzche",
    "income": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,"
        "total_revenue,n_income_attr_p,update_flag"
    ),
    "balancesheet": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,"
        "total_assets,total_liab,update_flag"
    ),
    "cashflow": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,n_cashflow_act,update_flag"
    ),
}

# New research contracts opt into these fields; old requests keep their bytes.
KNOWLEDGE_FIELDS = {
    "stock_basic": FIELDS["stock_basic"] + ",industry",
    "income": FIELDS["income"] + ",n_income",
    "balancesheet": FIELDS["balancesheet"] + ",total_hldr_eqy_exc_min_int",
}


def load_token():
    path = Path(os.getenv("MARKETPULSE_ENV_FILE", ".env"))
    values = {**dotenv_values(path, interpolate=False), **os.environ}
    return (values.get("TUSHARE_TOKEN") or "").strip()


def same_security_name(left, right):
    """Only exchange display markers, never ST or arbitrary company renames."""

    def base(value):
        value = re.sub(r"^(?:N|C)(?=[\u4e00-\u9fff])", "", value)
        return re.sub(r"-(?:UW|U|W)$", "", value)

    return base(left) == base(right)


def records(packet):
    if packet.get("code") != 0:
        raise ValueError("Tushare API rejected the read; response text is not logged")
    data = packet.get("data") or {}
    fields, items = data.get("fields"), data.get("items")
    if (
        not isinstance(fields, list)
        or not fields
        or len(fields) != len(set(fields))
        or not all(isinstance(f, str) for f in fields)
        or not isinstance(items, list)
    ):
        raise ValueError("invalid Tushare table")
    return [dict(zip(fields, row, strict=True)) for row in items]


def fetch(client, key, api, params, *, fields=None):
    if api not in FIELDS:
        raise ValueError("unsupported read-only Tushare API")
    source, packet = client.fetch(
        "tushare_" + api + "_" + digest(params)[:12],
        URL,
        data=dict(api_name=api, params=params, fields=fields or FIELDS[api]),
        family="TUSHARE",
    )
    return source, records(packet)


def request_body(definition):
    public = definition["data"]
    if (
        definition["family"] != "TUSHARE"
        or definition["url"] != URL
        or set(public) != {"api_name", "params", "fields"}
        or public["api_name"] not in FIELDS
        or public["fields"] not in (
            FIELDS[public["api_name"]], KNOWLEDGE_FIELDS.get(public["api_name"])
        )
    ):
        raise ValueError("unapproved Tushare request")
    token = load_token()
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured locally")
    return dict(public, token=token)


def validate_response(raw, token):
    # Never archive a service error or an unexpectedly echoed credential.
    if token and token.encode() in raw:
        raise ValueError("credential reflected by upstream; body not archived")
    records(json.loads(raw))


def cache_path(repo, key):
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("invalid cache identity")
    return repo.base.legacy_root.parent / "tushare-source-cache" / (key + ".json")


def cached(repo, definition):
    path = cache_path(repo, digest(definition))
    if not path.is_file():
        return None
    entry = json.loads(path.read_text(encoding="utf8"))
    if entry["request"] != definition:
        raise ValueError("Tushare cache request identity changed")
    source = entry["source"]
    fetched = datetime.fromisoformat(source["fetched_at"])
    now = datetime.now(UTC)
    if fetched > now or now - fetched > timedelta(hours=6):
        return None
    raw = repo.raw_bytes(source["blob_ref"])
    import hashlib

    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("Tushare cache hash mismatch")
    records(json.loads(raw))
    return source, raw


def remember(repo, definition, source):
    path = cache_path(repo, digest(definition))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(dict(request=definition, source=source)), encoding="utf8")
    os.replace(temporary, path)
