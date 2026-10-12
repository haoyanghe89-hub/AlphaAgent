"""Real page-click free exchange acceptance. No network or response mocks."""

import argparse
import json
import sys
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
    cases, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=dict(width=1440, height=900))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "/#/backtest/" + parent, wait_until="domcontentloaded")
        original = page.request.get(url + "/api/quant/backtests/" + parent).json()
        if existing_run:
            assert retry_key
            run, payload = existing_run, dict(request_key=retry_key)
            page.goto(url + "/#/backtest/" + run, wait_until="domcontentloaded")
        else:
            with page.expect_response(
                lambda r: r.url.endswith("/lifecycles") and r.request.method == "POST",
                timeout=45000,
            ) as response:
                page.get_by_role("button", name="核对免费交易所档案", exact=True).click()
            assert response.value.status == 202, response.value.text()
            run = response.value.json()["run_id"]
            payload = response.value.request.post_data_json
            page.wait_for_url("**/#/backtest/" + run)
        (output / "request.json").write_text(json.dumps(payload), encoding="utf-8")
        print(json.dumps(dict(run_id=run, parent_id=parent)), flush=True)
        panel = page.get_by_label("免费交易所档案核对结果")
        expect(panel).to_be_visible()
        expect(page.locator(".rep-h1 .tag")).not_to_have_text("历史采集中", timeout=210000)
        report = page.request.get(url + "/api/quant/backtests/" + run).json()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert report["check_status"] == "COMPLETED", report.get("acquisition")
        audit = report["lifecycle_audit"]
        assert audit["complete"] and len(audit["sources"]) == 5
        assert sum(s["row_count"] for s in audit["sources"]) >= 5000
        assert len(audit["annual"]) == 5 and len(audit["checks"]) >= 5000
        assert audit["pit_level"] == "PARTIAL" and report["execution_status"] == "BLOCKED"
        assert "metrics" not in report and "nav" not in report
        for key in ("historical_data", "input_audit", "filing_audit", "benchmark_audit"):
            if key in original:
                assert report[key] == original[key]
        assert page.request.get(url + "/api/quant/backtests/" + parent).json() == original
        assert (
            page.request.post(
                url + f"/api/quant/backtests/{parent}/lifecycles", data=payload
            ).json()["run_id"]
            == run
        )
        assert (
            page.request.post(
                url + f"/api/quant/backtests/{parent}/lifecycles",
                data={**payload, "url": "https://arbitrary"},
            ).status
            == 422
        )
        if "benchmark_audit" in original:
            expect(page.get_by_label("官方基准核对结果")).to_contain_text(
                "沿用父版本核对 · 4 份官方响应"
            )
        for annual in audit["annual"]:
            assert annual["common_count"] + annual["only_native_count"] == annual["native_count"]
            assert (
                annual["common_count"] + annual["only_official_count"]
                == annual["official_observed_count"]
            )
        first = next((a for a in audit["annual"] if a["details"]), audit["annual"][0])
        details = panel.locator("details").filter(
            has=page.get_by_text(
                f"{first['signal_day']} 名单差异（{len(first['details'])}）", exact=True
            )
        )
        details.locator("summary").click()
        expect(details.locator("tbody tr")).to_have_count(len(first["details"]) or 1)
        details.locator("summary").click()
        page.reload(wait_until="domcontentloaded")
        expect(panel).to_contain_text("冻结 5 / 5 份官方响应")
        with page.expect_download() as download:
            page.get_by_role("link", name="导出检查报告", exact=True).click()
        download.value.save_as(output / "export.json")
        assert json.loads((output / "export.json").read_text(encoding="utf-8")) == report
        page.get_by_role("link", name="历史库", exact=True).click()
        expect(page.locator('.lib-q[href="#/backtest/' + run + '"]')).to_be_visible()
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
                panel = page.get_by_label("免费交易所档案核对结果")
                expect(panel).to_contain_text("冻结 5 / 5 份官方响应")
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                assert page.locator("html").get_attribute("data-theme") == theme
                assert "undefined" not in page.locator("body").inner_text()
                panel.scroll_into_view_if_needed()
                page.screenshot(path=str(output / f"lifecycles-{width}-{theme}.png"))
                cases.append(dict(width=width, theme=theme))
                print(json.dumps(cases[-1]), flush=True)
                browser.close()
    assert not errors, errors
    summary = dict(
        run_id=run,
        parent_id=parent,
        cases=cases,
        errors=errors,
        sources=[{k: v for k, v in s.items() if k != "params"} for s in audit["sources"]],
        annual=[{k: v for k, v in a.items() if k != "details"} for a in audit["annual"]],
        check_counts=audit["check_counts"],
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
