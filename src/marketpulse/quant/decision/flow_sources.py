"""Bounded funding adapters. Original responses can rebuild every chart point."""

import copy
import json
from datetime import UTC, date, datetime, time
from decimal import InvalidOperation

from marketpulse.infrastructure.storage.models import BlobRef

from ..contracts import digest
from ..live.sources import EM, KLINE, em_params
from . import tushare
from .contracts import (
    EvidenceBundle,
    EvidenceItem,
    FlowInputs,
    FlowPoint,
    FlowWindowSpec,
    Locator,
    SourceCapability,
)
from .flows import METHOD, compute
from .market import number
from .next_session.calendar import parse_annual, rule_text
from .next_session.labels import SHANGHAI
from .next_session.prices import agree, quote
from .tools import SourceUnavailable, Stopped

VENDOR = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
ETF = "https://query.sse.com.cn/commonQuery.do"
NAV = "https://api.fund.eastmoney.com/f10/lsjz"


def window_spec(calendar, captured_at, aligned_session=None):
    now = datetime.fromisoformat(captured_at).astimezone(SHANGHAI)
    sessions = (
        []
        if not calendar
        else [
            date.fromisoformat(d)
            for d in calendar["calendar"]["sessions"]
            if datetime.combine(date.fromisoformat(d), time(15), SHANGHAI) <= now
        ]
    )
    if aligned_session:
        aligned = date.fromisoformat(aligned_session)
        if aligned not in sessions[-2:]:
            raise ValueError("资金共同窗口不是最新或前一交易日，拒绝静默回退")
        sessions = [d for d in sessions if d <= aligned]
    return FlowWindowSpec(
        end_date=sessions[-1] if sessions else now.date(),
        sessions=tuple(sessions[-61:]),
        definition_version=METHOD,
    )


