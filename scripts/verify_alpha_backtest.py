"""Browser acceptance over real frozen inventory; never creates example returns."""

import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def verify(url, output):
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".phase3-quant-o"):
        raise ValueError("new private acceptance output required")
    output.mkdir(parents=True)
    errors, cases = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url + "/#/backtest", wait_until="networkidle")
        expect(page.get_by_role("heading", name="策略回测工作台")).to_be_visible()
        expect(page.get_by_role("button", name="确认条件并执行")).to_be_enabled()
        page.get_by_label("PE 上限（≤）").fill("25")
        with page.expect_response(
            lambda r: r.url.endswith("/api/quant/backtests") and r.request.method == "POST"
        ) as submitted:
            page.get_by_role("button", name="确认条件并执行").click()
        started = submitted.value.json()
        assert submitted.value.status == 201, started
        run_id = started["run_id"]
        page.wait_for_url("**/#/backtest/" + run_id)
        expect(page.get_by_text("数据检查完成 · 回测受阻", exact=True)).to_be_visible(timeout=60000)
        report = page.request.get(url + "/api/quant/backtests/" + run_id).json()
        assert report["data_check"]["instrument_count"] == 3
        assert report["conditions"]["pe_max"] == "25"
        assert (
            report["execution_status"] == "BLOCKED"
            and "nav" not in report
            and "metrics" not in report
        )
        assert len(report["data_check"]["snapshots"]) > 0
        payload = submitted.value.request.post_data_json
        retry = page.request.post(url + "/api/quant/backtests", data=payload)
        assert retry.json()["run_id"] == run_id
        assert (
            page.request.post(url + "/api/quant/backtests", data={**payload, "years": 3}).status
            == 409
        )
        before = page.request.get(url + "/api/quant/runs").json()
        assert sum(r["run_id"] == run_id for r in before) == 1
        # Editable next-task conditions must not replace the frozen task's report.
        page.get_by_label("PE 上限（≤）").fill("40")
        expect(page.locator(".backtest-report .condition-tags")).to_contain_text("PE ≤ 25")
        page.reload(wait_until="networkidle")
        expect(page.get_by_label("PE 上限（≤）")).to_have_value("25")
        expect(page.locator(".backtest-report .condition-tags")).to_contain_text("PE ≤ 25")
        with page.expect_download() as download:
            page.get_by_role("link", name="导出检查报告").click()
        download.value.save_as(output / "check.json")
        assert json.loads((output / "check.json").read_text(encoding="utf-8")) == report
        page.get_by_role("link", name="历史库", exact=True).click()
        expect(page.locator('.lib-q[href="#/backtest/' + run_id + '"]')).to_be_visible()
        expect(
            page.locator(".lib-row").filter(
                has=page.locator('.lib-q[href="#/backtest/' + run_id + '"]')
            )
        ).to_contain_text("回测受阻")
        page.locator('.lib-q[href="#/backtest/' + run_id + '"]').click()
        expect(page.get_by_text("数据检查完成 · 回测受阻", exact=True)).to_be_visible()
        page.close()
        for width in (375, 768, 1440):
            for theme in ("dark", "light"):
                context = browser.new_context(viewport={"width": width, "height": 900})
                context.add_init_script(
                    "localStorage.setItem('search-report-theme', " + json.dumps(theme) + ")"
                )
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url + "/#/backtest/" + run_id, wait_until="networkidle")
                expect(page.get_by_text("数据检查完成 · 回测受阻", exact=True)).to_be_visible()
                expect(page.locator(".backtest-check > .bt-check-item")).to_have_count(7)
                expect(page.locator("html")).to_have_attribute("data-theme", theme)
                expect(page.get_by_role("heading", name="绩效对比")).to_be_visible()
                expect(
                    page.get_by_role("heading", name="净值曲线 vs 沪深300（含成本敏感性）")
                ).to_be_visible()
                assert (
                    page.locator(".backtest-report .rep-section table tbody")
                    .first.inner_text()
                    .count("—")
                    == 15
                )
                assert "undefined" not in page.locator(".backtest-report").inner_text()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.locator(".bt-source-details summary").click()
                expect(page.locator(".bt-source-details")).to_contain_text(
                    report["data_check"]["snapshots"][0]["snapshot_id"]
                )
                page.locator(".bt-source-details summary").click()
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=output / f"backtest-{width}-{theme}.png", full_page=True)
                cases.append({"width": width, "theme": theme})
                context.close()
        browser.close()
    assert not errors, errors
    (output / "summary.json").write_text(
        json.dumps(
            dict(run_id=run_id, url=url + "/#/backtest/" + run_id, cases=cases, page_errors=errors),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(dict(run_id=run_id, cases=len(cases), page_errors=errors), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with urlopen(args.url + "/api/health", timeout=10) as response:
        assert json.load(response)["status"] == "ok"
    verify(args.url.rstrip("/"), args.output)
