import asyncio
import copy
import hashlib
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.quant.daily import briefing, history
from marketpulse.quant.daily.api import router, visible
from marketpulse.quant.daily.runtime import DailyRuntime, pending_investment, pending_next
from marketpulse.quant.decision import tushare
from marketpulse.quant.decision.next_session.store import ExperimentStore
from marketpulse.quant.decision.store import DecisionStore
from marketpulse.quant.live.store import ResearchRunRow, ResearchStore


@pytest.fixture
def base(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "metadata.sqlite").as_posix())
    ResearchRunRow.__table__.create(engine)
    repo = ResearchStore(
        sessionmaker(engine),
        LocalContentAddressedBlobStorage(tmp_path / "blobs"),
        tmp_path / "live-research",
    )
    yield repo
    engine.dispose()


def packet(base):
    days = [f"202609{i:02d}" for i in range(1, 21)]
    sources = []

    def source(api, params, rows):
        fields = list(rows[0])
        raw = json.dumps(
            dict(code=0, data=dict(fields=fields, items=[[r[k] for k in fields] for r in rows]))
        ).encode()
        ref = base.raw(raw)
        request_ref = base.blob(
            dict(
                tool="SYNTHETIC",
                url=tushare.URL,
                family="TUSHARE",
                data=dict(api_name=api, params=params, fields=tushare.FIELDS[api]),
            )
        )
        sources.append(
            dict(
                source_family="TUSHARE",
                api_name=api,
                url=tushare.URL,
                parameters=params,
                sha256=hashlib.sha256(raw).hexdigest(),
                blob_ref=ref,
                request_ref=request_ref,
                fetched_at="2026-10-01T00:00:00+00:00",
            )
        )

    catalog = [
        dict(
            index_code="S" + str(i), industry_name="合成测试行业" + str(i), level="L1", src="SW2021"
        )
        for i in range(4)
    ]
    source("index_classify", dict(level="L1", src="SW2021"), catalog)
    master = [dict(ts_code=f"00000{i}.SZ", list_date="20200101") for i in range(4)]
    source("stock_basic", dict(exchange="", list_status="L"), master)
    memberships, daily = {}, {}
    for group, stock in zip(catalog, master, strict=True):
        members = [dict(ts_code=stock["ts_code"], l1_code=group["index_code"], is_new="Y")]
        source("index_member_all", dict(l1_code=group["index_code"], is_new="Y"), members)
        memberships[group["index_code"]] = [stock["ts_code"]]
    source(
        "trade_cal",
        dict(exchange="SSE", start_date=days[0], end_date=days[-1]),
        [dict(cal_date=d, is_open=1, exchange="SSE") for d in days],
    )
    for day in days:
        rows = [
            dict(ts_code=s["ts_code"], trade_date=day, close=11, pre_close=10, amount=200)
            for s in master
        ]
        source("daily", dict(trade_date=day), rows)
        daily[day] = {r["ts_code"]: r for r in rows}
    return dict(
        captured_at="2026-10-01T00:01:00+00:00",
        sessions=days,
        catalog=catalog,
        memberships=memberships,
        daily=daily,
        sources=sources,
    )


class SyntheticPort:
    def __init__(self, fail_writer=False, reject=False):
        self.calls = []
        self.fail_writer, self.reject = fail_writer, reject

    async def generate(self, request):
        context = json.loads(request.messages[-1].content.split("\n上次检查失败：")[0])
        schema = request.response_model
        self.calls.append(schema.__name__)
        if schema is briefing.Selection:
            value = dict(industry_ids=[s["industry_id"] for s in context["sectors"]][:4])
        elif schema is briefing.Briefing:
            value = dict(
                stance="RISK_FIRST",
                summary="合成测试只检查流程，观察条件还需真实核对。",
                items=[
                    dict(
                        industry_id=i,
                        action="WAIT",
                        reason="合成测试价格变化需要等待后续确认。",
                        counterargument="合成测试市场环境仍可能改变。",
                        confirmation="合成测试后续价格和成交继续核对。",
                        method_rule_ids=[sorted(briefing.RULES)[0]],
                    )
                    for i in context["industry_ids"]
                ],
            )
        elif schema is briefing.Review:
            value = dict(
                verdict="REJECT" if self.reject else "APPROVE",
                facts_supported=not self.reject,
                counterarguments_useful=True,
                no_invented_facts=True,
                reason="合成测试复核，不代表真实投资分析。",
            )
        else:
            if self.fail_writer:
                raise ValueError("合成测试发布中断")
            value = context
        return SimpleNamespace(
            output=schema.model_validate(value),
            usage=SimpleNamespace(input_tokens=100, output_tokens=100),
            provider="SYNTHETIC",
            model="SYNTHETIC",
            response_id="test",
        )