def rebuild(repo, snapshot):
    """Do not trust serialized parsed inputs or calculated chart values."""
    calendar = snapshot["calendar"]
    if calendar:
        c = calendar["calendar"]
        annual, rules = c["source_refs"]
        if [str(s) for s in parse_annual(repo.raw_bytes(annual).decode("utf8"), 2026)] != c[
            "sessions"
        ]:
            raise ValueError("flow calendar differs from official annual original")
        if "每周一至周五" not in rule_text(repo.raw_bytes(rules)):
            raise ValueError("flow trading rule original missing")
    spec = window_spec(calendar, snapshot["captured_at"], snapshot.get("aligned_session"))
    series, coverage, gaps = {}, [], list(snapshot["transport_gaps"])
    subject = snapshot["subject"]
    quotes = []
    for entry in snapshot["responses"]:
        source, role = entry["source"], entry["role"]
        raw = repo.raw_bytes(source["blob_ref"])
        if BlobRef.from_uri(source["blob_ref"]).sha256 != source["sha256"]:
            raise ValueError("flow original hash mismatch")
        if datetime.fromisoformat(source["fetched_at"]) > datetime.fromisoformat(
            snapshot["captured_at"]
        ):
            raise ValueError("flow source arrived after cutoff")
        # Each adapter is atomic: malformed identity, duplicate date or field rejects its series.
        parsed = {}

        def add(key, stamp, value, unit, *, positive=False, scale=1, parsed=parsed, source=source):
            d = date.fromisoformat(str(stamp)[:10])
            if d > spec.end_date:
                return
            points = parsed.setdefault(key, {})
            if d in points:
                raise ValueError("duplicate flow source date")
            points[d] = FlowPoint(
                session=d,
                value=number(value, positive=positive, nonnegative=key != "vendor_estimate")
                * scale,
                source_ref=source["blob_ref"],
                unit=unit,
            )

        try:
            if role.startswith("quote_"):
                family = source["source_family"]
                quotes.append(
                    (
                        source,
                        {
                            subject["symbol"]: quote(
                                raw.decode("gb18030"), subject["symbol"], family
                            )
                        },
                    )
                )
                continue
            packet = json.loads(raw)
            if role in {"tushare_daily", "tushare_margin"}:
                if (
                    source["source_family"] != "TUSHARE"
                    or source.get("api_name")
                    != ("daily" if role == "tushare_daily" else "margin_detail")
                    or source.get("parameters", {}).get("ts_code") != subject["security_id"]
                ):
                    raise ValueError("Tushare flow request identity conflict")
                for row in tushare.records(packet):
                    if row["ts_code"] != subject["security_id"]:
                        raise ValueError("Tushare flow row identity conflict")
                    stamp = datetime.strptime(row["trade_date"], "%Y%m%d").date().isoformat()
                    if role == "tushare_daily":
                        prices = [
                            number(row[f], positive=True) for f in ("open", "close", "high", "low")
                        ]
                        if not prices[3] <= min(prices[:2]) <= max(prices[:2]) <= prices[2]:
                            raise ValueError("Tushare daily OHLC conflict")
                        add("security_close", stamp, row["close"], "CNY/share", positive=True)
                        add("security_turnover", stamp, row["amount"], "CNY", scale=1000)
                    else:
                        for key, field in (
                            ("margin_balance", "rzye"),
                            ("margin_buy", "rzmre"),
                            ("margin_repay", "rzche"),
                        ):
                            if row.get(field) is not None:
                                add(key, stamp, row[field], "CNY")
            elif role in {"security_daily", "market_daily"}:
                symbol = subject["symbol"] if role == "security_daily" else "sh000300"
                for row in packet["data"][symbol]["day"]:
                    prices = [number(row[j], positive=True) for j in (1, 2, 3, 4)]
                    if not prices[3] <= min(prices[:2]) <= max(prices[:2]) <= prices[2]:
                        raise ValueError("daily OHLC conflict")
                    add(
                        "security_close" if role == "security_daily" else "market_close",
                        row[0],
                        row[2],
                        "CNY/share",
                        positive=True,
                    )
                    if role == "security_daily":
                        add("security_turnover", row[0], row[8], "CNY", scale=10000)
            elif role == "margin":
                for row in packet["result"]["data"]:
                    if str(row["SCODE"]) != subject["code"]:
                        raise ValueError("margin security identity conflict")
                    for key, field in (
                        ("margin_balance", "RZYE"),
                        ("margin_buy", "RZMRE"),
                        ("margin_repay", "RZCHE"),
                    ):
                        if row.get(field) is not None:
                            add(key, row["DATE"], row[field], "CNY")
            elif role == "vendor":
                if str(packet["data"]["code"]) != subject["code"]:
                    raise ValueError("vendor security identity conflict")
                for line in packet["data"]["klines"]:
                    fields = line.split(",")
                    add("vendor_estimate", fields[0], fields[1], "CNY")
            elif role == "etf_shares":
                rows = packet["result"]
                for row in rows:
                    if str(row["SEC_CODE"]) == "512480":
                        add("etf_shares", row["STAT_DATE"], row["TOT_VOL"], "shares", scale=10000)
            elif role == "etf_nav":
                for row in packet["Data"]["LSJZList"]:
                    add("etf_nav", row["FSRQ"], row["DWJZ"], "CNY/share", positive=True)
            else:
                raise ValueError("unknown flow adapter")
            if not parsed:
                raise ValueError("no usable flow fields")
            for key, points in parsed.items():
                series[key] = tuple(points[d] for d in sorted(points))
                coverage.append(
                    dict(
                        channel=key,
                        first=str(min(points)),
                        last=str(max(points)),
                        count=len(points),
                        source_ref=source["blob_ref"],
                        source_family=source["source_family"],
                    )
                )
        except (ValueError, KeyError, IndexError, TypeError, InvalidOperation) as error:
            gaps.append(dict(channel=role, reason="原始响应未通过字段核验：" + str(error)[:130]))
    inputs = FlowInputs(
        membership_ref="UNAVAILABLE:行业成员与同范围分母未核定", definition_version=METHOD, **series
    )
    if snapshot.get("aligned_session"):
        margin_sources = [e for e in snapshot["responses"] if e["role"] == "tushare_margin"]
        if (
            len(margin_sources) != 1
            or not inputs.margin_balance
            or max(p.session for p in inputs.margin_balance) != spec.end_date
        ):
            raise ValueError("资金共同窗口未绑定实际融资日期")
    checks = dict(
        status="UNAVAILABLE", note="两路供应商最新收盘核对尚不完整；交易所数值未逐值独立核验。"
    )
    if len(quotes) == 2:
        try:
            latest = agree(
                quotes,
                subject["symbol"],
                spec.end_date,
                datetime.fromisoformat(snapshot["captured_at"]),
            )
            close = {p.session: p.value for p in inputs.security_close}.get(spec.end_date)
            amount = {p.session: p.value for p in inputs.security_turnover}.get(spec.end_date)
            if (
                close != number(latest["close"])
                or amount is None
                or any(
                    abs(number(q[subject["symbol"]]["turnover"]) - amount) > 10000
                    for _, q in quotes
                )
            ):
                raise ValueError("daily close or turnover differs from quote sources")
            checks = dict(
                status="LATEST_QUOTE_MATCHED",
                session=str(spec.end_date),
                note=(
                    "最新收盘与成交额通过腾讯、新浪字段核对（成交额容差一万元）；"
                    "不是全窗口或交易所独立数值验证。"
                ),
                source_refs=[s["blob_ref"] for s, _ in quotes],
            )
        except ValueError as error:
            checks = dict(status="CONFLICT_OR_UNAVAILABLE", note=str(error))
    result = dict(
        inputs=inputs.model_dump(mode="json"),
        spec=spec.model_dump(mode="json"),
        report=compute(inputs, spec, subject["security_id"]).model_dump(mode="json"),
        coverage=coverage,
        gaps=gaps,
        checks=checks,
    )
    if snapshot.get("aligned_session"):
        result["alignment"] = dict(
            protocol="LATEST_COMMON_MARGIN_CLOSE_V3",
            session=snapshot["aligned_session"],
            latest_closed_session=str(window_spec(calendar, snapshot["captured_at"]).end_date),
            note="融资按实际公布日与价格对齐至最新共同交易日；不是当天尚未公布的融资。其他面板保留各自最新行情日期。",
        )
    return result


