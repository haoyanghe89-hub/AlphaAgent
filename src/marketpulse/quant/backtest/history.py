"""Bounded historical requests and summaries, with no strategy returns."""

import asyncio
import json
import os
import re
import sys
from datetime import date, timedelta
from decimal import Decimal

from ..contracts import canonical, digest
from ..data.base import ProviderUnavailable, bounded_read


def query_segments(start, end):
    if end < start or (end - start).days > 2954:
        raise ValueError("historical window must be ordered and <=8 years plus signal buffer")
    result = []
    while start <= end:
        last = min(end, start + timedelta(days=1096))
        result.append((start, last))
        start = last + timedelta(days=1)
    return result


def annual_windows(sessions, start, end):
    if sessions != sorted(set(sessions)):
        raise ValueError("ordered unique sessions required")
    windows = []
    for year in range(start.year, end.year + 1):
        may = [s for s in sessions if s.startswith(f"{year}-05-")]
        if not may:
            continue
        execution = may[0]
        if not start.isoformat() <= execution <= end.isoformat():
            continue
        previous = [s for s in sessions if s < execution]
        if not previous:
            raise ValueError("previous signal session unavailable")
        windows.append(dict(year=year, signal_day=previous[-1], execution_day=execution))
    return windows


def is_a_share(code):
    return bool(re.fullmatch(r"(?:sh\.6\d{5}|sz\.[03]\d{5})", code))


