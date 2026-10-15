"""Tushare-shaped synthetic tables retain original locators and frozen legacy reports."""

import copy
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from test_candidates_api import completed, payload, source_fixture
from test_decision_api import setup as setup
from test_manufacturing_candidates import ManufacturingPort

from marketpulse.quant.decision import candidate_sources, screening, tushare, tushare_candidates
from marketpulse.quant.decision.sector_profiles import profile


def synthetic_tushare(repo):
    legacy = source_fixture(repo, manufacturing=True)
    snapshot = copy.deepcopy(legacy)
    snapshot.update(
        version=tushare_candidates.VERSION,
        board_id="SYNTHETIC.SI",
        entries=[],
        excluded_market_members=[],
        market_at=legacy["cutoff_at"],
        sources=[
            s
            for s in legacy["sources"]
            if not s["source_family"].startswith("EASTMONEY") and s["source_family"] != "TENCENT"
        ],
    )
    original = {e["role"]: repo.value(e["source"]["blob_ref"]) for e in legacy["entries"]}
    members = original["membership"]["data"]["diff"]

    def entry(role, api, params, rows):
        fields = list(rows[0])
        packet = dict(code=0, data=dict(fields=fields, items=[list(r.values()) for r in rows]))
        ref = repo.raw(json.dumps(packet).encode())
        source = dict(
            key=role,
            source_family="TUSHARE",
            url=tushare.URL + "/",
            api_name=api,
            parameters=params,
            fetched_at=legacy["cutoff_at"],
            blob_ref=ref,
            sha256=ref.rsplit("/", 1)[-1],
        )
        snapshot["entries"].append(dict(role=role, source=source))
        snapshot["sources"].append(source)

    entry(
        "classification",
        "index_classify",
        {},
        [dict(index_code="SYNTHETIC.SI", industry_name="集成电路制造", level="L3", src="SW2021")],
    )
    entry(
        "master",
        "stock_basic",
        {},
        [
            dict(ts_code=r["f12"] + ".SH", name=r["f14"], list_status="L", list_date="20180101")
            for r in members
        ],
    )
    entry(
        "membership",
        "index_member_all",
        {},
        [
            dict(
                ts_code=r["f12"] + ".SH",
                name=r["f14"],
                l3_code="SYNTHETIC.SI",
                l3_name="集成电路制造",
                is_new="Y",
                out_date=None,
            )
            for r in members
        ],
    )
    for member in members:
        code, sid = member["f12"], member["f12"] + ".SH"
        values = original["daily:" + code]["data"]["sh" + code]["day"]
        entry(
            "daily:" + code,
            "daily",
            dict(ts_code=sid),
            [
                dict(
                    ts_code=sid,
                    trade_date=r[0].replace("-", ""),
                    open=r[1],
                    close=r[2],
                    high=r[3],
                    low=r[4],
                    vol=r[5],
                    amount=str(Decimal(r[8]) * 10),
                )
                for r in values
            ],
        )
        entry(
            "valuation:" + code,
            "daily_basic",
            {},
            [
                dict(
                    ts_code=sid,
                    trade_date=snapshot["sessions"][-1].replace("-", ""),
                    pe_ttm=member["f9"],
                    total_mv=str(Decimal(member["f20"]) / 10000),
                )
            ],
        )
        for role, (api, mapping) in tushare_candidates.FINANCIAL.items():
            row = next(r for r in original[role]["result"]["data"] if r["SECUCODE"] == sid)
            entry(
                role + ":" + sid,
                api,
                {},
                [
                    dict(
                        ts_code=sid,
                        end_date="20260630",
                        ann_date="20260820",
                        f_ann_date="20260820",
                        report_type="1",
                        comp_type="1",
                        **{original: row[target] for original, target in mapping.items()},
                    )
                ],
            )
        entry(
            "profile:" + code,
            "stock_company",
            {},
            [
                dict(
                    ts_code=sid,
                    main_business="合成晶圆制造代工业务",
                    introduction="合成公司简介",
                    business_scope="合成测试夹具",
                )
            ],
        )
    return snapshot


