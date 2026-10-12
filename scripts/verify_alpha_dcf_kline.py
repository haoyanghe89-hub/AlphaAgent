"""Real browser acceptance: actual candles, source cells and versioned DCF controls."""

import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def read(url):
    return json.load(urlopen(url, timeout=30))


def verify(url, output, parent_id=None, recalc=True):
    output = output.resolve()
    if not output.is_relative_to(ROOT / ".phase3-quant-o") or output.exists():
        raise ValueError("new private acceptance output required")
    output.mkdir(parents=True)
    stocks = read(url + "/api/quant/overview")["stocks"]
    stock = next(s for s in stocks if s["code"] == "000333")
    if parent_id:
        stock["run_id"] = parent_id
    bank = next(s for s in stocks if s["code"] == "000001")
    parent = read(url + "/api/quant/runs/" + stock["run_id"])
    original = urlopen(url + "/api/reports/" + parent["report_id"]).read()
    cases, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width in (375, 768, 1440):
            for theme in ("light", "dark"):
                context = browser.new_context(
                    viewport={"width": width, "height": 1000}, reduced_motion="reduce"
                )
                context.add_init_script(f"localStorage.setItem('search-report-theme', '{theme}')")
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                for name, path in (
                    ("report", "report/" + stock["run_id"]),
                    ("stock", "stock/000333"),
                ):
                    page.goto(url + "/#/" + path, wait_until="networkidle")
                    canvas = page.locator('[data-prototype-chart="kline"] .prototype-chart-canvas')
                    canvas.locator("canvas").wait_for(timeout=60000)
                    style = json.loads(canvas.get_attribute("data-chart-style"))
                    assert [s["type"] for s in style["series"]] == [
                        "candlestick",
                        "line",
                        "line",
                        "bar",
                    ]
                    assert len(style["series"][0]["data"]) >= 200
                    assert any(v is not None for v in style["series"][2]["data"])
                    assert style["series"][0]["data"] == parent["klines"][0]["ohlc"]
                    assert style["grid"][0]["left"] == 56
                    assert style["dataZoom"][1]["height"] == 14
                    assert style["dataZoom"][1]["start"] == 55
                    expect(page.locator(".kline-block .prototype-chart-empty")).to_have_count(0)
                    page.get_by_label("K线价格口径").select_option("raw")
                    raw = next(b for b in parent["klines"] if b["adjustment"] == "raw")
                    expect(page.locator(".kline-block .report-chart-title")).to_contain_text(
                        "未复权"
                    )
                    style = json.loads(canvas.get_attribute("data-chart-style"))
                    assert style["series"][0]["data"] == raw["ohlc"]
                    page.locator(".kline-evidence summary").click()
                    page.locator(".kline-evidence .ev-num").first.click()
                    expect(page.get_by_role("dialog", name="数值依据")).to_be_visible()
                    expect(page.locator(".evidence-dialog")).to_contain_text("frozen_OHLC")
                    page.keyboard.press("Escape")
                    if name == "report":
                        expect(
                            page.get_by_role("button", name="按当前假设重算", exact=True)
                        ).to_be_enabled()
                        assert (
                            page.get_by_label("阶段1增速（5年）").evaluate(
                                "el => getComputedStyle(el).height"
                            )
                            == "3px"
                        )
                        expect(page.locator(".dcf-matrix tbody tr")).to_have_count(5)
                        expect(page.locator(".dcf-matrix .ev-num")).to_have_count(25)
                        page.locator(".dcf-result .big").first.click()
                        expect(page.locator(".evidence-dialog")).to_contain_text("FCFF=")
                        expect(page.locator(".evidence-dialog")).to_contain_text("尚未验证")
                        page.keyboard.press("Escape")
                        page.locator(".dcf-matrix .ev-num").first.click()
                        expect(page.locator(".evidence-dialog")).to_contain_text("CNY/share")
                        page.keyboard.press("Escape")
                        page.locator("#sec-valuation").screenshot(
                            path=str(output / f"dcf-{width}-{theme}.png")
                        )
                    page.locator(".kline-block").screenshot(
                        path=str(output / f"kline-{name}-{width}-{theme}.png")
                    )
                    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (
                        width,
                        theme,
                        name,
                    )
                    cases.append(dict(width=width, theme=theme, page=name))
                context.close()
        if not recalc:
            browser.close()
            assert not errors, errors
            summary = dict(cases=cases, errors=errors, parent_run_id=stock["run_id"])
            (output / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(summary, ensure_ascii=False))
            return
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "/#/report/" + bank["run_id"], wait_until="networkidle")
        expect(page.get_by_role("button", name="按当前假设重算", exact=True)).to_be_disabled()
        expect(page.locator(".dcf-playground")).to_contain_text("金融企业需单独估值模型")
        page.goto(url + "/#/report/" + stock["run_id"], wait_until="networkidle")
        slider = page.get_by_label("阶段1增速（5年）")
        slider.focus()
        slider.press("Home")
        for _ in range(32):
            slider.press("ArrowRight")
        assert slider.input_value() == "12"
        page.get_by_role("button", name="按当前假设重算", exact=True).click()
        page.wait_for_url("**/#/research/**", timeout=30000)
        child_id = page.url.rsplit("/", 1)[-1]
        assert child_id != stock["run_id"]
        page.get_by_role("link", name="阅读完整报告", exact=True).wait_for(timeout=240000)
        page.get_by_role("link", name="阅读完整报告", exact=True).click()
        expect(page.locator(".version-select")).to_have_count(1)
        child = read(url + "/api/quant/runs/" + child_id)
        assert child["plan"]["dcf"]["g1"] == "0.12"
        assert child["input_snapshot_ids"] == parent["input_snapshot_ids"]
        assert (
            child["dcf"]["cells"]["per_share"]["value"]
            != parent["dcf"]["cells"]["per_share"]["value"]
        )
        assert urlopen(url + "/api/reports/" + parent["report_id"]).read() == original
        assert (
            read(url + "/api/quant/runs/" + stock["run_id"])["output_hash"] == parent["output_hash"]
        )
        page.locator(".version-select").select_option(stock["run_id"])
        page.wait_for_url("**/#/report/" + stock["run_id"])
        expect(page.get_by_label("阶段1增速（5年）")).to_have_value("8")
        page.reload(wait_until="networkidle")
        expect(page.get_by_label("阶段1增速（5年）")).to_have_value("8")
        context.close()
        browser.close()
    assert not errors, errors
    summary = dict(
        cases=cases,
        errors=errors,
        parent_run_id=stock["run_id"],
        child_run_id=child_id,
        versions=len(child["versions"]),
        parent_bytes_unchanged=True,
        parameters_pinned=True,
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8834")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parent")
    parser.add_argument("--no-recalc", action="store_true")
    args = parser.parse_args()
    verify(args.url, args.output, args.parent, not args.no_recalc)
