"""Actual public history acquisition through page clicks; no mocked market data."""

import argparse
import json
import sys
from pathlib import Path

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
        with page.expect_response(
            lambda r: r.url.endswith("/api/quant/backtests") and r.request.method == "POST"
        ) as response:
            page.get_by_role("button", name="确认条件并执行", exact=True).click()
        parent_id = response.value.json()["run_id"]
        page.wait_for_url("**/#/backtest/" + parent_id)
        expect(page.get_by_role("button", name="获取历史股票池与基准", exact=True)).to_be_enabled()
        parent = page.request.get(url + "/api/quant/backtests/" + parent_id).json()
        with page.expect_response(
            lambda r: r.url.endswith("/history") and r.request.method == "POST"
        ) as acquired:
            page.get_by_role("button", name="获取历史股票池与基准", exact=True).click()
        assert acquired.value.status == 202, acquired.value.text()
        run_id = acquired.value.json()["run_id"]
        page.wait_for_url("**/#/backtest/" + run_id)
        print(json.dumps(dict(run_id=run_id, parent_id=parent_id)), flush=True)
        expect(
            page.get_by_role("button", name="重新获取历史股票池与基准", exact=True)
        ).to_be_visible(timeout=210000)
        report = page.request.get(url + "/api/quant/backtests/" + run_id).json()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert report["check_status"] == "COMPLETED", report.get("acquisition")
        history = report["historical_data"]
        assert history["universes"] and history["benchmark"]["row_count"] > 0
        assert all(
            u["member_count"] > 1000 and u["eligible_count"] is None for u in history["universes"]
        )
        assert (
            report["execution_status"] == "BLOCKED"
            and "metrics" not in report
            and "nav" not in report
        )
        assert page.request.get(url + "/api/quant/backtests/" + parent_id).json() == parent
        payload = acquired.value.request.post_data_json
        assert (
            page.request.post(
                url + f"/api/quant/backtests/{parent_id}/history", data=payload
            ).json()["run_id"]
            == run_id
        )
        page.get_by_label("PE 上限（≤）").fill("40")
        expect(page.locator(".backtest-report .condition-tags")).to_contain_text("PE ≤ 30")
        page.reload(wait_until="networkidle")
        expect(page.get_by_label("PE 上限（≤）")).to_have_value("30")
        with page.expect_download() as download:
            page.get_by_role("link", name="导出检查报告", exact=True).click()
        download.value.save_as(output / "export.json")
        assert json.loads((output / "export.json").read_text(encoding="utf-8")) == report
        page.get_by_role("link", name="查看上一个冻结版本", exact=True).click()
        expect(page.get_by_role("button", name="获取历史股票池与基准", exact=True)).to_be_visible()
        page.get_by_role("link", name="历史库", exact=True).click()
        expect(page.locator('.lib-q[href="#/backtest/' + run_id + '"]')).to_be_visible()
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
                expect(
                    page.get_by_role("heading", name="历史股票池与沪深300基准", exact=True)
                ).to_be_visible()
                panel = page.get_by_label("历史数据采集结果")
                expect(panel.locator("tbody tr")).to_have_count(len(history["annual"]))
                expect(panel).to_contain_text("未计算")
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
                assert page.locator("html").get_attribute("data-theme") == theme
                assert "undefined" not in page.locator("body").inner_text()
                page.evaluate("scrollTo(0,0)")
                page.screenshot(path=str(output / f"history-{width}-{theme}.png"), full_page=True)
                cases.append(dict(width=width, theme=theme))
                context.close()
        browser.close()
    assert not errors, errors
    summary = dict(
        run_id=run_id,
        parent_id=parent_id,
        cases=cases,
        errors=errors,
        sources=len(history["sources"]),
        annual=history["annual"],
        benchmark=history["benchmark"],
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8838")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.url, args.output)
