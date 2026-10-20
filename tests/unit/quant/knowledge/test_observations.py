# ruff: noqa: F811
import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from marketpulse.quant.knowledge import evaluation as e
from marketpulse.quant.knowledge import observations as obs
from marketpulse.quant.knowledge.planning_service import build_report
from marketpulse.quant.knowledge.research_api import router
from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.research_service import run

from .test_context import snapshot, table
from .test_features import market_packet
from .test_research import runtime  # noqa: F401
from .test_technicals import TechnicalPort


def weekdays(start, end):
    start = datetime.strptime(start, "%Y%m%d")
    end = datetime.strptime(end, "%Y%m%d")
    return [
        dict(
            cal_date=(start + timedelta(days=i)).strftime("%Y%m%d"),
            is_open=int((start + timedelta(days=i)).weekday() < 5),
            exchange="SZSE",
        )
        for i in range((end - start).days + 1)
    ]


def fixture():
    initial = market_packet()
    anchor = initial["market_date"]
    initial.update(captured_at="2026-05-20T08:00:00+00:00", sources=[])
    request = InvestmentRequest(
        schema_version="investment-research-v4",
        request_key=uuid4(),
        entities=["000333"],
        horizon_sessions=10,
    ).model_dump(mode="json")
    state = dict(
        request=request, run_id="RUN-IR-" + uuid4().hex, created_at="2026-05-20T08:00:00+00:00"
    )
    spec = e.protocol(state)
    spec.update(calendars={"SZSE": weekdays("20260101", "20260630")}, calendar_sources=[])
    report = build_report(
        InvestmentRequest.model_validate(request).calculation_request(), initial, [], []
    )
    report.update(
        forward_protocol=spec,
        issued_at="2026-05-20T08:01:00+00:00",
        research_opinions=[
            dict(
                security_id="000333.SZ",
                direction="UNFAVORABLE",
                opposing=[dict(claim_id="CX", text="合成测试反证")],
                invalidation=["合成失效条件"],
            )
        ],
    )
    days = e.open_days(spec["calendars"]["SZSE"], "SZSE", anchor)[:10]
    price = initial["stocks"][0]["daily"][-1]["close"]
    daily = [
        dict(
            ts_code="000333.SZ",
            trade_date=day,
            close=price + 2 * (i + 1),
            high=price + 2 * (i + 1) + 0.1,
            low=price + 2 * (i + 1) - 0.1,
            vol=100,
            amount=1000,
        )
        for i, day in enumerate(days)
    ]
    factors = [dict(ts_code="000333.SZ", trade_date=day, adj_factor=1) for day in [anchor] + days]
    update = dict(
        version=e.VERSION,
        through=days[-1],
        captured_at="2026-06-03T10:00:00+00:00",
        calendars=spec["calendars"],
        benchmark=[dict(ts_code="000300.SH", trade_date=day, close=100) for day in days],
        stocks=[dict(security_id="000333.SZ", daily=daily, adjustments=factors, suspensions=[])],
        sources=[],
    )
    return initial, report, update


def test_direction_condition_and_simulation_are_separate_and_baselines_are_same_window():
    initial, report, update = fixture()
    value = e.evaluate(report, initial, update)["stocks"][0]
    assert (
        value["status"] == "MATURED"
        and value["actual_direction"] == "UP"
        and value["correct"] is False
    )
    assert value["baseline_correct"] == dict(
        ALWAYS_UP=True, ALWAYS_DOWN=False, PAST_20_SESSION_SIGN=True
    )
    assert (
        value["first_trigger"]["FROZEN_SELECTED_ROUTE"]
        == update["stocks"][0]["daily"][0]["trade_date"]
    )
    assert value["simulation"]["trades"] == 0 and value["simulation"]["return_after_cost"] is None
    assert value["counterarguments"][0]["status"] == "EVIDENCE_REVIEW_REQUIRED"


