"""Synthetic enhanced workflow checks; no real archive, network or investment results."""

import asyncio
import copy
import json
import sys
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_next_session_experiment import NOW, calendar
from test_next_session_experiment import repo as _repo

from marketpulse.quant.contracts import digest
from marketpulse.quant.decision import tushare
from marketpulse.quant.decision.next_session import enhanced, service, snapshot
from marketpulse.quant.decision.next_session.agents import Proposal, Review, Writing
from marketpulse.quant.decision.next_session.contracts import (
    EnhancedNextSessionSnapshot,
    NextSessionRunRequest,
    NextSessionSnapshot,
)
from marketpulse.quant.decision.service import source_capability
from marketpulse.quant.knowledge.active_research import ResearchPlan
from marketpulse.quant.live.store import ResearchRunRow

repo = _repo

SID = "688012.SH"


@pytest.fixture(autouse=True)
def fixed_closed_session_clock(monkeypatch):
    """A synthetic after-close experiment must not drift into the real target-day morning."""
    from marketpulse.quant.decision.next_session import agents, store
    from marketpulse.quant.knowledge import active_research, research_tools

    ticks = 0

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            nonlocal ticks
            ticks += 1
            current = NOW + timedelta(milliseconds=ticks)
            return current.astimezone(tz) if tz else current.astimezone().replace(tzinfo=None)

    for module in (
        sys.modules[__name__],
        service,
        store,
        agents,
        enhanced,
        active_research,
        research_tools,
    ):
        monkeypatch.setattr(module, "datetime", FixedDatetime)


def knowledge():
    return dict(
        rules=[
            dict(
                rule_id=key,
                version="a" * 64,
                rule=dict(
                    title="合成参考方法",
                    horizons=["MEDIUM"],
                    required_inputs=["真实资料"],
                    conditions=["核对口径"],
                    system_rule="检查真实证据与反证，不保证投资效果。",
                    counterexamples=["单项指标可能失效。"],
                    forbidden_inferences=["不能当成明日信号。"],
                    execution_status="REFERENCE_ONLY",
                    validation_status="UNVALIDATED",
                ),
            )
            for key in ("KR-AVOID-RUIN", "KR-COST-NET-RETURN")
        ],
        documents=[],
        fragments=[],
    )


class SyntheticPort:
    def __init__(self, reject_once=False):
        self.requests = []
        self.reject_once = reject_once

    async def generate(self, request):
        self.requests.append(request)
        context = json.loads(request.messages[-1].content.split("\n上次检查失败：")[0])
        schema = request.response_model
        if schema is ResearchPlan:
            value = dict(
                selections=[
                    dict(
                        security_id=SID,
                        method_rule_ids=["KR-COST-NET-RETURN"],
                        hypothesis="核查市场预期与价格方向是否相互支持。",
                        countercheck="若估值缺乏同行比较则不能当作短期方向证据。",
                    )
                ],
                tools=[
                    dict(tool="VALUATION_SNAPSHOT", security_id=SID, query="核对基准日估值。"),
                    dict(tool="EVIDENCE_SEARCH", security_id=SID, query="收盘价格"),
                ],
                ready=True,
            )
        elif schema is Proposal:
            opposing_id = (
                "R-688012SH-pb"
                if any(e["evidence_id"] == "R-688012SH-pb" for e in context["evidence"])
                else "NS-TENCENT-close"
            )
            value = dict(
                direction="UP",
                summary="倾向看涨，证据有限需留意隔夜变化。",
                reasons=[
                    dict(
                        text="收盘价格为本轮方向判断提供有限参考。",
                        evidence_ids=["NS-TENCENT-close"],
                        next_session_mechanism="若市场环境延续则可能支持价格方向。",
                    )
                ],
                opposing_reasons=[
                    dict(
                        text="估值仅是截面，无法单独证明短期价格方向。",
                        evidence_ids=[opposing_id],
                        next_session_mechanism="若情绪发生变化则可能推翻当前方向。",
                    )
                ],
                limitations=["合成软件流程测试，不代表真实投资结果。"],
                support_level="WEAK",
            )
        elif schema is Review:
            value = dict(
                verdict="APPROVE",
                citations_supported=True,
                next_session_relevant=True,
                no_invented_facts=True,
                reason="合成审核通过。",
            )
            if self.reject_once:
                self.reject_once = False
                value.update(
                    verdict="REJECT",
                    citations_supported=False,
                    reason="合成复核要求核对反证并重新分析。",
                )
        elif schema is Writing:
            value = context
        else:
            pytest.fail("unexpected model schema")
        return SimpleNamespace(
            output=schema.model_validate(value),
            usage=SimpleNamespace(input_tokens=100, output_tokens=100),
            provider="SYNTHETIC",
            model="synthetic-model",
            response_id="synthetic-response",
        )


