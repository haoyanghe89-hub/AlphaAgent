# ruff: noqa: F811
import asyncio
import copy
import json
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from marketpulse.quant.knowledge import advisor_quotes, observations
from marketpulse.quant.knowledge.advisor_api import router
from marketpulse.quant.knowledge.advisor_contracts import AdvisorRequest
from marketpulse.quant.knowledge.advisor_engine import build_answer, sell_quantity, sell_rules
from marketpulse.quant.knowledge.advisor_service import run
from marketpulse.quant.knowledge.advisor_store import AdvisorStore
from marketpulse.quant.live.store import ResearchRunRow

from .test_context import snapshot
from .test_observations import archived_calendar
from .test_research import knowledge, runtime  # noqa: F401
from .test_technicals import TechnicalPort


def request(**changes):
    p = dict(
        entity="000333",
        cost_price="95.17",
        shares=1000,
        sellable_shares=1000,
        total_assets="1000000.37",
        input_origin="SYNTHETIC_ACCEPTANCE",
    )
    p.update(changes.pop("position", {}))
    return AdvisorRequest(request_key=uuid4(), position=p, question="到什么条件可以卖？", **changes)


def report(direction="FAVORABLE"):
    return dict(
        market_date="20261016",
        sources=[],
        knowledge_usage=knowledge(),
        candidates=[
            dict(
                security_id="000333.SZ",
                name="SYNTHETIC",
                selected_route="TREND_SWING",
                metrics={
                    k: dict(value=v)
                    for k, v in dict(close="100", low_20="90", prior_high_20="110").items()
                },
                routes=[
                    dict(
                        route_id=r,
                        title=r,
                        eligible=True,
                        status="WAIT_TRIGGER",
                        invalidation="合成：原路线失效需复核",
                    )
                    for r in ("TREND_SWING", "QUALITY_GROWTH")
                ],
                chart=[],
            )
        ],
        research_opinions=[
            dict(
                security_id="000333.SZ",
                direction=direction,
                summary="合成测试方向",
                method_rule_ids=["KR-TREND-MARKET"],
                claims=[],
                opposing=[],
                invalidation=["合成反证"],
            )
        ],
    )


TRADING = dict(minimum=100, increment=100, source={})


def compute(req=None, data=None, quoted=None):
    data = data or report()
    return build_answer(req or request(), data, quoted or advisor_quotes.fallback(data), TRADING)


@pytest.mark.parametrize(
    "changes",
    [
        dict(cost_price="NaN"),
        dict(total_assets="Infinity"),
        dict(shares=1.1),
        dict(sellable_shares=1001),
        dict(sellable_shares=900, bought_today=101),
    ],
)
def test_private_contract_rejects_invalid_and_unsettled_positions(changes):
    with pytest.raises(ValidationError):
        request(position=changes)


def test_sale_rounding_never_sells_locked_shares_and_keeps_odd_lots_whole():
    assert sell_quantity(Decimal("101.1"), 1000, TRADING) == 200
    assert sell_quantity(2000, 159, TRADING) == 159
    assert sell_quantity(1, 0, TRADING) == 0
    assert sell_quantity(101, 1000, dict(minimum=200, increment=1)) == 200
    assert sell_quantity(201, 1000, dict(minimum=200, increment=1)) == 201


def test_holding_does_not_exit_just_because_new_entry_conditions_are_unmet():
    value = compute(request(position=dict(shares=100, sellable_shares=100)))
    assert value["action"] == "HOLD"
    assert value["suggested_now_shares"] == 0
    assert len(value["knowledge"]) >= 5
    assert not any("已满足" in line for line in value["answer"])


def test_local_risk_and_concentration_budget_and_locked_remainder():
    risk = compute()
    assert risk["facts"]["loss_to_risk"] == "10000.00"
    assert risk["facts"]["reduction_required"] == 506
    assert risk["suggested_now_shares"] == 600
    cap = compute(request(position=dict(total_assets="100000", sellable_shares=200)))
    assert cap["facts"]["reduction_required"] == 951  # Includes assumed exit fees.
    assert cap["suggested_now_shares"] == 200
    assert cap["facts"]["unresolved_reduction"] == 751
    locked = compute(request(position=dict(sellable_shares=0, bought_today=1000)))
    assert locked["suggested_now_shares"] == 0 and locked["warnings"]


def test_cost_is_not_a_price_target_and_explicit_scenarios_are_not_quotes():
    req = request(position=dict(shares=100, sellable_shares=100), scenario_price="85")
    result = compute(req)
    assert result["quote"]["price"] == "100"
    assert result["scenario"]["price"] == "85"
    assert result["scenario"]["shares"] == 100
    assert Decimal(result["scenario"]["estimated_net_proceeds"]) < 8500
    assert result["scenario"]["floating_pnl"] == "-1017.00"
    assert all(g["price"] != "95.17" for g in result["gates"])