@pytest.mark.parametrize("kind", ["missing", "suspended", "factor"])
def test_missing_and_full_suspension_are_not_flat_or_shortened(kind):
    initial, report, update = fixture()
    stock = update["stocks"][0]
    day = stock["daily"].pop(3)["trade_date"]
    if kind == "suspended":
        stock["suspensions"] = [
            dict(ts_code="000333.SZ", trade_date=day, suspend_type="S", suspend_timing=None)
        ]
    if kind == "factor":
        stock["adjustments"].pop(-1)
    value = e.evaluate(report, initial, update)["stocks"][0]
    assert value["status"] == (
        "SUSPENDED_UNSCORABLE" if kind == "suspended" else "DATA_MISSING_UNSCORABLE"
    )
    assert (
        value["correct"] is None
        and value["price_return"] is None
        and value["observed_sessions"] == 10
    )


def test_not_matured_calendar_changes_and_late_issue_remain_in_counts():
    initial, report, update = fixture()
    update["through"] = report["market_date"]
    update["stocks"][0]["daily"] = []
    update["stocks"][0]["adjustments"] = []
    update["benchmark"] = []
    pending = e.evaluate(report, initial, update)["stocks"][0]
    assert (
        pending["status"] == "PENDING"
        and pending["correct"] is None
        and pending["observed_sessions"] == 0
    )
    initial, report, update = fixture()
    update["calendars"] = copy.deepcopy(update["calendars"])
    day = update["stocks"][0]["daily"][0]["trade_date"]
    next(r for r in update["calendars"]["SZSE"] if r["cal_date"] == day)["is_open"] = 0
    assert (
        e.evaluate(report, initial, update)["stocks"][0]["status"] == "CALENDAR_CHANGED_UNSCORABLE"
    )
    initial, report, update = fixture()
    report["issued_at"] = "2026-05-21T03:00:00+00:00"
    late = e.evaluate(report, initial, update)["stocks"][0]
    assert late["status"] == "LATE_ISSUE_UNSCORABLE"
    stats = e.summarize([pending, late])
    assert stats["registered_samples"] == 2 and stats["groups"][0]["accuracy"] is None


def test_split_factor_does_not_create_false_loss():
    initial, report, update = fixture()
    for row in update["stocks"][0]["daily"]:
        row.update(close=row["close"] / 2, high=row["high"] / 2, low=row["low"] / 2)
    for row in update["stocks"][0]["adjustments"][1:]:
        row["adj_factor"] = 2
    value = e.evaluate(report, initial, update)["stocks"][0]
    assert value["status"] == "MATURED" and value["actual_direction"] == "UP"


def test_enhanced_and_fixed_research_are_not_pooled_in_accuracy():
    initial, report, update = fixture()
    sample = e.evaluate(report, initial, update)["stocks"][0]
    enhanced = dict(sample, research_protocol="active-public-research-v1")
    summary = e.summarize([sample, enhanced])
    assert summary["registered_samples"] == 2
    assert len(summary["groups"]) == 2
    assert all(g["registered"] == 1 for g in summary["groups"])
    state = dict(
        request=dict(report["request"], horizon_sessions=10),
        run_id="synthetic",
        created_at=report["issued_at"],
    )
    assert "research_protocol" not in e.protocol(state)
    state["request"]["enhanced_research"] = True
    assert e.protocol(state)["research_protocol"] == "active-public-research-v1"


def archived_calendar(repo, initial, spec):
    start = min(r["cal_date"] for rows in initial["calendars"].values() for r in rows)
    end = (datetime.strptime(spec["start_after"], "%Y%m%d") + timedelta(days=400)).strftime(
        "%Y%m%d"
    )
    rows = weekdays(start, end)
    raw = json.dumps(table(rows)).encode()
    ref = repo.raw(raw)
    return dict(
        calendars={"SZSE": rows},
        calendar_sources=[
            dict(
                role="calendar",
                exchange="SZSE",
                source=dict(
                    blob_ref=ref,
                    sha256=ref.rsplit("/", 1)[1],
                    api_name="trade_cal",
                    url="https://api.tushare.pro",
                    source_family="TUSHARE",
                    fetched_at=datetime.now(UTC).isoformat(),
                    parameters=dict(exchange="SZSE", start_date=start, end_date=end),
                ),
            )
        ],
    )


