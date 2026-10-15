"""Replay the exact first-role reservation using sealed real bytes; never publish output."""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.contracts import canonical
from marketpulse.quant.decision import agents, flow_sources, industry_sources, knowledge, medium, policy_sources, routing
sys.stdout.reconfigure(encoding='utf8')
stage=Path('.phase3-quant-o/decision-p6-real-01')
state=json.loads((stage/'single-real-03/state.json').read_text(encoding='utf8'))
blobs=LocalContentAddressedBlobStorage(Path('.phase3-quant-o/alpha-history-real-02/blobs'))
memory={}
def raw(ref):return memory[ref] if ref in memory else blobs.get_bytes(BlobRef.from_uri(ref))
def blob(v):
 b=canonical(v).encode();ref='blob://sha256/'+hashlib.sha256(b).hexdigest();memory[ref]=b;return ref
repo=SimpleNamespace(raw_bytes=raw,blob=blob,value=lambda r:json.loads(raw(r)))
data=repo.value(state['evidence_ref']);data['observation_calendar']=repo.value(state['observation_calendar_ref'])
data=flow_sources.attach(repo,data,repo.value(state['flow_snapshot_ref']));data['flow_protocol']=agents.FLOW_PROTOCOL
medium.initial_evidence(data,repo)
data=industry_sources.attach(repo,data,repo.value(state['industry_snapshot_ref']))
data=knowledge.attach(repo,data,repo.value(state['knowledge_snapshot_ref']))
data=policy_sources.attach(repo,data,repo.value(state['policy_snapshot_ref']))
data=routing.prepare(repo,data,state['subject'],3);data=routing.select(data,state['plan'])
base=medium.initial_evidence(data,repo)+[e for e in data['bundle']['items'] if e['source_family']=='DECISION_FLOW_CALC' and e['evidence_id'].split('-')[-1] in {'MARGIN_CHANGE','SECURITY_RETURN','VENDOR_NET'}]
industry=medium.industry_initial_evidence(data,state['subject']['security_id'])
policies=[e for e in data['bundle']['items'] if e['source_family'] in {'POLICY_CN','POLICY_US'} and e['locator']['path'].split(':')[-1] in {'body','advanced','equipment'}]
def build(chosen):
 evidence=list({e['evidence_id']:e for e in base+chosen+policies}.values())
 ctx=medium.model_context(state,data,evidence);ctx['focus_questions']=state['plan']['focus_questions']
 reserve=len((canonical(ctx)+agents.ROUTING_PROMPTS['Analyst']+canonical(agents.RoutedAnalysis.model_json_schema())).encode())+3000
 return reserve,[e['evidence_id'] for e in evidence if e['kind']=='disclosure']
before=build(industry)
focused=[e for e in industry if e['kind']!='disclosure' or e['evidence_id'].startswith('EV-IND-DEMAND-')]
after=build(focused)
report=dict(before=before,after=after,limit=40000)
(stage/'first-input-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(report)
