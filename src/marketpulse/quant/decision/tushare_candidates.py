"""Versioned Tushare originals adapted to the existing candidate calculation contract.

Views below exist only in memory. Evidence locators always address the original
Tushare fields/items table, not these calculation views. No EM response is archived.
"""

import re
from datetime import UTC, datetime
from decimal import Decimal

from ..live.securities import financial_type
from . import tushare
from .candidate_sources import checked, collect_originals, report_date
from .next_session.calendar import load_calendar
from .sector_profiles import profile
from .service import SHANGHAI
from .tools import SourceClient, SourceUnavailable

LEGACY_VERSION = "candidate-tushare-sw2021-2026-10-14-v1"
VERSION = "candidate-tushare-sw2021-2026-10-14-v2"
VERSIONS = {LEGACY_VERSION, VERSION}
FINANCIAL = {
    "income": (
        "income",
        {"total_revenue": "TOTAL_OPERATE_INCOME", "n_income_attr_p": "PARENT_NETPROFIT"},
    ),
    "balance": (
        "balancesheet",
        {"total_assets": "TOTAL_ASSETS", "total_liab": "TOTAL_LIABILITIES"},
    ),
    "cash": ("cashflow", {"n_cashflow_act": "NETCASH_OPERATE"}),
}


def collect(repo, state, stop, save, step):
    entity = state["request"]["entity"]
    client = SourceClient(repo, state, save, stop, max_requests=160)
    snapshot = dict(
        version=VERSION,
        industry_profile=profile(entity),
        entries=[],
        documents=[],
        subjects=[],
        gaps=[],
        excluded_market_members=[],
        depth_limit=state["request"]["budget_profile"]["max_candidates"],
    )

    def ts(role, api, params):
        source, values = tushare.fetch(client, role, api, params)
        snapshot["entries"].append(dict(role=role, source=source))
        return values

    def fetch(role, key, url, **kwargs):
        source, packet = client.fetch(key, url, **kwargs)
        snapshot["entries"].append(dict(role=role, source=source))
        return source, packet

    step("pool", "读取申万2021" + entity + "成分；按明确沪深范围取数")
    catalog = ts("classification", "index_classify", dict(level="L3", src="SW2021"))
    matches = [r for r in catalog if r["industry_name"] == entity]
    if len(matches) != 1:
        raise SourceUnavailable("申万行业分类缺失或冲突")
    snapshot["board_id"] = matches[0]["index_code"]
    master = ts("master", "stock_basic", dict(exchange="", list_status="L"))
    members = ts("membership", "index_member_all", dict(l3_code=snapshot["board_id"], is_new="Y"))
    if not members or len(members) >= 2000 or len({r["ts_code"] for r in members}) != len(members):
        raise SourceUnavailable("申万成分不完整或重复")
    identities = {r["ts_code"]: r for r in master}
    eligible = []
    for member in members:
        sid = member["ts_code"]
        if sid.endswith(".BJ"):
            snapshot["excluded_market_members"].append(
                dict(security_id=sid, name=member["name"], reason="本期只覆盖沪深A股")
            )
            continue
        if (
            not sid.endswith((".SH", ".SZ"))
            or sid not in identities
            or not tushare.same_security_name(identities[sid]["name"], member["name"])
        ):
            raise SourceUnavailable("成分与证券主数据身份冲突")
        eligible.append(member)
    if not eligible or len(eligible) > 40:
        raise SourceUnavailable("完整行业池超出本期有界预算，未截断")
    snapshot["calendar"] = load_calendar(client, "SSE").model_dump(mode="json")
    local = datetime.now(UTC).astimezone(SHANGHAI)
    snapshot["market_at"] = local.isoformat()
    from .tushare_market import ready_sessions

    snapshot["sessions"] = ready_sessions(snapshot["calendar"], local)
    snapshot["period"] = report_date(local.date())
    if len(snapshot["sessions"]) != 21:
        raise SourceUnavailable("共同窗口不足")
    step("pool", f"封存{len(eligible)}只沪深成分，逐只读取同期间报表与共同窗口")
    for member in eligible:
        sid, code = member["ts_code"], member["ts_code"][:6]
        ts(
            "daily:" + code,
            "daily",
            dict(
                ts_code=sid,
                start_date=snapshot["sessions"][0].replace("-", ""),
                end_date=snapshot["sessions"][-1].replace("-", ""),
            ),
        )
        ts(
            "valuation:" + code,
            "daily_basic",
            dict(ts_code=sid, trade_date=snapshot["sessions"][-1].replace("-", "")),
        )
        for role, (api, _) in FINANCIAL.items():
            ts(
                role + ":" + sid,
                api,
                dict(
                    ts_code=sid,
                    period=snapshot["period"].replace("-", ""),
                    report_type="1",
                    comp_type="1",
                ),
            )
    return collect_originals(repo, state, snapshot, client, save, step, fetch, local)


