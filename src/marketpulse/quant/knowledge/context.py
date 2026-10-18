"""Frozen market, funding and official-policy context; no private account data."""

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from ..contracts import digest
from ..decision import tushare
from ..decision.tools import SourceUnavailable, Stopped
from ..live.main_flow import QQ, minute_projection
from ..live.market_dashboard import SHANGHAI
from . import context_policies as policies

VERSION = "investment-context-v2"


def daily(rows, sid, sessions):
    allowed = set(sessions)
    by_date = {}
    for row in rows:
        day = row["trade_date"]
        if row["ts_code"] != sid or day not in allowed or day in by_date:
            raise ValueError("资金证券、交易日或重复记录不一致")
        values = [
            Decimal(str(row[k]))
            for k in ("buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount")
        ]
        if any(not v.is_finite() or v < 0 for v in values):
            raise ValueError("资金字段缺失或无效")
        buy_lg, sell_lg, buy_elg, sell_elg = values
        by_date[day] = dict(date=day, main=str((buy_lg - sell_lg + buy_elg - sell_elg) * 10000))
    points = [by_date[d] for d in sessions if d in by_date]
    windows = []
    for width in (5, 20):
        required = sessions[-width:]
        complete = len(required) == width and all(d in by_date for d in required)
        windows.append(
            dict(
                sessions=width,
                complete=complete,
                value=str(sum(Decimal(by_date[d]["main"]) for d in required)) if complete else None,
            )
        )
    return dict(
        provider="Tushare",
        granularity="daily",
        method="TS_LARGE_EXTRA_NET_V1",
        definition="大单与特大单买入减卖出，万元转元；供应商主力代理，非机构身份识别",
        points=points,
        windows=windows,
        expected_sessions=sessions,
        missing_sessions=sorted(allowed - set(by_date)),
        asof=points[-1]["date"] if points else None,
    )


def source_check(repo, source, cutoff, *, url=None):
    raw = repo.raw_bytes(source["blob_ref"])
    if (
        hashlib.sha256(raw).hexdigest() != source["sha256"]
        or source["blob_ref"] != "blob://sha256/" + source["sha256"]
    ):
        raise ValueError("上下文原文哈希不一致")
    if datetime.fromisoformat(source["fetched_at"]) > datetime.fromisoformat(cutoff):
        raise ValueError("上下文来源晚于封存截止")
    if url and source["url"] != url:
        raise ValueError("上下文来源地址不一致")
    return raw


def endpoint(url, expected):
    actual, target = urlsplit(url), urlsplit(expected)
    return (
        actual.scheme == target.scheme == "https"
        and actual.netloc == target.netloc
        and actual.path.rstrip("/") == target.path.rstrip("/")
        and not actual.fragment
    )


