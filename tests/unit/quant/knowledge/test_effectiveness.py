# ruff: noqa: F811
import asyncio
import copy
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from marketpulse.quant.contracts import digest
from marketpulse.quant.knowledge import active_research as active
from marketpulse.quant.knowledge import effectiveness as e
from marketpulse.quant.knowledge import forward_pairs as f
from marketpulse.quant.knowledge import quality_review as q
from marketpulse.quant.knowledge.paired_evaluation import EvaluationStore, paired_outcomes

from .test_research import runtime  # noqa: F401


def test_component_ladder_requests_are_registered_without_changing_shared_contract():
    rows = e.children("RUN-EV-" + uuid4().hex)
    assert len(rows) == 12
    assert len({r["run_id"] for r in rows}) == 12
    for case in e.CASES:
        own = {r["arm"]: r["request"] for r in rows if r["case_id"] == case["case_id"]}
        assert set(own) == set(e.ARMS)
        assert "enhanced_research" not in own["ORIGINAL"]
        for arm in e.ARMS:
            assert own[arm]["execution_contract"] == "investment-structured-v2"
            assert own[arm]["input_reservation"] == "deepseek-v4-tokenizer-v1"
            assert own[arm]["entities"] == case["entities"]
        assert own["METHODS"]["enhancement_stage"] == "METHODS"
        assert own["TOOLS"]["enhancement_stage"] == "TOOLS"
        assert "enhancement_stage" not in own["ENHANCED"]


def test_method_only_rejects_tool_calls_even_if_model_asks():
    plan = active.ResearchPlan(
        selections=[
            dict(
                security_id="000333.SZ",
                method_rule_ids=["K"],
                hypothesis="合成测试假设",
                countercheck="合成反证核查",
            )
        ],
        tools=[dict(tool="EVIDENCE_SEARCH", security_id="000333.SZ", query="合成")],
    )
    context = dict(stocks=[dict(security_id="000333.SZ")], horizon="SWING")
    knowledge = dict(rules=[dict(rule_id="K", rule=dict(horizons=["SWING"]))])
    active.guard(plan, context, knowledge, stage="TOOLS")
    with pytest.raises(ValueError, match="不得请求"):
        active.guard(plan, context, knowledge, stage="METHODS")


def test_quality_requires_each_stock_answer_and_each_dimension_and_valid_citations():
    context = dict(stocks=[dict(security_id="X", evidence=[dict(evidence_id="E")])])
    ratings = [
        dict(
            security_id="X",
            answer=a,
            dimensions=[
                dict(key=k, score=1, reason="合成具体评分理由", evidence_ids=["E"])
                for k in q.DIMENSIONS
            ],
        )
        for a in ("A", "B")
    ]
    answer = q.Assessment(ratings=ratings)
    q.guard(answer, context)
    summary = q.summarize([(answer, dict(A="ENHANCED", B="ORIGINAL"))])
    assert all(r["mean"] == 5 for r in summary["groups"])
    assert summary["effect"] == "UNVALIDATED"
    bad = copy.deepcopy(ratings)
    bad[0]["dimensions"][0]["evidence_ids"] = ["PRIVATE-NONEXISTENT"]
    with pytest.raises(ValueError):
        q.guard(q.Assessment(ratings=bad), context)

    bad = copy.deepcopy(ratings)
    bad[0]["answer"] = "B"
    with pytest.raises(ValueError):
        q.guard(q.Assessment(ratings=bad), context)
    bad = copy.deepcopy(ratings)
    bad[0]["dimensions"][0]["key"] = "time_scope"
    with pytest.raises(ValueError):
        q.guard(q.Assessment(ratings=bad), context)


def test_complete_quality_packet_contains_public_execution_conditions_and_excludes_accounts():
    opinion = SimpleNamespace(
        security_id="X",
        model_dump=lambda **_: dict(security_id="X", summary="合成解释", direction="FAVORABLE"),
    )
    analysis = SimpleNamespace(opinions=[opinion])
    route = dict(
        title="合成路线",
        status="WAIT_TRIGGER",
        action="等待条件",
        conditions=[dict(passed=False)],
        entry_reference="100",
        risk_reference="90",
        invalidation="合成失效条件",
        private_never="SECRET",
    )
    candidate = dict(security_id="X", selected_route="R", routes=[dict(route, route_id="R")])
    study = dict(
        approved_analysis_step="Analyst",
        public_context_ref="public",
        run_id="PRIVATE-RUN-ID",
        data=dict(candidates=[candidate], portfolio=dict(capital="PRIVATE-CAPITAL")),
    )
    repo = SimpleNamespace(
        output=lambda *_: analysis,
        value=lambda _: dict(
            horizon_sessions=20, evidence=[dict(security_id="X", evidence_id="E")]
        ),
    )
    packet = q.packet(repo, study, study, False, version=q.COMPLETE_VERSION)
    assert packet["stocks"][0]["answers"]["A"]["execution"]["entry_reference"] == "100"
    assert packet["stocks"][0]["answers"]["B"]["execution"]["risk_reference"] == "90"
    encoded = str(packet)
    assert not any(value in encoded for value in ("SECRET", "PRIVATE-RUN-ID", "PRIVATE-CAPITAL"))
    assert "execution" not in q.packet(repo, study, study, False)["stocks"][0]["answers"]["A"]


