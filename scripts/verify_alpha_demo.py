"""Click the implemented feature journey using real frozen inputs, without fixtures."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def read(url):
    with urlopen(url, timeout=60) as response:
        return json.load(response)


def verify(url, output, archive_id):
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".phase3-quant-o"):
        raise ValueError("new private evidence directory required")
    output.mkdir(parents=True)
    errors, cases, steps = [], [], {}
    original_archive = read(url + "/api/quant/backtests/" + archive_id)
    assert original_archive["check_status"] == "COMPLETED"
    assert original_archive["execution_status"] == "BLOCKED"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def start_example(question):
            page.goto(url, wait_until="networkidle")
            page.get_by_role("button", name=question, exact=True).click()
            scope = page.get_by_role("dialog", name="确认研究范围")
            expect(scope).to_be_visible()
            option = (
                page.get_by_label("数据窗口", exact=True)
                .locator("option")
                .filter(has_text="2023-06-15 — 2024-06-14")
            )
            expect(option).to_have_count(1, timeout=60000)
            page.get_by_label("数据窗口", exact=True).select_option(option.get_attribute("value"))
            page.get_by_role("button", name="确认并开始研究").click()
            page.wait_for_url("**/#/research/**", timeout=60000)
            run_id = page.url.rsplit("/", 1)[-1]
            print(
                json.dumps({"started": question, "run_id": run_id}, ensure_ascii=False), flush=True
            )
            expect(page.get_by_role("link", name="阅读完整报告", exact=True)).to_be_visible(
                timeout=240000
            )
            page.get_by_role("button", name="展开过程", exact=True).click()
            expect(page.locator(".proc-step.completed")).to_have_count(5)
            page.screenshot(path=str(output / (run_id + "-process.png")), full_page=True)
            page.get_by_role("link", name="阅读完整报告", exact=True).click()
            return run_id

        parent_id = start_example("全面分析美的集团")
        steps["research"] = parent_id
        print("research complete; checking evidence, K-line and DCF", flush=True)
        parent = read(url + "/api/quant/runs/" + parent_id)
        assert parent["dcf"]["cells"]["per_share"]["value"] is not None
        assert parent["plan"]["date_end"] == "2024-06-14"
        page.locator(".prototype-chart-canvas canvas").first.wait_for(timeout=60000)
        page.locator(".metric-cell .ev-num").first.click()
        expect(page.get_by_role("dialog", name="数值依据")).to_be_visible()
        expect(page.locator(".evidence-dialog")).to_contain_text("可复现")
        page.get_by_role("button", name="关闭数值依据").click()
        page.locator(".citation-link").first.click()
        expect(page.get_by_role("dialog", name="证据溯源")).to_be_visible()
        expect(page.locator(".evidence-dialog")).to_contain_text("COMPUTATION_CELL", timeout=60000)
        page.get_by_role("button", name="关闭证据溯源").click()
        assert len(parent["klines"]) == 3
        expect(page.locator(".kline-block .prototype-chart-canvas canvas")).to_have_count(1)
        page.get_by_label("K线价格口径").select_option("raw")
        page.locator(".kline-evidence summary").click()
        page.locator(".kline-evidence .ev-num").first.click()
        expect(page.get_by_role("dialog", name="数值依据")).to_be_visible()
        expect(page.locator(".evidence-dialog")).to_contain_text("可复现")
        page.get_by_role("button", name="关闭数值依据").click()
        page.get_by_label("K线价格口径").select_option("qfq")
        slider = page.get_by_label("阶段1增速（5年）")
        slider.focus()
        slider.press("Home")
        for _ in range(32):
            slider.press("ArrowRight")
        expect(slider).to_have_value("12")
        page.get_by_role("button", name="按当前假设重算", exact=True).click()
        page.wait_for_url("**/#/research/**", timeout=60000)
        child_id = page.url.rsplit("/", 1)[-1]
        print(json.dumps({"dcf_child": child_id}), flush=True)
        expect(page.get_by_role("link", name="阅读完整报告", exact=True)).to_be_visible(
            timeout=240000
        )
        page.get_by_role("link", name="阅读完整报告", exact=True).click()
        expect(page.locator(".version-select")).to_have_count(1)
        child = read(url + "/api/quant/runs/" + child_id)
        assert child["plan"]["dcf"]["g1"] == "0.12"
        assert child["input_snapshot_ids"] == parent["input_snapshot_ids"]
        assert (
            child["dcf"]["cells"]["per_share"]["value"]
            != parent["dcf"]["cells"]["per_share"]["value"]
        )
        assert read(url + "/api/quant/runs/" + parent_id)["output_hash"] == parent["output_hash"]
        steps["dcf_child"] = child_id
        page.reload(wait_until="networkidle")
        expect(page.get_by_label("阶段1增速（5年）")).to_have_value("12")
        page.locator(".version-select").select_option(parent_id)
        page.wait_for_url("**/#/report/" + parent_id)
        expect(page.get_by_label("阶段1增速（5年）")).to_have_value("8")

        comparison_id = start_example("美的集团 vs 贵州茅台，比较成长性、盈利能力和估值")
        expect(page.locator(".comparison-dimensions tbody tr")).to_have_count(5, timeout=60000)
        compared = read(url + "/api/quant/runs/" + comparison_id)
        assert len(compared["subjects"]) == 2
        assert all(
            s["plan"]["date_end"] == "2024-06-14"
            for s in compared["plan"]["comparison"]["branches"]
        )
        steps["comparison"] = comparison_id
        print("comparison complete; checking backtest gate and history", flush=True)

        page.get_by_role("navigation", name="主导航").get_by_role("link", name="策略回测").click()
        page.get_by_role("button", name="确认条件并执行", exact=True).click()
        page.wait_for_url("**/#/backtest/RUN-BT-**", timeout=60000)
        check_id = page.url.rsplit("/", 1)[-1]
        fresh_check = read(url + "/api/quant/backtests/" + check_id)
        assert fresh_check["check_status"] == "COMPLETED"
        assert fresh_check["execution_status"] == "BLOCKED"
        assert "nav" not in fresh_check and "metrics" not in fresh_check
        steps["input_check"] = check_id
        page.get_by_role("navigation", name="主导航").get_by_role("link", name="历史库").click()
        page.get_by_role("searchbox", name="搜索历史研究").fill(check_id)
        page.get_by_label("研究状态").select_option("done")
        expect(page.locator(".lib-row")).to_have_count(1, timeout=60000)
        expect(page.locator(".lib-row")).to_contain_text("检查完成 · 回测受阻")
        page.locator(".lib-q").click()
        page.wait_for_url("**/#/backtest/" + check_id)
        page.reload(wait_until="networkidle")

        # Read the existing real archive; do not refetch sources or rewrite its report.
        page.goto(url + "/#/backtest/" + archive_id, wait_until="networkidle")
        page.get_by_role("heading", name="沪深交易所生命周期核对").wait_for(timeout=60000)
        assert read(url + "/api/quant/backtests/" + archive_id) == original_archive
        exported = read(url + "/api/quant/backtests/" + archive_id + "/export")
        assert exported == original_archive
        steps["official_archive"] = archive_id
        page.close()

        for width in (375, 768, 1440):
            for theme in ("dark", "light"):
                context = browser.new_context(viewport={"width": width, "height": 900})
                context.add_init_script(f"localStorage.setItem('search-report-theme','{theme}')")
                layout_page = context.new_page()
                layout_page.on("pageerror", lambda error: errors.append(str(error)))
                for name, path in (
                    ("workbench", "/#/"),
                    ("research", "/#/report/" + parent_id),
                    ("comparison", "/#/report/" + comparison_id),
                    ("backtest", "/#/backtest/" + archive_id),
                ):
                    layout_page.goto(url + path, wait_until="networkidle")
                    if name in {"research", "comparison"}:
                        layout_page.locator(".prototype-chart-canvas canvas").first.wait_for(
                            timeout=60000
                        )
                    assert layout_page.locator("html").get_attribute("data-theme") == theme
                    assert not layout_page.evaluate(
                        "document.documentElement.scrollWidth > innerWidth"
                    )
                    layout_page.screenshot(path=str(output / f"{name}-{width}-{theme}.png"))
                    cases.append(dict(page=name, width=width, theme=theme))
                context.close()
        browser.close()
    assert not errors, errors
    summary = dict(
        steps=steps,
        cases=cases,
        page_errors=errors,
        parent_output_unchanged=True,
        archive_unchanged=True,
        archive_sha256=hashlib.sha256(
            json.dumps(original_archive, sort_keys=True).encode()
        ).hexdigest(),
        backtest_execution="BLOCKED",
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive-id", required=True)
    args = parser.parse_args()
    verify(args.url.rstrip("/"), args.output, args.archive_id)
