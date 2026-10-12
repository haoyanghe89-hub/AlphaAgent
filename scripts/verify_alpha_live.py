"""Real UI acceptance: fresh requests, all charts, repeated scenarios and history."""

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


def verify(url, output):
    output = output.resolve()
    if not output.is_relative_to(ROOT / ".phase3-quant-o") or output.exists():
        raise ValueError("new private output directory required")
    output.mkdir(parents=True)
    api = url + "/api/quant/live"
    cases, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="networkidle", timeout=60000)
        expect(page.locator(".stock-card")).to_contain_text("现场取数")
        page.screenshot(path=str(output / "workbench.png"))
        page.locator(".stock-card").click()
        page.wait_for_url("**/#/live/RUN-LIVE-*", timeout=30000)

        def ready():
            page.locator('[data-prototype-chart="nav"] canvas').wait_for(timeout=60000)
            expect(page.locator(".prototype-chart-empty")).to_have_count(0)
            expect(page.locator(".prototype-chart canvas")).to_have_count(6)
            run_id = page.url.rsplit("/", 1)[-1]
            result = read(api + "/runs/" + run_id)
            assert result["status"] == "COMPLETED", result.get("error")
            return run_id, result

        parent_id, parent = ready()
        assert len(parent["data"]["metrics"]) == 7
        for kind in ("financial_quarterly", "pe_history", "kline", "margin", "dcf_forecast", "nav"):
            canvas = page.locator(f'[data-prototype-chart="{kind}"] .prototype-chart-canvas')
            option = json.loads(canvas.get_attribute("data-chart-style"))
            assert all(len(s["data"]) > 0 for s in option["series"])
            assert option["color"][0] == "#e8b046"
        first_retrieval = parent["data"]["acquired_at"]
        page.locator(".conclusion-list .ev-num").first.click()
        expect(page.get_by_role("dialog", name="数据来源")).to_contain_text("SHA256")
        page.keyboard.press("Escape")
        with page.expect_response(lambda r: r.url.endswith("/live/quotes")) as refresh:
            page.get_by_role("button", name="刷新行情", exact=True).click()
        assert refresh.value.status == 200
        fresh_quote = refresh.value.json()
        assert fresh_quote["retrieved_at"] >= first_retrieval
        assert next(q for q in fresh_quote["quotes"] if q["code"] == "000333")["price"] > 0

        page.get_by_label("前 5 年增长率 %").fill("12")
        page.get_by_role("button", name="重算并保存新版本", exact=True).click()
        page.wait_for_url(lambda u: u.rsplit("/", 1)[-1] != parent_id, timeout=30000)
        child_id, child = ready()
        assert child["data"]["dcf"]["per_share"] > parent["data"]["dcf"]["per_share"]
        assert child["data"]["sources"] == parent["data"]["sources"]
        assert read(api + "/runs/" + parent_id) == parent

        # The server handles the first request, but the client loses its response.
        requests_seen = []

        def lose_once(route):
            requests_seen.append(route.request.post_data_json)
            if len(requests_seen) == 1:
                response = route.fetch()
                assert response.status == 201
                route.abort("failed")
            else:
                route.continue_()

        page.route("**/api/quant/live/runs/*/scenario", lose_once)
        page.get_by_label("快线天数").fill("10")
        page.get_by_label("慢线天数").fill("30")
        page.get_by_label("单侧成本 %").fill("0.30")
        before_count = len(read(api + "/runs"))
        page.get_by_role("button", name="重算策略并保存", exact=True).click()
        expect(page.locator('[role="alert"]')).to_contain_text("无法连接服务")
        page.get_by_role("button", name="重算策略并保存", exact=True).click()
        page.wait_for_url(lambda u: u.rsplit("/", 1)[-1] != child_id, timeout=30000)
        scenario_id, scenario = ready()
        assert len(requests_seen) == 2 and requests_seen[0] == requests_seen[1]
        assert len(read(api + "/runs")) == before_count + 1
        assert scenario["data"]["simulation"]["fast"] == 10
        assert scenario["data"]["simulation"]["cost_pct"] == 0.3
        assert scenario["data"]["simulation"]["metrics"] != child["data"]["simulation"]["metrics"]
        page.unroute("**/api/quant/live/runs/*/scenario", lose_once)
        page.get_by_text("展开全部调仓明细", exact=False).click()
        expect(page.locator("details").last.locator("tbody tr")).not_to_have_count(0)
        page.get_by_role("link", name="查看历史记录", exact=True).click()
        page.locator(f'a[href="#/live/{scenario_id}"]').click()
        assert ready()[0] == scenario_id
        before_count = len(read(api + "/runs"))
        page.reload(wait_until="networkidle")
        assert ready()[1]["result_sha256"] == scenario["result_sha256"]
        assert len(read(api + "/runs")) == before_count
        with urlopen(api + "/runs/" + scenario_id + "/export", timeout=20) as response:
            assert "单股研究模拟" in response.read().decode()

        # A new research run must go back to the sources rather than reuse the report.
        page.get_by_role("link", name="重新采集并研究", exact=True).click()
        page.wait_for_url(
            lambda u: "/RUN-LIVE-" in u and u.rsplit("/", 1)[-1] != scenario_id, timeout=30000
        )
        final_id, final = ready()
        assert final["data"]["acquired_at"] > first_retrieval
        assert all(s["retrieved_at"] > first_retrieval for s in final["data"]["sources"])

        for width in (375, 768, 1440):
            for theme in ("dark", "light"):
                page.set_viewport_size({"width": width, "height": 1000})
                page.evaluate("theme => localStorage.setItem('search-report-theme',theme)", theme)
                page.reload(wait_until="networkidle")
                ready()
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (
                    width,
                    theme,
                )
                page.screenshot(path=str(output / f"report-{width}-{theme}.png"), full_page=True)
                cases.append(dict(width=width, theme=theme, charts=6, overflow=False))

        page.route("**/api/quant/live/quotes", lambda route: route.abort("failed"))
        page.get_by_role("button", name="刷新行情", exact=True).click()
        expect(page.locator('[role="alert"]')).to_contain_text("无法连接服务")
        expect(page.locator(".stock-price")).to_have_text("—")
        page.unroute("**/api/quant/live/quotes")
        page.get_by_role("button", name="刷新行情", exact=True).click()
        expect(page.locator(".stock-price")).not_to_have_text("—")
        assert not errors, errors
        browser.close()
    summary = dict(
        parent=parent_id,
        dcf_version=child_id,
        strategy_version=scenario_id,
        final=final_id,
        cases=cases,
        errors=errors,
        data_dates=final["data"]["data_dates"],
        source_count=len(final["data"]["sources"]),
        fresh_quote=fresh_quote,
        verified=[
            "fresh-acquisition",
            "six-filled-charts",
            "source-dialog",
            "quote-refresh",
            "DCF-recalculation",
            "strategy-recalculation",
            "lost-response-idempotency",
            "history-replay",
            "parent-immutability",
            "export",
            "quote-failure-no-fallback",
        ],
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "fresh_quote"}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.url, args.output)
