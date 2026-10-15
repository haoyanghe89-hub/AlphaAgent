"""Synthetic sources verify candidate safety; never count as a real recommendation."""

import copy
import json
import time
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_decision_api import setup  # noqa: F401
from test_flow_integration import ANNUAL

from marketpulse.quant.contracts import digest
from marketpulse.quant.decision import agents, screening
from marketpulse.quant.decision import candidate_sources as sources
from marketpulse.quant.decision.next_session.calendar import parse_annual
from marketpulse.quant.decision.sector_profiles import profile

STAMP = "2026-10-13T10:00:00+00:00"


def source_fixture(repo, *, originals=True, missing=False, manufacturing=False):
    entries, descriptors, docs, subjects = [], [], [], []

    def source(key, value, family, url):
        ref = repo.raw(value if isinstance(value, bytes) else json.dumps(value).encode())
        result = dict(
            key=key,
            url=url,
            source_family=family,
            blob_ref=ref,
            sha256=ref.rsplit("/", 1)[-1],
            fetched_at=STAMP,
        )
        descriptors.append(result)
        return result

    def entry(role, value, family="EASTMONEY_FINANCIAL", url=sources.EM):
        result = source(role, value, family, url)
        entries.append(dict(role=role, source=result))

    annual = source("calendar_SSE", ANNUAL.encode(), "SSE", "https://www.sse.com.cn/calendar")
    sessions = [str(s) for s in parse_annual(ANNUAL, 2026)]
    window = [s for s in sessions if s <= "2026-10-13"][-21:]
    entry(
        "classification",
        dict(
            data=dict(
                total=1,
                diff=[dict(f12="BK0001", f14="集成电路制造" if manufacturing else "半导体设备")],
            )
        ),
        "EASTMONEY_CLASSIFICATION",
        sources.LIST,
    )
    members = [
        dict(f12=code, f13=1, f14=name, f20=cap, f9=20)
        for code, name, cap in [("688012", "合成甲", 200), ("688082", "合成乙", 100)]
    ]
    entry(
        "membership",
        dict(data=dict(total=2, diff=members)),
        "EASTMONEY_CLASSIFICATION",
        sources.LIST,
    )
    financial = []
    for index, member in enumerate(members):
        code, name = member["f12"], member["f14"]
        sid = code + ".SH"
        daily = [[d, "10", "10", "11", "9", "100", "0", "0", str(index + 1)] for d in window]
        if missing:
            daily.pop()
        entry("daily:" + code, dict(data={"sh" + code: dict(day=daily)}), "TENCENT", sources.KLINE)
        financial.append(
            dict(
                SECUCODE=sid,
                SECURITY_CODE=code,
                REPORT_DATE="2026-06-30 00:00:00",
                NOTICE_DATE="2026-08-20",
                TOTAL_OPERATE_INCOME=100,
                PARENT_NETPROFIT=20,
                NETCASH_OPERATE=5,
                TOTAL_ASSETS=200,
                TOTAL_LIABILITIES=60,
            )
        )
        if originals:
            subjects.append(dict(security_id=sid, code=code, name=name))
            source(
                "profile",
                dict(
                    jbzl=[
                        dict(
                            SECURITY_CODE=code,
                            SECURITY_NAME_ABBR=name,
                            ORG_PROFILE="合成晶圆制造企业主营晶圆代工。"
                            if manufacturing
                            else "合成设备企业主营刻蚀设备研发制造。",
                        )
                    ]
                ),
                "EASTMONEY_PROFILE",
                "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax",
            )
            raw = source(
                "pdf_" + code,
                ("%PDF-synthetic:" + code).encode(),
                "CNINFO",
                "https://static.cninfo.com.cn/test.pdf",
            )
            parsed = dict(
                parser_version="pypdf-pages-v1",
                subject_id=sid,
                pages=[
                    (
                        (
                            "合成夹具，非真实经营资料。公司主营晶圆制造代工业务，产能利用率与资本开支有风险。"
                            if manufacturing
                            else "合成夹具，非真实经营资料。公司主营刻蚀设备业务，研发设备收入与现金回收存在风险。"
                        )
                        * 12
                    )
                ],
            )
            text_ref = repo.blob(parsed)
            docs.append(
                dict(
                    document_id="DOC-" + code,
                    subject_id=sid,
                    title="合成半年度报告",
                    category="periodic",
                    original_source_id="SYNTHETIC:" + code,
                    url=raw["url"],
                    raw_ref=raw["blob_ref"],
                    content_hash=raw["sha256"],
                    text_ref=text_ref,
                    text_hash=digest(parsed),
                    page_count=1,
                    published_at=STAMP,
                    fetched_at=STAMP,
                    available_at=STAMP,
                    time_precision="day",
                    period_start="2026-01-01",
                    period_end="2026-06-30",
                    limitations=["合成测试夹具"],
                    aliases=[],
                )
            )
    for key in ("income", "balance", "cash"):
        entry(key, dict(result=dict(data=financial)))
    return dict(
        version=sources.VERSION_V2 if manufacturing else sources.VERSION,
        **({"industry_profile": profile("集成电路制造")} if manufacturing else {}),
        entries=entries,
        documents=docs,
        subjects=subjects,
        board_id="BK0001",
        calendar=dict(sessions=sessions, source_refs=[annual["blob_ref"]]),
        sessions=window,
        period="2026-06-30",
        cutoff_at=STAMP,
        sources=descriptors,
        gaps=[],
        depth_limit=3,
    )


