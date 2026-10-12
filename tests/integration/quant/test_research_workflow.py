"""Contract, durable recovery, immutable evidence and model-failure integration."""

import asyncio
import copy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marketpulse.config import Settings
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobIntegrityError
from marketpulse.investigation.ports.external import ModelUsage, StructuredModelResult
from marketpulse.quant.live import service
from marketpulse.quant.live.agents import (
    Analysis,
    Plan,
    RoleRunner,
    numeric_bindings,
    validate_analysis,
)
from marketpulse.quant.live.calculate import simulate
from marketpulse.quant.live.research_api import compat_router, router
from marketpulse.quant.live.service import ResearchWorkflow, process
from marketpulse.quant.live.store import ResearchRunRow, ResearchStore


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "research.sqlite").as_posix(),
        connect_args={"check_same_thread": False},
    )
    ResearchRunRow.__table__.create(engine)
    yield ResearchStore(
        sessionmaker(engine),
        LocalContentAddressedBlobStorage(tmp_path / "blobs"),
        tmp_path / "legacy",
    )
    engine.dispose()


class Model:
    def __init__(self, fail_verifier=False):
        self.calls = []
        self.fail_verifier = fail_verifier

    async def generate(self, request):
        name = request.response_model.__name__
        self.calls.append(name)
        if name == "Verification" and self.fail_verifier:
            self.fail_verifier = False
            raise RuntimeError("provider unavailable")
        outputs = {
            "Plan": dict(scope="single", entity="000333", focus="comprehensive", reason="测试规划"),
            "DataPlan": dict(
                tools=["collect_market_data", "calculate_metrics", "retrieve_disclosures"],
                reason="受限工具",
            ),
            "Analysis": dict(
                claims=[
                    dict(text="本次事实为{{F0}}。", evidence_ids=["F0"]),
                    dict(text="估值需要结合证据{{F1}}。", evidence_ids=["F1"]),
                ]
            ),
            "Verification": dict(
                decisions=[
                    dict(claim_index=0, approved=True, reason="支持"),
                    dict(claim_index=1, approved=True, reason="支持"),
                ]
            ),
            "Writing": dict(claim_order=[0, 1], heading="综合研究结论"),
        }
        return StructuredModelResult(
            output=request.response_model.model_validate(outputs[name]),
            model="fake-for-tests",
            provider="offline-fixture",
            usage=ModelUsage(input_tokens=20, output_tokens=10),
        )


def new_state(repository):
    run = "RUN-LIVE-" + uuid4().hex
    state = dict(
        run_id=run,
        status="RUNNING",
        created_at=datetime.now(UTC).isoformat(),
        title="测试",
        scenario="live",
        kind="fresh",
        request=dict(question="分析美的集团", code="000333"),
        process=process(),
        calls=[],
        raw_refs={},
    )
    repository.put(state)
    return run


@pytest.fixture
def tools(monkeypatch):
    calls = []
    subject = dict(code="000333", symbol="sz000333", name="美的集团", company_type="4")
    monkeypatch.setattr(service, "resolve", lambda query, save: (subject, []))

    def collect(*args):
        calls.append("collect")
        return {}, []

    monkeypatch.setattr(service.sources, "collect", collect)
    monkeypatch.setattr(service, "announcements", lambda *args: ([], []))
    monkeypatch.setattr(
        service,
        "build",
        lambda *args: dict(
            subject=subject,
            metrics=[
                dict(name="归母净利", value=100, unit="亿元", source="lrb"),
                dict(name="ROE", value=10, unit="%", source="lrb"),
            ],
            dcf=dict(status="UNAVAILABLE", reason="缺字段"),
            valuation={},
            risks=[],
            checks=[],
            coverage={},
            calculation_inputs={},
        ),
    )
    return calls


@pytest.mark.asyncio
async def test_resume_reuses_successful_roles_and_acquisition(repository, tools):
    model = Model(fail_verifier=True)
    run = new_state(repository)
    workflow = ResearchWorkflow(repository, Settings(), model)
    await workflow.run(run)
    first = repository.get(run)
    assert first["status"] == "FAILED"
    assert "data" not in first
    await workflow.run(run)
    final = repository.get(run)
    assert final["status"] == "COMPLETED"
    assert tools == ["collect"]
    assert model.calls == [
        "Plan",
        "DataPlan",
        "Analysis",
        "Verification",
        "Verification",
        "Writing",
    ]
    assert final["data"]["agent_report"]["claims"][0]["text"] == "本次事实为归母净利：100 亿元。"
    original = copy.deepcopy(final)
    final["title"] = "overwrite"
    with pytest.raises(ValueError, match="immutable"):
        repository.put(final)
    assert repository.get(run) == original
    # A model output is content-addressed and cannot be edited to fabricate a trace.
    model_blob = next(c["response_ref"] for c in final["calls"] if c["kind"] == "model")
    from marketpulse.infrastructure.storage.models import BlobRef

    path = repository.blobs._path(BlobRef.from_uri(model_blob))
    path.write_bytes(b"forged")
    with pytest.raises(BlobIntegrityError):
        repository.get(run)


