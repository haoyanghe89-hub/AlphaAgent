"""A finite, preregistered next-session experiment driven by the local daily runtime."""

import copy
import threading
from datetime import UTC, datetime, time
from uuid import NAMESPACE_URL, uuid5

from ..contracts import digest
from ..decision.next_session import service
from ..decision.next_session.contracts import NextSessionRunRequest, VerifiedSessionCalendar
from ..decision.next_session.labels import SHANGHAI
from ..decision.next_session.store import ExperimentStore
from ..decision.store import fingerprint

VERSION = "paired-next-session-forward-v1"
SECURITIES = ("000333.SZ", "600036.SH", "601088.SH", "600009.SH", "300750.SZ", "600519.SH")


def schedule(base, parent, now):
    repo = ExperimentStore(base)
    # Another component trial must not multiply the already registered daily calls.
    for item in base.list(prefix="RUN-EV-"):
        prior = base.get(item["run_id"], with_data=False)
        if prior.get("version") == "research-effectiveness-v1":
            plan = repo.value(prior["manifest_ref"])["forward_schedule"]
            if plan["slots"][-1]["base_session"] >= now.astimezone(SHANGHAI).date().isoformat():
                return plan
    activation = repo.activation(service.template().experiment_id)
    if not activation:
        raise ValueError("明日前瞻需要已核实的实验交易日历，请先开启明日预测")
    calendars = {
        k: VerifiedSessionCalendar.model_validate(v) for k, v in activation["calendars"].items()
    }
    for calendar in calendars.values():
        for ref in calendar.source_refs:
            repo.raw_bytes(ref)
    days = calendars["SSE"].sessions
    local = now.astimezone(SHANGHAI)
    future = [
        d for d in days if d > local.date() or d == local.date() and local.time() < time(19, 45)
    ]
    if len(future) < 11:
        raise ValueError("已核实交易日历不足十个实验日及其下一交易日")
    slots = []
    for i, day in enumerate(future[:10]):
        target = days[days.index(day) + 1]
        for sid in SECURITIES[(i % 3) * 2 : (i % 3) * 2 + 2]:
            if day not in calendars["SZSE"].sessions or target not in calendars["SZSE"].sessions:
                raise ValueError("沪深交易日历不一致，不能登记配对")
            slots.append(
                dict(
                    base_session=str(day),
                    target_session=str(target),
                    security_id=sid,
                    order=["ORIGINAL", "ENHANCED"] if i % 2 == 0 else ["ENHANCED", "ORIGINAL"],
                )
            )
    return dict(
        version=VERSION,
        parent_run_id=parent,
        registered_at=now.isoformat(),
        model=activation["config"]["model_version"],
        experiment_id=service.template().experiment_id,
        activation_ref=repo.blob(activation),
        slots=slots,
        window_start="19:45",
        window_end="20:15",
        budget=activation["config"]["budget"],
        note="独立探索配对协议，不混入旧正式20:00实验；过期不补签、失败不替换，十日期间本机须运行。",
    )


def key(plan, slot):
    return "DEC-EFFECT-PAIR-" + digest(dict(parent=plan["parent_run_id"], slot=slot))


def due(plan, now):
    local = now.astimezone(SHANGHAI)
    day = local.date().isoformat()
    return [
        s
        for s in plan["slots"]
        if s["base_session"] < day
        or s["base_session"] == day
        and local.strftime("%H:%M") >= plan["window_start"]
    ]


async def run_slot(base, plan, slot, settings):
    repo = ExperimentStore(base)
    identity = key(plan, slot)
    if repo.exists(identity):
        return repo.record(identity)
    now = datetime.now(UTC)
    local = now.astimezone(SHANGHAI)
    deadline = datetime.combine(
        datetime.fromisoformat(slot["base_session"]).date(), time(20, 15), SHANGHAI
    )
    if local.date().isoformat() != slot["base_session"] or now >= deadline:
        return repo.immutable(
            identity,
            dict(
                slot=slot,
                status="MISSED_WINDOW",
                runs={},
                recorded_at=now.isoformat(),
                reason="错过固定窗口，未补签或调用模型",
            ),
        )
    if local.strftime("%H:%M") < plan["window_start"]:
        raise ValueError("尚未到登记窗口")
    activation = repo.value(plan["activation_ref"])
    if settings.model != plan["model"]:
        return repo.immutable(
            identity, dict(slot=slot, status="MODEL_CHANGED", runs={}, recorded_at=now.isoformat())
        )
    runs = {}
    for arm in slot["order"]:
        request = NextSessionRunRequest(
            request_key=uuid5(NAMESPACE_URL, identity + arm),
            experiment_id=plan["experiment_id"],
            entity=slot["security_id"],
            exploratory=True,
            enhanced_research=arm == "ENHANCED",
        ).model_dump(mode="json", exclude_none=True)
        run, _ = repo.create_run(request, copy.deepcopy(activation["config"]), fingerprint())
        runs[arm] = run["run_id"]
    cache_key = identity.replace("DEC-EFFECT-PAIR-", "DEC-EFFECT-INPUT-")

    def shared_snapshot(storage, state, stop, security, calendar, save):
        if repo.exists(cache_key):
            frozen = repo.record(cache_key)
            snap = storage.snapshot(frozen["snapshot_ref"])
            return frozen["snapshot_ref"], snap, frozen["subject"]
        ref, snap, subject = service.build_snapshot(storage, state, stop, security, calendar, save)
        if (
            str(snap.base_session) != slot["base_session"]
            or str(snap.target_session) != slot["target_session"]
        ):
            raise ValueError("实际封存交易日与登记配对不一致")
        repo.immutable(cache_key, dict(snapshot_ref=ref, subject=subject))
        return ref, snap, subject

    for arm in slot["order"]:
        state = repo.get_run(runs[arm])
        # Any earlier start is preserved after a restart; never rerun it on a fresh budget.
        if state["status"] == "RUNNING" and not state["calls"]:
            await service.run(
                repo,
                state["run_id"],
                settings,
                threading.Event(),
                snapshot_builder=shared_snapshot,
                issue_deadline=deadline,
                allow_backup=False,
            )
    states = {arm: repo.get_run(rid) for arm, rid in runs.items()}
    valid = all(
        s["status"] == "COMPLETED" and len(s["prediction_ids"]) == 1 for s in states.values()
    )
    return repo.immutable(
        identity,
        dict(
            slot=slot,
            runs=runs,
            status="ISSUED" if valid else "FAILED",
            shared_snapshot_ref=repo.record(cache_key)["snapshot_ref"]
            if repo.exists(cache_key)
            else None,
            recorded_at=datetime.now(UTC).isoformat(),
        ),
    )


