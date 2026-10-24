"""Synthetic software gates; these records never represent prediction accuracy."""

import asyncio
from datetime import date, datetime
from decimal import Decimal
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.quant.contracts import digest
from marketpulse.quant.decision import api as parent_api
from marketpulse.quant.decision.next_session import outcomes, service
from marketpulse.quant.decision.next_session.agents import DailyRoles, Proposal, guard_proposal
from marketpulse.quant.decision.next_session.calendar import parse_annual
from marketpulse.quant.decision.next_session.contracts import (
    NextSessionPrediction,
    VerifiedSessionCalendar,
)
from marketpulse.quant.decision.next_session.store import ExperimentStore
from marketpulse.quant.knowledge.models import KnowledgeDocumentRow
from marketpulse.quant.live.store import ResearchRunRow, ResearchStore

ANNUAL = "2026年休市安排 " + "".join(
    label + "：" + period + "休市。"
    for label, period in [
        ("元旦", "1月1日至1月3日"),
        ("春节", "2月15日至2月23日"),
        ("清明节", "4月4日至4月6日"),
        ("劳动节", "5月1日至5月5日"),
        ("端午节", "6月19日至6月21日"),
        ("中秋节", "9月25日至9月27日"),
        ("国庆节", "10月1日至10月7日"),
    ]
)
NOW = datetime.fromisoformat("2026-10-12T20:01:00+08:00")


@pytest.fixture
def repo(tmp_path):
    engine = create_engine(
        "sqlite:///" + str(tmp_path / "test.sqlite"), connect_args={"check_same_thread": False}
    )
    ResearchRunRow.__table__.create(engine)
    KnowledgeDocumentRow.__table__.create(engine)
    base = ResearchStore(
        sessionmaker(engine),
        LocalContentAddressedBlobStorage(tmp_path / "blobs"),
        tmp_path / "legacy",
    )
    yield ExperimentStore(base)
    engine.dispose()


def calendar(exchange="SSE"):
    return VerifiedSessionCalendar(
        exchange=exchange,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 12, 31),
        sessions=parse_annual(ANNUAL, 2026),
        source_refs=("synthetic-calendar", "synthetic-rules"),
        verified_at=NOW,
        content_hash="a" * 64,
    )


def activate(repo):
    config = (
        service.template()
        .model_copy(
            update=dict(
                enabled=True,
                enabled_at=NOW,
                first_base_session=date(2026, 10, 12),
                model_version="synthetic-model",
            )
        )
        .model_dump(mode="json")
    )
    repo.immutable(
        "DEC-CONFIG-" + config["experiment_id"],
        dict(
            config=config,
            calendars={e: calendar(e).model_dump(mode="json") for e in ("SSE", "SZSE")},
            calls=[],
            policy_hash=service.policy_hash(),
        ),
    )
    return config


def prediction(**changes):
    reason = dict(
        text="合成的软件测试理由",
        evidence_ids=["test-evidence"],
        next_session_mechanism="合成条件机制",
    )
    value = dict(
        prediction_id="DEC-PRED-" + uuid4().hex,
        experiment_id="ALPHA_NEXT_SESSION_V1",
        protocol_id="NEXT_SESSION_EOD_V1",
        universe_version="test-v1",
        security_id="688012.SH",
        base_session="2026-10-12",
        target_session="2026-10-13",
        requested_at="2026-10-12T20:00:00+08:00",
        evidence_sealed_at="2026-10-12T20:00:30+08:00",
        issued_at=NOW,
        input_hash="b" * 64,
        method_versions=["test-v1"],
        model_version="synthetic-model",
        prompt_version="test-v1",
        prediction="UP",
        summary="测试",
        timing_group="20:00–20:15",
        support_ids=["test-evidence"],
        counter_ids=["test-evidence"],
        reasons=[reason],
        opposing_reasons=[reason],
        limitations=["合成夹具"],
        support_level="WEAK",
        review_status="APPROVE",
        review_refs=["synthetic-review"],
        cohort="PRIMARY",
        baselines=[["ALWAYS_UP", "UP"], ["ALWAYS_DOWN", "DOWN"], ["PERSISTENCE", "UP"]],
    )
    return NextSessionPrediction(**dict(value, **changes))


