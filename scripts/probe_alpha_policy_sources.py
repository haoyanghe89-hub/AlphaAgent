"""Live official transport/parser/CAS probe; no company qualification is evaluated."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from threading import Event

from marketpulse.quant.decision import policy_sources
from marketpulse.quant.decision.contracts import EvidenceItem
from marketpulse.quant.decision.tools import SourceClient


class ProbeRepository:
    def __init__(self):
        self.values = {}

    def raw(self, raw):
        ref = "blob://sha256/" + hashlib.sha256(raw).hexdigest()
        self.values[ref] = raw
        return ref

    def blob(self, value):
        return self.raw(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())

    def raw_bytes(self, ref):
        raw = self.values[ref]
        if hashlib.sha256(raw).hexdigest() != ref.rsplit("/", 1)[-1]:
            raise ValueError("probe CAS integrity mismatch")
        return raw

    def value(self, ref):
        return json.loads(self.raw_bytes(ref))


def main():
    repo = ProbeRepository()
    state = dict(request=dict(budget_profile=dict(max_seconds=120)), calls=[])
    client = SourceClient(repo, state, lambda: None, Event(), max_requests=6)
    # A declared sector selects endpoints only; it is not a company exposure assertion.
    subject = dict(security_id="POLICY-PROBE", industry="半导体", description="")
    snapshot = policy_sources.collect(repo, client, subject)
    data = dict(
        sources=[],
        evidence=[],
        documents=[],
        bundle=dict(
            items=[],
            sources=[],
            cutoff_at=datetime.now(UTC).isoformat(),
            coverage=dict(
                universe_total=0,
                attempted=0,
                available=0,
                missing=0,
                excluded=0,
                universe_ref="official-policy-transport-probe",
                member_refs=[],
                source_dates=[],
                method="transport/parser probe only",
                status="COMPLETE",
            ),
        ),
    )
    data = policy_sources.attach(repo, data, snapshot)
    policy_sources.verify(repo, data)
    for item in data["evidence"]:
        policy_sources.verify_item(repo, EvidenceItem.model_validate(item))
    output = dict(
        probe_only=True,
        requests=len(state["calls"]),
        status=data["policy_report"]["status"],
        policies=data["policy_report"]["policies"],
        originals=[
            dict(
                url=e["source"]["url"],
                raw_ref=e["source"]["blob_ref"],
                sha256=e["source"]["sha256"],
                fetched_at=e["source"]["fetched_at"],
                byte_count=e["source"].get("byte_count"),
            )
            for e in snapshot["entries"]
        ],
        locators=[
            dict(evidence_id=e["evidence_id"], locator=e["locator"], length=len(e["text"]))
            for e in data["evidence"]
        ],
        gaps=data["policy_report"]["gaps"],
    )
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
