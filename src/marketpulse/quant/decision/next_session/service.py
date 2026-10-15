"""Manual prediction batches: collect, seal, analyze, verify, publish, observe."""

import asyncio
import threading
import time
from datetime import UTC, date, datetime
from uuid import uuid4

from ...contracts import digest
from ..backup_model import open_backup
from ..methods import load_config
from ..tools import SourceClient, Stopped, resolve
from .agents import PROMPTS, DailyRoles, Proposal, Review, Writing, guard_proposal
from .calendar import load_calendar
from .contracts import ExperimentConfig, ExperimentRunSlot, NextSessionPrediction
from .labels import SHANGHAI, close_at, cohort_for, next_session
from .metrics import ratio, statistics
from .outcomes import append_result, observe_one
from .snapshot import build_snapshot


def template():
    return ExperimentConfig.model_validate(load_config("next_session/experiment_config.json"))


def policy_hash():
    return digest(
        dict(
            prompts=PROMPTS,
            schemas=[c.model_json_schema() for c in (Proposal, Review, Writing)],
            config=template().model_dump(mode="json"),
        )
    )


def select_security(client, state):
    """Keep requested stocks outside the preregistered fixed-universe statistics."""
    entity = state["request"].get("entity")
    if not entity:
        return
    subject = resolve(client, entity)
    config = dict(state["config"])
    config.update(
        securities=[subject["security_id"]],
        universe_version="user-selected-hs-a-v1:" + subject["security_id"],
    )
    ExperimentConfig.model_validate(config)
    state.update(config=config, query_subject=subject, title=subject["name"] + " · 明日预测")


def preflight(activation, now=None):
    from .contracts import VerifiedSessionCalendar

    now = now or datetime.now(UTC)
    days = VerifiedSessionCalendar.model_validate(activation["calendars"]["SSE"]).sessions
    base = max((d for d in days if close_at(d) <= now), default=None)
    if base is None:
        raise ValueError("尚无覆盖内的已收盘交易日，请先更新交易日历。")
    target = next_session(days, base)
    if now.astimezone(SHANGHAI).date() >= target:
        raise ValueError(
            "请在本交易日收盘后生成下一交易日预测；盘中不能用昨日资料补预测已开始的交易日。"
        )
    return base, target


def recover(repo):
    from .contracts import VerifiedSessionCalendar

    now = datetime.now(UTC)
    for state in repo.records("RUN-NS-"):
        if state["status"] != "RUNNING":
            continue
        activation = repo.activation(state["config"]["experiment_id"])
        if activation:
            days = VerifiedSessionCalendar.model_validate(activation["calendars"]["SSE"]).sessions
            base = (
                date.fromisoformat(state["base_session"])
                if state.get("base_session")
                else max(d for d in days if close_at(d) <= now)
            )
            target = next_session(days, base)
            attempted = {repo.record(a)["slot_key"][1] for a in state["attempt_ids"]}
            for security in (
                []
                if state["request"].get("entity") and not state.get("query_subject")
                else state["config"]["securities"]
            ):
                if security in attempted:
                    continue
                aid = "DEC-ATTEMPT-" + uuid4().hex
                attempt = dict(
                    attempt_id=aid,
                    slot_key=[state["config"]["experiment_id"], security, str(target)],
                    cohort=state.get("cohort", "EXPLORATORY"),
                    run_id=state["run_id"],
                    started_at=state["created_at"],
                    finished_at=now.isoformat(),
                    state="TIMEOUT",
                    reason="服务重启，未签发的标的保留中断记录。",
                    model_calls=0,
                    tokens=0,
                )
                repo.immutable(aid, attempt)
                state["attempt_ids"].append(aid)
        for call in state["calls"]:
            if call["status"] == "RUNNING":
                call["status"] = "INTERRUPTED"
        state.update(
            status="INTERRUPTED", error="服务重启，本次预测停止；已签发结果与失败尝试保留。"
        )
        repo.save(state)


