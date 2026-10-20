"""Synthetic model/market fixtures test enhanced safety, not investment performance."""

import asyncio
import copy
import json
import re
from threading import Event
from types import SimpleNamespace

import pytest
from test_candidates_api import Port, payload, source_fixture
from test_decision_api import setup  # noqa: F401

from marketpulse.quant.contracts import digest
from marketpulse.quant.decision import candidate_enhanced as enhanced
from marketpulse.quant.decision import screening
from marketpulse.quant.decision.contracts import BudgetProfile
from marketpulse.quant.knowledge.routing import RULE_IDS


def knowledge():
    return dict(
        rules=[
            dict(
                rule_id=rule_id,
                version="a" * 64,
                rule=dict(
                    title="合成测试方法",
                    system_rule="依据公开原文检验条件，不把方法当作事实。",
                    required_inputs=["公开披露"],
                    conditions=["核验主体和同期间。"],
                    counterexamples=["会计利润未必兑现现金。"],
                    forbidden_inferences=["不能保证收益。"],
                    execution_status="REFERENCE_ONLY",
                    validation_status="UNVALIDATED",
                    horizons=["MEDIUM"],
                ),
            )
            for rule_id in RULE_IDS
        ],
        documents=[],
        fragments=[],
    )


class EnhancedPort(Port):
    def __init__(self, reject_once=False, interrupt=False):
        self.contexts, self.schemas = [], []
        self.reject_once, self.interrupt = reject_once, interrupt
        self.reviews = 0

    async def generate(self, request):
        ctx = json.loads(request.messages[-1].content.split("\n上次检查失败")[0])
        self.contexts.append(ctx)
        self.schemas.append(request.response_model)
        if request.response_model in {enhanced.StockAnalysis, enhanced.StockReview}:
            assert request.thinking_enabled is False
            assert request.reasoning_effort is None
        else:
            assert request.thinking_enabled
            assert request.reasoning_effort == "low"
        if request.response_model is enhanced.ResearchPlan:
            output = enhanced.ResearchPlan(
                selections=[
                    dict(
                        security_id=sid,
                        method_rule_ids=["KR-QUALITY-ECONOMICS"],
                        hypothesis="合成测试假设经营与现金回收存在差异。",
                        countercheck="核验原文风险是否否定该假设。",
                    )
                    for sid in ctx["selected"]
                ],
                tools=[
                    dict(tool="EVIDENCE_SEARCH", security_id=sid, query="现金风险")
                    for sid in ctx["selected"][:2]
                ],
            )
            return SimpleNamespace(
                output=output,
                provider="SYNTHETIC",
                model="SYNTHETIC",
                response_id="synthetic",
                usage=SimpleNamespace(input_tokens=100, output_tokens=100),
            )
        if self.interrupt and request.response_model is enhanced.StockAnalysis:
            raise asyncio.CancelledError()
        if request.response_model is enhanced.StockReview:
            self.reviews += 1
            self.reject = self.reject_once and self.reviews == 1
        underlying = request.model_copy(
            update={
                "response_model": screening.CandidateAnalysis
                if request.response_model is enhanced.StockAnalysis
                else screening.CandidateReview
            }
        )
        response = await super().generate(underlying)
        if request.response_model is enhanced.StockAnalysis:
            value = next(
                c for c in response.output.candidates if c.security_id == ctx["target_security_id"]
            ).model_dump(mode="json")
            for plural, singular in (
                ("supporting_reasons", "supporting_reason"),
                ("opposing_reasons", "opposing_reason"),
                ("verification_conditions", "verification_condition"),
                ("invalidation_conditions", "invalidation_condition"),
            ):
                value[singular] = value.pop(plural)[0]
            response.output = enhanced.StockAnalysis.model_validate(value)
        else:
            value = response.output.decisions[0].model_dump(mode="json")
            response.output = enhanced.StockReview(
                **value,
                summary_supported=response.output.summary_supported,
                blocking_claims=(ctx["proposal"]["candidates"][0]["summary"],)
                if value["verdict"] != "APPROVE"
                else (),
            )
        if request.max_output_tokens == 1:
            response.usage.output_tokens = 1
        return response


