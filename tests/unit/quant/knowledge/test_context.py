# ruff: noqa: F811
# Imported pytest fixtures intentionally share names with test parameters.
import asyncio
import copy
import json
from datetime import datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketpulse.quant.knowledge import context as c
from marketpulse.quant.knowledge import context_policies as p
from marketpulse.quant.knowledge import research_agents as agents
from marketpulse.quant.knowledge.research_api import router
from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.research_service import run

from .test_research import SyntheticPort, runtime  # noqa: F401

STAMP = "2026-10-16T10:00:00+00:00"


def table(rows):
    fields = list(rows[0]) if rows else ["ts_code"]
    return dict(code=0, data=dict(fields=fields, items=[[r[k] for k in fields] for r in rows]))


def source(repo, raw, kind, sid="000333.SZ"):
    if not isinstance(raw, bytes):
        raw = json.dumps(raw).encode()
    ref = repo.raw(raw)
    return dict(
        blob_ref=ref,
        sha256=ref.rsplit("/", 1)[1],
        fetched_at=STAMP,
        key=kind,
        url=c.QQ
        + "?code=sz000333&type=historyFundFlow%2CtodayFundFlow%2CtodayFundTrend&klineNeedDay=20"
        if kind == "minute"
        else p.SOURCES["CN-HOME-2026"]["url"]
        if kind == "policy"
        else "https://api.tushare.pro/",
        source_family="TENCENT"
        if kind == "minute"
        else "POLICY_CN"
        if kind == "policy"
        else "TUSHARE",
        api_name="stock_company" if kind == "business" else "moneyflow",
        parameters=dict(ts_code=sid),
    )


def home_html(state=""):
    spec = p.SOURCES["CN-HOME-2026"]
    return (
        f'<title>合成测试</title><div class="content_head">{spec["number"]} 2025-12-31</div>'
        f'<div class="ctitle">{spec["title"]}</div><div class="gsj_htmlcon_bot">{state}'
        "一、明确享受补贴品类、范围和标准 2026年1月1日起。"
        + "合成夹具：冰箱空调政策条件需核对，不证明公司资格。" * 18
        + "四、优化政策实施方式 "
        + "合成夹具：经营主体资格名单另核。" * 15
        + "</div>"
    ).encode()


def snapshot(repo, packet):
    sid = packet["stocks"][0]["ts_code"]
    sessions = sorted(r["trade_date"] for r in packet["stocks"][0]["daily"])[-20:]
    rows = [
        dict(
            ts_code=sid,
            trade_date=d,
            buy_lg_amount="2",
            sell_lg_amount="1",
            buy_elg_amount="0",
            sell_elg_amount="2",
        )
        for d in sessions
    ]
    entries = [
        dict(
            kind="business",
            source=source(
                repo,
                table(
                    [
                        dict(
                            ts_code=sid,
                            main_business="合成测试：生产空调、冰箱，不是真实公司资料。",
                        )
                    ]
                ),
                "business",
            ),
        ),
        dict(kind="daily", source=source(repo, table(rows), "daily")),
        dict(kind="policy", policy_id="CN-HOME-2026", source=source(repo, home_html(), "policy")),
    ]
    return dict(
        version=c.VERSION,
        market_date=packet["market_date"],
        captured_at=STAMP,
        entries=[dict(security_id=sid, entries=entries)],
        gaps=[],
    )


def test_daily_windows_require_every_declared_session_and_convert_units():
    sessions = ["202610" + str(i).zfill(2) for i in range(1, 21)]
    rows = [
        dict(
            ts_code="000333.SZ",
            trade_date=d,
            buy_lg_amount="2",
            sell_lg_amount="1",
            buy_elg_amount="0",
            sell_elg_amount="2",
        )
        for d in sessions
    ]
    value = c.daily(rows, "000333.SZ", sessions)
    assert [w["value"] for w in value["windows"]] == ["-50000", "-200000"]
    value = c.daily(rows[:-1], "000333.SZ", sessions)
    assert all(w["value"] is None for w in value["windows"])
    for bad in [
        rows + [rows[0]],
        [dict(rows[0], ts_code="600000.SH")],
        [dict(rows[0], trade_date="20990101")],
        [dict(rows[0], buy_lg_amount="NaN")],
    ]:
        with pytest.raises(ValueError):
            c.daily(bad, "000333.SZ", sessions)


@pytest.mark.parametrize(
    "state,expected",
    [
        ("", "WITHIN_DECLARED_WINDOW"),
        ("本文件已废止。", "REPEALED"),
        ("公开征求意见，尚不是正式规则。", "CONSULTATION"),
    ],
)
def test_policy_lifecycle_from_original_and_future_rejection(state, expected):
    value = p.parse(home_html(state), "CN-HOME-2026", STAMP, "home")
    assert value["temporal_status"] == expected
    assert (
        value["qualification_status"] == "UNKNOWN" and value["company_link_status"] == "HYPOTHESIS"
    )
    assert p.temporal("2030-01-01", None, STAMP) == "NOT_YET_EFFECTIVE"
    assert p.temporal(None, "2025-12-31", STAMP) == "EXPIRED"
    with pytest.raises(ValueError, match="未来"):
        p.parse(home_html(), "CN-HOME-2026", "2025-01-01T10:00:00+00:00", "home")


