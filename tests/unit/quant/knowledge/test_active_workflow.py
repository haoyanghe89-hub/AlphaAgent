"""Enhanced public research uses real contracts with explicitly synthetic model fixtures."""

# ruff: noqa: F811

import asyncio
import json
from threading import Event

import pytest

from marketpulse.quant.knowledge import active_research as active
from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.research_service import run

from .test_research import SyntheticPort, knowledge, runtime  # noqa: F401


class ActivePort(SyntheticPort):
    async def generate(self, request):
        if request.response_model is not active.ResearchPlan:
            return await super().generate(request)
        from types import SimpleNamespace

        value = json.loads(request.messages[-1].content.split("\n上次检查失败：")[0])
        self.roles.append("Researcher")
        self.contexts.append(value)
        assert request.thinking_enabled
        sid = value["stocks"][0]["security_id"]
        result = active.ResearchPlan(
            selections=[
                dict(
                    security_id=sid,
                    method_rule_ids=["KR-TREND-MARKET"],
                    hypothesis="合成测试核对市场变化。",
                    countercheck="合成测试保留反向证据。",
                )
            ],
            tools=[dict(tool="EVIDENCE_SEARCH", security_id=sid, query="收盘价")],
        )
        return SimpleNamespace(
            output=result,
            usage=SimpleNamespace(input_tokens=100, output_tokens=100),
            provider="SYNTHETIC",
            model="SYNTHETIC",
            response_id="synthetic",
        )


def test_enhanced_run_keeps_old_history_and_replays_tool_manifest(runtime, monkeypatch):
    repo, packet, settings = runtime
    from uuid import uuid4

    snapshot = knowledge()
    for item in snapshot["rules"]:
        item["rule"].update(
            system_rule="核对实际证据并保留适用条件。",
            horizons=["SWING", "MEDIUM"],
            required_inputs=["行情"],
            conditions=["同一截止时点。"],
            counterexamples=["趋势可能反复。"],
            forbidden_inferences=["不能保证收益。"],
            execution_status="REFERENCE_ONLY",
            validation_status="UNVALIDATED",
        )
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.research_service.knowledge_snapshot", lambda *a, **kw: snapshot
    )
    request = InvestmentRequest(
        request_key=uuid4(),
        entities=["000333"],
        enhanced_research=True,
        profile=dict(capital="987654.32", available_cash="987654.32"),
    )
    assert "enhanced_research" not in request.calculation_request()
    state, _ = repo.create(request.model_dump(mode="json"))
    port = ActivePort()
    result = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert port.roles == ["Researcher", "Planner", "Analyst", "Verifier"]
    assert result["model_steps"]["Writer"]["output_kind"] == "deterministic_publication"
    assert "987654.32" not in json.dumps(port.contexts)
    assert repo.get(state["run_id"])["data"]["research_opinions"]
    manifest = repo.value(result["active_manifest_ref"])
    assert manifest["results"][0]["tool"] == "EVIDENCE_SEARCH"
    # A modified tool result cannot be accepted as an equivalent finished report.
    manifest["results"][0]["facts"] = []
    changed = dict(result, active_manifest_ref=repo.blob(manifest))
    with pytest.raises(ValueError, match="重现"):
        repo.verify(changed)
