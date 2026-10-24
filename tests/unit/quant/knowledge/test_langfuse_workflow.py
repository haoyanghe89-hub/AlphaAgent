import asyncio
import json
from threading import Event
from uuid import uuid4

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.research_service import run
from marketpulse.telemetry.config import TraceConfig
from marketpulse.telemetry.tracing import Runtime, set_runtime

from .test_research import SyntheticPort
from .test_research import runtime as existing_research_fixture


@pytest.fixture
def research_runtime(rules, monkeypatch):
    return existing_research_fixture.__wrapped__(rules, monkeypatch)


@pytest.fixture
def traced_runtime(tmp_path):
    runtime = Runtime(
        TraceConfig(enabled=True, journal_path=tmp_path / "trace.sqlite3"), background=False
    )
    previous = set_runtime(runtime)
    yield runtime
    set_runtime(previous)
    runtime.close()


def recorded(runtime):
    runtime.flush()
    with runtime.outbox.connection() as db:
        bodies = [
            row[0] for row in db.execute("SELECT body FROM batches WHERE path LIKE '%/traces'")
        ]
    observations = []
    for body in bodies:
        for resource in ExportTraceServiceRequest.FromString(body).resource_spans:
            for scope in resource.scope_spans:
                observations.extend(scope.spans)
    return observations, bodies


def test_real_research_code_has_nested_role_generations_without_private_profile(
    traced_runtime, research_runtime
):
    repo, packet, settings = research_runtime
    port = SyntheticPort()
    request = InvestmentRequest(
        request_key=uuid4(),
        entities=["000333"],
        profile={"capital": "987654.32", "available_cash": "987654.32"},
    )
    state, _ = repo.create(request.model_dump(mode="json"))
    result = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    observations, bodies = recorded(traced_runtime)
    assert result["status"] == "COMPLETED"
    generations = [s for s in observations if s.name == "model.call"]
    assert len(generations) == len(port.roles) == 4
    assert len({s.trace_id for s in observations}) == 1
    by_id = {s.span_id: s for s in observations}
    for item in generations:
        assert by_id[item.parent_span_id].name.startswith("agent.")
    assert not any(b"987654.32" in body for _, body in [(None, body) for body in bodies])
    assert traced_runtime.outbox.runs(state["run_id"])[-1]["state"] == "COMPLETED"
    exported = b"".join(bodies).decode("utf-8", errors="ignore")
    assert "合成测试：价格条件仍需等待确认" in exported
    assert "校验通过" in exported
    root = next(s for s in observations if s.name == "alphaagent.investment_research")
    root_output = next(
        a.value.string_value for a in root.attributes if a.key == "langfuse.observation.output"
    )
    assert "投资研究交付" in root_output and "最终引用方法" in root_output
    for generation in generations:
        attributes = {a.key: a.value.string_value for a in generation.attributes}
        messages = json.loads(attributes["langfuse.observation.input"])
        assert [m["role"] for m in messages] == ["system", "user"]
        assert json.loads(attributes["langfuse.observation.output"])[0]["role"] == "assistant"


def test_actual_knowledge_and_archived_external_text_are_exported(
    traced_runtime, research_runtime, rules, candidate, monkeypatch
):
    from marketpulse.quant.knowledge import context, research_context, routing
    from marketpulse.telemetry.tracing import observation

    from .test_context import snapshot

    repo, packet, _ = research_runtime
    draft = rules.draft(candidate)
    monkeypatch.setattr(rules, "current", lambda cutoff: [draft])
    monkeypatch.setattr(routing, "RULE_IDS", [candidate.rule_id])
    sealed = snapshot(repo, packet)
    with observation("public-test"):
        research_context.knowledge_snapshot(rules, sealed["captured_at"])
        context.rebuild(repo, packet, sealed)
    observations, bodies = recorded(traced_runtime)
    exported = b"".join(bodies).decode("utf-8", errors="ignore")
    assert "本次加载的知识规则" in exported
    assert candidate.system_rule in exported
    assert "Capital return and cash collection" in exported
    assert "生产空调、冰箱" in exported
    assert "政策" in exported and "blob://sha256/" in exported
    assert not any(
        "OBSERVABILITY_GAP" in a.value.string_value for s in observations for a in s.attributes
    )
