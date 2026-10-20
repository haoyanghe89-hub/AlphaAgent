# ruff: noqa: F811
"""Synthetic semantic contracts; no network or investment-performance claims."""

import asyncio
import copy
import json
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketpulse.quant.contracts import canonical
from marketpulse.quant.knowledge.question_understanding import (
    QuestionStore,
    Understanding,
    bound_understanding,
    collect_registry,
    dialogue,
    guard,
    projection,
    resolved,
    run,
)
from marketpulse.quant.knowledge.research_contracts import InvestmentRequest

from .test_advisor import advisor, complete, compute  # noqa: F401
from .test_advisor import request as holding_request
from .test_portfolio import request as account_request
from .test_research import runtime  # noqa: F401

REGISTRY = [
    dict(entity="美的集团", name="美的集团", code="000333.SZ"),
    dict(entity="格力电器", name="格力电器", code="000651.SZ"),
]


class SemanticPort:
    def __init__(self, targets=("S1",), focuses=("INVALIDATION",), mode="holding", inherited=False):
        self.requests = []
        self.output = Understanding(
            tasks=[dict(mode=mode, targets=targets, horizon="SWING", focuses=focuses)],
            inherited=inherited,
        )

    async def generate(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            output=self.output,
            usage=SimpleNamespace(input_tokens=100, output_tokens=100),
            provider="SYNTHETIC",
            model="SYNTHETIC",
            response_id="synthetic-semantic",
        )


def execute(
    runtime,
    port,
    *,
    previous=None,
    question="先别说补仓，解释什么变化会推翻你刚才的判断？",
    registry=None,
):
    repo = QuestionStore(runtime[0].base)
    state, _ = repo.create(
        dict(
            request_key=str(uuid4()),
            question=question,
            channel=port.output.tasks[0].mode
            if port.output.tasks[0].mode in {"holding", "portfolio"}
            else "workbench",
            previous_run_id=previous,
        ),
        registry=registry or REGISTRY,
    )
    return repo, asyncio.run(run(repo, state["run_id"], Event(), runtime[2], port=port))


def test_positive_projection_retains_negation_conditions_but_no_private_prose_or_values():
    question = (
        "先别说补仓，解释什么变化会推翻你刚才对美的集团的判断？"
        "成本83.719股数1379现金987654.31；密钥PRIVATE_MARKER_X；"
        "姓名张三；如果只是回撤但现金回收没变呢？"
    )
    public = projection(question, REGISTRY)
    for value in ("83.719", "1379", "987654.31", "PRIVATE_MARKER_X", "张三", "美的集团", "000333"):
        assert value not in public
    for value in (
        "先别",
        "补仓",
        "推翻",
        "刚才",
        "S1",
        "如果",
        "只是",
        "回撤",
        "但",
        "现金回收",
        "没变",
    ):
        assert value in public
    assert "第二只" in projection("那第二只呢？", REGISTRY)
    assert "[数值]" in projection("成本八十三点七元，仓位五成", REGISTRY)
    assert "未来三个月" in projection("美的集团未来3个月怎么看", REGISTRY)
    assert projection("先研究000333，再看000651", REGISTRY) == "先研究S1，再看S2"
    assert "S1" not in projection("现金100000333元", REGISTRY)


def test_private_fields_redact_even_when_the_secret_is_a_known_financial_word():
    question = "密码MACD，账号现金流，姓名趋势，密钥sk-MACD-private；解释美的集团的RSI指标。"
    public = projection(question, REGISTRY)
    assert "MACD" not in public and "现金流" not in public and "趋势" not in public
    assert "S1的RSI指标" in public
    assert "MACD" not in projection("sk-MACD-private 怎么看", REGISTRY)
    # Previously issued records retain their exact projection policy.
    assert "MACD" in projection(question, REGISTRY, policy="financial-projection-v1")


def test_semantic_guard_rejects_new_stocks_modes_and_multiple_single_stock_targets():
    context = dict(channel="holding", slots=["S1"])
    for changes in (dict(targets=["S2"]), dict(mode="portfolio"), dict(targets=["S1", "S1"])):
        task = dict(mode="holding", targets=["S1"], horizon="SWING", focuses=["BASIS"]) | changes
        with pytest.raises(ValueError):
            guard(Understanding(tasks=[task], inherited=False), context)


def test_real_call_contract_is_sealed_repeatable_and_private_values_absent(runtime):
    port = SemanticPort()
    repo, state = execute(
        runtime,
        port,
        question="成本83.719股数1379 PRIVATE_MARKER；先别说补仓，解释什么变化会推翻判断",
    )
    assert state["status"] == "COMPLETED", state.get("error")
    assert state["model_calls"] == 1 and state["tokens"] == 200
    assert state["data"]["tasks"][0]["focuses"] == ["INVALIDATION"]
    payload = canonical(repo.value(state["calls"][0]["request_ref"]))
    assert all(v not in payload for v in ("83.719", "1379", "PRIVATE_MARKER"))
    assert repo.get(state["run_id"])["data"] == state["data"]
    assert repo.create(state["request"])[1] is False
    with pytest.raises(ValueError):
        repo.save(dict(state, title="overwrite"))
    with pytest.raises(ValueError):
        repo.create(dict(state["request"], question="changed"))
    recorded = repo.base.get(state["run_id"])
    context = repo.context(recorded)
    assert context["question"] == state["public_projection"]["question"]


