from __future__ import annotations

import hashlib
import io
import json
from contextlib import nullcontext
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef

from ..contracts import canonical, digest, request_hash
from ..domain import DataRequest, DataResult, DatasetSnapshot, Instrument, Partition
from .models import DatasetSnapshotRow, InstrumentRow, SnapshotOwnershipRow


def cache_key(result: DataResult) -> str:
    return digest(
        {
            "request": request_hash(result.request),
            "provider": result.provider,
            "upstream": result.upstream,
            "rights": result.rights.model_dump(mode="json"),
            "anchor": result.request.adjustment_anchor.isoformat()
            if result.request.adjustment_anchor
            else None,
            "factor_hash": result.adjustment_factor_hash,
        }
    )


class SnapshotStore:
    def __init__(
        self, sessions: sessionmaker[Session], blobs: LocalContentAddressedBlobStorage
    ) -> None:
        self.sessions, self.blobs = sessions, blobs

    def register(self, instrument: Instrument) -> None:
        with self.sessions.begin() as session:
            existing = session.get(InstrumentRow, instrument.instrument_id)
            definition = instrument.model_dump(mode="json")
            if existing:
                if existing.definition != definition:
                    raise ValueError("instrument definition is frozen")
                return
            session.add(
                InstrumentRow(
                    instrument_id=instrument.instrument_id,
                    market=instrument.market,
                    exchange=instrument.aliases[0].exchange,
                    definition=definition,
                )
            )

    def freeze(self, result: DataResult) -> DatasetSnapshot:
        # Same writer boundary as archive GC: publishing files without a live DB
        # reference must not race removal of an old archive using identical bytes.
        with self.sessions.begin() as session:
            if session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            return self._freeze_locked(result, session)

    def _freeze_locked(self, result: DataResult, session: Session) -> DatasetSnapshot:
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows = json.loads(result.records_json)
        if len(rows) > 5000 or any(
            row.get("instrument_id") not in result.request.instruments for row in rows
        ):
            raise ValueError("dataset row/identity bound exceeded")
        if result.request.complete_sessions_only and any(row.get("provisional") for row in rows):
            raise ValueError("complete-session snapshot cannot contain provisional bars")
        if (
            not rows and result.request.dataset != "adjustment"
        ) or "duplicate_observations" in result.quality_flags:
            raise ValueError("empty/duplicate dataset cannot be frozen")
        parts = []
        for layer, content in (
            ("raw", result.raw_records_json),
            ("normalized", result.records_json),
        ):
            groups: dict[int, list[dict]] = {}
            for ordinal, row in enumerate(json.loads(content)):
                stamp = (
                    row.get("date")
                    or row.get("日期")
                    or row.get("数据日期")
                    or row.get("period_end")
                    or row.get("statDate")
                    or row.get("dividOperateDate")
                )
                # Wide financial input is a provider response, not individual bar blobs.
                year = int(str(stamp)[:4]) if stamp else result.request.end.year
                groups.setdefault(year, []).append(
                    {**row, "_quant_ordinal": ordinal, "_quant_json": canonical(row)}
                )
            if not groups and result.request.dataset == "adjustment":
                groups[result.request.end.year] = []
            for year, records in sorted(groups.items()):
                stream = io.BytesIO()
                table = pa.Table.from_pylist(records)
                if not records:
                    table = pa.table(
                        {
                            "instrument_id": pa.array([], type=pa.string()),
                            "date": pa.array([], type=pa.date32()),
                            "_quant_ordinal": pa.array([], type=pa.int64()),
                            "_quant_json": pa.array([], type=pa.string()),
                        }
                    )
                if layer == "normalized":
                    for column in ("date", "period_end", "published_at"):
                        if column in table.column_names:
                            values = [
                                datetime.fromisoformat(r[column]).date() if r.get(column) else None
                                for r in records
                            ]
                            table = table.set_column(
                                table.schema.get_field_index(column),
                                column,
                                pa.array(values, type=pa.date32()),
                            )
                pq.write_table(table, stream, compression="zstd", version="2.6")
                blob = self.blobs.put_bytes(stream.getvalue())
                parts.append(
                    Partition(
                        layer=layer,
                        dataset=result.request.dataset,
                        year=year,
                        blob_ref=blob.ref.uri,
                        sha256=blob.ref.sha256,
                        row_count=len(records),
                    )
                )
        semantic = digest(
            {
                "result": result.model_dump(mode="json", exclude={"retrieved_at"}),
                "partitions": [part.model_dump(mode="json") for part in parts],
            }
        )
        manifest = {
            "version": "2",
            "semantic_hash": semantic,
            "cache_key": cache_key(result),
            "request_hash": request_hash(result.request),
            "result": result.model_dump(mode="json", exclude={"records_json", "raw_records_json"}),
            "records_sha256": hashlib.sha256(result.records_json.encode()).hexdigest(),
            "raw_records_sha256": hashlib.sha256(result.raw_records_json.encode()).hexdigest(),
            "partitions": [part.model_dump(mode="json") for part in parts],
            "row_count": len(rows),
        }
        ref = self.blobs.put_bytes(canonical(manifest).encode()).ref
        snapshot = DatasetSnapshot(
            snapshot_id="qds_" + semantic,
            semantic_hash=semantic,
            request_hash=request_hash(result.request),
            result=result,
            partitions=tuple(parts),
            manifest_ref=ref.uri,
            row_count=len(rows),
            frozen_at=datetime.now(UTC),
        )
        try:
            with session.begin_nested():
                for instrument in result.request.instruments:
                    if session.get(InstrumentRow, instrument) is None:
                        raise ValueError("instrument must be registered before freezing")
                existing = session.get(DatasetSnapshotRow, snapshot.snapshot_id)
                if existing:
                    return self.load(existing.snapshot_id)
                session.add(
                    DatasetSnapshotRow(
                        snapshot_id=snapshot.snapshot_id,
                        semantic_hash=semantic,
                        cache_key=cache_key(result),
                        request_hash=snapshot.request_hash,
                        provider=result.provider,
                        upstream=result.upstream,
                        family_key=result.source_family,
                        lineage_status=result.lineage_status,
                        retrieved_at=result.retrieved_at,
                        asof=result.request.asof,
                        pit_level=result.pit.value,
                        rights_policy_id=result.rights.policy_id,
                        schema_version=result.request.schema_version,
                        normalizer_version=result.request.normalizer_version,
                        row_count=len(rows),
                        quality_flags=list(result.quality_flags),
                        manifest_ref=ref.uri,
                        partitions=manifest["partitions"],
                        frozen_payload=snapshot.model_dump(
                            mode="json", exclude={"result": {"records_json", "raw_records_json"}}
                        ),
                    )
                )
        except IntegrityError:
            # Concurrent identical publication: immutable first writer wins, never overwrite.
            return self.load(snapshot.snapshot_id)
        return snapshot

    def load(
        self, snapshot_id: str, *, stale: bool = False, session: Session | None = None
    ) -> DatasetSnapshot:
        import pyarrow.parquet as pq

        with nullcontext(session) if session is not None else self.sessions() as session:
            row = session.get(DatasetSnapshotRow, snapshot_id)
            if row is None:
                raise KeyError(snapshot_id)
            payload = dict(row.frozen_payload)
            payload["result"] = dict(payload["result"])
            for layer, field in (("raw", "raw_records_json"), ("normalized", "records_json")):
                records = []
                for part in payload["partitions"]:
                    if part["layer"] == layer:
                        ref = BlobRef.from_uri(part["blob_ref"])
                        if ref.sha256 != part["sha256"]:
                            raise ValueError("partition digest mismatch")
                        table = pq.read_table(
                            io.BytesIO(self.blobs.get_bytes(ref)),
                            columns=["_quant_ordinal", "_quant_json"],
                        )
                        if table.num_rows != part["row_count"]:
                            raise ValueError("partition row count mismatch")
                        records.extend(table.to_pylist())
                ordered = sorted(records, key=lambda item: item["_quant_ordinal"])
                payload["result"][field] = canonical(
                    [json.loads(r["_quant_json"]) for r in ordered]
                )
            snapshot = DatasetSnapshot.model_validate(payload)
        manifest = json.loads(self.blobs.get_bytes(BlobRef.from_uri(snapshot.manifest_ref)))
        if manifest["semantic_hash"] != snapshot.semantic_hash:
            raise ValueError("manifest semantic hash mismatch")
        expected = digest(
            {
                "result": snapshot.result.model_dump(mode="json", exclude={"retrieved_at"}),
                "partitions": [part.model_dump(mode="json") for part in snapshot.partitions],
            }
        )
        result_meta = snapshot.result.model_dump(
            mode="json", exclude={"records_json", "raw_records_json"}
        )
        if expected != snapshot.semantic_hash or manifest["result"] != result_meta:
            raise ValueError("snapshot payload integrity mismatch")
        for field in ("records_json", "raw_records_json"):
            key = "raw_records_sha256" if field == "raw_records_json" else "records_sha256"
            if (
                manifest[key]
                != hashlib.sha256(getattr(snapshot.result, field).encode()).hexdigest()
            ):
                raise ValueError("record stream integrity mismatch")
        if manifest["partitions"] != [part.model_dump(mode="json") for part in snapshot.partitions]:
            raise ValueError("partition manifest mismatch")
        for partition in snapshot.partitions:
            ref = BlobRef.from_uri(partition.blob_ref)
            if ref.sha256 != partition.sha256:
                raise ValueError("partition digest mismatch")
            self.blobs.verify_hash(ref)
        if snapshot.result.request.normalizer_version == "dcf-official-v1":
            from marketpulse.infrastructure.storage.models import BlobError

            for row in json.loads(snapshot.result.records_json):
                try:
                    self.blobs.verify_hash(BlobRef(row["document_sha256"]))
                except BlobError as error:
                    raise ValueError("DCF original document integrity failure") from error
        return snapshot.model_copy(update={"stale": stale})

    def find(self, request: DataRequest, *, stale: bool = False) -> DatasetSnapshot | None:
        if request.snapshot_id:
            value = self.load(request.snapshot_id, stale=stale)
            if value.request_hash != request_hash(request):
                raise ValueError("explicit snapshot does not match frozen request")
            return value
        with self.sessions() as session:
            ids = session.scalars(
                select(DatasetSnapshotRow.snapshot_id).where(
                    DatasetSnapshotRow.request_hash == request_hash(request)
                )
            ).all()
        # Ambiguity requires an explicit ID; do not silently resolve runtime latest.
        if len(ids) > 1:
            raise ValueError("multiple frozen snapshots; pin snapshot_id")
        return self.load(ids[0], stale=stale) if ids else None

    def attach(
        self, snapshot_id: str, investigation_id: str, retention_until: datetime | None = None
    ):
        value = self.load(snapshot_id)
        with self.sessions.begin() as session:
            key = (investigation_id, snapshot_id)
            if session.get(SnapshotOwnershipRow, key) is None:
                session.add(
                    SnapshotOwnershipRow(
                        investigation_id=investigation_id,
                        snapshot_id=snapshot_id,
                        rights_scope=value.result.request.scope.value,
                        retention_until=retention_until,
                    )
                )