def view(base, plan, *, now=None):
    from .paired_evaluation import usage

    repo = ExperimentStore(base, request_cache=True)
    rows, seen = [], set()
    correct = dict(ORIGINAL=0, ENHANCED=0)
    baselines = dict(ALWAYS_UP=0, ALWAYS_DOWN=0, PERSISTENCE=0)
    matured = 0
    now = now or datetime.now(UTC)
    for slot in plan["slots"]:
        identity = key(plan, slot)
        row = dict(slot, status="SCHEDULED", runs={}, actual=None)
        if repo.exists(identity):
            saved = repo.record(identity)
            row.update(status=saved["status"], runs=saved["runs"])
            row["usage"] = {arm: usage(repo.get_run(rid)) for arm, rid in saved["runs"].items()}
            if saved["status"] == "ISSUED":
                states = {arm: repo.get_run(rid) for arm, rid in saved["runs"].items()}
                predictions = {
                    arm: repo.prediction(s["prediction_ids"][0]) for arm, s in states.items()
                }
                frozen = saved["shared_snapshot_ref"]
                for arm, s in states.items():
                    snapshot_ref = s["snapshots"][slot["security_id"]]
                    snapshot = repo.snapshot(snapshot_ref)
                    original_ref = getattr(snapshot, "base_snapshot_ref", snapshot_ref)
                    p = predictions[arm]
                    if (
                        original_ref != frozen
                        or str(p.base_session) != slot["base_session"]
                        or str(p.target_session) != slot["target_session"]
                        or p.model_version != plan["model"]
                        or not plan["window_start"]
                        <= p.issued_at.astimezone(SHANGHAI).strftime("%H:%M")
                        < plan["window_end"]
                    ):
                        raise ValueError("明日配对输入、签发时间或模型身份不一致")
                row["predictions"] = {a: p.prediction for a, p in predictions.items()}
                outcomes = {
                    a: repo.outcome_versions(p.prediction_id) for a, p in predictions.items()
                }
                latest = {a: o[-1] if o else {} for a, o in outcomes.items()}
                row["usage"] = {a: usage(s) for a, s in states.items()}
                closed = now >= datetime.combine(
                    datetime.fromisoformat(slot["target_session"]).date(), time(15), SHANGHAI
                )
                if closed and all(o.get("status") == "SCORABLE" for o in latest.values()):
                    actuals = {o["return_direction"] for o in latest.values()}
                    prices = {(o.get("close"), o.get("reference_price")) for o in latest.values()}
                    if len(actuals) != 1 or len(prices) != 1:
                        row["status"] = "OUTCOME_CONFLICT"
                    else:
                        actual = actuals.pop()
                        row["actual"] = actual
                        slot_identity = (slot["security_id"], slot["target_session"])
                        if actual == "FLAT":
                            row["status"] = "FLAT"
                        elif slot_identity in seen:
                            row["status"] = "DUPLICATE"
                        else:
                            row["status"] = "PAIRED_MATURED"
                            seen.add(slot_identity)
                            matured += 1
                            for arm, p in predictions.items():
                                correct[arm] += int(p.prediction == actual)
                            for name, direction in predictions["ORIGINAL"].baselines:
                                baselines[name] += int(direction == actual)
                elif any(o.get("status") == "UNSCORABLE" for o in latest.values()):
                    row["status"] = "UNSCORABLE"
        rows.append(row)
    return dict(
        version=VERSION,
        rows=rows,
        registered_pairs=len(rows),
        paired_matured=matured,
        correct=correct,
        baseline_correct=baselines,
        accuracy_delta=(correct["ENHANCED"] - correct["ORIGINAL"]) / matured if matured else None,
        independent_dates=len({r["base_session"] for r in rows if r["status"] == "PAIRED_MATURED"}),
        note=plan["note"],
    )


async def tick(runtime, now):
    from .effectiveness import VERSION as EFFECT_VERSION
    from .paired_evaluation import EvaluationStore

    repo = EvaluationStore(runtime.base)
    for item in repo.list():
        state = repo.get(item["run_id"], with_data=False)
        if state.get("version") != EFFECT_VERSION:
            continue
        manifest = repo.value(state["manifest_ref"])
        plan = manifest["forward_schedule"]
        for slot in due(plan, now):
            if runtime.events.exists(key(plan, slot)):
                continue

            async def action(plan=plan, slot=slot):
                result = await run_slot(runtime.base, plan, slot, runtime.app.state.settings)
                runtime.state["forward_evaluation"] = dict(
                    parent_run_id=plan["parent_run_id"],
                    slot=slot,
                    status=result["status"],
                    checked_at=datetime.now(UTC).isoformat(),
                )

            await runtime.exclusive("daily:forward-evaluation", action)
            return