def enable(repo, settings, now=None):
    now = now or datetime.now(UTC)
    config = template()
    prior = repo.activation(config.experiment_id)
    if prior:
        return prior
    state = dict(request=dict(budget_profile=config.budget.model_dump()), calls=[])
    client = SourceClient(repo, state, lambda: None, threading.Event(), max_requests=8)
    calendars = {
        exchange: load_calendar(client, exchange, now.astimezone(SHANGHAI).year)
        for exchange in ("SSE", "SZSE")
    }
    local = now.astimezone(SHANGHAI)
    days = calendars["SSE"].sessions
    first = (
        local.date()
        if local.date() in days and local.time() < config.window_end
        else next((d for d in days if d > local.date()), None)
    )
    if first is None:
        raise ValueError("日历没有可预先登记的首个交易日")
    enabled = ExperimentConfig.model_validate(
        dict(
            config.model_dump(mode="json"),
            enabled=True,
            enabled_at=now,
            first_base_session=first,
            model_version=settings.model,
        )
    )
    payload = dict(
        config=enabled.model_dump(mode="json"),
        calendars={k: v.model_dump(mode="json") for k, v in calendars.items()},
        calls=state["calls"],
        policy_hash=policy_hash(),
    )
    repo.immutable("DEC-CONFIG-" + enabled.experiment_id, payload)
    plan(repo, payload, now)
    return payload


def plan(repo, activation, now):
    from .contracts import VerifiedSessionCalendar

    config = ExperimentConfig.model_validate(activation["config"])
    local = now.astimezone(SHANGHAI)
    for security in config.securities:
        exchange = "SSE" if security.endswith(".SH") else "SZSE"
        calendar = VerifiedSessionCalendar.model_validate(activation["calendars"][exchange])
        for day in calendar.sessions:
            if not config.first_base_session <= day <= local.date():
                continue
            target = next_session(calendar.sessions, day)
            slot = ExperimentRunSlot(
                experiment_id=config.experiment_id,
                security_id=security,
                base_session=day,
                target_session=target,
                window_start=datetime.combine(day, config.window_start, SHANGHAI),
                window_end=datetime.combine(day, config.window_end, SHANGHAI),
                state="PLANNED",
                reason="从实际开启首日预先登记，手动触发；未触发同样保留。",
            )
            repo.immutable(
                "DEC-SLOT-" + digest([config.experiment_id, security, str(target)]),
                slot.model_dump(mode="json"),
            )


def status(repo, now=None):
    now = now or datetime.now(UTC)
    config = template()
    activation = repo.activation(config.experiment_id)
    runs = repo.records("RUN-NS-")
    result = dict(
        enabled=bool(activation),
        experiment_id=config.experiment_id,
        securities=list(config.securities),
        window="20:00–20:15",
        timezone="Asia/Shanghai",
        now=now.isoformat(),
        latest_run_id=runs[-1]["run_id"] if runs else None,
        software_available=True,
    )
    if activation:
        result["activation"] = activation["config"]
        plan(repo, activation, now)
    return result


