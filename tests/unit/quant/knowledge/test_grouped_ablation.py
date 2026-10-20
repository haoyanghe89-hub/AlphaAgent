# ruff: noqa: F811
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from marketpulse.quant.knowledge import grouped_ablation as g

from .test_ablation import frozen
from .test_ablation_v2 import WirePort
from .test_research import runtime  # noqa: F401


def setup(runtime):
    repo, manifest, trial, settings = frozen(runtime)
    manifest.update(
        version=g.VERSION,
        prompt_version=g.VERSION,
        repair_rounds=2,
        prompts=dict(
            Analyst=g.base.prompts_for(g.base.REPAIRED_VERSION)["Analyst"], Verifier=g.REVIEW_PROMPT
        ),
    )
    trial.update(
        version=g.VERSION, manifest_ref=repo.blob(manifest), config=g.config_for(g.VERSION)
    )
    return repo, manifest, trial, settings


class GroupPort(WirePort):
    def __init__(self, *, reject=False, outage=False):
        super().__init__()
        self.reject, self.outage = reject, outage
        self.contexts = []
        self.reviews = 0

    async def generate(self, request):
        context = json.loads(request.messages[-1].content.split("\n上次检查失败：")[0])
        self.contexts.append((request.response_model, context))
        if request.response_model is not g.BatchReview:
            return await super().generate(request)
        if self.outage:
            error = ValueError("SYNTHETIC review outage with known usage")
            error.input_tokens, error.output_tokens = 100, 0
            raise error
        self.reviews += 1
        arms = []
        for arm in context["arms"]:
            reviews = []
            for opinion in arm["proposal"]["opinions"]:
                rejected = self.reject and self.reviews == 1 and arm["key"] == "1:NO_METHOD"
                decisions = [
                    dict(
                        claim_id=c["claim_id"], verdict="APPROVE", reason="合成流程仅核对证据绑定。"
                    )
                    for c in opinion["claims"] + opinion["opposing"]
                ]
                if rejected:
                    decisions[0].update(verdict="REJECT", reason="合成测试要求本组核对市场条件。")
                reviews.append(
                    dict(
                        security_id=opinion["security_id"],
                        verdict="REJECT" if rejected else "APPROVE",
                        decisions=decisions,
                        direction_supported=not rejected,
                        conditions_respected=True,
                        reason="合成流程复核，不作为真实研究或投资效果。",
                    )
                )
            arms.append(dict(key=arm["key"], review=dict(reviews=reviews)))
        return SimpleNamespace(
            output=g.BatchReview.model_validate(dict(arms=arms)),
            usage=SimpleNamespace(input_tokens=100, output_tokens=100),
            provider="SYNTHETIC",
            model="SYNTHETIC",
            response_id="SYNTHETIC",
        )


def run(repo, trial, settings, port):
    return asyncio.run(
        g.execute(
            repo,
            SimpleNamespace(archive_audit=lambda _: "SYNTHETIC"),
            trial,
            settings,
            lambda *_: None,
            port=port,
        )
    )


def test_six_independent_arms_shared_facts_and_isolated_correction(runtime):
    repo, manifest, trial, settings = setup(runtime)
    port = GroupPort(reject=True)
    result = run(repo, trial, settings, port)
    assert result["status"] == "COMPLETED", result.get("error")
    assert g.verify(repo.value, result)["completed"] == 6
    assert result["charged_tokens"] <= 60000 and result["model_calls"] <= 24
    contexts = [c for schema, c in port.contexts if schema is not g.BatchReview]
    assert len(contexts) == 7  # Six isolated analyses and only the rejected arm repaired.
    assert all(len(c["stocks"]) == 1 and "arms" not in c for c in contexts)
    assert contexts[0]["methods"] == [] and contexts[-1]["methods"] == []
    assert all(not c["review_feedback"] for c in contexts[:6])
    assert len(contexts[-1]["review_feedback"]) == 1
    batches = [c for schema, c in port.contexts if schema is g.BatchReview]
    assert len(batches[0]["arms"]) == 6 and len(batches[1]["arms"]) == 1
    assert len({e["evidence_id"] for e in batches[0]["common_evidence"]}) == len(
        batches[0]["common_evidence"]
    )
    for mutate in (
        lambda s: s["config"]["budget"].update(max_tokens=60001),
        lambda s: s["results"].pop("1:NO_METHOD"),
        lambda s: s["calls"][0].update(thinking_enabled=True),
    ):
        changed = copy.deepcopy(result)
        mutate(changed)
        with pytest.raises(ValueError):
            g.verify(repo.value, changed)
    missing = g.BatchReview.model_validate(
        dict(arms=repo.value(result["calls"][6]["response_ref"])["arms"][:1])
    )
    with pytest.raises(ValueError, match="逐组完整覆盖"):
        g.guard_review(
            missing,
            repo,
            {g.key_for(s): result["results"][g.key_for(s)] for s in manifest["scenarios"]},
        )


def test_review_outage_recovers_analyst_checkpoints_and_keeps_failed_calls(runtime):
    repo, _, trial, settings = setup(runtime)
    first = run(repo, trial, settings, GroupPort(outage=True))
    assert first["status"] == "INTERRUPTED"
    assert len([e for e in first["history"] if e["role"] == "Analyst"]) == 6
    failed = sum(c["status"] == "FAILED" for c in first["calls"])
    assert failed > 0
    port = GroupPort()
    result = run(repo, trial, settings, port)
    assert result["status"] == "COMPLETED", result.get("error")
    assert all(schema is g.BatchReview for schema, _ in port.contexts)
    assert sum(c["status"] == "FAILED" for c in result["calls"]) == failed
    assert g.verify(repo.value, result)["completed"] == 6


def test_compact_review_losslessly_interns_methods_stocks_and_windows(runtime):
    repo, manifest, trial, settings = setup(runtime)
    manifest.update(
        version=g.COMPACT_VERSION,
        prompt_version=g.COMPACT_VERSION,
        prompts=g.prompts_for(g.COMPACT_VERSION),
    )
    trial.update(
        version=g.COMPACT_VERSION,
        manifest_ref=repo.blob(manifest),
        config=g.config_for(g.COMPACT_VERSION),
    )
    port = GroupPort()
    result = run(repo, trial, settings, port)
    assert result["status"] == "COMPLETED", result.get("error")
    assert g.verify(repo.value, result)["completed"] == 6
    context = next(c for schema, c in port.contexts if schema is g.BatchReview)
    expanded = copy.deepcopy(context)
    expanded.pop("common_methods")
    expanded.pop("common_stocks")
    for arm in expanded["arms"]:
        arm["methods"] = [context["common_methods"][k] for k in arm.pop("method_refs")]
        arm["stocks"] = [context["common_stocks"][k] for k in arm.pop("stock_refs")]
    old = dict(manifest, version=g.VERSION)
    current = {e["key"]: e for e in result["history"] if e["role"] == "Analyst"}
    original = g.review_context(repo, old, current)
    assert expanded["arms"] == original["arms"]  # No condition, quote, route or proposal lost.
    for compact, full in zip(context["common_evidence"], original["common_evidence"], strict=True):
        assert context["windows"][compact["window"]] == original["windows"][full["window"]]
        assert {k: v for k, v in compact.items() if k != "window"} == {
            k: v for k, v in full.items() if k != "window"
        }
    assert len(json.dumps(context)) < len(json.dumps(original))
