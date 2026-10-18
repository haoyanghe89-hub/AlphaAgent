# ruff: noqa: F811
import asyncio
import copy
from types import SimpleNamespace

import pytest

from marketpulse.quant.knowledge import ablation as a
from marketpulse.quant.knowledge.planning_service import build_report
from marketpulse.quant.knowledge.research_context import public_context

from .test_research import knowledge, runtime  # noqa: F401


def frozen(runtime):
    repo, packet, settings = runtime
    request = dict(
        request_key="11111111-1111-1111-1111-111111111111",
        entities=["000333"],
        horizon="SWING",
        profile=None,
    )
    core = build_report(request, packet, [], [])
    base, _ = public_context(core, packet, knowledge(), 20)
    base["stocks"][0]["eligible_routes"].append("QUALITY_GROWTH")
    base["stocks"][0]["routes"][1]["eligible"] = True
    bank = copy.deepcopy(base["stocks"][0])
    bank.update(
        security_id="600036.SH",
        name="SYNTHETIC BANK",
        eligible_routes=["TREND_SWING"],
        financial_scope="FINANCIAL_NOT_APPLICABLE",
    )
    bank["routes"] = bank["routes"][:1]
    base["stocks"].append(bank)
    for fact in list(base["evidence"]):
        cloned = copy.deepcopy(fact)
        cloned["security_id"] = "600036.SH"
        cloned["evidence_id"] = cloned["evidence_id"].replace("000333SZ", "600036SH")
        base["evidence"].append(cloned)
    state = dict(
        status="COMPLETED",
        run_id="SYNTHETIC-K7",
        result_sha256="b" * 64,
        public_context_ref=repo.blob(base),
        inputs_ref=repo.blob(packet),
        source_refs=[],
        knowledge_ref=repo.blob(knowledge()),
        data=dict(market_date=packet["market_date"]),
    )
    manifest = a.freeze(repo, state, settings.model)
    execution = dict(
        version=a.VERSION,
        run_id="SYNTHETIC",
        manifest_ref=repo.blob(manifest),
        status="FROZEN",
        config=dict(prompt_version=a.PROMPT_VERSION, budget=a.BUDGET),
        model_calls=0,
        charged_tokens=0,
        tokens=0,
        calls=[],
        results={},
        elapsed_seconds=0,
    )
    return repo, manifest, execution, settings


class Port:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def generate(self, request):
        import json

        self.calls += 1
        context = json.loads(request.messages[-1].content.split("\n上次检查失败：")[0])
        if request.response_model is a.ComparisonAnalysis:
            sid = context["stocks"][0]["security_id"]
            facts = [f["evidence_id"] for f in context["evidence"]]
            output = dict(
                opinions=[
                    dict(
                        security_id=sid,
                        direction="FAVORABLE",
                        summary="倾向看多，合成测试仅核对流程。",
                        claims=[
                            dict(
                                claim_id="C1",
                                text="合成测试：价格条件需持续核对。",
                                evidence_ids=[facts[0]],
                            ),
                            dict(
                                claim_id="C2",
                                text="合成测试：市场条件仍有不确定性。",
                                evidence_ids=[facts[1]],
                            ),
                        ],
                        opposing=[
                            dict(
                                claim_id="C3",
                                text="合成测试：近期表现不能保证未来方向。",
                                evidence_ids=[facts[0]],
                            )
                        ],
                        direction_claim_ids=["C1"],
                        method_rule_ids=[context["methods"][0]["rule_id"]]
                        if context["methods"]
                        else [],
                        invalidation=["条件变化时需重新核查。"],
                        next_checks=["观察后续真实行情。"],
                    )
                ]
            )
        else:
            if self.fail:
                raise ValueError("SYNTHETIC verifier outage")
            output = dict(
                reviews=[
                    dict(
                        security_id=o["security_id"],
                        verdict="APPROVE",
                        decisions=[
                            dict(
                                claim_id=c["claim_id"],
                                verdict="APPROVE",
                                reason="合成测试确认证据绑定。",
                            )
                            for c in o["claims"] + o["opposing"]
                        ],
                        direction_supported=True,
                        conditions_respected=True,
                        reason="合成流程核对，不是投资效果验证。",
                    )
                    for o in context["proposal"]["opinions"]
                ]
            )
        return SimpleNamespace(
            output=request.response_model.model_validate(output),
            usage=SimpleNamespace(input_tokens=100, output_tokens=100),
            provider="SYNTHETIC",
            model="SYNTHETIC",
            response_id="SYNTHETIC",
        )


