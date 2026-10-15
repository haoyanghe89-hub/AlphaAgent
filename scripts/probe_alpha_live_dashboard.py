"""Read-only public market feed capability probe, without credentials."""
import concurrent.futures
import json
from pathlib import Path

import requests

stage = Path('.phase3-quant-o/live-dashboard-20261015-01')
stage.mkdir(parents=True, exist_ok=True)
tasks = [
    ('em-industry', 'https://push2.eastmoney.com/api/qt/clist/get', dict(pn=1, pz=100, np=1, fltt=2, invt=2, fid='f3', po=1, fs='m:90+t:2+f:!50', fields='f12,f14,f2,f3,f6,f104,f105,f124,f128,f140,f141')),
    ('sina-industry', 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php', {}),
    ('sina-concept', 'https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php', dict(param='class')),
    ('sina-nodes', 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodes', {}),
    ('sina-members', 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData', dict(page=1, num=5, sort='amount', asc=0, node='hangye_Z10', symbol='', _s_r_a='page')),
]

def run(task):
    key, url, params = task
    try:
        r = requests.get(url, params=params, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}, timeout=(5, 12))
        (stage / (key + '.raw')).write_bytes(r.content)
        text = r.content.decode('gb18030', errors='replace')
        return dict(key=key, status=r.status_code, size=len(r.content), sample=text[:1800])
    except Exception as error:
        return dict(key=key, error=type(error).__name__)

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(run, tasks))
    (stage / 'probe.json').write_text(json.dumps(results, ensure_ascii=False), encoding='utf8')
    print(json.dumps(results, ensure_ascii=True))