def raw_quotes(repo, close="99.50", reference="99.00", turnover="1000", day="2026-10-13"):
    t = [""] * 50
    t[1] = "合成"
    t[2] = "688012"
    t[3] = close
    t[4] = reference
    t[30] = day.replace("-", "") + "150000"
    t[35] = close + "/10/" + turnover
    s = [""] * 33
    s[0] = "合成"
    s[2] = reference
    s[3] = close
    s[8] = "10" if turnover != "0" else "0"
    s[9] = turnover
    s[30] = day
    s[31] = "15:00:00"
    values = [
        ("TENCENT", 'v_sh688012="' + "~".join(t) + '";'),
        ("SINA", 'var hq_str_sh688012="' + ",".join(s) + '";'),
    ]
    from marketpulse.quant.decision.next_session.prices import quote

    result = []
    for family, text in values:
        ref = repo.raw(text.encode("gb18030"))
        result.append(
            (
                dict(blob_ref=ref, sha256=ref.split("/")[-1]),
                {"sh688012": quote(text, "sh688012", family)},
            )
        )
    return result


def test_e01_calendar_uses_official_closures_and_refuses_unknown_year():
    days = parse_annual(ANNUAL, 2026)
    assert date(2026, 10, 7) not in days and date(2026, 10, 8) in days
    assert date(2026, 9, 25) not in days
    with pytest.raises(ValueError):
        parse_annual(ANNUAL, 2027)


def test_e03_first_primary_is_atomic_and_immutable(repo):
    first = prediction()
    second = prediction(prediction="DOWN")
    assert repo.publish(first) == first
    auxiliary = repo.publish(second)
    assert auxiliary.cohort == "EXPLORATORY" and auxiliary.supersedes_id == first.prediction_id
    assert repo.prediction(first.prediction_id) == first
    assert repo.publish(first) == first
    with pytest.raises(ValueError):
        repo.immutable(first.prediction_id, dict(first.model_dump(mode="json"), prediction="DOWN"))
    assert repo.base.list() == []


def test_e06_exact_reference_and_e12_observation_states(repo, monkeypatch):
    p = repo.publish(prediction())
    snap = SimpleNamespace(daily_prices=[SimpleNamespace(close=Decimal("100"))])
    calls = []
    monkeypatch.setattr(
        outcomes,
        "action_proof",
        lambda *args: (
            calls.append("original-action")
            or {"raw_ref": repo.raw(b"synthetic-action"), "content_hash": digest("unused")}
        ),
    )
    monkeypatch.setattr(outcomes, "fetch_quotes", lambda *args: raw_quotes(repo))
    before = datetime.fromisoformat("2026-10-13T14:59:00+08:00")
    after = datetime.fromisoformat("2026-10-13T16:00:00+08:00")
    assert outcomes.observe_one(repo, p, snap, None, before)["status"] == "PENDING"
    scored = outcomes.observe_one(repo, p, snap, None, after)
    assert scored["return_direction"] == "UP" and scored["correct"] and calls == ["original-action"]
    monkeypatch.setattr(
        outcomes, "fetch_quotes", lambda *args: (_ for _ in ()).throw(ValueError("没有收盘数据"))
    )
    assert outcomes.observe_one(repo, p, snap, None, after)["status"] == "PENDING_DATA"
    monkeypatch.setattr(
        outcomes,
        "fetch_quotes",
        lambda *args: raw_quotes(repo)[:1] + raw_quotes(repo, close="99.49")[1:],
    )
    assert outcomes.observe_one(repo, p, snap, None, after)["status"] == "CONFLICT"


def test_e04_flat_and_versioned_results_do_not_rewrite_or_regress(repo, monkeypatch):
    p = repo.publish(prediction())
    after = datetime.fromisoformat("2026-10-13T16:00:00+08:00")
    snap = SimpleNamespace(daily_prices=[SimpleNamespace(close=Decimal("99"))])
    monkeypatch.setattr(outcomes, "fetch_quotes", lambda *args: raw_quotes(repo, close="99.00"))
    pending = outcomes.append_result(repo, p, dict(status="PENDING", limitations=["未收盘"]), NOW)
    result = outcomes.observe_one(repo, p, snap, None, after)
    flat = outcomes.append_result(repo, p, result, after)
    assert (
        flat["version"] == 2
        and flat["supersedes_id"] == pending["outcome_id"]
        and flat["correct"] is False
    )
    assert outcomes.append_result(repo, p, result, after) == flat
    assert (
        outcomes.append_result(
            repo, p, dict(status="PENDING_DATA", limitations=["稍后无法取数"]), after
        )
        == flat
    )
    assert len(repo.outcome_versions(p.prediction_id)) == 2


