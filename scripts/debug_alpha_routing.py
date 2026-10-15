"""Rebuild a failed public-input snapshot offline; never mutate the research DB."""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.contracts import canonical
from marketpulse.quant.decision import agents, flow_sources, industry_sources, knowledge, medium, policy_sources, routing

sys.stdout.reconfigure(encoding='utf8')
root = Path('.phase3-quant-o/alpha-history-real-02')
blobs = LocalContentAddressedBlobStorage(root / 'blobs')
memory = {}


def raw_bytes(ref):
    return memory[ref] if ref in memory else blobs.get_bytes(BlobRef.from_uri(ref))


def blob(value):
    raw = canonical(value).encode()
    ref = 'blob://sha256/' + hashlib.sha256(raw).hexdigest()
    memory[ref] = raw
    return ref


repo = SimpleNamespace(blob=blob, raw_bytes=raw_bytes, value=lambda r: json.loads(raw_bytes(r)))
with sqlite3.connect('file:' + (root / 'metadata.sqlite').resolve().as_posix() + '?mode=ro', uri=True) as db:
    state = json.loads(db.execute('select payload from inv_quant_research_run where run_id=?', (sys.argv[1],)).fetchone()[0])
data = repo.value(state['evidence_ref'])
data['observation_calendar'] = repo.value(state['observation_calendar_ref'])
data = flow_sources.attach(repo, data, repo.value(state['flow_snapshot_ref']))
data['flow_protocol'] = agents.FLOW_PROTOCOL
medium.initial_evidence(data, repo)
data = industry_sources.attach(repo, data, repo.value(state['industry_snapshot_ref']))
data = knowledge.attach(repo, data, repo.value(state['knowledge_snapshot_ref']))
data = policy_sources.attach(repo, data, repo.value(state['policy_snapshot_ref']))
data = routing.prepare(repo, data, state['subject'], 3)
print('role', data['method_routing']['profile']['primary_role'], 'primary', routing.allowed_primary(data))
print('knowledge', len(data['knowledge_notes']), 'policy', len(data['policy_report']['policies']))
