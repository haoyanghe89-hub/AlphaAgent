"""Offline verification of an investment research export; never sends data."""

import argparse
import hashlib
import json
import re
import zipfile

from marketpulse.quant.contracts import digest
from marketpulse.quant.knowledge import research_agents as agents
from marketpulse.quant.knowledge.features import VERSION, compute_features
from marketpulse.quant.knowledge.playbook import allocate_plans
from marketpulse.quant.knowledge.research_context import analysis_basis, direction_check_context


def verify(path):
    with zipfile.ZipFile(path) as archive:
        if (
            len(archive.infolist()) > 202
            or sum(i.file_size for i in archive.infolist()) > 65_000_000
        ):
            raise ValueError("核验包超出大小限制")
        blobs = {}
        for item in archive.infolist():
            if item.filename in ("record.json", "README.txt"):
                continue
            if not re.fullmatch(r"blobs/[0-9a-f]{64}\.bin", item.filename):
                raise ValueError("核验包包含不支持的文件路径")
            raw = archive.read(item)
            sha = item.filename[6:-4]
            if hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError("原始文件哈希不一致")
            blobs["blob://sha256/" + sha] = raw
        state = json.loads(archive.read("record.json"))

    def value(ref):
        return json.loads(blobs[ref])

    report = state["data"]
    if value(state["result_ref"]) != report or digest(report) != state["result_sha256"]:
        raise ValueError("固定报告不一致")
    packet = value(state["inputs_ref"])
    assert digest(packet) == report["input_digest"]
    if report["features"]["feature_version"] != VERSION:
        raise ValueError("请使用归档版本的指标代码复算")
    assert compute_features(packet, strict=False) == report["features"]
    assert allocate_plans(report["candidates"], report["request"]["profile"]) == report["portfolio"]
    context = value(state["public_context_ref"])
    if state["request"]["schema_version"] in {
        "investment-research-v2",
        "investment-research-v3",
        "investment-research-v4",
    }:
        from types import SimpleNamespace

        from marketpulse.quant.knowledge.context import verify as verify_context

        verify_context(SimpleNamespace(value=value, raw_bytes=lambda ref: blobs[ref]), state)
    if state["request"]["schema_version"] == "investment-research-v4":
        from types import SimpleNamespace

        from marketpulse.quant.knowledge.evaluation import evaluate
        from marketpulse.quant.knowledge.observations import verify_protocol, verify_snapshot

        repo = SimpleNamespace(value=value, raw_bytes=lambda ref: blobs[ref])
        verify_protocol(repo, state)
        for observation in state.get("observations", []):
            original = {
                k: v for k, v in observation.items() if k not in {"record_ref", "observation_id"}
            }
            assert digest(original) == observation["observation_id"]
            assert value(observation["record_ref"]) == original
            assert (
                observation["result_ref"] == state["result_ref"]
                and observation["protocol_ref"] == state["forward_protocol_ref"]
            )
            if observation.get("snapshot_ref"):
                snap = value(observation["snapshot_ref"])
                verify_snapshot(repo, state, snap)
                assert evaluate(report, packet, snap) == observation["evaluation"]

    def output(key, schema):
        step = state["model_steps"][key]
        version = step.get("prompt_version", state["config"]["prompt_version"])
        call = state["calls"][step["call_index"]]
        request = value(call["request_ref"])
        kind = step.get("output_kind")
        actual_schema = (
            agents.ReviewChecks
            if kind == "review_checks"
            else agents.DirectionCheck
            if kind == "direction_check"
            else schema
        )
        assert call["status"] == "COMPLETED" and call["response_ref"] == step["response_ref"]
        assert request["context"] == value(step["context_ref"])
        assert (
            request["instructions"]
            == agents.role_prompts(dict(state["config"], prompt_version=version))[step["role"]]
        )
        assert request["prompt_version"] == version
        assert request["schema"] == actual_schema.model_json_schema()
        result = actual_schema.model_validate(value(step["response_ref"]))
        if kind == "review_checks" and schema is agents.Review:
            return agents.finalize_review(result)
        if kind == "direction_check" and schema is agents.Review:
            previous = output(step["previous_review_step"], agents.Review)
            expected = direction_check_context(
                value(state["model_steps"][step["previous_review_step"]]["context_ref"]), previous
            )
            assert request["context"] == expected
            return agents.combine_direction_check(result, previous)
        return result

    plan = output("Planner", agents.Plan)
    agents.guard_plan(plan, context)
    analysis = output(state["approved_analysis_step"], agents.Analysis)
    agents.guard_analysis(
        analysis,
        analysis_basis(
            context,
            plan.model_dump(mode="json"),
            scoped=state["config"].get("scoped_analysis", False),
        ),
    )
    review = output(state["approved_review_step"], agents.Review)
    agents.guard_review(review, analysis)
    assert all(r.verdict == "APPROVE" for r in review.reviews)
    approved = analysis.model_dump(mode="json")
    assert report["research_opinions"] == approved["opinions"] and report[
        "approved_hash"
    ] == digest(approved)
    agents.guard_writing(output("Writer", agents.Writing), approved)
    assert (
        state["model_calls"] <= 24
        and state["charged_tokens"] <= 60000
        and state["elapsed_seconds"] <= 900
    )
    return dict(
        run_id=state["run_id"],
        verified_blobs=len(blobs),
        features_recomputed=True,
        portfolio_recomputed=True,
        approved_content_verified=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    print(json.dumps(verify(parser.parse_args().archive), ensure_ascii=False))


if __name__ == "__main__":
    main()