def test_e08_missing_input_keeps_both_failed_attempts(repo, monkeypatch):
    config = activate(repo)
    state, _ = repo.create_run(
        dict(request_key=str(uuid4()), experiment_id=config["experiment_id"], exploratory=True),
        config,
        "test",
    )
    monkeypatch.setattr(
        service, "load_calendar", lambda *a: (_ for _ in ()).throw(ValueError("合成的来源失败"))
    )
    asyncio.run(service.run(repo, state["run_id"], SimpleNamespace(), Event()))
    final = repo.get_run(state["run_id"])
    assert (
        final["status"] == "FAILED"
        and final["prediction_ids"] == []
        and len(final["attempt_ids"]) == 2
    )
    assert all(repo.record(a)["state"] == "FAILED_INPUT" for a in final["attempt_ids"])


def test_no_issued_prediction_is_failed_instead_of_empty_success(repo, monkeypatch):
    config = activate(repo)
    state, _ = repo.create_run(
        dict(request_key=str(uuid4()), experiment_id=config["experiment_id"], exploratory=True),
        config,
        "synthetic",
    )
    monkeypatch.setattr(
        service, "load_calendar", lambda _client, exchange, _year: calendar(exchange)
    )
    monkeypatch.setattr(
        service, "build_snapshot", lambda *_: (_ for _ in ()).throw(ValueError("合成日线来源失败"))
    )
    asyncio.run(
        service.run(
            repo, state["run_id"], SimpleNamespace(model="synthetic-model"), Event(), port=object()
        )
    )
    final = repo.get_run(state["run_id"])
    assert final["status"] == "FAILED" and final["prediction_ids"] == []
    assert len(final["attempt_ids"]) == 2


def test_e11_versions_and_cohorts_are_not_pooled(repo):
    activate(repo)
    for model, cohort in [("synthetic-model", "PRIMARY"), ("other-model", "EXPLORATORY")]:
        repo.publish(prediction(model_version=model, cohort=cohort))
    board = service.scoreboard(repo, NOW)
    assert len(board["groups"]) == 2 and board["coverage"]["numerator"] == 1
    assert all(g["statistics"]["accuracy"]["value"] is None for g in board["groups"])
    assert service.scoreboard(repo, NOW)["snapshot_id"] == board["snapshot_id"]


def test_public_api_rejects_outcomes_and_deduplicates_before_capacity(repo):
    app = FastAPI()
    app.include_router(parent_api.router)
    app.state.research_store = repo.base
    app.state.research_jobs = {"synthetic-busy": SimpleNamespace(done=lambda: False)}
    app.state.settings = SimpleNamespace(model="synthetic-model")
    config = activate(repo)
    body = dict(request_key=str(uuid4()), experiment_id=config["experiment_id"], exploratory=True)
    state, _ = repo.create_run(body, config, "test")
    with TestClient(app) as c:
        root = "/api/quant/decision/experiments/next-session"
        assert c.post(root + "/runs", json=body).json()["run_id"] == state["run_id"]
        assert c.post(root + "/runs", json=dict(body, actual="UP")).status_code == 422
        assert (
            c.post(
                root + "/runs/" + state["run_id"] + "/observe", json={"correct": True}
            ).status_code
            == 422
        )
        assert c.get(root + "/runs/" + state["run_id"]).json().get("development") is None
        assert c.get(root + "/runs/" + state["run_id"] + "?developer=true").json()["development"]
        assert c.post(root + "/runs", json=dict(body, request_key=str(uuid4()))).status_code == 429


