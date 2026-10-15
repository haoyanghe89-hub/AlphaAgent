"""P6 read-only source probe and live-record baseline; never issues model calls."""
import concurrent.futures
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import requests
sys.stdout.reconfigure(encoding='utf8')

stage = Path('.phase3-quant-o/decision-p6-real-01')
stage.mkdir(exist_ok=True)
dbpath = Path('.phase3-quant-o/alpha-history-real-02/metadata.sqlite').resolve()
if not (stage / 'baseline.json').exists():
    with sqlite3.connect('file:' + dbpath.as_posix() + '?mode=ro', uri=True) as db:
        baseline = {r[0]: hashlib.sha256(r[1].encode()).hexdigest() for r in db.execute("select run_id,payload from inv_quant_research_run where status='COMPLETED'")}
        with sqlite3.connect(stage / 'before.sqlite') as target:
            db.backup(target)
    (stage / 'baseline.json').write_text(json.dumps(baseline), encoding='utf8')

def fetch(key, url, params):
    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        (stage / (key + '.raw')).write_bytes(response.content)
        value = json.loads(response.content)
        if key.startswith('board'):
            print(key, [r for r in value.get('data', {}).get('diff', {}).values() if '半导体' in r['f14'] or '集成电路' in r['f14']], flush=True)
        else:
            print(key, str(value)[:1500], flush=True)
    except Exception as error:
        print(key, type(error).__name__, str(error)[:100], flush=True)

tasks = [('board'+str(p), 'https://push2.eastmoney.com/api/qt/clist/get', dict(pn=p,pz=100,fs='m:90+t:2+f:!50',fid='f12',po=1,fields='f12,f14')) for p in (1,2,3)]
tasks += [(s,'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get',dict(param=f'{s},day,2026-08-01,2026-10-13,100,')) for s in ('sh000002','sz399107')]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    list(pool.map(lambda args: fetch(*args), tasks))
