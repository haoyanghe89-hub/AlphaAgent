"""Single-owner app scheduler; no browser, Codex session or trading executor required."""

import asyncio
import time
from datetime import UTC, datetime

import requests

from marketpulse.infrastructure.storage.models import BlobError

from ..contracts import digest
from ..decision.next_session import service as next_session
from ..decision.next_session.store import ExperimentStore
from ..knowledge.observations import ObservationStore
from ..knowledge.research_store import InvestmentStore
from ..live.main_flow import MainFlowFeed
from ..live.market_dashboard import MarketDashboard, write
from . import briefing


def pending_next(repo, state, through):
    for pid in state.get("prediction_ids", []):
        p = repo.prediction(pid)
        latest = repo.outcome_versions(pid)
        if p.target_session.isoformat().replace("-", "") <= through and (
            not latest or latest[-1]["status"] not in {"SCORABLE", "UNSCORABLE"}
        ):
            return True
    return False


def pending_investment(view, through):
    latest = view.get("latest")
    if latest and any(
        s["status"] == "DATA_MISSING_UNSCORABLE" for s in latest["evaluation"]["stocks"]
    ):
        return True
    if latest and all(
        s["status"] in {"MATURED", "SUSPENDED_UNSCORABLE", "LATE_ISSUE_UNSCORABLE"}
        for s in latest["evaluation"]["stocks"]
    ):
        return False
    if (view.get("last_attempt") or {}).get("status") == "ACQUISITION_FAILED":
        return True
    return not latest or latest["evaluation"]["through"] < through


