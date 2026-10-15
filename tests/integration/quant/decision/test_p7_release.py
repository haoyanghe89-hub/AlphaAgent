import json

from fastapi.testclient import TestClient
from test_decision_api import setup as setup
from test_flow_integration import snapshot

from marketpulse.quant.decision import flow_sources


def test_acceptance_absence_sealed_record_and_corruption(setup):
    app, _ = setup
    repo = app.state.decision_store
    with TestClient(app) as client:
        response = client.get("/api/quant/decision/acceptance")
        assert response.json()["status"] == "NOT_RUN"
        pointer = repo.base.legacy_root.parent / "decision-release.json"
        report = dict(
            schema_version="decision-release-v1",
            status="SELF_TESTED",
            executed=48,
            predictive_validity="UNVERIFIED",
            user_acceptance="PENDING",
        )
        pointer.write_text(json.dumps(dict(report_ref=repo.blob(report))), encoding="utf8")
        result = client.get("/api/quant/decision/acceptance").json()
        assert result["executed"] == 48 and result["predictive_validity"] == "UNVERIFIED"
        assert client.get("/api/quant/decision/capabilities").json()["evaluation"]["executed"] == 48
        pointer.write_text(
            json.dumps(dict(report_ref=repo.blob(dict(schema_version="unknown")))), encoding="utf8"
        )
        assert client.get("/api/quant/decision/acceptance").status_code == 409


def test_tushare_margin_units_identity_and_dated_missing(setup, monkeypatch):
    app, _ = setup
    monkeypatch.setattr(flow_sources, "rule_text", lambda _: "每周一至周五")
    repo = app.state.decision_store
    subject = dict(security_id="688012.SH", code="688012", symbol="sh688012", exchange="SSE")
    source_snapshot = snapshot(repo, subject)
    dates = flow_sources.window_spec(
        source_snapshot["calendar"], source_snapshot["captured_at"]
    ).sessions
    rows = [
        dict(
            ts_code=subject["security_id"],
            trade_date=str(d).replace("-", ""),
            rzye=100 + i * 10,
            rzmre=30,
            rzche=20,
        )
        for i, d in enumerate(dates)
    ]
    fields = list(rows[0])

    def source(values):
        raw = repo.blob(
            dict(code=0, data=dict(fields=fields, items=[[r[f] for f in fields] for r in values]))
        )
        return dict(
            key="synthetic-ts-margin",
            url="https://api.tushare.pro",
            source_family="TUSHARE",
            api_name="margin_detail",
            parameters=dict(ts_code=subject["security_id"]),
            blob_ref=raw,
            sha256=raw.rsplit("/", 1)[-1],
            fetched_at=source_snapshot["captured_at"],
        )

    source_snapshot["responses"] = [dict(role="tushare_margin", source=source(rows))]
    result = flow_sources.rebuild(repo, source_snapshot)
    assert result["inputs"]["margin_balance"][-1]["value"] == str(100 + (len(dates) - 1) * 10)
    assert result["inputs"]["margin_buy"][-1]["unit"] == "CNY"
    rows[-1]["ts_code"] = "002371.SZ"
    source_snapshot["responses"][0]["source"] = source(rows)
    result = flow_sources.rebuild(repo, source_snapshot)
    assert result["inputs"]["margin_balance"] == []
    assert any("identity" in g["reason"] for g in result["gaps"])


def test_latest_joint_window_uses_published_margin_date_and_keeps_legacy_missing(
    setup, monkeypatch
):
    import copy

    import pytest

    repo = setup[0].state.decision_store
    monkeypatch.setattr(flow_sources, "rule_text", lambda _: "每周一至周五")
    subject = dict(security_id="688012.SH", code="688012", symbol="sh688012", exchange="SSE")
    snap = snapshot(repo, subject)
    current = flow_sources.window_spec(snap["calendar"], snap["captured_at"]).end_date
    eligible = [d for d in snap["calendar"]["calendar"]["sessions"] if d <= str(current)]
    dates = eligible[-62:-1]
    rows = [
        dict(
            ts_code=subject["security_id"],
            trade_date=d.replace("-", ""),
            rzye=100 + i * 10,
            rzmre=30,
            rzche=20,
        )
        for i, d in enumerate(dates)
    ]
    raw = repo.blob(
        dict(code=0, data=dict(fields=list(rows[0]), items=[list(r.values()) for r in rows]))
    )
    source = dict(
        key="synthetic-aligned-margin",
        source_family="TUSHARE",
        api_name="margin_detail",
        parameters=dict(ts_code=subject["security_id"]),
        blob_ref=raw,
        sha256=raw.rsplit("/", 1)[-1],
        fetched_at=snap["captured_at"],
    )
    snap["responses"] = [dict(role="tushare_margin", source=source)]
    old = flow_sources.rebuild(repo, snap)
    assert old["report"]["windows"][0]["metrics"][0]["value"] is None
    snap["aligned_session"] = dates[-1]
    result = flow_sources.rebuild(repo, snap)
    assert result["spec"]["end_date"] == dates[-1]
    assert result["alignment"]["latest_closed_session"] == str(current)
    assert [w["metrics"][0]["value"] for w in result["report"]["windows"]] == ["50", "200", "600"]
    damaged = copy.deepcopy(snap)
    damaged["aligned_session"] = dates[-2]
    with pytest.raises(ValueError):
        flow_sources.rebuild(repo, damaged)