@pytest.fixture
def runtime(setup, monkeypatch):  # noqa: F811
    app, _ = setup
    repo = app.state.decision_store
    request = dict(
        payload(), enhanced_research=True, budget_profile=BudgetProfile().model_dump(mode="json")
    )
    state, _ = repo.create(request, "synthetic-enhanced")
    state["config"] = dict(
        agentic_research_v1=True, prompt_version=enhanced.VERSION, budget=request["budget_profile"]
    )
    repo.save(state)
    monkeypatch.setattr(enhanced, "knowledge_for", lambda *a, **kw: knowledge())
    settings = SimpleNamespace(
        model="SYNTHETIC", model_read_timeout_seconds=30, reasoning_effort="high"
    )
    return repo, state["run_id"], settings


def run(runtime, port):
    repo, rid, settings = runtime
    asyncio.run(
        screening.run(
            repo, rid, Event(), settings, port=port, collector=lambda repo, *_: source_fixture(repo)
        )
    )
    return repo.get(rid)


def test_candidate_enhanced_publication_methods_original_search_and_state_binding(runtime):
    port = EnhancedPort()
    result = run(runtime, port)
    assert result["status"] == "COMPLETED", result.get("error")
    repo = runtime[0]
    enhanced.verify_state(repo, result)
    assert result["model_calls"] == 5
    assert runtime[2].reasoning_effort == "high"
    assert port.schemas == [
        enhanced.ResearchPlan,
        enhanced.StockAnalysis,
        enhanced.StockReview,
        enhanced.StockAnalysis,
        enhanced.StockReview,
    ]
    assert result["data"]["selected"] == ["688082.SH", "688012.SH"]
    assert "测试候选和同业对照" not in json.dumps(port.contexts, ensure_ascii=False)
    meta = result["data"]["enhanced_research"]
    assert all(m["conditions"] and m["forbidden_inferences"] for m in meta["methods"])
    manifest = repo.value(meta["manifest_ref"])
    assert len(manifest["results"]) == 2
    assert all(r["status"] == "AVAILABLE" for r in manifest["results"])
    assert all(r["facts"][0]["locator"]["page"] == 1 for r in manifest["results"])
    analyst_context = next(
        ctx
        for ctx, schema in zip(port.contexts, port.schemas, strict=True)
        if schema is enhanced.StockAnalysis
    )
    review_context = next(
        ctx
        for ctx, schema in zip(port.contexts, port.schemas, strict=True)
        if schema is enhanced.StockReview
    )
    assert analyst_context["plan"] == result["plan"]
    assert "plan" not in review_context and "coverage" not in review_context
    assert review_context["research_selection"] == analyst_context["research_selection"]
    assert repo.value(meta["focus_ref"])["output_kind"] == "deterministic_research_focus"
    assert "evidence" not in port.contexts[0]
    assert {e["evidence_id"] for e in port.contexts[0]["evidence_catalog"]} <= {
        e["evidence_id"]
        for ctx, schema in zip(port.contexts, port.schemas, strict=True)
        if schema is enhanced.StockAnalysis
        for e in ctx["evidence"]
    }
    proposal = screening.CandidateAnalysis.model_validate(review_context["proposal"])
    for claims, verdict in (
        (("proposal从未说过的风险已经全部排除。",), "REJECT"),
        ((proposal.candidates[0].summary,), "APPROVE"),
    ):
        with pytest.raises(ValueError):
            enhanced.guard_stock_review(
                enhanced.StockReview(
                    security_id=proposal.candidates[0].security_id,
                    verdict=verdict,
                    summary_supported=True,
                    reason="合成复核定位测试。",
                    blocking_claims=claims,
                ),
                proposal,
            )
    cited = {e for c in proposal.candidates for e in (*c.evidence_ids, *c.counter_ids)}
    assert {e["evidence_id"] for e in review_context["evidence"]} == cited
    assert review_context["companies"] == analyst_context["companies"]
    assert review_context["methods"] == analyst_context["methods"]
    full = repo.value(result["data"]["analysis_context_ref"])
    originals = {e["evidence_id"]: e for e in full["evidence"]}
    for fact in analyst_context["evidence"]:
        original = originals[fact["evidence_id"]]
        if fact["kind"] == "disclosure":
            start, end = fact["original_excerpt_range"]
            assert fact["text"] == original["text"][start:end]
        else:
            assert fact == original
    assert all(
        method["conditions"] and method["counterexamples"] and method["forbidden_inferences"]
        for method in analyst_context["methods"]
    )
    assert result["config"]["candidate_input_packing"] == enhanced.INPUT_PACKING
    anchor_pattern = full.get("business_profile", {}).get("original_pattern", r"(?!)")
    business_ids = {e for c in full["companies"] for e in c["business_evidence_ids"]}
    for fact in analyst_context["evidence"]:
        original = originals[fact["evidence_id"]]
        if fact["evidence_id"] in business_ids and re.search(anchor_pattern, original["text"]):
            assert re.search(anchor_pattern, fact["text"])
    assert repo.value(meta["publication_ref"])["output_kind"] == "deterministic_publication"
    changed = copy.deepcopy(result)
    changed["config"]["reasoning_profile"]["roles"]["Researcher"]["thinking_enabled"] = False
    with pytest.raises(ValueError):
        enhanced.verify_state(repo, changed)
    changed = copy.deepcopy(result)
    changed["elapsed_seconds"] = result["request"]["budget_profile"]["max_seconds"] + 1
    with pytest.raises(ValueError):
        enhanced.verify_state(repo, changed)
    changed = copy.deepcopy(result["data"])
    manifest["results"][0]["facts"][0]["text"] = "篡改原文"
    changed["enhanced_research"]["manifest_ref"] = repo.blob(manifest)
    with pytest.raises(ValueError):
        screening.verify(repo, changed)


