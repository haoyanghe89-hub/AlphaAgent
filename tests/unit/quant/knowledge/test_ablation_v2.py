# ruff: noqa: F811
import asyncio
from types import SimpleNamespace

import pytest

from marketpulse.quant.knowledge import ablation as a
from marketpulse.quant.knowledge.structured_contract import WireAnalysis

from .test_ablation import Port, frozen
from .test_research import runtime  # noqa: F401


class WirePort(Port):
    async def generate(self, request):
        if request.response_model is not WireAnalysis:
            return await super().generate(request)
        response = await super().generate(
            request.model_copy(update=dict(response_model=a.ComparisonAnalysis))
        )
        value = response.output.model_dump(mode="json")
        for opinion in value["opinions"]:
            opinion.pop("direction_claim_ids")
            opinion["direction_supports"] = ["S1"]
            for c in opinion["claims"] + opinion["opposing"]:
                c.pop("claim_id")
        response.output = WireAnalysis.model_validate(value)
        return response


def test_v2_six_arms_keep_guards_and_v1_reads_unchanged(runtime):
    repo, old_manifest, trial, settings = frozen(runtime)
    manifest = dict(
        old_manifest,
        version=a.REPAIRED_VERSION,
        prompt_version=a.REPAIRED_VERSION,
        prompts=a.prompts_for(a.REPAIRED_VERSION),
    )
    trial.update(
        version=a.REPAIRED_VERSION,
        manifest_ref=repo.blob(manifest),
        config=a.config_for(a.REPAIRED_VERSION),
    )
    saved = []
    rules = SimpleNamespace(archive_audit=lambda state: "SYNTHETIC-AUDIT")
    result = asyncio.run(
        a.execute(
            repo, rules, trial, settings, lambda state, audit: saved.append(audit), port=WirePort()
        )
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert a.verify(repo.value, result)["completed"] == 6
    assert result["config"]["budget"] == a.BUDGET
    # The bank's fixed/adaptive routes are identical; exact calls may be reused, never fabricated.
    assert len(result["calls"]) == 10
    assert all(c["thinking_enabled"] is False for c in result["calls"])
    assert all(r.get("wire_ref") for r in result["results"].values())
    assert old_manifest["version"] == a.VERSION
    assert a.prompts_for(a.VERSION) == old_manifest["prompts"]
    changed = dict(result, config=dict(result["config"], budget=dict(max_tokens=999999)))
    with pytest.raises(ValueError, match="预算"):
        a.verify(repo.value, changed)


def test_individual_review_uses_native_reservation_and_rejects_contradictory_verdict(
    runtime, monkeypatch
):
    from marketpulse.quant.decision.next_session import input_reservation
    from marketpulse.quant.knowledge import research_agents

    # Synthetic token metadata only; the official tokenizer has independent real-call calibration.
    monkeypatch.setattr(
        input_reservation,
        "estimate",
        lambda *args: dict(
            version=input_reservation.VERSION,
            local_tokens=100,
            margin_tokens=1024,
            reserved_input_tokens=1124,
        ),
    )
    repo, old_manifest, trial, settings = frozen(runtime)
    manifest = dict(
        old_manifest,
        version=a.TOKEN_VERSION,
        prompt_version=a.TOKEN_VERSION,
        prompts=a.prompts_for(a.TOKEN_VERSION),
    )
    trial.update(
        version=a.TOKEN_VERSION,
        manifest_ref=repo.blob(manifest),
        config=a.config_for(a.TOKEN_VERSION),
    )
    result = asyncio.run(
        a.execute(
            repo,
            SimpleNamespace(archive_audit=lambda _: "SYNTHETIC"),
            trial,
            settings,
            lambda *_: None,
            port=WirePort(),
        )
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert a.verify(repo.value, result)["completed"] == 6 and len(result["calls"]) == 10
    record = next(iter(result["results"].values()))
    analysis = a.ComparisonAnalysis.model_validate(repo.value(record["analysis_ref"]))
    review = repo.value(record["review_ref"])
    review["reviews"][0]["verdict"] = "REJECT"
    with pytest.raises(ValueError, match="矛盾"):
        a.guard_review(research_agents.Review.model_validate(review), analysis, a.TOKEN_VERSION)
    assert a.config_for(a.TOKEN_VERSION)["budget"] == a.BUDGET