class Port:
    reject = False

    async def generate(self, request):
        ctx = json.loads(request.messages[-1].content.split("\n上次检查失败")[0])
        schema = request.response_model
        if schema is agents.Plan:
            output = schema(focus_questions=("核查同业差异和反证",))
        elif schema is screening.CandidateAnalysis:
            candidates = []
            for sid in ctx["selected"]:
                own = next(c for c in ctx["companies"] if c["security_id"] == sid)
                candidates.append(
                    dict(
                        security_id=sid,
                        outlook="FAVORABLE",
                        stance="WAIT_FOR_CONFIRMATION",
                        summary="倾向看多，公司披露有支持，仍需核查现金回收。",
                        reason="支持与风险并存，保留条件取舍。",
                        supporting_reasons=["公司披露有设备业务原文。"],
                        opposing_reasons=["重大风险覆盖未完整核定。"],
                        funding="融资渠道资料不足，不推机构意图。",
                        valuation="供应商估值快照不能证明便宜。",
                        comparison="同业业务可能不同，仅做同期间合并报表对照。",
                        peer_ids=[p for p in ctx["selected"] if p != sid],
                        evidence_ids=own["business_evidence_ids"][:1],
                        counter_ids=[],
                        verification_conditions=["后续披露现金回收改善。"],
                        invalidation_conditions=["后续披露风险扩大。"],
                    )
                )
            output = schema(summary="本轮按证据形成有限候选，资料缺项保留。", candidates=candidates)
        elif schema is screening.CandidateReview:
            output = schema(
                decisions=[
                    dict(
                        security_id=c["security_id"],
                        verdict="REJECT" if self.reject else "APPROVE",
                        reason="合成输入复核，不代表投资有效。",
                    )
                    for c in ctx["proposal"]["candidates"]
                ],
                summary_supported=True,
            )
        else:
            output = schema.model_validate(ctx)
        return SimpleNamespace(
            output=output,
            provider="synthetic",
            model="synthetic",
            response_id="synthetic",
            usage=SimpleNamespace(input_tokens=100, output_tokens=100),
        )


def completed(client, payload):
    response = client.post("/api/quant/decision/runs", json=payload)
    assert response.status_code == 202, response.text
    rid = response.json()["run_id"]
    for _ in range(300):
        value = client.get("/api/quant/decision/runs/" + rid).json()
        if value["status"] != "RUNNING":
            return value
        time.sleep(0.01)
    pytest.fail("candidate fixture did not finish")


def payload():
    return dict(
        request_key=str(uuid4()),
        entity="半导体设备",
        scope="industry",
        research_mode="candidates",
        question="测试候选和同业对照，不是真实推荐",
        horizon_months=3,
    )


@pytest.fixture
def candidate_app(setup):  # noqa: F811
    app, base = setup
    app.state.decision_candidate_collector = lambda repo, *args: source_fixture(repo)
    port = Port()
    app.state.decision_model_port = port
    app.state.settings.model_read_timeout_seconds = 30
    return app, base, port


