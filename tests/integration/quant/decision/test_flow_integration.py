"""Exercise P3 publication and original-to-calculation tamper checks."""

import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_decision_api import fixed_sources, setup  # noqa: F401
from test_p2_workflow import Port, complete, configured  # noqa: F401

from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.decision import agents, flow_sources, medium
from marketpulse.quant.decision.contracts import ResearchRequest
from marketpulse.quant.decision.next_session.calendar import parse_annual

ANNUAL = "2026年部分节假日休市安排 " + " ".join(
    f"{name}：{m}月{d}日至{n}月{e}日休市"
    for name, m, d, n, e in [
        ("元旦", 1, 1, 1, 3),
        ("春节", 2, 16, 2, 20),
        ("清明节", 4, 4, 4, 6),
        ("劳动节", 5, 1, 5, 5),
        ("端午节", 6, 19, 6, 21),
        ("中秋节", 9, 25, 9, 27),
        ("国庆节", 10, 1, 10, 7),
    ]
)


def snapshot(repo, subject):
    annual = repo.raw(ANNUAL.encode())
    rules = repo.raw(b"%PDF-synthetic-rule")
    sessions = [str(s) for s in parse_annual(ANNUAL, 2026)]
    # This fixture deliberately has real calendar-shaped dates but synthetic values.
    calendar = dict(calendar=dict(sessions=sessions, source_refs=[annual, rules]))
    captured = datetime.now(UTC).isoformat()
    days = flow_sources.window_spec(calendar, captured).sessions
    rows = [
        dict(SCODE=subject["code"], DATE=str(d), RZYE=100 + i * 10, RZMRE=30, RZCHE=20)
        for i, d in enumerate(days)
    ]
    raw = repo.blob(dict(result=dict(data=rows)))
    source = dict(
        key="synthetic-margin",
        url="https://example.com/synthetic",
        source_family="EASTMONEY_MARGIN",
        blob_ref=raw,
        sha256=BlobRef.from_uri(raw).sha256,
        fetched_at=captured,
    )
    return dict(
        subject=subject,
        calendar=calendar,
        started_at=captured,
        captured_at=captured,
        responses=[dict(role="margin", source=source)],
        transport_gaps=[dict(channel="vendor", reason="synthetic unavailable")],
    )


class FlowPort(Port):
    reject_flows = False
    approve_ready = False

    async def generate(self, request):
        schema = request.response_model
        mapped = {
            agents.FlowAnalysis: agents.DirectionalAnalysis,
            agents.FinalFlowAnalysis: agents.FinalDirectionalAnalysis,
            agents.FlowReview: agents.DirectionalReview,
        }.get(schema, schema)
        response = await super().generate(request.model_copy(update={"response_model": mapped}))
        result = response.output.model_dump(mode="json")
        if schema in {agents.FlowAnalysis, agents.FinalFlowAnalysis}:
            context = json.loads(request.messages[-1].content.split("\n上次检查失败")[0])
            result["flow_assessments"] = [
                dict(
                    window=n,
                    summary="融资余额增加，仍需观察其他渠道。",
                    persistence="余额增加不能代替全部资金持续性。",
                    breadth="行业成员未核定，广度缺项。",
                    divergence="价格窗口缺项，不判断背离。",
                    alternatives=["融资需求也可能只是短期交易变化。"],
                    evidence_ids=[
                        e["evidence_id"]
                        for e in context["evidence"]
                        if e["evidence_id"] == f"EV-FLOW-{n}-MARGIN_CHANGE"
                    ],
                )
                for n in (5, 20, 60)
            ]
            if self.approve_ready and not result["scenarios"]:
                result["scenarios"] = [
                    dict(
                        scenario_id=k,
                        kind=k,
                        text="若后续披露变化，重新核查经营兑现。",
                        claim_ids=[result["claims"][0]["claim_id"]],
                        conditions=["后续公司披露支持该变化"],
                    )
                    for k in ("BASE", "IMPROVEMENT", "DETERIORATION")
                ]
        elif schema is agents.FlowReview:
            if self.approve_ready:
                result["scenarios_supported"] = result["opinion_supported"] = True
                for decision in result["decisions"]:
                    decision["verdict"] = "APPROVE"
            result["decisions"] += [
                dict(
                    claim_id=f"FLOW-{n}",
                    verdict="REJECT" if self.reject_flows else "APPROVE",
                    reason="合成数据仅验证发布路径。",
                )
                for n in (5, 20, 60)
            ]
        return SimpleNamespace(**(vars(response) | {"output": schema.model_validate(result)}))


