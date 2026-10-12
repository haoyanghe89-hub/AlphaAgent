"""Reviewed public code-change documents. A single event is not a full archive."""

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO

import httpx

from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.investigation.api import _error

from ..contracts import canonical, digest

PROFILES = {
    "avic-issuer-20250207": dict(
        url="https://disc.static.szse.cn/download/disc/disk03/finalpage/2025-02-07/e2d8b9d3-879b-4587-9e1e-235ebf4c8a44.PDF",
        sha256="90427f29e153de4a4ebd236e25f226ac1b2a72911e0577a667a70076ad495f03",
        publication_date="2025-02-07",
        pages=4,
        family="issuer_disclosure",
        old_code="sz.300114",
        new_code="sz.302132",
        effective_day="2025-02-17",
    ),
    "avic-cni-20250212": dict(
        url="https://www.cnindex.com.cn/zh_information/notices_news/2025/202502/P020250212310734081331.pdf",
        sha256="b33bebb1b6abe915250f3cc1be47a97ec407eb96789afe09cf149e1e8c875f95",
        publication_date="2025-02-12",
        pages=2,
        family="cni_index_notice",
        old_code="sz.300114",
        new_code="sz.302132",
        effective_day="2025-02-17",
    ),
}


def extract_notice(document, profile_id):
    return json.loads(_extract(document, profile_id, digest(PROFILES[profile_id])))


@lru_cache(maxsize=4)
def _extract(document, profile_id, profile_hash):
    from pypdf import PdfReader

    p = PROFILES[profile_id]
    if len(document) > 4 * 1024 * 1024 or hashlib.sha256(document).hexdigest() != p["sha256"]:
        raise ValueError("reviewed notice document mismatch")
    pages = PdfReader(BytesIO(document)).pages
    if len(pages) != p["pages"]:
        raise ValueError("notice page count mismatch")
    compact = re.sub(r"\s+", "", "".join(page.extract_text() for page in pages[:2]))
    if p["family"] == "issuer_disclosure":
        dates = set(re.findall(r"启用日期[：为]*(\d{4})年(\d{1,2})月(\d{1,2})日", compact))
        if "证券代码300114302132" not in re.sub(r"[：]", "", compact):
            raise ValueError("issuer before/after code table missing")
        if "上市主体没有发生实质变化" not in compact or "启用当日设置涨跌幅限制" not in compact:
            raise ValueError("issuer continuity or trading notice missing")
    else:
        dates = set(re.findall(r"决定于(\d{4})年(\d{1,2})月(\d{1,2})日", compact))
        if "中航电测（300114）" not in compact or "中航成飞（302132）" not in compact:
            raise ValueError("index code transition missing")
        if "399001深证成指302132中航成飞300114中航电测" not in compact:
            raise ValueError("index addition/deletion table missing")
    if len(dates) != 1:
        raise ValueError("ambiguous notice effective date")
    y, m, d = map(int, next(iter(dates)))
    effective = f"{y:04}-{m:02}-{d:02}"
    if effective != p["effective_day"]:
        raise ValueError("reviewed effective date mismatch")
    return canonical(
        dict(
            profile_id=profile_id,
            event_kind="symbol_change",
            old_code=p["old_code"],
            new_code=p["new_code"],
            effective_day=effective,
            publication_date=p["publication_date"],
            publication_time=None,
            source_family=p["family"],
            source_pages=[1, 2],
            source_url=p["url"],
            document_sha256=p["sha256"],
            identity_continuity="ISSUER_CONFIRMED"
            if p["family"] == "issuer_disclosure"
            else "INDEX_SYMBOL_ADJUSTMENT",
        )
    )


