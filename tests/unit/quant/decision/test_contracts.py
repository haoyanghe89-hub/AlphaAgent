from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from marketpulse.quant.decision.contracts import (
    BudgetProfile,
    CoverageReport,
    EvidenceBundle,
    EvidenceItem,
    ResearchRequest,
    ReviewRecord,
)

AT = datetime.fromisoformat("2026-10-12T11:00:00+08:00")


def evidence(**changes):
    return EvidenceItem.model_validate(
        dict(
            evidence_id="E1",
            subject_id="688012.SH",
            kind="disclosure",
            source_family="CNINFO",
            original_source_id="report-1",
            url="https://www.cninfo.com.cn/",
            raw_ref="blob:raw",
            content_hash="a" * 64,
            locator=dict(kind="pdf", path="business", page=2),
            text="经营资料",
            fetched_at=AT,
            available_at=AT,
            time_precision="day",
            method_version="v1",
            quality_status="AVAILABLE",
            **changes,
        )
    )


@pytest.mark.parametrize("months", [0, 4])
def test_daily_request_cannot_use_medium_term_horizon(months):
    with pytest.raises(ValidationError):
        ResearchRequest(
            request_key=uuid4(),
            question="研究",
            entity="688012",
            cutoff_at=AT,
            horizon_months=months,
        )


def test_client_cannot_assert_review_success():
    with pytest.raises(ValidationError):
        ResearchRequest(
            request_key=uuid4(), question="研究", entity="688012", cutoff_at=AT, reviewed=True
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"max_model_calls": 25},
        {"max_tokens": 60001},
        {"max_seconds": 0},
        {"max_rounds": 3},
    ],
)
def test_budget_cannot_expand_approved_limits(changes):
    with pytest.raises(ValidationError):
        BudgetProfile(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_family": ""},
        {"locator": {"kind": "pdf", "path": "business"}},
        {"value": "12.34", "unit": "万元"},
        {"quality_status": "VERIFIED"},
        {"available_at": "2026-09-01T00:00:00+08:00"},
        {"availability_basis": "DATED_SOURCE_VERSION"},
    ],
)
def test_evidence_rejects_missing_locator_bad_unit_or_backdating(changes):
    base = evidence().model_dump()
    base.update(changes)
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(base)


def test_missing_date_is_not_inferred_from_report_period():
    item = evidence(period_end="2026-06-30")
    assert item.published_at is None
    assert item.available_at == AT


def test_bundle_rejects_future_material():
    with pytest.raises(ValidationError, match="future evidence"):
        EvidenceBundle(
            items=(evidence(),),
            sources=(),
            cutoff_at=AT.replace(month=9),
            coverage=CoverageReport(
                universe_total=1,
                attempted=1,
                available=1,
                missing=0,
                excluded=0,
                universe_ref="u",
                member_refs=("688012.SH",),
                source_dates=(),
                method="v1",
                status="COMPLETE",
            ),
        )


def test_ai_review_is_not_external_review():
    with pytest.raises(ValidationError, match="not external"):
        ReviewRecord(
            identity="EXTERNAL_REVIEWED",
            reviewer="Codex",
            reviewed_at=AT,
            reference="review-v1",
            note="AI自审",
        )


def test_coverage_cannot_hide_missing_members():
    with pytest.raises(ValidationError):
        CoverageReport(
            universe_total=10,
            attempted=8,
            available=8,
            missing=0,
            excluded=0,
            universe_ref="u",
            member_refs=(),
            source_dates=(),
            method="v1",
            status="COMPLETE",
        )
