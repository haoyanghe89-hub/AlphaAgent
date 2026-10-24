"""Bounded current research inputs; credentials never enter the frozen packet."""

import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from marketpulse.telemetry.tracing import stage

from ..decision import tushare
from ..decision.next_session.calendar import URLS

SHANGHAI = ZoneInfo("Asia/Shanghai")
SSE_NOTICE = "https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml"
SSE_DEFERRED = "https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/10816492/files/7476908baade49b8b14881439d2f89b6.docx"


@stage("public_data.collect", as_type="retriever")
def capture_inputs(client, entities, captured_at):
    if captured_at.tzinfo is None or not 1 <= len(entities) <= 4:
        raise ValueError("aware capture time and one to four stocks required")
    local = captured_at.astimezone(SHANGHAI)
    end = (
        local.date() if (local.hour, local.minute) >= (15, 30) else local.date() - timedelta(days=1)
    )
    start = end - timedelta(days=400)
    sources = []

    def ts(role, api, params):
        source, rows = tushare.fetch(
            client, role, api, params, fields=tushare.KNOWLEDGE_FIELDS.get(api)
        )
        sources.append(dict(role=role, source=source))
        return rows

    master = ts("plan_master", "stock_basic", dict(exchange="", list_status="L"))
    stocks, seen = [], set()
    for entity in entities:
        query = entity.strip().upper()
        matches = [
            r
            for r in master
            if re.fullmatch(
                r"(?:(?:60[0135]|688)\d{3}\.SH|(?:00[0-3]|30[01])\d{3}\.SZ)", r.get("ts_code", "")
            )
            and (r["ts_code"] == query or r["ts_code"][:6] == query or r["name"].upper() == query)
        ]
        if len(matches) != 1:
            raise ValueError("请使用准确的沪深A股名称或六位代码：" + entity[:80])
        subject = matches[0]
        if subject["ts_code"] in seen:
            raise ValueError("same stock cannot be duplicated in a plan")
        seen.add(subject["ts_code"])
        stocks.append(dict(subject))
    calendars = {}
    for exchange in sorted({"SSE" if r["ts_code"].endswith(".SH") else "SZSE" for r in stocks}):
        calendars[exchange] = ts(
            "plan_calendar_" + exchange,
            "trade_cal",
            dict(
                exchange=exchange,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            ),
        )
    benchmark = ts(
        "plan_benchmark",
        "index_daily",
        dict(
            ts_code="000300.SH",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        ),
    )
    if not benchmark:
        raise ValueError("declared CSI300 benchmark input unavailable")
    market_date = max(r["trade_date"] for r in benchmark)
    if market_date > end.strftime("%Y%m%d"):
        raise ValueError("future market input rejected")
    gaps = []
    for subject in stocks:
        code = subject["ts_code"]
        params = dict(ts_code=code, start_date=start.strftime("%Y%m%d"), end_date=market_date)
        subject["daily"] = ts("plan_daily_" + code, "daily", params)
        subject["adjustments"] = ts("plan_adjustment_" + code, "adj_factor", params)
        subject["industry_memberships"] = ts(
            "plan_industry_" + code, "index_member_all", dict(ts_code=code)
        )
        subject["financials"] = {}
        if subject.get("industry") in ("银行", "证券", "保险", "多元金融"):
            subject["financial_scope"] = "FINANCIAL"
            continue
        subject["financial_scope"] = "NON_FINANCIAL_PENDING_CHECK"
        for api in ("income", "cashflow", "balancesheet"):
            try:
                subject["financials"][api] = ts(
                    "plan_" + api + "_" + code,
                    api,
                    dict(
                        ts_code=code,
                        start_date=(end - timedelta(days=1500)).strftime("%Y%m%d"),
                        end_date=market_date,
                    ),
                )
            except ValueError as error:
                subject["financials"][api] = []
                gaps.append(dict(security_id=code, dataset=api, reason=type(error).__name__))
    rule_sources = []
    for exchange in sorted(calendars):
        source, raw = client.fetch(
            "plan_rules_" + exchange, URLS[exchange][1], family=exchange, binary=True
        )
        rule_sources.append(dict(exchange=exchange, kind="RULES", source=source))
        sources.append(dict(role="plan_rules_" + exchange, source=source))
    if "SSE" in calendars:
        for label, url, binary in (("NOTICE", SSE_NOTICE, False), ("DEFERRED", SSE_DEFERRED, True)):
            source, _ = client.fetch(
                "plan_sse_" + label,
                url,
                family="SSE",
                binary=binary,
                encoding=None if binary else "utf8",
            )
            rule_sources.append(dict(exchange="SSE", kind=label, source=source))
            sources.append(dict(role="plan_sse_" + label, source=source))
    return dict(
        input_version="investment-inputs-v1",
        captured_at=captured_at.isoformat(),
        market_date=market_date,
        benchmark_id="000300.SH",
        benchmark=benchmark,
        calendars=calendars,
        stocks=stocks,
        sources=sources,
        trading_rule_sources=rule_sources,
        gaps=gaps,
        usage="CURRENT_RESEARCH_NOT_HISTORICAL_BACKTEST",
    )
