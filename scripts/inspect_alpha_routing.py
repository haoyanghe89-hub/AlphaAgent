"""Public-source, read-only prompt budget preflight; never creates a forecast."""

import copy
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef, BlobNotFoundError
from marketpulse.quant.contracts import canonical
from marketpulse.quant.decision import agents, knowledge, medium, policy_sources, routing
from marketpulse.quant.decision.tools import SourceClient

stage = Path('.phase3-quant-o/decision-routing-20261013-01')
original = LocalContentAddressedBlobStorage(Path('.phase3-quant-o/alpha-history-real-02/blobs'))
probe = LocalContentAddressedBlobStorage(stage / 'probe-blobs')


def raw_bytes(ref):
    try:
        return probe.get_bytes(BlobRef.from_uri(ref))
    except BlobNotFoundError:
        return original.get_bytes(BlobRef.from_uri(ref))


repo = SimpleNamespace(raw=lambda b: probe.put_bytes(b).ref.uri,
                       blob=lambda v: probe.put_bytes(canonical(v).encode()).ref.uri,
                       raw_bytes=raw_bytes, value=lambda r: json.loads(raw_bytes(r)))
with sqlite3.connect('file:' + Path('.phase3-quant-o/alpha-history-real-02/metadata.sqlite').resolve().as_posix() + '?mode=ro', uri=True) as db:
    old = json.loads(db.execute('select payload from inv_quant_research_run where run_id=?', ('RUN-DEC-d259103fe2f64d84a4e874dcecefbad2',)).fetchone()[0])
data = copy.deepcopy(repo.value(old['result_ref']))
state = dict(request=dict(budget_profile=dict(max_seconds=180)), calls=[])
client = SourceClient(repo, state, lambda: None, Event(), max_requests=8)
cache = stage / 'preflight-sources.json'
if cache.exists():
    packet = json.loads(cache.read_text(encoding='utf8'))
    kn, pol = packet['knowledge'], packet['policy']
else:
    kn = knowledge.collect(repo, client, old['subject'])
    pol = policy_sources.collect(repo, client, old['subject'])
    cache.write_text(json.dumps(dict(knowledge=kn, policy=pol), ensure_ascii=False), encoding='utf8')
# Diagnostic only: old immutable financial inputs remain untouched in their DB.
data['bundle']['cutoff_at'] = datetime.now(UTC).isoformat()
data = knowledge.attach(repo, data, kn)
data = policy_sources.attach(repo, data, pol)
data = routing.prepare(repo, data, old['subject'], 3)
primary = routing.allowed_primary(data)[0]
plan = dict(focus_questions=['核查经营与反证'], primary_method_id=primary,
            auxiliary_method_ids=[c['method_id'] for c in data['method_routing']['candidates'] if c['status'] in routing.ACTIVE and c['method_id'] != primary], selection_reason='诊断输入大小，不生成投资结论。')
data = routing.select(data, plan)
ids = {e['evidence_id'] for e in repo.value(data['analysis_context_ref'])['evidence']}
evidence = [e for e in data['bundle']['items'] if e['evidence_id'] in ids or e['source_family'] in {'POLICY_CN', 'POLICY_US'}]
evidence = [e for e in evidence if not (e['source_family'] in {'POLICY_CN','POLICY_US'} and e['locator']['path'].split(':')[-1] not in {'body','advanced','equipment'})]
context = medium.model_context(old, data, evidence)
output = dict(diagnostic_only=True, source_calls=len(state['calls']), role=data['method_routing']['profile']['primary_role'], primary=primary,
              knowledge=[dict(note_id=n['note_id'], raw_ref=n['raw_ref'], source_hash=n['source_hash'], coverage=n['coverage']) for n in data['knowledge_notes']], knowledge_gaps=kn['gaps'],
              policy_count=len(data['policy_report']['policies']), policy_gaps=data['policy_report']['gaps'],
              components={k: len(canonical(v).encode()) for k, v in context.items()},
              analyst_reserve=len((canonical(context)+agents.ROUTING_PROMPTS['Analyst']+canonical(agents.RoutedAnalysis.model_json_schema())).encode())+3000)
(stage / 'preflight-01.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf8')
print(json.dumps(output, ensure_ascii=False, indent=2))
