"""Synthetic execution boundaries with real request registration and SQLite/CAS."""

import asyncio
import copy
from threading import Event
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import select

from marketpulse.quant.knowledge import paired_evaluation as e
from marketpulse.quant.knowledge.models import InvestmentStudyRow

from .test_research import runtime  # noqa: F401


@pytest.fixture
def execution(request, rules, monkeypatch):
    ir, packet, settings = request.getfixturevalue("runtime")
    repo = e.EvaluationStore(ir.base)
    events = dict(capture=0, context=0, studies=[], observed=[], ablation=0)
    faults = dict(interrupt_child=None)

    def registered_children():
        return ir.base.list(prefix="RUN-IR-")

    def capture(*_):
        events["capture"] += 1
        # Registration is a real transaction before the first public-data boundary.
        assert len(registered_children()) == 4
        with ir.base.sessions() as session:
            assert len(session.scalars(select(InvestmentStudyRow)).all()) == 4
        return copy.deepcopy(packet)

    def context(*_):
        events["context"] += 1
        return dict(kind="SYNTHETIC-CONTEXT", stocks=[])

    async def study(study_repo, _rules, rid, _stop, _settings, **_):
        state = study_repo.get(rid, with_data=False)
        events["studies"].append(rid)
        assert state["config"]["budget"] == e.BUDGET
        state["model_calls"] += 1
        state["charged_tokens"] += 137
        state["tokens"] += 137
        state["elapsed_seconds"] = state.get("elapsed_seconds", 0) + 1
        state["calls"].append(
            dict(
                kind="model",
                status="COMPLETED",
                model="SYNTHETIC",
                input_tokens=100,
                output_tokens=37,
                charged_tokens=137,
            )
        )
        if faults["interrupt_child"] == rid:
            faults["interrupt_child"] = None
            state["status"] = "INTERRUPTED"
            study_repo.save(state)
            raise ValueError("synthetic interruption after charged call")
        # No fabricated production report: the real research boundary is replaced.
        state["status"] = "COMPLETED"
        study_repo.save(state)
        return state

    def observe(_store, state):
        events["observed"].append(state["run_id"])

    def freeze(_repo, source, model):
        assert source["status"] == "COMPLETED" and model == settings.model
        return dict(version=e.ablation.VERSION, model=model)

    async def ablate(_repo, _rules, trial, _settings, persist, **_):
        events["ablation"] += 1
        trial.update(status="COMPLETED", model_calls=1, charged_tokens=23, tokens=23)
        persist(trial, "SYNTHETIC-AUDIT")
        return trial

    def report(_repo, state):
        manifest = _repo.value(state["manifest_ref"])
        return dict(
            rows=[
                dict(
                    run_id=x["run_id"],
                    status=ir.get(x["run_id"], with_data=False)["status"]
                    if ir.exists(x["run_id"])
                    else "NOT_STARTED",
                )
                for x in manifest["children"]
            ],
            ablation_status=state.get("ablation", {}).get("status"),
        )

    monkeypatch.setattr(e, "capture_inputs", capture)
    monkeypatch.setattr(e, "collect_context", context)
    monkeypatch.setattr(e, "rebuild", lambda *_: dict(kind="SYNTHETIC-REBUILT"))
    monkeypatch.setattr(e, "run_with_recovery", study)
    monkeypatch.setattr(e.ObservationStore, "observe", observe)
    monkeypatch.setattr(e.ablation, "freeze", freeze)
    monkeypatch.setattr(e.ablation, "execute", ablate)
    monkeypatch.setattr(e, "report", report)
    return repo, ir, rules, settings, events, faults


def create(repo, settings):
    request = e.EvaluationRequest(request_key=uuid4()).model_dump(mode="json")
    return repo.create(request, settings.model)[0]