def test_tushare_candidate_units_locators_scope_and_legacy_retention(setup):
    app, _ = setup
    repo = app.state.decision_store
    legacy = source_fixture(repo, manufacturing=True)
    old = candidate_sources.rebuild(repo, legacy)
    snapshot = synthetic_tushare(repo)
    new = candidate_sources.rebuild(repo, snapshot)
    assert new["data_supply"]["data_source"] == "Tushare"
    assert new["universe"]["classification_version"].startswith("SW2021:L3:")
    assert {k: Decimal(v) for k, v in new["members"][0]["metrics"].items()} == {
        k: Decimal(v) for k, v in old["members"][0]["metrics"].items()
    }
    assert new["selected"] == old["selected"]
    assert candidate_sources.rebuild(repo, legacy) == old
    for fact in new["bundle"]["items"]:
        if fact["source_family"] == "TUSHARE":
            assert fact["locator"]["path"].startswith("/data/items/")
    ctx = screening.context(new, 3, screening.TUSHARE_PROMPT_VERSION)
    assert ctx["business_profile"] == profile("集成电路制造")
    assert "过去十二个月市盈率" in screening.TUSHARE_PROMPTS["Analyst"]


def test_latest_revision_uses_original_row_locator_and_legacy_keeps_conflict(setup):
    app, _ = setup
    repo = app.state.decision_store
    snapshot = synthetic_tushare(repo)
    source = next(e["source"] for e in snapshot["entries"] if e["role"] == "income:688012.SH")
    raw = tushare_candidates.json_packet(repo, source)
    raw["data"]["fields"].append("update_flag")
    raw["data"]["items"][0].append("0")
    updated = list(raw["data"]["items"][0])
    updated[-1] = "1"
    updated[raw["data"]["fields"].index("total_revenue")] = 120
    raw["data"]["items"].append(updated)
    ref = repo.raw(json.dumps(raw).encode())
    source.update(blob_ref=ref, sha256=ref.rsplit("/", 1)[-1])
    data = candidate_sources.rebuild(repo, snapshot)
    row = next(r for r in data["members"] if r["security_id"] == "688012.SH")
    assert row["core_complete"] and Decimal(row["metrics"]["TOTAL_OPERATE_INCOME"]) == 120
    fact = next(
        e
        for e in data["bundle"]["items"]
        if e["evidence_id"] == "EV-CAND-688012.SH-TOTAL_OPERATE_INCOME"
    )
    assert fact["locator"]["path"].startswith("/data/items/1/")
    from marketpulse.quant.decision.contracts import EvidenceItem

    assert tushare_candidates.verified_numeric(repo, EvidenceItem.model_validate(fact)) == 120
    legacy = copy.deepcopy(snapshot)
    legacy["version"] = tushare_candidates.LEGACY_VERSION
    old = candidate_sources.rebuild(repo, legacy)
    assert not next(r for r in old["members"] if r["security_id"] == "688012.SH")["core_complete"]
    assert (
        len(
            tushare_candidates.select_revision(
                [
                    (0, dict(NOTICE_DATE="2026-08-20", PROVIDER_UPDATE_FLAG="1")),
                    (1, dict(NOTICE_DATE="2026-08-20", PROVIDER_UPDATE_FLAG="1")),
                ],
                "2026-10-14",
            )
        )
        == 2
    )


def test_tushare_manufacturing_publication_still_requires_four_roles(setup):
    app, _ = setup
    app.state.decision_model_port = ManufacturingPort()
    app.state.settings.model_read_timeout_seconds = 30
    app.state.decision_candidate_collector = lambda repo, *args: synthetic_tushare(repo)
    with TestClient(app) as client:
        state = completed(client, payload() | dict(entity="集成电路制造"))
        assert state["status"] == "COMPLETED", state.get("error")
        assert state["model_calls"] == 4
        assert state["data"]["report"]["prompt_version"] == screening.TUSHARE_PROMPT_VERSION
        repo = app.state.decision_store
        proposal = screening.CandidateAnalysis.model_validate(
            repo.value(state["data"]["analysis_ref"])
        )
        ctx = repo.value(state["data"]["analysis_context_ref"])
        with pytest.raises(ValueError, match="explicit conditional outlooks"):
            screening.guard_analysis(
                proposal.model_copy(update=dict(summary="本轮不构成方向判断。")), ctx
            )
        valuation = proposal.candidates[0].model_copy(
            update=dict(valuation="市盈率快照偏高，缺比较基准。")
        )
        with pytest.raises(ValueError, match="valuation level"):
            screening.guard_analysis(
                proposal.model_copy(update=dict(candidates=(valuation, *proposal.candidates[1:]))),
                ctx,
            )
        unsupported = proposal.candidates[0].model_copy(
            update=dict(reason="公司已经满产，产能利用率提升。")
        )
        with pytest.raises(ValueError, match="manufacturing operating"):
            screening.guard_analysis(
                proposal.model_copy(
                    update=dict(candidates=(unsupported, *proposal.candidates[1:]))
                ),
                ctx,
            )
        assert (
            client.get("/api/quant/decision/runs/" + state["run_id"] + "/export").status_code == 200
        )
