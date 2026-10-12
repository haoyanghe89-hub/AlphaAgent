"""Real frozen two-stock browser acceptance; no demo data or financial calculations."""

import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def get(url):
    return json.load(urlopen(url, timeout=60))


def verify(url, output, run_id=None):
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".phase3-quant-o"):
        raise ValueError("new private acceptance output required")
    output.mkdir(parents=True)
    errors, cases = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        if run_id is None:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url, wait_until="networkidle")
            page.get_by_role("textbox", name="投研问题").fill(
                "美的集团 vs 贵州茅台，比较成长性、盈利能力和估值"
            )
            page.get_by_role("button", name="开始研究", exact=True).click()
            scope = page.get_by_role("dialog", name="确认研究范围")
            expect(scope).to_be_visible()
            expect(scope.locator(".scope-stock")).to_have_count(2)
            expect(page.get_by_role("button", name="确认并开始研究")).to_be_disabled()
            option = (
                page.get_by_label("数据窗口", exact=True)
                .locator("option")
                .filter(has_text="241 日")
            )
            expect(option).to_have_count(1, timeout=60000)
            page.get_by_label("数据窗口", exact=True).select_option(option.get_attribute("value"))
            page.get_by_label("价格口径", exact=True).select_option("qfq")
            page.get_by_role("button", name="确认并开始研究").click()
            page.wait_for_url("**/#/research/**", timeout=60000)
            run_id = page.url.rsplit("/", 1)[-1]
            (output / "started.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
            expect(page.get_by_role("link", name="阅读完整报告")).to_be_visible(timeout=300000)
            page.get_by_role("link", name="阅读完整报告").click()
            expect(page.locator(".comparison-dimensions tbody tr")).to_have_count(5, timeout=60000)
            page.close()

        data = get(url + "/api/quant/runs/" + run_id)
        assert data["scenario"] == "compare" and len(data["subjects"]) == 2
        assert all(n["status"] == "COMPLETED" for n in data["process"])
        for source in data["subjects"]:
            original = get(url + "/api/quant/runs/" + source["run_id"])
            assert source["output_hash"] == original["output_hash"]
            assert source["manifest_hash"] == original["manifest_hash"]
            assert source["input_snapshot_ids"] == original["input_snapshot_ids"]
            assert all(c in original["values"] for c in source["data"]["values"])
        for row in data["comparison"]["dimensions"] + data["comparison"]["supplementary"]:
            for source, cell in zip(data["subjects"], row["cells"], strict=True):
                if cell:
                    assert cell in source["data"]["values"] and cell["status"] == "UNVERIFIED"
        (output / "run.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for width in (375, 768, 1440):
            for theme in ("dark", "light"):
                context = browser.new_context(
                    viewport={"width": width, "height": 900}, reduced_motion="reduce"
                )
                context.add_init_script(f"localStorage.setItem('search-report-theme','{theme}')")
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url + "/#/report/" + run_id, wait_until="networkidle")
                expect(page.locator(".comparison-dimensions tbody tr")).to_have_count(
                    5, timeout=60000
                )
                expect(page.locator(".comparison-detail")).to_have_count(2)
                expect(page.locator(".prototype-chart-canvas canvas")).to_have_count(2)
                expect(page.locator(".quant-exhibit")).to_have_count(2)
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                page.screenshot(path=str(output / f"report-{width}-{theme}.png"), full_page=True)
                for index, source in enumerate(data["subjects"]):
                    button = (
                        page.locator(".comparison-dimensions tbody tr")
                        .first.locator("button.ev-num")
                        .nth(index)
                    )
                    button.click()
                    dialog = page.get_by_role("dialog", name="数值依据")
                    expect(dialog).to_be_visible()
                    dialog.locator("summary").click()
                    expect(dialog).to_contain_text(source["manifest_hash"])
                    expect(dialog).to_contain_text(source["input_snapshot_ids"][0])
                    expect(dialog).to_contain_text("尚未验证")
                    page.get_by_role("button", name="关闭数值依据").click()
                page.get_by_role("link", name="关键指标对照", exact=True).click()
                assert page.evaluate("scrollY") > 0
                page.reload(wait_until="networkidle")
                expect(page.locator(".comparison-dimensions tbody tr")).to_have_count(5)
                page.goto(url + "/#/library", wait_until="networkidle")
                parent = page.locator(".lib-row").filter(
                    has=page.locator('a[href="#/report/' + run_id + '"]')
                )
                expect(parent).to_contain_text("对比")
                expect(page.locator(".lib-row")).to_have_count(1)
                cases.append(
                    dict(
                        width=width,
                        theme=theme,
                        report=run_id,
                        source_evidence_clicks=2,
                        refresh=True,
                        history=True,
                    )
                )
                context.close()
        content = (
            urlopen(url + "/api/quant/runs/" + run_id + "/comparison-export", timeout=60)
            .read()
            .decode()
        )
        assert "五维对照" in content and "UNAVAILABLE" in content and "UNVERIFIED" in content
        assert all(s["output_hash"] in content for s in data["subjects"])
        (output / "comparison.md").write_text(content, encoding="utf-8")
        browser.close()
    assert not errors, errors
    result = dict(
        run_id=run_id,
        report_id=data["report_id"],
        cases=cases,
        errors=errors,
        all_cells_match_original_sources=True,
        no_verification_upgrade=True,
        markdown_export=True,
    )
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8835")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run")
    args = parser.parse_args()
    verify(args.url, args.output, args.run)
