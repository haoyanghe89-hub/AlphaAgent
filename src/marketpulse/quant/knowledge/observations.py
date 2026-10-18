"""Local append-only registrations, fixed-source observations and review candidates."""

import json
import re
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..contracts import digest
from ..decision import tushare
from ..decision.tools import SourceClient
from . import evaluation as e
from .inputs import SHANGHAI
from .models import InvestmentObservationRow, InvestmentStudyRow
from .supplements import SupplementStore


def refs(value):
    return sorted(set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(value))))


def calendars(client, initial, spec):
    sources, tables = [], {}
    start = min(r["cal_date"] for rows in initial["calendars"].values() for r in rows)
    end = (datetime.strptime(spec["start_after"], "%Y%m%d") + timedelta(days=400)).strftime(
        "%Y%m%d"
    )
    for exchange in sorted(initial["calendars"]):
        source, rows = tushare.fetch(
            client,
            "forward_calendar_" + exchange,
            "trade_cal",
            dict(exchange=exchange, start_date=start, end_date=end),
        )
        e.open_days(rows, exchange, spec["start_after"])
        tables[exchange] = rows
        sources.append(dict(role="calendar", exchange=exchange, source=source))
    return dict(calendars=tables, calendar_sources=sources)


def collect(client, state, now):
    report = state["data"]
    spec = report["forward_protocol"]
    local = now.astimezone(SHANGHAI)
    end = (
        local.date() if (local.hour, local.minute) >= (15, 30) else local.date() - timedelta(days=1)
    ).strftime("%Y%m%d")
    sources = []

    def read(role, api, params, **metadata):
        source, rows = tushare.fetch(client, role, api, params)
        sources.append(dict(role=role, source=source, **metadata))
        return rows

    tables = {}
    for exchange in sorted(spec["calendars"]):
        start = min(r["cal_date"] for r in spec["calendars"][exchange])
        tables[exchange] = read(
            "calendar",
            "trade_cal",
            dict(exchange=exchange, start_date=start, end_date=end),
            exchange=exchange,
        )
    stocks = []
    anchor = report["market_date"]
    future = any(
        e.open_days(rows, exchange, spec["start_after"], end) for exchange, rows in tables.items()
    )
    benchmark = (
        read("benchmark", "index_daily", dict(ts_code="000300.SH", start_date=anchor, end_date=end))
        if future
        else []
    )
    for c in report["candidates"]:
        sid = c["security_id"]
        params = dict(ts_code=sid, start_date=anchor, end_date=end)
        data = dict(security_id=sid, daily=[], adjustments=[], suspensions=[])
        if future:
            for role, api in (
                ("daily", "daily"),
                ("adjustments", "adj_factor"),
                ("suspensions", "suspend_d"),
            ):
                data[role] = read(role, api, params, security_id=sid)
        stocks.append(data)
    return dict(
        version=e.VERSION,
        through=end,
        captured_at=now.isoformat(),
        calendars=tables,
        stocks=stocks,
        benchmark=benchmark,
        sources=sources,
    )


def source_rows(repo, source, api):
    if (
        source.get("api_name") != api
        or source.get("source_family") != "TUSHARE"
        or source.get("url", "").rstrip("/") != tushare.URL
    ):
        raise ValueError("观察来源身份不一致")
    raw = repo.raw_bytes(source["blob_ref"])
    if (
        digest_bytes(raw) != source["sha256"]
        or source["blob_ref"].rsplit("/", 1)[1] != source["sha256"]
    ):
        raise ValueError("观察来源哈希不一致")
    return tushare.records(json.loads(raw))


def digest_bytes(raw):
    import hashlib

    return hashlib.sha256(raw).hexdigest()