def pending_snapshot(repo, state, now):
    spec = state["data"]["forward_protocol"]
    local = now.astimezone(obs.SHANGHAI)
    end = (
        local.date() if (local.hour, local.minute) >= (15, 30) else local.date() - timedelta(days=1)
    ).strftime("%Y%m%d")
    start = min(r["cal_date"] for r in spec["calendars"]["SZSE"])
    rows = weekdays(start, end)
    raw = json.dumps(table(rows)).encode()
    ref = repo.raw(raw)
    return dict(
        version=e.VERSION,
        through=end,
        captured_at=now.isoformat(),
        calendars={"SZSE": rows},
        stocks=[dict(security_id="000333.SZ", daily=[], adjustments=[], suspensions=[])],
        benchmark=[],
        sources=[
            dict(
                role="calendar",
                exchange="SZSE",
                source=dict(
                    blob_ref=ref,
                    sha256=ref.rsplit("/", 1)[1],
                    api_name="trade_cal",
                    url="https://api.tushare.pro",
                    source_family="TUSHARE",
                    fetched_at=now.isoformat(),
                    parameters=dict(exchange="SZSE", start_date=start, end_date=end),
                ),
            )
        ],
    )


def test_v4_registration_precedes_models_append_only_observation_and_candidate(
    runtime, monkeypatch, rules
):
    repo, packet, settings = runtime
    monkeypatch.setattr(
        obs, "calendars", lambda client, initial, spec: archived_calendar(repo, initial, spec)
    )
    request = InvestmentRequest(
        schema_version="investment-research-v4", request_key=uuid4(), entities=["000333"]
    )
    state, _ = repo.create(request.model_dump(mode="json"))
    store = obs.ObservationStore(repo)
    assert store.registration(state) and not store.history(state)
    result = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=TechnicalPort(),
            collector=lambda *_: packet,
            context_collector=lambda *_: snapshot(repo, packet),
        )
    )
    assert result["status"] == "COMPLETED", result.get("error")
    before = repo.get(state["run_id"])
    first = store.observe(
        before, collector=lambda client, state, now: pending_snapshot(repo, state, now)
    )
    assert first["status"] == "RECORDED", first.get("error")
    assert first["evaluation"]["stocks"][0]["correct"] is None
    assert repo.get(state["run_id"]) == before
    assert store.append({k: v for k, v in first.items() if k != "observation_id"}) == first
    with store.sessions.begin() as session:
        with pytest.raises(IntegrityError, match="immutable"):
            session.execute(
                text("UPDATE inv_quant_investment_observation SET created_at=created_at")
            )
    candidate = store.suggest(before, rules)
    assert candidate["status"] == "CANDIDATE" and not candidate["retrieval_eligible"]
    assert store.suggest(before, rules)["supplement_id"] == candidate["supplement_id"]
    assert store.summary()["registered_samples"] == 1
    # Acquisition failures append, preserve the last successful observation and remain visible.
    failed = store.observe(
        before, collector=lambda *_: (_ for _ in ()).throw(ValueError("SYNTHETIC unavailable"))
    )
    assert failed["status"] == "ACQUISITION_FAILED"
    view = store.view(before)
    assert (
        view["last_attempt"]["status"] == "ACQUISITION_FAILED"
        and view["latest"]["observation_id"] == first["observation_id"]
    )
    store.append(
        dict(
            kind="ACQUISITION_CHECKPOINT",
            attempt_id="synthetic-crash",
            run_id=before["run_id"],
            created_at=datetime.now(UTC).isoformat(),
            result_ref=before["result_ref"],
            journal={},
        )
    )
    assert store.view(before)["incomplete_acquisitions"] == 1
    # Long daily histories stay in the observation archive, not the candidate's bounded text.
    large = copy.deepcopy(view)
    large["latest"]["observation_id"] = "b" * 64
    for item in large["latest"]["evaluation"]["stocks"]:
        item["points"] = [dict(date="20260101", price="100")] * 1200
        item["signals"] = [dict(date="20260101", risk_reference_breached=True)] * 120
    monkeypatch.setattr(store, "view", lambda *_: large)
    assert len(store.suggest(before, rules)["request"]["text"]) < 20000


