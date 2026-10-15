"""Append-only experiment records in the existing SQLite/CAS store."""

import copy
import json
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ...contracts import digest
from ...live.store import ResearchRunRow
from ..store import DecisionStore
from .contracts import (
    ExperimentConfig,
    NextSessionOutcome,
    NextSessionPrediction,
    NextSessionSnapshot,
)


class ExperimentStore:
    def __init__(self, base, *, request_cache=False):
        self.base = base
        self.evidence = DecisionStore(base)
        self.request_cache = request_cache
        self._bytes = {}
        self._snapshots = {}

    def blob(self, value):
        return self.base.blob(value)

    def raw(self, value):
        return self.base.raw(value)

    def value(self, ref):
        return json.loads(self.raw_bytes(ref))

    def raw_bytes(self, ref):
        if self.request_cache and ref in self._bytes:
            return self._bytes[ref]
        value = self.evidence.raw_bytes(ref)
        if self.request_cache:
            self._bytes[ref] = value
        return value

    def exists(self, key):
        with self.base.sessions() as s:
            return s.get(ResearchRunRow, key) is not None

    def records(self, prefix):
        with self.base.sessions() as s:
            rows = s.scalars(
                select(ResearchRunRow)
                .where(ResearchRunRow.run_id.like(prefix + "%"))
                .order_by(ResearchRunRow.created_at)
            ).all()
            return [copy.deepcopy(r.payload) for r in rows]

    def immutable(self, key, payload, status="COMPLETED"):
        payload = copy.deepcopy(payload)
        envelope = dict(payload=payload, payload_hash=digest(payload))
        try:
            with self.base.sessions.begin() as s:
                s.add(
                    ResearchRunRow(
                        run_id=key,
                        status=status,
                        created_at=datetime.now(UTC).isoformat(),
                        payload=envelope,
                    )
                )
            return payload
        except IntegrityError:
            prior = self.record(key)
            if prior != payload:
                raise ValueError("不可变记录已存在不同内容") from None
            return prior

    def record(self, key):
        with self.base.sessions() as s:
            row = s.get(ResearchRunRow, key)
            if row is None:
                raise KeyError(key)
            envelope = copy.deepcopy(row.payload)
        if digest(envelope["payload"]) != envelope["payload_hash"]:
            raise ValueError("实验记录哈希不符")
        return envelope["payload"]

    def activation(self, experiment_id):
        key = "DEC-CONFIG-" + experiment_id
        if not self.exists(key):
            return None
        value = self.record(key)
        ExperimentConfig.model_validate(value["config"])
        for call in value["calls"]:
            for name in ("request_ref", "response_ref"):
                if call.get(name):
                    self.raw_bytes(call[name])
        return value

    def create_run(self, request, config, implementation):
        rid = "RUN-NS-" + request["request_key"].replace("-", "")
        state = dict(
            run_id=rid,
            request=request,
            config=config,
            status="RUNNING",
            scenario="next_session",
            title="下一交易日预测",
            created_at=datetime.now(UTC).isoformat(),
            implementation=implementation,
            process=[],
            calls=[],
            model_calls=0,
            tokens=0,
            charged_tokens=0,
            elapsed_seconds=0,
            prediction_ids=[],
            attempt_ids=[],
            snapshots={},
        )
        try:
            with self.base.sessions.begin() as s:
                s.add(
                    ResearchRunRow(
                        run_id=rid, status="RUNNING", created_at=state["created_at"], payload=state
                    )
                )
            return state, True
        except IntegrityError:
            old = self.get_run(rid)
            if old["request"] != request:
                raise ValueError("请求标识已绑定其他条件") from None
            return old, False

    def save(self, state):
        with self.base.sessions.begin() as s:
            old = s.get(ResearchRunRow, state["run_id"])
            if old is None or old.payload["request"] != state["request"]:
                raise ValueError("实验请求不一致")
            changed = s.execute(
                update(ResearchRunRow)
                .where(ResearchRunRow.run_id == state["run_id"], ResearchRunRow.status == "RUNNING")
                .values(payload=copy.deepcopy(state), status=state["status"])
            )
            if changed.rowcount != 1:
                raise ValueError("终止任务不能被迟到结果覆盖")

    def get_run(self, rid):
        with self.base.sessions() as s:
            row = s.get(ResearchRunRow, rid)
            if row is None:
                raise KeyError(rid)
            state = copy.deepcopy(row.payload)
        for call in state.get("calls", []):
            for name in ("request_ref", "response_ref"):
                if call.get(name):
                    self.raw_bytes(call[name])
        for ref in state.get("snapshots", {}).values():
            self.snapshot(ref)
        for pid in state.get("prediction_ids", []):
            prediction = self.prediction(pid)
            snapshot = self.snapshot(state["snapshots"][prediction.security_id])
            if (
                prediction.input_hash != snapshot.input_hash
                or prediction.base_session != snapshot.base_session
                or prediction.target_session != snapshot.target_session
            ):
                raise ValueError("预测与封存输入不一致")
            from .agents import Proposal, Review, Writing, guard_proposal

            review = Review.model_validate(self.value(prediction.review_refs[0]))
            writing = Writing.model_validate(self.value(prediction.review_refs[1]))
            proposal = Proposal(
                direction=prediction.prediction,
                summary=prediction.summary,
                reasons=prediction.reasons,
                opposing_reasons=prediction.opposing_reasons,
                limitations=prediction.limitations,
                support_level=prediction.support_level,
            )
            guard_proposal(proposal, snapshot.model_context)
            if (
                review.verdict != "APPROVE"
                or not all(
                    (
                        review.citations_supported,
                        review.next_session_relevant,
                        review.no_invented_facts,
                    )
                )
                or writing.direction != prediction.prediction
                or writing.summary != prediction.summary
                or writing.approved_hash != digest(proposal.model_dump(mode="json"))
            ):
                raise ValueError("预测与已保存审核结果不一致")
        for aid in state.get("attempt_ids", []):
            self.record(aid)
        return state

    def snapshot(self, ref):
        if self.request_cache and ref in self._snapshots:
            return self._snapshots[ref]
        value = self.value(ref)
        snap = NextSessionSnapshot.model_validate(value)
        if digest({k: v for k, v in value.items() if k != "input_hash"}) != snap.input_hash:
            raise ValueError("封存输入哈希不符")
        data = dict(
            bundle=value["evidence"],
            evidence=value["evidence"]["items"],
            sources=value["sources"],
            documents=value["documents"],
        )
        # P1 facts/quotes/calculations are verified separately by the daily reader.
        from .snapshot import verify_snapshot

        verify_snapshot(self, snap, data)
        if self.request_cache:
            self._snapshots[ref] = snap
        return snap

    def publish(self, prediction):
        value = prediction.model_dump(mode="json")
        if prediction.cohort == "PRIMARY":
            key = "DEC-PRIMARY-" + digest(list(map(str, prediction.primary_key)))
            pointer = dict(prediction_id=prediction.prediction_id)
            try:
                with self.base.sessions.begin() as s:
                    for record_id, payload in ((key, pointer), (prediction.prediction_id, value)):
                        s.add(
                            ResearchRunRow(
                                run_id=record_id,
                                status="COMPLETED",
                                created_at=datetime.now(UTC).isoformat(),
                                payload=dict(payload=payload, payload_hash=digest(payload)),
                            )
                        )
                return prediction
            except IntegrityError:
                if self.exists(prediction.prediction_id):
                    old = self.prediction(prediction.prediction_id)
                    if old == prediction:
                        return old
                    raise ValueError("预测标识内容冲突") from None
                prediction = prediction.model_copy(
                    update={
                        "cohort": "EXPLORATORY",
                        "supersedes_id": self.record(key)["prediction_id"],
                    }
                )
                value = prediction.model_dump(mode="json")
        self.immutable(prediction.prediction_id, value)
        return prediction

    def prediction(self, pid):
        return NextSessionPrediction.model_validate(self.record(pid))

    def predictions(self):
        return [self.prediction(r["payload"]["prediction_id"]) for r in self.records("DEC-PRED-")]

    def snapshot_for_prediction(self, prediction):
        with self.base.sessions() as session:
            states = session.scalars(
                select(ResearchRunRow).where(ResearchRunRow.run_id.like("RUN-NS-%"))
            ).all()
            refs = {
                s.payload["snapshots"][prediction.security_id]
                for s in states
                if prediction.prediction_id in s.payload.get("prediction_ids", [])
            }
        if len(refs) != 1:
            raise ValueError("历史核对无法唯一绑定原封存快照")
        snapshot = self.snapshot(next(iter(refs)))
        if snapshot.input_hash != prediction.input_hash:
            raise ValueError("历史核对封存输入哈希不符")
        return snapshot

    def outcome_versions(self, pid):
        results = []
        for row in self.records("DEC-OUT-"):
            p = row["payload"]
            if p["prediction_id"] == pid:
                value = self.record(p["outcome_id"])
                NextSessionOutcome.model_validate(value)
                if value["status"] == "UNSCORABLE":
                    from .outcomes import full_suspension

                    p = self.prediction(pid)
                    if not value["price_source_refs"] or not full_suspension(
                        self.value(value["price_source_refs"][0]), p.security_id, p.target_session
                    ):
                        raise ValueError("全日停牌不能按原文复核")
                if value["status"] == "SCORABLE":
                    from .labels import classify_outcome
                    from .prices import agree, quote

                    p = self.prediction(pid)
                    symbol = ("sh" if p.security_id.endswith(".SH") else "sz") + p.security_id[:6]
                    refs = value["price_source_refs"][:2]
                    historical = self.raw_bytes(refs[0]).lstrip().startswith(b"{")
                    if historical:
                        from .historical_prices import verify

                        observed = verify(self, p, value)
                    else:
                        observed = agree(
                            [
                                (
                                    None,
                                    {
                                        symbol: quote(
                                            self.raw_bytes(ref).decode("gb18030"), symbol, family
                                        )
                                    },
                                )
                                for ref, family in zip(refs, ("TENCENT", "SINA"), strict=True)
                            ],
                            symbol,
                            p.target_session,
                            datetime.fromisoformat(value["observed_at"]),
                        )
                    from decimal import Decimal

                    direction, correct = classify_outcome(
                        p.prediction,
                        Decimal(observed["close"]),
                        Decimal(observed["reference_price"]),
                    )
                    if (
                        Decimal(observed["close"]) != Decimal(value["close"])
                        or Decimal(observed["reference_price"]) != Decimal(value["reference_price"])
                        or direction != value["return_direction"]
                        or correct != value["correct"]
                    ):
                        raise ValueError("核对结果与目标日原始行情不符")
                for ref, hash_value in zip(
                    value["price_source_refs"], value["source_hashes"], strict=True
                ):
                    from marketpulse.infrastructure.storage.models import BlobRef

                    if BlobRef.from_uri(ref).sha256 != hash_value:
                        raise ValueError("结果原文哈希不符")
                    self.raw_bytes(ref)
                results.append(value)
        return sorted(results, key=lambda v: v["version"])

    def add_outcome(self, outcome):
        # Compare the current result inside the writer transaction; readers never choose versions.
        value = outcome.model_dump(mode="json")
        with self.base.sessions.begin() as s:
            rows = s.scalars(
                select(ResearchRunRow).where(ResearchRunRow.run_id.like("DEC-OUT-%"))
            ).all()
            prior = [
                r.payload["payload"]
                for r in rows
                if r.payload["payload"]["prediction_id"] == outcome.prediction_id
            ]
            latest = max(prior, key=lambda v: v["version"]) if prior else None
            for previous in prior:
                if previous["outcome_id"] == outcome.outcome_id:
                    if previous != value:
                        raise ValueError("结果标识内容冲突")
                    return previous
            if outcome.version != (
                latest["version"] + 1 if latest else 1
            ) or outcome.supersedes_id != (latest["outcome_id"] if latest else None):
                raise ValueError("结果版本发生并发变化，请重读")
            s.add(
                ResearchRunRow(
                    run_id=outcome.outcome_id,
                    status="COMPLETED",
                    created_at=datetime.now(UTC).isoformat(),
                    payload=dict(payload=value, payload_hash=digest(value)),
                )
            )
        return value