def test_quality_route_does_not_mechanically_take_profit_at_twenty_day_high():
    result = compute(
        request(route_id="QUALITY_GROWTH", position=dict(shares=100, sellable_shares=100))
    )
    gate = next(g for g in result["gates"] if g["kind"] == "UPSIDE")
    assert gate["shares"] is None and "不据此机械止盈" in gate["condition"]


def test_followup_scenario_honors_risk_budget_even_below_single_stock_cap():
    result = compute(request(scenario_price="105"))
    assert result["scenario"]["shares"] == 700
    assert "风险" in result["scenario"]["conclusion"]


def test_short_followup_price_uses_local_dialogue_context():
    req = request().model_copy(update=dict(question="那跌到85元呢？"))
    assert compute(req)["scenario"]["price"] == "85"


def test_adjustment_conflict_disables_numeric_price_triggers():
    quoted = dict(
        advisor_quotes.fallback(report()),
        kind="OBSERVED_QUOTE",
        quoted_at="2026-10-19T10:00:00+08:00",
        previous_close="50",
        price="51",
    )
    result = compute(request(position=dict(shares=100, sellable_shares=100)), quoted=quoted)
    assert not any(g["kind"] in ("DEFENSE", "UPSIDE") for g in result["gates"])
    assert result["facts"]["loss_to_risk"] is None
    assert any("口径" in w for w in result["warnings"])


def test_missing_exit_method_or_inconsistent_assets_is_blocked():
    broken = report()
    broken["knowledge_usage"]["rules"] = [
        r for r in broken["knowledge_usage"]["rules"] if r["rule_id"] != "KR-EXPECTATION-EXIT"
    ]
    with pytest.raises(ValueError, match="知识"):
        compute(data=broken)
    with pytest.raises(ValueError, match="超过已填总资产"):
        compute(request(position=dict(total_assets="1000")))


def test_sale_clauses_must_bind_official_archived_rules(monkeypatch):
    from marketpulse.quant.knowledge import advisor_engine

    monkeypatch.setattr(advisor_engine, "rule_text", lambda raw: raw.decode())
    text = (
        "3.1.4投资者买入的证券在交收前不得卖出3.1.5其他3.3.8通过竞价交易买入证券的数量为"
        "100股整数倍。卖出时不足100股应一次性申报卖出3.3.9其他6.7卖出200股，余额应一次性卖出6.8其他"
    )
    data = dict(
        trading_rules=[
            dict(exchange="SSE", source=dict(blob_ref="synthetic"), effective_date="20260706")
        ]
    )
    assert sell_rules(data, lambda _: text.encode(), "688001.SH")["minimum"] == 200
    with pytest.raises(ValueError, match="条款"):
        sell_rules(data, lambda _: text.replace("一次性", "分别").encode(), "600000.SH")


@pytest.fixture
def advisor(runtime, monkeypatch):
    public, packet, settings = runtime
    repo = AdvisorStore(public.base)
    monkeypatch.setattr(
        observations,
        "calendars",
        lambda client, initial, spec: archived_calendar(public, initial, spec),
    )
    # Synthetic exchange clauses are never registered in the real application database.
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.advisor_service.sell_rules", lambda *_: TRADING
    )
    monkeypatch.setattr("marketpulse.quant.knowledge.advisor_store.sell_rules", lambda *_: TRADING)
    options = dict(
        collector=lambda *_: packet, context_collector=lambda *_: snapshot(public, packet)
    )
    return repo, settings, options


def complete(advisor, req=None, port=None):
    repo, settings, options = advisor
    state, _ = repo.create((req or request()).model_dump(mode="json"))
    return asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            research_options=dict(options, port=port or TechnicalPort()),
            quote_collector=lambda client, report: advisor_quotes.fallback(report),
        )
    )


def test_four_roles_use_existing_knowledge_without_private_inputs_and_followups_reuse(advisor):
    port = TechnicalPort()
    req = request(position=dict(total_assets="9876543.21"))
    req = req.model_copy(update=dict(question="PRIVATE-HOLDING-QUESTION-3579 到多少可以卖？"))
    first = complete(advisor, req, port)
    assert first["status"] == "COMPLETED", first.get("error")
    assert port.roles == ["Planner", "Analyst", "Verifier", "Writer"]
    serialized = json.dumps(port.contexts)
    assert "9876543.21" not in serialized and "PRIVATE-HOLDING-QUESTION-3579" not in serialized
    assert first["data"]["privacy"] == dict(
        position_sent_to_model=False, question_sent_to_model=False
    )
    public_hash = first["data"]["research_sha256"]
    second = complete(advisor, request(previous_run_id=first["run_id"], scenario_price="85"))
    assert second["status"] == "COMPLETED", second.get("error")
    assert second["research_run_id"] == first["research_run_id"]
    assert second["data"]["research_sha256"] == public_hash
    assert advisor[0].base.list() == []
    assert [t["run_id"] for t in advisor[0].conversation(second["run_id"])] == [
        first["run_id"],
        second["run_id"],
    ]