class DailyRuntime:
    def __init__(self, app):
        self.app, self.base = app, app.state.research_store
        self.root = self.base.legacy_root.parent
        self.events = ExperimentStore(self.base)
        self.market = MarketDashboard(self.root / "market-live-cache")
        self.funds = MainFlowFeed(self.base, self.market)
        app.state.market_dashboard, app.state.main_flow = self.market, self.funds
        self.stopping = asyncio.Event()
        self.task = None
        self.last_probe = self.last_market = self.last_funds = 0.0
        self.state = dict(
            enabled=True,
            instance_id=digest(str(self.root.resolve())),
            source=dict(status="CHECKING"),
            schedule=dict(
                prepare="09:10", publish="09:25", observe="15:40 / 18:10", timezone="Asia/Shanghai"
            ),
            last_action=None,
            calendar_status="CHECKING",
        )
        self.calendar = None
        self.last_calendar = 0.0

    def persist(self):
        self.state["checked_at"] = datetime.now(UTC).isoformat()
        write(self.root / "daily-status.json", self.state)

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        self.stopping.set()
        if self.task:
            # Finish the bounded in-flight collector before disposing its DB session factory.
            await self.task

    def probe(self):
        checked = datetime.now(UTC).isoformat()
        try:
            with requests.get(
                "https://api.tushare.pro", timeout=(5, 10), allow_redirects=False
            ) as response:
                status = "TLS_READY" if not response.is_redirect else "UNEXPECTED_REDIRECT"
        except requests.exceptions.SSLError:
            status = "TLS_FAILED"
        except requests.RequestException:
            status = "NETWORK_FAILED"
        self.state["source"] = dict(status=status, checked_at=checked, token_sent=False)

    def trading_days(self, now):
        # A source-bound exchange calendar already on disk remains usable during a vendor outage.
        activation = self.events.activation(next_session.template().experiment_id)
        calendar = (activation or {}).get("calendars", {}).get("SSE")
        if (
            calendar
            and calendar["coverage_start"] <= now.date().isoformat() <= calendar["coverage_end"]
        ):
            for ref in calendar["source_refs"]:
                self.events.raw_bytes(ref)
            self.state["calendar_status"] = "ARCHIVED_EXCHANGE_CALENDAR"
            return [d.replace("-", "") for d in calendar["sessions"]]
        # New installations and future years use a newly sealed, bounded provider calendar.
        from threading import Event

        from ..decision import tushare
        from ..decision.tools import SourceClient

        journal = dict(request=dict(budget_profile=dict(max_seconds=45)), calls=[])
        key = "DEC-DAILY-CALENDAR-" + now.strftime("%Y%m%d")
        if self.events.exists(key):
            saved = self.events.record(key)
            self.state["calendar_status"] = "TUSHARE_CALENDAR"
            return saved["sessions"]
        client = SourceClient(self.events, journal, lambda: None, Event(), max_requests=2)
        source, rows = tushare.fetch(
            client,
            "daily_calendar",
            "trade_cal",
            dict(exchange="SSE", start_date=f"{now.year}0101", end_date=f"{now.year}1231"),
        )
        if (
            not rows
            or len({r["cal_date"] for r in rows}) != len(rows)
            or any(r["exchange"] != "SSE" for r in rows)
        ):
            raise ValueError("交易日历缺失或冲突")
        days = sorted(r["cal_date"] for r in rows if int(r["is_open"]) == 1)
        self.events.immutable(key, dict(sessions=days, source=source, calls=journal["calls"]))
        self.state["calendar_status"] = "TUSHARE_CALENDAR"
        return days

    async def exclusive(self, key, action):
        active = self.app.state.research_jobs
        if any(not t.done() for t in active.values()):
            return False
        # Reserve capacity before yielding so UI research and scheduled research cannot race.
        task = asyncio.create_task(action())
        active[key] = task
        try:
            await asyncio.shield(task)
        finally:
            active.pop(key, None)
        return True

    async def observe_one(self, now, days):
        local_stamp = now.strftime("%Y%m%d")
        through = max(
            (
                d
                for d in days
                if d < local_stamp or d == local_stamp and now.strftime("%H:%M") >= "15:40"
            ),
            default=None,
        )
        if not through:
            return
        slot = "LATE" if now.strftime("%H:%M") >= "18:10" and local_stamp == through else "EARLY"
        candidates = [
            ("NEXT", s) for s in self.events.records("RUN-NS-") if s.get("prediction_ids")
        ]
        candidates += [
            ("INVESTMENT", s)
            for s in self.events.records("RUN-IR-")
            if s["status"] == "COMPLETED"
            and s["request"]["schema_version"] == "investment-research-v4"
        ]
        for kind, state in candidates:
            if self.stopping.is_set():
                return
            rid = state["run_id"]
            repo = InvestmentStore(self.base)
            key = digest([kind, rid, through, slot])
            if self.events.exists("DEC-DAILY-START-" + key):
                continue
            try:
                if kind == "NEXT":
                    state = self.events.get_run(rid)
                    if not pending_next(self.events, state, through):
                        continue
                else:
                    state = repo.get(rid)
                    if not pending_investment(ObservationStore(repo).view(state), through):
                        continue
            except (ValueError, KeyError, OSError, BlobError):
                self.events.immutable(
                    "DEC-DAILY-START-" + key,
                    dict(
                        run_id=rid,
                        kind=kind,
                        through=through,
                        slot=slot,
                        status="INTEGRITY_FAILED",
                        checked_at=now.isoformat(),
                    ),
                )
                self.state["last_action"] = dict(run_id=rid, status="INTEGRITY_FAILED")
                continue

            async def action(key=key, rid=rid, kind=kind, state=state, repo=repo):
                self.events.immutable(
                    "DEC-DAILY-START-" + key,
                    dict(
                        run_id=rid,
                        kind=kind,
                        through=through,
                        slot=slot,
                        started_at=now.isoformat(),
                    ),
                )
                if kind == "NEXT":
                    result = await asyncio.to_thread(next_session.observe, self.events, state)
                    status = (
                        "RECORDED"
                        if all(
                            o["status"] in {"SCORABLE", "UNSCORABLE"} for o in result["outcomes"]
                        )
                        else "PENDING_DATA"
                    )
                else:
                    result = await asyncio.to_thread(ObservationStore(repo).observe, state)
                    status = result["status"]
                event = dict(
                    run_id=rid,
                    kind=kind,
                    through=through,
                    slot=slot,
                    status=status,
                    finished_at=datetime.now(UTC).isoformat(),
                )
                self.events.immutable("DEC-DAILY-END-" + key, event)
                self.state["last_action"] = event

            if await self.exclusive("daily:observe:" + rid, action):
                return  # One bounded record per tick; user research gets capacity between records.

    async def morning(self, now, days):
        day = now.strftime("%Y%m%d")
        if self.state.get("schedule_date") != day:
            self.state["schedule_date"] = day
            self.state.pop("morning_status", None)
        if day not in days or not "09:10" <= now.strftime("%H:%M") < "09:30":
            if day in days and now.strftime("%H:%M") >= "09:30":
                rid = briefing.identity(day)
                if not self.base.exists(rid):
                    if not self.events.exists("DEC-DAILY-MISSED-" + day):
                        self.events.immutable(
                            "DEC-DAILY-MISSED-" + day,
                            dict(
                                trade_date=day,
                                reason="09:30前服务未生成盘前任务，未事后回填",
                                checked_at=now.isoformat(),
                            ),
                        )
                    self.state["morning_status"] = "MISSED_WINDOW"
            return
        state, fresh = briefing.create(self.base, day)
        if not fresh:
            # Explicit resume preserves spent budgets after failure or interruption.
            return

        async def action():
            await briefing.run(
                self.base, self.app.state.knowledge_rules, state["run_id"], self.app.state.settings
            )
            value = self.base.get(state["run_id"], with_data=False)
            self.state["last_action"] = dict(
                kind="MORNING",
                run_id=state["run_id"],
                status=value["status"],
                finished_at=datetime.now(UTC).isoformat(),
            )

        await self.exclusive("daily:morning", action)

    async def tick(self, now=None):
        now = (now or datetime.now(UTC)).astimezone(briefing.ZONE)
        if any(not t.done() for t in self.app.state.research_jobs.values()):
            return
        monotonic = time.monotonic()
        if not self.last_probe or monotonic - self.last_probe >= 3600:
            await asyncio.to_thread(self.probe)
            self.last_probe = monotonic
        if self.calendar is None and monotonic - self.last_calendar >= 300:
            self.last_calendar = monotonic
            try:
                self.calendar = await asyncio.to_thread(self.trading_days, now)
            except (ValueError, OSError):
                self.state["calendar_status"] = "UNAVAILABLE"
        # Re-evaluate calendar on a year boundary, never infer holidays from weekdays.
        if self.calendar and not any(d.startswith(str(now.year)) for d in self.calendar):
            self.calendar = None
        if self.calendar:
            await self.morning(now, self.calendar)
            if not self.stopping.is_set():
                await self.observe_one(now, self.calendar)
        trading = self.calendar and now.strftime("%Y%m%d") in self.calendar
        intraday = trading and (
            "09:15" <= now.strftime("%H:%M") <= "11:30"
            or "13:00" <= now.strftime("%H:%M") <= "15:00"
        )
        if monotonic - self.last_market >= (30 if intraday else 600):
            self.last_market = monotonic
            try:
                value = await asyncio.to_thread(self.market.snapshot)
                self.state["market"] = dict(
                    quoted_at=value["quoted_to"],
                    retrieved_at=value["retrieved_at"],
                    status=value["freshness"],
                    error=value.get("refresh_error"),
                )
            except (ValueError, OSError):
                self.state["market"] = dict(status="UNAVAILABLE")
        if monotonic - self.last_funds >= 600 and not self.stopping.is_set():
            self.last_funds = monotonic
            try:
                value = await asyncio.to_thread(self.funds.daily)
                self.state["funding_status"] = (
                    "DELAYED" if value.get("refresh_error") else "AVAILABLE"
                )
            except (ValueError, OSError):
                self.state["funding_status"] = "UNAVAILABLE"
        self.persist()

    async def loop(self):
        while not self.stopping.is_set():
            try:
                await self.tick()
                self.state.pop("runtime_error", None)
            except Exception:
                self.state["runtime_error"] = "自动更新本轮未完成，历史保留；稍后自动重试。"
                self.persist()
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=30)
            except TimeoutError:
                pass
