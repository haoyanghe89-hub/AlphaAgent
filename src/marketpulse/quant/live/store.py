"""Transactionally register jobs in the existing DB and hash-address their evidence."""

import copy
import json
from pathlib import Path

from sqlalchemy import JSON, String, select
from sqlalchemy.orm import Mapped, mapped_column

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.investigation.persistence.base import Base

from ..contracts import canonical


class ResearchRunRow(Base):
    __tablename__ = "inv_quant_research_run"
    run_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class ResearchStore:
    def __init__(self, sessions, blobs, legacy_root: Path):
        self.sessions, self.blobs, self.legacy_root = sessions, blobs, legacy_root

    def put(self, state):
        payload = copy.deepcopy(state)
        payload.pop("data", None)
        with self.sessions.begin() as session:
            row = session.get(ResearchRunRow, state["run_id"])
            if row:
                if row.payload["request"] != payload["request"]:
                    raise ValueError("request identity conflict")
                if row.status == "COMPLETED" and row.payload != payload:
                    raise ValueError("completed research is immutable")
                row.payload, row.status = payload, payload["status"]
            else:
                session.add(
                    ResearchRunRow(
                        run_id=payload["run_id"],
                        status=payload["status"],
                        created_at=payload["created_at"],
                        payload=payload,
                    )
                )

    def blob(self, value):
        return self.blobs.put_bytes(canonical(value).encode()).ref.uri

    def value(self, uri):
        return json.loads(self.blobs.get_bytes(BlobRef.from_uri(uri)))

    def raw(self, content):
        return self.blobs.put_bytes(content).ref.uri

    def exists(self, run_id):
        with self.sessions() as session:
            if session.get(ResearchRunRow, run_id):
                return True
        return (self.legacy_root / run_id / "state.json").is_file()

    def get(self, run_id, *, with_data=True):
        with self.sessions() as session:
            row = session.get(ResearchRunRow, run_id)
            state = copy.deepcopy(row.payload) if row else None
        if state is None:
            from .api import load

            return load(self.legacy_root, run_id)
        # A trace is part of the record, not just decorative UI content.
        for call in state.get("calls", []):
            for name in ("request_ref", "response_ref"):
                if call.get(name):
                    self.blobs.get_bytes(BlobRef.from_uri(call[name]))
        if with_data and state.get("result_ref"):
            data = self.value(state["result_ref"])
            for name in ("package_ref", "reviews_ref", "references_ref", "computation_ref"):
                if data.get(name):
                    self.value(data[name])
            for source in data["sources"]:
                ref = BlobRef.from_uri(source["blob_ref"])
                if ref.sha256 != source["sha256"]:
                    raise ValueError("source descriptor hash mismatch")
                self.blobs.get_bytes(ref)
            for evidence in data.get("evidence", []):
                if evidence.get("text_ref"):
                    text = self.value(evidence["text_ref"])["text"]
                    if text[evidence["start"] : evidence["end"]] != evidence["text"]:
                        raise ValueError("evidence character locator mismatch")
            state["data"] = data
        return state

    def list(self, *, prefix=None):
        with self.sessions() as session:
            query = select(ResearchRunRow)
            query = (
                query.where(ResearchRunRow.run_id.like(prefix + "%"))
                if prefix
                else query.where(
                    ~ResearchRunRow.run_id.like("RUN-DEC-%"),
                    ~ResearchRunRow.run_id.like("RUN-NS-%"),
                    ~ResearchRunRow.run_id.like("DEC-%"),
                )
            )
            registered = [
                copy.deepcopy(row.payload)
                for row in session.scalars(
                    query.order_by(ResearchRunRow.created_at.desc()).limit(100)
                )
            ]
        ids = {s["run_id"] for s in registered}
        from .api import read

        for path in [] if prefix else self.legacy_root.glob("RUN-LIVE-*/state.json"):
            if path.parent.name not in ids:
                registered.append(read(path))
        return sorted(registered, key=lambda s: s["created_at"], reverse=True)[:100]

    def interrupt_abandoned(self):
        with self.sessions.begin() as session:
            for row in session.scalars(
                select(ResearchRunRow).where(ResearchRunRow.status == "RUNNING")
            ):
                state = copy.deepcopy(row.payload)
                state.update(status="INTERRUPTED", error="服务重启，已完成步骤保留，可继续研究。")
                for node in state.get("process", []) + state.get("calls", []):
                    if node.get("status") == "RUNNING":
                        node["status"] = "INTERRUPTED"
                row.status, row.payload = state["status"], state


def local_store(sessions, root):
    return ResearchStore(
        sessions, LocalContentAddressedBlobStorage(root / "research-blobs"), root / "live-research"
    )