def summarize(packets, start, end, *, version="history-data-v2"):
    if version not in {"history-data-v1", "history-data-v2"}:
        raise ValueError("unsupported historical normalizer version")
    basics, sessions, prices, universes, sources = {}, set(), {}, [], []
    for packet in packets:
        query, rows = packet["query"], packet["rows"]
        if len(rows) > 20000:
            raise ValueError("source row bound exceeded")
        sources.append(
            dict(
                query=query,
                sha256=digest(packet),
                row_count=len(rows),
                retrieved_at=packet["retrieved_at"],
                provider=packet["provider"],
                pit_level="PARTIAL",
                status="UNVERIFIED",
            )
        )
        if query["kind"] == "basic":
            for r in rows:
                if r["code"] in basics:
                    raise ValueError("duplicate lifecycle code requires separate identities")
                basics[r["code"]] = r
        elif query["kind"] == "calendar":
            local_days = set()
            for r in rows:
                day = r["calendar_date"]
                if day in local_days or r["is_trading_day"] not in {"0", "1"}:
                    raise ValueError("duplicate/unknown calendar day")
                if not query["start"] <= day <= query["end"]:
                    raise ValueError("calendar outside query")
                local_days.add(day)
                if r["is_trading_day"] == "1":
                    if day in sessions:
                        raise ValueError("duplicate calendar segment")
                    sessions.add(day)
            expected = (
                date.fromisoformat(query["end"]) - date.fromisoformat(query["start"])
            ).days + 1
            if len(local_days) != expected:
                raise ValueError("calendar interval incomplete")
        elif query["kind"] == "benchmark":
            for r in rows:
                day = r["date"]
                if (
                    not query["start"] <= day <= query["end"]
                    or r["code"] != "sh.000300"
                    or day in prices
                ):
                    raise ValueError("duplicate/wrong benchmark or date")
                p = Decimal(r["close"])
                if not p.is_finite() or p <= 0:
                    raise ValueError("invalid benchmark close")
                prices[day] = p
    for packet in packets:
        if packet["query"]["kind"] != "members":
            continue
        day = packet["query"]["day"]
        if sessions and day not in sessions:
            raise ValueError("membership not on a supplied session")
        codes = [r["code"] for r in packet["rows"]]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate membership")
        count = suspended = delisted = unknown = excluded = 0
        for row in packet["rows"]:
            code = row["code"]
            if not is_a_share(code):
                continue
            basic = basics.get(code)
            if version == "history-data-v2" and basic and basic.get("type") in {"2", "3", "4", "5"}:
                excluded += 1
                continue
            if not basic or basic.get("type") != "1" or not basic.get("ipoDate"):
                unknown += 1
                continue
            listed = date.fromisoformat(basic["ipoDate"])
            retired = date.fromisoformat(basic["outDate"]) if basic.get("outDate") else None
            if listed > date.fromisoformat(day) or (retired and retired <= date.fromisoformat(day)):
                unknown += 1  # conflict, never silently drop a historical returned member
                continue
            if row.get("tradeStatus") not in {"0", "1"}:
                unknown += 1
                continue
            count += 1
            suspended += row["tradeStatus"] == "0"
            delisted += basic.get("status") == "0"
        universes.append(
            dict(
                signal_day=day,
                member_count=count,
                suspended_count=suspended,
                currently_delisted_count=delisted,
                unknown_count=unknown,
                returned_count=len(codes),
                **({"excluded_non_stock_count": excluded} if version == "history-data-v2" else {}),
                pit_level="PARTIAL",
                source_hash=digest(packet),
                selection_status="BLOCKED",
                eligible_count=None,
                reason="缺少全量财报最初／修订版本与逐项核对，尚未执行条件筛选。",
            )
        )
    expected_sessions = {s for s in sessions if start.isoformat() <= s <= end.isoformat()}
    actual_dates = set(prices)
    if actual_dates - expected_sessions and sessions:
        raise ValueError("benchmark on unknown/closed session")
    benchmark = dict(
        code="sh.000300",
        name="沪深300价格指数",
        return_basis="price_index",
        pit_level="PARTIAL",
        status="UNVERIFIED",
        row_count=len(prices),
        start=min(prices) if prices else None,
        end=max(prices) if prices else None,
        missing_session_count=len(expected_sessions - actual_dates),
        coverage="COVERED"
        if expected_sessions and actual_dates == expected_sessions
        else "INCOMPLETE"
        if prices
        else "UNAVAILABLE",
    )
    windows = annual_windows(sorted(sessions), start, end) if sessions else []
    by_day = {u["signal_day"]: u for u in universes}
    annual = [
        {
            **w,
            **by_day.get(
                w["signal_day"],
                dict(
                    member_count=None,
                    selection_status="BLOCKED",
                    eligible_count=None,
                    reason="该日历史名单缺失。",
                ),
            ),
        }
        for w in windows
    ]
    return dict(
        schema_version=version,
        sources=sources,
        universes=sorted(universes, key=lambda u: u["signal_day"]),
        annual=annual,
        benchmark=benchmark,
        status="PARTIAL",
        strategy_computed=False,
        limitations=[
            "供应商事后历史名单及价格尚未完成独立完整性核对，不是STRICT PIT。",
            "股票池是沪深A股范围，北交所、B股、指数和基金不纳入；未知生命周期单独计数。",
            "停牌股保留为历史成员；当前已退市股票不会仅因当前状态被删去。",
            "基准是价格指数，不含股息再投资；不是全收益基准。",
            "本次只获取名单、日历和价格基准；没有生成选股、交易或策略收益。",
        ],
    )


async def fetch_query(query, budget=45):
    # No model/API credentials or arbitrary SDK method names enter this worker.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "marketpulse.quant.backtest.history_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        async with asyncio.timeout(budget):
            child.stdin.write(canonical(query).encode())
            await child.stdin.drain()
            child.stdin.close()
            stdout, _ = await asyncio.gather(
                bounded_read(child.stdout, 16 * 1024 * 1024),
                bounded_read(child.stderr, 1024 * 1024),
            )
            await child.wait()
    except BaseException:
        if child.returncode is None:
            child.kill()
        await child.wait()
        raise
    lines = [line for line in stdout.splitlines() if line.startswith(b"HISTORY_JSON:")]
    if child.returncode or len(lines) != 1:
        raise ProviderUnavailable("historical SDK worker failed")
    result = json.loads(lines[0][13:])
    if result.get("error"):
        raise ProviderUnavailable(result["error"])
    if result.get("query") != query or len(result.get("rows", [])) > 20000:
        raise ProviderUnavailable("history response scope mismatch")
    return result
