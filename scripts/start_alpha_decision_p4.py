"""User-authorized real research: public disclosures/market data to configured roles."""
import json
from pathlib import Path
from uuid import uuid4
import httpx

stage=Path('.phase3-quant-o/decision-p4-20261013-01')
payload=dict(request_key=str(uuid4()),entity='688012.SH',question='研究中微公司未来三个月的经营与现金回收，结合行业原始发布、制造端资本开支、产品和地域适配、同窗价格与其他公告，明确权衡后给看多或看空倾向；行业传导保留条件，价格比较不推因果贡献。',research_mode='medium_term',include_flows=True,include_industry=True,horizon_months=3,parent_run_id='RUN-DEC-529672d81d814125aa4459fa7ffd08c1',budget_profile=dict(max_rounds=1,max_evidence_calls=1))
with httpx.Client(base_url='http://127.0.0.1:8839',timeout=60) as client:
    response=client.post('/api/quant/decision/runs',json=payload)
    response.raise_for_status(); state=response.json()
with (stage/('request-'+payload['request_key']+'.json')).open('x',encoding='utf8') as f:
    json.dump(dict(request=payload,initial_state=state),f,ensure_ascii=False,indent=2)
print(state['run_id'],state['status'])
