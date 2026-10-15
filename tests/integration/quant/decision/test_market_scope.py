"""Isolated market scope fixtures; never presented as live turnover or predictions."""

import copy
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_decision_api import setup as setup
from test_flow_integration import ANNUAL

from marketpulse.quant.contracts import digest
from marketpulse.quant.decision import api, market_scan, versions
from marketpulse.quant.decision.contracts import ResearchRequest
from marketpulse.quant.decision.next_session.calendar import parse_annual

STAMP = "2026-10-13T10:00:00+00:00"


def market_fixture(repo, missing=False):
    entries, sources = [], []

    def source(key, value):
        ref = repo.raw(value if isinstance(value, bytes) else json.dumps(value).encode())
        row = dict(
            key=key,
            url="https://proxy.finance.qq.com/test",
            source_family="SYNTHETIC",
            blob_ref=ref,
            sha256=ref.rsplit("/", 1)[-1],
            fetched_at=STAMP,
        )
        sources.append(row)
        return row

    def entry(role, value):
        entries.append(dict(role=role, source=source(role, value)))

    annual = source("calendar", ANNUAL.encode())
    rule = source("rule", b"synthetic rules")
    sessions = [str(d) for d in parse_annual(ANNUAL, 2026)]
    days = [d for d in sessions if d <= "2026-10-13"][-21:]
    boards = [dict(f12="BK0001", f14="半导体设备"), dict(f12="BK0002", f14="集成电路制造")]
    entry(
        "catalog:1",
        dict(data=dict(total=3, diff=boards + [dict(f12="BK0003", f14="未接入合成行业")])),
    )
    for i, b in enumerate(boards):
        code = "688012" if i == 0 else "688981"
        entry(
            "members:" + b["f12"],
            dict(data=dict(total=1, diff=[dict(f12=code, f13=1, f14="合成" + str(i))])),
        )
        rows = [[d, "10", "10", "11", "9", "1", {}, "0", "3" if i == 0 else "1"] for d in days]
        if missing and i == 1:
            rows.pop(5)
        entry("daily:" + code, dict(data={"sh" + code: dict(day=rows)}))
    return dict(
        version=market_scan.VERSION,
        entries=entries,
        gaps=[],
        boards=boards,
        market_at=STAMP,
        cutoff_at=STAMP,
        sessions=days,
        calendar=dict(sessions=sessions, source_refs=[annual["blob_ref"], rule["blob_ref"]]),
        sources=sources,
    )


def complete_market(repo):
    payload = ResearchRequest(
        request_key=uuid4(),
        scope="market",
        entity="设备与制造观察范围",
        research_mode="market_scan",
        question="合成范围测试",
    ).model_dump(mode="json")
    state, _ = repo.create(payload, "fixture")
    snapshot = market_fixture(repo)
    data = market_scan.rebuild(repo, snapshot)
    data["market_snapshot_ref"] = repo.blob(snapshot)
    state.update(
        status="COMPLETED",
        title=data["report"]["title"],
        result_ref=repo.blob(data),
        result_sha256=digest(data),
    )
    state["calls"] = [dict(status="COMPLETED", source=s) for s in snapshot["sources"]]
    repo.save(state)
    return state


def test_fixed_scope_arithmetic_and_tamper_guard(setup):
    app, _ = setup
    repo = app.state.decision_store
    snapshot = market_fixture(repo)
    data = market_scan.rebuild(repo, snapshot)
    assert [r["mean_20_share"] for r in data["metrics"]] == ["0.75", "0.25"]
    assert data["metrics"][0]["change_5_share"] == "0"
    assert data["coverage"]["full_market_share_status"] == "UNAVAILABLE"
    assert data["coverage"]["unconnected"][0]["f14"] == "未接入合成行业"
    data["market_snapshot_ref"] = repo.blob(snapshot)
    market_scan.verify(repo, data)
    data["metrics"][0]["mean_20_share"] = "0.8"
    with pytest.raises(ValueError, match="changed"):
        market_scan.verify(repo, data)


