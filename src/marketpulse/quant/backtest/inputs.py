"""Conservative source input observations, not financial or execution calculations."""

import calendar
from datetime import date
from decimal import Decimal

from ..contracts import digest
from ..domain import decimal_text
from .history import query_segments


def input_queries(target):
    code = target["provider_code"]
    start, end = date.fromisoformat(target["start"]), date.fromisoformat(target["end"])
    queries = [
        dict(kind="stock_daily", code=code, start=str(a), end=str(b))
        for a, b in query_segments(start, end)
    ]
    queries += [dict(kind="dividends", code=code, year=y) for y in range(start.year, end.year + 1)]
    year = target["signal_year"]
    queries += [
        dict(kind="profit", code=code, year=y, quarter=q)
        for y, q in [(year - 1, 1), (year - 1, 4), (year, 1)]
    ]
    if len(queries) > 24:
        raise ValueError("input audit request budget exceeded")
    return queries


def amount(value):
    if value in {None, ""}:
        return None
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("invalid nonnegative source amount")
    return number


def inspect_inputs(packets, target):
    expected = set(target["expected_sessions"])
    sources, prices, events, financials = [], {}, [], []
    empty_actions = empty_financials = suspended = unknown_status = unknown_st = 0
    code = target["provider_code"]
    queries = input_queries(target)
    seen_queries = set()
    seen_events = set()
    for packet in packets:
        query, rows = packet["query"], packet["rows"]
        if (
            query not in queries
            or digest(query) in seen_queries
            or packet["provider"] != "baostock"
            or len(rows) > 20000
        ):
            raise ValueError("input source scope/duplicate mismatch")
        seen_queries.add(digest(query))
        sha = digest(packet)
        sources.append(
            dict(
                query=query,
                sha256=sha,
                row_count=len(rows),
                retrieved_at=packet["retrieved_at"],
                provider=packet["provider"],
                pit_level="PARTIAL",
                status="UNVERIFIED",
            )
        )
        kind = query["kind"]
        if kind == "dividends" and not rows:
            empty_actions += 1
        if kind == "profit" and not rows:
            empty_financials += 1
        for row in rows:
            if row.get("code") != code:
                raise ValueError("response security mismatch")
            if kind == "stock_daily":
                day = row["date"]
                date.fromisoformat(day)
                if (
                    not query["start"] <= day <= query["end"]
                    or day in prices
                    or day not in expected
                    or row.get("adjustflag") != "3"
                ):
                    raise ValueError("daily date/adjustment/session mismatch")
                status, st = row.get("tradestatus"), row.get("isST")
                unknown_status += status not in {"0", "1"}
                unknown_st += st not in {"0", "1"}
                suspended += status == "0"
                opening, high, low, closing = (
                    amount(row.get(field)) for field in ("open", "high", "low", "close")
                )
                if status == "1" and (
                    any(v is None or v <= 0 for v in (opening, high, low, closing))
                    or not low <= min(opening, closing) <= max(opening, closing) <= high
                ):
                    raise ValueError("invalid trading OHLC")
                for field in ("volume", "amount", "preclose"):
                    amount(row.get(field))
                prices[day] = True
            elif kind == "dividends":
                dates = {
                    name: row.get(field) or None
                    for name, field in (
                        ("ex_date", "dividOperateDate"),
                        ("register_date", "dividRegistDate"),
                        ("pay_date", "dividPayDate"),
                        ("stock_listing_date", "dividStockMarketDate"),
                    )
                }
                for d in dates.values():
                    if d:
                        date.fromisoformat(d)
                ex = dates["ex_date"]
                if ex and date.fromisoformat(ex).year != query["year"]:
                    raise ValueError("dividend operate year mismatch")
                if ex and not target["start"] <= ex <= target["end"]:
                    continue
                identity = (ex, dates["register_date"])
                if identity in seen_events:
                    raise ValueError("ambiguous dividend event")
                seen_events.add(identity)
                values = {
                    name: amount(row.get(field))
                    for name, field in (
                        ("cash_per_share_before_tax", "dividCashPsBeforeTax"),
                        ("bonus_shares_per_share", "dividStocksPs"),
                        ("reserve_shares_per_share", "dividReserveToStockPs"),
                    )
                }
                events.append(
                    {
                        **dates,
                        **{
                            k: decimal_text(v) if v is not None else None for k, v in values.items()
                        },
                        "cash_unit": "CNY/share",
                        "stock_unit": "share/share",
                        "source_hash": sha,
                        "status": "PARTIAL",
                        "missing_fields": [k for k, v in {**dates, **values}.items() if v is None],
                    }
                )
            elif kind == "profit":
                month = query["quarter"] * 3
                period = str(
                    date(query["year"], month, calendar.monthrange(query["year"], month)[1])
                )
                if row.get("statDate") != period:
                    raise ValueError("financial period mismatch")
                pub = row.get("pubDate") or None
                if pub:
                    date.fromisoformat(pub)
                    if pub < period:
                        raise ValueError("financial publication before period end")
                observation = (
                    "MISSING_DATE"
                    if not pub
                    else "BEFORE_SIGNAL"
                    if pub < target["signal_day"]
                    else "SAME_DAY_TIME_UNKNOWN"
                    if pub == target["signal_day"]
                    else "AFTER_SIGNAL"
                )
                financials.append(
                    dict(
                        period_end=period,
                        publication_date=pub,
                        date_observation=observation,
                        source_hash=sha,
                        pit_level="PARTIAL",
                        version_status="ORIGINAL_REVISION_ARCHIVE_UNAVAILABLE",
                    )
                )
    if len({(f["period_end"], f["publication_date"]) for f in financials}) != len(financials):
        raise ValueError("ambiguous financial observations")
    scope = {
        k: target[k]
        for k in (
            "instrument_id",
            "name",
            "provider_code",
            "start",
            "end",
            "signal_year",
            "signal_day",
            "execution_day",
        )
    }
    return dict(
        schema_version="backtest-input-audit-v1",
        scope=scope,
        sources=sources,
        execution_status="BLOCKED",
        metric_status="UNVERIFIED",
        prices=dict(
            row_count=len(prices),
            expected_session_count=len(expected),
            missing_session_count=len(expected - set(prices)),
            suspended_count=suspended,
            unknown_status_count=unknown_status,
            unknown_st_count=unknown_st,
            coverage="COVERED"
            if expected and set(prices) == expected
            else "INCOMPLETE"
            if prices
            else "UNAVAILABLE",
            currency="CNY",
            volume_unit="share",
            adjustment="raw",
            pit_level="PARTIAL",
        ),
        actions=dict(
            events=sorted(events, key=lambda e: e["ex_date"] or ""),
            empty_query_count=empty_actions,
            event_count=len(events),
            complete=False,
            pit_level="PARTIAL",
        ),
        financials=dict(
            observations=sorted(financials, key=lambda f: f["period_end"]),
            empty_query_count=empty_financials,
            original_versions_available=False,
            pit_level="PARTIAL",
        ),
        gaps=[
            "只核验所选一个证券，财报仅抽查所选年度三期；不是全市场数据准入。",
            "pubDate只表示供应商披露日期观察，没有最初版本、修订档案、原披露定位和可得时间。",
            "供应商季度财务口径尚未核对，缺少归母权益与信号日发行股数；未自算PE、ROE或TTM。",
            "分红送转记录不覆盖完整配股、拆合股、退市及税款处理；缺日期或金额不填零。",
            "有OHLC、停牌或ST字段仍不能证明开盘可成交；涨跌停、订单限制和规则生效版本缺失。",
            "全部来源PARTIAL，仍须独立原披露及权威口径交叉核对；不执行交易或收益计算。",
        ],
    )