def test_manifest_preregisters_four_deterministic_public_requests(execution):
    repo, ir, _, settings, events, _ = execution
    state = create(repo, settings)
    manifest = repo.value(state["manifest_ref"])
    assert manifest["model"] == settings.model
    assert len(manifest["children"]) == 4
    assert [(c["case_id"], c["arm"]) for c in manifest["children"]] == [
        (case["case_id"], arm) for case in e.CASES for arm in case["order"]
    ]
    for child in manifest["children"]:
        key = uuid5(NAMESPACE_URL, state["run_id"] + ":" + child["case_id"] + ":" + child["arm"])
        assert child["run_id"] == "RUN-IR-" + key.hex
        assert child["request"]["request_key"] == str(key)
        assert child["request"]["entities"] == e.ENTITIES
        assert child["request"]["profile"] is None
        assert child["request"].get("enhanced_research", False) == (child["arm"] == "ENHANCED")
    assert ir.base.list(prefix="RUN-IR-") == []
    assert events["capture"] == events["context"] == 0
    repeated, fresh = repo.create(state["request"], settings.model)
    assert not fresh and repeated["manifest_ref"] == state["manifest_ref"]


def test_charged_interruption_resumes_same_children_and_shared_inputs_once(execution):
    repo, ir, rules, settings, events, faults = execution
    state = create(repo, settings)
    children = repo.value(state["manifest_ref"])["children"]
    ids = [c["run_id"] for c in children]
    faults["interrupt_child"] = ids[1]
    first = asyncio.run(e.execute(repo, rules, state["run_id"], Event(), settings))
    assert first["status"] == "INTERRUPTED"
    assert events["capture"] == events["context"] == 1
    interrupted = ir.get(ids[1], with_data=False)
    assert interrupted["model_calls"] == 1 and interrupted["charged_tokens"] == 137
    shared = {
        key: first[key] for key in ("inputs_ref", "context_snapshot_ref", "context_report_ref")
    }
    complete = asyncio.run(e.execute(repo, rules, state["run_id"], Event(), settings))
    assert complete["status"] == "COMPLETED", complete.get("error")
    assert {key: complete[key] for key in shared} == shared
    assert events["capture"] == events["context"] == events["ablation"] == 1
    assert events["studies"] == [ids[0], ids[1], ids[1], ids[2], ids[3]]
    assert events["observed"] == ids
    assert [c["run_id"] for c in repo.value(complete["manifest_ref"])["children"]] == ids
    resumed = ir.get(ids[1], with_data=False)
    assert resumed["model_calls"] == 2 and resumed["charged_tokens"] == 274
    assert resumed["elapsed_seconds"] == 2 and len(resumed["calls"]) == 2
    assert resumed["config"]["budget"] == interrupted["config"]["budget"] == e.BUDGET
    for rid in ids:
        child = ir.get(rid, with_data=False)
        assert {key: child[key] for key in shared} == shared
    before = copy.deepcopy(events)
    again = asyncio.run(e.execute(repo, rules, state["run_id"], Event(), settings))
    assert again == complete and events == before


def test_registered_model_cannot_change_before_any_public_boundary(execution):
    repo, ir, rules, settings, events, _ = execution
    state = create(repo, settings)
    changed = copy.copy(settings)
    changed.model = "DIFFERENT-SYNTHETIC-MODEL"
    result = asyncio.run(e.execute(repo, rules, state["run_id"], Event(), changed))
    assert result["status"] == "INTERRUPTED"
    assert result.get("error")
    assert repo.get(state["run_id"], with_data=False)["status"] == "INTERRUPTED"
    assert events == dict(capture=0, context=0, studies=[], observed=[], ablation=0)
    assert ir.base.list(prefix="RUN-IR-") == []


def test_sealing_stopped_measurement_does_not_promote_or_retry_failed_children(execution):
    repo, ir, rules, settings, events, faults = execution
    state = create(repo, settings)
    children = repo.value(state["manifest_ref"])["children"]
    faults["interrupt_child"] = children[1]["run_id"]
    first = asyncio.run(e.execute(repo, rules, state["run_id"], Event(), settings))
    assert first["status"] == "INTERRUPTED"
    before_events = copy.deepcopy(events)
    before_child = ir.get(children[1]["run_id"], with_data=False)
    sealed = e.seal(repo, state["run_id"])
    assert sealed["status"] == "COMPLETED"
    assert sealed["completion_reason"] == "BOUNDED_ROUND_FINISHED_INCLUDING_FAILURES"
    assert ir.get(children[1]["run_id"], with_data=False) == before_child
    assert events == before_events
    assert repo.value(sealed["report_ref"])["rows"][1]["status"] == "INTERRUPTED"
    assert e.seal(repo, state["run_id"])["report_sha256"] == sealed["report_sha256"]
