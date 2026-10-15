"""Verify full declared stock scope and provider funding identities before integration."""
import concurrent.futures
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

stage = Path('.phase3-quant-o/main-flow-20261015-01')
classification = json.loads(Path('.phase3-quant-o/alpha-history-real-02/market-live-cache/classification.json').read_text(encoding='utf8'))
symbols = sorted({s for g in classification['groups'] if g['kind'] == 'industry' for s in g['symbols']})
fields = 'f12,f13,f14,f2,f3,f6,f62,f184,f66,f72,f78,f84,f124'

def fetch(index, batch):
    params = dict(fltt=2,invt=2,secids=','.join(('1.' if s.startswith('sh') else '0.')+s[2:] for s in batch),fields=fields,ut='b2884a393a59ad64002292a3e90d46a5')
    for attempt in range(2):
        try:
            r=requests.get('https://push2.eastmoney.com/api/qt/ulist.np/get',params=params,headers={'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/'},timeout=(5,15))
            r.raise_for_status();raw=r.content;v=json.loads(raw);rows=v['data']['diff']
            assert v['rc']==0 and len(rows)==len(batch)
            stage.joinpath(f'bulk-{index:02}.raw').write_bytes(raw)
            return rows
        except (requests.RequestException,ValueError,KeyError,AssertionError):
            if attempt:return []

rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    tasks=[pool.submit(fetch,i,symbols[i:i+300]) for i in range(0,len(symbols),300)]
    for task in concurrent.futures.as_completed(tasks):rows.extend(task.result())
issues=[];dates=set();inactive=0
for r in rows:
    try:
        stamp=datetime.fromtimestamp(float(r['f124']),ZoneInfo('Asia/Shanghai')).isoformat();dates.add(stamp[:10])
        if float(r['f6'])==0:inactive+=1;continue
        main,large,extra=map(float,[r['f62'],r['f72'],r['f66']])
        if abs(main-large-extra)>max(128,abs(main)*1e-6):issues.append((r['f12'],'additivity'))
        if abs(main/float(r['f6'])*100-float(r['f184']))>.06:issues.append((r['f12'],'ratio'))
    except (ValueError,TypeError,KeyError):issues.append((r.get('f12'),'missing'))
result=dict(expected=len(symbols),returned=len(rows),unique=len({(r['f13'],r['f12']) for r in rows}),dates=sorted(dates),inactive=inactive,issues=issues)
stage.joinpath('bulk-probe.json').write_text(json.dumps(result),encoding='utf8');print(json.dumps(result))
