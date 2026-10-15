"""Original bytes, parsed pages and bounded excerpts have separate hashes."""

import copy
import io
import re
from datetime import UTC, datetime

from pypdf import PdfReader

from ..contracts import digest
from .contracts import EvidenceItem, Locator


def deduplicate(documents):
    result = []
    for document in documents:
        previous = next((d for d in result if d["content_hash"] == document["content_hash"]), None)
        if previous:
            if previous["subject_id"] != document["subject_id"]:
                raise ValueError("同一原文绑定了不同证券")
            previous["aliases"].append(
                dict(url=document["url"], original_source_id=document["original_source_id"])
            )
            continue
        doc = copy.deepcopy(document)
        if any(d["original_source_id"] == doc["original_source_id"] for d in result):
            doc["limitations"].append("同一公告标识存在不同原文版本；并存保存，不合并事实。")
        result.append(doc)
    return result


def parse_pdf(repo, subject, announcement, source, raw):
    if not raw.startswith(b"%PDF-"):
        raise ValueError("原文不是 PDF")
    reader = PdfReader(io.BytesIO(raw))
    if reader.is_encrypted or not 0 < len(reader.pages) <= 300:
        raise ValueError("PDF 加密或页数超过上限")
    pages, length = [], 0
    for page in reader.pages:
        text = (page.extract_text() or "").replace("\x00", "")
        length += len(text)
        if length > 1_000_000:
            raise ValueError("解析文本超过上限")
        pages.append(text)
    cover = "".join("".join(pages[:12]).split())
    if subject["name"] not in cover or subject["code"] not in cover:
        raise ValueError("PDF 原文的公司与证券代码未能匹配")
    if not any(p.strip() for p in pages):
        raise ValueError("PDF 无可定位文字；未把 OCR 升级为已核验")
    title = announcement["announcementTitle"]
    period_start, period_end = report_period(title)
    if announcement["category"] == "periodic":
        year = re.search(r"20\d{2}", title)
        if year and year[0] not in cover:
            raise ValueError("PDF 报告年度与目录不一致")
    parsed = dict(parser_version="pypdf-pages-v1", subject_id=subject["security_id"], pages=pages)
    text_ref = repo.blob(parsed)
    published = announcement.get("announcementTime")
    published_at = datetime.fromtimestamp(published / 1000, UTC).isoformat() if published else None
    fetched = source["fetched_at"]
    if published_at and datetime.fromisoformat(published_at) > datetime.fromisoformat(fetched):
        raise ValueError("来源宣称未来披露时点，不能作为当前证据")
    limitations = [
        "公司披露中的解释是公司陈述，不等于独立证实的因果。",
        "首次取得时点作为可用时点，公告日期不证明历史可得。",
    ]
    if "未经审计" in "".join(pages):
        limitations.append("报告注明未经审计。")
    return dict(
        document_id="DOC-" + source["sha256"][:24],
        subject_id=subject["security_id"],
        title=title,
        category=announcement["category"],
        original_source_id="CNINFO:" + str(announcement["announcementId"]),
        url=announcement["original_url"],
        raw_ref=source["blob_ref"],
        content_hash=source["sha256"],
        text_ref=text_ref,
        text_hash=digest(parsed),
        page_count=len(pages),
        published_at=published_at,
        time_precision="day" if published_at else "unknown",
        fetched_at=fetched,
        available_at=fetched,
        period_start=period_start,
        period_end=period_end,
        aliases=[],
        limitations=limitations,
    )


def report_period(title):
    year = re.search(r"(20\d{2})年?", title)
    if not year:
        return None, None
    end = (
        "06-30"
        if "半年度" in title
        else "03-31"
        if "一季度" in title or "第一季度" in title
        else "09-30"
        if "三季度" in title or "第三季度" in title
        else "12-31"
        if "年度报告" in title
        else None
    )
    return (year[1] + "-01-01", year[1] + "-" + end) if end else (None, None)


def verify_document(document, parsed):
    if digest(parsed) != document["text_hash"]:
        raise ValueError("parsed text hash mismatch")
    pages = parsed.get("pages")
    if not isinstance(pages, list) or len(pages) != document.get("page_count", len(pages)):
        raise ValueError("parsed PDF page count mismatch")
    if parsed.get("subject_id") != document["subject_id"]:
        raise ValueError("parsed PDF subject mismatch")
    return pages


def terms(question):
    return set(re.findall(r"[a-z0-9]+", question.lower())) | {
        question[i : i + 2]
        for i in range(len(question) - 1)
        if re.fullmatch(r"[\u4e00-\u9fff]{2}", question[i : i + 2])
    }


def retrieve(documents, question, cutoff, *, limit=12):
    wanted = terms(question) | {"业务", "经营", "收入", "利润", "现金", "需求", "订单", "风险"}
    selected = []
    for doc in documents:
        if datetime.fromisoformat(doc["available_at"]) > cutoff:
            continue
        candidates = []
        for page_no, text in enumerate(doc["pages"], 1):
            if page_no <= 3 and len(doc["pages"]) > 10:
                continue
            for start in range(0, len(text), 650):
                content = text[start : start + 850]
                score = len(terms(content) & wanted)
                if score and len(content.strip()) > 10 and "目录" not in content[:60]:
                    candidates.append((score, page_no, start, content))
        # Per-document cap preserves category diversity instead of one PDF dominating.
        for _, page_no, start, content in sorted(candidates, key=lambda c: (-c[0], c[1], c[2]))[:2]:
            selected.append(
                EvidenceItem(
                    evidence_id=f"EV-{doc['document_id']}-{page_no}-{start}",
                    subject_id=doc["subject_id"],
                    kind="disclosure",
                    source_family="CNINFO",
                    original_source_id=doc["original_source_id"],
                    url=doc["url"],
                    raw_ref=doc["raw_ref"],
                    content_hash=doc["content_hash"],
                    locator=Locator(
                        kind="pdf",
                        path=doc["text_ref"],
                        page=page_no,
                        start=start,
                        end=start + len(content),
                    ),
                    text=content,
                    published_at=doc["published_at"],
                    period_start=doc["period_start"],
                    period_end=doc["period_end"],
                    fetched_at=doc["fetched_at"],
                    available_at=doc["available_at"],
                    time_precision=doc["time_precision"],
                    method_version="lexical-typed-v1",
                    quality_status="AVAILABLE",
                    limitations=tuple(doc["limitations"]),
                )
            )
    return selected[:limit]


def verify_excerpt(item, documents, read_value):
    doc = next(
        (
            d
            for d in documents
            if d["raw_ref"] == item.raw_ref and d["text_ref"] == item.locator.path
        ),
        None,
    )
    if not doc or doc["content_hash"] != item.content_hash:
        raise ValueError("evidence document reference mismatch")
    pages = verify_document(doc, read_value(doc["text_ref"]))
    loc = item.locator
    if (
        not loc.page
        or loc.page > len(pages)
        or loc.start is None
        or loc.end is None
        or loc.end > len(pages[loc.page - 1])
    ):
        raise ValueError("evidence PDF page/character locator mismatch")
    if pages[loc.page - 1][loc.start : loc.end] != item.text:
        raise ValueError("evidence text locator mismatch")
