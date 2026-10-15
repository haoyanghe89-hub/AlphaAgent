"""Real cancellation and bounded resume checks; model budget forbids model requests."""
import json
import time
from pathlib import Path
from uuid import uuid4
import requests

stage=Path('.phase3-quant-o/decision-p6-real-01')
base='http://127.0.0.1:8839/api/quant/decision/runs'
def read(rid):
 r=requests.get(base+'/'+rid,timeout=30);r.raise_for_status();return r.json()
def create(payload):
 for _ in range(30):
  r=requests.post(base,json=payload,timeout=30)
  if r.status_code!=429:r.raise_for_status();return r.json()
  time.sleep(1)
 raise TimeoutError('task lease did not clear')
def terminal(rid):
 for _ in range(100):
  s=read(rid)
  if s['status'] not in ('PENDING','RUNNING'):return s
  time.sleep(1)
 raise TimeoutError('diagnostic did not terminate')

cancelled=create(dict(request_key=str(uuid4()),scope='market',research_mode='market_scan',entity='设备与制造观察范围',question='开发自测：协作式取消，不发布市场意见',budget_profile=dict(max_seconds=30,max_tokens=1)))
rid=cancelled['run_id']
r=requests.post(base+'/'+rid+'/cancel',timeout=30);r.raise_for_status()
assert r.json()['status']=='CANCELLED'
failed=create(dict(request_key=str(uuid4()),scope='industry',research_mode='candidates',entity='集成电路制造',question='开发自测：来源失败留痕与恢复；不发起付费模型请求',budget_profile=dict(max_seconds=90,max_tokens=1,max_model_calls=1,max_candidates=0)))
before=terminal(failed['run_id'])
assert before['status']=='FAILED' and before['model_calls']==0
r=requests.post(base+'/'+failed['run_id']+'/resume',timeout=30)
if r.status_code==202:
 after=terminal(failed['run_id'])
 assert after['status']=='FAILED' and after['model_calls']==0
else:
 assert r.status_code==409
 after=read(failed['run_id'])
late=read(rid)
assert late['status']=='CANCELLED' and not late.get('result_ref')
result=dict(cancelled=late,resume_before=before,resume_after=after,resume_http_status=r.status_code,notes='模型预算仅一token，未调用模型；源端仍不可用，恢复尝试有留痕，未冒充成功恢复。')
(stage/'lifecycle-real.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print(dict(cancelled=rid,cancel_status=late['status'],resume=failed['run_id'],resume_http=r.status_code,before_calls=len(before['calls']),after_calls=len(after['calls']),model_calls=after['model_calls']))