def test_added_original_search_citation_can_be_opened_from_candidate_page(runtime, setup):  # noqa: F811
    from fastapi.testclient import TestClient

    repo = runtime[0]
    result = run(runtime, EnhancedPort())
    assert result["status"] == "COMPLETED", result.get("error")
    new_fact = next(e for e in result["data"]["evidence"] if e["evidence_id"].startswith("CE-"))
    with TestClient(setup[0]) as client:
        response = client.get(
            "/api/quant/decision/runs/" + result["run_id"] + "/evidence/" + new_fact["evidence_id"]
        )
        assert response.status_code == 200
        assert response.json()["evidence"] == new_fact
        assert response.json()["document"]["raw_ref"] == new_fact["raw_ref"]
        export = client.get("/api/quant/decision/runs/" + result["run_id"] + "/export").json()
        assert export["enhancement_audit"]["focus"]["plan"] == result["plan"]
        assert all(
            c["record"]["role"] != "Planner" for c in export["enhancement_audit"]["model_calls"]
        )
    changed = copy.deepcopy(result["data"])
    changed["selected"].reverse()
    with pytest.raises(ValueError):
        screening.verify(repo, changed)


def test_candidate_enhanced_corrects_semantic_rejection_in_same_budget(runtime):
    result = run(runtime, EnhancedPort(reject_once=True))
    assert result["status"] == "COMPLETED", result.get("error")
    assert len(result["candidate_stock_rounds"]["688082.SH"]) == 2
    assert len(result["candidate_stock_rounds"]["688012.SH"]) == 1
    assert result["model_calls"] == 7
    assert result["charged_tokens"] == 1400
    first, second = result["candidate_stock_rounds"]["688082.SH"]
    repo = runtime[0]
    second_request = repo.value(result["calls"][second["analyst_index"]]["request_ref"])
    assert second_request["context"]["review_feedback"] == enhanced.StockReview.model_validate(
        repo.value(result["calls"][first["review_index"]]["response_ref"])
    ).review().model_dump(mode="json")
    enhanced.verify_state(repo, result)


def test_candidate_third_contract_correction_is_charged_without_relaxing_schema(runtime):
    from marketpulse.investigation.recording.errors import InvalidProviderResponseError

    class RepairPort(EnhancedPort):
        analyses = 0

        async def generate(self, request):
            response = await super().generate(request)
            if request.response_model is enhanced.StockAnalysis:
                self.analyses += 1
                if self.analyses == 1:
                    response.output = response.output.model_copy(update={"summary": "超预期"})
                elif self.analyses == 2:
                    raise InvalidProviderResponseError(
                        "synthetic extra field",
                        validation_issues=("candidates.0.<field>: extra_forbidden",),
                        input_tokens=100,
                        output_tokens=100,
                    )
            return response

    result = run(runtime, RepairPort())
    assert result["status"] == "COMPLETED", result.get("error")
    assert result["model_calls"] == 7 and result["charged_tokens"] == 1400
    failures = [
        c for c in result["calls"] if c.get("role") == "Analyst" and c["status"] == "FAILED"
    ]
    assert len(failures) == 2
    final = next(
        c for c in result["calls"] if c.get("role") == "Analyst" and c["status"] == "COMPLETED"
    )
    request = runtime[0].value(final["request_ref"])
    assert "extra_forbidden" in request["messages"][1]["content"]
    enhanced.verify_state(runtime[0], result)


