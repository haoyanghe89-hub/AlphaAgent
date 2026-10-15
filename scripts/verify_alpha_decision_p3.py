"""Read-only P3 acceptance: export, original arithmetic, old payloads, future nulls."""

import argparse
import hashlib
import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

import httpx

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef


def save(path, value):
    with path.open("x", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    rid = args.run_id
    with httpx.Client(base_url="http://127.0.0.1:8839", timeout=90) as client:
        last = None
        for _ in range(450):
            response = client.get("/api/quant/decision/runs/" + rid)
            response.raise_for_status()
            state = response.json()
            progress = (
                state["status"],
                state.get("model_calls"),
                state.get("process", [{}])[-1].get("detail"),
            )
            if progress != last:
                print(progress, flush=True)
                last = progress
            if state["status"] != "RUNNING":
                break
            time.sleep(2)
        save(args.output / "run.json", state)
        assert state["status"] == "COMPLETED", state.get("error")
        data = state["data"]
        assert data["report"]["stage"] == "P3"
        assert state["request"]["include_flows"]
        assert data["report"]["opinion"]["outlook"] in {"FAVORABLE", "UNFAVORABLE"}
        assert state["model_calls"] >= 4 and all(
            c.get("model") != "synthetic" for c in state["calls"] if c.get("role")
        )
        exported = client.get(f"/api/quant/decision/runs/{rid}/export")
        exported.raise_for_status()
        assert exported.json()["data"] == data
        save(args.output / "export.json", exported.json())
        chain = client.get(f"/api/quant/decision/runs/{rid}/forecast")
        chain.raise_for_status()
        save(args.output / "forecast.json", chain.json())
        for e in data["bundle"]["items"]:
            if e["source_family"] == "DECISION_FLOW_CALC":
                client.get(
                    f"/api/quant/decision/runs/{rid}/evidence/{e['evidence_id']}"
                ).raise_for_status()
        outcome = client.post(f"/api/quant/decision/runs/{rid}/observe")
        outcome.raise_for_status()
        assert all(
            o["maturity"] == "PENDING" and o["security_return"] is None
            for o in outcome.json()["outcomes"]
        )
        assert client.post(f"/api/quant/decision/runs/{rid}/observe").json() == outcome.json()
        save(args.output / "outcomes.json", outcome.json())
    root = Path(".phase3-quant-o/alpha-history-real-02")
    blobs = LocalContentAddressedBlobStorage(root / "blobs")
    original = next(
        r["source"] for r in data["flow_snapshot"]["responses"] if r["role"] == "margin"
    )
    packet = json.loads(blobs.get_bytes(BlobRef.from_uri(original["blob_ref"])))
    by_date = {r["DATE"][:10]: r for r in packet["result"]["data"]}
    vendor_source = next(
        (r["source"] for r in data["flow_snapshot"]["responses"] if r["role"] == "vendor"), None
    )
    vendor_by_date = {}
    if vendor_source:
        vendor_packet = json.loads(blobs.get_bytes(BlobRef.from_uri(vendor_source["blob_ref"])))
        vendor_by_date = {
            row.split(",")[0]: Decimal(row.split(",")[1])
            for row in vendor_packet.get("data", {}).get("klines", [])
        }
    manual = []
    for window in data["flow_report"]["windows"]:
        days, baseline = window["sessions"], window["baseline_date"]
        metrics = {m["metric_id"]: m for m in window["metrics"]}
        change = Decimal(str(by_date[days[-1]]["RZYE"])) - Decimal(str(by_date[baseline]["RZYE"]))
        buys = sum(Decimal(str(by_date[d]["RZMRE"])) for d in days)
        repays = sum(Decimal(str(by_date[d]["RZCHE"])) for d in days)
        assert Decimal(metrics["MARGIN_RECONCILIATION"]["value"]) == change - buys + repays
        for key, value in (
            ("MARGIN_CHANGE", change),
            ("MARGIN_BUY", buys),
            ("MARGIN_REPAY", repays),
        ):
            assert metrics[key]["status"] == "AVAILABLE" and Decimal(metrics[key]["value"]) == value
        assert metrics["ETF_ESTIMATE"]["value"] is None
        assert metrics["INDUSTRY_SHARE"]["value"] is None
        if metrics["VENDOR_NET"]["value"] is not None:
            assert set(days) <= set(vendor_by_date)
            assert sum(vendor_by_date[d] for d in days) == Decimal(metrics["VENDOR_NET"]["value"])
        manual.append(
            dict(
                window=window["window"],
                start=days[0],
                end=days[-1],
                baseline=baseline,
                raw_start_balance=by_date[baseline]["RZYE"],
                raw_end_balance=by_date[days[-1]]["RZYE"],
                change=str(change),
                buys=str(buys),
                repays=str(repays),
                chart_unit="CNY / 100000000 = 亿元",
            )
        )
    latest = by_date[data["flow_report"]["end_date"]]
    assert Decimal(str(latest["RZMRE5D"])) == Decimal(manual[0]["buys"])
    assert Decimal(str(latest["RZCHE5D"])) == Decimal(manual[0]["repays"])
    save(
        args.output / "manual-original-crosscheck.json",
        dict(
            windows=manual,
            five_day_published_provider_aggregate="MATCHED_SAME_PROVIDER",
            definition_reference="https://www.sse.com.cn/market/othersdata/margin/detail/index.shtml",
            independent_exchange_numeric_verification="NOT_DONE",
            coverage=data["flow_coverage"],
            checks=data["flow_checks"],
        ),
    )
    baseline = json.loads(
        Path(".phase3-quant-o/decision-p3-20261013-01/baseline.json").read_text("utf8")
    )
    with sqlite3.connect(
        "file:" + (root / "metadata.sqlite").resolve().as_posix() + "?mode=ro", uri=True
    ) as db:
        after = {
            r[0]: hashlib.sha256(r[1].encode()).hexdigest()
            for r in db.execute(
                "select run_id,payload from inv_quant_research_run where status='COMPLETED'"
            )
        }
    assert all(after.get(k) == v for k, v in baseline.items())
    result = dict(
        run_id=rid,
        model_calls=state["model_calls"],
        tokens=state["tokens"],
        outlook=data["report"]["opinion"]["outlook"],
        unchanged_completed=len(baseline),
        prediction_validity="UNVERIFIED",
        user_acceptance="PENDING",
        manual=manual,
    )
    save(args.output / "acceptance.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