async def fetch_notice(query, store):
    if (
        set(query) != {"kind", "profile_id"}
        or query["kind"] != "official_notice"
        or query["profile_id"] not in PROFILES
    ):
        raise ValueError("fixed reviewed notice query required")
    profile = PROFILES[query["profile_id"]]
    async with httpx.AsyncClient(timeout=35, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", profile["url"]) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 4 * 1024 * 1024:
                    raise ValueError("notice byte budget exceeded")
                chunks.append(chunk)
    document = b"".join(chunks)
    row = await asyncio.to_thread(extract_notice, document, query["profile_id"])
    store.blobs.put_bytes(document)
    return dict(
        provider="official_reviewed_notice",
        query=query,
        rows=[row],
        retrieved_at=datetime.now(UTC).isoformat(),
        document_size=size,
        document_sha256=profile["sha256"],
        source_url=profile["url"],
    )


def notice_target(runtime, parent_id):
    from .api import load_check

    parent = load_check(runtime, parent_id)
    return target_for_parent(parent)


def target_for_parent(parent):
    if parent["check_status"] != "COMPLETED" or not parent.get("lifecycle_audit"):
        raise _error(422, "NOTICE_PARENT_REQUIRED", "请先完成官方生命周期核对。")
    codes = {c["code"] for c in parent["lifecycle_audit"]["checks"]}
    profiles = [
        k
        for k, p in PROFILES.items()
        if p["old_code"] in codes
        and parent["meta"]["date_start"] <= p["effective_day"] <= parent["meta"]["date_end"]
    ]
    if not profiles:
        raise _error(422, "NOTICE_PROFILE_UNAVAILABLE", "当前范围没有已复核的改码公告配置。")
    return dict(
        profile_ids=profiles,
        profile_hash=digest(PROFILES),
        start=parent["meta"]["date_start"],
        end=parent["meta"]["date_end"],
        lifecycle_hash=digest(parent["lifecycle_audit"]),
        parent_id=parent["run_id"],
    )


def inspect_notices(packets, target, store):
    if (
        target["profile_hash"] != digest(PROFILES)
        or not target["profile_ids"]
        or len(set(target["profile_ids"])) != len(target["profile_ids"])
        or not set(target["profile_ids"]) <= PROFILES.keys()
    ):
        raise ValueError("reviewed notice profiles changed")
    documents, seen = [], set()
    for packet in packets:
        query = packet["query"]
        profile_id = query.get("profile_id")
        if (
            query != dict(kind="official_notice", profile_id=profile_id)
            or profile_id not in target["profile_ids"]
            or profile_id in seen
            or packet["provider"] != "official_reviewed_notice"
        ):
            raise ValueError("notice target or duplicate mismatch")
        profile = PROFILES[profile_id]
        raw = store.blobs.get_bytes(BlobRef(packet["document_sha256"]))
        row = extract_notice(raw, profile_id)
        if (
            len(raw) != packet["document_size"]
            or packet["source_url"] != profile["url"]
            or packet["rows"] != [row]
        ):
            raise ValueError("notice extraction binding mismatch")
        documents.append(
            {**row, "packet_sha256": digest(packet), "retrieved_at": packet["retrieved_at"]}
        )
        seen.add(profile_id)
    events = []
    for pair in sorted(
        {(PROFILES[k]["old_code"], PROFILES[k]["new_code"]) for k in target["profile_ids"]}
    ):
        docs = [d for d in documents if (d["old_code"], d["new_code"]) == pair]
        complete = {d["source_family"] for d in docs} == {"issuer_disclosure", "cni_index_notice"}
        days = {d["effective_day"] for d in docs}
        status = (
            "MATCH"
            if complete and len(days) == 1
            else "CONFLICT"
            if len(days) > 1
            else "UNAVAILABLE"
        )
        events.append(
            dict(
                old_code=pair[0],
                new_code=pair[1],
                effective_day=next(iter(days)) if len(days) == 1 else None,
                status=status,
                identity_status="CONFIRMED_EVENT" if status == "MATCH" else "UNAVAILABLE",
                source_hashes=sorted(d["document_sha256"] for d in docs),
                source_pages=[
                    dict(sha256=d["document_sha256"], pages=d["source_pages"]) for d in docs
                ],
                publication_time_status="DAY_ONLY",
                strict_pit=False,
            )
        )
    return dict(
        schema_version="backtest-notice-audit-v1",
        start=target["start"],
        end=target["end"],
        complete=len(seen) == len(target["profile_ids"]),
        documents=documents,
        events=events,
        parent_lifecycle_hash=target["lifecycle_hash"],
        execution_status="BLOCKED",
        pit_level="PARTIAL",
        gaps=[
            "单项改码公告确认代码延续及生效日；不证明全市场历史身份档案完整。",
            "原文只有披露日，未取得精确发布时间，不能据此升级信号日STRICT。",
            "旧父报告的缺失和冲突保留，新版本只附加证据；没有生成交易或收益。",
        ],
    )