def test_unbound_numbers_and_unknown_evidence_are_rejected():
    evidence = [dict(id="F0", text="利润100")]
    for claim in [
        dict(text="利润增长99%", evidence_ids=["F0"]),
        dict(text="结果{{F9}}", evidence_ids=["F9"]),
        dict(text="结果{{F0}}", evidence_ids=["F9"]),
    ]:
        with pytest.raises(ValueError):
            validate_analysis(Analysis(claims=[claim, claim]), evidence)

    analysis = Analysis(claims=[dict(text="利润100", evidence_ids=["F0"])] * 2)
    validate_analysis(analysis, evidence)
    assert numeric_bindings(analysis.claims[0], evidence) == [
        dict(value="100", evidence_id="F0", start=2, end=5)
    ]


def test_no_model_config_never_creates_a_fake_agent_run(repository):
    app = FastAPI()
    app.state.research_store = repository
    app.state.settings = Settings()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(
            "/api/quant/research/runs", json=dict(request_key=str(uuid4()), question="分析美的")
        )
        assert response.status_code == 503
        assert repository.list() == []
        assert client.get("/api/quant/research/quotes?symbol=sh000001").status_code == 422


@pytest.mark.asyncio
async def test_restart_keeps_completed_trace_and_marks_active_job(repository, tools):
    run = new_state(repository)
    repository.interrupt_abandoned()
    assert repository.get(run)["status"] == "INTERRUPTED"
    await ResearchWorkflow(repository, Settings(), Model()).run(run)
    assert repository.get(run)["status"] == "COMPLETED"
    repository.interrupt_abandoned()
    assert repository.get(run)["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_changed_environment_cannot_mix_steps_on_resume(repository, tools, monkeypatch):
    run = new_state(repository)
    workflow = ResearchWorkflow(repository, Settings(), Model(fail_verifier=True))
    await workflow.run(run)
    monkeypatch.setattr(service, "implementation_hash", lambda: "changed")
    await workflow.run(run)
    assert repository.get(run)["status"] == "FAILED"
    assert "执行环境已变化" in repository.get(run)["error"]
    assert tools == ["collect"]


@pytest.mark.asyncio
async def test_ambiguous_security_requires_input_instead_of_fabricating_subject(
    repository, tools, monkeypatch
):
    def ambiguous(*args):
        raise ValueError("匹配多只股票，请写出全名或代码")

    monkeypatch.setattr(service, "resolve", ambiguous)
    run = new_state(repository)
    await ResearchWorkflow(repository, Settings(), Model()).run(run)
    assert repository.get(run)["status"] == "NEEDS_INPUT"
    assert tools == []


@pytest.mark.asyncio
async def test_cancellation_preserves_a_truthful_resumable_trace(repository, tools):
    entered = asyncio.Event()

    class WaitingModel(Model):
        async def generate(self, request):
            entered.set()
            await asyncio.Event().wait()

    run = new_state(repository)
    task = asyncio.create_task(ResearchWorkflow(repository, Settings(), WaitingModel()).run(run))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = repository.get(run)
    assert state["status"] == "INTERRUPTED"
    assert state["calls"][0]["status"] == "INTERRUPTED"
    assert state["process"][0]["status"] == "INTERRUPTED"
    assert tools == []
    await ResearchWorkflow(repository, Settings(), Model()).run(run)
    assert repository.get(run)["status"] == "COMPLETED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,status", [("clarify", "NEEDS_INPUT"), ("unsupported", "UNSUPPORTED")]
)
async def test_scope_gates_do_not_acquire_unrequested_data(repository, tools, scope, status):
    class ScopedModel(Model):
        async def generate(self, request):
            response = await super().generate(request)
            if request.response_model is Plan:
                return response.model_copy(
                    update={
                        "output": Plan(
                            scope=scope, entity="", focus="risk", reason="请明确研究范围"
                        )
                    }
                )
            return response

    model, run = ScopedModel(), new_state(repository)
    await ResearchWorkflow(repository, Settings(), model).run(run)
    assert repository.get(run)["status"] == status
    assert model.calls == ["Plan"]
    assert tools == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prior",
    [
        [dict(kind="model", role="Planner", status="FAILED")] * 10,
        [dict(kind="model", role="Planner", status="FAILED", input_tokens=26000)],
    ],
)
async def test_exhausted_model_budget_cannot_make_another_provider_request(repository, prior):
    state = repository.get(new_state(repository))
    state["calls"] = [
        {**call, "request_ref": repository.blob(dict(previous=True))} for call in prior
    ]
    model = Model()
    async with RoleRunner(Settings(), repository, state, lambda: None, model) as runner:
        with pytest.raises(RuntimeError, match="预算"):
            await runner.run("Planner", Plan, dict(question="test"))
    assert model.calls == []


