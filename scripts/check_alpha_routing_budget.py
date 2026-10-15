"""Read-only replay of input reservation on a preserved real Analyst response."""

import json
import sqlite3
import sys
from pathlib import Path

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.contracts import canonical
from marketpulse.quant.decision import agents, medium

root = Path(".phase3-quant-o/alpha-history-real-02")
blobs = LocalContentAddressedBlobStorage(root / "blobs")


def value(ref):
    return json.loads(blobs.get_bytes(BlobRef.from_uri(ref)))


with sqlite3.connect(
    "file:" + (root / "metadata.sqlite").resolve().as_posix() + "?mode=ro", uri=True
) as db:
    state = json.loads(
        db.execute(
            "select payload from inv_quant_research_run where run_id=?", (sys.argv[1],)
        ).fetchone()[0]
    )
call = next(
    c for c in state["calls"] if c.get("role") == "Analyst" and c["status"] == "COMPLETED"
)
proposal = agents.RoutedAnalysis.model_validate(value(call["response_ref"]))
original = value(call["request_ref"])["context"]
context = medium.review_input(original, proposal)
original_company = {e["evidence_id"] for e in original["evidence"] if e["kind"] == "disclosure"}
review_company = {e["evidence_id"] for e in context["evidence"] if e["kind"] == "disclosure"}
assert original_company == review_company
reserve = len(
    (
        canonical(context)
        + agents.ROUTING_PROMPTS["Verifier"]
        + canonical(agents.RoutedReview.model_json_schema())
    ).encode()
) + 3000
print(json.dumps(dict(run_id=state["run_id"], reserve=reserve, limit=40000,
                      company_originals_preserved=len(review_company))))
assert reserve <= 40000
