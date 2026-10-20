"""Locate reviewed anchors in bounded public originals, preserving exact text offsets."""

import io
import re
from functools import lru_cache

from bs4 import BeautifulSoup
from pypdf import PdfReader

from .contracts import SourceDefinition

EXTRACTOR = "knowledge-original-text-v1"
MAX_BYTES = 5 * 1024 * 1024


def extract(raw: bytes, source: SourceDefinition) -> tuple[str, list[dict]]:
    # Cache parsing only; callers still read and hash-check original and saved text each time.
    normalized, locators = _extract(raw, source)
    return normalized, [dict(locator) for locator in locators]


@lru_cache(maxsize=64)
def _extract(raw: bytes, source: SourceDefinition) -> tuple[str, list[dict]]:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("original is empty or exceeds the source limit")
    if source.media_type == "application/pdf":
        if not raw.startswith(b"%PDF"):
            raise ValueError("source is not a PDF original")
        pdf = PdfReader(io.BytesIO(raw))
        if pdf.is_encrypted or len(pdf.pages) > 100:
            raise ValueError("unsupported original PDF")
        content = " ".join(page.extract_text() or "" for page in pdf.pages)
    else:
        soup = BeautifulSoup(raw.decode(source.encoding, errors="strict"), "html.parser")
        for node in soup.select("script,style,nav,footer,header,noscript,form"):
            node.decompose()
        body = soup.select_one(source.selector)
        if body is None:
            raise ValueError("original body was not located")
        content = body.get_text(" ", strip=True)
    normalized = re.sub(r"\s+", " ", content).strip()
    if not 300 <= len(normalized) <= 1_000_000:
        raise ValueError("source body is incomplete or too large")
    locators = []
    for anchor in source.anchors:
        match = re.search(re.escape(anchor), normalized, re.IGNORECASE)
        if not match:
            raise ValueError("reviewed original anchor was not located")
        start, end = max(0, match.start() - 160), min(len(normalized), match.end() + 340)
        locators.append(dict(start=start, end=end, anchor=normalized[match.start() : match.end()]))
    return normalized, locators
