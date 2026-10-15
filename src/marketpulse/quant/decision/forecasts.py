"""Append-only medium-term ledger; publication and run completion share a transaction."""

import copy
import json
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import JSON, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.investigation.persistence.base import Base

from ..contracts import digest
from ..live.sources import KLINE
from ..live.store import ResearchRunRow
from .contracts import ForecastRecord, OutcomeRecord
from .market import number
from .next_session.calendar import parse_annual
from .tools import SourceClient


class ForecastRow(Base):
    __tablename__ = "inv_quant_decision_forecast"
    forecast_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(100), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    record_ref: Mapped[str] = mapped_column(String(200))
    report_ref: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON)


class ObservationRow(Base):
    __tablename__ = "inv_quant_decision_observation"
    __table_args__ = (UniqueConstraint("forecast_id", "input_hash"),)
    observation_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    forecast_id: Mapped[str] = mapped_column(String(100))
    input_hash: Mapped[str] = mapped_column(String(64))
    record_ref: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON)


def initialize(repo):
    engine = repo.base.sessions.kw["bind"]
    ForecastRow.__table__.create(engine, checkfirst=True)
    ObservationRow.__table__.create(engine, checkfirst=True)


def verify_binding(repo, state):
    initialize(repo)
    with repo.base.sessions() as session:
        row = session.get(ForecastRow, state.get("forecast_id"))
        if (
            not row
            or row.run_id != state["run_id"]
            or row.report_ref != state["result_ref"]
            or row.payload != state["data"]["forecast"]
            or digest(row.payload) != row.content_hash
            or repo.value(row.record_ref) != row.payload
        ):
            raise ValueError("original forecast/report binding failed")


def record_forecast(repo, record, *, state=None):
    initialize(repo)
    value = record.model_dump(mode="json")
    record_ref, content_hash = repo.blob(value), digest(value)
    report_ref = state["result_ref"] if state else ""
    with repo.base.sessions.begin() as session:
        old = session.get(ForecastRow, record.forecast_id)
        if old:
            if old.payload != value or old.content_hash != content_hash:
                raise ValueError("forecast identity conflict")
            if repo.value(old.record_ref) != value:
                raise ValueError("forecast original damaged")
            return record
        if record.supersedes_id:
            parent = session.get(ForecastRow, record.supersedes_id)
            if (
                not parent
                or parent.payload["subject_id"] != record.subject_id
                or datetime.fromisoformat(parent.payload["issued_at"]) > record.issued_at
            ):
                raise ValueError("forecast parent identity/time conflict")
            if repo.value(parent.record_ref) != parent.payload:
                raise ValueError("forecast parent damaged")
        session.add(
            ForecastRow(
                forecast_id=record.forecast_id,
                run_id=record.run_id,
                record_ref=record_ref,
                content_hash=content_hash,
                report_ref=report_ref,
                payload=value,
            )
        )
        if state:
            row = session.get(ResearchRunRow, state["run_id"])
            if not row or row.status != "RUNNING" or row.payload["request"] != state["request"]:
                raise ValueError("cancelled/completed run cannot publish")
            row.status, row.payload = "COMPLETED", copy.deepcopy(state)
    return record


def forecast(repo, run_id):
    initialize(repo)
    with repo.base.sessions() as session:
        row = session.scalar(select(ForecastRow).where(ForecastRow.run_id == run_id))
        if row is None:
            return None
        value, ref, hash_value = copy.deepcopy(row.payload), row.record_ref, row.content_hash
        report_ref = row.report_ref
    if (
        digest(value) != hash_value
        or BlobRef.from_uri(ref).sha256 != hash_value
        or repo.value(ref) != value
    ):
        raise ValueError("forecast original integrity failed")
    record = ForecastRecord.model_validate(value)
    if report_ref:
        state = repo.get(run_id)
        if (
            state["result_ref"] != report_ref
            or state["data"].get("forecast") != value
            or record.evidence_hash != digest(state["data"]["bundle"])
        ):
            raise ValueError("forecast report binding failed")
    return value


