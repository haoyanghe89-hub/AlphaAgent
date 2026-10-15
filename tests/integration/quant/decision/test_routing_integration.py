"""Synthetic roles and source fixtures validate routing publication and immutable bindings."""

import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_decision_api import fixed_sources, setup  # noqa: F401
from test_flow_integration import flows_configured  # noqa: F401
from test_industry_integration import IndustryPort, industry_configured  # noqa: F401
from test_p2_workflow import complete, configured  # noqa: F401

from marketpulse.quant.contracts import digest
from marketpulse.quant.decision import agents, knowledge, medium, routing


class RoutedPort(IndustryPort):
    reject_methods = False
    approve_ready = True

    async def generate(self, request):
        schema = request.response_model
        context = json.loads(request.messages[-1].content.split("\n上次检查失败")[0])
        if schema is routing.RoutingPlan:
            c = context["routing_context"]
            primary = c["allowed_primary"][0]
            output = schema(
                focus_questions=("核查现金回收与经营反证",),
                primary_method_id=primary,
                auxiliary_method_ids=tuple(
                    m["method_id"] for m in c["methods"] if m["method_id"] != primary
                ),
                selection_reason="按本公司原文执行有限经营核查，资金只作分渠道观察。",
            )
            # Reuse the fixture port's response envelope and accounting.
            response = await super().generate(
                request.model_copy(update={"response_model": agents.Plan})
            )
            return SimpleNamespace(**(vars(response) | {"output": output}))
        mapped = {
            agents.RoutedAnalysis: agents.IndustryAnalysis,
            agents.FinalRoutedAnalysis: agents.FinalIndustryAnalysis,
            agents.RoutedReview: agents.IndustryReview,
        }.get(schema, schema)
        response = await super().generate(request.model_copy(update={"response_model": mapped}))
        value = response.output.model_dump(mode="json")
        if schema in {agents.RoutedAnalysis, agents.FinalRoutedAnalysis}:
            plan = context["routing_context"]["plan"]
            eid = next(e["evidence_id"] for e in context["evidence"] if e["kind"] == "disclosure")
            value["method_assessment"] = dict(
                primary_method_id=plan["primary_method_id"],
                auxiliary_method_ids=plan["auxiliary_method_ids"],
                summary="按公司原文核查经营与现金回收，标准化质量仍缺项。",
                evidence_ids=[eid],
            )
            value["policy_assessment"] = dict(
                summary="本次政策原文覆盖不足。",
                company_link="公司适用资格待核查。",
                opposing_risk="缺项不能解释为没有政策风险。",
                next_check="定向核查官方政策与公司业务范围。",
                evidence_ids=[],
            )
        if schema is agents.RoutedReview:
            value["decisions"] += [
                dict(
                    claim_id=k,
                    verdict="REJECT" if self.reject_methods and k == "METHODS" else "APPROVE",
                    reason="合成夹具核查，未验证投资收益。",
                )
                for k in ("METHODS", "POLICY")
            ]
        return SimpleNamespace(**(vars(response) | {"output": schema.model_validate(value)}))


@pytest.fixture
def routed(industry_configured, monkeypatch):  # noqa: F811
    app, base, _ = industry_configured
    port = RoutedPort()
    app.state.decision_model_port = port
    monkeypatch.setattr(
        knowledge,
        "collect",
        lambda repo, client, subject: dict(
            schema_version=knowledge.VERSION,
            subject_id=subject["security_id"],
            responses=[],
            gaps=[dict(note_id="SYNTHETIC", reason="合成夹具不访问真实资料")],
        ),
    )
    return app, base, port