class SyntheticClient:
    fail_valuation = False

    def __init__(self, repo, state, save, stop, **_):
        self.repo, self.state, self.save = repo, state, save

    def fetch(self, key, url, *, data, family):
        assert url == tushare.URL and data["api_name"] == "daily_basic"
        fields = data["fields"].split(",")
        row = dict(
            ts_code=SID,
            trade_date=data["params"]["trade_date"],
            close="99.50",
            pe_ttm="15.25",
            pb="2.5",
            total_mv="1000",
            circ_mv="900",
            total_share="10",
        )
        value = dict(code=0, data=dict(fields=fields, items=[[row[k] for k in fields]]))
        raw = json.dumps(value).encode()
        ref = self.repo.raw(raw)
        definition = dict(tool=key, url=url, params=None, data=data, family=family)
        if self.fail_valuation:
            from marketpulse.quant.decision.tools import SourceUnavailable

            self.state["calls"].append(
                dict(
                    kind="source",
                    status="FAILED",
                    request_ref=self.repo.blob(definition),
                    finished_at=datetime.now(UTC).isoformat(),
                    error="synthetic unavailable",
                )
            )
            self.save()
            raise SourceUnavailable("synthetic unavailable")
        source = dict(
            key=key,
            url=url,
            source_family=family,
            blob_ref=ref,
            sha256=ref.rsplit("/", 1)[1],
            fetched_at=datetime.now(UTC).isoformat(),
            byte_count=len(raw),
            request_ref=self.repo.blob(definition),
            api_name=data["api_name"],
            parameters=data["params"],
        )
        self.state["calls"].append(
            dict(
                kind="source",
                status="COMPLETED",
                source=source,
                request_ref=source["request_ref"],
                response_ref=ref,
            )
        )
        self.save()
        return source, value


def synthetic_snapshot(repo, state, security):
    now = datetime.now(UTC)
    days = calendar().sessions
    base = max(d for d in days if service.close_at(d) <= now)
    target = service.next_session(days, base)
    original = repo.blob(dict(close="99.50"))
    source = dict(
        key="synthetic_quote",
        url="https://example.com/quote",
        source_family="TENCENT",
        blob_ref=original,
        sha256=original.rsplit("/", 1)[1],
        fetched_at=now.isoformat(),
    )
    item = dict(
        evidence_id="NS-TENCENT-close",
        subject_id=security,
        kind="fact",
        source_family="TENCENT",
        original_source_id="synthetic-quote",
        url=source["url"],
        raw_ref=original,
        content_hash=source["sha256"],
        locator=dict(kind="json", path="/close"),
        text="合成收盘价格",
        value="99.50",
        unit="CNY/share",
        fetched_at=now.isoformat(),
        available_at=now.isoformat(),
        time_precision="second",
        method_version="synthetic-v1",
        quality_status="AVAILABLE",
    )
    value = dict(
        security_id=security,
        base_session=base,
        target_session=target,
        requested_at=state["created_at"],
        evidence_sealed_at=now,
        calendar=calendar(),
        daily_prices=[
            dict(
                security_id=security,
                session=d,
                close="99.50",
                price_tick="0.01",
                turnover="1000",
                source_ref=original,
                reference_price="99.00",
                reference_basis="EXCHANGE_QUOTE_PREVCLOSE",
                suspended_all_day=False,
            )
            for d in (base - timedelta(days=1), base)
        ],
        evidence=dict(
            items=[item],
            sources=[source_capability(source)],
            cutoff_at=now,
            coverage=dict(
                universe_total=1,
                attempted=1,
                available=1,
                missing=0,
                excluded=0,
                universe_ref=security,
                member_refs=[security],
                source_dates=[str(base)],
                method="synthetic only",
                status="COMPLETE",
            ),
        ),
        sources=[source],
        indicator_refs=[],
        missing_inputs=["合成数据，不是真实预测"],
        input_hash="0" * 64,
        model_context=dict(
            security_id=security,
            name="合成股票",
            base_session=str(base),
            target_session=str(target),
            evidence_sealed_at=now.isoformat(),
            evidence=[
                dict(
                    evidence_id=item["evidence_id"],
                    text=item["text"],
                    kind="fact",
                    period_end=None,
                    limitations=[],
                )
            ],
            missing_inputs=["合成数据，不是真实预测"],
            method_version="next-session-observations-v1",
        ),
    )
    serialized = NextSessionSnapshot(**value).model_dump(mode="json")
    serialized["input_hash"] = digest({k: v for k, v in serialized.items() if k != "input_hash"})
    snap = NextSessionSnapshot.model_validate(serialized)
    return repo.blob(serialized), snap, dict(name="合成股票", security_id=security)


