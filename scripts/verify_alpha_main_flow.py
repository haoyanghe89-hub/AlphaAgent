"""Read-only real API/source acceptance; never creates research or model calls."""
import hashlib
import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

import requests

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.decision.tushare import records

stage = Path('.phase3-quant-o/main-flow-20261015-01')
root = Path('.phase3-quant-o/alpha-history-real-02')
blobs = LocalContentAddressedBlobStorage(root/'blobs')
base = 'http://127.0.0.1:8839/api/quant/research/main-flow'
daily_response=requests.get(base,timeout=120);daily_response.raise_for_status();daily=daily_response.json()
assert daily['asof']==daily['session'] and daily['granularity']=='daily'
assert daily['coverage']==dict(industries=31,stocks=5226)
source=next(s for s in daily['sources'] if s['api']=='moneyflow')
raw=blobs.get_bytes(BlobRef.from_uri(source['blob_ref']))
assert hashlib.sha256(raw).hexdigest()==source['sha256']
table={r['ts_code']:r for r in records(json.loads(raw))}
for stock in daily['stocks']:
    sid=stock['code']+('.SH' if stock['symbol'].startswith('sh') else '.SZ')
    row=table[sid]
    expected=(Decimal(str(row['buy_lg_amount']))+Decimal(str(row['buy_elg_amount']))-Decimal(str(row['sell_lg_amount']))-Decimal(str(row['sell_elg_amount'])))*10000
    assert abs(Decimal(str(stock['main']))-expected)<Decimal('.01')
for group in daily['industries']:
    members=[s for s in daily['stocks'] if s['industry']==group['name']]
    assert len(members)==group['count']
    assert abs(sum(s['main'] for s in members)-group['main'])<.01
observations=[]
for cycle in range(3):
    if cycle:time.sleep(31)
    for symbol in ['sh600519','sz300750','sz000333']:
        response=requests.get(base+'/'+symbol,timeout=120);response.raise_for_status();value=response.json()
        stage.joinpath(f'accepted-{cycle}-{symbol}.json').write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')
        assert value['symbol']==symbol and value['granularity']=='minute' and len(value['points'])==242
        assert value['refresh_error'] is None
        raw=blobs.get_bytes(BlobRef.from_uri(value['sources'][0]['blob_ref']))
        rows=json.loads(raw)['data']['todayFundTrend']['minList']
        end=rows[-1]
        assert float(end['MainNetInflow'])==value['latest']['main']
        by_time={r['time']:r for r in rows}
        for window in value['windows']:
            target=window['from_at'][:16].replace('-','').replace(':','').replace('T','')
            assert window['main']==float(end['MainNetInflow'])-float(by_time[target]['MainNetInflow'])
        observations.append(dict(cycle=cycle,symbol=symbol,asof=value['asof'],retrieved_at=value['retrieved_at'],main=value['latest']['main']))
        print(json.dumps(observations[-1]),flush=True)
for symbol in ['sh600519','sz300750','sz000333']:
    assert len({o['retrieved_at'] for o in observations if o['symbol']==symbol})>=2
with sqlite3.connect('file:'+(root/'metadata.sqlite').as_posix()+'?mode=ro',uri=True) as db:
    current={r[0]:hashlib.sha256(r[1].encode()).hexdigest() for r in db.execute('select run_id,payload from inv_quant_research_run')}
for name in ['baseline.json','before-final-restart.json']:
    before=json.loads(stage.joinpath(name).read_text())
    assert all(current.get(key)==value for key,value in before.items())
result=dict(status='PASSED',daily_stocks_rebuilt=len(daily['stocks']),industries_rebuilt=len(daily['industries']),observations=observations,original_records_unchanged=len(json.loads(stage.joinpath('baseline.json').read_text())),records_at_last_restart_unchanged=len(json.loads(stage.joinpath('before-final-restart.json').read_text())),current_records=len(current))
stage.joinpath('real-acceptance.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf8')
print(json.dumps({k:v for k,v in result.items() if k!='observations'}),flush=True)
