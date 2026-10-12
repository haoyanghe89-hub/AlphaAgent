"""Read-only acceptance of fresh model evidence, reader math and failure fallback."""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def verify(url, run, output):
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".phase3-quant-o"):
        raise ValueError("new private output directory required")
    output.mkdir(parents=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=dict(width=1440, height=1100))
        page.on("pageerror", lambda e: errors.append(str(e)))
        endpoint = url + "/api/quant/research/runs/" + run
        original = page.request.get(endpoint).json()
        assert original["status"] == "COMPLETED"
        models = [c for c in original["calls"] if c["kind"] == "model"]
        assert [c["role"] for c in models] == ["Planner", "Data", "Analyst", "Verifier", "Writer"]
        assert all(c["status"] == "COMPLETED" for c in models)
        d = original["data"]
        facts = {e["id"] for e in d["evidence"] if e.get("locator", "").startswith("reader/")}
        assert len(facts) >= 3
        assert any(facts.intersection(c["evidence_ids"]) for c in d["agent_report"]["claims"])
        overview = page.request.get(endpoint + "/overview").json()
        # Independent, ordinary float checkpoints over the report's public inputs.
        start = date.fromisoformat(d["data_dates"]["price"]) - timedelta(days=364)
        sample = [
            row[1]
            for dt, row in zip(d["kline"]["x"], d["kline"]["ohlc"], strict=True)
            if start <= date.fromisoformat(dt) <= date.fromisoformat(d["data_dates"]["price"])
        ]
        low, high, last = min(sample), max(sample), sample[-1]
        assert abs(overview["price"]["range_position"] - (last - low) / (high - low) * 100) < 1e-5
        assert (overview["price"]["low"], overview["price"]["high"]) == (low, high)
        page.goto(url + "/#/live/" + run, wait_until="networkidle")
        summary = page.get_by_label("结论速览", exact=True)
        expect(summary.locator(".reader-summary")).to_contain_text("低估待确认")
        expect(summary.locator(".conclusion-item")).to_have_count(4)
        expect(summary).to_contain_text("后续看")
        summary.locator(".conclusion-details > summary").click()
        expect(summary.locator(".conclusion-analysis")).to_have_count(
            len(d["agent_report"]["claims"])
        )
        summary.locator(".conclusion-details > summary").click()
        summary.screenshot(path=str(output / "fresh-overview.png"))
        dc = page.get_by_label("DCF 假设解读", exact=True)
        dc.get_by_role("button").first.click()
        dialog = page.get_by_role("dialog", name="数据来源", exact=True)
        expect(dialog).to_contain_text("压力测试")
        expect(dialog).to_contain_text("现金流年增长率")
        page.keyboard.press("Escape")
        exported = page.request.get(endpoint + "/export").text()
        assert overview["summary"] in exported and "后续观察条件" in exported
        assert page.request.get(endpoint).json() == original
        # Simulate only reader-endpoint failure; genuine report data stays intact.
        page.route(
            "**/api/quant/research/runs/*/overview",
            lambda r: r.fulfill(
                status=503, content_type="application/json", body='{"detail":"unavailable"}'
            ),
        )
        page.reload(wait_until="networkidle")
        expect(summary).to_contain_text("白话解读暂未加载")
        expect(summary.locator(".reader-summary")).to_have_count(0)
        expect(summary.locator(".conclusion-item")).to_have_count(4)
        expect(page.locator("#live-market")).to_contain_text("技术面与资金面")
        assert page.request.get(endpoint).json() == original
        browser.close()
    assert not errors, errors
    (output / "summary.json").write_text(
        json.dumps(
            dict(
                run_id=run,
                model_roles=5,
                calculated_evidence_count=len(facts),
                independent_price_math=True,
                export=True,
                reader_failure_retains_report=True,
                immutable_report=True,
                errors=errors,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("fresh model, derived evidence, independent math, export and fallback passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.url, args.run, args.output)
