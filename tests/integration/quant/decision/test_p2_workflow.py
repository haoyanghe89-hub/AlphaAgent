"""Synthetic role results test the loop, not real prediction value."""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_decision_api import await_terminal, fixed_sources, payload, setup  # noqa: F401

from marketpulse.quant.decision import agents, forecasts, medium
from marketpulse.quant.decision.contracts import ForecastRecord, ResearchRequest
from marketpulse.quant.decision.store import fingerprint


class Port:
    def __init__(self):
        self.calls = []
        self.interrupt_writer = False
        self.approve_incomplete = False
        self.outlook = "UNFAVORABLE"
        self.reject_direction = False
        self.needs_auxiliary = False

    async def generate(self, request):
        context = json.loads(request.messages[-1].content.split("\n上次检查失败")[0])
        schema = request.response_model
        self.calls.append(schema.__name__)
        if schema is agents.Plan:
            output = dict(focus_questions=["利润为什么下降，现金回收怎样"])
        elif schema in {agents.DirectionalAnalysis, agents.FinalDirectionalAnalysis}:
            final = bool(context.get("final_reconciliation"))
            second = final or bool(context.get("previous_review"))
            claim = dict(
                claim_id="C2" if second else "C1",
                claim_type="hypothesis",
                text="公司披露利润下降主要受产品结构影响。"
                if second
                else "原材料上涨可能拖累利润。",
                subject_ids=[context["subject"]["security_id"]],
                horizon="MEDIUM_TERM",
                support_ids=[context["evidence"][-1 if second else 0]["evidence_id"]],
                mechanism="原文否定原材料解释，后续需核查产品结构。"
                if second
                else "成本原因尚需补查。",
                support_level="WEAK",
                supersedes_id="C1" if second else None,
            )
            output = dict(
                claims=[claim],
                gaps=[]
                if second
                else [
                    dict(
                        gap_id="G1",
                        claim_id="C1",
                        need="利润下降原因",
                        materiality="HIGH",
                        allowed_tool="disclosures",
                        arguments_json='{"query":"产品结构 利润 成本"}',
                        expected_benefit="检验原材料解释",
                    )
                ],
                outlook=self.outlook,
                stance="WAIT_FOR_CONFIRMATION",
                summary=("倾向看多" if self.outlook == "FAVORABLE" else "倾向看空")
                + ("，产品结构仍待改善，先看经营兑现。" if second else "，利润变化仍有压力。"),
                direction_reason="虽然现金回收尚有疑问，仍以披露的产品结构变化作为主要取舍依据。"
                if second
                else "虽然可能有恢复机会，利润压力暂时更值得重视。",
                direction_claim_ids=[claim["claim_id"]],
                opposing_reasons=["现金回收还有疑问。"],
                catalysts=["后续经营披露"],
                invalidation_conditions=["经营兑现与披露背离"],
                next_checks=["核查产品结构"],
                scenarios=[
                    dict(
                        scenario_id=k,
                        kind=k,
                        text="若产品结构变化，重新核查经营兑现。",
                        claim_ids=[claim["claim_id"]],
                        conditions=["后续公司披露支持该变化"],
                    )
                    for k in ("BASE", "IMPROVEMENT", "DETERIORATION")
                ]
                if second
                else [],
            )
            if final:
                output["claims"] = copy.deepcopy(context["permitted_claims"])
            elif second and self.needs_auxiliary:
                auxiliary = dict(
                    claim, claim_id="C3", text="研发转化仍需验证。", supersedes_id=None
                )
                output["claims"].append(auxiliary)
                output["gaps"] = [
                    dict(
                        gap_id="G2",
                        claim_id="C3",
                        need="研发转化",
                        materiality="HIGH",
                        allowed_tool="disclosures",
                        arguments_json='{"query":"研发 新产品"}',
                        expected_benefit="检验研发转化",
                    )
                ]
        elif schema is agents.DirectionalReview:
            proposal = context["proposal"]
            second = proposal["claims"][0]["claim_id"] == "C2"
            output = dict(
                decisions=[
                    dict(
                        claim_id=claim["claim_id"],
                        verdict="APPROVE"
                        if claim["claim_id"] != "C3" and (second or self.approve_incomplete)
                        else "NEED_EVIDENCE",
                        reason="新增原文支持结构解释" if second else "需补原文",
                    )
                    for claim in proposal["claims"]
                ],
                scenarios_supported=second,
                opinion_supported=second,
                conclusion_verdict="REJECT" if self.reject_direction else "APPROVE",
                conclusion_reason="取舍仍无支持"
                if self.reject_direction
                else "有限证据下保留反证的取舍",
            )
        else:
            if self.interrupt_writer:
                raise asyncio.CancelledError()
            output = context
        return SimpleNamespace(
            output=schema.model_validate(output),
            provider="synthetic",
            model="synthetic",
            response_id="fixture",
            usage=SimpleNamespace(input_tokens=100, output_tokens=100),
        )