@pytest.fixture
def completed(repo, monkeypatch, request):
    seen = []
    base_values = {}

    def collect(repo, state, stop, security, calendar, save):
        ref, snap, subject = synthetic_snapshot(repo, state, security)
        base_values[ref] = snap.model_dump(mode="json")
        return ref, snap, subject

    def verify_original(repo, snap, data):
        seen.append(snap.schema_version)
        # The new wrapper must always invoke the old reader on the original bytes.
        assert snap.schema_version == "next-session-v1"
        if snap.model_dump(mode="json") not in base_values.values():
            raise ValueError("synthetic original changed")

    monkeypatch.setattr(service, "SourceClient", SyntheticClient)
    monkeypatch.setattr(service, "build_snapshot", collect)
    monkeypatch.setattr(snapshot, "verify_snapshot", verify_original)
    monkeypatch.setattr(service, "load_calendar", lambda _c, exchange, _y: calendar(exchange))
    monkeypatch.setattr(service, "resolve", lambda *_: dict(security_id=SID, name="合成股票"))
    monkeypatch.setattr(service, "cohort_for", lambda *_: "PRIMARY")
    monkeypatch.setattr(enhanced, "rules_for", lambda _: object())
    monkeypatch.setattr(enhanced, "knowledge_snapshot", lambda *_, **__: knowledge())
    mode = getattr(request, "param", None)
    monkeypatch.setattr(SyntheticClient, "fail_valuation", mode == "unavailable")
    config = service.template().model_dump(mode="json")
    config["model_version"] = "synthetic-model"
    if mode == "noentity":
        config["securities"] = [SID]
    request = NextSessionRunRequest(
        request_key=uuid4(),
        experiment_id=config["experiment_id"],
        entity=None if mode == "noentity" else "合成股票",
        enhanced_research=True,
    )
    state, _ = repo.create_run(request.model_dump(mode="json"), config, "synthetic")
    port = SyntheticPort(reject_once=mode == "repair")
    asyncio.run(
        service.run(
            repo,
            state["run_id"],
            SimpleNamespace(
                model="synthetic-model", model_read_timeout_seconds=10, reasoning_effort="high"
            ),
            Event(),
            port,
        )
    )
    final = repo.get_run(state["run_id"])
    assert final["status"] == "COMPLETED", [repo.record(a)["reason"] for a in final["attempt_ids"]]
    return repo, final, port, seen


