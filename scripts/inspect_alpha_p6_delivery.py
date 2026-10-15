"""Read and archive real delivery records; no model calls, no result rewrites."""
import json
import sys
from pathlib import Path
import requests

sys.stdout.reconfigure(encoding='utf8')
stage=Path('.phase3-quant-o/decision-p6-real-01')
base='http://127.0.0.1:8839/api/quant/decision/runs/'
records={
 'market':'RUN-DEC-d26ccc728bcf4024a8912c3708428bb2',
 'manufacturing':'RUN-DEC-00eeb4f527e242f986480a5662607542',
 'refresh':'RUN-DEC-74c0bc90522a4d71828c877b8f6be53b',
}
summary={}
def get(url):
 r=requests.get(url,timeout=120);r.raise_for_status();return r.json()
def write(name,value):
 (stage/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
for name,rid in records.items():
 s=get(base+rid); assert s['status']=='COMPLETED'
 export=get(base+rid+'/export');assert s==export
 write('delivery-'+name+'.json',export)
 d=s['data']
 summary[name]=dict(run_id=rid,source_calls=sum(bool(c.get('tool')) for c in s['calls']),source_failures=sum(c.get('tool') is not None and c['status']=='FAILED' for c in s['calls']),model_calls=s.get('model_calls'),tokens=s.get('tokens'),elapsed=s.get('elapsed_seconds'),summary=d['report']['summary'])
 if s.get('forecast_id'):
  chain=get(base+rid+'/forecast');write(name+'-chain.json',chain)
  outcomes=get(base+rid+'/outcomes');write(name+'-outcomes.json',outcomes)
  summary[name].update(template=d['industry_report']['template'],primary_method=d['method_routing']['primary_method_id'],forecast_count=len(chain['versions']),observations=len(outcomes['observations']))
summary['comparison']=get(base+records['refresh']+'/comparison')
write('acceptance-summary.json',summary)
print({name:{k:v for k,v in value.items() if k!='summary'} for name,value in summary.items() if name!='comparison'})