def test_candidate_publication_scope_budget_citations_and_tamper(candidate_app):
    app, _, _ = candidate_app
    with TestClient(app) as client:
        request = payload()
        result = completed(client, request)
        assert result["status"] == "COMPLETED", result.get("error")
        assert result["model_calls"] == 4 and result["tokens"] == 800
        data, repo = result["data"], app.state.decision_store
        assert data["coverage"]["universe_total"] == 2
        assert data["selected"] == ["688082.SH", "688012.SH"]
        assert all(r["risk_status"] == "UNKNOWN" for r in data["members"])
        assert all(r["leader_type"] != "BUSINESS" for r in data["members"])
        assert client.post("/api/quant/decision/runs", json=request).json() == result
        assert (
            client.post(
                "/api/quant/decision/runs", json=dict(request, question="different")
            ).status_code
            == 409
        )
        assert client.get(f"/api/quant/decision/runs/{result['run_id']}/export").json() == result
        evidence = data["report"]["candidates"][0]["evidence_ids"][0]
        assert (
            client.get(
                f"/api/quant/decision/runs/{result['run_id']}/evidence/{evidence}"
            ).status_code
            == 200
        )
        for key in ("members", "report", "screening_result"):
            changed = copy.deepcopy(data)
            changed[key] = []
            with pytest.raises((ValueError, TypeError)):
                repo.verify_data(changed, digest(changed))
        proposal = screening.CandidateAnalysis.model_validate(repo.value(data["analysis_ref"]))
        ctx = repo.value(data["analysis_context_ref"])
        bad = proposal.model_copy(update={"candidates": tuple(reversed(proposal.candidates))})
        with pytest.raises(ValueError):
            screening.guard_analysis(bad, ctx)
        first, second = proposal.candidates
        different = copy.deepcopy(ctx)
        different["companies"][1]["business_group"] = "EQUIPMENT_TEST"
        limited = proposal.model_copy(
            update={
                "candidates": (
                    first.model_copy(
                        update={
                            "comparison": "产品结构不可简单混排盈利指标，可比对象有限。",
                            "peer_ids": (),
                        }
                    ),
                    second.model_copy(
                        update={
                            "comparison": "产品结构不可简单混排盈利指标，可比对象有限。",
                            "peer_ids": (),
                        }
                    ),
                )
            }
        )
        screening.guard_analysis(limited, different)
        bad = proposal.model_copy(
            update={
                "candidates": (
                    first.model_copy(update={"evidence_ids": second.evidence_ids}),
                    second,
                )
            }
        )
        with pytest.raises(ValueError):
            screening.guard_analysis(bad, ctx)


def test_no_originals_or_missing_sessions_produce_zero_candidates(candidate_app):
    app, _, _ = candidate_app
    for kwargs in (dict(originals=False), dict(missing=True)):
        app.state.decision_candidate_collector = lambda repo, *args, options=kwargs: source_fixture(
            repo, **options
        )
        with TestClient(app) as client:
            result = completed(client, payload())
            assert result["status"] == "COMPLETED", result.get("error")
            assert result["data"]["report"]["candidates"] == []
            assert result["data"]["coverage"]["missing"] == 2


def test_rejected_review_and_invalid_scope_cannot_publish(candidate_app):
    app, _, port = candidate_app
    port.reject = True
    with TestClient(app) as client:
        result = completed(client, payload())
        assert result["status"] == "FAILED" and not result.get("result_ref")
        assert result["model_calls"] == 3
        assert (
            client.post(
                "/api/quant/decision/runs", json=dict(payload(), mode="historical")
            ).status_code
            == 422
        )
        assert (
            client.post("/api/quant/decision/runs", json=dict(payload(), entity="银行")).status_code
            == 422
        )