def entries(repo, snapshot):
    """Provide deterministic calculation views while retaining actual raw source bindings."""
    result = {}
    for entry in snapshot["entries"]:
        role, source = entry["role"], entry["source"]
        raw = checked(repo, source, snapshot["cutoff_at"])
        if source["source_family"] != "TUSHARE":
            result[role] = (source, raw)
            continue
        values = tushare.records(raw)
        if role == "classification":
            if (
                not values
                or len(values) >= 2000
                or any(r["level"] != "L3" or r["src"] != "SW2021" for r in values)
            ):
                raise ValueError("mixed or incomplete SW classification")
            value = dict(
                data=dict(diff=[dict(f12=r["index_code"], f14=r["industry_name"]) for r in values])
            )
        elif role == "membership":
            if (
                not values
                or len(values) >= 2000
                or any(
                    r["l3_code"] != snapshot["board_id"] or r["is_new"] != "Y" or r.get("out_date")
                    for r in values
                )
            ):
                raise ValueError("invalid SW membership validity")
            excluded = [
                dict(security_id=r["ts_code"], name=r["name"], reason="本期只覆盖沪深A股")
                for r in values
                if r["ts_code"].endswith(".BJ")
            ]
            if excluded != snapshot["excluded_market_members"]:
                raise ValueError("declared market exclusions changed")
            included = [r for r in values if r["ts_code"].endswith((".SH", ".SZ"))]
            value = dict(
                data=dict(
                    total=len(included),
                    diff=[
                        dict(
                            f12=r["ts_code"][:6],
                            f13=1 if r["ts_code"].endswith(".SH") else 0,
                            f14=r["name"],
                        )
                        for r in included
                    ],
                )
            )
        elif role.startswith("daily:"):
            sid = source["parameters"]["ts_code"]
            if len(values) >= 6000 or any(r["ts_code"] != sid for r in values):
                raise ValueError("daily identity/truncation mismatch")
            symbol = ("sh" if sid.endswith(".SH") else "sz") + sid[:6]
            # Existing calculation reads amount in ten-thousand CNY. Tushare is thousand CNY.
            value = dict(
                data={
                    symbol: dict(
                        day=[
                            [
                                iso(r["trade_date"]),
                                r["open"],
                                r["close"],
                                r["high"],
                                r["low"],
                                r["vol"],
                                {},
                                0,
                                str(Decimal(str(r["amount"])) / 10),
                            ]
                            for r in values
                        ]
                    )
                }
            )
        elif role.split(":")[0] in FINANCIAL:
            mapping = FINANCIAL[role.split(":")[0]][1]
            if any(str(r["report_type"]) != "1" or str(r["comp_type"]) != "1" for r in values):
                raise ValueError("financial statement basis differs")
            value = dict(
                result=dict(
                    data=[
                        dict(
                            SECUCODE=r["ts_code"],
                            REPORT_DATE=iso(r["end_date"]),
                            NOTICE_DATE=iso(r.get("f_ann_date") or r["ann_date"]),
                            PROVIDER_UPDATE_FLAG=r.get("update_flag"),
                            **{target: r.get(original) for original, target in mapping.items()},
                        )
                        for r in values
                    ]
                )
            )
        else:
            value = raw
        result[role] = (source, value)
    for member in result["membership"][1]["data"]["diff"]:
        code = member["f12"]
        valuation = result["valuation:" + code]
        actual = tushare.records(valuation[1])
        sid = code + (".SH" if member["f13"] == 1 else ".SZ")
        if (
            len(actual) != 1
            or actual[0]["ts_code"] != sid
            or iso(actual[0]["trade_date"]) != snapshot["sessions"][-1]
        ):
            raise ValueError("valuation identity/date conflict")
        r = actual[0]
        member.update(
            f20=str(Decimal(str(r["total_mv"])) * 10000) if r.get("total_mv") is not None else None,
            f9=r.get("pe_ttm"),
        )
    return result