def test_disabled_request_bytes_and_original_policy_are_unchanged():
    request = dict(
        request_key=str(uuid4()), experiment_id="ALPHA_NEXT_SESSION_V1", exploratory=False
    )
    assert NextSessionRunRequest(**request).model_dump(mode="json", exclude_none=True) == request
    explicit = NextSessionRunRequest(**request, enhanced_research=False)
    assert explicit.model_dump(mode="json", exclude_none=True) == request
    state = dict(request=request, config=service.template().model_dump(mode="json"))
    original = copy.deepcopy(state)
    policy = service.policy_hash()
    enhanced.enable(state)
    assert state == original and service.policy_hash() == policy


def test_enhanced_workflow_seals_tools_references_and_restores_history(completed):
    repo, state, port, seen = completed
    assert state["status"] == "COMPLETED", state.get("error") or state.get("attempt_ids")
    assert state["cohort"] == "EXPLORATORY"
    assert len(state["prediction_ids"]) == 1 and len(port.requests) == 4
    assert all(r.thinking_enabled for r in port.requests[:-1])
    assert port.requests[-1].thinking_enabled is False
    prediction = repo.prediction(state["prediction_ids"][0])
    snap = repo.snapshot(state["snapshots"][SID])
    assert isinstance(snap, EnhancedNextSessionSnapshot)
    base = repo.snapshot(snap.base_snapshot_ref)
    assert prediction.input_hash == snap.input_hash != base.input_hash
    assert (
        prediction.cohort == "EXPLORATORY" and prediction.prompt_version == enhanced.PROMPT_VERSION
    )
    assert not repo.records("DEC-PRIMARY-")
    assert "next-session-v1" in seen
    assert {m["rule_id"] for m in snap.model_context["methods"]} == {
        "KR-AVOID-RUIN",
        "KR-COST-NET-RETURN",
    }
    facts = {e.evidence_id: e for e in snap.evidence.items}
    assert facts["R-688012SH-pb"].value == 2.5
    assert facts["R-688012SH-total_mv"].value == 10000000
    research = repo.value(snap.research_state_ref)
    assert research["calls"] == state["calls"][: len(research["calls"])]
    assert {snap.base_snapshot_ref, snap.research_state_ref, snap.knowledge_ref} <= set(
        state["source_refs"]
    )
    assert repo.get_run(state["run_id"])["prediction_ids"] == state["prediction_ids"]
    assert repo.snapshot_for_prediction(prediction) == snap


@pytest.mark.parametrize("completed", ["noentity", "unavailable"], indirect=True)
def test_enhanced_is_exploratory_without_entity_and_survives_optional_source_gap(completed):
    repo, state, _port, _seen = completed
    assert state["status"] == "COMPLETED" and state["cohort"] == "EXPLORATORY"
    assert not repo.records("DEC-PRIMARY-")
    snap = repo.snapshot(state["snapshots"][SID])
    valuation = next(
        t for t in snap.model_context["tool_results"] if t["tool"] == "VALUATION_SNAPSHOT"
    )
    if not state["request"].get("entity"):
        assert valuation["status"] == "AVAILABLE"
    else:
        assert valuation["status"] == "UNAVAILABLE" and valuation["facts"] == []
        assert not any(e.evidence_id.startswith("R-") for e in snap.evidence.items)
        assert valuation["failed_call_indices"]


@pytest.mark.parametrize("completed", ["repair"], indirect=True)
def test_enhanced_reader_replays_rejected_review_and_corrected_analysis(completed):
    repo, state, port, _seen = completed
    assert state["status"] == "COMPLETED" and len(port.requests) == 6
    analyses = [c for c in state["calls"] if c.get("role") == "Analyst"]
    reviews = [c for c in state["calls"] if c.get("role") == "Verifier"]
    assert len(analyses) == len(reviews) == 2
    repaired = repo.value(analyses[1]["request_ref"])["context"]
    assert repaired["previous_proposal"] == repo.value(analyses[0]["response_ref"])
    assert repaired["review_feedback"] == repo.value(reviews[0]["response_ref"])
    assert repo.get_run(state["run_id"])["status"] == "COMPLETED"


