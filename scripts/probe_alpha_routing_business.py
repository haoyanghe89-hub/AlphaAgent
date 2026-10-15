"""Real manufacturer identity and archived issuer PDF admission probe; no forecast."""

import copy
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from inspect_alpha_routing import original, probe, raw_bytes, repo, stage  # noqa: F401
from marketpulse.quant.decision import routing
from marketpulse.quant.decision.evidence import retrieve
from marketpulse.quant.decision.tools import SourceClient, resolve

with sqlite3.connect('file:' + Path('.phase3-quant-o/alpha-history-real-02/metadata.sqlite').resolve().as_posix() + '?mode=ro', uri=True) as db:
    old = json.loads(db.execute('select payload from inv_quant_research_run where run_id=?', ('RUN-DEC-d259103fe2f64d84a4e874dcecefbad2',)).fetchone()[0])
data = copy.deepcopy(repo.value(old['result_ref']))
doc = data['industry_documents'][0]
state = dict(request=dict(budget_profile=dict(max_seconds=90)), calls=[])
client = SourceClient(repo, state, lambda: None, Event(), max_requests=4)
subject = resolve(client, '688981.SH')
assert doc['subject_id'] == subject['security_id']
now = datetime.now(UTC)
items = retrieve([dict(doc, pages=repo.value(doc['text_ref'])['pages'])], '资本开支 产能利用率 晶圆代工', now, limit=2)
data['bundle']['cutoff_at'] = now.isoformat()
data['bundle']['items'] = [i.model_dump(mode='json') for i in items]
data['documents'] = [doc]
data['sources'] = [s for s in data['sources'] if s.get('key') != 'profile'] + [c['source'] for c in state['calls'] if c['status'] == 'COMPLETED']
data['knowledge_notes'] = []
data = routing.prepare(repo, data, subject, 3)
result = dict(probe_only=True, profile=data['method_routing']['profile'], allowed_primary=routing.allowed_primary(data),
              originals=[dict(evidence_id=i.evidence_id, subject_id=i.subject_id, locator=i.locator.model_dump(), raw_ref=i.raw_ref) for i in items],
              candidates=data['method_routing']['candidates'])
assert result['profile']['primary_role'] == 'MANUFACTURING'
assert result['allowed_primary'] == ['MFG-OPERATIONS']
(stage / 'manufacturer-admission.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
print(json.dumps({'subject':subject['name'], 'role':result['profile']['primary_role'], 'primary':result['allowed_primary'], 'original_count':len(items)},ensure_ascii=False))
