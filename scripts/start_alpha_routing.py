"""Authorized new real research; public issuer/policy/method data to existing model roles."""

import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx

stage = Path('.phase3-quant-o/decision-routing-20261013-01')
payload = dict(request_key=str(uuid4()), entity='688012.SH',
               question='根据中微公司的实际设备业务选择适用研究方法，核查未来三个月经营与现金回收、行业和资金分歧，并结合本次官方政策条款与资格限制，权衡后明确给看多或看空及反证和改变判断的条件。',
               horizon_months=3, research_mode='medium_term', include_flows=True, include_industry=True, include_methods=True,
               parent_run_id='RUN-DEC-d259103fe2f64d84a4e874dcecefbad2', budget_profile=dict(max_rounds=1, max_evidence_calls=1))
with httpx.Client(base_url='http://127.0.0.1:8839', timeout=60) as client:
    response = client.post('/api/quant/decision/runs', json=payload)
    response.raise_for_status()
    state = response.json()
with (stage / ('request-' + payload['request_key'] + '.json')).open('x', encoding='utf8') as handle:
    json.dump(dict(request=payload, initial_state=state), handle, ensure_ascii=False, indent=2)
sys.stdout.reconfigure(encoding='utf8')
print(state['run_id'], state['status'])