def test_candidate_enhanced_resume_reuses_methods_originals_and_prior_model_calls(runtime):
    with pytest.raises(asyncio.CancelledError):
        run(runtime, EnhancedPort(interrupt=True))
    repo, rid, _ = runtime
    prior = repo.get(rid, with_data=False)
    assert prior["status"] == "INTERRUPTED"
    before = prior["model_calls"]
    resume = EnhancedPort()
    result = run(runtime, resume)
    assert result["status"] == "COMPLETED", result.get("error")
    assert enhanced.ResearchPlan not in resume.schemas
    assert screening.agents.Plan not in resume.schemas
    assert result["model_calls"] >= before + 2
    assert result["candidate_knowledge_ref"] == prior["candidate_knowledge_ref"]
    assert result["candidate_snapshot_ref"] == prior["candidate_snapshot_ref"]
    assert result["charged_tokens"] >= prior["charged_tokens"]
    enhanced.verify_state(repo, result)


def test_candidate_resume_preserves_approved_stock_and_shared_budget(runtime):
    class InterruptSecond(EnhancedPort):
        async def generate(self, request):
            ctx = json.loads(request.messages[-1].content.split("\n上次检查失败")[0])
            if (
                request.response_model is enhanced.StockAnalysis
                and ctx.get("target_security_id") == "688012.SH"
            ):
                raise asyncio.CancelledError()
            return await super().generate(request)

    with pytest.raises(asyncio.CancelledError):
        run(runtime, InterruptSecond())
    repo, rid, _ = runtime
    prior = repo.get(rid, with_data=False)
    approved_calls = prior["candidate_stock_rounds"]["688082.SH"]
    assert len(approved_calls) == 1
    port = EnhancedPort()
    result = run(runtime, port)
    assert result["status"] == "COMPLETED", result.get("error")
    assert result["candidate_stock_rounds"]["688082.SH"] == approved_calls
    assert all(c.get("target_security_id") == "688012.SH" for c in port.contexts)
    assert result["model_calls"] == prior["model_calls"] + 2
    assert result["charged_tokens"] == prior["charged_tokens"] + 400
    enhanced.verify_state(repo, result)
    changed = copy.deepcopy(result)
    changed["candidate_stock_rounds"]["688012.SH"] = approved_calls
    with pytest.raises(ValueError):
        enhanced.verify_state(repo, changed)
    ctx = repo.value(result["data"]["analysis_context_ref"])
    proposal = screening.CandidateAnalysis.model_validate(
        repo.value(result["data"]["analysis_ref"])
    )
    with pytest.raises(ValueError, match="membership/order"):
        screening.guard_analysis(proposal, ctx, expected_ids=["688082.SH"])
    with pytest.raises(ValueError, match="membership/order"):
        screening.guard_analysis(proposal, ctx, expected_ids=["outside"])


def test_candidate_enhanced_rejects_changed_budget_and_no_legacy_upgrade(runtime):
    repo, rid, settings = runtime
    state = repo.get(rid, with_data=False)
    state["config"]["budget"] = dict(state["config"]["budget"], max_tokens=60001)
    repo.save(state)
    asyncio.run(
        screening.run(
            repo,
            rid,
            Event(),
            settings,
            port=EnhancedPort(),
            collector=lambda repo, *_: source_fixture(repo),
        )
    )
    result = repo.get(rid, with_data=False)
    assert result["status"] == "FAILED"
    assert "预算" in result["error"]
    assert result["model_calls"] == 0


def test_candidate_stocks_share_one_call_limit_without_reset(runtime):
    from uuid import uuid4

    repo, rid, _ = runtime
    state = repo.get(rid, with_data=False)
    request = copy.deepcopy(state["request"])
    request["request_key"] = str(uuid4())
    request["budget_profile"]["max_model_calls"] = 3
    state, _ = repo.create(request, "synthetic-shared-budget")
    state["config"] = dict(
        agentic_research_v1=True, prompt_version=enhanced.VERSION, budget=request["budget_profile"]
    )
    repo.save(state)
    result = run((repo, state["run_id"], runtime[2]), EnhancedPort())
    assert result["status"] == "FAILED"
    assert "预算" in result["error"]
    assert result["model_calls"] == 3
    assert result["charged_tokens"] == 600
    assert len(result["candidate_stock_rounds"]["688082.SH"]) == 1
    assert result["candidate_stock_rounds"]["688012.SH"] == []
    assert "result_ref" not in result


