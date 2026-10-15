"""Read-only public intraday funding capability probe; never handles credentials."""
import concurrent.futures
import hashlib
import json
import sqlite3
from pathlib import Path

import requests

stage = Path('.phase3-quant-o/main-flow-20261015-01')
stage.mkdir(parents=True, exist_ok=True)
with sqlite3.connect('file:.phase3-quant-o/alpha-history-real-02/metadata.sqlite?mode=ro', uri=True) as db:
    baseline = {r[0]: hashlib.sha256(r[1].encode()).hexdigest() for r in db.execute('select run_id,payload from inv_quant_research_run')}
    if not (stage / 'baseline.json').exists():
        (stage / 'baseline.json').write_text(json.dumps(baseline))
tasks = [
    ('qq-flow', 'https://qt.gtimg.cn/q=ff_sh600519,ff_sz300750,ff_sz002371', None),
    ('qq-page', 'https://gu.qq.com/sh600519', None),
    ('em-flow-minute', 'https://push2.eastmoney.com/api/qt/stock/fflow/kline/get', dict(secid='1.600519', lmt=0, klt=1, fields1='f1,f2,f3,f7', fields2='f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63')),
    ('em-flow-current', 'https://push2.eastmoney.com/api/qt/stock/get', dict(secid='1.600519',fields='f57,f58,f124,f62,f184,f64,f65,f66,f69,f70,f71,f72,f75,f76,f77,f78,f81,f82,f83,f84')),
    ('sina-flow', 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs', dict(symbol='sh600519')),
]

def run(task):
    key, url, params = task
    try:
        r = requests.get(url, params=params, headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'},timeout=(5,15))
        raw = r.content
        (stage/(key+'.raw')).write_bytes(raw)
        return dict(key=key,status=r.status_code,size=len(raw),sample=raw.decode('gb18030',errors='replace')[:1800])
    except requests.RequestException as e:
        return dict(key=key,error=type(e).__name__)

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        result=list(pool.map(run,tasks))
    (stage/'probe.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf8')
    print(json.dumps(result,ensure_ascii=True))
