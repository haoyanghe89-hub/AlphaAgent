import copy
from decimal import Decimal

import pytest
from test_decision_api import setup as setup
from test_tushare_candidates import synthetic_tushare

from marketpulse.quant.decision import candidate_margin, candidate_sources, screening
from marketpulse.quant.decision.contracts import EvidenceItem


def test_dated_candidate_financing_is_complete_replayable_and_preserves_v2(setup):
    repo = setup[0].state.decision_store
    snapshot = synthetic_tushare(repo)
    old = candidate_sources.rebuild(repo, snapshot)
    before = copy.deepcopy(old)
    sid = old["selected"][0]
    days = [d for d in snapshot["calendar"]["sessions"] if d <= snapshot["sessions"][-1]][-22:-1]
    rows = [
        dict(ts_code=sid, trade_date=d.replace("-", ""), rzye=100 + i * 10, rzmre=30, rzche=20)
        for i, d in enumerate(days)
    ]
    raw = repo.blob(
        dict(code=0, data=dict(fields=list(rows[0]), items=[list(r.values()) for r in rows]))
    )
    source = dict(
        key="synthetic-financing",
        source_family="TUSHARE",
        api_name="margin_detail",
        parameters=dict(ts_code=sid),
        url="https://api.tushare.pro",
        blob_ref=raw,
        sha256=raw.rsplit("/", 1)[-1],
        fetched_at=snapshot["cutoff_at"],
    )
    snapshot["entries"].append(dict(role="dated_margin:" + sid, source=source))
    snapshot["sources"].append(source)
    snapshot["version"] = candidate_margin.VERSION
    result = candidate_sources.rebuild(repo, snapshot)
    row = next(r for r in result["members"] if r["security_id"] == sid)
    assert row["funding_change"] == "200"
    assert (
        row["funding_window"]["end"] == days[-1] and row["funding_window"]["price_end"] != days[-1]
    )
    ctx = screening.context(result, 3, screening.TUSHARE_PROMPT_VERSION)
    assert (
        next(c for c in ctx["companies"] if c["security_id"] == sid)["funding_window"]
        == row["funding_window"]
    )
    items = [
        EvidenceItem.model_validate(i)
        for i in result["bundle"]["items"]
        if i["method_version"] == candidate_margin.VERSION
    ]
    assert [candidate_margin.verified_numeric(repo, i) for i in items] == [
        Decimal(100),
        Decimal(300),
    ]
    assert before == old
    bad = items[0].model_copy(update=dict(subject_id="002371.SZ"))
    with pytest.raises(ValueError):
        candidate_margin.verified_numeric(repo, bad)


def test_financing_missing_stale_or_wrong_issuer_never_becomes_zero():
    calendar = [f"2026-09-{d:02}" for d in range(1, 25)]
    rows = [
        dict(ts_code="688981.SH", trade_date=d.replace("-", ""), rzye=100, rzmre=30, rzche=20)
        for d in calendar[:-1]
    ]
    days, _ = candidate_margin.window(rows, "688981.SH", calendar, calendar[-1])
    assert len(days) == 21
    for bad in (
        rows[:-2],
        rows[:5],
        [dict(r, ts_code="688012.SH") for r in rows],
        rows[:10] + rows[11:],
    ):
        with pytest.raises(ValueError):
            candidate_margin.window(bad, "688981.SH", calendar, calendar[-1])