def test_published_unpacked_candidate_protocol_is_still_readable(runtime):
    repo, rid, _ = runtime
    state = repo.get(rid, with_data=False)
    state["config"]["candidate_input_packing"] = None
    repo.save(state)
    port = EnhancedPort()
    result = run(runtime, port)
    assert result["status"] == "COMPLETED", result.get("error")
    review = next(
        ctx
        for ctx, schema in zip(port.contexts, port.schemas, strict=True)
        if schema is enhanced.StockReview
    )
    assert review["plan"] == result["plan"]
    enhanced.verify_state(repo, result)


def test_candidate_planning_is_bounded_even_when_model_requests_another_round(runtime):
    class TwoRounds(EnhancedPort):
        async def generate(self, request):
            response = await super().generate(request)
            if request.response_model is enhanced.ResearchPlan:
                ctx = json.loads(request.messages[-1].content)
                # A first round can deliberately proceed without extra source search.
                response.output = response.output.model_copy(
                    update={"ready": bool(ctx["research_round"]), "tools": ()}
                )
            return response

    result = run(runtime, TwoRounds())
    assert result["status"] == "COMPLETED", result.get("error")
    repo = runtime[0]
    manifest = repo.value(result["candidate_manifest_ref"])
    assert len(manifest["rounds"]) == 1
    assert result["model_calls"] == 5
    enhanced.verify_state(repo, result)


def test_candidate_enhanced_terminal_rejection_keeps_calls_and_never_publishes(runtime):
    class RejectAll(EnhancedPort):
        async def generate(self, request):
            response = await super().generate(request)
            if request.response_model is enhanced.StockReview:
                ctx = json.loads(request.messages[-1].content.split("\n上次检查失败")[0])
                response.output = response.output.model_copy(
                    update={
                        "summary_supported": False,
                        "blocking_claims": (ctx["proposal"]["candidates"][0]["summary"],),
                    }
                )
            return response

    result = run(runtime, RejectAll())
    assert result["status"] == "FAILED"
    assert "有限纠错" in result["error"]
    assert len(result["candidate_stock_rounds"]["688082.SH"]) == 3
    assert result["model_calls"] == 7
    assert result["charged_tokens"] == 1400
    assert "result_ref" not in result
    assert "candidate_manifest_ref" in result


def test_candidate_enhanced_missing_search_and_same_subject_citation_limits(runtime):
    repo = runtime[0]
    data = screening.candidate_sources.rebuild(repo, source_fixture(repo))
    result = enhanced.search_originals(
        repo,
        data,
        enhanced.SearchRequest(
            tool="EVIDENCE_SEARCH", security_id=data["selected"][0], query="zzzzzzzz"
        ),
    )
    assert result["status"] == "MISSING"
    assert result["facts"] == []
    result = run(runtime, EnhancedPort())
    assert result["status"] == "COMPLETED", result.get("error")
    ctx = repo.value(result["data"]["analysis_context_ref"])
    proposal = screening.CandidateAnalysis.model_validate(
        repo.value(result["data"]["analysis_ref"])
    )
    own, other = proposal.candidates
    changed = proposal.model_copy(
        update={"candidates": (own.model_copy(update={"evidence_ids": other.evidence_ids}), other)}
    )
    with pytest.raises(ValueError, match="own actual source"):
        screening.guard_analysis(changed, ctx)


def test_candidate_researcher_cannot_change_list_methods_or_tool_schema(runtime):
    repo = runtime[0]
    data = screening.candidate_sources.rebuild(repo, source_fixture(repo))
    ctx = screening.context(data, 3, screening.PROMPT_VERSION)
    plan = enhanced.ResearchPlan(
        selections=[
            dict(
                security_id=sid,
                method_rule_ids=["KR-QUALITY-ECONOMICS"],
                hypothesis="仅用于合成契约测试。",
                countercheck="核验反向原文资料。",
            )
            for sid in data["selected"]
        ]
    )
    enhanced.guard_plan(plan, ctx, knowledge())
    with pytest.raises(ValueError):
        enhanced.guard_plan(
            plan.model_copy(update={"selections": tuple(reversed(plan.selections))}),
            ctx,
            knowledge(),
        )
    with pytest.raises(ValueError):
        enhanced.SearchRequest(
            tool="WEB_FETCH", security_id=data["selected"][0], query="https://example.com"
        )
    changed = copy.deepcopy(knowledge())
    changed["rules"] = [r for r in changed["rules"] if r["rule_id"] != "KR-QUALITY-ECONOMICS"]
    with pytest.raises(ValueError):
        enhanced.guard_plan(plan, ctx, changed)
    assert digest(data["selected"]) == digest(ctx["selected"])
