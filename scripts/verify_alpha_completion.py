"""Browser acceptance of delivery flows; production never receives synthetic NAV."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def verify(url, output, comparison=None, test_url=None):
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".phase3-quant-o"):
        raise ValueError("new private output directory required")
    output.mkdir(parents=True)
    errors = []
    summary = dict(
        production=url, synthetic_test_service=test_url, layouts=[], comparison=comparison
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=dict(width=1440, height=1100))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "/#/delivery", wait_until="networkidle")
        expect(page.get_by_role("heading", name="功能与数据接入", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="后续数据接入位置", exact=True)).to_be_visible()
        assert page.request.get(url + "/api/quant/strategies/schema").json()["independent_results"]
        page.goto(url + "/#/", wait_until="networkidle")
        page.get_by_label("投研问题", exact=True).fill("000333 vs 000651，比较估值")
        page.get_by_role("button", name="开始研究", exact=True).click()
        expect(page.get_by_label("第一只股票", exact=True)).to_have_value("000333")
        expect(page.get_by_label("第二只股票", exact=True)).to_have_value("000651")
        page.goto(url + "/#/strategy", wait_until="networkidle")
        page.get_by_role("button", name="确认条件并执行完整回测", exact=True).click()
        expect(page.get_by_text("数据受限 · 未计算收益", exact=True)).to_be_visible(timeout=20000)
        blocked_run = page.url.rsplit("/", 1)[1]
        original = page.request.get(url + "/api/quant/deliveries/" + blocked_run).json()
        assert original["data"]["execution_status"] == "BLOCKED" and "nav" not in original["data"]
        page.reload(wait_until="networkidle")
        expect(page.get_by_text("数据受限 · 未计算收益", exact=True)).to_be_visible()
        assert (
            page.request.get(url + "/api/quant/deliveries/" + blocked_run + "/export").json()[
                "data"
            ]
            == original["data"]
        )
        page.goto(url + "/#/library", wait_until="networkidle")
        expect(page.locator('a[href="#/strategy/' + blocked_run + '"]')).to_be_visible()
        expect(page.get_by_text("检查完成 · 回测受阻", exact=True).first).to_be_visible()
        summary["blocked_run"] = blocked_run
        paths = ["delivery", "strategy/" + blocked_run, "compare"]
        if comparison:
            state = page.request.get(url + "/api/quant/deliveries/" + comparison).json()
            assert state["status"] == "COMPLETED", state.get("error")
            assert all(b["status"] == "COMPLETED" for b in state["branches"])
            for b in state["branches"]:
                child = page.request.get(url + "/api/quant/research/runs/" + b["run_id"]).json()
                assert {
                    c["role"]
                    for c in child["calls"]
                    if c["kind"] == "model" and c["status"] == "COMPLETED"
                } == {"Planner", "Data", "Analyst", "Verifier", "Writer"}
                assert child["data"]["sources"]
            assert len(state["data"]["chart"]["x"]) > 120
            assert len(state["presentation"]["dimensions"]) == 5
            page.goto(url + "/#/compare/" + comparison, wait_until="networkidle")
            expect(page.get_by_label("第一只股票", exact=True)).to_have_value(
                state["request"]["entities"][0]
            )
            page.locator(".ev-num").first.click()
            expect(page.get_by_role("dialog", name="比较数值依据")).to_contain_text("SHA256")
            page.keyboard.press("Escape")
            assert (
                page.request.get(url + "/api/quant/deliveries/" + comparison + "/export").json()[
                    "data"
                ]
                == state["data"]
            )
            paths[-1] = "compare/" + comparison
        for theme in ("dark", "light"):
            for width in (1440, 768, 375):
                page.set_viewport_size(dict(width=width, height=1100))
                for path in paths:
                    page.goto(url + "/#/" + path, wait_until="networkidle")
                    current = page.locator("html").get_attribute("data-theme")
                    if current != theme:
                        page.get_by_role(
                            "button",
                            name="切换浅色" if theme == "light" else "切换深色",
                            exact=True,
                        ).click()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth + 1"
                    ), (theme, width, path)
                    name = path.split("/")[0] + "-" + theme + "-" + str(width)
                    page.evaluate("window.scrollTo(0,0)")
                    page.screenshot(path=str(output / (name + ".png")), full_page=True)
                    summary["layouts"].append(name)
        if test_url:
            page.set_viewport_size(dict(width=1440, height=1100))
            page.goto(test_url + "/#/strategy", wait_until="networkidle")
            page.get_by_label("冻结历史数据包").select_option("synthetic-ui-test-only")
            page.get_by_role("button", name="确认条件并执行完整回测", exact=True).click()
            expect(page.get_by_text("计算完成 · 指标待独立核验", exact=True)).to_be_visible(
                timeout=20000
            )
            run = page.url.rsplit("/", 1)[1]
            result = page.request.get(test_url + "/api/quant/deliveries/" + run).json()["data"]
            assert (
                result["ledgers"]["strategy"]["orders"]
                and result["metrics"]["total_ret"] is not None
            )
            canvas = page.locator('[data-prototype-chart="nav"] .prototype-chart-canvas')
            expect(canvas).to_have_attribute("data-chart-style", timeout=15000)
            style = json.loads(canvas.get_attribute("data-chart-style"))
            assert len(style["series"]) == 4 and all(s["data"] for s in style["series"])
            assert style["series"][0]["lineStyle"]["width"] == 2
            page.locator(".annual-result").first.click()
            expect(page.locator(".annual-holdings")).to_contain_text("选中不代表全部成交")
            page.get_by_role("button", name="使用冻结原始输入离线复算", exact=True).click()
            expect(page.get_by_text("MATCH · 同引擎复算", exact=True)).to_be_visible(timeout=20000)
            for theme in ("dark", "light"):
                for width in (1440, 768, 375):
                    page.set_viewport_size(dict(width=width, height=1100))
                    current = page.locator("html").get_attribute("data-theme")
                    if current != theme:
                        page.get_by_role(
                            "button",
                            name="切换浅色" if theme == "light" else "切换深色",
                            exact=True,
                        ).click()
                    page.wait_for_function(
                        "document.documentElement.scrollWidth <= innerWidth + 1", timeout=5000
                    )
                    page.evaluate("window.scrollTo(0,0)")
                    page.screenshot(
                        path=str(
                            output / ("TEST-ONLY-strategy-" + theme + "-" + str(width) + ".png")
                        ),
                        full_page=True,
                    )
            summary["synthetic_test_run"] = run
            summary["synthetic_test_four_curves"] = style
        assert not errors, errors
        browser.close()
    summary["page_errors"] = errors
    (output / "results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("delivery, data gaps, history, themes and isolated four-curve strategy passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison")
    parser.add_argument("--test-url")
    args = parser.parse_args()
    verify(args.url, args.output, args.comparison, args.test_url)
