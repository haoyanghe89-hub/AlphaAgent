"""Real P6 requests, version preservation, manual observations and failure evidence."""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from uuid import uuid4

import requests

sys.stdout.reconfigure(encoding='utf8')
stage=Path('.phase3-quant-o/decision-p6-real-01')
base='http://127.0.0.1:8839/api/quant/decision'
parser=argparse.ArgumentParser()
parser.add_argument('mode',choices=['audit','market','manufacturing','single','refresh','compatibility'])
parser.add_argument('--attempt',default='01')
args=parser.parse_args()

def get(path):
    r=requests.get(base+path,timeout=120)
    r.raise_for_status()
    return r.json()

def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')

if args.mode=='audit':
    rows=get('/runs')
    write(stage/'pre-restart-runs.json',rows)
    print('active_decision',[(r['run_id'],r['status']) for r in rows if r['status'] in ('RUNNING','PENDING')])
    old=requests.get('http://127.0.0.1:8839/api/quant/research/runs',timeout=30).json()
    print('active_legacy',[(r['run_id'],r['status']) for r in old if r['status'] in ('RUNNING','PENDING')])
    for name,rid in [('p5','RUN-DEC-cfcc09838d704db5b3f964d69bc7f07c'),('p4k','RUN-DEC-9679b9a1bc81435d95cf42a7e6e7febe')]:
        value=get('/runs/'+rid)
        write(stage/('before-'+name+'.json'),value)
        print(name,value['status'])
    raise SystemExit()
if args.mode=='compatibility':
    baseline=json.loads((stage/'baseline.json').read_text())
    dbpath=Path('.phase3-quant-o/alpha-history-real-02/metadata.sqlite').resolve()
    with sqlite3.connect('file:'+dbpath.as_posix()+'?mode=ro',uri=True) as db:
        current={r[0]:hashlib.sha256(r[1].encode()).hexdigest() for r in db.execute("select run_id,payload from inv_quant_research_run where status='COMPLETED'")}
    checks={rid:current.get(rid)==value for rid,value in baseline.items()}
    for name in ('p5','p4k'):
        before=json.loads((stage/('before-'+name+'.json')).read_text(encoding='utf8'))
        checks[name+'-api']=before==get('/runs/'+before['run_id'])
    write(stage/'compatibility.json',checks)
    print('unchanged',sum(checks.values()),'/',len(checks))
    assert all(checks.values())
    raise SystemExit()

folder=stage/(args.mode+'-real-'+args.attempt)
folder.mkdir(exist_ok=True)
keyfile=folder/'request.json'
if keyfile.exists():
    payload=json.loads(keyfile.read_text(encoding='utf8'))
else:
    payload=dict(request_key=str(uuid4()))
    if args.mode=='market':
        payload.update(scope='market',research_mode='market_scan',entity='设备与制造观察范围',question='在明确覆盖范围内比较行业成交关注、上涨广度和集中度，再选择研究方向。')
    elif args.mode=='manufacturing':
        payload.update(scope='industry',research_mode='candidates',entity='集成电路制造',question='结合晶圆需求、产能利用率、工艺结构与资本开支分析制造企业。缺项保留，不套设备订单验收逻辑，明确中期方向与反证。')
    elif args.mode=='single':
        payload.update(entity='688981.SH',research_mode='medium_term',include_flows=True,include_industry=True,include_methods=True,question='研究中芯国际未来几个月，按晶圆制造与代工业务选择方法，核查经营、利用率、资本开支、政策及现金回收。明确倾向看多或看空，比较反证并说明验证条件。',budget_profile=dict(max_rounds=1,max_evidence_calls=1))
    write(keyfile,payload)
path='/runs/RUN-DEC-9679b9a1bc81435d95cf42a7e6e7febe/refresh' if args.mode=='refresh' else '/runs'
r=requests.post(base+path,json=payload,timeout=120)
if r.status_code!=202:
    write(folder/'create-error.json',dict(status=r.status_code,body=r.text))
    r.raise_for_status()
state=r.json()
rid=state['run_id']
write(folder/'created.json',state)
print(args.mode,rid,flush=True)
started=time.monotonic()
while state['status'] in ('RUNNING','PENDING'):
    if time.monotonic()-started>930:raise TimeoutError('workflow did not terminate')
    time.sleep(2)
    state=get('/runs/'+rid)
    write(folder/'state.json',state)
    print(state['status'],state.get('process',[])[-1:],flush=True)
print('terminal',state['status'],state.get('error'),state.get('model_calls'),state.get('tokens'),flush=True)
if state['status']=='COMPLETED':
    export=requests.get(base+'/runs/'+rid+'/export',timeout=120)
    export.raise_for_status()
    assert export.json()==state
    write(folder/'export.json',export.json())
    write(folder/'comparison.json',get('/runs/'+rid+'/comparison'))
    if state.get('forecast_id'):
        response=requests.post(base+'/runs/'+rid+'/observe',timeout=120)
        response.raise_for_status()
        write(folder/'observed.json',response.json())
        write(folder/'outcomes.json',get('/runs/'+rid+'/outcomes'))