def forecast_chain(repo, run_id):
    current, chain, seen = forecast(repo, run_id), [], set()
    while current:
        if current["forecast_id"] in seen:
            raise ValueError("forecast version cycle")
        seen.add(current["forecast_id"])
        chain.append(current)
        if not current["supersedes_id"]:
            break
        with repo.base.sessions() as session:
            parent = session.get(ForecastRow, current["supersedes_id"])
            if not parent:
                raise ValueError("forecast parent missing")
            parent_run = parent.run_id
        current = forecast(repo, parent_run)
    if not chain:
        return dict(current=None, versions=[])
    versions = list(reversed(chain))
    # A user opening the original can also find later updates; no record is rewritten.
    known = {r["forecast_id"] for r in versions}
    with repo.base.sessions() as session:
        candidates = [
            (r.run_id, r.payload["supersedes_id"], r.forecast_id)
            for r in session.scalars(select(ForecastRow))
        ]
    while True:
        children = [
            (rid, fid)
            for rid, parent_id, fid in candidates
            if parent_id in known and fid not in known
        ]
        if not children:
            break
        for rid, fid in children:
            versions.append(forecast(repo, rid))
            known.add(fid)
    versions.sort(key=lambda r: (r["issued_at"], r["forecast_id"]))
    return dict(current=chain[0], versions=versions)


def calendar_sessions(repo, data):
    descriptor = data.get("observation_calendar")
    if not descriptor:
        return ()
    calendar = descriptor["calendar"]
    sessions = parse_annual(repo.raw_bytes(calendar["source_refs"][0]).decode("utf8"), 2026)
    if [str(d) for d in sessions] != calendar["sessions"]:
        raise ValueError("forecast calendar disagrees with original")
    repo.raw_bytes(calendar["source_refs"][1])
    return sessions


def schedule(repo, data):
    issued = datetime.fromisoformat(data["forecast"]["issued_at"])
    from .service import SHANGHAI

    anchor_date = issued.astimezone(SHANGHAI).date()
    future = [d for d in calendar_sessions(repo, data) if d > anchor_date]
    return [
        dict(
            window=w,
            due_session=str(future[w - 1]) if len(future) >= w else None,
            calendar_note=None if len(future) >= w else "尚缺下一年度核定交易日历，不猜到期日期。",
        )
        for w in (20, 40, 60)
    ]


def observe_forecast(record, prices):
    """Pure calculation on aligned, explicitly supplied PRICE data; never upgrades quality."""
    window = prices["window"]
    dates = tuple(date.fromisoformat(d) for d in prices.get("sessions", []))
    fields = dict(
        forecast_id=record.forecast_id,
        observed_at=prices["observed_at"],
        window=window,
        actual_dates=dates,
        return_basis="PRICE",
        quality_status="PARTIAL",
    )
    if not prices.get("due_session") or prices["observed_at"][:10] < prices["due_session"]:
        return OutcomeRecord(**fields, maturity="PENDING")
    security, market = prices.get("security_prices", []), prices.get("market_prices", [])
    partial_benchmarks = prices.get("protocol") == "forecast-observe-2026-10-14-v2"
    if (
        len(dates) != window + 1
        or len(security) != len(dates)
        or (not partial_benchmarks and len(market) != len(dates))
    ):
        return OutcomeRecord(**fields, maturity="UNAVAILABLE")
    if (
        len(set(dates)) != len(dates)
        or tuple(sorted(dates)) != dates
        or str(dates[-1]) != prices["due_session"]
    ):
        raise ValueError("observation dates do not align")
    security, market = (
        [number(x, positive=True) for x in security],
        [number(x, positive=True) for x in market],
    )
    r = security[-1] / security[0] - 1
    m = market[-1] / market[0] - 1 if len(market) == len(dates) else None
    return OutcomeRecord(
        **fields,
        maturity="MATURE",
        security_return=r,
        market_return=m,
        relative_returns=(("CSI300_PRICE", r - m),) if m is not None else (),
        max_adverse_excursion=min(Decimal(0), *(p / security[0] - 1 for p in security)),
    )


