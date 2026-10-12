"""Generate current-code reports from explicitly selected real frozen windows."""

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def api(url, path, payload=None):
    request = Request(
        url + "/api" + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def prepare(url, output, start, end):
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path not in {"", "/"}:
        raise ValueError("local loopback preview URL required")
    output = output.resolve()
    if not output.is_relative_to(ROOT / ".phase3-quant-o") or output.exists():
        raise ValueError("new private manifest directory required; never blindly repeat POSTs")
    overview = api(url, "/quant/overview")
    plans = []
    for stock in overview["stocks"]:
        windows = api(url, "/quant/instruments/" + stock["instrument_id"] + "/windows")
        matches = [w for w in windows if (w["date_start"], w["date_end"]) == (start, end)]
        if len(matches) != 1 or "raw" not in matches[0]["adjustments"]:
            raise ValueError(f"ambiguous or unavailable explicit window for {stock['code']}")
        plans.append((stock, matches[0]))
    if not plans:
        raise ValueError("no real registered instruments")
    output.mkdir(parents=True)
    manifest = dict(url=url, date_start=start, date_end=end, mode="historical_frozen", studies=[])

    def save():
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    for stock, window in plans:
        body = dict(
            instrument=stock["instrument_id"],
            question="全面分析" + stock["name"] + " · 历史冻结研究",
            window="1y",
            date_start=start,
            date_end=end,
            asof=window["asof"],
            adjustment="raw",
            depth="quick",
            frozen_snapshot_ids=window["frozen_snapshot_ids"],
        )
        entry = dict(code=stock["code"], name=stock["name"], request=body, status="SUBMITTING")
        manifest["studies"].append(entry)
        save()  # A lost response is not permission to submit a second job.
        started = api(url, "/quant/runs", body)
        entry.update(run_id=started["run_id"], status="RUNNING")
        save()
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            detail = api(url, "/quant/runs/" + entry["run_id"])
            entry["status"] = detail["run"]["status"]
            if entry["status"] == "COMPLETED":
                break
            if entry["status"] in {"FAILED", "INTERRUPTED", "CANCELLED", "TIMED_OUT"}:
                raise RuntimeError(f"research stopped: {entry}")
            time.sleep(1)
        if entry["status"] != "COMPLETED" or not detail["report_id"]:
            raise TimeoutError(f"research still pending: {entry['run_id']}; inspect before retry")
        entry.update(
            report_url=url + "/#/report/" + entry["run_id"],
            report_id=detail["report_id"],
            output_hash=detail["output_hash"],
            source_count=len(detail["input_snapshot_ids"]),
        )
        save()
        print(json.dumps({k: entry[k] for k in ("code", "run_id", "status")}), flush=True)
    print(str(output / "manifest.json"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date-start", required=True)
    parser.add_argument("--date-end", required=True)
    args = parser.parse_args()
    prepare(args.url.rstrip("/"), args.output, args.date_start, args.date_end)