def test_registration_and_request_rollback_together_if_initial_registration_fails(
    runtime, monkeypatch
):
    from marketpulse.quant.knowledge.research_store import InvestmentStore

    repo, _, _ = runtime
    original = InvestmentStore.register_in_session

    def failure(self, session, state):
        original(self, session, state)
        raise ValueError("SYNTHETIC before commit")

    monkeypatch.setattr(InvestmentStore, "register_in_session", failure)
    request = InvestmentRequest(
        schema_version="investment-research-v4", request_key=uuid4(), entities=["000333"]
    ).model_dump(mode="json")
    with pytest.raises(ValueError, match="before commit"):
        repo.create(request)
    assert not repo.exists(repo.prefix + request["request_key"].replace("-", ""))
    assert obs.ObservationStore(repo).summary()["registered_samples"] == 0


def test_registration_retains_failed_requests_and_old_requests_are_not_backfilled(runtime):
    repo, _, _ = runtime
    store = obs.ObservationStore(repo)
    old, _ = repo.create(
        InvestmentRequest(request_key=uuid4(), entities=["000333"]).model_dump(mode="json")
    )
    assert store.view(old)["status"] == "NOT_ENROLLED"
    state, _ = repo.create(
        InvestmentRequest(
            schema_version="investment-research-v4",
            request_key=uuid4(),
            entities=["000333", "600036"],
        ).model_dump(mode="json")
    )
    state["status"] = "INTERRUPTED"
    repo.save(state)
    stats = store.summary()
    assert stats["registered_samples"] == 2
    assert stats["groups"][0]["status_counts"] == dict(RESEARCH_INTERRUPTED=2)


def test_v4_local_observation_export_recomputes_and_never_overwrites_report(
    runtime, monkeypatch, tmp_path, rules
):
    import time

    from scripts.verify_investment_research_export import verify

    repo, packet, settings = runtime
    monkeypatch.setattr(
        obs, "calendars", lambda client, initial, spec: archived_calendar(repo, initial, spec)
    )
    app = FastAPI()
    app.include_router(router)
    app.state.research_store = repo.base
    app.state.knowledge_rules = rules
    app.state.settings = settings
    app.state.research_jobs = {}
    app.state.investment_research_port = TechnicalPort()
    app.state.investment_research_collector = lambda *_: packet
    app.state.investment_context_collector = lambda *_: snapshot(repo, packet)
    app.state.investment_observation_collector = lambda client, state, now: pending_snapshot(
        repo, state, now
    )
    definition = InvestmentRequest(
        schema_version="investment-research-v4", request_key=uuid4(), entities=["000333"]
    ).model_dump(mode="json")
    with TestClient(app) as client:
        response = client.post("/api/quant/investment-research/runs", json=definition)
        assert response.status_code == 202
        url = "/api/quant/investment-research/runs/" + response.json()["run_id"]
        for _ in range(100):
            response = client.get(url)
            if response.json()["status"] != "RUNNING":
                break
            time.sleep(0.01)
        assert response.json()["status"] == "COMPLETED", response.json().get("error")
        before = response.json()
        assert (
            client.post(url + "/observe", headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
        assert client.post(url + "/observe").json()["status"] == "RECORDED"
        assert client.get(url).json() == before
        assert (
            client.get("/api/quant/investment-research/evaluation").json()["registered_samples"]
            == 1
        )
        candidate = client.post(url + "/review-candidate").json()
        assert candidate["status"] == "CANDIDATE" and not candidate["retrieval_eligible"]
        export = client.get(url + "/export")
        assert export.status_code == 200
    path = tmp_path / "forward.zip"
    path.write_bytes(export.content)
    assert verify(path)["approved_content_verified"]
