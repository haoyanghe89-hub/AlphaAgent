"""Read-only UI acceptance of concise conclusions using saved real reports."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def verify(url, run_id, bank_id, output):
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".phase3-quant-o"):
        raise ValueError("new private output directory required")
    output.mkdir(parents=True)
    result = dict(layouts=[], checks=[], errors=[])
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width in (1440, 768, 375):
            for theme in ("dark", "light"):
                context = browser.new_context(viewport=dict(width=width, height=1000))
                context.add_init_script(
                    "localStorage.setItem('search-report-theme'," + json.dumps(theme) + ")"
                )
                page = context.new_page()
                page.on("pageerror", lambda error: result["errors"].append(str(error)))
                response = page.request.get(url + "/api/quant/research/runs/" + run_id)
                assert response.ok, response.text()
                original = response.json()
                assert original["status"] == "COMPLETED"
                page.goto(url + "/#/live/" + run_id, wait_until="networkidle")
                overview = page.get_by_label("结论速览", exact=True)
                expect(overview).to_be_visible()
                expect(overview.locator(".conclusion-item")).to_have_count(4)
                assert overview.locator(".conclusion-number").all_text_contents() == [
                    "01",
                    "02",
                    "03",
                    "04",
                ]
                expect(overview.locator(".conclusion-analysis").first).not_to_be_visible()
                expect(overview).to_contain_text("3.46%")
                expect(overview).to_contain_text("1.66%")
                expect(overview).to_contain_text("14.18倍")
                expect(overview.locator(".reader-summary")).to_contain_text(
                    "价格区间偏上，近期偏弱"
                )
                expect(overview).to_contain_text("低估待确认")
                expect(overview).to_contain_text("后续看")
                expect(overview).to_contain_text("2026-10-09")
                assert not overview.locator(".conclusion-details").get_attribute("open")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("html").get_attribute("data-theme") == theme
                if width == 1440:
                    assert overview.bounding_box()["height"] < 850
                if width == 1440 and theme == "dark":
                    number = overview.get_by_role("button", name="查看营收累计同比的依据")
                    number.focus()
                    page.keyboard.press("Enter")
                    dialog = page.get_by_role("dialog", name="数据来源", exact=True)
                    expect(dialog).to_be_visible()
                    expect(dialog).to_contain_text("3.456122")
                    expect(dialog).to_contain_text("SHA256")
                    page.keyboard.press("Escape")
                    overview.get_by_role("button", name="查看样本最高收盘价的依据").click()
                    expect(dialog).to_contain_text("区间位置=")
                    expect(dialog).to_contain_text("88.95")
                    expect(dialog).to_contain_text("不是价格历史分位")
                    page.keyboard.press("Escape")
                    overview.locator(".reader-details > summary").click()
                    expect(overview).to_contain_text("公司做什么")
                    expect(overview).to_contain_text("下一期财报")
                    expect(overview).to_contain_text("行业需求")
                    overview.locator(".reader-details > summary").click()
                    overview.locator(".conclusion-details > summary").click()
                    expect(overview.locator(".conclusion-analysis")).to_have_count(
                        len(original["data"]["agent_report"]["claims"])
                    )
                    overview.locator(".conclusion-analysis .chip").first.click()
                    expect(dialog).to_contain_text("定位")
                    page.keyboard.press("Escape")
                    overview.locator(".conclusion-details > summary").click()
                    result["checks"].extend(
                        [
                            "keyboard-highlight-opens-original-evidence",
                            "full-analysis-and-evidence-retained",
                            "derived-price-proof-has-method-and-original-source",
                            "watch-conditions-company-description-and-gaps-visible",
                        ]
                    )
                overview.scroll_into_view_if_needed()
                overview.screenshot(path=str(output / f"conclusions-{width}-{theme}.png"))
                dcf = page.get_by_label("DCF 假设解读", exact=True)
                expect(dcf).to_contain_text("压力测试")
                expect(dcf).to_contain_text("109.77 元")
                expect(dcf).to_contain_text("129.83 元")
                expect(dcf).to_contain_text("152.80 元")
                if width == 1440:
                    dcf.screenshot(path=str(output / f"dcf-{theme}.png"))
                result["layouts"].append(dict(width=width, theme=theme))
                if width == 1440 and theme == "light" and bank_id:
                    page.goto(url + "/#/live/" + bank_id, wait_until="networkidle")
                    bank = page.get_by_label("结论速览", exact=True)
                    expect(bank.locator(".conclusion-item")).to_have_count(4)
                    expect(bank.locator(".conclusion-list")).to_contain_text("工业 DCF 不适用")
                    bank.screenshot(path=str(output / "bank-light.png"))
                    result["checks"].append("bank-dcf-remains-not-applicable")
                unchanged = page.request.get(url + "/api/quant/research/runs/" + run_id).json()
                assert unchanged == original
                context.close()
                print(json.dumps(result["layouts"][-1]), flush=True)
        browser.close()
    assert not result["errors"], result["errors"]
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--run", required=True)
    parser.add_argument("--bank")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.url, args.run, args.bank, args.output)
