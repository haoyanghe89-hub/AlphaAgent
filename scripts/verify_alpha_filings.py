"""Page clicks over real official PDFs; no mocked financial evidence."""

import argparse
import json
import sys
from collections import Counter
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
            run_id, payload = existing_run, dict(request_key=retry_key)
            page.goto(url + "/#/backtest/" + run_id, wait_until="networkidle")
        else:
            with page.expect_response(
                lambda r: r.url.endswith("/filings") and r.request.method == "POST"
            ) as response:
                page.get_by_role("button", name="核对官方财报原文", exact=True).click()
            assert response.value.status == 202, response.value.text()
            run_id = response.value.json()["run_id"]
            payload = response.value.request.post_data_json
            page.wait_for_url("**/#/backtest/" + run_id)
        (output / "request.json").write_text(json.dumps(payload), encoding="utf-8")
        print(json.dumps(dict(run_id=run_id, parent_id=parent_id)), flush=True)
        panel = page.get_by_label("官方财报口径核对结果")
        expect(panel).to_be_visible()
        expect(page.locator(".rep-h1 .tag")).not_to_have_text("历史采集中", timeout=210000)
        report = page.request.get(url + "/api/quant/backtests/" + run_id).json()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert report["check_status"] == "COMPLETED", report.get("acquisition")
        audit = report["filing_audit"]
        assert len(audit["documents"]) == len(audit["sources"]) == 3
        assert len(audit["checks"]) == 12
        assert audit["complete"] and not audit["original_revision_archive_complete"]
        assert audit["metric_status"] == "UNVERIFIED"
        assert report["execution_status"] == "BLOCKED"
        assert "metrics" not in report and "nav" not in report
        assert audit["derived"]["ttm_parent_profit"]["value"] == "34678089000"
        assert audit["derived"]["ttm_parent_profit"]["crosscheck_status"] == "UNAVAILABLE"
        assert report["input_audit"] == parent["input_audit"]
        assert page.request.get(url + "/api/quant/backtests/" + parent_id).json() == parent
        endpoint = url + f"/api/quant/backtests/{parent_id}/filings"
        assert page.request.post(endpoint, data=payload).json()["run_id"] == run_id
        assert (
            page.request.post(endpoint, data={**payload, "url": "https://example.invalid"}).status
            == 422
        )
        assert page.request.get(url + f"/api/quant/backtests/{run_id}/export").json() == report
        assert page.request.get(url + "/api/quant/backtests/" + run_id).json() == report
        for doc in audit["documents"]:
            assert doc["pit_level"] == "PARTIAL"
            assert doc["version_status"] == "REVISION_ARCHIVE_INCOMPLETE"
            for name, key in (("摘要", "summary"), ("利润表", "income")):
                link = panel.get_by_role(
                    "link", name=f"{name}第{doc['source_pages'][key]}页", exact=True
                )
                expect(link).to_have_attribute(
                    "href", doc["source_url"] + "#page=" + str(doc["source_pages"][key])
                )
        page.get_by_label("财报信号年度", exact=True).select_option("2023")
        expect(panel).to_contain_text("2024 年信号")
        page.reload(wait_until="networkidle")
        expect(page.get_by_label("财报信号年度", exact=True)).to_have_value("2024")
        with page.expect_download() as download:
            page.get_by_role("link", name="导出检查报告", exact=True).click()
        download.value.save_as(output / "export.json")
        assert json.loads((output / "export.json").read_text(encoding="utf-8")) == report
        page.get_by_role("link", name="查看上一个冻结版本", exact=True).click()
        page.wait_for_url("**/#/backtest/" + parent_id)
        expect(page.get_by_role("button", name="核对官方财报原文", exact=True)).to_be_enabled()
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
                panel = page.get_by_label("官方财报口径核对结果")
                expect(panel).to_contain_text("PARTIAL · 未验证")
                expect(panel).to_contain_text("信号日同日 · 披露时间未知")
                expect(panel.locator(".filing-data tbody tr")).to_have_count(3)
                expect(panel.locator(".filing-checks tbody tr")).to_have_count(12)
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
                assert page.locator("html").get_attribute("data-theme") == theme
                assert "undefined" not in page.locator("body").inner_text()
                page.screenshot(path=str(output / f"filings-{width}-{theme}.png"), full_page=True)
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
        check_counts=dict(Counter(c["status"] for c in audit["checks"])),
        derived=audit["derived"],
        execution_status=report["execution_status"],
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
