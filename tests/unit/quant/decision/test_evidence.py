"""Fixed synthetic materials test provenance; they are not market forecasts."""

from datetime import UTC, datetime

import pytest

from marketpulse.quant.decision.disclosures import classify, select_announcements
from marketpulse.quant.decision.evidence import deduplicate, retrieve, verify_document


def document(**changes):
    value = dict(
        document_id="D1",
        subject_id="688012.SH",
        title="合成半年报",
        category="periodic",
        original_source_id="CNINFO:123",
        content_hash="a" * 64,
        raw_ref="raw",
        text_ref="parsed",
        text_hash="b" * 64,
        pages=["合成业务资料：公司说明需求变化。"],
        fetched_at="2026-10-12T03:00:00+00:00",
        available_at="2026-10-12T03:00:00+00:00",
        published_at=None,
        time_precision="unknown",
        period_start=None,
        period_end=None,
        url="https://static.cninfo.com.cn/finalpage/2026-10-12/123.PDF",
        aliases=[],
        limitations=["合成夹具"],
    )
    return dict(value, **changes)


def test_reprints_count_once_but_revisions_and_conflicts_are_retained():
    original = document()
    mirror = document(document_id="D2", url="https://example.org/mirror")
    revised = document(document_id="D3", content_hash="c" * 64)
    result = deduplicate([original, mirror, revised])
    assert len(result) == 2
    assert len(result[0]["aliases"]) == 1
    assert all(item["limitations"] for item in result)


def test_no_first_fetch_evidence_can_enter_an_earlier_cutoff():
    assert retrieve([document()], "需求", datetime(2026, 9, 1, tzinfo=UTC)) == []
    result = retrieve([document()], "需求", datetime(2026, 10, 12, 4, tzinfo=UTC))
    assert result[0].locator.page == 1
    assert result[0].locator.start == 0


def test_pdf_page_and_text_hash_must_match_archive():
    with pytest.raises(ValueError, match="parsed"):
        verify_document(document(), {"pages": ["被篡改的合成文字"]})


def test_typed_selection_does_not_just_take_latest_two_or_invent_urls():
    rows = [
        dict(
            announcementId="1",
            secCode="688012",
            announcementTitle="2026年半年度报告",
            adjunctUrl="finalpage/2026-08-20/1.PDF",
            announcementTime=1787174400000,
        ),
        dict(
            announcementId="2",
            secCode="688012",
            announcementTitle="2026年半年度报告摘要",
            adjunctUrl="finalpage/2026-08-20/2.PDF",
            announcementTime=1787174400000,
        ),
        dict(
            announcementId="3",
            secCode="688012",
            announcementTitle="投资者关系活动记录表",
            adjunctUrl="https://evil.invalid/3.PDF",
            announcementTime=1787174400000,
        ),
    ]
    assert classify("2026年半年度报告") == "periodic"
    assert classify("2026年业绩预告更正公告") == "earnings"
    assert classify("投资者关系活动记录表") == "investor"
    assert classify("投资者关系管理办法（2025年8月）") is None
    assert [
        v["announcementId"] for v in select_announcements(rows, "periodic", "经营", "688012")
    ] == ["1"]
    with pytest.raises(ValueError, match="URL"):
        select_announcements(rows, "investor", "调研", "688012")