@pytest.fixture
def configured(setup, fixed_sources, monkeypatch):  # noqa: F811
    app, repo = setup
    port = Port()
    app.state.decision_model_port = port
    app.state.settings = SimpleNamespace(
        model="synthetic", deepseek_api_key=None, model_read_timeout_seconds=120
    )
    original_supplement = medium.supplement

    def supplement(repo, data, query, evidence):
        if port.needs_auxiliary and query == "研发 新产品":
            return []
        return original_supplement(repo, data, query, evidence)

    monkeypatch.setattr(medium, "supplement", supplement)
    monkeypatch.setattr(
        medium,
        "load_calendar",
        lambda *args: (_ for _ in ()).throw(ValueError("synthetic calendar unavailable")),
    )
    return app, repo, port


def complete(client, **changes):
    p = payload() | dict(research_mode="medium_term") | changes
    response = client.post("/api/quant/decision/runs", json=p)
    assert response.status_code == 202, response.text
    return await_terminal(client, response.json()["run_id"])


def test_completed_report_has_explicit_reviewed_direction(configured):
    app, _, _ = configured
    with TestClient(app) as client:
        result = complete(client)
        assert result["status"] == "COMPLETED", result.get("error")
        report = result["data"]["report"]
        assert report["opinion"]["outlook"] in {"FAVORABLE", "UNFAVORABLE"}
        prefix = "倾向看多" if report["opinion"]["outlook"] == "FAVORABLE" else "倾向看空"
        assert report["summary"].startswith(prefix)
        assert report["conclusion"]["reason"]
        assert set(report["conclusion"]["claim_ids"]) <= set(
            report["opinion"]["approved_claim_ids"]
        )
        damaged = copy.deepcopy(result["data"])
        damaged["report"]["conclusion"]["reason"] = "替换成未经审核的理由。"
        with pytest.raises(ValueError, match="direction rationale"):
            medium.verify_medium(app.state.decision_store, damaged)
        data = copy.deepcopy(result["data"])
        repo = app.state.decision_store
        context = repo.value(data["analysis_context_ref"])
        context.pop("qualitative_only")
        data["analysis_context_ref"] = repo.blob(context)
        with pytest.raises(ValueError, match="qualitative text guard missing"):
            medium.verify_medium(repo, data)
        for previous in agents.DIRECTION_PROMPT_VERSIONS[:-1]:
            data["forecast"]["prompt_version"] = previous
            medium.verify_medium(repo, data)


def test_real_orchestration_revises_cause_and_atomically_freezes_forecast(configured):
    app, base, port = configured
    with TestClient(app) as client:
        result = complete(client)
        assert result["status"] == "COMPLETED", result.get("error")
        assert len(result["rounds"]) == 2 and result["evidence_calls"] == 1
        assert "原材料" not in result["data"]["report"]["summary"]
        assert "产品结构" in result["data"]["report"]["claims"][0]["text"]
        assert result["model_calls"] == 6
        rid, repo = result["run_id"], app.state.decision_store
        original = forecasts.forecast(repo, rid)
        assert original["outlook"] == "UNFAVORABLE"
        assert (
            client.post(f"/api/quant/decision/runs/{rid}/observe", json={"actual": 99}).status_code
            == 422
        )
        first = client.post(f"/api/quant/decision/runs/{rid}/observe").json()
        again = client.post(f"/api/quant/decision/runs/{rid}/observe").json()
        assert first["observation_id"] == again["observation_id"]
        assert all(
            o["maturity"] == "PENDING" and o["security_return"] is None for o in first["outcomes"]
        )
        port.outlook = "FAVORABLE"
        revised = complete(client, parent_run_id=rid)
        assert revised["status"] == "COMPLETED", revised.get("error")
        chain = client.get(f"/api/quant/decision/runs/{revised['run_id']}/forecast").json()
        assert len(chain["versions"]) == 2 and chain["versions"][0] == original
        assert chain["versions"][1]["outlook"] == "FAVORABLE"
        assert forecasts.forecast(repo, rid) == original
        record = ForecastRecord.model_validate(original)
        assert forecasts.record_forecast(repo, record) == record
        with pytest.raises(ValueError):
            forecasts.record_forecast(repo, record.model_copy(update={"outlook": "FAVORABLE"}))
        # Completed observations/forecast remain isolated from old history.
        assert base.list() == []


