"""Exercise real bounded market acquisition and responsive UI without mocked data."""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402

expect.set_options(timeout=60000)


def verify(url, parent, output, acquire):
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".phase3-quant-o"):
        raise ValueError("new private output directory required")
    output.mkdir(parents=True)
    errors, cases = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=dict(width=1440, height=1000))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "/#/backtest/" + parent, wait_until="domcontentloaded")
        panel = page.get_by_label("全市场历史数据采集", exact=True)
        expect(panel).to_be_visible()
        path = url + f"/api/quant/backtests/{parent}/market"
        original = page.request.get(url + f"/api/quant/backtests/{parent}").json()
        if acquire:
            # Existing plans replace the initial disabled creation button after
            # the asynchronous inventory read. Re-resolve either label while
            # waiting instead of retaining the transient creation locator.
            button = panel.get_by_role(
                "button", name=re.compile(r"^(建立全市场清单并采集|继续采集缺失分区)$")
            )
            expect(button).to_be_enabled()
            with page.expect_response(
                lambda r: r.url == path and r.request.method == "POST", timeout=90000
            ) as response:
                button.click()
            assert response.value.status == 202, response.value.text()
            body = response.value.request.post_data_json
            (output / "request.json").write_text(json.dumps(body), encoding="utf-8")
            print(json.dumps(response.value.json()), flush=True)
            deadline = time.monotonic() + 220
            while True:
                live = page.request.get(path).json()
                if live["acquisition_status"] != "RUNNING":
                    break
                if time.monotonic() > deadline:
                    raise AssertionError("bounded market acquisition did not terminate")
                page.wait_for_timeout(2000)
            assert page.request.post(path, data=body).json() == response.value.json()
        state_response = page.request.get(path)
        assert state_response.status == 200, state_response.text()
        report = state_response.json()
        assert report["coverage"]["stock_count"] >= 5000
        assert report["coverage"]["delisted_count"] > 0
        assert report["coverage"]["frozen_count"] > 0
        assert report["admission_status"] == "BLOCKED" and report["strict_partition_count"] == 0
        assert report["acquisition_status"] != "RUNNING"
        assert page.request.get(url + f"/api/quant/backtests/{parent}").json() == original
        (output / "status.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps({k: v for k, v in report["coverage"].items() if k != "stocks"}), flush=True
        )
        page.reload(wait_until="domcontentloaded")
        expect(panel).to_contain_text(str(report["coverage"]["query_count"]))
        with page.expect_download() as dl:
            panel.get_by_role("link", name="导出全市场覆盖", exact=True).click()
        dl.value.save_as(output / "export.json")
        exported = json.loads((output / "export.json").read_text(encoding="utf-8"))
        assert exported["plan_hash"] == report["plan_hash"]
        assert len(exported["partitions"]) == report["coverage"]["frozen_count"]
        browser.close()
        for width in (375, 768, 1440):
            for theme in ("dark", "light"):
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport=dict(width=width, height=1000))
                context.add_init_script(
                    "localStorage.setItem('search-report-theme'," + json.dumps(theme) + ")"
                )
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url + "/#/backtest/" + parent, wait_until="domcontentloaded")
                panel = page.get_by_label("全市场历史数据采集", exact=True)
                expect(panel).to_contain_text(str(report["coverage"]["query_count"]))
                panel.locator("summary").filter(has_text="逐股覆盖明细").click()
                expect(panel.locator("tbody").last.locator("tr")).to_have_count(25)
                panel.get_by_role("button", name="下一页", exact=True).click()
                expect(panel).to_contain_text("26–50")
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                panel.locator("summary").filter(has_text="逐股覆盖明细").click()
                panel.locator("h2").scroll_into_view_if_needed()
                page.screenshot(path=str(output / f"market-{width}-{theme}.png"))
                cases.append(dict(width=width, theme=theme))
                print(json.dumps(cases[-1]), flush=True)
                browser.close()
    assert not errors, errors
    (output / "summary.json").write_text(
        json.dumps(
            dict(
                parent=parent,
                cases=cases,
                errors=errors,
                plan_hash=report["plan_hash"],
                frozen_count=report["coverage"]["frozen_count"],
            ),
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--parent", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--acquire", action="store_true")
    args = parser.parse_args()
    verify(args.url, args.parent, args.output, args.acquire)
