"""Real-source preflight in separate CAS; no model call or published forecast."""

import json
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.contracts import canonical
from marketpulse.quant.decision import candidate_sources, screening

sys.stdout.reconfigure(encoding="utf8")
stage = Path(".phase3-quant-o/decision-p5-real-01")
blobs = LocalContentAddressedBlobStorage(stage / "probe-blobs")
repo = SimpleNamespace(
    raw=lambda raw: blobs.put_bytes(raw).ref.uri,
    blob=lambda data: blobs.put_bytes(canonical(data).encode()).ref.uri,
    raw_bytes=lambda ref: blobs.get_bytes(BlobRef.from_uri(ref)),
    value=lambda ref: json.loads(blobs.get_bytes(BlobRef.from_uri(ref))),
)
state = dict(request=dict(budget_profile=dict(max_candidates=3, max_seconds=900)), calls=[])


def save():
    (stage / "probe-state.json").write_text(canonical(state), encoding="utf8")


snapshot = candidate_sources.collect(repo, state, Event(), save, lambda key, detail: print(detail, flush=True))
(stage / "probe-snapshot.json").write_text(canonical(snapshot), encoding="utf8")
data = candidate_sources.rebuild(repo, snapshot)
(stage / "probe-data.json").write_text(canonical(data), encoding="utf8")
ctx = screening.context(data, 3)
print(dict(source_calls=len(state["calls"]), selected=data["selected"], coverage=data["coverage"],
           context_bytes=len(canonical(ctx).encode())), flush=True)
print([(r["name"], r["core_complete"], r["liquidity_ready"], r["business_verified"], r["gaps"]) for r in data["members"][:4]], flush=True)
