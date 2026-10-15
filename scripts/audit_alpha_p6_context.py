"""Measure preserved real continuation input, without model calls or DB changes."""
import copy
import json
from pathlib import Path
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.contracts import canonical
from marketpulse.quant.decision import agents
stage=Path('.phase3-quant-o/decision-p6-real-01')
state=json.loads((stage/'single-real-02/state.json').read_text(encoding='utf8'))
blobs=LocalContentAddressedBlobStorage(Path('.phase3-quant-o/alpha-history-real-02/blobs'))
value=lambda ref:json.loads(blobs.get_bytes(BlobRef.from_uri(ref)))
call=next(c for c in state['calls'] if c.get('role')=='Analyst' and c['status']=='COMPLETED')
context=value(call['request_ref'])['context']
docs={d['raw_ref']:d for d in value(state['evidence_ref'])['documents']}
original=copy.deepcopy(context)
for e in state['rounds'][0]['new_evidence']:
 original['evidence'].append({k:e[k] for k in ('evidence_id','text','kind','source_family','value','unit','period_start','period_end','original_source_id')}|dict(title=docs[e['raw_ref']]['title']))
original.update(previous_review=state['rounds'][0]['review'],previous_claims=state['rounds'][0]['proposal']['claims'],supplements_open=False)
focused=copy.deepcopy(original)
ids={e['evidence_id'] for e in state['rounds'][0]['new_evidence']}
for e in focused['evidence']:
 if e['evidence_id'] in ids:
  text=e['text']; positions=[text.find(term) for term in ('产能利用率','资本开支','折旧') if term in text]
  start=max(0,positions[0]-70) if positions else 0
  e['text']=text[start:start+400]
reserve=lambda c:len((canonical(c)+agents.ROUTING_PROMPTS['Analyst']+canonical(agents.RoutedAnalysis.model_json_schema())).encode())+3000
report=dict(before=reserve(original),after=reserve(focused),limit=40000,company_originals_before=sum(e['kind']=='disclosure' for e in original['evidence']),company_originals_after=sum(e['kind']=='disclosure' for e in focused['evidence']))
(stage/'context-budget-audit.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print(report)