def collect(repo, client, subject, calendar):
    started = datetime.now(UTC).isoformat()
    spec = window_spec(calendar, started)
    start = spec.sessions[0] if spec.sessions else spec.end_date
    end, symbol, code = spec.end_date, subject["symbol"], subject["code"]
    if tushare.load_token() and calendar:
        eligible = [d for d in calendar["calendar"]["sessions"] if d <= str(end)]
        if len(eligible) >= 62:
            start = date.fromisoformat(eligible[-62])
    definitions = [
        ("security_daily", KLINE, {"param": f"{symbol},day,{start},{end},640,"}, "TENCENT", None),
        ("market_daily", KLINE, {"param": f"sh000300,day,{start},{end},640,"}, "TENCENT", None),
        (
            "margin",
            EM,
            em_params("RPTA_WEB_RZRQ_GGMX", "DATE", 120, "SCODE", code),
            "EASTMONEY_MARGIN",
            None,
        ),
        (
            "vendor",
            VENDOR,
            dict(
                lmt=120,
                klt=101,
                secid=("1." if symbol.startswith("sh") else "0.") + code,
                fields1="f1,f2,f3,f7",
                fields2=",".join(f"f{i}" for i in range(51, 66)),
            ),
            "EASTMONEY_ORDER_ESTIMATE",
            None,
        ),
        (
            "etf_shares",
            ETF,
            {
                "isPagination": "true",
                "pageHelp.pageSize": 10000,
                "pageHelp.pageNo": 1,
                "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
                "STAT_DATE": str(end),
            },
            "SSE_ETF_SHARES",
            None,
        ),
        (
            "etf_nav",
            NAV,
            dict(
                fundCode="512480", pageIndex=1, pageSize=100, startDate=str(start), endDate=str(end)
            ),
            "EASTMONEY_FUND_NAV",
            None,
        ),
        ("quote_tencent", "https://qt.gtimg.cn/", {"q": symbol}, "TENCENT", "gb18030"),
        ("quote_sina", "https://hq.sinajs.cn/?list=" + symbol, None, "SINA", "gb18030"),
    ]
    snapshot = dict(
        subject=subject,
        calendar=calendar,
        started_at=started,
        captured_at=started,
        responses=[],
        transport_gaps=[],
    )
    if tushare.load_token():
        for role, api in (("tushare_daily", "daily"), ("tushare_margin", "margin_detail")):
            try:
                source, rows = tushare.fetch(
                    client,
                    "flow_" + role,
                    api,
                    dict(
                        ts_code=subject["security_id"],
                        start_date=str(start).replace("-", ""),
                        end_date=str(end).replace("-", ""),
                    ),
                )
                if not rows or any(r.get("ts_code") != subject["security_id"] for r in rows):
                    raise ValueError("Tushare flow response empty or wrong identity")
                snapshot["responses"].append(dict(role=role, source=source))
                if role == "tushare_margin":
                    latest = max(
                        datetime.strptime(r["trade_date"], "%Y%m%d").date().isoformat()
                        for r in rows
                    )
                    window_spec(calendar, started, latest)
                    snapshot["aligned_session"] = latest
                old_role = "security_daily" if role == "tushare_daily" else "margin"
                definitions = [d for d in definitions if d[0] != old_role]
            except Stopped:
                raise
            except (SourceUnavailable, ValueError) as error:
                snapshot["transport_gaps"].append(dict(channel=role, reason=str(error)))
    for role, url, params, family, encoding in definitions:
        try:
            source, _ = client.fetch(
                "flow_" + role, url, params=params, family=family, encoding=encoding
            )
            snapshot["responses"].append(dict(role=role, source=source))
        except Stopped:
            raise
        except SourceUnavailable as error:
            snapshot["transport_gaps"].append(dict(channel=role, reason=str(error)))
    snapshot["captured_at"] = datetime.now(UTC).isoformat()
    # A run crossing the close must not silently change its target after fetching.
    if window_spec(calendar, snapshot["captured_at"]) != spec:
        raise ValueError("取数跨越收盘边界，请重新研究以固定一致的窗口")
    return snapshot