@pytest.fixture
def flows_configured(configured, monkeypatch):  # noqa: F811
    app, repo, _ = configured
    port = FlowPort()
    app.state.decision_model_port = port
    monkeypatch.setattr(flow_sources, "rule_text", lambda raw: "每周一至周五")
    monkeypatch.setattr(
        flow_sources, "collect", lambda repo, client, subject, calendar: snapshot(repo, subject)
    )
    return app, repo, port


def test_p3_freezes_reviewed_funding_and_recomputes_from_originals(flows_configured):
    app, _, _ = flows_configured
    with TestClient(app) as client:
        result = complete(client, include_flows=True)
        assert result["status"] == "COMPLETED", result.get("error")
        data = result["data"]
        assert data["report"]["stage"] == "P3"
        assert data["forecast"]["prompt_version"] == agents.FLOW_PROMPT_VERSION
        assert len(data["report"]["flow_analysis"]) == 3
        final_context = repo_context = app.state.decision_store.value(data["analysis_context_ref"])
        if final_context.get("final_reconciliation"):
            cited = {
                i
                for c in final_context["permitted_claims"]
                for i in c["support_ids"] + c["counter_ids"]
            }
            assert all(
                e["evidence_id"] in cited
                or e["evidence_id"].split("-")[-1]
                in {"MARGIN_CHANGE", "SECURITY_RETURN", "VENDOR_NET"}
                for e in repo_context["evidence"]
                if e["source_family"] == "DECISION_FLOW_CALC"
            )
        assert data["flow_report"]["windows"][0]["metrics"][0]["value"] == "50"
        repo = app.state.decision_store
        # New hashes alone cannot authorize a changed source-derived value.
        damaged = copy.deepcopy(data)
        damaged["flow_report"]["windows"][0]["metrics"][0]["value"] = "999"
        with pytest.raises(ValueError, match="sealed originals"):
            flow_sources.verify(repo, damaged)
        damaged = copy.deepcopy(data)
        damaged["report"]["flow_analysis"][0]["summary"] = "替换解释"
        with pytest.raises(ValueError, match="funding explanation"):
            medium.verify_medium(repo, damaged)
        damaged = copy.deepcopy(data)
        context = repo.value(damaged["analysis_context_ref"])
        context["flow_context"]["end_date"] = "2026-01-01"
        damaged["analysis_context_ref"] = repo.blob(context)
        with pytest.raises(ValueError, match="funding model input"):
            medium.verify_medium(repo, damaged)
        damaged = copy.deepcopy(data)
        damaged.pop("flow_protocol")
        with pytest.raises(ValueError, match="channel assessment protocol"):
            medium.verify_medium(repo, damaged)
        eid = "EV-FLOW-5-MARGIN_CHANGE"
        assert (
            client.get(f"/api/quant/decision/runs/{result['run_id']}/evidence/{eid}").status_code
            == 200
        )
        assert (
            client.get(f"/api/quant/decision/runs/{result['run_id']}/export").json()["data"] == data
        )


def test_matched_closing_quotes_keep_original_references(flows_configured, monkeypatch):
    app, _, _ = flows_configured
    repo = app.state.decision_store
    subject = dict(security_id="688012.SH", symbol="sh688012", code="688012")
    packet = snapshot(repo, subject)
    session = str(flow_sources.window_spec(packet["calendar"], packet["captured_at"]).end_date)
    daily = repo.blob(
        dict(data=dict(sh688012=dict(day=[[session, "10", "10", "10", "10", "1", "0", "0", "1"]])))
    )
    refs = []
    for role, family, ref in (
        ("security_daily", "TENCENT", daily),
        ("quote_tencent", "TENCENT", repo.raw(b"synthetic tencent")),
        ("quote_sina", "SINA", repo.raw(b"synthetic sina")),
    ):
        source = dict(
            key=role,
            url="https://example.com/synthetic",
            source_family=family,
            blob_ref=ref,
            sha256=BlobRef.from_uri(ref).sha256,
            fetched_at=packet["captured_at"],
        )
        packet["responses"].append(dict(role=role, source=source))
        if role.startswith("quote_"):
            refs.append(ref)
    monkeypatch.setattr(flow_sources, "quote", lambda *args: dict(close="10", turnover="10000"))
    monkeypatch.setattr(flow_sources, "agree", lambda *args: dict(close="10"))
    checks = flow_sources.rebuild(repo, packet)["checks"]
    assert checks["status"] == "LATEST_QUOTE_MATCHED"
    assert checks["source_refs"] == refs


