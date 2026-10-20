# ruff: noqa: F811
import asyncio
import copy
import json
from threading import Event
from uuid import uuid4

import pytest

from marketpulse.quant.knowledge import research_agents as agents
from marketpulse.quant.knowledge import structured_contract as s
from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.research_service import run

from .test_research import SyntheticPort, runtime  # noqa: F401


def wire():
    return s.WireAnalysis(
        opinions=[
            dict(
                security_id="000333.SZ",
                direction="FAVORABLE",
                summary="倾向看多，但等待条件核对。",
                claims=[
                    dict(text="经营情况仍须持续核查。", evidence_ids=["F-A"]),
                    dict(text="趋势变化存在条件支撑。", evidence_ids=["F-B"]),
                ],
                opposing=[dict(text="市场环境仍有反向风险。", evidence_ids=["F-C"])],
                direction_supports=["S1"],
                method_rule_ids=["KR-AVOID-RUIN"],
                invalidation=["若支撑转弱则重新判断。"],
                next_checks=["跟踪后续披露与价格。"],
            )
        ]
    )


def test_machine_ids_do_not_rewrite_judgment_and_cannot_duplicate():
    original = wire()
    value = s.analysis(original)
    o = value.opinions[0]
    assert [c.claim_id for c in o.claims + o.opposing] == ["C1", "C2", "C3"]
    assert o.direction_claim_ids == ("C1",)
    assert o.opposing[0].text == original.opinions[0].opposing[0].text
    assert o.opposing[0].evidence_ids == original.opinions[0].opposing[0].evidence_ids
    assert "claim_id" not in s.WireAnalysis.model_json_schema()["$defs"]["Statement"]["properties"]
    changed = original.model_dump(mode="json")
    changed["opinions"][0]["opposing"][0] = changed["opinions"][0]["claims"][0]
    with pytest.raises(ValueError, match="重复"):
        s.analysis(s.WireAnalysis.model_validate(changed))


def test_bad_support_and_evidence_not_silently_repaired():
    changed = wire().model_dump(mode="json")
    changed["opinions"][0]["direction_supports"] = ["S3"]
    with pytest.raises(ValueError, match="不存在"):
        s.analysis(s.WireAnalysis.model_validate(changed))
    canonical = s.analysis(wire())
    with pytest.raises(ValueError):
        agents.guard_analysis(
            canonical, dict(stocks=[dict(security_id="000333.SZ")], methods=[], evidence=[])
        )


def test_legacy_request_bytes_and_calculation_remain_unchanged():
    request = InvestmentRequest(request_key=uuid4(), entities=["000333"])
    assert "execution_contract" not in request.model_dump(mode="json")
    revised = request.model_copy(update=dict(execution_contract=s.VERSION))
    assert revised.calculation_request() == request.calculation_request()


class WirePort(SyntheticPort):
    async def generate(self, request):
        if request.response_model is not s.WireAnalysis:
            return await super().generate(request)
        response = await super().generate(
            request.model_copy(update=dict(response_model=agents.Analysis))
        )
        value = response.output.model_dump(mode="json")
        context = json.loads(request.messages[-1].content.split("\n上次检查失败：")[0])
        required = {r["security_id"]: r for r in context["citation_requirements"]}
        for opinion in value["opinions"]:
            identifiers = [x["claim_id"] for x in opinion["claims"]]
            opinion["direction_supports"] = [
                "S" + str(identifiers.index(i) + 1) for i in opinion.pop("direction_claim_ids")
            ]
            for statement in opinion["claims"] + opinion["opposing"]:
                statement.pop("claim_id")
            for index, kind in enumerate(("technical", "funding", "policy")):
                facts = required[opinion["security_id"]][kind]
                if facts:
                    opinion["claims"][index % 2]["evidence_ids"].append(facts[0])
        response.output = s.WireAnalysis.model_validate(value)
        return response


def test_new_wire_call_replay_and_tamper_protection_use_original_guards(runtime):
    repo, packet, settings = runtime
    request = InvestmentRequest(
        schema_version="investment-research-v1",
        request_key=uuid4(),
        entities=["000333"],
        execution_contract=s.VERSION,
    )
    state, _ = repo.create(request.model_dump(mode="json"))
    result = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=WirePort(),
            collector=lambda *_: packet,
        )
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert result["config"]["budget"] == dict(max_model_calls=24, max_tokens=60000, max_seconds=900)
    checkpoint = result["model_steps"][result["approved_analysis_step"]]
    assert checkpoint["output_kind"] == "structured_analysis"
    assert (
        repo.get(result["run_id"])["data"]["research_opinions"][0]["claims"][0]["claim_id"] == "C1"
    )
    tampered = copy.deepcopy(result)
    index = checkpoint["call_index"]
    definition = repo.value(tampered["calls"][index]["request_ref"])
    definition["schema"] = agents.Analysis.model_json_schema()
    tampered["calls"][index]["request_ref"] = repo.blob(definition)
    with pytest.raises(ValueError, match="固定调用"):
        repo.output(tampered, result["approved_analysis_step"], agents.Analysis)