def collect(repo, client, packet):
    snapshot = dict(version=VERSION, market_date=packet["market_date"], entries=[], gaps=[])
    for stock in packet["stocks"]:
        sid = stock["ts_code"]
        entries = []

        def fetch(role, api, params, sid=sid, entries=entries):
            source, rows = tushare.fetch(client, "context_" + role + "_" + sid, api, params)
            entries.append(dict(kind=role, source=source))
            return rows

        try:
            fetch("business", "stock_company", dict(ts_code=sid))
        except Stopped:
            raise
        except SourceUnavailable:
            snapshot["gaps"].append(
                dict(
                    security_id=sid,
                    channel="BUSINESS",
                    reason="公司业务资料未取得，行业连接仅作待核线索",
                )
            )
        sessions = sorted(r["trade_date"] for r in stock["daily"])[-20:]
        try:
            fetch(
                "daily",
                "moneyflow",
                dict(ts_code=sid, start_date=sessions[0], end_date=sessions[-1]),
            )
        except Stopped:
            raise
        except SourceUnavailable:
            snapshot["gaps"].append(
                dict(security_id=sid, channel="DAILY_FLOW", reason="本次日频资金未取得")
            )
        symbol = ("sh" if sid.endswith(".SH") else "sz") + sid[:6]
        try:
            source, _ = client.fetch(
                "context_minute_" + sid,
                QQ,
                params=dict(
                    code=symbol,
                    type="historyFundFlow,todayFundFlow,todayFundTrend",
                    klineNeedDay=20,
                ),
                family="TENCENT",
            )
            entries.append(dict(kind="minute", source=source))
        except Stopped:
            raise
        except SourceUnavailable:
            snapshot["gaps"].append(
                dict(
                    security_id=sid,
                    channel="MINUTE_FLOW",
                    reason="本次分钟资金未取得，不能以日频代替",
                )
            )
        snapshot["entries"].append(dict(security_id=sid, entries=entries))
    snapshot["captured_at"] = datetime.now(UTC).isoformat()
    subjects = rebuild(repo, packet, snapshot)["stocks"]
    fetched = {}
    for record in snapshot["entries"]:
        subject = next(s for s in subjects if s["security_id"] == record["security_id"])
        for pid in policies.selected(subject):
            spec = policies.SOURCES[pid]
            try:
                if pid not in fetched:
                    source, _ = client.fetch(
                        "context_policy_" + pid, spec["url"], family=spec["family"], binary=True
                    )
                    fetched[pid] = source
                record["entries"].append(dict(kind="policy", policy_id=pid, source=fetched[pid]))
            except Stopped:
                raise
            except SourceUnavailable:
                snapshot["gaps"].append(
                    dict(
                        security_id=record["security_id"],
                        channel="POLICY",
                        policy_id=pid,
                        reason="官方原文未取得",
                    )
                )
    snapshot["captured_at"] = datetime.now(UTC).isoformat()
    return snapshot


