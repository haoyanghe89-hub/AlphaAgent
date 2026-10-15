"""Real routing acceptance; source traceability and software gates, not trading efficacy."""

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import httpx


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    with httpx.Client(base_url='http://127.0.0.1:8839', timeout=90) as c:
        last = None
        for _ in range(450):
            response = c.get('/api/quant/decision/runs/' + args.run_id)
            response.raise_for_status()
            state = response.json()
            progress = (state['status'], state.get('model_calls'), state.get('process', [{}])[-1].get('detail'))
            if progress != last:
                print(progress, flush=True)
                last = progress
            if state['status'] != 'RUNNING':
                break
            time.sleep(2)
        save(args.output / 'run.json', state)
        assert state['status'] == 'COMPLETED', state.get('error')
        data = state['data']
        assert data['report']['stage'] == 'P4K'
        assert data['report']['opinion']['outlook'] in {'FAVORABLE','UNFAVORABLE'}
        route = data['method_routing']
        assert route['profile']['primary_role'] == 'EQUIPMENT'
        assert route['primary_method_id'] == 'EQ-TRANSMISSION'
        selected = {route['primary_method_id'], *route['auxiliary_method_ids']}
        assert {r['method_id'] for r in route['execution_records']} == selected
        assert len(data['forecast']['method_versions']) == len(selected)
        assert all(r['input_evidence_ids'] for r in route['execution_records'])
        assert len(data['knowledge_notes']) >= 2
        assert not any(e['source_family'] == 'METHOD_KNOWLEDGE' for e in data['bundle']['items'])
        assert all(n['coverage'] == 'PUBLIC_EXCERPT_VERIFIED' for n in data['knowledge_notes'])
        assert {p['jurisdiction'] for p in data['policy_report']['policies']} == {'CN','US'}
        assert all(p['qualification_status'] == 'UNKNOWN' and p['company_link_status'] == 'HYPOTHESIS' for p in data['policy_report']['policies'])
        assert state['model_calls'] >= 4 and state['tokens'] <= 60000 and state['elapsed_seconds'] <= 900
        for endpoint in ('export','forecast'):
            response = c.get(f'/api/quant/decision/runs/{args.run_id}/{endpoint}')
            response.raise_for_status()
            save(args.output / (endpoint + '.json'), response.json())
            if endpoint == 'export':
                assert response.json()['data'] == data
        for family in ('POLICY_CN','POLICY_US'):
            for item in [e for e in data['bundle']['items'] if e['source_family'] == family]:
                response = c.get(f"/api/quant/decision/runs/{args.run_id}/evidence/{item['evidence_id']}")
                response.raise_for_status()
                assert response.json()['evidence'] == item
        response = c.post(f'/api/quant/decision/runs/{args.run_id}/observe')
        response.raise_for_status()
        outcomes = response.json()
        assert all(o['maturity'] == 'PENDING' and o['security_return'] is None for o in outcomes['outcomes'])
        assert c.post(f'/api/quant/decision/runs/{args.run_id}/observe').json() == outcomes
        save(args.output / 'outcomes.json', outcomes)
    baseline = json.loads(Path('.phase3-quant-o/decision-routing-20261013-01/baseline.json').read_text(encoding='utf8'))
    dbpath = Path('.phase3-quant-o/alpha-history-real-02/metadata.sqlite').resolve()
    with sqlite3.connect('file:' + dbpath.as_posix() + '?mode=ro', uri=True) as db:
        now = {r[0]:hashlib.sha256(r[1].encode()).hexdigest() for r in db.execute("select run_id,payload from inv_quant_research_run where status='COMPLETED'")}
    assert all(now.get(k) == v for k,v in baseline.items())
    result = dict(run_id=args.run_id, model_calls=state['model_calls'], tokens=state['tokens'], primary=route['primary_method_id'],
                  actual_methods=sorted(selected), knowledge_count=len(data['knowledge_notes']), policy_count=len(data['policy_report']['policies']),
                  unchanged_completed=len(baseline), user_acceptance='PENDING', prediction_validity='UNVERIFIED')
    save(args.output / 'acceptance.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
