"""Page-click acceptance over actual public data, without mocked market responses."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def verify(url, parent_id, output, existing_run=None, retry_key=None):
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".phase3-quant-o"):
        raise ValueError("new private acceptance output required")
    output.mkdir(parents=True)
    errors, cases = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url + "/#/backtest/" + parent_id, wait_until="networkidle")
        parent = page.request.get(url + "/api/quant/backtests/" + parent_id).json()
        expect(page.get_by_role("heading", name="策略回测工作台")).to_be_visible()
        if existing_run:
            if not retry_key:
                raise ValueError("original retry key required for persisted-run acceptance")
            run_id = existing_run
            payload = dict(
                request_key=retry_key,
                instrument_id="ins_c317bc1b5fc95003aa6e556c8045a5b8",
                signal_year=2024,
            )
            page.goto(url + "/#/backtest/" + run_id, wait_until="networkidle")
        else:
            page.get_by_label("核验证券", exact=True).select_option(
                "ins_c317bc1b5fc95003aa6e556c8045a5b8"
            )
            page.get_by_label("财报信号年度", exact=True).select_option("2024")
            with page.expect_response(
                lambda r: r.url.endswith("/inputs") and r.request.method == "POST"
            ) as response:
                page.get_by_role("button", name="核验股票历史输入", exact=True).click()
            assert response.value.status == 202, response.value.text()
            run_id = response.value.json()["run_id"]
            payload = response.value.request.post_data_json
            page.wait_for_url("**/#/backtest/" + run_id)
        (output / "request.json").write_text(json.dumps(payload), encoding="utf-8")
        print(json.dumps(dict(run_id=run_id, parent_id=parent_id)), flush=True)
        expect(page.locator(".report-rail")).to_contain_text(run_id)
        expect(page.get_by_label("股票历史输入核验结果")).to_be_visible()
        expect(page.locator(".rep-h1 .tag")).not_to_have_text("历史采集中", timeout=210000)
        report = page.request.get(url + "/api/quant/backtests/" + run_id).json()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert report["check_status"] == "COMPLETED", report.get("acquisition")
        audit = report["input_audit"]
        assert audit["prices"]["coverage"] == "COVERED"
        assert audit["prices"]["row_count"] == audit["prices"]["expected_session_count"]
        assert audit["actions"]["complete"] is False
        event = next(e for e in audit["actions"]["events"] if e["ex_date"] == "2024-05-15")
        assert event["cash_per_share_before_tax"] == "3"
        assert event["reserve_shares_per_share"] is None
        financial = next(
            f for f in audit["financials"]["observations"] if f["period_end"] == "2024-03-31"
        )
        assert financial["date_observation"] == "SAME_DAY_TIME_UNKNOWN"
        assert financial["publication_date"] == audit["scope"]["signal_day"]
        assert audit["financials"]["original_versions_available"] is False
        assert report["execution_status"] == "BLOCKED"
        assert "metrics" not in report and "nav" not in report
        assert page.request.get(url + "/api/quant/backtests/" + parent_id).json() == parent
        endpoint = url + f"/api/quant/backtests/{parent_id}/inputs"
        assert page.request.post(endpoint, data=payload).json()["run_id"] == run_id
        assert page.request.post(endpoint, data={**payload, "signal_year": 2023}).status == 409
        page.get_by_label("财报信号年度", exact=True).select_option("2023")
        expect(page.get_by_label("股票历史输入核验结果")).to_contain_text("2024 年信号日")
        page.get_by_label("PE 上限（≤）").fill("40")
        expect(page.locator(".backtest-report .condition-tags")).to_contain_text("PE ≤ 30")
        page.reload(wait_until="networkidle")
        expect(page.get_by_label("财报信号年度", exact=True)).to_have_value("2024")
        expect(page.get_by_label("PE 上限（≤）")).to_have_value("30")
        with page.expect_download() as download:
            page.get_by_role("link", name="导出检查报告", exact=True).click()
        download.value.save_as(output / "export.json")
        assert json.loads((output / "export.json").read_text(encoding="utf-8")) == report
        page.get_by_role("link", name="查看上一个冻结版本", exact=True).click()
        page.wait_for_url("**/#/backtest/" + parent_id)
        expect(page.get_by_role("button", name="核验股票历史输入", exact=True)).to_be_enabled()
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
                panel = page.get_by_label("股票历史输入核验结果")
                expect(panel).to_contain_text("与信号日同日 · 披露时间未知")
                expect(panel).to_contain_text("PARTIAL · 未验证")
                expect(panel.locator(".input-events tbody tr")).to_have_count(
                    audit["actions"]["event_count"]
                )
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
                assert page.locator("html").get_attribute("data-theme") == theme
                assert "undefined" not in page.locator("body").inner_text()
                page.screenshot(path=str(output / f"inputs-{width}-{theme}.png"), full_page=True)
                if width == 1440:
                    panel.screenshot(path=str(output / f"panel-{theme}.png"))
                cases.append(dict(width=width, theme=theme))
                context.close()
        browser.close()
    assert not errors, errors
    summary = dict(
        run_id=run_id,
        parent_id=parent_id,
        cases=cases,
        errors=errors,
        sources=len(audit["sources"]),
        prices=audit["prices"],
        action_events=audit["actions"]["event_count"],
        financials=audit["financials"],
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