def test_new_protocol_restores_seals_and_verifies_manifest_without_affecting_old_protocol(
    runtime, monkeypatch
):
    ir, _, settings = runtime
    monkeypatch.setattr(
        f, "schedule", lambda *_: dict(version=f.VERSION, slots=[], note="SYNTHETIC")
    )
    repo = EvaluationStore(ir.base)
    state, _ = repo.create(
        dict(request_key=str(uuid4()), protocol_version=e.VERSION), settings.model
    )
    result = repo.get(state["run_id"])
    assert result["data"]["prediction"]["registered_pairs"] == 6
    assert result["data"]["prediction"]["accuracy_delta"] is None
    assert result["data"]["round_summary"]["declared_studies"] == 12
    manifest = repo.value(state["manifest_ref"])
    assert manifest["quality_budget"]["max_tokens"] == 40000
    e.verify_manifest(manifest, state["run_id"])
    manifest["children"][0]["request"]["entities"] = ["OTHER"]
    with pytest.raises(ValueError):
        e.verify_manifest(manifest, state["run_id"])


def test_supplemental_quality_review_is_append_only_idempotent_and_keeps_failures(
    runtime, monkeypatch
):
    ir, _, settings = runtime
    monkeypatch.setattr(
        f, "schedule", lambda *_: dict(version=f.VERSION, slots=[], note="SYNTHETIC")
    )
    repo = EvaluationStore(ir.base)
    original, _ = repo.create(
        dict(request_key=str(uuid4()), protocol_version=e.VERSION), settings.model
    )
    original.update(status="COMPLETED", report_ref=repo.blob({}), report_sha256=digest({}))
    repo.save(original)
    calls = []

    async def limited(_repo, worker, _manifest, _settings, save, _stop, _port):
        calls.append(worker["quality"]["config"]["budget"])
        worker["quality"].update(
            status="INTERRUPTED",
            error="SYNTHETIC bounded failure",
            model_calls=1,
            charged_tokens=40000,
        )
        save()

    monkeypatch.setattr(e, "evaluate_quality", limited)
    first = asyncio.run(e.recheck_quality(repo, original["run_id"], settings))
    second = asyncio.run(e.recheck_quality(repo, original["run_id"], settings))
    assert first == second and len(calls) == 1
    assert calls[0] == dict(max_model_calls=8, max_tokens=40000, max_seconds=300)
    worker = repo.value(first["worker_ref"])
    assert (
        worker["quality"]["charged_tokens"] == 40000
        and worker["quality"]["status"] == "INTERRUPTED"
    )
    assert repo.get(original["run_id"], with_data=False) == original


def test_new_cases_only_compare_standard_and_full_enhanced_not_intermediate_arms():
    rows = []
    for case in e.CASES:
        for arm in e.ARMS:
            rows.append(
                dict(
                    case_id=case["case_id"],
                    arm=arm,
                    matched_inputs=True,
                    registration_date="20261019",
                    prediction=dict(
                        stocks=[
                            dict(
                                security_id=s,
                                actual_direction="UP",
                                target_date="20261116",
                                status="MATURED",
                                correct=True,
                                baseline_correct={"ALWAYS_UP": True},
                            )
                            for s in case["entities"]
                        ]
                    ),
                )
            )
    result = paired_outcomes(rows, e.CASES)
    assert result["registered_pairs"] == result["paired_matured"] == 6
    assert result["accuracy_delta"] == 0


