"""Synthetic source/model fixtures test P4 publication and tamper resistance only."""

import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_decision_api import fixed_sources, setup  # noqa: F401
from test_flow_integration import FlowPort, flows_configured  # noqa: F401
from test_p2_workflow import complete, configured  # noqa: F401

from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.contracts import digest
from marketpulse.quant.decision import agents, industry_sources, medium


class IndustryPort(FlowPort):
    approve_ready = True
    reject_industry = False

    async def generate(self, request):
        schema = request.response_model
        mapped = {
            agents.IndustryAnalysis: agents.FlowAnalysis,
            agents.FinalIndustryAnalysis: agents.FinalFlowAnalysis,
            agents.IndustryReview: agents.FlowReview,
        }.get(schema, schema)
        response = await super().generate(request.model_copy(update={"response_model": mapped}))
        value = response.output.model_dump(mode="json")
        if schema in {agents.IndustryAnalysis, agents.FinalIndustryAnalysis}:
            value["next_checks"] = ["核查行业资本开支与验收"]
            for scenario in value["scenarios"]:
                scenario["conditions"] = ["若行业需求转为可核实验收"]
            context = json.loads(request.messages[-1].content.split("\n上次检查失败")[0])
            eid = next(
                e["evidence_id"] for e in context["evidence"] if e["source_family"] == "WSTS"
            )
            value["industry_assessment"] = dict(
                summary="行业背景仍需核查公司连接。",
                company_link="若需求转为匹配设备的采购，才可能受益。",
                funding_crosscheck="融资观察不证明行业订单。",
                next_check="核查资本支出与验收。",
                evidence_ids=[eid],
            )
            value["attribution_assessment"] = dict(
                summary="不能区分公司与行业的独立贡献。",
                main_explanation="共同市场变化可能影响价格。",
                alternatives=["除权息仍待核查。", "其他披露也可能影响观察。"],
                next_check="核查公告时点与业务兑现。",
            )
        elif schema is agents.IndustryReview:
            value["decisions"] += [
                dict(
                    claim_id=k,
                    verdict="REJECT" if self.reject_industry else "APPROVE",
                    reason="合成夹具验证复核门槛。",
                )
                for k in ("INDUSTRY", "ATTRIBUTION")
            ]
        return SimpleNamespace(**(vars(response) | {"output": schema.model_validate(value)}))


@pytest.fixture
def industry_configured(flows_configured, monkeypatch):  # noqa: F811
    app, base, _ = flows_configured
    port = IndustryPort()
    app.state.decision_model_port = port

    def collect(repo, client, subject, calendar):
        stamp = datetime.now(UTC).isoformat()
        ref = repo.raw(
            b"<h1>Synthetic only</h1><p>Statistic: Synthetic product sales. "
            b"Not actual market data.</p><p>Outlook: Synthetic forecast version.</p>"
        )
        source = dict(
            key="synthetic-industry",
            url="https://www.wsts.org/synthetic",
            source_family="WSTS",
            blob_ref=ref,
            sha256=BlobRef.from_uri(ref).sha256,
            fetched_at=stamp,
        )
        return dict(
            subject=subject,
            started_at=stamp,
            captured_at=stamp,
            responses=[dict(role="WSTS_article_0", source=source)],
            manufacturer_document=None,
            coverage=[],
            gaps=[],
        )

    monkeypatch.setattr(industry_sources, "collect", collect)
    return app, base, port