def test_routed_publication_real_use_binding_and_old_record(routed):
    app, _, _ = routed
    with TestClient(app) as c:
        old = complete(c, include_flows=True, include_industry=True)
        assert old["status"] == "COMPLETED", old.get("error")
        result = complete(c, include_flows=True, include_industry=True, include_methods=True)
        assert result["status"] == "COMPLETED", result.get("error")
        data = result["data"]
        assert data["report"]["stage"] == "P4K"
        assert data["method_routing"]["primary_method_id"] == "Q-DISCLOSURE"
        actual = data["method_routing"]["execution_records"]
        assert {r["method_id"] for r in actual} == {"Q-DISCLOSURE", "FLOW-CHECK"}
        assert len(data["forecast"]["method_versions"]) == len(actual)
        assert all("LIMITED_OR_DISABLED" not in m for m in data["forecast"]["method_versions"])
        assert c.get("/api/quant/decision/runs/" + old["run_id"]).json() == old
        repo = app.state.decision_store
        for mutate in ("policy_assessment", "method_assessment"):
            changed = copy.deepcopy(data)
            changed["report"][mutate]["summary"] = "未经复核改写"
            with pytest.raises(ValueError):
                repo.verify_data(changed, digest(changed), result["subject"]["security_id"])
        changed = copy.deepcopy(data)
        changed["method_routing"]["execution_records"][0]["input_evidence_ids"] = []
        with pytest.raises(ValueError):
            repo.verify_data(changed, digest(changed), result["subject"]["security_id"])
        proposal = agents.RoutedAnalysis.model_validate(repo.value(data["analysis_ref"]))
        review = agents.RoutedReview.model_validate(repo.value(data["review_ref"]))
        bad = review.model_copy(
            update={"decisions": tuple(d for d in review.decisions if d.claim_id != "POLICY")}
        )
        assert not medium.publication_ready(proposal, bad)
        with pytest.raises(ValueError):
            agents.guard_review(bad, proposal)
        invalid = proposal.model_copy(
            update={
                "method_assessment": proposal.method_assessment.model_copy(
                    update={"primary_method_id": "M02"}
                )
            }
        )
        with pytest.raises(ValueError):
            agents.guard_analysis(invalid, repo.value(data["analysis_context_ref"]))
        ctx = repo.value(data["analysis_context_ref"])
        ctx["evidence"].append(
            dict(
                evidence_id="EV-POL-TEST",
                kind="industry",
                source_family="POLICY_CN",
                text="合成征求意见稿，并非真实政策",
            )
        )
        ctx["policy_context"] = dict(
            policy=dict(policies=[dict(document_status="CONSULTATION", current_validity="UNKNOWN")])
        )
        invalid = proposal.model_copy(
            update={
                "claims": (
                    proposal.claims[0].model_copy(
                        update={"text": "公司已享受税收优惠。", "support_ids": ("EV-POL-TEST",)}
                    ),
                )
            }
        )
        with pytest.raises(ValueError):
            agents.guard_analysis(invalid, ctx)
        for summary in (
            "资格、现行有效性与公司适用均未核定。",
            "当前适用资格尚未确认。",
            "现行有效性仍待核查。",
        ):
            negative = proposal.model_copy(
                update={
                    "policy_assessment": proposal.policy_assessment.model_copy(
                        update={
                            "summary": summary,
                            "next_check": (
                                "核查公司产品、地区与资格状态，以及政策修订与现行有效性。"
                            ),
                            "evidence_ids": ("EV-POL-TEST",),
                        }
                    )
                }
            )
            agents.guard_policy_prose(negative, ctx)
        for summary in (
            "该政策已经正式生效，但公司资格待核。",
            "该规则当前有效。",
            "公司已享受税收优惠。",
            "核查发现公司已享受税收优惠。",
        ):
            affirmative = proposal.model_copy(
                update={
                    "policy_assessment": proposal.policy_assessment.model_copy(
                        update={"summary": summary, "evidence_ids": ("EV-POL-TEST",)}
                    )
                }
            )
            with pytest.raises(ValueError):
                agents.guard_policy_prose(affirmative, ctx)
        invalid = proposal.model_copy(
            update={
                "policy_assessment": proposal.policy_assessment.model_copy(
                    update={
                        "summary": "该政策已经正式生效并覆盖本公司全部产品。",
                        "evidence_ids": ("EV-POL-TEST",),
                    }
                )
            }
        )
        with pytest.raises(ValueError):
            agents.guard_analysis(invalid, ctx)


def test_routed_report_read_rejects_changed_forecast_ledger(routed):
    from marketpulse.quant.decision.forecasts import ForecastRow

    app, _, _ = routed
    with TestClient(app) as client:
        result = complete(client, include_flows=True, include_industry=True, include_methods=True)
        assert result["status"] == "COMPLETED", result.get("error")
        repo = app.state.decision_store
        with repo.base.sessions.begin() as session:
            row = session.get(ForecastRow, result["forecast_id"])
            row.report_ref = "blob://sha256/" + "0" * 64
        response = client.get("/api/quant/decision/runs/" + result["run_id"])
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "DECISION_INTEGRITY_FAILED"