def observations(repo, run_id):
    initialize(repo)
    record = forecast(repo, run_id)
    if not record:
        raise ValueError("no published forecast")
    with repo.base.sessions() as session:
        rows = [
            copy.deepcopy(r.payload)
            for r in session.scalars(
                select(ObservationRow).where(ObservationRow.forecast_id == record["forecast_id"])
            )
        ]
        refs = [
            r.record_ref
            for r in session.scalars(
                select(ObservationRow).where(ObservationRow.forecast_id == record["forecast_id"])
            )
        ]
    for row, ref in zip(rows, refs, strict=True):
        if repo.value(ref) != row:
            raise ValueError("observation integrity failed")
        for source in row["sources"]:
            if BlobRef.from_uri(source["blob_ref"]).sha256 != source["sha256"]:
                raise ValueError("observation source hash mismatch")
            repo.raw_bytes(source["blob_ref"])
        for inputs in row["inputs"]:
            if inputs.get("sessions"):
                state = repo.get(run_id)
                for name, symbol in (
                    ("security_prices", state["subject"]["symbol"]),
                    ("market_prices", "sh000300"),
                ):
                    if (
                        inputs.get("protocol") == "forecast-observe-2026-10-14-v2"
                        and not inputs[name]
                    ):
                        continue
                    matching = [s for s in row["sources"] if s["key"] == "observe_" + symbol]
                    valid = False
                    for source in matching:
                        packet = json.loads(repo.raw_bytes(source["blob_ref"]))
                        values = {
                            r[0]: str(number(r[2], positive=True))
                            for r in packet["data"][symbol]["day"]
                        }
                        if all(
                            values.get(day) == price
                            for day, price in zip(inputs["sessions"], inputs[name], strict=True)
                        ):
                            valid = True
                    if not valid:
                        raise ValueError("observation prices differ from archived source")
        expected = [
            observe_forecast(ForecastRecord.model_validate(record), p).model_dump(mode="json")
            for p in row["inputs"]
        ]
        if expected != row["outcomes"]:
            raise ValueError("observation calculation disagrees")
    return sorted(rows, key=lambda r: r["observed_at"])


