"""Frozen official CSI price/total-return inputs and independent price checks."""

import asyncio
import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.investigation.api import _error

from ..contracts import digest
from ..domain import decimal_text
from .filings import compare_value
from .history import query_segments

URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
BASIS = {"000300": "price_index", "H00300": "total_return_index"}


def benchmark_target(runtime, parent_id):
    from .api import load_check

    parent = load_check(runtime, parent_id)
    if parent["check_status"] != "COMPLETED" or not parent.get("historical_data"):
        raise _error(422, "BENCHMARK_PARENT_INCOMPLETE", "请先完成历史日历和基准采集。")
    store = runtime.service.store
    sessions, native = set(), {}
    for source in parent["historical_data"]["sources"]:
        if source["query"]["kind"] not in {"calendar", "benchmark"}:
            continue
        packet = json.loads(store.blobs.get_bytes(BlobRef(source["sha256"])))
        if digest(packet) != source["sha256"]:
            raise ValueError("benchmark parent source drift")
        if source["query"]["kind"] == "calendar":
            sessions.update(
                r["calendar_date"]
                for r in packet["rows"]
                if r["is_trading_day"] == "1"
                and parent["meta"]["date_start"] <= r["calendar_date"] <= parent["meta"]["date_end"]
            )
        else:
            for r in packet["rows"]:
                if r["date"] in native:
                    raise ValueError("duplicate native benchmark day")
                native[r["date"]] = dict(close=r["close"], source_hash=source["sha256"])
    if not sessions:
        raise _error(422, "BENCHMARK_CALENDAR_MISSING", "父版本缺少冻结交易日历。")
    queries = []
    for start, end in query_segments(
        date.fromisoformat(min(sessions)), date.fromisoformat(max(sessions))
    ):
        days = sorted(s for s in sessions if str(start) <= s <= str(end))
        if days:
            queries.extend(
                dict(kind="official_index", code=code, start=days[0], end=days[-1])
                for code in BASIS
            )
    return dict(
        start=parent["meta"]["date_start"],
        end=parent["meta"]["date_end"],
        sessions=sorted(sessions),
        native=native,
        queries=queries,
        source_url=URL,
    )


def extract_index(document, query):
    raw = json.loads(document, parse_float=Decimal)
    if (
        raw.get("success") is not True
        or not isinstance(raw.get("data"), list)
        or len(raw["data"]) > 20000
    ):
        raise ValueError("official index response status or row bound")
    rows, seen = [], set()
    for row in raw["data"]:
        day = str(datetime.strptime(row["tradeDate"], "%Y%m%d").date())
        close = Decimal(str(row["close"]))
        if (
            row["indexCode"] != query["code"]
            or not query["start"] <= day <= query["end"]
            or day in seen
            or not close.is_finite()
            or close <= 0
        ):
            raise ValueError("official index scope/close/duplicate mismatch")
        seen.add(day)
        rows.append(dict(date=day, close=decimal_text(close)))
    return sorted(rows, key=lambda r: r["date"])


async def fetch_index(query, store):
    if (
        set(query) != {"kind", "code", "start", "end"}
        or query["kind"] != "official_index"
        or query["code"] not in BASIS
    ):
        raise ValueError("fixed official index query required")
    start, end = date.fromisoformat(query["start"]), date.fromisoformat(query["end"])
    if end < start or (end - start).days > 1096:
        raise ValueError("bounded index query required")
    params = dict(
        indexCode=query["code"],
        startDate=query["start"].replace("-", ""),
        endDate=query["end"].replace("-", ""),
    )
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", URL, params=params) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 4 * 1024 * 1024:
                    raise ValueError("official index document byte bound")
                chunks.append(chunk)
    document = b"".join(chunks)
    rows = await asyncio.to_thread(extract_index, document, query)
    blob = store.blobs.put_bytes(document)
    return dict(
        provider="csindex_official",
        query=query,
        rows=rows,
        retrieved_at=datetime.now(UTC).isoformat(),
        document_sha256=blob.ref.sha256,
        document_size=size,
        source_url=URL,
    )


def inspect_benchmark(packets, target, store):
    series = {code: {} for code in BASIS}
    sources, seen, checks = [], set(), []
    for packet in packets:
        query = packet["query"]
        if (
            query not in target["queries"]
            or digest(query) in seen
            or packet["provider"] != "csindex_official"
            or packet["source_url"] != URL
        ):
            raise ValueError("official benchmark target/duplicate mismatch")
        seen.add(digest(query))
        document = store.blobs.get_bytes(BlobRef(packet["document_sha256"]))
        if (
            len(document) != packet["document_size"]
            or hashlib.sha256(document).hexdigest() != packet["document_sha256"]
            or extract_index(document, query) != packet["rows"]
        ):
            raise ValueError("official index extraction drift")
        for row in packet["rows"]:
            if row["date"] not in target["sessions"] or row["date"] in series[query["code"]]:
                raise ValueError("official index non-session or overlapping partition")
            series[query["code"]][row["date"]] = row["close"]
        sources.append(
            dict(
                query=query,
                source_url=URL,
                document_sha256=packet["document_sha256"],
                packet_sha256=digest(packet),
                row_count=len(packet["rows"]),
                retrieved_at=packet["retrieved_at"],
            )
        )
    for day, close in sorted(series["000300"].items()):
        native = target["native"].get(day, {})
        checks.append(
            dict(
                date=day,
                source_hash=native.get("source_hash"),
                **compare_value(
                    native.get("close"), close, "CSI300_price_close", "CSI300_price_close", "0.005"
                ),
            )
        )
    counts = {
        name: sum(c["status"] == name for c in checks)
        for name in ("MATCH", "CONFLICT", "METHOD_DIFFERENCE", "UNAVAILABLE")
    }
    summaries = []
    for code, basis in BASIS.items():
        points = series[code]
        missing = sorted(set(target["sessions"]) - set(points))
        summaries.append(
            dict(
                code=code,
                return_basis=basis,
                row_count=len(points),
                start=min(points) if points else None,
                end=max(points) if points else None,
                missing_session_count=len(missing),
                coverage="COVERED" if not missing else "INCOMPLETE",
                points=[dict(date=d, close=v) for d, v in sorted(points.items())],
                independent_check="MATCH"
                if code == "000300" and not missing and counts["MATCH"] == len(target["sessions"])
                else "UNAVAILABLE"
                if code == "H00300"
                else "CONFLICT"
                if counts["CONFLICT"]
                else "UNAVAILABLE",
            )
        )
    return dict(
        schema_version="backtest-benchmark-audit-v1",
        start=target["start"],
        end=target["end"],
        expected_session_count=len(target["sessions"]),
        sources=sources,
        series=summaries,
        checks=checks,
        check_counts=counts,
        complete=len(seen) == len(target["queries"]),
        metric_status="UNVERIFIED",
        execution_status="BLOCKED",
        gaps=[
            "价格指数已按冻结日历逐日核对；同项MATCH不证明日历、股票池或财报历史版本完整。",
            "全收益指数单独保留，不与价格指数混拼；尚无第二独立来源或公布收益基准核对，仍UNVERIFIED。",
            "全市场财报最初及修订档案、完整公司行为和成交限制未准入，不执行选股、成交或净值计算。",
        ],
    )
