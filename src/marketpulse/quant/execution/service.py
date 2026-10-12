from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text

from marketpulse.infrastructure.storage.models import BlobRef

from ..contracts import canonical, digest
from ..domain import ArtifactBundle, ComputeInput, Instrument, MetricSpec
from ..storage.models import ComputeArtifactRow, ComputeJobRow, InstrumentRow, SnapshotOwnershipRow
from .environment import environment
from .recording import ComputeJournal
from .worker import ComputeWorker


def check_bundle(bundle: ArtifactBundle):
    if hashlib.sha256(bundle.output_json.encode()).hexdigest() != bundle.output_hash:
        raise ValueError("output hash mismatch")
    if canonical([v.model_dump(mode="json") for v in bundle.values]) != bundle.output_json:
        raise ValueError("numeric output drift")
    if hashlib.sha256(bundle.manifest_json.encode()).hexdigest() != bundle.manifest_hash:
        raise ValueError("manifest hash mismatch")
    manifest = json.loads(bundle.manifest_json)
    if (
        manifest["output_hash"] != bundle.output_hash
        or bundle.artifact_id != f"qart_{bundle.manifest_hash}"
    ):
        raise ValueError("artifact binding mismatch")
    if tuple(i["snapshot_id"] for i in manifest["inputs"]) != bundle.input_snapshot_ids:
        raise ValueError("input snapshot binding mismatch")


def check_input_binding(bundle, request):
    manifest = json.loads(bundle.manifest_json)
    if (
        manifest["spec"] != request.spec.model_dump(mode="json")
        or manifest["asof"] != request.asof.isoformat()
    ):
        raise ValueError("compute request binding mismatch")
    if manifest["environment"] != json.loads(request.environment_json):
        raise ValueError("compute environment binding mismatch")
    if manifest["instrument"] != json.loads(request.instrument_definition_json):
        raise ValueError("frozen market rules binding mismatch")
    inputs = {s.snapshot_id: s for s in request.snapshots}
    if set(inputs) != set(bundle.input_snapshot_ids):
        raise ValueError("compute input identities mismatch")
    for descriptor in manifest["inputs"]:
        snapshot = inputs[descriptor["snapshot_id"]]
        result = snapshot.result
        expected = {
            "snapshot_id": snapshot.snapshot_id,
            "semantic_hash": snapshot.semantic_hash,
            "manifest_ref": snapshot.manifest_ref,
            "partitions": [p.model_dump(mode="json") for p in snapshot.partitions],
            "request": result.request.model_dump(mode="json"),
            "normalizer": result.request.normalizer_version,
            "units": result.units,
            "rights": result.rights.model_dump(mode="json"),
            "pit": result.pit,
            "stale": snapshot.stale,
            "provider": result.provider,
            "upstream": result.upstream,
            "family": result.source_family,
            "lineage": result.lineage_status,
            "quality": result.quality_flags,
        }
        if canonical(descriptor) != canonical(expected):
            raise ValueError("compute frozen input descriptor mismatch")


