"""Read-only inspection/reconstruction of public P4 role input sizes."""
import copy,json,sqlite3,hashlib
from pathlib import Path
from types import SimpleNamespace
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.contracts import canonical,digest
from marketpulse.quant.decision import agents,flow_sources,industry_sources,medium

root=Path('.phase3-quant-o/alpha-history-real-02').resolve()
blobs=LocalContentAddressedBlobStorage(root/'blobs')
memory={}
def raw_bytes(ref):
    return memory[ref] if ref in memory else blobs.get_bytes(BlobRef.from_uri(ref))
def blob(value):
    raw=canonical(value).encode(); ref='blob://sha256/'+hashlib.sha256(raw).hexdigest(); memory[ref]=raw; return ref
repo=SimpleNamespace(raw_bytes=raw_bytes,value=lambda ref:json.loads(raw_bytes(ref)),blob=blob)
with sqlite3.connect('file:'+(root/'metadata.sqlite').as_posix()+'?mode=ro',uri=True) as db:
    state=json.loads(db.execute('select payload from inv_quant_research_run where run_id=?',('RUN-DEC-beef28ce446849a09676c76b452265aa',)).fetchone()[0])
data=copy.deepcopy(repo.value(state['evidence_ref']))
data['observation_calendar']=repo.value(state['observation_calendar_ref'])
data=flow_sources.attach(repo,data,repo.value(state['flow_snapshot_ref']))
data['flow_protocol']=agents.FLOW_PROTOCOL
data=industry_sources.attach(repo,data,repo.value(state['industry_snapshot_ref']))
evidence=medium.initial_evidence(data,repo)
evidence += [e for e in data['bundle']['items'] if e['source_family']=='DECISION_FLOW_CALC' and e['evidence_id'].split('-')[-1] in {'MARGIN_CHANGE','SECURITY_RETURN','VENDOR_NET'}]
exposures=set(data['industry_report']['target_exposure_refs'][:1])
evidence += [e for e in data['bundle']['items'] if e['kind']=='industry' and not e['evidence_id'].startswith(('EV-EVENT','EV-ATTR')) or e['evidence_id'] in exposures]
evidence=list({e['evidence_id']:e for e in evidence}.values())
context=medium.model_context(state,data,evidence)
print('context bytes',len(canonical(context).encode()),'evidence',len(evidence))
print('components',[(k,len(canonical(v).encode())) for k,v in context.items()])
print('reserve',len((canonical(context)+agents.INDUSTRY_PROMPTS['Analyst']+canonical(agents.IndustryAnalysis.model_json_schema())).encode())+3500)
print('template',data['industry_report']['template_status'],'gaps',data['industry_report']['gaps'])
print('manufacturer',[(e['evidence_id'],e['text'][:400]) for e in evidence if 'DEMAND' in e['evidence_id']])
print('events',len(data['attribution_report']['timeline']),'windows',[(w['window'],w['security_return'],w['market_return'],w['proxy_return']) for w in data['attribution_report']['windows']])