def verify_protocol(repo, state):
    spec = repo.value(state["forward_protocol_ref"])
    base = e.protocol(state)
    if any(spec.get(k) != v for k, v in base.items()) or spec != state["data"]["forward_protocol"]:
        raise ValueError("前瞻协议未绑定原请求")
    expected = {}
    initial = repo.value(state["inputs_ref"])
    for entry in spec["calendar_sources"]:
        exchange = entry["exchange"]
        source = entry["source"]
        start = min(r["cal_date"] for rows in initial["calendars"].values() for r in rows)
        end = (datetime.strptime(spec["start_after"], "%Y%m%d") + timedelta(days=400)).strftime(
            "%Y%m%d"
        )
        if exchange in expected or source["parameters"] != dict(
            exchange=exchange, start_date=start, end_date=end
        ):
            raise ValueError("前瞻日历参数不一致")
        expected[exchange] = source_rows(repo, source, "trade_cal")
    if set(expected) != set(initial["calendars"]) or expected != spec["calendars"]:
        raise ValueError("前瞻日历不等于原文复算")
    return spec


def verify_snapshot(repo, state, snapshot):
    """Bind every derived row to a bounded public provider response."""
    if snapshot["version"] != e.VERSION:
        raise ValueError("未知观察输入版本")
    spec = state["data"]["forward_protocol"]
    anchor = state["data"]["market_date"]
    end = snapshot["through"]
    local = datetime.fromisoformat(snapshot["captured_at"]).astimezone(SHANGHAI)
    allowed = (
        local.date() if (local.hour, local.minute) >= (15, 30) else local.date() - timedelta(days=1)
    ).strftime("%Y%m%d")
    if end != allowed or end < anchor:
        raise ValueError("观察日期不符合真实取得时间")
    actual = {
        "calendars": {},
        "stocks": {
            c["security_id"]: dict(
                security_id=c["security_id"], daily=[], adjustments=[], suspensions=[]
            )
            for c in state["data"]["candidates"]
        },
        "benchmark": [],
    }
    seen = set()
    for entry in snapshot["sources"]:
        role = entry["role"]
        sid = entry.get("security_id")
        exchange = entry.get("exchange")
        source = entry["source"]
        key = (role, sid, exchange)
        if key in seen:
            raise ValueError("观察来源重复")
        seen.add(key)
        if datetime.fromisoformat(source["fetched_at"]) > datetime.fromisoformat(
            snapshot["captured_at"]
        ) + timedelta(minutes=3):
            raise ValueError("来源取得时间晚于观察")
        api = {
            "calendar": "trade_cal",
            "daily": "daily",
            "adjustments": "adj_factor",
            "suspensions": "suspend_d",
            "benchmark": "index_daily",
        }[role]
        params = source["parameters"]
        if role == "calendar":
            if exchange not in spec["calendars"] or params != dict(
                exchange=exchange,
                start_date=min(r["cal_date"] for r in spec["calendars"][exchange]),
                end_date=end,
            ):
                raise ValueError("观察日历参数不一致")
            actual["calendars"][exchange] = source_rows(repo, source, api)
        else:
            if role != "benchmark" and sid not in actual["stocks"]:
                raise ValueError("观察证券不在原研究中")
            if params != dict(
                ts_code="000300.SH" if role == "benchmark" else sid, start_date=anchor, end_date=end
            ):
                raise ValueError("观察行情参数不一致")
            rows = source_rows(repo, source, api)
            if role == "benchmark":
                actual["benchmark"] = rows
            else:
                actual["stocks"][sid][role] = rows
    if (
        actual["calendars"] != snapshot["calendars"]
        or list(actual["stocks"].values()) != snapshot["stocks"]
        or actual["benchmark"] != snapshot["benchmark"]
    ):
        raise ValueError("观察输入不等于原文复算")