def observe(repo, state, now=None):
    now = now or datetime.now(UTC)
    data, record = state["data"], ForecastRecord.model_validate(forecast(repo, state["run_id"]))
    from .service import SHANGHAI

    local = now.astimezone(SHANGHAI)
    plans, calls, inputs = schedule(repo, data), [], []
    # Observe is a separate read-only acquisition, with no model calls or mutations to the run.
    ephemeral = dict(request=state["request"], calls=calls, elapsed_seconds=0)
    import threading

    client = SourceClient(repo, ephemeral, lambda: None, threading.Event(), max_requests=12)
    sessions = calendar_sessions(repo, data)
    issued_date = record.issued_at.astimezone(SHANGHAI).date()
    prior = [d for d in sessions if d <= issued_date]
    for plan in plans:
        p = dict(
            **plan,
            protocol="forecast-observe-2026-10-14-v2",
            observed_at=now.isoformat(),
            sessions=[],
            security_prices=[],
            market_prices=[],
            errors=[],
        )
        due = plan["due_session"]
        if due and (
            local.date().isoformat() > due or (str(local.date()) == due and local.hour >= 16)
        ):
            try:
                if not prior:
                    raise ValueError("no anchor session")
                base = prior[-1]
                needed = [d for d in sessions if base <= d <= date.fromisoformat(due)]
                if len(needed) != plan["window"] + 1:
                    raise ValueError("calendar window incomplete")
                for key, symbol in (
                    ("security_prices", state["subject"]["symbol"]),
                    ("market_prices", "sh000300"),
                ):
                    try:
                        _, packet = client.fetch(
                            "observe_" + symbol,
                            KLINE,
                            params={"param": f"{symbol},day,{base},{due},640,"},
                            family="TENCENT",
                        )
                        rows = packet["data"][symbol]["day"]
                        mapping = {r[0]: str(number(r[2], positive=True)) for r in rows}
                        if len(mapping) != len(rows):
                            raise ValueError("duplicate observation dates")
                        p[key] = [mapping[str(d)] for d in needed]
                    except (ValueError, KeyError, IndexError) as error:
                        p["errors"].append(dict(channel=key, reason=str(error)[:200]))
                if len(p["security_prices"]) == len(needed):
                    p["sessions"] = [str(d) for d in needed]
            except (ValueError, KeyError, IndexError) as error:
                p.update(sessions=[], security_prices=[], market_prices=[])
                p["errors"].append(dict(channel="calendar", reason=str(error)[:200]))
        # On due day before close, comparison stays PENDING even if a quote exists.
        if due == str(local.date()) and local.hour < 16:
            p["observed_at"] = local.replace(hour=0).isoformat()
            p["due_session"] = None
        inputs.append(p)
    sources = [c["source"] for c in calls if c["status"] == "COMPLETED"]
    # Pending observations are deduplicated per local date; changed raw data creates a new version.
    input_hash = digest(
        dict(
            date=str(local.date()),
            schedule=plans,
            sources=[s["sha256"] for s in sources],
            values=[{k: v for k, v in p.items() if k != "observed_at"} for p in inputs],
        )
    )
    value = dict(
        observation_id="OBS-" + record.forecast_id + "-" + input_hash[:16],
        forecast_id=record.forecast_id,
        input_hash=input_hash,
        observed_at=now.isoformat(),
        inputs=inputs,
        sources=sources,
        source_attempts=calls,
        quality=dict(
            security_basis="RAW_PRICE",
            market_benchmark="CSI300_RAW_PRICE",
            industry_benchmark="UNAVAILABLE",
            corporate_actions="NOT_INDEPENDENTLY_VERIFIED",
            catalyst_realization="UNVERIFIED_REQUIRES_ORIGINAL_DISCLOSURE",
        ),
        outcomes=[observe_forecast(record, p).model_dump(mode="json") for p in inputs],
        limitations=[
            "仅未复权价格观察；行业基准未接入，不是已核验全收益或正式策略绩效。",
            "未用股价涨跌判断催化兑现；须另查实际披露。",
        ],
    )
    initialize(repo)
    try:
        with repo.base.sessions.begin() as session:
            old = session.scalar(
                select(ObservationRow).where(
                    ObservationRow.forecast_id == record.forecast_id,
                    ObservationRow.input_hash == input_hash,
                )
            )
            if old:
                return copy.deepcopy(old.payload)
            session.add(
                ObservationRow(
                    observation_id=value["observation_id"],
                    forecast_id=record.forecast_id,
                    input_hash=input_hash,
                    record_ref=repo.blob(value),
                    payload=value,
                )
            )
    except IntegrityError:
        with repo.base.sessions() as session:
            old = session.scalar(
                select(ObservationRow).where(
                    ObservationRow.forecast_id == record.forecast_id,
                    ObservationRow.input_hash == input_hash,
                )
            )
            if old is None:
                raise
            return copy.deepcopy(old.payload)
    return value


def withdraw(repo, run_id, reason):
    """One user-marked withdrawal at actual receipt time; original judgement stays immutable."""
    record = forecast(repo, run_id)
    if not record:
        raise ValueError("no forecast to withdraw")
    input_hash = digest(dict(forecast_id=record["forecast_id"], kind="USER_WITHDRAWAL"))
    now = datetime.now(UTC).isoformat()
    value = dict(
        observation_id="WITHDRAW-" + record["forecast_id"],
        forecast_id=record["forecast_id"],
        input_hash=input_hash,
        observed_at=now,
        kind="USER_WITHDRAWAL",
        reason=reason,
        identity="USER_MARKED_NOT_FACT_VERIFIED",
        inputs=[],
        sources=[],
        outcomes=[],
        limitations=[
            "用户手动撤回标记；触发时点为本次提交时间，未回填更早日期；原预测仍保留并继续观察。"
        ],
    )
    try:
        with repo.base.sessions.begin() as session:
            old = session.scalar(
                select(ObservationRow).where(
                    ObservationRow.forecast_id == record["forecast_id"],
                    ObservationRow.input_hash == input_hash,
                )
            )
            if old:
                return copy.deepcopy(old.payload)
            session.add(
                ObservationRow(
                    observation_id=value["observation_id"],
                    forecast_id=record["forecast_id"],
                    input_hash=input_hash,
                    record_ref=repo.blob(value),
                    payload=value,
                )
            )
    except IntegrityError:
        return next(r for r in observations(repo, run_id) if r.get("kind") == "USER_WITHDRAWAL")
    return value