def attach(repo, data, snapshot):
    data = copy.deepcopy(data)
    rebuilt = rebuild(repo, snapshot)
    ref = repo.blob(rebuilt)
    definition_url = "https://www.sse.com.cn/market/othersdata/margin/detail/index.shtml"
    source = dict(
        key="flow_calculation",
        url=definition_url,
        source_family="DECISION_FLOW_CALC",
        blob_ref=ref,
        sha256=BlobRef.from_uri(ref).sha256,
        fetched_at=snapshot["captured_at"],
    )
    sources = [e["source"] for e in snapshot["responses"]]
    if snapshot["calendar"]:
        # Official calendar originals are already archived; bind them to the snapshot.
        for raw_ref in snapshot["calendar"]["calendar"]["source_refs"]:
            sources.append(
                dict(
                    key="flow_calendar",
                    url="",
                    source_family=snapshot["subject"]["exchange"],
                    blob_ref=raw_ref,
                    sha256=BlobRef.from_uri(raw_ref).sha256,
                    fetched_at=snapshot["captured_at"],
                )
            )
    data["sources"] = list(
        {s["blob_ref"]: s for s in data["sources"] + sources + [source]}.values()
    )
    items = []
    for wi, window in enumerate(rebuilt["report"]["windows"]):
        for mi, m in enumerate(window["metrics"]):
            if m["value"] is None:
                continue
            items.append(
                EvidenceItem(
                    evidence_id=f"EV-FLOW-{window['window']}-{m['metric_id']}",
                    subject_id=snapshot["subject"]["security_id"],
                    kind="flow",
                    source_family="DECISION_FLOW_CALC",
                    original_source_id="CALC:" + METHOD,
                    url=definition_url,
                    raw_ref=ref,
                    content_hash=source["sha256"],
                    locator=Locator(kind="json", path=f"/report/windows/{wi}/metrics/{mi}/value"),
                    text=f"近{window['window']}交易日 {m['label']}：{m['value']} {m['unit']}",
                    value=m["value"],
                    unit=m["unit"],
                    observed_at=str(rebuilt["spec"]["end_date"]) + "T15:00:00+08:00",
                    period_start=window["sessions"][0],
                    period_end=rebuilt["spec"]["end_date"],
                    fetched_at=snapshot["captured_at"],
                    available_at=snapshot["captured_at"],
                    time_precision="day",
                    method_version=METHOD,
                    quality_status="PARTIAL",
                    limitations=rebuilt["report"]["limitations"],
                ).model_dump(mode="json")
            )
    capability = SourceCapability(
        source_id="flow-calculation-v1",
        name="分渠道窗口计算（服务端）",
        url=definition_url,
        source_family="DECISION_FLOW_CALC",
        acquisition="本次原始响应与官方日历重算",
        authentication="沿用原始公开来源",
        license_note="仅供研究，不扩大原来源许可",
        coverage="固定交易日；缺字段不补零",
        frequency="每次研究封存",
        status="PARTIAL",
        reason="缺少行业成员、连续ETF份额与事件核定，未做交易所逐值独立验证",
        raw_ref=ref,
        content_hash=source["sha256"],
        tested_at=snapshot["captured_at"],
    ).model_dump(mode="json")
    # The P1 catalog may contain today's in-progress bar; P3 is strictly closed-session based.
    base_items = [
        e
        for e in data["bundle"]["items"]
        if not (
            e["kind"] == "fact"
            and e["source_family"] == "TENCENT"
            and e.get("period_end")
            and e["period_end"] > rebuilt["spec"]["end_date"]
        )
    ]
    bundle = dict(
        data["bundle"],
        cutoff_at=snapshot["captured_at"],
        items=base_items + items,
        sources=data["bundle"]["sources"] + [capability],
    )
    data["bundle"] = EvidenceBundle.model_validate(bundle).model_dump(mode="json")
    data.update(
        evidence=data["bundle"]["items"],
        flow_snapshot=snapshot,
        flow_calculation_ref=ref,
        flow_report=rebuilt["report"],
        flow_coverage=rebuilt["coverage"],
        flow_gaps=rebuilt["gaps"],
        flow_checks=rebuilt["checks"],
    )
    if rebuilt.get("alignment"):
        data["flow_alignment"] = rebuilt["alignment"]
    return data


def verify(repo, data):
    rebuilt = rebuild(repo, data["flow_snapshot"])
    if data.get("flow_alignment") != rebuilt.get("alignment"):
        raise ValueError("资金共同窗口日期被改写")
    if digest(rebuilt) != digest(repo.value(data["flow_calculation_ref"])) or any(
        data[k] != rebuilt[v]
        for k, v in (
            ("flow_report", "report"),
            ("flow_coverage", "coverage"),
            ("flow_gaps", "gaps"),
            ("flow_checks", "checks"),
        )
    ):
        raise ValueError("flow calculation or presentation differs from sealed originals")
    if datetime.fromisoformat(data["bundle"]["cutoff_at"]) != datetime.fromisoformat(
        data["flow_snapshot"]["captured_at"]
    ):
        raise ValueError("flow cutoff differs from evidence bundle")
