"""Publish local acceptance only after fresh tests, readback, and immutable-record checks."""

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import requests
from evaluate_alpha_p7 import repository

from marketpulse.quant.decision.store import fingerprint
from marketpulse.quant.decision.tushare import load_token


def main():
    stage = Path(".phase3-quant-o/decision-p6-p7-real-01")
    root = Path(".phase3-quant-o/alpha-history-real-02").resolve()
    log = (stage / "quant-release-final-08.log").read_text(encoding="utf8")
    match = re.search(r"(\d+) passed, 1 warning in", log)
    assert match and int(match[1]) >= 536 and not re.search(r"\d+ failed|\d+ error", log)
    assert "pass 91" in (stage / "frontend-final-08-test.log").read_text(encoding="utf8")
    assert "built in" in (stage / "frontend-final-08-build.log").read_text(encoding="utf8")
    with sqlite3.connect(root / "metadata.sqlite") as db:
        rows = db.execute("select run_id,status,payload from inv_quant_research_run").fetchall()
    assert not any(s == "RUNNING" for _, s, _ in rows)
    baseline = json.loads((stage / "baseline.json").read_text(encoding="utf8"))
    current = {rid: hashlib.sha256(p.encode()).hexdigest() for rid, _, p in rows}
    assert all(current.get(rid) == value for rid, value in baseline.items())
    single = json.loads((stage / "single-bounded-final.json").read_text(encoding="utf8"))
    assert single["status"] == "COMPLETED" and single["data"].get("flow_alignment")
    assert all(
        next(m for m in w["metrics"] if m["metric_id"] == "MARGIN_CHANGE")["value"] is not None
        for w in single["data"]["flow_report"]["windows"]
    )
    market = json.loads((stage / "market-refresh-final.json").read_text(encoding="utf8"))["run_id"]
    candidates = "RUN-DEC-eaf634157eac4ff6863530db7030cf64"
    ids = [
        market,
        candidates,
        single["run_id"],
        "RUN-DEC-39ad6f80812d4fe5ad9cca0e4c366086",
        "RUN-DEC-d259103fe2f64d84a4e874dcecefbad2",
        "RUN-DEC-74c0bc90522a4d71828c877b8f6be53b",
        "RUN-DEC-cfcc09838d704db5b3f964d69bc7f07c",
        "RUN-DEC-5da2f8d615ff41699c5c032888c8f033",
    ]
    checks = []
    for rid in ids:
        base = "http://127.0.0.1:8839/api/quant/decision/runs/" + rid
        a = requests.get(base, timeout=60)
        b = requests.get(base + "/export", timeout=60)
        assert a.status_code == b.status_code == 200 and a.json() == b.json()
        checks.append(dict(run_id=rid, read_export_equal=True))
    for path in (
        "/api/quant/delivery",
        "/api/quant/strategies/schema",
        "/api/quant/research/comparisons/schema",
    ):
        assert requests.get("http://127.0.0.1:8839" + path, timeout=30).status_code == 200
    score = requests.get(
        "http://127.0.0.1:8839/api/quant/decision/experiments/next-session/scoreboard", timeout=60
    ).json()
    assert sum(g["outcome_counts"]["SCORABLE"] for g in score["groups"]) == 4
    assert all(
        g["cohort"] == "EXPLORATORY" and g["statistics"]["independent_dates"] == 1
        for g in score["groups"]
    )
    gates = json.loads((stage / "model-v4/gates-report.json").read_text(encoding="utf8"))
    ablation = json.loads(
        (stage / "ablations-batch-02/ablations-report-total.json").read_text(encoding="utf8")
    )
    assert gates["executed"] == gates["allowed_behavior_met"] == 48 and not any(
        gates["violations"].values()
    )
    assert ablation["cases"] == 6 and ablation["metrics"]["genuine_initial_gap"] == 6
    token = load_token().encode()
    checked = 0
    for folder in (root / "blobs", stage):
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix not in (".sqlite", ".log", ".png"):
                assert not token or token not in path.read_bytes(), "credential present in archive"
                checked += 1
    repo = repository(root)
    report = dict(
        schema_version="decision-release-v1",
        status="SELF_TESTED",
        executed=48,
        generated_at=datetime.now(UTC).isoformat(),
        implementation=fingerprint(),
        software=f"P6功能与P7统一自测通过；quant {match[1]}项、前端91项及构建通过",
        sources="设备与制造范围内真实行情、财务、原文及融资；未覆盖全市场，来源许可与公布时滞分别记录",
        summary="已接通市场观察、行业候选、单股行业/资金研究、原观点、手动更新和到期核对。开发记录可在开发者视图查看。",
        predictive_validity="UNVERIFIED",
        user_acceptance="PENDING",
        external_review="NOT_REVIEWED",
        method_review="AI_AND_PROGRAM_REVIEW",
        legacy_52="NOT_PASSED",
        recommendation_entry="NOT_ENABLED_RESEARCH_ONLY",
        links=dict(
            market="#/market/" + market,
            candidates="#/candidates/" + candidates,
            single="#/decision/" + single["run_id"],
            daily="#/decision/RUN-NS-9f77c5a29b3345e4a4f04612bb46791e",
        ),
        evaluation=dict(
            gates=gates,
            ablation=ablation,
            split_notice=(
                "Frozen splits retained; cases were inspected and rerun for regression, "
                "not an untouched holdout or predictive score."
            ),
        ),
        verification=dict(
            unchanged_original_records=len(baseline),
            api_read_export=checks,
            credential_archives_checked=checked,
            quant_log="quant-release-final-08.log",
            frontend_test="frontend-final-08-test.log",
            frontend_build="frontend-final-08-build.log",
        ),
        daily=dict(
            coverage=score["coverage"],
            planned=score["planned"],
            missed=score["missed"],
            groups=score["groups"],
        ),
        limitations=[
            "申万指数日线权限未取得；主题ETF仅作代理。",
            "未披露订单、验收、可靠一致预期与规范化全收益资料仍不能由供应商接通推出。",
            "20/40/60交易日观察未成熟；2027日历未核定的日期不猜。",
            "同模型审核不是外部独立审计；六个补证问题只涉及三家公司。",
            "旧5.2数据与独立全收益门禁未降低，未自动交易或创建定时任务。",
        ],
    )
    ref = repo.blob(report)
    pointer = root / "decision-release.json"
    temp = pointer.with_name(pointer.name + "." + uuid4().hex + ".tmp")
    temp.write_text(json.dumps(dict(report_ref=ref)), encoding="utf8")
    temp.replace(pointer)
    (stage / "acceptance-final.json").write_text(
        json.dumps(dict(report, report_ref=ref), ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(
        json.dumps(
            dict(
                status=report["status"],
                report_ref=ref,
                quant=int(match[1]),
                old_records_unchanged=len(baseline),
                archives_checked=checked,
                single=single["run_id"],
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
