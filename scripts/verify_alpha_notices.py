"""Real page clicks and frozen official notice checks; no response mocks."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402

expect.set_options(timeout=45000)


def verify(url, parent, output, existing_run=None):
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".phase3-quant-o"):
        raise ValueError("new private output required")
    output.mkdir(parents=True)
    cases, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=dict(width=1440, height=900))
        page.on("pageerror", lambda e: errors.append(str(e)))
        parent_path = url + "/api/quant/backtests/" + parent
        with page.expect_response(
            lambda r: r.url == parent_path and r.request.method == "GET", timeout=60000
        ) as loaded:
            page.goto(url + "/#/backtest/" + parent, wait_until="domcontentloaded")
        assert loaded.value.status == 200, loaded.value.text()
        original = loaded.value.json()
        if existing_run:
            run = existing_run
            page.goto(url + "/#/backtest/" + run, wait_until="domcontentloaded")
        else:
            button = page.get_by_role("button", name="核对历史改码公告", exact=True)
            expect(button).to_be_enabled()

            def capture(request):
                if request.url.endswith("/notices") and request.method == "POST":
                    (output / "request.json").write_text(
                        json.dumps(request.post_data_json), encoding="utf-8"
                    )

            page.on("request", capture)
            with page.expect_response(
                lambda r: r.url.endswith("/notices") and r.request.method == "POST", timeout=45000
            ) as response:
                button.click()
            assert response.value.status == 202, response.value.text()
            run = response.value.json()["run_id"]
            body = response.value.request.post_data_json
            (output / "request.json").write_text(json.dumps(body), encoding="utf-8")
            page.wait_for_url("**/#/backtest/" + run)
        print(json.dumps(dict(run_id=run, parent_id=parent)), flush=True)
        panel = page.get_by_label("历史改码公告核对结果")
        expect(panel).to_be_visible()
        expect(page.locator(".rep-h1 .tag")).not_to_have_text("历史采集中", timeout=210000)
        response = page.request.get(url + "/api/quant/backtests/" + run)
        assert response.status == 200, response.text()
        report = response.json()
        audit = report["notice_audit"]
        assert report["check_status"] == "COMPLETED", report["acquisition"]
        assert audit["complete"] and len(audit["documents"]) == 2
        event = audit["events"][0]
        assert (event["old_code"], event["new_code"], event["effective_day"], event["status"]) == (
            "sz.300114",
            "sz.302132",
            "2025-02-17",
            "MATCH",
        )
        assert event["strict_pit"] is False and event["publication_time_status"] == "DAY_ONLY"
        assert (
            report["execution_status"] == "BLOCKED"
            and "metrics" not in report
            and "nav" not in report
        )
        for key in (
            "historical_data",
            "input_audit",
            "filing_audit",
            "benchmark_audit",
            "lifecycle_audit",
        ):
            if key in original:
                assert report[key] == original[key]
        assert page.request.get(url + "/api/quant/backtests/" + parent).json() == original
        if not existing_run:
            assert (
                page.request.post(url + f"/api/quant/backtests/{parent}/notices", data=body).json()[
                    "run_id"
                ]
                == run
            )
        expect(page.get_by_label("免费交易所档案核对结果")).to_contain_text(
            "沿用父版本核对 · 5 份官方响应"
        )
        panel.locator("summary").click()
        expect(panel.get_by_role("link")).to_have_count(2)
        expect(panel).to_contain_text("取证页 1、2")
        panel.locator("summary").click()
        with page.expect_download() as download:
            page.get_by_role("link", name="导出检查报告", exact=True).click()
        download.value.save_as(output / "export.json")
        assert json.loads((output / "export.json").read_text(encoding="utf-8")) == report
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        browser.close()
        for width in (375, 768, 1440):
            for theme in ("dark", "light"):
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport=dict(width=width, height=900))
                context.add_init_script(
                    "localStorage.setItem('search-report-theme'," + json.dumps(theme) + ")"
                )
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url + "/#/backtest/" + run, wait_until="domcontentloaded")
                panel = page.get_by_label("历史改码公告核对结果")
                expect(panel).to_contain_text("2025-02-17")
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                assert page.locator("html").get_attribute("data-theme") == theme
                assert "undefined" not in page.locator("body").inner_text()
                panel.scroll_into_view_if_needed()
                page.screenshot(path=str(output / f"notices-{width}-{theme}.png"))
                cases.append(dict(width=width, theme=theme))
                print(json.dumps(cases[-1]), flush=True)
                browser.close()
    assert not errors, errors
    summary = dict(
        run_id=run,
        parent_id=parent,
        cases=cases,
        errors=errors,
        events=audit["events"],
        sources=audit["documents"],
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(dict(run_id=run, cases=len(cases), errors=errors)), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--parent", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--existing-run")
    args = parser.parse_args()
    verify(args.url, args.parent, args.output, args.existing_run)