async def run(repo, rid, settings, stop, port=None):
    state = repo.get_run(rid)
    started = time.monotonic()
    budget = state["config"]["budget"]
    deadline = started + budget["max_seconds"]
    current_cohort = None

    def check():
        if stop.is_set():
            raise Stopped("用户停止了本次预测")
        if time.monotonic() >= deadline:
            raise TimeoutError("批次达到时间上限")

    def save():
        check()
        state["elapsed_seconds"] = time.monotonic() - started
        repo.save(state)

    def stage(role, text, security=None):
        state["process"].append(
            dict(role=role, detail=text, security_id=security, at=datetime.now(UTC).isoformat())
        )
        save()

    def record_unfinished(reason, kind):
        from .contracts import VerifiedSessionCalendar

        if state["request"].get("entity") and not state.get("query_subject"):
            return
        activation = repo.activation(state["config"]["experiment_id"])
        if not activation:
            return
        now = datetime.now(UTC)
        days = VerifiedSessionCalendar.model_validate(activation["calendars"]["SSE"]).sessions
        base = max(d for d in days if close_at(d) <= now)
        target = next_session(days, base)
        attempted = {repo.record(a)["slot_key"][1] for a in state["attempt_ids"]}
        try:
            attempt_cohort = current_cohort or cohort_for(now, base, target)
        except ValueError:
            attempt_cohort = "EXPLORATORY"
        if state["request"].get("entity"):
            attempt_cohort = "EXPLORATORY"
        for security in state["config"]["securities"]:
            if security in attempted:
                continue
            aid = "DEC-ATTEMPT-" + uuid4().hex
            attempt = dict(
                attempt_id=aid,
                slot_key=[state["config"]["experiment_id"], security, str(target)],
                cohort=attempt_cohort,
                run_id=rid,
                started_at=state["created_at"],
                finished_at=now.isoformat(),
                state=kind,
                reason=reason,
                model_calls=0,
                tokens=0,
            )
            repo.immutable(aid, attempt)
            state["attempt_ids"].append(aid)

    try:
        stage("Planner", "核对固定样本、交易日与签发窗口")
        client = SourceClient(repo, state, save, stop, max_requests=60, budget_profile=budget)
        if state["request"].get("entity"):
            stage("Planner", "核对所选股票身份与行业")
            await asyncio.to_thread(select_security, client, state)
            save()
        calendars = {}
        for exchange in ("SSE", "SZSE"):
            calendars[exchange] = await asyncio.to_thread(
                load_calendar, client, exchange, datetime.now(UTC).astimezone(SHANGHAI).year
            )
        now = datetime.now(UTC)
        sessions = calendars["SSE"].sessions
        base = max(d for d in sessions if close_at(d) <= now)
        target = next_session(sessions, base)
        current_cohort = cohort_for(now, base, target)
        if cohort_for(datetime.fromisoformat(state["created_at"]), base, target) != "PRIMARY":
            current_cohort = "EXPLORATORY"
        if state["request"]["exploratory"] or state["request"].get("entity"):
            current_cohort = "EXPLORATORY"
        state.update(base_session=str(base), target_session=str(target), cohort=current_cohort)
        if current_cohort == "PRIMARY":
            deadline = min(
                deadline,
                started
                + max(
                    0,
                    (
                        datetime.combine(
                            base, datetime.min.replace(hour=20, minute=15).time(), SHANGHAI
                        )
                        - now
                    ).total_seconds(),
                ),
            )
        save()
        async with asyncio.timeout(max(0, deadline - time.monotonic())):
            async with (
                open_backup(
                    settings,
                    enabled=bool(state["request"].get("entity")) and port is None,
                    repo=repo,
                    state=state,
                ) as backup,
                DailyRoles(
                    repo,
                    state,
                    settings,
                    save,
                    check,
                    port,
                    fallback_model=backup.model if backup else None,
                    fallback_port=backup.port if backup else None,
                ) as roles,
            ):
                for security in state["config"]["securities"]:
                    check()
                    attempt_started = datetime.now(UTC)
                    calls_before = state["model_calls"]
                    tokens_before = state["tokens"]
                    attempt = dict(
                        attempt_id="DEC-ATTEMPT-" + uuid4().hex,
                        slot_key=[state["config"]["experiment_id"], security, str(target)],
                        cohort=current_cohort,
                        run_id=rid,
                        started_at=attempt_started.isoformat(),
                        state="FAILED_INPUT",
                        reason="取证未完成",
                    )
                    try:
                        stage("Data", "取得行情、披露与融资，并封存输入", security)
                        calendar = calendars["SSE" if security.endswith(".SH") else "SZSE"]
                        ref, snapshot, subject = await asyncio.to_thread(
                            build_snapshot, repo, state, stop, security, calendar, save
                        )
                        state["snapshots"][security] = ref
                        state.setdefault("subjects", {})[security] = subject
                        save()
                        attempt["state"] = "FAILED_REVIEW"
                        attempt["reason"] = "模型或审核未完成"
                        stage("Analyst", "基于封存资料预测下一交易日涨跌", security)
                        proposal, analysis_call = await roles.call(
                            "Analyst",
                            Proposal,
                            snapshot.model_context,
                            lambda v, context=snapshot.model_context: guard_proposal(v, context),
                        )
                        stage("Verifier", "复核理由、反对依据与下一交易日期限", security)
                        review, review_call = await roles.call(
                            "Verifier",
                            Review,
                            dict(
                                snapshot=snapshot.model_context,
                                proposal=proposal.model_dump(mode="json"),
                            ),
                        )
                        if review.verdict != "APPROVE" or not all(
                            (
                                review.citations_supported,
                                review.next_session_relevant,
                                review.no_invented_facts,
                            )
                        ):
                            raise ValueError("语义审核未通过：" + review.reason)
                        approved = dict(
                            direction=proposal.direction,
                            summary=proposal.summary,
                            approved_hash=digest(proposal.model_dump(mode="json")),
                        )
                        stage("Writer", "保存审核后的方向和理由", security)
                        writing, write_call = await roles.call(
                            "Writer",
                            Writing,
                            approved,
                            lambda v, approved=approved: (
                                None
                                if v.model_dump() == approved
                                else (_ for _ in ()).throw(ValueError("发布角色改变了审核内容"))
                            ),
                        )
                        check()
                        issued = datetime.now(UTC)
                        # Missing the primary deadline fails instead of reclassifying output.
                        if (
                            current_cohort == "PRIMARY"
                            and cohort_for(issued, base, target) != "PRIMARY"
                        ):
                            raise TimeoutError("错过正式签发截止时间")
                        baseline = (
                            "UP"
                            if snapshot.daily_prices[-1].close
                            >= snapshot.daily_prices[-1].reference_price
                            else "DOWN"
                        )
                        prediction = NextSessionPrediction(
                            prediction_id="DEC-PRED-" + uuid4().hex,
                            experiment_id=state["config"]["experiment_id"],
                            protocol_id=state["config"]["protocol_id"],
                            universe_version=state["config"]["universe_version"],
                            security_id=security,
                            base_session=base,
                            target_session=target,
                            requested_at=state["created_at"],
                            evidence_sealed_at=snapshot.evidence_sealed_at,
                            issued_at=issued,
                            input_hash=snapshot.input_hash,
                            method_versions=(
                                state["config"]["method_version"],
                                "next-session-observations-v1",
                            ),
                            model_version=analysis_call["model"],
                            prompt_version=state["config"]["prompt_version"],
                            prediction=proposal.direction,
                            summary=writing.summary,
                            timing_group="20:00–20:15"
                            if current_cohort == "PRIMARY"
                            else issued.astimezone(SHANGHAI).strftime("%H:00–%H:59"),
                            support_ids=tuple(
                                dict.fromkeys(e for r in proposal.reasons for e in r.evidence_ids)
                            ),
                            counter_ids=tuple(
                                dict.fromkeys(
                                    e for r in proposal.opposing_reasons for e in r.evidence_ids
                                )
                            ),
                            reasons=proposal.reasons,
                            opposing_reasons=proposal.opposing_reasons,
                            limitations=proposal.limitations,
                            support_level=proposal.support_level,
                            review_status="APPROVE",
                            review_refs=(review_call["response_ref"], write_call["response_ref"]),
                            cohort=current_cohort,
                            baselines=(
                                ("ALWAYS_UP", "UP"),
                                ("ALWAYS_DOWN", "DOWN"),
                                ("PERSISTENCE", baseline),
                            ),
                        )
                        prediction = repo.publish(prediction)
                        state["prediction_ids"].append(prediction.prediction_id)
                        save()
                        append_result(
                            repo,
                            prediction,
                            dict(status="PENDING", limitations=["目标交易日尚未收盘。"]),
                            issued,
                        )
                        attempt.update(
                            state="ISSUED" if prediction.cohort == "PRIMARY" else "AUXILIARY",
                            reason="真实模型方向与理由已通过程序及模型职责复核；预测价值待观察。",
                            prediction_id=prediction.prediction_id,
                        )
                    except asyncio.CancelledError:
                        attempt.update(state="TIMEOUT", reason="批次已停止，本标的未发布方向。")
                        raise
                    except TimeoutError:
                        attempt.update(
                            state="TIMEOUT", reason="达到签发或批次时间上限，未发布方向。"
                        )
                        raise
                    except Exception as error:
                        attempt["reason"] = str(error)[:400]
                    finally:
                        attempt.update(
                            finished_at=datetime.now(UTC).isoformat(),
                            model_calls=state["model_calls"] - calls_before,
                            tokens=state["tokens"] - tokens_before,
                        )
                        repo.immutable(attempt["attempt_id"], attempt)
                        state["attempt_ids"].append(attempt["attempt_id"])
                    save()
        state.update(status="COMPLETED", completed_at=datetime.now(UTC).isoformat())
        save()
    except (asyncio.CancelledError, TimeoutError, Stopped):
        stop.set()
        record_unfinished("批次停止或超过时限，未执行的标的保留失败记录。", "TIMEOUT")
        latest = repo.get_run(rid)
        if latest["status"] == "RUNNING":
            state.update(
                status="INTERRUPTED",
                error="预测已停止或超过签发时限，原判断与失败尝试保留。",
                elapsed_seconds=time.monotonic() - started,
            )
            repo.save(state)
    except Exception as error:
        stop.set()
        record_unfinished(str(error)[:400], "FAILED_INPUT")
        state.update(
            status="FAILED", error=str(error)[:400], elapsed_seconds=time.monotonic() - started
        )
        if repo.get_run(rid)["status"] == "RUNNING":
            repo.save(state)


