"""Parse each exchange's official annual closures, with archived trading rules."""

import html
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from functools import lru_cache

from pypdf import PdfReader

from ...contracts import digest
from .contracts import VerifiedSessionCalendar

URLS = {
    "SSE": (
        "https://www.sse.com.cn/disclosure/dealinstruc/closed/",
        "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/10816482/files/72a6404194404b898258b6edfc5f6e0f.docx",
    ),
    "SZSE": (
        "https://investor.szse.cn/disclosure/notice/general/t20251222_618087.html",
        "https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf",
    ),
}


def plain(value):
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", value, flags=re.S | re.I)
    return "".join(html.unescape(re.sub("<[^>]*>", "", value)).split())


def parse_annual(text, year):
    text = plain(text)
    if year != 2026 or not re.search(r"2026年(?:部分节假日)?休市安排", text):
        raise ValueError("尚未核定该年度的交易所休市公告")
    closures = set()
    for label in ("元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节"):
        pattern = (
            label
            + r"：(?P<m>\d{1,2})月(?P<d>\d{1,2})日[^至]*至"
            + r"(?P<n>\d{1,2})月(?P<e>\d{1,2})日[^休]*休市"
        )
        found = re.search(pattern, text)
        if not found:
            raise ValueError("休市公告结构缺失：" + label)
        start, end = (
            date(year, int(found["m"]), int(found["d"])),
            date(year, int(found["n"]), int(found["e"])),
        )
        if end < start or (end - start).days > 15:
            raise ValueError("休市日期范围无效")
        closures.update(start + timedelta(days=i) for i in range((end - start).days + 1))
    start, end = date(year, 1, 1), date(year, 12, 31)
    return tuple(
        d
        for i in range((end - start).days + 1)
        if (d := start + timedelta(days=i)).weekday() < 5 and d not in closures
    )


@lru_cache(maxsize=8)
def rule_text(raw):
    # Pure parsing only: callers still read/hash-check every source before supplying bytes.
    if raw.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entry = archive.getinfo("word/document.xml")
            if entry.file_size > 5 * 1024 * 1024:
                raise ValueError("规则正文过大")
            return "".join(ET.fromstring(archive.read(entry)).itertext())
    return "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(raw)).pages)


def load_calendar(client, exchange, year=2026):
    annual_url, rules_url = URLS[exchange]
    annual, text = client.fetch(
        "calendar_" + exchange, annual_url, family=exchange, encoding="utf8"
    )
    binary = rules_url.endswith((".pdf", ".docx"))
    rules, value = client.fetch(
        "rules_" + exchange,
        rules_url,
        family=exchange,
        binary=binary,
        encoding=None if binary else "utf8",
    )
    ruletext = rule_text(value) if binary else value
    normalized = plain(ruletext)
    if "每周一至周五" not in normalized or not re.search("前收盘[价格]*为.*?除权", normalized):
        raise ValueError("交易时间或除权息参考价规则未能核对")
    sessions = parse_annual(text, year)
    material = dict(
        exchange=exchange,
        year=year,
        sessions=[str(s) for s in sessions],
        annual_hash=annual["sha256"],
        rules_hash=rules["sha256"],
        parser="official-annual-2026-v1",
    )
    return VerifiedSessionCalendar(
        exchange=exchange,
        coverage_start=date(year, 1, 1),
        coverage_end=date(year, 12, 31),
        sessions=sessions,
        source_refs=(annual["blob_ref"], rules["blob_ref"]),
        verified_at=max(annual["fetched_at"], rules["fetched_at"]),
        content_hash=digest(material),
    )