def test_unapproved_funding_cannot_be_published_or_defaulted(flows_configured):
    app, _, port = flows_configured
    port.reject_flows = True
    with TestClient(app) as client:
        result = complete(client, include_flows=True)
        assert result["status"] == "FAILED"
        assert not result.get("forecast_id")


def test_approved_p3_stops_without_spending_budget_on_future_questions(flows_configured):
    app, _, port = flows_configured
    port.approve_ready = True
    with TestClient(app) as client:
        result = complete(client, include_flows=True)
        assert result["status"] == "COMPLETED", result.get("error")
        assert result["evidence_calls"] == 0
        assert result["model_calls"] == 4
        assert len(result["rounds"]) == 1
        # An open future question is retained, never fabricated as answered.
        assert result["rounds"][0]["proposal"]["gaps"][0]["status"] == "OPEN"


def test_flow_adapter_identity_missing_field_and_stale_latest(flows_configured):
    app, _, _ = flows_configured
    repo = app.state.decision_store
    subject = dict(code="688012", symbol="sh688012", security_id="688012.SH", exchange="SSE")
    raw = snapshot(repo, subject)
    base = flow_sources.rebuild(repo, raw)
    assert base["report"]["windows"][2]["metrics"][0]["value"] == "600"
    source = raw["responses"][0]["source"]
    packet = repo.value(source["blob_ref"])
    packet["result"]["data"] = packet["result"]["data"][:-1]
    source["blob_ref"] = repo.blob(packet)
    source["sha256"] = BlobRef.from_uri(source["blob_ref"]).sha256
    assert flow_sources.rebuild(repo, raw)["report"]["windows"][0]["metrics"][0]["value"] is None
    packet["result"]["data"][0]["SCODE"] = "600000"
    source["blob_ref"] = repo.blob(packet)
    source["sha256"] = BlobRef.from_uri(source["blob_ref"]).sha256
    rebuilt = flow_sources.rebuild(repo, raw)
    assert not rebuilt["coverage"] and rebuilt["gaps"]


def test_funding_flag_cannot_bypass_historical_or_basis_mode():
    from uuid import uuid4

    for kwargs in (
        {},
        {"research_mode": "medium_term", "mode": "historical", "cutoff_at": "2026-01-01T00:00:00Z"},
    ):
        with pytest.raises(ValueError):
            ResearchRequest(
                request_key=uuid4(), entity="X", question="X", include_flows=True, **kwargs
            )


@pytest.mark.asyncio
async def test_invalid_response_archive_and_retry(setup, monkeypatch):  # noqa: F811
    from marketpulse.quant.decision.next_session.agents import DailyRoles

    app, _ = setup
    requests = []
    valid = dict(
        decisions=[
            dict(claim_id=f"FLOW-{n}", verdict="APPROVE", reason="合成复核") for n in (5, 20, 60)
        ],
        scenarios_supported=True,
        opinion_supported=True,
        conclusion_verdict="APPROVE",
        conclusion_reason="合成复核",
    )

    async def create(**kwargs):
        requests.append(kwargs)
        content = (
            '{"decisions":[]},"flows_supported":true}' if len(requests) == 1 else json.dumps(valid)
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
            id="synthetic",
        )

    async def close():
        pass

    fake = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=close
    )
    monkeypatch.setattr(
        "marketpulse.quant.decision.next_session.agents.configure_deepseek", lambda _: fake
    )
    state = dict(
        config=dict(
            budget=dict(max_model_calls=24, max_tokens=60000),
            prompt_version=agents.FLOW_PROMPT_VERSION,
        ),
        model_calls=0,
        charged_tokens=0,
        tokens=0,
        calls=[],
    )
    settings = SimpleNamespace(model="synthetic", model_read_timeout_seconds=5)
    repo = app.state.decision_store
    async with DailyRoles(
        repo, state, settings, lambda: None, lambda: None, prompts=agents.FLOW_PROMPTS
    ) as roles:
        result, _ = await roles.call("Verifier", agents.FlowReview, {})
    assert len(result.decisions) == 3 and len(requests) == 2
    assert state["calls"][0]["status"] == "FAILED"
    assert state["calls"][0]["validation_issues"] == [": json_invalid"]
    assert (
        repo.raw_bytes(state["calls"][0]["raw_model_response_ref"])
        == b'{"decisions":[]},"flows_supported":true}'
    )
    assert any("json_invalid" in message["content"] for message in requests[1]["messages"])
    assert (
        state["charged_tokens"] > state["tokens"]
    )  # Failed output remains conservatively charged.
