"""Actual page-click CSI benchmark acceptance; never mock public market data."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402

expect.set_options(timeout=30000)


def verify(url, parent, output, existing_run=None, retry_key=None):
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".phase3-quant-o"):
        raise ValueError("new private output required")
    output.mkdir(parents=True)
    errors, cases = [], []
    started = time.monotonic()

    def trace(kind, item):
        with (output / "network.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(dict(seconds=round(time.monotonic() - started, 3), kind=kind, **item))
                + "\n"
            )

    def instrument(page):
        page.on("request", lambda request: trace("request", dict(url=request.url)))
        page.on(
            "response",
            lambda response: trace("response", dict(url=response.url, status=response.status)),
        )
        page.on(
            "requestfailed",
            lambda request: trace("failed", dict(url=request.url, error=request.failure)),
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=dict(width=1440, height=900))
        instrument(page)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "/#/backtest/" + parent, wait_until="domcontentloaded")
        original = page.request.get(url + "/api/quant/backtests/" + parent).json()
        if existing_run:
            assert retry_key
            run, payload = existing_run, dict(request_key=retry_key)
            page.goto(url + "/#/backtest/" + run, wait_until="domcontentloaded")
        else:
            with page.expect_response(
                lambda r: r.url.endswith("/benchmark") and r.request.method == "POST"
            ) as response:
                page.get_by_role("button", name="核对官方基准", exact=True).click()
            assert response.value.status == 202, response.value.text()
            run, payload = response.value.json()["run_id"], response.value.request.post_data_json
            page.wait_for_url("**/#/backtest/" + run)
        (output / "request.json").write_text(json.dumps(payload), encoding="utf-8")
        print(json.dumps(dict(run_id=run, parent_id=parent)), flush=True)
        panel = page.get_by_label("官方基准核对结果")
        expect(panel).to_be_visible()
        expect(page.locator(".rep-h1 .tag")).not_to_have_text("历史采集中", timeout=210000)
        report = page.request.get(url + "/api/quant/backtests/" + run).json()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert report["check_status"] == "COMPLETED", report.get("acquisition")
        audit = report["benchmark_audit"]
        assert audit["complete"] and len(audit["sources"]) == 4
        assert audit["expected_session_count"] == 1211
        assert audit["check_counts"]["MATCH"] == 1211, audit["check_counts"]
        assert all(
            s["row_count"] == 1211 and s["missing_session_count"] == 0 for s in audit["series"]
        )
        assert audit["series"][1]["independent_check"] == "UNAVAILABLE"
        assert report["execution_status"] == "BLOCKED" and audit["metric_status"] == "UNVERIFIED"
        assert "nav" not in report and "metrics" not in report
        for key in ("filing_audit", "input_audit", "historical_data"):
            assert report[key] == original[key]
        assert page.request.get(url + "/api/quant/backtests/" + parent).json() == original
        assert (
            page.request.post(
                url + f"/api/quant/backtests/{parent}/benchmark", data=payload
            ).json()["run_id"]
            == run
        )
        assert (
            page.request.post(
                url + f"/api/quant/backtests/{parent}/benchmark",
                data={**payload, "code": "arbitrary"},
            ).status
            == 422
        )
        expect(page.get_by_label("官方财报口径核对结果")).to_contain_text(
            "沿用父版本核对 · 3 份原文响应"
        )
        panel.get_by_text("查看逐日价格核对（1211）", exact=True).click()
        expect(panel.locator("table").nth(1).locator("tbody tr")).to_have_count(1211)
        panel.get_by_text("查看逐日价格核对（1211）", exact=True).click()
        page.reload(wait_until="domcontentloaded")
        expect(panel).to_contain_text("MATCH 1211 日")
        with page.expect_download() as download:
            page.get_by_role("link", name="导出检查报告", exact=True).click()
        download.value.save_as(output / "export.json")
        assert json.loads((output / "export.json").read_text(encoding="utf-8")) == report
        page.get_by_role("link", name="查看上一个冻结版本", exact=True).click()
        page.wait_for_url("**/#/backtest/" + parent)
        expect(page.get_by_role("button", name="核对官方基准", exact=True)).to_be_enabled()
        page.get_by_role("link", name="历史库", exact=True).click()
        expect(page.locator('.lib-q[href="#/backtest/' + run + '"]')).to_be_visible()
        page.close()
        browser.close()
        for width in (375, 768, 1440):
            for theme in ("dark", "light"):
                # Give each layout fresh preferences and a fixed viewport.
                # Every page still fetches real API data.
                browser = p.chromium.launch(headless=True)
                print(json.dumps(dict(width=width, theme=theme)), flush=True)
                context = browser.new_context(viewport=dict(width=width, height=900))
                context.add_init_script(
                    "localStorage.setItem('search-report-theme'," + json.dumps(theme) + ")"
                )
                page = context.new_page()
                instrument(page)
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url + "/#/backtest/" + run, wait_until="domcontentloaded")
                panel = page.get_by_label("官方基准核对结果")
                expect(panel).to_contain_text("全收益指数")
                expect(panel).to_contain_text("MATCH 1211 日")
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
                assert page.locator("html").get_attribute("data-theme") == theme
                assert "undefined" not in page.locator("body").inner_text()
                assert panel.locator("table").first.locator("td").first.evaluate(
                    "cell => getComputedStyle(cell).whiteSpace === 'nowrap'"
                )
                panel.scroll_into_view_if_needed()
                page.screenshot(path=str(output / f"benchmark-{width}-{theme}.png"))
                cases.append(dict(width=width, theme=theme))
                context.close()
                browser.close()
        browser.close()
    assert not errors, errors
    summary = dict(
        run_id=run,
        parent_id=parent,
        cases=cases,
        errors=errors,
        sources=len(audit["sources"]),
        check_counts=audit["check_counts"],
        series=[{k: v for k, v in s.items() if k != "points"} for s in audit["series"]],
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--parent", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--existing-run")
    parser.add_argument("--retry-key")
    args = parser.parse_args()
    verify(args.url, args.parent, args.output, args.existing_run, args.retry_key)