def test_p4_freezes_model_explanations_and_rebuilds_originals(industry_configured):
    app, _, _ = industry_configured
    with TestClient(app) as c:
        result = complete(c, include_flows=True, include_industry=True)
        assert result["status"] == "COMPLETED", result.get("error")
        data = result["data"]
        repo = app.state.decision_store
        assert data["report"]["stage"] == "P4"
        assert data["forecast"]["prompt_version"] == agents.INDUSTRY_PROMPT_VERSION
        assert all(e["status"] == "HYPOTHESIS" for e in data["industry_report"]["chain"])
        assert data["attribution_report"]["causal_identification"] == "NOT_IDENTIFIED"
        medium.verify_medium(repo, data)
        bad = repo.value(data["analysis_ref"])
        bad["gaps"][0]["allowed_tool"] = "industry"
        bad["scenarios"][0]["text"] = "若资本支出超预期则重新研究。"
        with pytest.raises(ValueError, match="补查.*预期基准"):
            agents.guard_analysis(
                agents.IndustryAnalysis.model_validate(bad),
                repo.value(data["analysis_context_ref"]),
            )
        risk = repo.value(data["analysis_ref"])
        risk["claims"][0]["text"] = "公司披露下游扩产存在不及预期风险。"
        context = repo.value(data["analysis_context_ref"])
        with pytest.raises(ValueError, match="预期基准"):
            agents.guard_analysis(agents.IndustryAnalysis.model_validate(risk), context)
        disclosure = next(e for e in context["evidence"] if e["kind"] == "disclosure")
        disclosure["text"] += "（合成夹具）公司披露下游扩产不及预期风险。"
        agents.guard_analysis(agents.IndustryAnalysis.model_validate(risk), context)
        qualified = repo.value(data["analysis_ref"])
        qualified["industry_assessment"]["summary"] = "同一底层统计不能独立印证公司受益。"
        agents.guard_analysis(agents.IndustryAnalysis.model_validate(qualified), context)
        qualified["industry_assessment"]["summary"] = "同一底层统计，非独立印证。"
        agents.guard_analysis(agents.IndustryAnalysis.model_validate(qualified), context)
        qualified["industry_assessment"]["summary"] = "同一底层统计可以独立印证公司受益。"
        with pytest.raises(ValueError, match="causal contributions"):
            agents.guard_analysis(agents.IndustryAnalysis.model_validate(qualified), context)
        risk["scenarios"][0]["text"] = "若下游扩产不及预期，设备需求可能减弱。"
        agents.guard_analysis(agents.IndustryAnalysis.model_validate(risk), context)
        risk["claims"][0]["text"] = "公司实际扩产已经不及预期。"
        with pytest.raises(ValueError, match="预期基准"):
            agents.guard_analysis(agents.IndustryAnalysis.model_validate(risk), context)
        additional = copy.deepcopy(data)
        company_item = next(e for e in data["bundle"]["items"] if e["kind"] == "disclosure")
        additional["bundle"]["items"].append(dict(company_item, evidence_id="EV-EXTRA-SUPPLEMENT"))
        # A later loop read cannot silently rewrite the fixed industry observation.
        industry_sources.verify(repo, additional)
        eid = data["industry_report"]["facts"][0]["evidence_id"]
        assert (
            c.get(f"/api/quant/decision/runs/{result['run_id']}/evidence/{eid}").status_code == 200
        )
        damaged = copy.deepcopy(data)
        damaged["attribution_report"]["windows"][0]["industry_return"] = ".9"
        with pytest.raises(ValueError, match="sealed originals"):
            industry_sources.verify(repo, damaged)
        damaged = copy.deepcopy(data)
        damaged["report"]["industry_analysis"]["summary"] = "改写解释"
        with pytest.raises(ValueError, match="industry explanation"):
            medium.verify_medium(repo, damaged)
        damaged = copy.deepcopy(data)
        damaged["bundle"]["items"][-1]["text"] = "伪造摘录"
        damaged["evidence"] = damaged["bundle"]["items"]
        with pytest.raises(ValueError):
            repo.verify_data(damaged, digest(damaged), result["subject"]["security_id"])
        assert (
            c.post("/api/quant/decision/runs", json=result["request"]).json()["run_id"]
            == result["run_id"]
        )


def test_unapproved_industry_cannot_publish(industry_configured):
    app, _, port = industry_configured
    port.reject_industry = True
    with TestClient(app) as c:
        result = complete(c, include_flows=True, include_industry=True)
        assert result["status"] == "FAILED" and not result.get("forecast_id")