def public_run(repo, state, developer=False):
    predictions = []
    for pid in state["prediction_ids"]:
        p = repo.prediction(pid)
        snapshot = repo.snapshot(state["snapshots"][p.security_id])
        versions = repo.outcome_versions(pid)
        predictions.append(
            dict(
                prediction=p.model_dump(mode="json"),
                name=state["subjects"][p.security_id]["name"],
                outcome=versions[-1] if versions else None,
                evidence_run_id=snapshot.evidence_run_id,
                chart_data=dict(
                    daily_prices=[
                        dict(session=str(v.session), close=str(v.close), turnover=str(v.turnover))
                        for v in snapshot.daily_prices
                    ],
                    indicators=list(snapshot.indicators),
                ),
            )
        )
    attempts = [repo.record(a) for a in state["attempt_ids"]]
    result = {
        k: state.get(k)
        for k in (
            "run_id",
            "title",
            "request",
            "status",
            "created_at",
            "base_session",
            "target_session",
            "cohort",
            "error",
        )
    }
    result.update(
        predictions=predictions,
        attempts=attempts,
        progress=state["process"][-1]["detail"] if state["process"] else "准备读取资料",
    )
    if developer:
        result["development"] = state
    return result


def scoreboard(repo, now=None):
    now = now or datetime.now(UTC)
    activation = repo.activation(template().experiment_id)
    if not activation:
        return dict(
            enabled=False,
            groups=[],
            slots=[],
            coverage=ratio(0, 0),
            calculated_at=now.isoformat(),
            predictive_validity="UNVERIFIED",
        )
    plan(repo, activation, now)
    predictions = repo.predictions()
    attempts = [repo.record(r["payload"]["attempt_id"]) for r in repo.records("DEC-ATTEMPT-")]
    slots = []
    for row in repo.records("DEC-SLOT-"):
        slot = row["payload"]
        key = (slot["experiment_id"], slot["security_id"], slot["target_session"])
        p = next(
            (
                p
                for p in predictions
                if p.cohort == "PRIMARY" and tuple(map(str, p.primary_key)) == key
            ),
            None,
        )
        relevant = [a for a in attempts if a["cohort"] == "PRIMARY" and tuple(a["slot_key"]) == key]
        ended = now >= datetime.fromisoformat(slot["window_end"])
        state = (
            "ISSUED"
            if p
            else relevant[-1]["state"]
            if relevant
            else "MISSED"
            if ended
            else "PLANNED"
        )
        slots.append(
            dict(
                slot,
                state=state,
                prediction_id=p.prediction_id if p else None,
                attempt_refs=[a["attempt_id"] for a in relevant],
                reason=relevant[-1]["reason"] if relevant else slot["reason"],
            )
        )
    groups = {}
    version_ids = []
    for p in predictions:
        versions = repo.outcome_versions(p.prediction_id)
        outcome = versions[-1] if versions else None
        if outcome:
            version_ids.append(outcome["outcome_id"])
        key = digest(
            [
                p.experiment_id,
                p.protocol_id,
                p.universe_version,
                p.model_version,
                p.prompt_version,
                p.method_versions,
                p.cohort,
                p.timing_group,
            ]
        )
        group = groups.setdefault(
            key,
            dict(
                group_id=key,
                experiment_id=p.experiment_id,
                protocol_id=p.protocol_id,
                universe_version=p.universe_version,
                model_version=p.model_version,
                prompt_version=p.prompt_version,
                method_versions=list(p.method_versions),
                cohort=p.cohort,
                timing_group=p.timing_group,
                records=[],
            ),
        )
        group["records"].append(
            dict(
                prediction_id=p.prediction_id,
                security_id=p.security_id,
                base_session=str(p.base_session),
                target_session=str(p.target_session),
                prediction=p.prediction,
                actual=outcome.get("return_direction") if outcome else None,
                outcome_status=outcome["status"] if outcome else "PENDING",
                outcome_version=outcome["version"] if outcome else None,
                baselines=dict(p.baselines),
            )
        )
    for group in groups.values():
        group["statistics"] = statistics(group["records"])
        group["outcome_counts"] = {
            s: sum(r["outcome_status"] == s for r in group["records"])
            for s in ("PENDING", "PENDING_DATA", "SCORABLE", "UNSCORABLE", "CONFLICT")
        }
    result = dict(
        enabled=True,
        config=activation["config"],
        slots=slots,
        groups=list(groups.values()),
        coverage=ratio(sum(s["state"] == "ISSUED" for s in slots), len(slots)),
        planned=len(slots),
        missed=sum(s["state"] == "MISSED" for s in slots),
        failed=sum(s["state"] in ("FAILED_INPUT", "FAILED_REVIEW", "TIMEOUT") for s in slots),
        result_versions_hash=digest(sorted(version_ids)),
        predictive_validity="UNVERIFIED",
    )
    key = "DEC-SCORE-" + digest(result)
    if repo.exists(key):
        return repo.record(key)
    result.update(snapshot_id=key, calculated_at=now.isoformat())
    return repo.immutable(key, result)


def observe(repo, state, now=None):
    now = now or datetime.now(UTC)
    observation = dict(request=dict(budget_profile=dict(max_seconds=120)), calls=[])
    client = SourceClient(repo, observation, lambda: None, threading.Event(), max_requests=20)
    outcomes = []
    for pid in state["prediction_ids"]:
        prediction = repo.prediction(pid)
        snapshot = repo.snapshot(state["snapshots"][prediction.security_id])
        result = observe_one(repo, prediction, snapshot, client, now)
        outcomes.append(append_result(repo, prediction, result, now))
    record = dict(
        run_id=state["run_id"],
        observed_at=now.isoformat(),
        calls=observation["calls"],
        outcome_ids=[o["outcome_id"] for o in outcomes],
    )
    repo.immutable("DEC-OBSERVE-" + uuid4().hex, record)
    return dict(outcomes=outcomes, scoreboard=scoreboard(repo, now))