def test_forward_schedule_has_twenty_fixed_pairs_and_never_backfills_late_registration(monkeypatch):
    days = [date(2026, 10, 19) + timedelta(days=i) for i in range(25)]
    calendar = dict(
        coverage_start=str(days[0]),
        coverage_end=str(days[-1]),
        sessions=[str(d) for d in days],
        source_refs=["blob://sha256/" + "a" * 64],
        verified_at="2026-10-19T00:00:00Z",
        content_hash="a" * 64,
    )
    activation = dict(
        config=dict(model_version="SYNTHETIC", budget={}),
        calendars={ex: dict(calendar, exchange=ex) for ex in ("SSE", "SZSE")},
    )

    class Repo:
        def __init__(self, *_):
            pass

        def activation(self, *_):
            return activation

        def raw_bytes(self, *_):
            return b"SYNTHETIC"

        def blob(self, *_):
            return "activation"

    monkeypatch.setattr(f, "ExperimentStore", Repo)
    base = SimpleNamespace(list=lambda **_: [])
    early = f.schedule(base, "PARENT", datetime(2026, 10, 19, 3, tzinfo=UTC))
    late = f.schedule(base, "PARENT", datetime(2026, 10, 19, 12, tzinfo=UTC))
    assert len(early["slots"]) == 20
    assert early["slots"][0]["base_session"] == "2026-10-19"
    assert late["slots"][0]["base_session"] == "2026-10-20"
    assert len({(s["security_id"], s["target_session"]) for s in early["slots"]}) == 20
    assert not f.due(early, datetime(2026, 10, 19, 3, tzinfo=UTC))
    assert len(f.due(early, datetime(2026, 10, 19, 12, tzinfo=UTC))) == 2


def test_missed_forward_window_records_failure_without_models_or_new_runs(monkeypatch):
    records = {}

    class Repo:
        def __init__(self, *_):
            pass

        def exists(self, k):
            return k in records

        def record(self, k):
            return records[k]

        def immutable(self, k, v):
            records[k] = v
            return v

    monkeypatch.setattr(f, "ExperimentStore", Repo)
    plan = dict(parent_run_id="P", window_start="19:45")
    slot = dict(base_session="2020-01-01", target_session="2020-01-02", security_id="X")
    first = asyncio.run(f.run_slot(None, plan, slot, SimpleNamespace(model="SYNTHETIC")))
    assert first["status"] == "MISSED_WINDOW" and first["runs"] == {}
    assert asyncio.run(f.run_slot(None, plan, slot, None)) == first
    assert len(records) == 1


def test_forward_future_flat_conflict_and_matching_outcomes_are_separate(monkeypatch):
    slot = dict(base_session="2026-10-19", target_session="2026-10-20", security_id="000333.SZ")
    plan = dict(
        version=f.VERSION,
        parent_run_id="P",
        slots=[slot],
        model="SYNTHETIC",
        window_start="19:45",
        window_end="20:15",
        note="SYNTHETIC",
    )
    saved = dict(
        slot=slot,
        status="ISSUED",
        runs={"ORIGINAL": "A", "ENHANCED": "B"},
        shared_snapshot_ref="BASE",
    )
    predictions = {
        a: SimpleNamespace(
            prediction_id=a,
            security_id=slot["security_id"],
            prediction="UP",
            base_session="2026-10-19",
            target_session="2026-10-20",
            model_version="SYNTHETIC",
            issued_at=datetime(2026, 10, 19, 12, tzinfo=UTC),
            baselines=[("ALWAYS_UP", "UP"), ("ALWAYS_DOWN", "DOWN"), ("PERSISTENCE", "UP")],
        )
        for a in ("A", "B")
    }
    outcomes = {
        a: [dict(status="SCORABLE", return_direction="UP", close="101", reference_price="100")]
        for a in ("A", "B")
    }

    class Repo:
        def __init__(self, *_, **__):
            pass

        def exists(self, _):
            return True

        def record(self, _):
            return saved

        def get_run(self, rid):
            return dict(prediction_ids=[rid], snapshots={slot["security_id"]: "BASE"}, calls=[])

        def prediction(self, pid):
            return predictions[pid]

        def snapshot(self, _):
            return SimpleNamespace()

        def outcome_versions(self, pid):
            return outcomes[pid]

    monkeypatch.setattr(f, "ExperimentStore", Repo)
    before = datetime(2026, 10, 20, 3, tzinfo=UTC)
    after = datetime(2026, 10, 20, 8, tzinfo=UTC)
    assert f.view(None, plan, now=before)["paired_matured"] == 0
    result = f.view(None, plan, now=after)
    assert result["paired_matured"] == 1 and result["correct"] == dict(ORIGINAL=1, ENHANCED=1)
    outcomes["B"][0]["close"] = "102"
    assert f.view(None, plan, now=after)["rows"][0]["status"] == "OUTCOME_CONFLICT"
    for own in outcomes.values():
        own[0].update(return_direction="FLAT", close="100")
    result = f.view(None, plan, now=after)
    assert result["paired_matured"] == 0 and result["rows"][0]["status"] == "FLAT"
