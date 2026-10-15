"""Real, read-only homepage feed acceptance and immutable research regression."""
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

import requests

stage = Path('.phase3-quant-o/live-dashboard-20261015-01')
classification = json.loads(Path('.phase3-quant-o/alpha-history-real-02/market-live-cache/classification.json').read_text(encoding='utf8'))
members = {g['id']: set(g['symbols']) for g in classification['groups']}
observations = []
for index in range(4):
    if index:
        time.sleep(31)
    response = requests.get('http://127.0.0.1:8839/api/quant/research/market-dashboard', timeout=120)
    response.raise_for_status()
    data = response.json()
    (stage / f'live-cycle-{index}.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
    coverage = data['coverage']
    assert coverage['industries_ranked'] == coverage['industries_expected'] == 31
    assert coverage['stocks_quoted'] == coverage['stocks_expected']
    assert [g['change'] for g in data['industries']] == sorted([g['change'] for g in data['industries']], reverse=True)
    assert [g['amount'] for g in data['hot_sectors']] == sorted([g['amount'] for g in data['hot_sectors']], reverse=True)
    for group in data['industries'] + data['hot_sectors']:
        assert group['up'] + group['down'] + group['flat'] == group['active_count']
        for label, field in [('leaders', 'market_cap'), ('trading_leaders', 'amount')]:
            assert len(group[label]) == 3
            assert all(q['symbol'] in members[group['id']] and q['quoted_at'][:10] == data['quote_date'] for q in group[label])
            assert [q[field] for q in group[label]] == sorted([q[field] for q in group[label]], reverse=True)
    observation = dict(cycle=index, retrieved_at=data['retrieved_at'], quote_date=data['quote_date'],
                       freshness=data['freshness'], coverage=coverage, refresh_error=data['refresh_error'])
    observations.append(observation)
    print(json.dumps(observation, ensure_ascii=True), flush=True)

sample = observations[-1]
assert len({v['retrieved_at'] for v in observations}) >= 2
names = [q['symbol'] for q in data['industries'][0]['leaders']]
raw = requests.get('https://qt.gtimg.cn/q=' + ','.join(names), timeout=(5, 15)).content
(stage / 'leader-source.raw').write_bytes(raw)
observed = {s: body.split('~') for s, body in re.findall(r'v_(\w+)="([^"]*)";', raw.decode('gb18030'))}
for stock in data['industries'][0]['leaders']:
    fields = observed[stock['symbol']]
    assert fields[2] == stock['code'] and fields[1] == stock['name']
    # This acceptance run is after close; prices must match the separate real read.
    assert float(fields[3]) == stock['price'] and float(fields[32]) == stock['change']

before = json.loads((stage / 'baseline.json').read_text())
with sqlite3.connect('file:.phase3-quant-o/alpha-history-real-02/metadata.sqlite?mode=ro', uri=True) as db:
    after = {r[0]: hashlib.sha256(r[1].encode()).hexdigest() for r in db.execute('select run_id,payload from inv_quant_research_run')}
assert before == after
result = dict(status='PASSED', research_records_unchanged=len(before), observations=observations,
              independent_leader_quotes=len(names), source_sha256=hashlib.sha256(raw).hexdigest())
(stage / 'real-acceptance.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf8')
print(json.dumps(dict(status='PASSED', unchanged=len(before)), ensure_ascii=True), flush=True)