def test_shared_model_budget_and_failed_attempts_are_charged(repo):
    config = service.template().model_dump(mode="json")
    config["budget"]["max_model_calls"] = 1
    state, _ = repo.create_run(dict(request_key=str(uuid4())), config, "test")

    class Port:
        async def generate(self, request):
            raise ValueError("synthetic failure")

    async def execute():
        async with DailyRoles(
            repo,
            state,
            SimpleNamespace(model="test", model_read_timeout_seconds=10),
            lambda: repo.save(state),
            lambda: None,
            Port(),
        ) as roles:
            with pytest.raises(ValueError):
                await roles.call("Analyst", Proposal, {"evidence": []})

    asyncio.run(execute())
    assert (
        state["model_calls"] == 1
        and state["charged_tokens"] > 0
        and state["calls"][0]["status"] == "FAILED"
    )


def test_model_cannot_publish_unbound_references_or_numbers():
    reason = dict(
        text="测试文字", evidence_ids=["absent"], next_session_mechanism="若延续则可能影响"
    )
    proposal = Proposal(
        direction="UP",
        summary="测试",
        reasons=[reason],
        opposing_reasons=[reason],
        limitations=["测试"],
        support_level="WEAK",
    )
    with pytest.raises(ValueError):
        guard_proposal(proposal, {"evidence": []})
    with pytest.raises(ValueError):
        guard_proposal(
            proposal.model_copy(update={"summary": "2026"}),
            {"evidence": [{"evidence_id": "absent"}]},
        )


def test_e05_full_day_suspension_is_unscorable_and_does_not_roll(repo, monkeypatch):
    p = repo.publish(prediction())
    snap = SimpleNamespace(daily_prices=[SimpleNamespace(close=Decimal("99"))])
    packet = dict(
        result=dict(
            data=[
                dict(
                    SECUCODE="688012.SH",
                    SUSPEND_EXPIRE="连续停牌",
                    SUSPEND_START_TIME="2026-10-12 09:30:00",
                    SUSPEND_END_TIME=None,
                    PREDICT_RESUME_DATE=None,
                )
            ]
        )
    )
    ref = repo.blob(packet)

    class Client:
        def fetch(self, *a, **kw):
            return dict(blob_ref=ref, sha256=ref.split("/")[-1]), packet

    monkeypatch.setattr(
        outcomes, "fetch_quotes", lambda *a: (_ for _ in ()).throw(ValueError("停牌时行情缺失"))
    )
    result = outcomes.observe_one(
        repo, p, snap, Client(), datetime.fromisoformat("2026-10-13T16:00:00+08:00")
    )
    assert result["status"] == "UNSCORABLE" and "close" not in result
    outcome = outcomes.append_result(
        repo, p, result, datetime.fromisoformat("2026-10-13T16:00:00+08:00")
    )
    assert repo.outcome_versions(p.prediction_id) == [outcome]
    assert repo.prediction(p.prediction_id).target_session == date(2026, 10, 13)
    packet["result"]["data"][0]["SUSPEND_END_TIME"] = "2026-10-13 10:00:00"
    assert not outcomes.full_suspension(packet, p.security_id, p.target_session)


def test_restart_preserves_attempts_and_does_not_resume_or_overwrite(repo):
    config = activate(repo)
    state, _ = repo.create_run(
        dict(request_key=str(uuid4()), experiment_id=config["experiment_id"]), config, "test"
    )
    state.update(base_session="2026-10-12", target_session="2026-10-13", cohort="PRIMARY")
    state["process"] = [dict(role="Planner", detail="testing")]
    repo.save(state)
    service.recover(repo)
    recovered = repo.get_run(state["run_id"])
    assert recovered["status"] == "INTERRUPTED" and len(recovered["attempt_ids"]) == 2
    with pytest.raises(ValueError):
        repo.save(dict(state, status="COMPLETED"))
    service.recover(repo)
    assert repo.get_run(state["run_id"]) == recovered


def test_http_cache_is_confined_to_one_request_and_new_reads_recheck_blobs(repo, tmp_path):
    from marketpulse.infrastructure.storage.models import BlobError
    from marketpulse.quant.decision.next_session.api import repository

    ref = repo.raw(b"synthetic-original")
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(research_store=repo.base)),
    )
    first = repository(request)
    assert first is repository(request) and first.raw_bytes(ref) == b"synthetic-original"
    from marketpulse.infrastructure.storage.models import BlobRef

    blob = BlobRef.from_uri(ref)
    path = next((tmp_path / "blobs").rglob(blob.sha256))
    path.write_bytes(b"synthetic-corrupt")
    next_request = SimpleNamespace(state=SimpleNamespace(), app=request.app)
    assert repository(next_request) is not first
    with pytest.raises((ValueError, OSError, BlobError)):
        repository(next_request).raw_bytes(ref)


