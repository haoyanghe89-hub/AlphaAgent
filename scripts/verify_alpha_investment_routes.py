"""Live API acceptance; real market inputs and explicitly synthetic capital profiles."""

import argparse
import hashlib
import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import requests

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.contracts import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    stage = args.evidence.resolve()
    if not stage.is_relative_to(Path.cwd().resolve() / ".phase3-quant-o"):
        raise ValueError("private evidence path required")
    baseline = json.loads((stage / "baseline.json").read_text(encoding="utf8"))
    database = Path(baseline["database"])
    blobs = LocalContentAddressedBlobStorage(database.parent / "blobs")
    root = "http://127.0.0.1:8839/api/quant/investment-plans"
    results = []
    for capital in (None, 50000, 500000, 5000000):
        label = "no-profile" if capital is None else str(capital)
        path = stage / ("plan-" + label + ".json")
        if path.is_file():
            request = json.loads(path.read_text(encoding="utf8"))["request"]
        else:
            request = dict(
                request_key=str(uuid4()),
                entities=["000333", "600036", "601088", "600009"],
                horizon="SWING",
                profile=None
                if capital is None
                else dict(
                    capital=str(capital),
                    available_cash=str(capital),
                    input_origin="SYNTHETIC_ACCEPTANCE",
                ),
            )
            path.write_text(json.dumps(dict(request=request)), encoding="utf8")
        response = requests.post(root, json=request, timeout=60)
        response.raise_for_status()
        state = response.json()
        deadline = time.monotonic() + 910
        while state["status"] == "RUNNING" and time.monotonic() < deadline:
            time.sleep(2)
            response = requests.get(root + "/" + state["run_id"], timeout=60)
            response.raise_for_status()
            state = response.json()
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8")
        assert state["status"] == "COMPLETED", state.get("error")
        report = state["data"]
        assert len({c["industry_code"] for c in report["candidates"]}) == 4
        packet = json.loads(blobs.get_bytes(BlobRef.from_uri(state["inputs_ref"])))
        assert digest(packet) == report["input_digest"]
        for stock, feature in zip(packet["stocks"], report["features"]["stocks"], strict=True):
            factors = {r["trade_date"]: Decimal(str(r["adj_factor"])) for r in stock["adjustments"]}
            rows = sorted(stock["daily"], key=lambda r: r["trade_date"])
            close = [
                Decimal(str(r["close"])) * factors[r["trade_date"]] / factors[packet["market_date"]]
                for r in rows
            ]
            assert abs(
                Decimal(feature["metrics"]["ma_60"]["value"]) - sum(close[-60:]) / 60
            ) < Decimal("1e-24")
            assert abs(
                Decimal(feature["metrics"]["return_20"]["value"]) - (close[-1] / close[-21] - 1)
            ) < Decimal("1e-24")
        bank = next(c for c in report["candidates"] if c["security_id"] == "600036.SH")
        assert bank["financials"]["scope"] == "FINANCIAL_NOT_APPLICABLE"
        assert bank["routes"][1]["status"] == "NOT_APPLICABLE"
        portfolio = report["portfolio"]
        if capital is None:
            assert portfolio["plans"] == []
        else:
            assert Decimal(portfolio["reserved_cash"]) <= capital
            assert sum(
                Decimal(p.get("estimated_risk", "0")) for p in portfolio["plans"]
            ) <= Decimal(capital) * Decimal(".02")
            assert all(p["shares"] % 100 == 0 for p in portfolio["plans"])
        # Reposting the exact request cannot create another forecast or overwrite its result.
        again = requests.post(root, json=request, timeout=60)
        again.raise_for_status()
        assert again.json()["result_ref"] == state["result_ref"]
        results.append(
            dict(
                profile=label,
                run_id=state["run_id"],
                market_date=report["market_date"],
                portfolio=portfolio,
            )
        )
        print(
            json.dumps(
                dict(profile=label, run_id=state["run_id"], status=state["status"]),
                ensure_ascii=False,
            ),
            flush=True,
        )
    with sqlite3.connect(database) as connection:
        for run_id, expected in baseline["runs"].items():
            raw = connection.execute(
                "select payload from inv_quant_research_run where run_id=?", (run_id,)
            ).fetchone()[0]
            assert hashlib.sha256(raw.encode()).hexdigest() == expected, run_id
        for table, expected in baseline["knowledge_tables"].items():
            rows = connection.execute('select * from "' + table + '" order by rowid').fetchall()
            assert [list(r) for r in rows] == expected, table
    (stage / "acceptance.json").write_text(
        json.dumps(
            dict(
                results=results, old_runs_unchanged=len(baseline["runs"]), knowledge_unchanged=True
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf8",
    )


if __name__ == "__main__":
    main()