def test_missing_member_never_shrinks_fixed_denominator(setup):
    app, _ = setup
    data = market_scan.rebuild(
        app.state.decision_store, market_fixture(app.state.decision_store, True)
    )
    assert data["selection"]["ranking"] == []
    assert all(r["mean_20_share"] is None for r in data["metrics"])
    assert data["coverage"]["members_total"] == 2
    assert data["coverage"]["members_available"] == 1
    assert all(r["value"] is None for r in data["heat_inputs"]["market_turnover"])
    assert data["metrics"][0]["last_turnover"] == "30000"


def test_unavailable_industry_still_allows_a_separate_real_price_contract(setup):
    app, _ = setup
    repo = app.state.decision_store
    original = market_fixture(repo)
    entry = next(e for e in original["entries"] if e["role"] == "daily:688012")
    rows = json.loads(repo.raw_bytes(entry["source"]["blob_ref"]))["data"]["sh688012"]["day"]
    raw = repo.raw(json.dumps(dict(data=dict(sh000300=dict(day=rows)))).encode())
    source = dict(entry["source"], key="market_csi300", blob_ref=raw, sha256=raw.rsplit("/", 1)[-1])
    snapshot = dict(
        version=market_scan.PARTIAL_VERSION,
        calendar=original["calendar"],
        sessions=original["sessions"],
        market_at=STAMP,
        cutoff_at=STAMP,
        index_source=source,
        reason="SYNTHETIC: industry unavailable",
        sources=[source],
    )
    data = market_scan.rebuild(repo, snapshot)
    assert data["report"]["research_status"] == "PARTIAL"
    assert data["market_index"]["return_20"] == "0"
    assert data["metrics"] == [] and data["selection"]["ranking"] == []
    assert data["coverage"]["members_total"] is None
    assert data["heat_inputs"] is None
    data["market_snapshot_ref"] = repo.blob(snapshot)
    market_scan.verify(repo, data)
    data["market_index"]["return_20"] = "0.5"
    with pytest.raises(ValueError, match="changed"):
        market_scan.verify(repo, data)


def test_refresh_is_new_immutable_idempotent_task_and_rejects_historical_scan(setup, monkeypatch):
    app, _ = setup
    repo = app.state.decision_store
    parent = complete_market(repo)
    original = repo.get(parent["run_id"])
    scheduled = []
    monkeypatch.setattr(api, "schedule", lambda request, state: scheduled.append(state["run_id"]))
    key = dict(request_key=str(uuid4()))
    with TestClient(app) as client:
        path = "/api/quant/decision/runs/" + parent["run_id"] + "/refresh"
        first = client.post(path, json=key)
        assert first.status_code == 202
        child = first.json()
        assert child["run_id"] != parent["run_id"] and child["parent_run_id"] == parent["run_id"]
        assert client.post(path, json=key).json()["run_id"] == child["run_id"]
        assert len(scheduled) == 1 and repo.get(parent["run_id"]) == original
        assert client.post(path, json=dict(key, outlook="FAVORABLE")).status_code == 422
        p = dict(parent["request"], request_key=str(uuid4()), mode="historical", cutoff_at=STAMP)
        assert client.post("/api/quant/decision/runs", json=p).status_code == 422
        assert (
            client.get("/api/quant/decision/runs/" + parent["run_id"] + "/export").status_code
            == 200
        )


def test_no_new_facts_does_not_invent_a_change_story(setup):
    app, _ = setup
    parent = app.state.decision_store.get(complete_market(app.state.decision_store)["run_id"])
    child = copy.deepcopy(parent)
    child.update(run_id="RUN-DEC-" + uuid4().hex, parent_run_id=parent["run_id"])
    result = versions.compare(parent, child)
    assert not result["evidence_changed"] and not result["conclusion_changed"]
    child["data"]["report"]["summary"] = "措辞改变但没有新事实"
    result = versions.compare(parent, child)
    assert not result["evidence_changed"] and result["conclusion_changed"]
    assert "不能归因为新事实" in result["explanation"]