def rebuild(repo, packet, snapshot):
    legacy_url_rules = snapshot["version"] == "investment-context-v1"
    if (
        snapshot["version"] not in {VERSION, "investment-context-v1"}
        or snapshot["market_date"] != packet["market_date"]
    ):
        raise ValueError("上下文版本或行情日期不一致")
    stocks = {s["ts_code"]: s for s in packet["stocks"]}
    if [s["security_id"] for s in snapshot["entries"]] != list(stocks):
        raise ValueError("上下文证券身份不一致")
    result = dict(
        version=snapshot["version"],
        cutoff=snapshot["captured_at"],
        stocks=[],
        sources=[],
        gaps=copy.deepcopy(snapshot["gaps"]),
    )
    for record in snapshot["entries"]:
        sid = record["security_id"]
        stock = stocks[sid]
        subject = dict(
            security_id=sid,
            industry=stock.get("industry", ""),
            main_business="",
            business_source=None,
            daily=None,
            minute=None,
            policies=[],
        )
        seen = set()
        ordered = sorted(record["entries"], key=lambda e: e["kind"] == "policy")
        for entry in ordered:
            kind = entry["kind"]
            unique = (kind, entry.get("policy_id"))
            if unique in seen or kind not in {"business", "daily", "minute", "policy"}:
                raise ValueError("重复或未知上下文来源")
            seen.add(unique)
            source = entry["source"]
            raw = source_check(repo, source, snapshot["captured_at"])
            if source not in result["sources"]:
                result["sources"].append(source)
            try:
                if kind in {"business", "daily"}:
                    api = "stock_company" if kind == "business" else "moneyflow"
                    if (
                        source.get("api_name") != api
                        or source.get("parameters", {}).get("ts_code") != sid
                        or source["source_family"] != "TUSHARE"
                        or not endpoint(source["url"], tushare.URL)
                        or urlsplit(source["url"]).query
                        or (legacy_url_rules and source["url"] != tushare.URL)
                    ):
                        raise ValueError("上下文数据请求身份不一致")
                    rows = tushare.records(json.loads(raw))
                    if kind == "business":
                        if len(rows) != 1 or rows[0]["ts_code"] != sid:
                            raise ValueError("公司业务身份未核对")
                        subject.update(
                            main_business=(rows[0].get("main_business") or "")[:1500],
                            business_source=source,
                        )
                    else:
                        sessions = sorted(r["trade_date"] for r in stock["daily"])[-20:]
                        subject["daily"] = dict(daily(rows, sid, sessions), source=source)
                elif kind == "minute":
                    symbol = ("sh" if sid.endswith(".SH") else "sz") + sid[:6]
                    query = parse_qs(urlsplit(source["url"]).query)
                    if (
                        not endpoint(source["url"], QQ)
                        or source["source_family"] != "TENCENT"
                        or (legacy_url_rules and source["url"] != QQ)
                        or query
                        != dict(
                            code=[symbol],
                            type=["historyFundFlow,todayFundFlow,todayFundTrend"],
                            klineNeedDay=["20"],
                        )
                    ):
                        raise ValueError("分钟资金来源身份不一致")
                    view = minute_projection(
                        json.loads(raw),
                        symbol,
                        datetime.fromisoformat(source["fetched_at"]).astimezone(SHANGHAI),
                    )
                    stamp = datetime.fromisoformat(view["asof"])
                    cutoff = datetime.fromisoformat(snapshot["captured_at"]).astimezone(SHANGHAI)
                    clock = cutoff.hour * 60 + cutoff.minute
                    active = cutoff.weekday() < 5 and (570 <= clock <= 690 or 780 <= clock <= 900)
                    view["freshness"] = (
                        "STALE"
                        if stamp.date() != cutoff.date()
                        else "DELAYED"
                        if active and cutoff - stamp > timedelta(minutes=3)
                        else "LIVE"
                        if active
                        else "LATEST_MINUTE"
                    )
                    view.update(
                        source=source,
                        history_basis="本次实际取得的供应商当日序列，非系统此前连续监测历史",
                    )
                    subject["minute"] = view
                else:
                    pid = entry["policy_id"]
                    if pid not in policies.selected(subject):
                        raise ValueError("政策与公司业务连接不一致")
                    spec = policies.SOURCES[pid]
                    if source["url"] != spec["url"] or source["source_family"] != spec["family"]:
                        raise ValueError("政策官方来源身份不一致")
                    subject["policies"].append(
                        dict(
                            policies.parse(
                                raw, pid, source["fetched_at"], policies.category(subject)
                            ),
                            source=source,
                        )
                    )
            except (ValueError, KeyError, TypeError, UnicodeError) as error:
                result["gaps"].append(
                    dict(
                        security_id=sid,
                        channel=kind.upper(),
                        reason="原文或口径未通过核对",
                        detail=str(error)[:160],
                    )
                )
        result["stocks"].append(subject)
    result["digest"] = digest(snapshot)
    return result