@pytest.mark.parametrize("kind", ["context", "value", "base", "seal"])
def test_enhanced_reader_rejects_changed_inputs_despite_rehashed_envelope(completed, kind):
    repo, state, _port, _seen = completed
    value = repo.value(state["snapshots"][SID])
    if kind == "context":
        value["model_context"]["methods"][0]["system_rule"] = "篡改方法"
    elif kind == "value":
        value["evidence"]["items"][-1]["value"] = "1"
    elif kind == "base":
        original = repo.value(value["base_snapshot_ref"])
        original["model_context"]["name"] = "篡改主体"
        original["input_hash"] = digest({k: v for k, v in original.items() if k != "input_hash"})
        value["base_snapshot_ref"] = repo.blob(original)
    else:
        value["evidence_sealed_at"] = repo.value(value["base_snapshot_ref"])["evidence_sealed_at"]
    value["input_hash"] = digest({k: v for k, v in value.items() if k != "input_hash"})
    with pytest.raises(ValueError):
        repo.snapshot(repo.blob(value))


@pytest.mark.parametrize("role", ["Analyst", "Verifier", "Writer"])
@pytest.mark.parametrize("mutation", ["profile", "instructions", "schema", "prompt", "context"])
def test_enhanced_completed_role_calls_are_bound_to_profile_and_fixed_context(
    completed, role, mutation
):
    repo, state, _port, _seen = completed
    changed = copy.deepcopy(state)
    call = next(c for c in changed["calls"] if c.get("role") == role)
    request = repo.value(call["request_ref"])
    if mutation == "profile":
        call["thinking_enabled"] = not call["thinking_enabled"]
        request["thinking_enabled"] = call["thinking_enabled"]
    elif mutation == "instructions":
        request["instructions"] = "替换角色职责"
        request["messages"][0]["content"] = request["instructions"]
    elif mutation == "schema":
        request["schema"] = {"type": "object"}
    elif mutation == "prompt":
        request["prompt_version"] = "unrelated-version"
    else:
        request["context"] = dict(request["context"], injected_fact="未经封存的新事实")
        from marketpulse.quant.contracts import canonical

        request["messages"][1]["content"] = canonical(request["context"])
    call["request_ref"] = repo.blob(request)
    with repo.base.sessions.begin() as session:
        session.get(ResearchRunRow, changed["run_id"]).payload = changed
    with pytest.raises(ValueError):
        repo.get_run(changed["run_id"])


@pytest.mark.parametrize(
    "mutation",
    ["removed_review", "unbound_approval", "changed_analyst", "changed_writer", "before_seal"],
)
def test_self_declared_approval_cannot_replace_completed_model_publication(completed, mutation):
    repo, state, _port, _seen = completed
    changed = copy.deepcopy(state)
    if mutation == "removed_review":
        changed["calls"] = [c for c in changed["calls"] if c.get("role") != "Verifier"]
    elif mutation == "unbound_approval":
        pid = changed["prediction_ids"][0]
        envelope = repo.record(pid)
        approved = repo.value(envelope["review_refs"][0])
        approved["reason"] = "伪造批准，不来自实际模型调用"
        envelope["review_refs"][0] = repo.blob(approved)
        with repo.base.sessions.begin() as session:
            session.get(ResearchRunRow, pid).payload = dict(
                payload=envelope, payload_hash=digest(envelope)
            )
    elif mutation in {"changed_analyst", "changed_writer"}:
        role = "Analyst" if mutation == "changed_analyst" else "Writer"
        call = next(c for c in changed["calls"] if c.get("role") == role)
        output = repo.value(call["response_ref"])
        output["summary"] = "替换为未经对应审核的结论"
        call["response_ref"] = repo.blob(output)
    else:
        call = next(c for c in changed["calls"] if c.get("role") == "Analyst")
        snap = repo.snapshot(changed["snapshots"][SID])
        call["started_at"] = (snap.evidence_sealed_at - timedelta(seconds=1)).isoformat()
    with repo.base.sessions.begin() as session:
        session.get(ResearchRunRow, changed["run_id"]).payload = changed
    with pytest.raises(ValueError):
        repo.get_run(changed["run_id"])