def test_continuous_reference_uses_three_prior_turns_and_remaps_reordered_slots(runtime):
    repo, first = execute(
        runtime, SemanticPort(targets=("S2",), mode="portfolio"), question="解释格力电器的反证"
    )
    port = SemanticPort(targets=("S1",), mode="portfolio", inherited=True)
    repo, second = execute(
        runtime,
        port,
        previous=first["run_id"],
        question="那第二只呢？",
        registry=list(reversed(REGISTRY)),
    )
    assert second["status"] == "COMPLETED", second.get("error")
    context = json.loads(port.requests[0].messages[-1].content)
    assert context["previous"][0]["tasks"][0]["targets"] == ["S1"]
    assert context["previous"][0]["question"].endswith("S1的反证")
    assert second["data"]["tasks"][0]["entities"] == ["格力电器"]
    assert second["data"]["inherited"]


def test_private_flags_omit_old_defaults_and_never_modify_account_constraints():
    holding, account = holding_request(), account_request()
    assert "semantic_understanding" not in holding.model_dump(mode="json")
    assert "semantic_understanding" not in account.model_dump(mode="json")
    analysis = resolved(SemanticPort().output, REGISTRY)
    old = compute(holding)
    new = compute(holding.model_copy(update=dict(semantic_understanding=True)))
    new["dialogue"] = dialogue(new, analysis)
    assert new["facts"] == old["facts"] and new["gates"] == old["gates"]
    assert "推翻" in new["dialogue"]["summary"]
    assert "position" not in canonical(analysis)
    assert account.model_dump(mode="json") == account_request(
        request_key=account.request_key
    ).model_dump(mode="json")


def test_focus_new_public_field_is_excluded_from_local_calculation_and_legacy_omits_it():
    req = InvestmentRequest(request_key=uuid4(), entities=["000333"])
    assert "analysis_focus" not in req.model_dump(mode="json")
    focused = req.model_copy(update=dict(analysis_focus=("CASHFLOW", "INVALIDATION")))
    assert focused.model_dump(mode="json")["analysis_focus"] == ["CASHFLOW", "INVALIDATION"]
    assert focused.calculation_request() == req.calculation_request()


def test_bound_semantics_rejects_wrong_question_even_when_valid_result_exists(runtime):
    repo, state = execute(runtime, SemanticPort())
    fake = dict(
        request=dict(semantic_understanding=True, request_key=str(uuid4()), question="other"),
        understanding=dict(run_id=state["run_id"]),
    )
    with pytest.raises(ValueError, match="未绑定"):
        bound_understanding(repo, fake, [])


def test_tampered_projection_or_answer_is_rejected(runtime):
    repo, state = execute(runtime, SemanticPort())
    altered = copy.deepcopy(state)
    altered["request"]["question"] = "新问题"
    with pytest.raises(ValueError, match="不一致"):
        repo.verify(altered)
    for key in ("model_calls", "charged_tokens", "tokens", "elapsed_seconds"):
        altered = copy.deepcopy(state)
        altered[key] += 500
        with pytest.raises(ValueError, match="不一致"):
            repo.verify(altered)


def test_stock_codes_have_numeric_boundaries(monkeypatch):
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.question_understanding.fetch",
        lambda *a: ({}, [dict(ts_code=s["code"], name=s["name"]) for s in REGISTRY]),
    )
    assert collect_registry(None, "现金100000333元，明日预测", [])[0] == []
    assert collect_registry(None, "研究000333未来3个月", [])[0] == REGISTRY[:1]