def test_cross_period_financial_and_duplicate_members_are_not_admitted(candidate_app):
    app, _, _ = candidate_app
    repo = app.state.decision_store
    snapshot = source_fixture(repo)
    cash = next(e for e in snapshot["entries"] if e["role"] == "cash")
    packet = json.loads(repo.raw_bytes(cash["source"]["blob_ref"]))
    packet["result"]["data"][0]["REPORT_DATE"] = "2026-03-31"
    ref = repo.blob(packet)
    cash["source"].update(blob_ref=ref, sha256=ref.rsplit("/", 1)[-1])
    data = sources.rebuild(repo, snapshot)
    assert "688012.SH" not in data["selected"]
    member = next(e for e in snapshot["entries"] if e["role"] == "membership")
    packet = json.loads(repo.raw_bytes(member["source"]["blob_ref"]))
    packet["data"]["diff"][1] = packet["data"]["diff"][0]
    ref = repo.blob(packet)
    member["source"].update(blob_ref=ref, sha256=ref.rsplit("/", 1)[-1])
    with pytest.raises(ValueError, match="duplicate"):
        sources.rebuild(repo, snapshot)


def test_candidate_batch_uses_shared_lease_and_late_collection_cannot_publish(candidate_app):
    app, _, _ = candidate_app
    started, release = Event(), Event()

    def blocked(repo, *args):
        started.set()
        assert release.wait(3)
        return source_fixture(repo)

    app.state.decision_candidate_collector = blocked
    with TestClient(app) as client:
        result = client.post("/api/quant/decision/runs", json=payload()).json()
        assert started.wait(1)
        assert client.post("/api/quant/decision/runs", json=payload()).status_code == 429
        rid = result["run_id"]
        assert client.post(f"/api/quant/decision/runs/{rid}/cancel").status_code == 200
        release.set()
        time.sleep(0.1)
        state = client.get(f"/api/quant/decision/runs/{rid}").json()
        assert state["status"] == "CANCELLED" and not state.get("result_ref")


def test_collection_reads_verified_calendar_coverage_fields(candidate_app, monkeypatch):
    from marketpulse.quant.decision.next_session.contracts import VerifiedSessionCalendar

    app, _, _ = candidate_app
    repo = app.state.decision_store
    fixture = source_fixture(repo, originals=False)
    calendar = VerifiedSessionCalendar(
        exchange="SSE",
        coverage_start="2026-01-01",
        coverage_end="2026-12-31",
        sessions=fixture["calendar"]["sessions"],
        source_refs=fixture["calendar"]["source_refs"],
        verified_at=STAMP,
        content_hash="0" * 64,
    )

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, key, url, **kwargs):
            role = (
                "classification"
                if key.startswith("candidate_boards")
                else "membership"
                if key == "candidate_members"
                else None
            )
            if role is None:
                raise sources.SourceUnavailable("synthetic missing data")
            entry = next(e for e in fixture["entries"] if e["role"] == role)
            return entry["source"], json.loads(repo.raw_bytes(entry["source"]["blob_ref"]))

    monkeypatch.setattr(sources, "SourceClient", Client)
    monkeypatch.setattr(sources, "load_calendar", lambda *args: calendar)
    state = dict(request=dict(budget_profile=dict(max_candidates=3)), calls=[])
    snapshot = sources.collect(repo, state, Event(), lambda: None, lambda *args: None)
    assert len(snapshot["sessions"]) == 21
    assert not snapshot["subjects"]


@pytest.mark.parametrize("text", ["估值快照偏高，需验证。", "市盈率偏低，值得研究。"])
def test_valuation_without_benchmark_cannot_assert_a_level(text):
    with pytest.raises(ValueError, match="benchmark"):
        screening.guard_valuation(text)
    screening.guard_valuation("当前估值快照不能判断便宜，比较基准尚未核定。")


def test_known_member_identity_is_checked_directly_without_fuzzy_search():
    member = dict(code="688012", security_id="688012.SH", name="合成甲")
    profile = dict(
        SECURITY_CODE="688012",
        SECURITY_NAME_ABBR="合成甲",
        SECURITY_TYPE="A股",
        EM2016="半导体设备",
        ORG_PROFILE="合成测试公司主营设备。",
    )

    class Client:
        def fetch(self, key, url, **kwargs):
            assert key == "profile" and url == sources.PROFILE
            assert kwargs["params"] == {"code": "SH688012"}
            return {}, dict(jbzl=[profile])

    assert sources.resolve_member(Client(), member)["security_id"] == "688012.SH"
    profile["SECURITY_CODE"] = "688082"
    with pytest.raises(ValueError, match="identity"):
        sources.resolve_member(Client(), member)