def test_three_arms_have_identical_facts_no_method_group_empty_and_bank_no_quality(runtime):
    repo, manifest, _, _ = frozen(runtime)
    assert len(manifest["scenarios"]) == 6
    for case_id in ("1", "2"):
        contexts = [
            repo.value(s["context_ref"]) for s in manifest["scenarios"] if s["case_id"] == case_id
        ]
        assert contexts[0]["evidence"] == contexts[1]["evidence"] == contexts[2]["evidence"]
    scenarios = {
        s["case_id"] + s["arm"]: repo.value(s["context_ref"]) for s in manifest["scenarios"]
    }
    assert (
        scenarios["1NO_METHOD"]["methods"] == []
        and scenarios["1NO_METHOD"]["stocks"][0]["routes"] == []
    )
    assert scenarios["1ADAPTIVE_ROUTE"]["stocks"][0]["preferred_route"] == "QUALITY_GROWTH"
    assert scenarios["2ADAPTIVE_ROUTE"]["stocks"][0]["preferred_route"] == "TREND_SWING"


def test_real_checkpoints_recover_do_not_repeat_analysis_or_change_complete_results(runtime, rules):
    repo, manifest, state, settings = frozen(runtime)
    snapshots = []

    def persist(s, i):
        snapshots.append((copy.deepcopy(s), i))

    first = asyncio.run(a.execute(repo, rules, state, settings, persist, port=Port(fail=True)))
    assert first["status"] == "INTERRUPTED"
    assert any(c["role"] == "Analyst" and c["status"] == "COMPLETED" for c in first["calls"])
    port = Port()
    result = asyncio.run(a.execute(repo, rules, state, settings, persist, port=port))
    assert result["status"] == "COMPLETED", result.get("error")
    # The bank's adaptive and fixed route have exactly identical inputs and reuse a checkpoint.
    assert len(result["results"]) == 6 and port.calls == 9
    assert a.verify(repo.value, result)["completed"] == 6
    before = copy.deepcopy(result)
    assert asyncio.run(a.execute(repo, rules, state, settings, persist, port=Port())) == before
    bad = copy.deepcopy(result)
    bad["report"]["future_accuracy"] = "1"
    with pytest.raises(ValueError):
        a.verify(repo.value, bad)
    bad = copy.deepcopy(result)
    bad["config"]["budget"] = dict(a.BUDGET, max_tokens=60001)
    with pytest.raises(ValueError):
        a.verify(repo.value, bad)


def test_context_or_method_injection_fails_fixed_comparison_binding(runtime, rules):
    repo, manifest, state, settings = frozen(runtime)
    result = asyncio.run(a.execute(repo, rules, state, settings, lambda *_: None, port=Port()))
    scenario = manifest["scenarios"][0]
    context = repo.value(scenario["context_ref"])
    context["evidence"][0]["value"] = "999999"
    scenario["context_ref"] = repo.blob(context)
    state["manifest_ref"] = repo.blob(manifest)
    with pytest.raises(ValueError, match="固定公开输入"):
        a.verify(repo.value, state)
    context = repo.value(manifest["base_context_ref"])
    output = a.ComparisonAnalysis.model_validate(
        repo.value(next(iter(result["results"].values()))["analysis_ref"])
    )
    with pytest.raises(ValueError):
        a.guard_analysis(output, context)