def test_overseas_selection_requires_related_business_not_foreign_policy_for_every_stock():
    assert "US-EAR-74423" in p.selected(dict(industry="半导体", main_business="半导体设备制造"))
    assert "US-EAR-74423" not in p.selected(dict(industry="家用电器", main_business="空调"))
    assert p.selected(dict(industry="测试未知行业", main_business="测试")) == []


def test_rebuild_ignores_unverified_identity_and_deduplicates_policy(runtime):
    repo, packet, _ = runtime
    snap = snapshot(repo, packet)
    result = c.rebuild(repo, packet, snap)
    assert len(result["stocks"][0]["policies"]) == 1
    assert result["stocks"][0]["daily"] is not None
    old = dict(snap, version="investment-context-v1")
    assert c.rebuild(repo, packet, old)["stocks"][0]["daily"] is None
    altered = copy.deepcopy(snap)
    altered["entries"][0]["entries"][1]["source"]["parameters"]["ts_code"] = "600000.SH"
    result = c.rebuild(repo, packet, altered)
    assert result["stocks"][0]["daily"] is None and result["gaps"]
    altered = copy.deepcopy(snap)
    altered["entries"][0]["entries"].append(altered["entries"][0]["entries"][-1])
    with pytest.raises(ValueError, match="重复"):
        c.rebuild(repo, packet, altered)
    altered = copy.deepcopy(snap)
    altered["entries"][0]["entries"][0]["source"]["fetched_at"] = "2099-01-01T10:00:00+00:00"
    with pytest.raises(ValueError, match="截止"):
        c.rebuild(repo, packet, altered)


def test_minute_gaps_never_use_daily_rows_or_invent_history(runtime):
    repo, packet, _ = runtime
    snap = snapshot(repo, packet)
    start = datetime.fromisoformat("2026-10-16T14:40:00+08:00")
    points = [
        dict(
            time=(start + timedelta(minutes=i)).strftime("%Y%m%d%H%M"),
            MainNetInflow=str(i * 100),
            BigNetInflow=str(i * 60),
            SuperNetInflow=str(i * 40),
            MainInflow=str(i * 200),
            MainOutflow=str(i * 100),
            Price="10",
        )
        for i in range(21)
    ]
    points.pop(-3)
    minute = dict(
        code=0,
        data=dict(
            todayFundFlow=dict(stockCode="sz000333"),
            todayFundTrend=dict(stockCode="sz000333", minList=points),
        ),
    )
    entry = dict(kind="minute", source=source(repo, minute, "minute"))
    snap["entries"][0]["entries"].append(entry)
    view = c.rebuild(repo, packet, snap)["stocks"][0]["minute"]
    assert view["granularity"] == "minute" and all(w["main"] is None for w in view["windows"])
    entry["source"] = source(
        repo, table([dict(ts_code="000333.SZ", trade_date="20261016")]), "minute"
    )
    result = c.rebuild(repo, packet, snap)
    assert result["stocks"][0]["minute"] is None


class ContextPort(SyntheticPort):
    async def generate(self, request):
        response = await super().generate(request)
        if request.response_model is agents.Analysis:
            context = self.contexts[-1]
            raw = response.output.model_dump(mode="json")
            for o in raw["opinions"]:
                flow = next(f for f in context["evidence"] if f.get("kind") == "DAILY_FLOW")
                policy = next(f for f in context["evidence"] if f.get("kind") == "POLICY_CLAUSE")
                o["claims"][0].update(
                    text="合成测试：供应商日频资金代理净额为负，需保留反证。",
                    evidence_ids=[flow["evidence_id"]],
                )
                o["claims"][1].update(
                    text="合成测试：政策可能影响产品需求，但公司资格待核。",
                    evidence_ids=[policy["evidence_id"]],
                )
            response.output = agents.Analysis.model_validate(raw)
        return response


