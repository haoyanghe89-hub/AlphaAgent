"""Real browser version acceptance and source-derived prototype layout checks."""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def read(url):
    return json.load(urlopen(url))


def verify(url, output):
    output = output.resolve()
    if not output.is_relative_to(ROOT / ".phase3-quant-o") or output.exists():
        raise ValueError("new private acceptance output required")
    output.mkdir(parents=True)
    stock = read(url + "/api/quant/overview")["stocks"][0]
    parent = read(url + "/api/quant/runs/" + stock["run_id"])
    plan = {
        key: value
        for key, value in parent["plan"].items()
        if key not in {"parent_run_id", "changes"}
    }
    plan.update(
        question="全面分析" + stock["name"],
        rf=None,
        frozen_snapshot_ids=parent["input_snapshot_ids"],
    )
    root = json.load(
        urlopen(
            Request(
                url + "/api/quant/runs",
                data=json.dumps(plan).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )
    )
    stock["run_id"] = root["run_id"]
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        parent = read(url + "/api/quant/runs/" + root["run_id"])
        if parent["run"]["status"] == "COMPLETED":
            break
        time.sleep(0.5)
    assert parent["run"]["status"] == "COMPLETED"
    original = urlopen(url + "/api/reports/" + parent["report_id"]).read()
    original_citations = urlopen(url + "/api/reports/" + parent["report_id"] + "/citations").read()
    errors, children = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
        )
        context.add_init_script("localStorage.setItem('search-report-theme', 'dark')")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url + "/#/report/" + stock["run_id"], wait_until="networkidle")
        page.locator(".chart-canvas canvas").first.wait_for(timeout=60000)
        layout = page.evaluate("""() => {
          const css = selector => getComputedStyle(document.querySelector(selector));
          const root = getComputedStyle(document.documentElement);
          return {
            background:root.getPropertyValue('--bg-base').trim(),
            panel:root.getPropertyValue('--bg-panel').trim(),
            raised:root.getPropertyValue('--bg-raised').trim(),
            accent:root.getPropertyValue('--accent').trim(),
            secondary:root.getPropertyValue('--text-3').trim(),
            topbar:css('.topbar').height, page:css('.page').maxWidth,
            columns:css('.report-grid').gridTemplateColumns, gap:css('.report-grid').gap,
            body:css('body').fontSize, report:css('.rep-block-text').fontSize
          };
        }""")
        assert layout == dict(
            background="#0a0d12",
            panel="#10141b",
            raised="#161b25",
            accent="#e8b046",
            secondary="#5c6675",
            topbar="44px",
            page="1320px",
            columns="250px 1006px",
            gap="24px",
            body="13px",
            report="13.5px",
        ), layout
        expect(page.locator(".quote-strip .qs-item")).to_have_count(5)
        page.get_by_role("link", name="查看研究过程").click()
        page.get_by_role("textbox", name="研究追问").fill("增长率按12%重算")
        page.get_by_role("button", name="追问", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("尚未接入")
        assert page.url.endswith(stock["run_id"])
        current = stock["run_id"]
        for question in ("无风险利率按0重算", "重新核对收益与风险"):
            if "#/report/" in page.url:
                page.get_by_role("link", name="查看研究过程").click()
            page.get_by_role("textbox", name="研究追问").fill(question)
            with page.expect_response(
                lambda r: r.url.endswith("/followups") and r.request.method == "POST"
            ) as response:
                page.get_by_role("button", name="追问", exact=True).click()
            assert response.value.status == 202
            child = response.value.json()
            page.wait_for_url("**/#/research/" + child["run_id"])
            expect(page.locator(".msg-user")).to_have_text(question)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                detail = read(url + "/api/quant/runs/" + child["run_id"])
                if detail["run"]["status"] == "COMPLETED":
                    break
                page.wait_for_timeout(500)
            assert detail["run"]["status"] == "COMPLETED"
            assert detail["parent_run_id"] == current
            assert detail["input_snapshot_ids"] == parent["input_snapshot_ids"]
            assert detail["plan"]["asof"] == parent["plan"]["asof"]
            assert detail["methodology"]["explicit_zero_risk_free"]
            page.get_by_role("link", name="阅读完整报告").click(timeout=60000)
            page.locator(".chart-canvas canvas").first.wait_for(timeout=60000)
            expect(page.get_by_role("heading", level=1)).to_have_text(question)
            page.reload(wait_until="networkidle")
            expect(page.get_by_label("报告版本")).to_have_value(child["run_id"])
            children.append(child["run_id"])
            current = child["run_id"]
        assert [v["version"] for v in detail["versions"]] == [1, 2, 3]
        page.screenshot(path=str(output / "report-v3.png"), full_page=True)
        page.get_by_label("报告版本").select_option(stock["run_id"])
        page.wait_for_url("**/#/report/" + stock["run_id"])
        page.locator(".chart-canvas canvas").first.wait_for(timeout=60000)
        expect(page.get_by_label("报告版本")).to_have_value(stock["run_id"])
        page.screenshot(path=str(output / "report-v1.png"), full_page=True)
        assert urlopen(url + "/api/reports/" + parent["report_id"]).read() == original
        assert (
            urlopen(url + "/api/reports/" + parent["report_id"] + "/citations").read()
            == original_citations
        )
        restored = read(url + "/api/quant/runs/" + stock["run_id"])
        assert [v["citation_id"] for v in restored["values"]] == [
            v["citation_id"] for v in parent["values"]
        ]
        page.locator(".metric-cell .ev-num").first.click()
        popup = page.get_by_role("dialog", name="数值依据")
        expect(popup).to_be_visible()
        assert popup.evaluate("(node) => Math.round(node.getBoundingClientRect().width)") == 320
        page.screenshot(path=str(output / "evidence-popover.png"))
        page.keyboard.press("Escape")
        expect(popup).to_have_count(0)
        page.get_by_role("navigation", name="主导航").get_by_role("link", name="历史库").click()
        page.get_by_role("searchbox", name="搜索历史研究").fill("无风险利率按0重算")
        item = page.locator('.lib-q[href="#/report/' + children[0] + '"]')
        expect(item).to_have_count(1)
        item.click()
        page.locator(".chart-canvas canvas").first.wait_for(timeout=60000)
        assert page.url.endswith(children[0])
        browser.close()
    assert not errors, errors
    result = dict(
        parent=stock["run_id"],
        children=children,
        versions=[1, 2, 3],
        original_report_sha256=hashlib.sha256(original).hexdigest(),
        layout=layout,
        errors=errors,
    )
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
