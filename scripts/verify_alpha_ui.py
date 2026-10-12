"""Actual AlphaAgent browser acceptance over an isolated, historical evidence server."""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def verify(url, output):
    output = output.resolve()
    if not output.is_relative_to(ROOT / ".phase3-quant-o") or output.exists():
        raise ValueError("new private acceptance output required")
    output.mkdir(parents=True)
    overview = json.load(urlopen(url + "/api/quant/overview"))
    stocks = overview["stocks"]
    assert len(stocks) >= 3 and overview["mode"] == "historical_frozen"
    errors, cases = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width in (375, 768, 1440):
            for theme in ("light", "dark"):
                context = browser.new_context(
                    viewport={"width": width, "height": 900}, reduced_motion="reduce"
                )
                context.add_init_script(f"localStorage.setItem('search-report-theme', '{theme}')")
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url, wait_until="networkidle")
                expect(page.locator(".stock-card")).to_have_count(len(stocks))
                expect(page.get_by_role("heading", name="提出一个投研问题")).to_be_visible()
                assert page.locator("html").get_attribute("data-theme") == theme
                for name, fragment in (
                    ("workbench", "#/"),
                    ("stock", "#/stock/" + stocks[0]["code"]),
                    ("report", "#/report/" + stocks[0]["run_id"]),
                    ("library", "#/library"),
                    ("backtest", "#/backtest"),
                ):
                    page.goto(url + "/" + fragment, wait_until="networkidle")
                    if name in {"stock", "report"}:
                        page.locator(".chart-canvas canvas").first.wait_for(timeout=60000)
                        expect(page.locator(".quant-exhibit")).to_have_count(3)
                        assert (
                            page.locator(".chart-canvas").first.get_attribute(
                                "data-chart-animation"
                            )
                            == "false"
                        )
                    if name == "report":
                        expect(
                            page.locator(".metric-cell").filter(has_text="年化波动率").locator(".m-value")
                        ).to_contain_text("%")
                        page.screenshot(path=str(output / f"report-top-{width}-{theme}.png"))
                        page.locator(".metric-cell .ev-num").first.click()
                        expect(page.get_by_role("dialog", name="数值依据")).to_be_visible()
                        expect(page.locator(".evidence-dialog")).to_contain_text("可复现")
                        page.get_by_role("button", name="关闭数值依据").click()
                        page.locator(".citation-link").first.click()
                        expect(page.get_by_role("dialog", name="证据溯源")).to_be_visible()
                        expect(page.locator(".evidence-dialog")).to_contain_text("COMPUTATION_CELL")
                        page.get_by_role("button", name="关闭证据溯源").click()
                        page.get_by_role("link", name="价格、估值与财务", exact=True).click()
                        assert page.evaluate("scrollY") > 0
                    if name == "backtest":
                        expect(page.get_by_role("button", name="回测执行待接入")).to_be_disabled()
                    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (
                        width,
                        theme,
                        name,
                    )
                    page.screenshot(
                        path=str(output / f"{name}-{width}-{theme}.png"), full_page=True
                    )
                    cases.append(dict(width=width, theme=theme, page=name))
                context.close()

        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url, wait_until="networkidle")
        expect(page.locator(".stock-card")).to_have_count(len(stocks))
        page.get_by_role("button", name="切换浅色").click()
        assert page.locator("html").get_attribute("data-theme") == "light"
        page.reload(wait_until="networkidle")
        assert page.locator("html").get_attribute("data-theme") == "light"
        for question, annual in (
            ("平安银行近三年的风险", False),
            ("平安银行2024年的估值", True),
        ):
            page.get_by_role("textbox", name="投研问题").fill(question)
            page.get_by_role("button", name="开始研究", exact=True).click()
            expect(page.get_by_role("dialog", name="确认研究范围")).to_be_visible()
            if annual:
                expect(page.get_by_label("开始日", exact=True)).to_have_value("2024-01-01")
                expect(page.get_by_label("结束日", exact=True)).to_have_value("2024-12-31")
            else:
                expect(page.get_by_label("数据窗口", exact=True)).to_contain_text("近三年")
            page.get_by_role("button", name="取消", exact=True).click()
        for question in ("平安银行 vs 贵州茅台", "贵州茅台按20%增长率重算"):
            page.get_by_role("textbox", name="投研问题").fill(question)
            page.get_by_role("button", name="开始研究", exact=True).click()
            expect(page.get_by_role("alert")).to_be_visible()
            assert page.locator(".scope-dialog").count() == 0
        page.get_by_role("textbox", name="投研问题").fill("全面分析平安银行")
        page.get_by_role("button", name="开始研究", exact=True).click()
        expect(page.get_by_role("dialog", name="确认研究范围")).to_be_visible()
        page.get_by_label("数据窗口", exact=True).select_option(index=1)
        with page.expect_response(
            lambda r: r.url.endswith("/api/quant/runs") and r.request.method == "POST"
        ) as started:
            page.get_by_role("button", name="确认并开始研究").click()
        assert started.value.status == 202
        new = started.value.json()
        page.wait_for_url("**/#/research/" + new["run_id"])
        expect(page.locator(".msg-user")).to_have_text("全面分析平安银行")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            detail = json.load(urlopen(url + "/api/quant/runs/" + new["run_id"]))
            if detail["run"]["status"] == "COMPLETED":
                break
            page.wait_for_timeout(500)
        assert detail["run"]["status"] == "COMPLETED" and detail["report_id"]
        assert detail["plan"]["question"] == "全面分析平安银行"
        assert all(n["status"] == "COMPLETED" for n in detail["process"])
        page.get_by_role("link", name="阅读完整报告").click(timeout=60000)
        page.locator(".chart-canvas canvas").first.wait_for(timeout=60000)
        page.reload(wait_until="networkidle")
        page.locator(".chart-canvas canvas").first.wait_for(timeout=60000)
        expect(page.get_by_role("heading", level=1)).to_have_text("全面分析平安银行")
        page.get_by_role("link", name="查看研究过程").click()
        page.get_by_role("button", name="展开过程").click(timeout=60000)
        expect(page.locator(".proc-step.completed")).to_have_count(5)
        page.screenshot(path=str(output / "research-completed.png"), full_page=True)
        page.get_by_role("navigation", name="主导航").get_by_role("link", name="历史库").click()
        page.get_by_role("searchbox", name="搜索历史研究").fill("全面分析平安银行")
        expect(page.locator(".lib-q").first).to_have_text("全面分析平安银行")
        page.locator(".lib-q").first.click()
        page.locator(".chart-canvas canvas").first.wait_for(timeout=60000)
        page.go_back(wait_until="networkidle")
        expect(page.get_by_role("heading", name="历史研究库")).to_be_visible()
        browser.close()
    assert not errors, errors
    result = dict(
        scenarios=cases,
        errors=errors,
        entry=new,
        final_status=detail["run"]["status"],
        report_id=detail["report_id"],
    )
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                scenarios=len(cases),
                errors=errors,
                run_id=new["run_id"],
                report_id=detail["report_id"],
            )
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.url.rstrip("/"), args.output)
