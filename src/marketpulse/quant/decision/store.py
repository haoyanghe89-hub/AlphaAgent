"""Decision namespace on the existing SQLite and verified content-addressed store."""

import copy
import hashlib
import importlib.metadata
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from marketpulse.infrastructure.storage.models import BlobRef

from ..contracts import digest
from ..live.store import ResearchRunRow
from .contracts import EvidenceBundle
from .evidence import verify_document, verify_excerpt


def fingerprint():
    package = Path(__file__).parent
    root = package.parents[3]
    paths = sorted([*package.rglob("*.py"), *package.rglob("*.json")])
    paths.extend(
        package.parent / "live" / name for name in ("securities.py", "sources.py", "store.py")
    )
    paths.extend(
        p
        for name in ("uv.lock", "poetry.lock", "pyproject.toml", "requirements.txt")
        if (p := root / name).is_file()
    )
    return digest(
        dict(
            files={
                str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
            },
            dependencies={
                n: importlib.metadata.version(n)
                for n in ("pydantic", "pypdf", "requests", "SQLAlchemy")
            },
        )
    )


class DecisionStore:
    def __init__(self, research_store):
        self.base = research_store

    def blob(self, value):
        return self.base.blob(value)

    def raw(self, value):
        return self.base.raw(value)

    def value(self, ref):
        return self.base.value(ref)

    def raw_bytes(self, ref):
        return self.base.blobs.get_bytes(BlobRef.from_uri(ref))

    def exists(self, run_id):
        return self.base.exists(run_id)

    def create(self, request, implementation):
        run_id = "RUN-DEC-" + UUID(request["request_key"]).hex
        state = dict(
            schema_version="decision-v1",
            run_id=run_id,
            request=request,
            status="RUNNING",
            scenario="decision",
            title=request["entity"] + " · 定向取证",
            created_at=datetime.now(UTC).isoformat(),
            implementation=implementation,
            parent_run_id=request.get("parent_run_id"),
            process=[],
            calls=[],
            tool_results={},
            model_calls=0,
            tokens=0,
        )
        try:
            with self.base.sessions.begin() as session:
                session.add(
                    ResearchRunRow(
                        run_id=run_id,
                        status=state["status"],
                        created_at=state["created_at"],
                        payload=state,
                    )
                )
            return state, True
        except IntegrityError:
            old = self.get(run_id)
            if old["request"] != request:
                raise ValueError("request identity conflict") from None
            return old, False

    def save(self, state):
        payload = copy.deepcopy(state)
        payload.pop("data", None)
        with self.base.sessions.begin() as session:
            old = session.get(ResearchRunRow, state["run_id"])
            if old is None or old.payload["request"] != payload["request"]:
                raise ValueError("decision request identity conflict")
            if old.status == "COMPLETED" and old.payload == payload:
                return
            changed = session.execute(
                update(ResearchRunRow)
                .where(
                    ResearchRunRow.run_id == state["run_id"],
                    ResearchRunRow.status.not_in(["COMPLETED", "CANCELLED"]),
                )
                .values(payload=payload, status=payload["status"])
            )
            if changed.rowcount != 1:
                raise ValueError("completed/cancelled decision cannot be overwritten")

    def cancel(self, run_id):
        state = self.get(run_id, with_data=False)
        if state["status"] not in {"RUNNING", "PENDING"}:
            raise ValueError("该记录当前不能停止")
        state.update(status="CANCELLED", error="用户停止取证；已取得的资料与请求记录保留。")
        for call in state["calls"]:
            if call["status"] == "RUNNING":
                call["status"] = "CANCELLED"
        self.save(state)
        return state

    def get(self, run_id, *, with_data=True):
        if not self.exists(run_id):
            raise KeyError(run_id)
        state = self.base.get(run_id, with_data=with_data)
        if with_data and state.get("result_ref"):
            if BlobRef.from_uri(state["result_ref"]).sha256 != state.get("result_sha256"):
                raise ValueError("decision result hash mismatch")
            data = state["data"]
            self.verify_data(
                data, state["result_sha256"], state.get("subject", {}).get("security_id")
            )
            if data.get("report", {}).get("stage") == "P5" and data["role_records"] != [
                c for c in state["calls"] if c.get("role") and c["status"] == "COMPLETED"
            ]:
                raise ValueError("candidate report differs from actual task roles")
            if data.get("report", {}).get("stage") == "P6_MARKET" and (
                data["sources"] != [c["source"] for c in state["calls"] if c.get("source")]
                or state.get("model_calls", 0) != 0
            ):
                raise ValueError("market report differs from actual source acquisitions")
            if data.get("report", {}).get("stage") in {"P2", "P3", "P4", "P4K"}:
                from .forecasts import verify_binding

                verify_binding(self, state)
        return state

    def verify_data(self, data, result_hash, subject_id=None):
        if digest(data) != result_hash:
            raise ValueError("decision result content mismatch")
        if "report" in data and not isinstance(data["report"], dict):
            raise ValueError("decision report must be an object")
        if data.get("report", {}).get("stage") == "P6_MARKET":
            from .market_scan import verify

            verify(self, data)
            return
        if data.get("report", {}).get("stage") == "P5":
            from .screening import verify

            verify(self, data)
            return
        bundle = EvidenceBundle.model_validate(data["bundle"])
        if data["evidence"] != [e.model_dump(mode="json") for e in bundle.items]:
            raise ValueError("duplicated evidence views disagree")
        documents = data["documents"]
        for doc in documents:
            if BlobRef.from_uri(doc["raw_ref"]).sha256 != doc["content_hash"]:
                raise ValueError("document original hash mismatch")
            if subject_id and doc["subject_id"] != subject_id:
                raise ValueError("document subject mismatch")
            self.raw_bytes(doc["raw_ref"])
            verify_document(doc, self.value(doc["text_ref"]))
        industry_documents = data.get("industry_documents", [])
        for doc in industry_documents:
            if (
                doc["subject_id"] != "688981.SH"
                or BlobRef.from_uri(doc["raw_ref"]).sha256 != doc["content_hash"]
            ):
                raise ValueError("demand-side document identity/hash mismatch")
            self.raw_bytes(doc["raw_ref"])
            verify_document(doc, self.value(doc["text_ref"]))
        allowed = {s["blob_ref"]: s["sha256"] for s in data["sources"]}
        for ref, hash_value in allowed.items():
            if BlobRef.from_uri(ref).sha256 != hash_value:
                raise ValueError("source hash mismatch")
            self.raw_bytes(ref)
        for item in bundle.items:
            if (
                allowed.get(item.raw_ref) != item.content_hash
                or subject_id
                and item.subject_id != subject_id
            ):
                raise ValueError("evidence raw source/subject not in run")
            if item.locator.kind == "pdf":
                verify_excerpt(item, documents + industry_documents, self.value)
            elif (
                item.locator.kind == "text"
                and item.source_family in {"POLICY_CN", "POLICY_US"}
                and data.get("policy_snapshot")
            ):
                from .policy_sources import verify_item

                verify_item(self, item)
            elif item.locator.kind == "text" and data.get("industry_snapshot"):
                from .industry_sources import verify_item

                verify_item(self, item)
            elif item.locator.kind == "json":
                value = json.loads(self.raw_bytes(item.raw_ref))
                for part in item.locator.path.strip("/").split("/"):
                    value = (
                        value[int(part)]
                        if isinstance(value, list)
                        else value[part.replace("~1", "/").replace("~0", "~")]
                    )
                from decimal import Decimal

                if item.value is None or Decimal(str(value)) != item.value:
                    raise ValueError("numeric evidence value mismatch")
            else:
                raise ValueError("P1 unsupported evidence locator")
        if data.get("report", {}).get("stage") in {"P2", "P3", "P4", "P4K"}:
            from .medium import verify_medium

            verify_medium(self, data)

    def list(self):
        return self.base.list(prefix="RUN-DEC-")

    def archived(self, subject_id, cutoff):
        bundles = []
        for state in self.list():
            if (
                state["status"] != "COMPLETED"
                or state.get("subject", {}).get("security_id") != subject_id
            ):
                continue
            data = self.get(state["run_id"])["data"]
            bundles.append(data)
        return bundles