def test_e02_intraday_rejection_and_midnight_rollover_never_leave_running(repo, monkeypatch):
    config = activate(repo)
    activation = repo.activation(config["experiment_id"])
    midday = datetime.fromisoformat("2026-10-13T09:30:00+08:00")
    with pytest.raises(ValueError, match="收盘后"):
        service.preflight(activation, midday)
    assert service.preflight(activation, datetime.fromisoformat("2026-10-13T16:00:00+08:00")) == (
        date(2026, 10, 13),
        date(2026, 10, 14),
    )
    state, _ = repo.create_run(
        dict(request_key=str(uuid4()), experiment_id=config["experiment_id"], exploratory=True),
        config,
        "test",
    )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return midday.astimezone(tz)

    monkeypatch.setattr(service, "datetime", Clock)
    monkeypatch.setattr(service, "load_calendar", lambda client, exchange, year: calendar(exchange))
    asyncio.run(service.run(repo, state["run_id"], SimpleNamespace(), Event()))
    final = repo.get_run(state["run_id"])
    assert final["status"] == "FAILED" and len(final["attempt_ids"]) == 2
    assert not final["prediction_ids"]


@pytest.mark.parametrize("entity,security", [("贵州茅台", "600519.SH"), ("300750", "300750.SZ")])
def test_requested_stock_changes_run_universe_without_mutating_activation(
    repo, monkeypatch, entity, security
):
    config = activate(repo)
    original = repo.activation(config["experiment_id"])
    state, _ = repo.create_run(
        dict(
            request_key=str(uuid4()),
            experiment_id=config["experiment_id"],
            entity=entity,
            exploratory=False,
        ),
        config,
        "test",
    )
    seen = []

    def resolve(client, query):
        seen.append(query)
        return dict(security_id=security, name=entity)

    monkeypatch.setattr(service, "resolve", resolve)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(service, "datetime", Clock)
    monkeypatch.setattr(
        service, "load_calendar", lambda *a: (_ for _ in ()).throw(ValueError("合成来源失败"))
    )
    asyncio.run(service.run(repo, state["run_id"], SimpleNamespace(), Event()))
    final = repo.get_run(state["run_id"])
    assert seen == [entity] and final["config"]["securities"] == [security]
    assert final["config"]["universe_version"] == "user-selected-hs-a-v1:" + security
    assert repo.activation(config["experiment_id"]) == original
    assert len(final["attempt_ids"]) == 1
    assert repo.record(final["attempt_ids"][0])["slot_key"][1] == security
    assert repo.record(final["attempt_ids"][0])["cohort"] == "EXPLORATORY"
    assert not final["prediction_ids"] and final["status"] == "FAILED"


def test_unresolved_query_never_falls_back_to_fixed_stocks(repo, monkeypatch):
    config = activate(repo)
    state, _ = repo.create_run(
        dict(
            request_key=str(uuid4()),
            experiment_id=config["experiment_id"],
            entity="不存在的股票",
            exploratory=True,
        ),
        config,
        "test",
    )
    monkeypatch.setattr(
        service, "resolve", lambda *a: (_ for _ in ()).throw(ValueError("未找到沪深 A 股"))
    )
    asyncio.run(service.run(repo, state["run_id"], SimpleNamespace(), Event()))
    final = repo.get_run(state["run_id"])
    assert final["status"] == "FAILED" and not final["attempt_ids"]
    assert final["prediction_ids"] == [] and not final.get("query_subject")


def test_selected_stock_statistics_remain_separate_from_fixed_universe(repo):
    activate(repo)
    for universe in [service.template().universe_version, "user-selected-hs-a-v1:688012.SH"]:
        repo.publish(prediction(cohort="EXPLORATORY", universe_version=universe))
    board = service.scoreboard(repo, NOW)
    assert len(board["groups"]) == 2
    assert len({g["universe_version"] for g in board["groups"]}) == 2
    assert board["coverage"]["numerator"] == 0