@pytest.mark.asyncio
async def test_duplicate_tool_plan_is_rejected_before_market_acquisition(repository, tools):
    class BadToolsModel(Model):
        async def generate(self, request):
            response = await super().generate(request)
            if request.response_model.__name__ == "DataPlan":
                return response.model_copy(
                    update={
                        "output": request.response_model(
                            tools=["collect_market_data"] * 3, reason="bad"
                        )
                    }
                )
            return response

    run = new_state(repository)
    await ResearchWorkflow(repository, Settings(), BadToolsModel()).run(run)
    assert repository.get(run)["status"] == "FAILED"
    assert tools == []


def test_shared_alias_scenario_idempotency_and_parent_immutability(repository):
    from datetime import date, timedelta

    run = new_state(repository)
    state = repository.get(run)
    prices = [
        dict(date=(date(2025, 1, 1) + timedelta(days=i)).isoformat(), open=100 + i, close=100.5 + i)
        for i in range(160)
    ]
    data = dict(
        subject=dict(name="平安银行", code="000001"),
        sources=[],
        dcf=dict(status="NOT_APPLICABLE"),
        simulation=simulate(prices, prices),
        calculation_inputs=dict(prices=prices, benchmark=prices),
        agent_report=dict(claims=[dict(text="旧参数解释")]),
        evidence=[],
    )
    state.update(status="COMPLETED", result_ref=repository.blob(data))
    repository.put(state)
    original = repository.get(run)
    app = FastAPI()
    app.state.research_store, app.state.settings = repository, Settings()
    app.include_router(router)
    app.include_router(compat_router)
    with TestClient(app) as client:
        assert (
            client.get(f"/api/quant/live/runs/{run}").json()
            == client.get(f"/api/quant/research/runs/{run}").json()
        )
        payload = dict(request_key=str(uuid4()), fast=10, slow=30, cost_pct=0.3)
        child = client.post(f"/api/quant/research/runs/{run}/scenario", json=payload)
        assert child.status_code == 201
        repeat = client.post(f"/api/quant/live/runs/{run}/scenario", json=payload)
        assert repeat.json() == child.json()
        assert child.json()["parent_run_id"] == run
        assert child.json()["data"]["simulation"]["fast"] == 10
        assert "agent_report" not in child.json()["data"]
        assert "calculation_inputs" not in child.json()["data"]
        conflict = client.post(
            f"/api/quant/research/runs/{run}/scenario", json={**payload, "cost_pct": 0.5}
        )
        assert conflict.status_code == 409
        assert repository.get(run) == original


@pytest.mark.asyncio
async def test_numeric_guard_rejects_and_bounded_retry_uses_facts(repository):
    class RepairModel(Model):
        def __init__(self):
            super().__init__()
            self.requests = []

        async def generate(self, request):
            self.requests.append(request)
            response = await super().generate(request)
            if len(self.requests) == 1:
                return response.model_copy(
                    update={
                        "output": Analysis(
                            claims=[
                                dict(text="利润增长999%", evidence_ids=["F0"]),
                                dict(text="参考事实{{F1}}", evidence_ids=["F1"]),
                            ]
                        )
                    }
                )
            return response

    model = RepairModel()
    state = dict(calls=[])
    evidence = [dict(id="F0", text="利润增长5%"), dict(id="F1", text="参考事实")]
    settings = Settings().model_copy(update={"model_retry_backoff_seconds": 0})
    async with RoleRunner(settings, repository, state, lambda: None, model) as runner:
        result = await runner.run(
            "Analyst",
            Analysis,
            dict(bounded_evidence=evidence),
            lambda output: validate_analysis(output, evidence),
        )
    assert len(model.requests) == 2 and [c["status"] for c in state["calls"]] == [
        "FAILED",
        "COMPLETED",
    ]
    assert "占位符" in model.requests[1].messages[1].content
    assert "999" not in str(result)
