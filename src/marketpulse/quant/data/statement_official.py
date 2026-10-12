"""Fixed, reviewed issuer documents; no arbitrary URL or inferred accounting basis."""

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from functools import lru_cache
from io import BytesIO

import httpx

from ..contracts import canonical, digest
from ..domain import decimal_text

ISSUER = "ins_c317bc1b5fc95003aa6e556c8045a5b8"
PROFILES = {
    "midea-2023q1-v1": dict(
        url="https://static.cninfo.com.cn/finalpage/2023-04-29/1216695016.PDF",
        sha256="a292fe0c0abeb8d47e4db0868bf9e398a19cd47f75d6d14937013bd344cbfb74",
        period_end="2023-03-31",
        publication_date="2023-04-29",
        summary_page=1,
        income_page=7,
        page_count=10,
        checkpoints=dict(
            parent_net_profit="8041853",
            consolidated_net_profit="8145732",
            minority_profit="103879",
            revenue="96262922",
            previous_revenue="90381167",
            parent_equity="152352338",
            weighted_roe="5.46",
            revenue_yoy="6.51",
        ),
    ),
    "midea-2023fy-v1": dict(
        url="https://static.cninfo.com.cn/finalpage/2024-03-28/1219429374.PDF",
        sha256="22f4915d91ada74a37eab8d074aef4c15420f3b1a8b5f5bd5777c00f97d58b86",
        period_end="2023-12-31",
        publication_date="2024-03-28",
        summary_page=10,
        income_page=161,
        page_count=291,
        checkpoints=dict(
            parent_net_profit="33719935",
            consolidated_net_profit="33745352",
            minority_profit="25417",
            revenue="372037280",
            previous_revenue="343917531",
            parent_equity="162878825",
            weighted_roe="22.23",
            revenue_yoy="8.18",
        ),
    ),
    "midea-2024q1-v1": dict(
        url="https://static.cninfo.com.cn/finalpage/2024-04-30/1219910111.PDF",
        sha256="6dd7c1759a73b2209bdf189266eb2761010c927423318e7a5250e5bd83de81e1",
        period_end="2024-03-31",
        publication_date="2024-04-30",
        summary_page=2,
        income_page=9,
        page_count=13,
        checkpoints=dict(
            parent_net_profit="9000007",
            consolidated_net_profit="9036321",
            minority_profit="36314",
            revenue="106101612",
            previous_revenue="96262922",
            parent_equity="172498707",
            weighted_roe="5.37",
            revenue_yoy="10.22",
        ),
    ),
}


def extract_statement(document, profile_id):
    return json.loads(_extract_statement(document, profile_id, digest(PROFILES[profile_id])))


@lru_cache(maxsize=4)
def _extract_statement(document, profile_id, profile_hash):
    from pypdf import PdfReader

    profile = PROFILES[profile_id]
    if hashlib.sha256(document).hexdigest() != profile["sha256"]:
        raise ValueError("official document differs from reviewed version")
    pages = PdfReader(BytesIO(document)).pages
    if len(pages) != profile["page_count"]:
        raise ValueError("official page count mismatch")
    summary = re.sub(r"\s+", " ", pages[profile["summary_page"] - 1].extract_text())
    income = re.sub(r"\s+", " ", pages[profile["income_page"] - 1].extract_text())
    if "人民币千元" not in income or "合并" not in income or "公司" not in income:
        raise ValueError("explicit consolidated CNY-thousand statement required")
    revenue = r"营业收入[（(]千元[）)]\s+([\d,]+)\s+([\d,]+)\s+([\d.]+)%"
    patterns = {
        "parent_net_profit": (summary, r"归属于上市公司股东的净利润[（(]千元[）)]\s+([\d,]+)", 1),
        "revenue": (summary, revenue, 1),
        "previous_revenue": (summary, revenue, 2),
        "revenue_yoy": (summary, revenue, 3),
        "parent_equity": (
            summary,
            r"归属于上市公司股东的(?:所有者权益|净资产)[（(]千元[）)]\s+([\d,]+)",
            1,
        ),
        "weighted_roe": (summary, r"加权平均净资产收益率\s+([\d.]+)%", 1),
        "consolidated_net_profit": (income, r"[四五]、净利润\s+([\d,]+)", 1),
        "minority_profit": (income, r"少数股东损益\s+([\d,]+)", 1),
    }
    raw = {}
    for key, (text, pattern, group) in patterns.items():
        matches = list(re.finditer(pattern, text))
        if len(matches) != 1:
            raise ValueError("ambiguous reviewed statement field: " + key)
        raw[key] = matches[0].group(group).replace(",", "")
    if raw != profile["checkpoints"]:
        raise ValueError("independently transcribed official checkpoints differ")
    parent = re.findall(r"归属于母公司股东的\s*净利润\s+([\d,]+)", income)
    if len(parent) != 1 or parent[0].replace(",", "") != raw["parent_net_profit"]:
        raise ValueError("summary and consolidated statement parent profit differ")
    with localcontext() as context:
        context.prec = 40
        values = {
            key: decimal_text(
                Decimal(value) / 100
                if key in {"weighted_roe", "revenue_yoy"}
                else Decimal(value) * 1000
            )
            for key, value in raw.items()
        }
        if Decimal(values["parent_net_profit"]) + Decimal(values["minority_profit"]) != Decimal(
            values["consolidated_net_profit"]
        ):
            raise ValueError("official parent plus minority reconciliation differs")
    return canonical(
        dict(
            profile_id=profile_id,
            period_end=profile["period_end"],
            publication_date=profile["publication_date"],
            source_url=profile["url"],
            document_sha256=profile["sha256"],
            source_hash=profile["sha256"],
            source_pages=dict(summary=profile["summary_page"], income=profile["income_page"]),
            monetary_unit="CNY",
            source_monetary_unit="CNY_thousand",
            **values,
        )
    )


async def fetch_statement(profile_id, store):
    """Only fixed HTTPS documents, bounded bytes, no redirects, no credentials."""
    profile = PROFILES[profile_id]
    async with httpx.AsyncClient(timeout=35, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", profile["url"]) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 12 * 1024 * 1024:
                    raise ValueError("official document byte bound exceeded")
                chunks.append(chunk)
    document = b"".join(chunks)
    if not document.startswith(b"%PDF"):
        raise ValueError("official response is not PDF")
    row = await asyncio.to_thread(extract_statement, document, profile_id)
    store.blobs.put_bytes(document)
    return dict(
        provider="cninfo_official_reviewed",
        query=dict(kind="official_filing", profile_id=profile_id),
        rows=[row],
        retrieved_at=datetime.now(UTC).isoformat(),
        document_size=size,
    )