def test_budget_zero_remaining_does_not_call_model(configured):
    app, _, port = configured
    repo = app.state.decision_store
    request = ResearchRequest(**(payload() | dict(research_mode="medium_term"))).model_dump(
        mode="json"
    )
    state, _ = repo.create(request, fingerprint())
    state["model_calls"] = request["budget_profile"]["max_model_calls"]
    repo.save(state)
    asyncio.run(medium.run(repo, state["run_id"], Event(), app.state.settings, port=port))
    assert not port.calls
    assert repo.get(state["run_id"])["status"] == "FAILED"
    assert forecasts.forecast(repo, state["run_id"]) is None


def test_approved_limitation_does_not_hide_high_materiality_evidence_gap(configured):
    app, _, port = configured
    port.approve_incomplete = True
    with TestClient(app) as client:
        result = complete(client)
        assert result["status"] == "COMPLETED", result.get("error")
        assert len(result["rounds"]) == 2
        assert result["evidence_calls"] == 1


def test_resume_reuses_completed_roles_and_new_originals(configured):
    app, _, port = configured
    repo = app.state.decision_store
    request = ResearchRequest(**(payload() | dict(research_mode="medium_term"))).model_dump(
        mode="json"
    )
    state, _ = repo.create(request, fingerprint())
    port.interrupt_writer = True
    asyncio.run(medium.run(repo, state["run_id"], Event(), app.state.settings, port=port))
    assert repo.get(state["run_id"])["status"] == "INTERRUPTED"
    counts = port.calls.count("DirectionalAnalysis"), port.calls.count("DirectionalReview")
    state = repo.get(state["run_id"], with_data=False)
    state["status"] = "RUNNING"
    repo.save(state)
    port.interrupt_writer = False
    asyncio.run(medium.run(repo, state["run_id"], Event(), app.state.settings, port=port))
    result = repo.get(state["run_id"])
    assert result["status"] == "COMPLETED", result.get("error")
    assert (
        port.calls.count("DirectionalAnalysis"),
        port.calls.count("DirectionalReview"),
    ) == counts


def test_unapproved_direction_cannot_publish_a_default_side(configured):
    app, _, port = configured
    port.reject_direction = True
    with TestClient(app) as client:
        result = complete(client)
        assert result["status"] == "FAILED"
        assert "明确结论未通过复核" in result["error"]
        assert forecasts.forecast(app.state.decision_store, result["run_id"]) is None


def test_final_reconciliation_removes_unapproved_auxiliary_without_more_retrieval(configured):
    app, _, port = configured
    port.needs_auxiliary = True
    with TestClient(app) as client:
        result = complete(client)
        assert result["status"] == "COMPLETED", result.get("error")
        assert result["evidence_calls"] == 2
        assert len(result["rounds"]) == 2
        assert port.calls.count("FinalDirectionalAnalysis") == 1
        assert result["model_calls"] == 8
        assert result["final_reconciliation"]["review"]["conclusion_verdict"] == "APPROVE"
        assert [c["claim_id"] for c in result["data"]["report"]["claims"]] == ["C2"]
        assert result["data"]["report"]["conclusion"]["claim_ids"] == ["C2"]
        medium.verify_medium(app.state.decision_store, result["data"])


