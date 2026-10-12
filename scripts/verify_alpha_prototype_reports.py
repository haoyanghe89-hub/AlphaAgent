"""Actual browser checks for prototype report blocks, rendered chart styles and empty states."""

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
    if not output.is_relative_to(ROOT / ".phase3-quant-o") or output.exists():
        raise ValueError("new private acceptance output required")
    output.mkdir(parents=True)
    overview = json.load(urlopen(url + "/api/quant/overview"))
    stock = overview["stocks"][0]
    errors, cases = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width in (375, 768, 1440):
            for theme in ("light", "dark"):
                context = browser.new_context(viewport={"width": width, "height": 1000})
                context.add_init_script(f"localStorage.setItem('search-report-theme', '{theme}')")
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                for name, path in (
                    ("report", "report/" + stock["run_id"]),
                    ("stock", "stock/" + stock["code"]),
                    ("backtest", "backtest"),
                ):
                    page.goto(url + "/#/" + path, wait_until="networkidle")
                    page.locator(".prototype-chart-canvas canvas").first.wait_for(timeout=60000)
                    if name == "report":
                        expected = [
                            "基本面分析",
                            "估值分析",
                            "技术面与资金面",
                            "A股特色风险扫描",
                            "数据与来源总览",
                        ]
                        assert (
                            page.locator(".prototype-research-section > h2").all_text_contents()
                            == expected
                        )
                        expect(page.locator(".quant-exhibit")).to_have_count(3)
                        expect(
                            page.get_by_role("button", name="按当前假设重算", exact=True)
                        ).to_be_disabled()
                        page.get_by_role("link", name="数据与来源总览", exact=True).click()
                        page.locator("#sec-evidence .ev-num").first.click()
                        expect(page.get_by_role("dialog", name="数值依据")).to_be_visible()
                        page.keyboard.press("Escape")
                        page.get_by_role("link", name="基本面分析", exact=True).click()
                        chart = page.locator(".chart-canvas").first
                        style = json.loads(chart.get_attribute("data-chart-style"))
                        assert style["grid"]["left"] == 52 and style["grid"]["top"] == 30
                        assert style["legend"]["right"] == 0
                        assert style["textStyle"]["fontSize"] == 10.5
                        assert style["tooltip"]["axisPointer"]["type"] == "cross"
                        if theme == "dark":
                            assert style["color"][:3] == ["#e8b046", "#4cc2ff", "#f04a5e"]
                    elif name == "stock":
                        expect(page.locator(".prototype-chart")).to_have_count(4)
                        expect(page.locator(".quant-exhibit")).to_have_count(3)
                        kline = page.locator(
                            '[data-prototype-chart="kline"] .prototype-chart-canvas'
                        )
                        style = json.loads(kline.get_attribute("data-chart-style"))
                        assert style["series"][0]["type"] == "candlestick"
                        assert style["series"][0]["data"] == []
                        assert style["dataZoom"][1]["height"] == 14
                    else:
                        headings = page.locator(
                            ".backtest-report .rep-section > h2"
                        ).all_text_contents()
                        assert headings == [
                            "选股条件",
                            "绩效对比",
                            "净值曲线 vs 沪深300（含成本敏感性）",
                            "分年度表现",
                            "方法说明与过拟合提示",
                        ]
                        expect(page.get_by_text("条件预览 · 未执行", exact=True)).to_be_visible()
                        page.get_by_role("button", name="低PE高ROE高增长", exact=True).click()
                        expect(page.locator(".condition-tags")).to_contain_text("营收同比 ≥ 15%")
                        page.get_by_label("PE 上限（≤）", exact=True).fill("0")
                        expect(page.locator(".condition-tags")).to_contain_text("PE ≤ 0")
                        expect(page.locator(".bt-condition")).to_contain_text("PE≤0")
                        page.get_by_role("button", name="放宽版", exact=True).click()
                        expect(page.locator(".condition-tags")).to_contain_text("PE ≤ 40")
                        expect(page.locator(".condition-tags")).not_to_contain_text("营收同比")
                        expect(page.get_by_role("button", name="回测执行待接入")).to_be_disabled()
                        canvas = page.locator(
                            '[data-prototype-chart="nav"] .prototype-chart-canvas'
                        )
                        style = json.loads(canvas.get_attribute("data-chart-style"))
                        assert canvas.evaluate("node => node.getBoundingClientRect().height") == 340
                        assert [s["lineStyle"]["width"] for s in style["series"]] == [2, 1.5, 1, 1]
                        assert all(s["data"] == [] for s in style["series"])
                        assert style["dataZoom"][1]["height"] == 14
                        if theme == "dark":
                            assert style["color"] == ["#e8b046", "#4cc2ff", "#5c6675", "#f0a13a"]
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
        browser.close()
    assert not errors, errors
    result = dict(scenarios=cases, errors=errors, run_id=stock["run_id"])
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.url.rstrip("/"), args.output)