def measured_trace(repo, state, role):
    """Add a synthetic paid input measurement, preserving the real publication inputs."""
    from marketpulse.quant.contracts import canonical
    from marketpulse.quant.decision.next_session.agents import input_count_key

    state = copy.deepcopy(state)
    index, formal = next((i, c) for i, c in enumerate(state["calls"]) if c.get("role") == role)
    request = repo.value(formal["request_ref"])
    key = input_count_key(request)
    count_request = dict(request, purpose="input_count", max_output_tokens=1, input_count_key=key)
    count_ref = repo.blob(count_request)
    probe = dict(
        formal,
        purpose="input_count",
        status="MEASURED",
        request_ref=count_ref,
        max_output_tokens=1,
        input_count_key=key,
        input_tokens=100,
        output_tokens=1,
        charged_tokens=101,
        finished_at=formal["started_at"],
        reserved_tokens=len(
            (
                request["messages"][0]["content"]
                + request["messages"][1]["content"]
                + canonical(request["schema"])
            ).encode("utf8")
        )
        + 1,
    )
    probe.pop("response_ref", None)
    request.update(input_count_ref=count_ref, input_count_call_index=index, input_count_key=key)
    formal.update(
        request_ref=repo.blob(request),
        input_count_ref=count_ref,
        input_count_call_index=index,
        input_count_key=key,
        reserved_tokens=100 + request["max_output_tokens"],
    )
    state["calls"].insert(index, probe)
    state["model_calls"] += 1
    state["tokens"] += 101
    state["charged_tokens"] += 101
    return state, index


def store_trace(repo, state):
    with repo.base.sessions.begin() as session:
        session.get(ResearchRunRow, state["run_id"]).payload = state


@pytest.mark.parametrize("role", ["Analyst", "Verifier", "Writer"])
def test_paid_input_measurement_is_verified_without_becoming_an_approval(completed, role):
    repo, original, _port, _seen = completed
    state, index = measured_trace(repo, original, role)
    store_trace(repo, state)
    restored = repo.get_run(state["run_id"])
    assert restored["status"] == "COMPLETED"
    assert restored["prediction_ids"] == original["prediction_ids"]
    assert restored["model_calls"] == original["model_calls"] + 1
    assert restored["charged_tokens"] == original["charged_tokens"] + 101
    assert restored["calls"][index]["status"] == "MEASURED"


@pytest.mark.parametrize(
    "mutation",
    ["context", "key", "usage", "max_output", "status", "index", "ref", "reserve", "approval"],
)
def test_paid_measurement_and_formal_call_cannot_be_rebound_or_bypass_review(completed, mutation):
    from marketpulse.quant.contracts import canonical

    repo, original, _port, _seen = completed
    state, index = measured_trace(repo, original, "Verifier")
    probe, formal = state["calls"][index : index + 2]
    count = repo.value(probe["request_ref"])
    request = repo.value(formal["request_ref"])
    if mutation == "context":
        count["context"] = dict(count["context"], invented="未经封存的新事实")
        count["messages"][1]["content"] = canonical(count["context"])
        probe["request_ref"] = repo.blob(count)
    elif mutation == "key":
        probe["input_count_key"] = "f" * 64
    elif mutation == "usage":
        probe["charged_tokens"] += 1
    elif mutation == "max_output":
        probe["max_output_tokens"] = count["max_output_tokens"] = 2
        probe["request_ref"] = repo.blob(count)
    elif mutation == "status":
        probe["status"] = "COMPLETED"
    elif mutation == "index":
        formal["input_count_call_index"] = request["input_count_call_index"] = index + 1
        formal["request_ref"] = repo.blob(request)
    elif mutation == "ref":
        formal["input_count_ref"] = request["input_count_ref"] = repo.blob({})
        formal["request_ref"] = repo.blob(request)
    elif mutation == "reserve":
        formal["reserved_tokens"] -= 1
    else:
        formal["status"] = "MEASURED"
        formal["purpose"] = "input_count"
    store_trace(repo, state)
    with pytest.raises(ValueError):
        repo.get_run(state["run_id"])
