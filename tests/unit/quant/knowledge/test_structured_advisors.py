# ruff: noqa: F811
import asyncio
import copy
import json
from threading import Event

import pytest

from marketpulse.quant.knowledge import research_agents as a
from marketpulse.quant.knowledge import structured_contract as wire
from marketpulse.quant.knowledge.advisor_service import run as holding_run
from marketpulse.quant.knowledge.portfolio_service import run as portfolio_run
from marketpulse.quant.knowledge.research_store import InvestmentStore

from .test_advisor import advisor  # noqa: F401
from .test_advisor import request as holding_request
from .test_portfolio import PortfolioPort, portfolio  # noqa: F401
from .test_portfolio import request as portfolio_request
from .test_research import runtime  # noqa: F401


class StructuredPort(PortfolioPort):
    async def generate(self, request):
        if request.response_model is not wire.WireAnalysis:
            result = await super().generate(request)
            result.model = "deepseek-flash"  # Synthetic transport, pinned tokenizer model identity.
            return result
        result = await super().generate(request.model_copy(update=dict(response_model=a.Analysis)))
        value = result.output.model_dump(mode="json")
        context = json.loads(request.messages[-1].content.split("\n上次检查失败：")[0])
        required = {r["security_id"]: r for r in context["citation_requirements"]}
        for opinion in value["opinions"]:
            ids = [c["claim_id"] for c in opinion["claims"]]
            opinion["direction_supports"] = [
                "S" + str(ids.index(c) + 1) for c in opinion.pop("direction_claim_ids")
            ]
            for c in opinion["claims"] + opinion["opposing"]:
                c.pop("claim_id")
            for i, k in enumerate(("technical", "funding", "policy")):
                facts = required[opinion["security_id"]][k]
                if facts and not any(
                    facts[0] in c["evidence_ids"] for c in opinion["claims"] + opinion["opposing"]
                ):
                    opinion["claims"][i % 2]["evidence_ids"].append(facts[0])
            for c in opinion["claims"] + opinion["opposing"]:
                c["evidence_ids"] = list(dict.fromkeys(c["evidence_ids"]))
            opinion["claims"][0]["text"] = "合成测试：资金与价格资料仍需核查。"
            opinion["opposing"][0]["text"] = "合成测试：政策资格尚未核定，保留反向风险。"
        result.output = wire.WireAnalysis.model_validate(value)
        result.model = "deepseek-flash"
        return result


@pytest.mark.parametrize("kind", ["holding", "portfolio"])
def test_new_structured_advice_is_reviewed_and_replayable_without_writer_call(request, kind):
    repo, settings, options = request.getfixturevalue(
        "advisor" if kind == "holding" else "portfolio"
    )
    settings = copy.copy(settings)
    settings.model = "deepseek-flash"
    definition = (holding_request if kind == "holding" else portfolio_request)(
        structured_research=True
    )
    state, _ = repo.create(definition.model_dump(mode="json"))
    port = StructuredPort()
    result = asyncio.run(
        (holding_run if kind == "holding" else portfolio_run)(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            research_options=dict(options, port=port),
        )
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert "Writer" not in port.roles
    assert "Researcher" not in port.roles
    assert repo.get(state["run_id"])["status"] == "COMPLETED"
    ids = (
        [result["research_run_id"]]
        if kind == "holding"
        else [g["run_id"] for g in result["research_groups"]]
    )
    for rid in ids:
        study = InvestmentStore(repo.base).get(rid)
        assert study["config"]["input_reservation"]["version"] == "deepseek-v4-tokenizer-v1"
        assert study["config"]["budget"]["max_tokens"] == 60000