def test_resume_reuses_final_reconciliation_and_rejects_changed_permitted_claims(configured):
    app, _, port = configured
    repo = app.state.decision_store
    request = ResearchRequest(**(payload() | dict(research_mode="medium_term"))).model_dump(
        mode="json"
    )
    state, _ = repo.create(request, fingerprint())
    port.needs_auxiliary = port.interrupt_writer = True
    asyncio.run(medium.run(repo, state["run_id"], Event(), app.state.settings, port=port))
    assert repo.get(state["run_id"])["status"] == "INTERRUPTED"
    counts = list(port.calls)
    state = repo.get(state["run_id"], with_data=False)
    state["status"] = "RUNNING"
    repo.save(state)
    port.interrupt_writer = False
    asyncio.run(medium.run(repo, state["run_id"], Event(), app.state.settings, port=port))
    result = repo.get(state["run_id"])
    assert result["status"] == "COMPLETED", result.get("error")
    assert port.calls[:-1] == counts
    data = copy.deepcopy(result["data"])
    context = repo.value(data["analysis_context_ref"])
    context["permitted_claims"][0]["text"] = "伪造通过的解释。"
    data["analysis_context_ref"] = repo.blob(context)
    with pytest.raises(ValueError, match="approved claims"):
        medium.verify_medium(repo, data)


def test_legacy_undetermined_report_still_replays_without_changing_its_opinion(configured):
    from marketpulse.quant.contracts import digest
    from marketpulse.quant.decision.contracts import EvidenceBundle

    app, _, _ = configured
    with TestClient(app) as client:
        result = complete(client)
        repo = app.state.decision_store
        data = copy.deepcopy(result["data"])
        raw = repo.value(data["analysis_ref"])
        raw.pop("direction_reason")
        raw.pop("direction_claim_ids")
        raw.update(outlook="UNDETERMINED", summary="旧版资料尚不能确定未来方向。")
        proposal = agents.Analysis.model_validate(raw)
        raw_review = repo.value(data["review_ref"])
        raw_review.pop("conclusion_verdict")
        raw_review.pop("conclusion_reason")
        review = agents.Review.model_validate(raw_review)
        verified, opinion, summary = medium.approved_result(
            proposal, review, EvidenceBundle.model_validate(data["bundle"]), 3
        )
        data.update(
            analysis_ref=repo.blob(raw),
            review_ref=repo.blob(raw_review),
            verified_research=verified.model_dump(mode="json"),
            writer_ref=repo.blob(
                dict(
                    summary=summary, approved_hash=digest(medium.approved_payload(summary, opinion))
                )
            ),
        )
        data["report"].pop("conclusion")
        data["report"].update(summary=summary, opinion=opinion.model_dump(mode="json"))
        data["forecast"].update(prompt_version=agents.LEGACY_PROMPT_VERSION, outlook="UNDETERMINED")
        medium.verify_medium(repo, data)
        assert data["report"]["opinion"]["outlook"] == "UNDETERMINED"
        data["report"]["conclusion"] = dict(reason="把旧记录改成看多。")
        with pytest.raises(ValueError, match="direction rationale"):
            medium.verify_medium(repo, data)


def test_original_ledger_damage_is_visible_not_silently_rebuilt(configured):
    app, _, _ = configured
    with TestClient(app) as client:
        result = complete(client)
        with app.state.decision_store.base.sessions.begin() as session:
            row = session.get(forecasts.ForecastRow, result["forecast_id"])
            row.payload = dict(row.payload, outlook="FAVORABLE")
        assert (
            client.get(f"/api/quant/decision/runs/{result['run_id']}/forecast").status_code == 409
        )
        assert (
            client.post(f"/api/quant/decision/runs/{result['run_id']}/observe").status_code == 409
        )


def test_mature_missing_and_pending_never_become_zero():
    now = datetime.now(UTC)
    record = SimpleNamespace(forecast_id="synthetic")
    p = dict(
        window=20, observed_at=now.isoformat(), due_session=str((now + timedelta(days=1)).date())
    )
    assert forecasts.observe_forecast(record, p).maturity == "PENDING"
    p["due_session"] = str((now - timedelta(days=1)).date())
    value = forecasts.observe_forecast(record, p)
    assert value.maturity == "UNAVAILABLE" and value.security_return is None