def test_immutable_turn_idempotence_parent_route_and_local_write_guard(advisor):
    repo, settings, _ = advisor
    first = complete(advisor)
    assert first["status"] == "COMPLETED", first.get("error")
    assert repo.create(first["request"])[1] is False
    with pytest.raises(ValueError):
        repo.save(dict(first, title="overwrite"))
    with pytest.raises(ValueError, match="请求标识"):
        repo.create(dict(first["request"], question="different"))
    with pytest.raises(ValueError, match="更换股票"):
        repo.create(
            request(previous_run_id=first["run_id"], position=dict(entity="600519")).model_dump(
                mode="json"
            )
        )
    with pytest.raises(ValueError, match="不能在追问中切换"):
        repo.create(
            request(previous_run_id=first["run_id"], route_id="QUALITY_GROWTH").model_dump(
                mode="json"
            )
        )
    app = FastAPI()
    app.include_router(router)
    app.state.research_store, app.state.research_jobs, app.state.settings = repo.base, {}, settings
    with TestClient(app) as client:
        url = "/api/quant/holding-advisor/turns/" + first["run_id"]
        assert client.get(url).status_code == 200
        assert client.get(url + "/export").json()["turns"][0]["result_ref"] == first["result_ref"]
        assert (
            client.post("/api/quant/holding-advisor/turns", json=first["request"]).status_code
            == 202
        )
        assert (
            client.post(url + "/resume", headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
        assert client.get("/api/quant/holding-advisor/turns/RUN-IP-" + "0" * 32).status_code == 404


def test_restart_resume_reuses_public_checkpoints_and_budget(advisor):
    failed = complete(advisor, port=TechnicalPort(fail_writer=True))
    assert failed["status"] == "INTERRUPTED"
    repo, settings, options = advisor
    port = TechnicalPort()
    final = asyncio.run(
        run(
            repo,
            None,
            failed["run_id"],
            Event(),
            settings,
            research_options=dict(options, port=port),
            quote_collector=lambda _, r: advisor_quotes.fallback(r),
        )
    )
    assert final["status"] == "COMPLETED", final.get("error")
    assert port.roles == ["Writer"]
    assert final["research_run_id"] == failed["research_run_id"]


def test_automatic_continuation_keeps_all_guard_feedback_and_same_budget(advisor):
    from marketpulse.quant.knowledge import research_agents as agents

    class AlternatingPort(TechnicalPort):
        def __init__(self):
            super().__init__()
            self.analysis_count = 0
            self.messages = []

        async def generate(self, model_request):
            result = await super().generate(model_request)
            if model_request.response_model is agents.Analysis:
                self.analysis_count += 1
                self.messages.append(model_request.messages[-1].content)
                output = result.output.model_dump(mode="json")
                if self.analysis_count <= 2:
                    for opinion in output["opinions"]:
                        for claim in opinion["claims"] + opinion["opposing"]:
                            removed = (
                                (lambda e: e.startswith("T-"))
                                if self.analysis_count == 1
                                else (lambda e: "CN-HOME" in e)
                            )
                            claim["evidence_ids"] = [
                                e for e in claim["evidence_ids"] if not removed(e)
                            ] or ["F-000333SZ-close"]
                    result.output = agents.Analysis.model_validate(output)
            return result

    port = AlternatingPort()
    result = complete(advisor, port=port)
    assert result["status"] == "COMPLETED", result.get("error")
    assert "技术辅助证据" in port.messages[2]
    assert "政策或资金证据" in port.messages[2]
    assert result["automatic_continuations"] == 1


def test_tampered_local_answer_is_recomputed_and_rejected(advisor):
    repo, _, _ = advisor
    result = complete(advisor)
    assert result["status"] == "COMPLETED", result.get("error")
    from marketpulse.quant.contracts import digest

    changed = copy.deepcopy(result["data"])
    changed["suggested_now_shares"] = 99999999
    with repo.base.sessions.begin() as session:
        row = session.scalar(
            select(ResearchRunRow).where(ResearchRunRow.run_id == result["run_id"])
        )
        payload = dict(row.payload, result_ref=repo.blob(changed), result_sha256=digest(changed))
        row.payload = payload
    with pytest.raises(ValueError, match="本地计算"):
        repo.get(result["run_id"])


def test_dialogue_read_verifies_shared_research_once_per_request(advisor, monkeypatch):
    from marketpulse.quant.knowledge.research_store import InvestmentStore

    first = complete(advisor)
    second = complete(advisor, request(previous_run_id=first["run_id"]))
    calls = []
    original = InvestmentStore.get

    def counted(self, run_id, **kwargs):
        calls.append(run_id)
        return original(self, run_id, **kwargs)

    monkeypatch.setattr(InvestmentStore, "get", counted)
    fresh = AdvisorStore(advisor[0].base)
    assert len(fresh.conversation(second["run_id"])) == 2
    assert calls == [first["research_run_id"]]
