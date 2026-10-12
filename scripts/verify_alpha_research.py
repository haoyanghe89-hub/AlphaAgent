"""Browser acceptance against fresh upstream data and the configured real model."""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports/taskj/playwright-runtime"))
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def read(url):
    return json.load(urlopen(url, timeout=30))


def verify(url, output, first_run=None, reuse=()):
    output = output.resolve()
    if not output.is_relative_to(ROOT / ".phase3-quant-o") or output.exists():
        raise ValueError("new private output directory required")
    output.mkdir(parents=True)
    api = url + "/api/quant/research"
    summary = dict(runs=[], layouts=[], errors=[], verified=[])
    prior_runs = dict(item.split("=", 1) for item in reuse)
    if first_run:
        prior_runs["000333"] = first_run

    def checkpoint():
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page.on("pageerror", lambda error: summary["errors"].append(str(error)))

        def ready(*, real_agents=True):
            run_id = page.url.rsplit("/", 1)[-1]
            deadline = time.monotonic() + 600
            previous = None
            resumed = False
            while time.monotonic() < deadline:
                result = read(api + "/runs/" + run_id)
                status = (
                    result["status"],
                    sum(c["status"] == "COMPLETED" for c in result.get("calls", [])),
                )
                if status != previous:
                    print(
                        json.dumps(dict(run=run_id, status=status), ensure_ascii=False), flush=True
                    )
                    previous = status
                if result["status"] != "RUNNING":
                    if result["status"] in {"FAILED", "INTERRUPTED"} and not resumed:
                        page.get_by_role("button", name="继续未完成的研究", exact=True).click(
                            timeout=15000
                        )
                        resumed = True
                        summary["verified"].append("manual-resume-after-real-source-failure")
                        page.wait_for_timeout(2000)
                        continue
                    assert result["status"] == "COMPLETED", result.get("error")
                    break
                page.wait_for_timeout(2000)
            else:
                raise TimeoutError(run_id)
            data = result["data"]
            count = sum(v == "AVAILABLE" for v in data["coverage"].values())
            expect(page.locator(".prototype-chart canvas")).to_have_count(count, timeout=15000)
            expect(page.locator(".prototype-chart-empty")).to_have_count(0)
            for canvas in page.locator(".prototype-chart-canvas").all():
                option = json.loads(canvas.get_attribute("data-chart-style"))
                accent = page.evaluate(
                    "getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()"
                )
                assert option["color"][0] == accent
                for series in option["series"]:
                    assert any(point is not None for point in series["data"])
            if real_agents:
                calls = [
                    c
                    for c in result["calls"]
                    if c["kind"] == "model" and c["status"] == "COMPLETED"
                ]
                assert [c["role"] for c in calls] == [
                    "Planner",
                    "Data",
                    "Analyst",
                    "Verifier",
                    "Writer",
                ]
                assert all(c["model"] != "fake-for-tests" and c["input_tokens"] > 0 for c in calls)
                assert data["agent_report"]["claims"]
                assert data["retrieval"]["passages"] > 0
            assert "calculation_inputs" not in data
            expect(page.locator("#live-simulation .condition-tags")).to_contain_text(
                data["subject"]["name"]
            )
            return run_id, result

        for code, question in [
            ("000333", "全面分析美的集团"),
            ("000651", "分析格力电器的盈利能力和风险"),
            ("600519", "分析贵州茅台的估值与经营风险"),
            ("000001", "分析平安银行的盈利能力和估值"),
        ]:
            if code in prior_runs:
                page.goto(
                    url + "/#/live/" + prior_runs[code], wait_until="networkidle", timeout=60000
                )
            else:
                page.goto(url + "/#/", wait_until="networkidle", timeout=60000)
                expect(page.locator(".stock-card")).to_have_count(3)
                page.get_by_label("投研问题", exact=True).fill(question)
                page.get_by_role("button", name="开始研究", exact=True).click()
                page.wait_for_url("**/#/live/RUN-LIVE-*", timeout=30000)
            run_id, result = ready()
            assert result["data"]["subject"]["code"] == code
            assert all(s["retrieved_at"] >= result["created_at"] for s in result["data"]["sources"])
            if code == "000333":
                parent_id, parent = run_id, result
                assert result["data"]["dcf"]["status"] == "AVAILABLE"
            if code == "000001":
                assert result["data"]["dcf"]["status"] == "NOT_APPLICABLE"
                assert result["data"]["financial"]["status"] == "AVAILABLE"
                assert result["data"]["valuation"]["status"] == "AVAILABLE"
            summary["runs"].append(
                dict(
                    code=code,
                    run_id=run_id,
                    coverage=result["data"]["coverage"],
                    model_calls=[
                        {
                            k: c.get(k)
                            for k in (
                                "role",
                                "model",
                                "input_tokens",
                                "output_tokens",
                                "request_ref",
                                "response_ref",
                            )
                        }
                        for c in result["calls"]
                        if c["kind"] == "model"
                    ],
                    retrieval=result["data"]["retrieval"],
                )
            )
            page.screenshot(path=str(output / (code + ".png")), full_page=True)
            checkpoint()

        page.goto(url + "/#/live/" + parent_id, wait_until="networkidle")
        ready()
        page.locator(".conclusion-details > summary").click()
        page.locator(".conclusion-analysis .chip").first.click()
        expect(page.get_by_role("dialog", name="数据来源")).to_contain_text("定位")
        expect(page.get_by_role("dialog", name="数据来源")).to_contain_text("SHA256")
        page.keyboard.press("Escape")
        page.locator(".agent-trace summary").click()
        expect(page.locator(".agent-trace")).to_contain_text("deepseek")
        page.get_by_label("前 5 年增长率 %").fill("12")
        page.get_by_role("button", name="重算并保存新版本", exact=True).click()
        page.wait_for_url(lambda u: u.rsplit("/", 1)[-1] != parent_id, timeout=30000)
        child_id, child = ready(real_agents=False)
        assert child["data"]["dcf"]["per_share"] > parent["data"]["dcf"]["per_share"]
        assert "agent_report" not in child["data"]
        assert read(api + "/runs/" + parent_id) == parent
        page.get_by_label("研究追问", exact=True).fill("结合公告和财报，解释当前估值假设与主要风险")
        page.locator('form:has(input[aria-label="研究追问"]) button').click()
        page.wait_for_url(lambda u: u.rsplit("/", 1)[-1] != child_id, timeout=30000)
        followup_id, followup = ready()
        assert followup["parent_run_id"] == child_id
        assert followup["data"]["dcf"]["assumptions"] == child["data"]["dcf"]["assumptions"]
        assert all(
            s["retrieved_at"] > child["data"]["acquired_at"] for s in followup["data"]["sources"]
        )
        summary.update(parent=parent_id, scenario=child_id, followup=followup_id)
        checkpoint()

        page.get_by_role("link", name="查看历史记录", exact=True).click()
        page.get_by_label("搜索历史研究").fill(followup_id)
        page.locator(f'a[href="#/live/{followup_id}"]').click()
        ready()
        page.reload(wait_until="networkidle")
        assert ready()[1] == followup
        with urlopen(api + "/runs/" + followup_id + "/export", timeout=30) as response:
            text = response.read().decode()
            assert "Agent 解读" in text and "SHA256" in text
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
                summary["layouts"].append(dict(width=width, theme=theme, overflow=False))
        page.route("**/api/quant/research/quotes*", lambda route: route.abort("failed"))
        page.get_by_role("button", name="刷新行情", exact=True).click()
        expect(page.locator(".stock-price")).to_have_text("—", timeout=30000)
        page.unroute("**/api/quant/research/quotes*")
        page.get_by_role("button", name="刷新行情", exact=True).click()
        expect(page.locator(".stock-price")).not_to_have_text("—", timeout=30000)
        assert not summary["errors"], summary["errors"]
        summary["verified"] += [
            "four-fresh-stocks",
            "actual-five-roles",
            "filled-applicable-charts",
            "evidence-dialog",
            "trace",
            "dcf-version",
            "parent-immutable",
            "fresh-followup",
            "parameter-memory",
            "history-replay",
            "export",
            "quote-failure-clears",
            "six-layouts",
        ]
        checkpoint()
        browser.close()
    print(
        json.dumps(dict(output=str(output), verified=summary["verified"]), ensure_ascii=False),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--first-run", help="Reuse the first fresh request after an interrupted UI check"
    )
    parser.add_argument(
        "--reuse",
        action="append",
        default=[],
        metavar="CODE=RUN",
        help="Continue checking a previously created fresh run",
    )
    args = parser.parse_args()
    verify(args.url.rstrip("/"), args.output, args.first_run, args.reuse)