def run_fixture(base, monkeypatch, port=None):
    data = packet(base)
    monkeypatch.setattr(
        briefing,
        "knowledge_snapshot",
        lambda *_: dict(
            fragments=[
                dict(
                    rule_id=r,
                    version="a" * 64,
                    system_rule="合成测试方法，不代表投资效果。",
                    counterexamples=["合成测试反例需要核查。"],
                )
                for r in briefing.RULES
            ]
        ),
    )
    state, fresh = briefing.create(base, "20261017", "CURRENT_REVIEW")
    assert fresh
    settings = SimpleNamespace(model="SYNTHETIC", model_read_timeout_seconds=5)
    kwargs = dict(collector=lambda *_: data, port=port or SyntheticPort())
    asyncio.run(briefing.run(base, None, state["run_id"], settings, **kwargs))
    return state["run_id"], settings, kwargs


def test_history_over_100_ties_search_and_private_fields(base):
    for i in range(137):
        base.put(
            dict(
                run_id=f"RUN-IR-{i:032x}",
                status="COMPLETED",
                created_at="2026-10-01T00:00:00+00:00",
                scenario="investment_research",
                title=f"合成股票{i}",
                request=dict(profile=dict(capital="PRIVATE")),
            )
        )
    ExperimentStore(base).immutable("DEC-DAILY-SYNTHETIC", dict(secret="PRIVATE"))
    ids, cursor = [], None
    while True:
        result = history.page(base, limit=17, cursor=cursor)
        ids += [r["run_id"] for r in result["items"]]
        assert "PRIVATE" not in json.dumps(result)
        cursor = result["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 137
    assert len(history.page(base, query="合成股票136")["items"]) == 1
    with pytest.raises(ValueError):
        history.page(base, cursor="invalid")


@pytest.mark.parametrize(
    "mode,target",
    [("market_scan", "market"), ("candidates", "candidates"), ("medium_term", "decision")],
)
def test_unified_history_keeps_market_and_candidate_detail_routes(base, mode, target):
    base.put(
        dict(
            run_id="RUN-DEC-" + "d" * 32,
            status="COMPLETED",
            created_at="2026-10-01T00:00:00+00:00",
            scenario="decision",
            title="合成历史路由测试",
            request=dict(research_mode=mode),
        )
    )
    assert history.page(base)["items"][0]["page"] == target


def test_old_empty_finished_batch_is_not_presented_as_a_success(base):
    state = dict(
        run_id="RUN-NS-" + "e" * 32,
        status="COMPLETED",
        created_at="2026-10-01T00:00:00+00:00",
        scenario="next_session",
        title="合成失败历史",
        request={},
        prediction_ids=[],
        attempt_ids=["synthetic-failure"],
    )
    base.put(state)
    assert history.page(base)["items"][0]["status"] == "FAILED"
    assert base.get(state["run_id"])["status"] == "COMPLETED"


def test_sectors_use_fixed_denominator_and_actual_amount_unit(base):
    data = packet(base)
    result = briefing.metrics(data)
    assert Decimal(result["sectors"][0]["amount"]) == 200000
    changed = copy.deepcopy(data)
    changed["daily"][data["sessions"][2]].pop("000000.SZ")
    with pytest.raises(ValueError, match="不足四"):
        briefing.metrics(changed)
    with pytest.raises(ValueError, match="日行情"):
        briefing.verify_packet(DecisionStore(base), changed)


from decimal import Decimal  # noqa: E402


def test_four_roles_approved_binding_and_immutable_completed_history(base, monkeypatch):
    rid, _, _ = run_fixture(base, monkeypatch)
    state = briefing.get(base, rid)
    assert state["status"] == "COMPLETED" and state["model_calls"] == 4
    assert state["data"]["timing"] == "CURRENT_REVIEW"
    assert state["charged_tokens"] == 800
    state["title"] = "改写历史"
    with pytest.raises(ValueError, match="immutable"):
        base.put(state)
    assert briefing.create(base, "20261017", "CURRENT_REVIEW")[1] is False


def test_reject_does_not_publish_or_fake_a_result(base, monkeypatch):
    rid, _, _ = run_fixture(base, monkeypatch, SyntheticPort(reject=True))
    state = base.get(rid)
    assert state["status"] == "INTERRUPTED" and not state.get("result_ref")
    assert "复核" in state["error"]


def test_resume_reuses_sources_roles_and_spent_budget(base, monkeypatch):
    port = SyntheticPort(fail_writer=True)
    rid, settings, kwargs = run_fixture(base, monkeypatch, port)
    old = base.get(rid)
    assert old["status"] == "INTERRUPTED"
    port.fail_writer = False
    kwargs["collector"] = lambda *_: pytest.fail("不能重取封存输入")
    asyncio.run(briefing.run(base, None, rid, settings, **kwargs))
    final = briefing.get(base, rid)
    assert final["status"] == "COMPLETED"
    assert (
        port.calls.count("Selection")
        == port.calls.count("Briefing")
        == port.calls.count("Review")
        == 1
    )
    assert final["model_calls"] == old["model_calls"] + 1
    assert final["charged_tokens"] > old["charged_tokens"]


def test_pending_rules_keep_missing_retryable_and_stop_matured():
    pending = dict(
        latest=dict(
            evaluation=dict(through="20261016", stocks=[dict(status="DATA_MISSING_UNSCORABLE")])
        )
    )
    assert pending_investment(pending, "20261019")
    assert pending_investment(pending, "20261016")
    pending["latest"]["evaluation"]["stocks"][0]["status"] = "MATURED"
    assert not pending_investment(pending, "20261019")
    repo = SimpleNamespace(
        prediction=lambda _: SimpleNamespace(target_session=date(2026, 10, 16)),
        outcome_versions=lambda _: [],
    )
    assert pending_next(repo, dict(prediction_ids=["p"]), "20261016")
    assert not pending_next(repo, dict(prediction_ids=["p"]), "20261015")
    pending["last_attempt"] = dict(status="ACQUISITION_FAILED")
    pending["latest"]["evaluation"]["stocks"][0]["status"] = "PENDING"
    assert pending_investment(pending, "20261016")


def runtime(base):
    return DailyRuntime(
        SimpleNamespace(
            state=SimpleNamespace(
                research_store=base, research_jobs={}, knowledge_rules=None, settings=None
            )
        )
    )


def test_scheduler_no_weekend_generation_and_one_daily_request(base, monkeypatch):
    worker = runtime(base)
    calls = []

    async def fake_run(*args):
        calls.append(args[2])

    monkeypatch.setattr(briefing, "run", fake_run)

    async def check():
        await worker.morning(datetime.fromisoformat("2026-10-17T09:20:00+08:00"), ["20261019"])
        assert calls == []
        await worker.morning(datetime.fromisoformat("2026-10-19T09:10:00+08:00"), ["20261019"])
        await worker.morning(datetime.fromisoformat("2026-10-19T09:15:00+08:00"), ["20261019"])
        assert len(calls) == 1

    asyncio.run(check())


def test_exclusive_does_not_start_while_user_research_active(base):
    worker = runtime(base)

    async def check():
        worker.app.state.research_jobs["user"] = asyncio.create_task(asyncio.sleep(0.01))

        async def forbidden():
            pytest.fail("不能和用户研究争抢容量")

        assert await worker.exclusive("scheduled", forbidden) is False
        await worker.app.state.research_jobs["user"]

    asyncio.run(check())


def test_daily_api_preserves_old_record_and_safe_origin(base, monkeypatch):
    rid, _, _ = run_fixture(base, monkeypatch)
    app = FastAPI()
    app.state.research_store = base
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/quant/daily/status").json()["enabled"] is False
        assert client.get("/api/quant/daily/history").json()["items"][0]["run_id"] == rid
        assert client.get("/api/quant/daily/briefings/" + rid).json()["status"] == "COMPLETED"
        assert client.get("/api/quant/daily/briefings/../../bad").status_code == 404
        assert (
            client.post(
                "/api/quant/daily/briefings/" + rid + "/resume",
                headers={"origin": "https://other.example"},
            ).status_code
            == 403
        )


def test_schedule_visibility_hides_early_conclusion():
    state = dict(
        run_id="synthetic",
        request=dict(kind="SCHEDULED", trade_date="19000101"),
        status="COMPLETED",
        data=dict(summary="synthetic"),
    )
    # Old days are always readable; only today's early prepared draft is held until 09:25.
    assert visible(state)["data"]["summary"] == "synthetic"


def test_auto_observation_claim_survives_restart_without_repeating(base, monkeypatch):
    state = dict(
        run_id="RUN-NS-" + "a" * 32,
        created_at="2026-10-15T12:00:00+00:00",
        status="COMPLETED",
        request={},
        prediction_ids=["p"],
    )
    base.put(state)
    calls = []

    def install(worker):
        monkeypatch.setattr(worker.events, "get_run", lambda _: state)
        monkeypatch.setattr(
            worker.events,
            "prediction",
            lambda _: SimpleNamespace(target_session=date(2026, 10, 16)),
        )
        monkeypatch.setattr(worker.events, "outcome_versions", lambda _: [])
        return worker

    def observe(*_):
        calls.append(1)
        return dict(outcomes=[dict(status="PENDING_DATA")])

    monkeypatch.setattr("marketpulse.quant.daily.runtime.next_session.observe", observe)
    asyncio.run(
        install(runtime(base)).observe_one(
            datetime.fromisoformat("2026-10-16T15:40:00+08:00"), ["20261016"]
        )
    )
    asyncio.run(
        install(runtime(base)).observe_one(
            datetime.fromisoformat("2026-10-16T15:50:00+08:00"), ["20261016"]
        )
    )
    assert len(calls) == 1
    asyncio.run(
        install(runtime(base)).observe_one(
            datetime.fromisoformat("2026-10-16T18:10:00+08:00"), ["20261016"]
        )
    )
    assert len(calls) == 2


@pytest.mark.parametrize("kind", ["NEXT", "INVESTMENT"])
def test_observation_integrity_reads_do_not_block_page_requests(base, monkeypatch, kind):
    from threading import Event

    from marketpulse.quant.daily import runtime as module

    worker = runtime(base)
    state = dict(
        run_id="RUN-NS-" + "b" * 32 if kind == "NEXT" else "RUN-IR-" + "b" * 32,
        status="COMPLETED",
        request=dict(schema_version="investment-research-v4"),
        prediction_ids=["p"] if kind == "NEXT" else [],
    )
    page_served = Event()
    verified = []

    def read(_):
        assert page_served.wait(2), "自动核对的完整性复算阻塞了页面请求"
        verified.append(kind)
        return state

    monkeypatch.setattr(
        worker.events,
        "records",
        lambda prefix: [state] if state["run_id"].startswith(prefix) else [],
    )
    monkeypatch.setattr(worker.events, "get_run", read)
    monkeypatch.setattr(module, "InvestmentStore", lambda _: SimpleNamespace(get=read))
    monkeypatch.setattr(module, "ObservationStore", lambda _: SimpleNamespace(view=lambda _: {}))
    monkeypatch.setattr(module, "pending_next", lambda *_: False)
    monkeypatch.setattr(module, "pending_investment", lambda *_: False)

    async def check():
        task = asyncio.create_task(
            worker.observe_one(datetime.fromisoformat("2026-10-18T18:30:00+08:00"), ["20261016"])
        )
        await asyncio.sleep(0)
        page_served.set()
        await task

    asyncio.run(check())
    assert verified == [kind]


def test_early_draft_is_held_until_publish_time(monkeypatch):
    from marketpulse.quant.daily import api

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 19, 1, 20, tzinfo=UTC)

    monkeypatch.setattr(api, "datetime", Clock)
    state = dict(
        run_id="SYNTHETIC",
        request=dict(kind="SCHEDULED", trade_date="20261019"),
        status="COMPLETED",
        data=dict(summary="HIDDEN_BEFORE_0925"),
    )
    assert "data" not in visible(state) and visible(state)["status"] == "PREPARING"


def test_calendar_missed_window_is_retained_without_fake_recommendation(base):
    worker = runtime(base)
    asyncio.run(worker.morning(datetime.fromisoformat("2026-10-19T10:00:00+08:00"), ["20261019"]))
    assert worker.state["morning_status"] == "MISSED_WINDOW"
    assert not base.list(prefix="RUN-MB-")
    asyncio.run(worker.morning(datetime.fromisoformat("2026-10-19T10:01:00+08:00"), ["20261019"]))
    assert len(worker.events.records("DEC-DAILY-MISSED-")) == 1
