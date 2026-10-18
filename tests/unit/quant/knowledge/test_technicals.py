# ruff: noqa: F811
import asyncio
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest

from marketpulse.quant.knowledge import research_agents as agents
from marketpulse.quant.knowledge import technicals as t
from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.research_service import run

from .test_context import ContextPort, snapshot
from .test_features import market_packet
from .test_research import runtime  # noqa: F401


def test_wilder_seed_and_recurrence_are_hand_computable():
    values = [Decimal(i) for i in range(1, 16)]
    result = t.wilder(values)
    assert result[:13] == [None] * 13
    assert result[13] == Decimal("7.5")
    assert result[14] == (Decimal("7.5") * 13 + 15) / 14
    assert t.wilder(values + [None] + values)[15:29] == [None] * 14


def test_linear_price_macd_rsi_atr_and_warmup():
    result = t.compute(market_packet())["stocks"][0]
    points = result["points"]
    assert all(p["macd_dea"] is None for p in points[:33])
    assert Decimal(points[-1]["macd_dif"]) == 7
    assert Decimal(points[-1]["macd_dea"]) == 7
    assert Decimal(points[-1]["macd_histogram"]) == 0
    assert Decimal(points[-1]["rsi_14"]) == 100
    assert Decimal(points[-1]["atr_14"]) == 2


def test_split_is_normalized_and_flat_rsi_stays_undefined():
    packet = market_packet()
    for i, row in enumerate(packet["stocks"][0]["daily"]):
        value = 100 if i < 50 else 50
        row.update(close=value, high=value, low=value)
        packet["stocks"][0]["adjustments"][i]["adj_factor"] = 1 if i < 50 else 2
    m = t.compute(packet)["stocks"][0]["metrics"]
    assert Decimal(m["macd_histogram"]["value"]) == 0
    assert Decimal(m["atr_14"]["value"]) == 0
    assert m["rsi_14"]["value"] is None


def test_missing_sessions_reset_instead_of_shortening_and_bad_ohlc_rejected():
    packet = market_packet()
    packet["stocks"][0]["daily"].pop(-10)
    m = t.compute(packet)["stocks"][0]["metrics"]
    assert all(m[k]["value"] is None for k in ("macd_histogram", "rsi_14", "atr_14"))
    packet = market_packet()
    packet["stocks"][0]["daily"][-1]["low"] = 9999
    with pytest.raises(ValueError):
        t.compute(packet)


class TechnicalPort(ContextPort):
    async def generate(self, request):
        response = await super().generate(request)
        if request.response_model is agents.Analysis:
            context = self.contexts[-1]
            output = response.output.model_dump(mode="json")
            for opinion in output["opinions"]:
                fact = next(
                    f
                    for f in context["evidence"]
                    if f.get("kind") == "TECHNICAL"
                    and f["evidence_id"].startswith("T-" + opinion["security_id"] + "-")
                )
                opinion["opposing"].append(
                    dict(
                        claim_id="CT",
                        text="合成测试：技术动量仅作辅助，震荡仍可能反复。",
                        evidence_ids=[fact["evidence_id"]],
                    )
                )
            response.output = agents.Analysis.model_validate(output)
        return response


def test_v3_uses_bound_technicals_and_preserves_v2_prompts(runtime):
    repo, packet, settings = runtime
    request = InvestmentRequest(
        schema_version="investment-research-v3", request_key=uuid4(), entities=["000333"]
    )
    state, _ = repo.create(request.model_dump(mode="json"))
    result = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=TechnicalPort(),
            collector=lambda *_: packet,
            context_collector=lambda *_: snapshot(repo, packet),
        )
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert result["data"]["technicals"] == t.compute(packet)
    assert result["config"]["prompt_version"] == agents.CHECKS_PROMPT_VERSION
    assert "技术指标仅引用T证据" not in agents.prompts(agents.CONTEXT_PROMPT_VERSION)["Analyst"]
    changed = dict(result, data=dict(result["data"], technicals={}))
    with pytest.raises(ValueError, match="复算"):
        repo.verify(changed)