def enrich(context, evidence, report):
    result, facts = copy.deepcopy(context), copy.deepcopy(evidence)
    result.update(protocol="public-investment-context-v2", cutoff=report["cutoff"])
    result["methods"] = [
        {
            k: (v[:180] if k == "quote" else v[:1] if k == "counterexamples" else v)
            for k, v in method.items()
            if k != "adaptation"
        }
        for method in result["methods"]
    ]
    for stock in result["stocks"]:
        for route in stock["routes"]:
            route["conditions"] = [
                {k: v for k, v in condition.items() if k != "observed"}
                for condition in route["conditions"]
            ]
    result["limitations"] = [
        "资金是供应商主力代理，不识别机构身份；日频与分钟分列，不能相加或凭共时变动认定因果。",
        "政策资格及修订覆盖未核定，公司受益与受限均为待核假设；无完整估值、新闻和独立财报审计。",
        "原始资料仅用于本次研究，首次取得不是历史连续监测；方法效果未验证。",
    ]
    for view in report["stocks"]:
        sid = view["security_id"]
        stock = next(s for s in result["stocks"] if s["security_id"] == sid)
        stock["external"] = dict(
            business_basis="公开主营范围，供应商转录，未独立核对公司披露"
            if view["main_business"]
            else "行业线索，业务原文缺项",
            daily_asof=view["daily"]["asof"] if view["daily"] else None,
            minute_asof=view["minute"]["asof"] if view["minute"] else None,
            minute_freshness=view["minute"]["freshness"] if view["minute"] else None,
            policy_scope="只覆盖本次定向官方原文，非完整政策库",
        )

        def add(key, label, kind, value, unit, window, source, *, text=None, state=None, sid=sid):
            fact = dict(
                evidence_id="X-" + sid + "-" + key,
                security_id=sid,
                label=label,
                kind=kind,
                value=value,
                unit=unit,
                window=window,
                source_refs=[source["blob_ref"]],
                formula=label,
            )
            if text:
                fact["text"] = text
            if state:
                fact["policy_state"] = state
            facts.append(fact)
            result["evidence"].append(
                {k: v for k, v in fact.items() if k not in {"formula", "source_refs"}}
            )

        if view["main_business"]:
            add(
                "business",
                "公开主营范围",
                "BUSINESS_TEXT",
                None,
                "text",
                report["cutoff"],
                view["business_source"],
                text=view["main_business"][:300],
            )
        if view["daily"]:
            daily_view = view["daily"]
            for w in daily_view["windows"]:
                if w["complete"]:
                    add(
                        "daily-" + str(w["sessions"]),
                        str(w["sessions"]) + "个交易日大单与特大单净额合计",
                        "DAILY_FLOW",
                        w["value"],
                        "CNY",
                        daily_view["expected_sessions"][-w["sessions"]]
                        + "—"
                        + daily_view["expected_sessions"][-1],
                        daily_view["source"],
                    )
        if view["minute"] and view["minute"]["freshness"] not in {"STALE", "DELAYED"}:
            m = view["minute"]
            add(
                "minute",
                "供应商当日累计主力代理净额",
                "MINUTE_FLOW",
                str(m["latest"]["main"]),
                "CNY",
                m["asof"],
                m["source"],
            )
        for p in view["policies"]:
            if p["temporal_status"] in {"REPEALED", "CONSULTATION", "NOT_YET_EFFECTIVE", "EXPIRED"}:
                continue
            state = {
                k: p[k]
                for k in (
                    "temporal_status",
                    "current_validity",
                    "effective_at",
                    "declared_scope_end",
                    "amendment_status",
                    "qualification_status",
                    "company_link_status",
                )
            }
            add(
                p["policy_id"],
                p["title"],
                "POLICY_CLAUSE",
                None,
                "text",
                p["published_at"] or report["cutoff"],
                p["source"],
                text=p["clauses"][0]["text"][:200],
                state=state,
            )
    result["evidence"].sort(key=lambda f: f["evidence_id"])
    facts.sort(key=lambda f: f["evidence_id"])
    return result, facts


def verify(repo, state):
    from .research_context import public_context

    snapshot = repo.value(state["context_snapshot_ref"])
    packet = repo.value(state["inputs_ref"])
    report = rebuild(repo, packet, snapshot)
    if (
        report != repo.value(state["context_report_ref"])
        or report != state["data"]["external_context"]
    ):
        raise ValueError("上下文报告不等于原文复算")
    base, facts = public_context(
        repo.value(state["core_ref"]),
        packet,
        repo.value(state["knowledge_ref"]),
        state["request"]["horizon_sessions"],
    )
    expected, evidence = enrich(base, facts, report)
    if state["request"]["schema_version"] in {"investment-research-v3", "investment-research-v4"}:
        from . import technicals

        technical = technicals.compute(packet)
        if (
            technical != repo.value(state["technicals_ref"])
            or technical != state["data"]["technicals"]
        ):
            raise ValueError("技术指标不等于封存行情复算")
        expected, evidence = technicals.enrich(expected, evidence, technical, packet)
    if expected != repo.value(state["public_context_ref"]) or evidence != state["data"]["evidence"]:
        raise ValueError("模型上下文或证据不等于原文复算")
    return report