def iso(value):
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise ValueError("invalid Tushare date")
    return value[:4] + "-" + value[4:6] + "-" + value[6:]


def locator(repo, source, field, row=0):
    raw = json_packet(repo, source)
    originals = {"market_cap": "total_mv", "pe": "pe_ttm"}
    originals.update(
        {
            target: original
            for _, fields in FINANCIAL.values()
            for original, target in fields.items()
        }
    )
    return f"/data/items/{row}/{raw['data']['fields'].index(originals[field])}"


def json_packet(repo, source):
    import json

    return json.loads(repo.raw_bytes(source["blob_ref"]))


def verified_numeric(repo, item):
    """Bind unit conversion to the original field, issuer and protocol, not free text."""
    from .market import number

    raw = json_packet(repo, dict(blob_ref=item.raw_ref))
    match = re.fullmatch(r"/data/items/(\d+)/(\d+)", item.locator.path)
    if not match or item.method_version not in VERSIONS:
        raise ValueError("unknown Tushare numeric protocol")
    row_index, column = map(int, match.groups())
    row = tushare.records(raw)[row_index]
    field = raw["data"]["fields"][column]
    outputs = {"total_mv": "market_cap", "pe_ttm": "pe"}
    outputs.update(
        {
            original: target
            for _, mapping in FINANCIAL.values()
            for original, target in mapping.items()
        }
    )
    if (
        row["ts_code"] != item.subject_id
        or field not in outputs
        or item.evidence_id != "EV-CAND-" + item.subject_id + "-" + outputs[field]
    ):
        raise ValueError("Tushare numeric issuer/field binding mismatch")
    return number(row[field]) * (10000 if field == "total_mv" else 1)


def select_revision(matches, cutoff):
    """The provider documents update_flag=1 as latest. Preserve original row indices."""
    available = [(i, r) for i, r in matches if r["NOTICE_DATE"] <= cutoff[:10]]
    if len(available) <= 1:
        return available
    latest = [(i, r) for i, r in available if str(r.get("PROVIDER_UPDATE_FLAG")) == "1"]
    return latest if len(latest) == 1 else available


def resolve_member(repo, snapshot, client, row):
    sid = row["security_id"]
    source, values = tushare.fetch(client, "company", "stock_company", dict(ts_code=sid))
    if len(values) != 1 or values[0]["ts_code"] != sid:
        raise ValueError("company profile identity conflict")
    snapshot["entries"].append(dict(role="profile:" + row["code"], source=source))
    description = " ".join(
        str(values[0].get(k) or "") for k in ("main_business", "introduction", "business_scope")
    )
    entity = snapshot["industry_profile"]["entity"]
    return dict(
        code=row["code"],
        security_id=sid,
        name=row["name"],
        symbol=("sh" if sid.endswith(".SH") else "sz") + row["code"],
        exchange="SSE" if sid.endswith(".SH") else "SZSE",
        industry=entity,
        company_type=financial_type(entity),
        description=description[:1500],
    )