def test_busy_intake_and_orphan_running_task_can_retry_same_key(runtime, monkeypatch):
    from marketpulse.quant.knowledge import question_api

    app = FastAPI()
    app.include_router(question_api.router)
    app.state.research_store = runtime[0].base
    app.state.research_jobs = {"busy": SimpleNamespace(done=lambda: False)}
    scheduled = []
    monkeypatch.setattr(
        question_api, "schedule", lambda request, state: scheduled.append(state["run_id"])
    )
    payload = dict(request_key=str(uuid4()), question="研究美的集团")
    rid = QuestionStore.prefix + payload["request_key"].replace("-", "")
    with TestClient(app) as client:
        assert client.post("/api/quant/questions/understand", json=payload).status_code == 429
        assert not runtime[0].base.exists(rid)
        app.state.research_jobs.clear()
        assert client.post("/api/quant/questions/understand", json=payload).status_code == 202
        assert scheduled == [rid]
        # A process restart may leave a RUNNING record with no task in memory.
        assert client.post("/api/quant/questions/understand", json=payload).status_code == 202
        assert scheduled == [rid, rid]
        assert (
            client.post(
                "/api/quant/questions/understand",
                json=payload,
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )


def install_semantic_port(monkeypatch, port):
    from marketpulse.quant.knowledge import question_understanding as semantic

    original = semantic.run

    async def understood(repo, run_id, stop, settings, **options):
        return await original(repo, run_id, stop, settings, port=port)

    monkeypatch.setattr(semantic, "run", understood)
    return original


def test_advisor_semantics_negation_and_followup_are_bound_and_do_not_change_position(
    advisor, monkeypatch
):
    port = SemanticPort()
    install_semantic_port(monkeypatch, port)
    first = complete(advisor, holding_request(semantic_understanding=True))
    assert first["status"] == "COMPLETED", first.get("error")
    second = complete(
        advisor,
        holding_request(semantic_understanding=True, previous_run_id=first["run_id"]).model_copy(
            update=dict(question="先别说补仓，解释什么变化会推翻判断；PRIVATE_MARKER成本83.719")
        ),
    )
    assert second["status"] == "COMPLETED", second.get("error")
    assert second["data"]["theme"] == "INVALIDATION"
    assert second["data"]["dialogue"]["model_used"]
    assert second["research_run_id"] == first["research_run_id"]
    assert second["request"]["position"] == first["request"]["position"]
    actual = "".join(p.messages[-1].content for p in port.requests)
    assert all(v not in actual for v in ("PRIVATE_MARKER", "83.719", "1000000.37", "95.17"))
    assert advisor[0].get(second["run_id"])["data"] == second["data"]


def test_failed_optional_semantics_does_not_block_next_advisor_turn(advisor, monkeypatch):
    class FailedPort:
        async def generate(self, request):
            raise ValueError("synthetic failure")

    original = install_semantic_port(monkeypatch, FailedPort())
    first = complete(advisor, holding_request(semantic_understanding=True))
    assert first["status"] == "COMPLETED", first.get("error")
    assert not first["data"]["dialogue"]["model_used"]
    assert first["data"]["privacy"]["anonymous_semantics_sent_to_model"]
    monkeypatch.setattr("marketpulse.quant.knowledge.question_understanding.run", original)
    install_semantic_port(monkeypatch, SemanticPort())
    second = complete(
        advisor, holding_request(semantic_understanding=True, previous_run_id=first["run_id"])
    )
    assert second["status"] == "COMPLETED", second.get("error")
    assert second["data"]["dialogue"]["model_used"]


def test_account_focus_keeps_aggregate_short_and_continuation_targets_only_requested_stock():
    data = dict(
        changes=[
            dict(
                entity=s["entity"],
                name=s["name"],
                opinion=dict(summary="合成已审核观点"),
                industry_code="SYNTHETIC",
                sell_shares=100,
                buy_shares=0,
            )
            for s in REGISTRY
        ],
        before=dict(industries=[dict(code="SYNTHETIC", name="合成行业", weight="0.3")]),
        request=dict(industry_cap="0.25"),
        initial_breaches=[dict(label="合成行业行业集中度")],
        correlation=dict(status="COMPUTED", reason=None),
        correlated_groups=[],
    )
    understanding = resolved(
        SemanticPort(
            targets=("S1", "S2"),
            focuses=("CONCENTRATION", "CORRELATION", "DEFENSE"),
            mode="portfolio",
        ).output,
        REGISTRY,
    )
    legacy = dialogue(data, understanding, portfolio=True)
    aggregate = dialogue(data, understanding, portfolio=True, version="question-dialogue-v2")
    assert len(aggregate["lines"]) == 2 and len(legacy["lines"]) > 2
    understanding["tasks"][0]["entities"] = ["格力电器"]
    focused = dialogue(data, understanding, portfolio=True, version="question-dialogue-v2")
    assert any("格力电器所在合成行业" in line for line in focused["lines"])
    assert not any("美的集团：" in line for line in focused["lines"])
    data["correlation"] = dict(status="MISSING", reason="合成：缺少共同窗口")
    missing = dialogue(data, understanding, portfolio=True, version="question-dialogue-v2")
    assert any("不可计算" in line and "缺少共同窗口" in line for line in missing["lines"])
    assert not any("未识别超过" in line for line in missing["lines"])


def test_analysis_focus_reaches_method_and_tool_planning_without_altering_legacy():
    from marketpulse.quant.knowledge.active_research import planning_context
    from marketpulse.quant.knowledge.evaluation import protocol

    from .test_active_research import basis

    context, knowledge = basis()
    legacy = planning_context(context, knowledge, [])
    assert "user_analysis_focus" not in legacy
    focused = planning_context(dict(context, user_analysis_focus=["CASHFLOW"]), knowledge, [])
    assert focused["user_analysis_focus"] == ["CASHFLOW"]
    request = InvestmentRequest(request_key=uuid4(), entities=["000333"], enhanced_research=True)
    state = dict(
        request=request.model_dump(mode="json"),
        run_id="SYNTHETIC",
        created_at="2026-10-18T00:00:00+00:00",
    )
    assert protocol(state)["research_protocol"] == "active-public-research-v1"
    state["request"]["analysis_focus"] = ["CASHFLOW"]
    assert protocol(state)["research_protocol"] == "active-public-research-v1+semantic-focus-v1"