class ObservationStore:
    def __init__(self, repo):
        self.repo = repo
        self.sessions = repo.base.sessions

    def register(self, state):
        if state["request"]["schema_version"] != "investment-research-v4":
            return None
        spec = e.protocol(state)
        ref = self.repo.blob(spec)
        try:
            with self.sessions.begin() as session:
                session.add(
                    InvestmentStudyRow(
                        run_id=state["run_id"], created_at=state["created_at"], protocol_ref=ref
                    )
                )
        except IntegrityError:
            with self.sessions() as session:
                row = session.get(InvestmentStudyRow, state["run_id"])
            if row.protocol_ref != ref:
                raise ValueError("前瞻登记不可变更") from None
        return spec

    def registration(self, state):
        with self.sessions() as session:
            row = session.get(InvestmentStudyRow, state["run_id"])
        if row is None:
            return None
        spec = self.repo.value(row.protocol_ref)
        if spec != e.protocol(state) or spec["registered_at"] != row.created_at:
            raise ValueError("前瞻登记与请求不一致")
        return spec

    def append(self, payload):
        key = digest(payload)
        record = self.repo.blob(payload)
        related = set(refs(payload))
        for ref in list(related):
            try:
                related.update(refs(self.repo.value(ref)))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        try:
            with self.sessions.begin() as session:
                session.add(
                    InvestmentObservationRow(
                        observation_id=key,
                        run_id=payload["run_id"],
                        created_at=payload["created_at"],
                        record_ref=record,
                        blob_refs_json=json.dumps(sorted(related)),
                    )
                )
        except IntegrityError:
            pass
        return dict(payload, observation_id=key)

    def history(self, state):
        with self.sessions() as session:
            rows = session.scalars(
                select(InvestmentObservationRow)
                .where(InvestmentObservationRow.run_id == state["run_id"])
                .order_by(
                    InvestmentObservationRow.created_at, InvestmentObservationRow.observation_id
                )
            ).all()
        result = []
        for row in rows:
            payload = self.repo.value(row.record_ref)
            if (
                digest(payload) != row.observation_id
                or payload["run_id"] != state["run_id"]
                or payload["created_at"] != row.created_at
                or payload["result_ref"] != state.get("result_ref")
            ):
                raise ValueError("观察记录身份或原判断绑定失败")
            if payload.get("snapshot_ref"):
                snapshot = self.repo.value(payload["snapshot_ref"])
                verify_snapshot(self.repo, state, snapshot)
                expected = e.evaluate(state["data"], self.repo.value(state["inputs_ref"]), snapshot)
                if expected != payload["evaluation"]:
                    raise ValueError("观察结论不等于封存数据复算")
            result.append(dict(payload, observation_id=row.observation_id))
        return result

    def view(self, state):
        spec = self.registration(state)
        if spec is None:
            return dict(
                status="NOT_ENROLLED",
                message="此研究早于前瞻登记，保留原报告；新研究会自动纳入，不回填历史成绩。",
            )
        records = self.history(state)
        history = [r for r in records if r.get("kind") == "OBSERVATION"]
        finished = {r.get("attempt_id") for r in history}
        incomplete = {
            r["attempt_id"]
            for r in records
            if r.get("kind") == "ACQUISITION_CHECKPOINT"
            and r.get("attempt_id")
            and r["attempt_id"] not in finished
        }
        latest = next((r for r in reversed(history) if r.get("evaluation")), None)
        return dict(
            status="ENROLLED",
            protocol=state.get("data", {}).get("forward_protocol", spec),
            research_status=state["status"],
            latest=latest,
            history=history,
            last_attempt=history[-1] if history else None,
            incomplete_acquisitions=len(incomplete),
        )

    def observe(self, state, *, now=None, collector=collect):
        if not self.registration(state):
            raise ValueError("原研究未事先登记，不能事后补成预测样本")
        if state["status"] != "COMPLETED":
            raise ValueError("研究完成后才能核对真实行情；未完成请求仍保留登记")
        now = now or datetime.now(UTC)
        attempt_id = digest(dict(run_id=state["run_id"], started_at=now.isoformat()))
        journal = dict(run_id=state["run_id"], request={}, calls=[], elapsed_seconds=0)

        # Every transport checkpoint is rooted even if collection is interrupted.
        def checkpoint():
            self.append(
                dict(
                    kind="ACQUISITION_CHECKPOINT",
                    attempt_id=attempt_id,
                    run_id=state["run_id"],
                    created_at=datetime.now(UTC).isoformat(),
                    result_ref=state["result_ref"],
                    journal=journal,
                )
            )

        client = SourceClient(
            self.repo,
            journal,
            checkpoint,
            Event(),
            max_requests=32,
            budget_profile=dict(max_seconds=180),
        )
        payload = dict(
            kind="OBSERVATION",
            attempt_id=attempt_id,
            run_id=state["run_id"],
            created_at=now.isoformat(),
            result_ref=state["result_ref"],
            protocol_ref=state["forward_protocol_ref"],
        )
        try:
            snapshot = collector(client, state, now)
            payload["snapshot_ref"] = self.repo.blob(snapshot)
            verify_snapshot(self.repo, state, snapshot)
            payload.update(
                evaluation=e.evaluate(
                    state["data"], self.repo.value(state["inputs_ref"]), snapshot
                ),
                status="RECORDED",
            )
        except Exception as error:
            payload.update(
                status="ACQUISITION_FAILED",
                error=str(error)[:180]
                if isinstance(error, ValueError)
                else "本次行情取数未完成，原研究和以往核对保留，可再次核对。",
            )
            payload.pop("snapshot_ref", None)
        payload["journal"] = journal
        return self.append(payload)

    def summary(self):
        # Include every registered request, including failed and not-yet-observed ones.
        with self.sessions() as session:
            ids = session.scalars(
                select(InvestmentStudyRow.run_id).order_by(InvestmentStudyRow.created_at)
            ).all()
        samples = []
        for rid in ids:
            state = self.repo.get(rid)
            view = self.view(state)
            spec = view["protocol"]
            if view["latest"]:
                samples.extend(view["latest"]["evaluation"]["stocks"])
                continue
            candidates = state.get("data", {}).get("candidates")
            for c in candidates or [
                dict(security_id=name, selected_route="UNASSIGNED")
                for name in state["request"]["entities"]
            ]:
                samples.append(
                    dict(
                        route_id=c["selected_route"],
                        profile_group=spec["profile_group"],
                        horizon_sessions=spec["horizon_sessions"],
                        status="PENDING_OBSERVATION"
                        if state["status"] == "COMPLETED"
                        else "RESEARCH_" + state["status"],
                    )
                )
        return e.summarize(samples)

    def suggest(self, state, rules):
        view = self.view(state)
        latest = view.get("latest")
        if latest is None:
            raise ValueError("先取得一份真实观察记录，再提交复盘候选")
        content = dict(
            original_result_ref=state["result_ref"],
            observation_id=latest["observation_id"],
            route_version=state["data"]["route_version"],
            knowledge_versions=state["data"]["knowledge"],
            evaluation=dict(
                through=latest["evaluation"]["through"],
                stocks=[
                    {k: v for k, v in stock.items() if k not in {"points", "signals"}}
                    | {
                        "risk_breach_dates": [
                            s["date"] for s in stock["signals"] if s.get("risk_reference_breached")
                        ]
                    }
                    for stock in latest["evaluation"]["stocks"]
                ],
            ),
            proposal="仅建议复核原方法及反例；未完成有无知识匹配实验，不调整阈值，不发布新规则。",
        )
        return SupplementStore(rules).submit(
            dict(
                request_key=str(uuid5(NAMESPACE_URL, "alphaagent-k6:" + latest["observation_id"])),
                title="前瞻复盘候选 · " + state["data"]["market_date"],
                text=json.dumps(content, ensure_ascii=False),
                kind="METHOD_IDEA",
                user_note="系统复盘候选，待出处、反例和双职责审核。",
                author_claim="AlphaAgent系统研究，非原作者结论",
            )
        )