def test_v2_real_pipeline_binds_original_context_and_retains_old_prompt_versions(runtime):
    repo, packet, settings = runtime
    request = InvestmentRequest(
        schema_version="investment-research-v2", request_key=uuid4(), entities=["000333"]
    )
    state, _ = repo.create(request.model_dump(mode="json"))
    result = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=ContextPort(fail_writer=True),
            collector=lambda *_: packet,
            context_collector=lambda *_: snapshot(repo, packet),
        )
    )
    assert result["status"] == "INTERRUPTED"
    port = ContextPort()

    def forbidden(*_):
        raise AssertionError("must reuse frozen context and inputs")

    result = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=port,
            collector=forbidden,
            context_collector=forbidden,
        )
    )
    assert port.roles == ["Writer"]
    assert result["status"] == "COMPLETED", result.get("error")
    assert result["config"]["prompt_version"] == agents.CONTEXT_PROMPT_VERSION
    assert (
        result["data"]["external_context"]["stocks"][0]["daily"]["windows"][0]["value"] == "-50000"
    )
    assert result["data"]["privacy"]["profile_sent_to_model"] is False
    review = repo.value(result["model_steps"][result["approved_review_step"]]["context_ref"])
    full = repo.value(result["public_context_ref"])
    original = {e["evidence_id"]: e for e in full["evidence"]}
    for e in review["evidence"]:
        assert review["windows"][e["window"]] == original[e["evidence_id"]]["window"]
        assert e.get("value") == original[e["evidence_id"]].get("value")
        assert e.get("policy_state") == original[e["evidence_id"]].get("policy_state")
    assert agents.prompts(agents.PROMPT_VERSION) == agents.PROMPTS
    changed = copy.deepcopy(result)
    changed["data"]["external_context"]["stocks"][0]["daily"]["windows"][0]["value"] = "1"
    with pytest.raises(ValueError, match="复算"):
        c.verify(repo, changed)


def test_v2_api_exports_policy_and_funding_originals_for_offline_rebuild(runtime, tmp_path):
    from scripts.verify_investment_research_export import verify

    repo, packet, settings = runtime
    app = FastAPI()
    app.include_router(router)
    app.state.research_store = repo.base
    app.state.knowledge_rules = None
    app.state.settings = settings
    app.state.research_jobs = {}
    app.state.investment_research_port = ContextPort()
    app.state.investment_research_collector = lambda *_: packet
    app.state.investment_context_collector = lambda *_: snapshot(repo, packet)
    definition = InvestmentRequest(
        schema_version="investment-research-v2", request_key=uuid4(), entities=["000333"]
    ).model_dump(mode="json")
    with TestClient(app) as client:
        response = client.post("/api/quant/investment-research/runs", json=definition)
        url = "/api/quant/investment-research/runs/" + response.json()["run_id"]
        import time

        for _ in range(150):
            response = client.get(url)
            if response.json()["status"] != "RUNNING":
                break
            time.sleep(0.01)
        assert response.json()["status"] == "COMPLETED", response.json().get("error")
        data = response.json()["data"]
        ref = data["external_context"]["stocks"][0]["policies"][0]["source"]["blob_ref"]
        assert client.get(url + "/sources/" + ref.rsplit("/", 1)[1]).status_code == 200
        archive = client.get(url + "/export")
        assert archive.status_code == 200
    path = tmp_path / "v2.zip"
    path.write_bytes(archive.content)
    assert verify(path)["approved_content_verified"]


def test_invalid_provider_output_keeps_observed_usage_and_bounded_correction(runtime):
    from marketpulse.investigation.recording.errors import InvalidProviderResponseError

    class CorrectingPort(ContextPort):
        failed = False

        async def generate(self, request):
            if request.response_model is agents.Analysis and not self.failed:
                self.failed = True
                raise InvalidProviderResponseError(
                    "synthetic invalid schema", input_tokens=100, output_tokens=50
                )
            return await super().generate(request)

    repo, packet, settings = runtime
    request = InvestmentRequest(
        schema_version="investment-research-v2", request_key=uuid4(), entities=["000333"]
    )
    state, _ = repo.create(request.model_dump(mode="json"))
    result = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=CorrectingPort(),
            collector=lambda *_: packet,
            context_collector=lambda *_: snapshot(repo, packet),
        )
    )
    assert result["status"] == "COMPLETED", result.get("error")
    failed = next(call for call in result["calls"] if call["status"] == "FAILED")
    assert failed["charged_tokens"] == 150 and failed["reserved_tokens"] > 150
    assert result["charged_tokens"] == 950 and result["model_calls"] == 5


def test_adapter_preserves_usage_when_schema_rejects_extra_fields():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from marketpulse.investigation.adapters.model import OpenAICompatibleModelAdapter
    from marketpulse.investigation.ports.external import ModelRequest
    from marketpulse.investigation.recording.errors import InvalidProviderResponseError

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"opinions":[],"extra":"SYNTHETIC"}'),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=35),
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=response)))
    )
    port = OpenAICompatibleModelAdapter(client, provider="SYNTHETIC", default_model="SYNTHETIC")
    request = ModelRequest(
        messages=[],
        response_model=agents.Analysis,
        response_schema_version="investment-research-v2",
        prompt_version=agents.CONTEXT_PROMPT_VERSION,
    )
    with pytest.raises(InvalidProviderResponseError) as error:
        asyncio.run(port.generate(request))
    assert error.value.input_tokens == 120 and error.value.output_tokens == 35