class QuantService:
    def __init__(self, store, *, root: Path, worker: ComputeWorker | None = None):
        self.store, self.sessions, self.blobs = store, store.sessions, store.blobs
        self.root, self.worker = root, worker or ComputeWorker()
        self.journal = ComputeJournal(self.sessions, self.blobs)
        self.tasks = {}

    def _input(self, run_id, spec, inputs, instrument_id, asof):
        snapshots = tuple(self.store.load(i, stale=False) for i in sorted(set(inputs)))
        with self.sessions() as session:
            from marketpulse.investigation.persistence.models import InvestigationRunRow

            run = session.get(InvestigationRunRow, run_id)
            if run is None:
                raise ValueError("unknown run")
            for s in snapshots:
                if not session.get(SnapshotOwnershipRow, (run.investigation_id, s.snapshot_id)):
                    raise PermissionError("snapshot is not owned by this investigation")
            row = session.get(InstrumentRow, instrument_id)
            if row is None:
                raise ValueError("unknown instrument")
            definition = Instrument.model_validate(row.definition)
        return ComputeInput(
            snapshots=snapshots,
            instrument_id=instrument_id,
            spec=spec,
            asof=asof,
            instrument_definition_json=definition.model_dump_json(),
            environment_json=environment(self.root),
        )

    async def submit(
        self,
        *,
        run_id: str,
        spec: MetricSpec,
        inputs: tuple[str, ...],
        instrument_id: str,
        asof: datetime,
        idempotency_key: str,
        budget_seconds: float = 30,
    ) -> str:  # noqa: ASYNC109
        if not 0 < budget_seconds <= 120:
            raise ValueError("bounded compute timeout required")
        request = await asyncio.to_thread(self._input, run_id, spec, inputs, instrument_id, asof)
        identity = digest(
            {
                "spec": spec.model_dump(mode="json"),
                "inputs": sorted(set(inputs)),
                "instrument": instrument_id,
                "asof": asof.isoformat(),
            }
        )
        job_id, token, completed = await asyncio.to_thread(
            self._claim, run_id, idempotency_key, identity, request, budget_seconds
        )
        if completed:
            await asyncio.to_thread(self.bundle_for, job_id)
            return job_id
        task = asyncio.current_task()
        self.tasks[job_id] = task
        try:
            # Recovery uses the original pinned environment/input, never runtime latest.
            request = await asyncio.to_thread(self.input_for, job_id)
            bundle = await self.worker.run(request, timeout=budget_seconds)
            check_bundle(bundle)
            await asyncio.to_thread(self._complete, job_id, token, bundle)
        except BaseException as error:
            await asyncio.to_thread(self._failed, job_id, token, type(error).__name__)
            raise
        finally:
            self.tasks.pop(job_id, None)
        return job_id

    def _claim(self, run_id, key, identity, request, timeout):
        if not key or len(key) > 100:
            raise ValueError("invalid idempotency key")
        now, token = datetime.now(UTC), uuid.uuid4().hex
        with self.sessions.begin() as session:
            if session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            row = session.scalar(
                select(ComputeJobRow).where(
                    ComputeJobRow.run_id == run_id, ComputeJobRow.idempotency_key == key
                )
            )
            if row:
                if row.input_hash != identity:
                    raise ValueError("idempotency key reused for different computation")
                if row.status == "COMPLETED":
                    return row.job_id, token, True
                lease = row.lease_until
                if lease and lease.tzinfo is None:
                    lease = lease.replace(tzinfo=UTC)
                if row.status == "RUNNING" and lease and lease > now:
                    raise RuntimeError("compute lease is active")
            else:
                ref = self.blobs.put_bytes(request.model_dump_json().encode()).ref.uri
                row = ComputeJobRow(
                    job_id=f"qjob_{uuid.uuid4().hex}",
                    run_id=run_id,
                    idempotency_key=key,
                    input_hash=identity,
                    input_ref=ref,
                    status="PENDING",
                )
                session.add(row)
                row.attempt = 0
            row.attempt += 1
            row.status, row.lease_token, row.lease_until = (
                "RUNNING",
                token,
                now + timedelta(seconds=timeout + 5),
            )
            return row.job_id, token, False

    def _complete(self, job_id, token, bundle):
        check_input_binding(bundle, self.input_for(job_id))
        with self.sessions.begin() as session:
            if session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(ComputeJobRow, job_id)
            if row.lease_token != token or row.status != "RUNNING":
                raise ValueError("compute lease lost")
            existing = session.get(ComputeArtifactRow, bundle.artifact_id)
            if not existing:
                session.add(
                    ComputeArtifactRow(
                        artifact_id=bundle.artifact_id,
                        manifest_ref=self.blobs.put_bytes(bundle.manifest_json.encode()).ref.uri,
                        output_ref=self.blobs.put_bytes(bundle.output_json.encode()).ref.uri,
                        bundle_ref=self.blobs.put_bytes(bundle.model_dump_json().encode()).ref.uri,
                    )
                )
            self.journal.append_in_session(
                session,
                job_id=job_id,
                logical_key=row.idempotency_key,
                ordinal=0,
                attempt=row.attempt,
                input_hash=row.input_hash,
                artifact_id=bundle.artifact_id,
            )
            row.artifact_id, row.status, row.lease_until, row.lease_token = (
                bundle.artifact_id,
                "COMPLETED",
                None,
                None,
            )

    def _failed(self, job_id, token, error):
        with self.sessions.begin() as session:
            row = session.get(ComputeJobRow, job_id)
            if row.lease_token != token or row.status != "RUNNING":
                return
            self.journal.append_in_session(
                session,
                job_id=job_id,
                logical_key=row.idempotency_key,
                ordinal=0,
                attempt=row.attempt,
                input_hash=row.input_hash,
                error=error,
            )
            row.status = "CANCELLED" if error == "CancelledError" else "FAILED"
            row.error_code, row.lease_until, row.lease_token = error, None, None

    def input_for(self, job_id, *, require_current_environment=True):
        with self.sessions() as session:
            row = session.get(ComputeJobRow, job_id)
            if not row:
                raise ValueError("unknown computation")
            request = ComputeInput.model_validate_json(
                self.blobs.get_bytes(BlobRef.from_uri(row.input_ref))
            )
        # Re-load every frozen stream, not just an inline copy of its digest.
        snapshots = tuple(self.store.load(s.snapshot_id, stale=s.stale) for s in request.snapshots)
        if snapshots != request.snapshots:
            raise ValueError("frozen input drift")
        current = json.loads(environment(self.root))
        pinned = json.loads(request.environment_json)
        if require_current_environment and (
            current["source_tree_hash"] != pinned["source_tree_hash"]
            or current["lock_hash"] != pinned["lock_hash"]
        ):
            raise ValueError("pinned source tree or lock is unavailable")
        return request

    def load_artifact(self, artifact_id):
        with self.sessions() as session:
            row = session.get(ComputeArtifactRow, artifact_id)
            if not row:
                raise ValueError("incomplete artifact")
            bundle = ArtifactBundle.model_validate_json(
                self.blobs.get_bytes(BlobRef.from_uri(row.bundle_ref))
            )
            if (
                self.blobs.get_bytes(BlobRef.from_uri(row.manifest_ref))
                != bundle.manifest_json.encode()
                or self.blobs.get_bytes(BlobRef.from_uri(row.output_ref))
                != bundle.output_json.encode()
            ):
                raise ValueError("stored artifact mismatch")
            check_bundle(bundle)
            return bundle

    def bundle_for(self, job_id):
        with self.sessions() as session:
            row = session.get(ComputeJobRow, job_id)
            if not row or row.status != "COMPLETED":
                raise ValueError("computation not complete")
            return self.load_artifact(row.artifact_id)

    async def reproduce(self, *, job_id, offline=True):
        if not offline:
            raise ValueError("reproduction cannot refetch data")
        request = await asyncio.to_thread(self.input_for, job_id)
        recorded = await asyncio.to_thread(self.bundle_for, job_id)
        # Freeze the current environment for a truthful cross-environment comparison.
        current = await asyncio.to_thread(environment, self.root)
        new = await self.worker.run(request.model_copy(update={"environment_json": current}))
        check_bundle(new)
        from ..validation import compare_reproduction

        compare_reproduction(recorded, new, request.spec)
        return new

    async def cancel(self, *, job_id):
        task = self.tasks.get(job_id)
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def cancel_run(self, run_id):
        with self.sessions() as session:
            ids = list(
                session.scalars(select(ComputeJobRow.job_id).where(ComputeJobRow.run_id == run_id))
            )
        for job_id in ids:
            await self.cancel(job_id=job_id)
