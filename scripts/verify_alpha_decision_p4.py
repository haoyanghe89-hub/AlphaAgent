"""Real P4 acceptance: original prices, time ordering, source versions, immutable history."""
import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime
from decimal import Decimal,localcontext
from pathlib import Path

import httpx
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef

def save(path,value):
    with path.open('x',encoding='utf8') as f: json.dump(value,f,ensure_ascii=False,indent=2)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(exist_ok=False)
    rid=args.run_id;last=None
    with httpx.Client(base_url='http://127.0.0.1:8839',timeout=90) as client:
        for _ in range(450):
            response=client.get('/api/quant/decision/runs/'+rid);response.raise_for_status();state=response.json()
            progress=(state['status'],state.get('model_calls'),state.get('process',[{}])[-1].get('detail'))
            if progress!=last: print(progress,flush=True);last=progress
            if state['status']!='RUNNING': break
            time.sleep(2)
        save(args.output/'run.json',state)
        assert state['status']=='COMPLETED',state.get('error')
        data=state['data'];report=data['report'];industry=data['industry_report'];attribution=data['attribution_report']
        assert report['stage']=='P4' and report['opinion']['outlook'] in {'FAVORABLE','UNFAVORABLE'}
        assert industry['template_status']=='CONDITIONAL_ENABLED' and industry['business_role']=='EQUIPMENT'
        assert len(industry['chain'])==4 and all(e['status']=='HYPOTHESIS' for e in industry['chain'])
        assert industry['source_dependencies'].count('WSTS')==1
        assert len({f['version'] for f in industry['facts'] if f['underlying_statistics']=='WSTS'})>=2
        assert attribution['causal_identification']=='NOT_IDENTIFIED' and attribution['adjustment']=='raw'
        assert state['model_calls']>=4 and all(c.get('model')!='synthetic' for c in state['calls'] if c.get('role'))
        assert state['model_calls']<=24 and state['tokens']<=60000 and state['elapsed_seconds']<=900
        for endpoint,name in [('export','export.json'),('forecast','forecast.json')]:
            response=client.get(f'/api/quant/decision/runs/{rid}/{endpoint}');response.raise_for_status();save(args.output/name,response.json())
            if endpoint=='export': assert response.json()['data']==data
        prefixes=['EV-IND-WSTS','EV-IND-DEMAND','EV-EVENT','EV-ATTR','EV-FLOW']
        for prefix in prefixes:
            item=next(e for e in data['bundle']['items'] if e['evidence_id'].startswith(prefix))
            response=client.get(f"/api/quant/decision/runs/{rid}/evidence/{item['evidence_id']}")
            response.raise_for_status();assert response.json()['evidence']==item
        outcome=client.post(f'/api/quant/decision/runs/{rid}/observe');outcome.raise_for_status()
        assert all(o['maturity']=='PENDING' and o['security_return'] is None for o in outcome.json()['outcomes'])
        assert client.post(f'/api/quant/decision/runs/{rid}/observe').json()==outcome.json()
        save(args.output/'outcomes.json',outcome.json())
    root=Path('.phase3-quant-o/alpha-history-real-02').resolve()
    blobs=LocalContentAddressedBlobStorage(root/'blobs')
    def original(source): return json.loads(blobs.get_bytes(BlobRef.from_uri(source['blob_ref'])))
    prices={}
    for entry in data['flow_snapshot']['responses']:
        if entry['role'] in {'security_daily','market_daily'}:
            symbol=data['flow_snapshot']['subject']['symbol'] if entry['role']=='security_daily' else 'sh000300'
            prices[entry['role']]={r[0]:Decimal(str(r[2])) for r in original(entry['source'])['data'][symbol]['day']}
    proxy=next(e for e in data['industry_snapshot']['responses'] if e['role']=='theme_proxy')
    prices['proxy']={r[0]:Decimal(str(r[2])) for r in original(proxy['source'])['data']['sh512480']['day']}
    manual=[]
    for window in attribution['windows']:
        baseline=window['baseline_date'];end=window['sessions'][-1]
        assert len(window['sessions'])==window['window'] and window['industry_return'] is None
        result={}
        with localcontext() as context:
            context.prec=28
            for role,key in [('security_daily','security_return'),('market_daily','market_return'),('proxy','proxy_return')]:
                raw=prices[role]
                assert all(d in raw for d in [baseline,*window['sessions']])
                expected=raw[end]/raw[baseline]-1
                assert Decimal(window[key])==expected
                result[key]=dict(baseline_price=str(raw[baseline]),end_price=str(raw[end]),return_value=str(expected))
        assert Decimal(window['relative_market'])==Decimal(window['security_return'])-Decimal(window['market_return'])
        assert Decimal(window['relative_proxy'])==Decimal(window['security_return'])-Decimal(window['proxy_return'])
        manual.append(dict(window=window['window'],baseline=baseline,end=end,original_prices=result))
        for event in window['events']:
            published=datetime.fromisoformat(event['published_at']).astimezone().date()
            assert event['effective_session']>published.isoformat()
            assert window['sessions'][0]<=event['effective_session']<=end
    for doc in data['industry_documents']:
        raw=blobs.get_bytes(BlobRef.from_uri(doc['raw_ref']))
        assert raw.startswith(b'%PDF-') and hashlib.sha256(raw).hexdigest()==doc['content_hash']
        parsed=json.loads(blobs.get_bytes(BlobRef.from_uri(doc['text_ref'])))
        assert parsed['subject_id']=='688981.SH' and '688981' in ''.join(parsed['pages'][:12])
    baseline=json.loads(Path('.phase3-quant-o/decision-p4-20261013-01/baseline.json').read_text('utf8'))
    with sqlite3.connect('file:'+(root/'metadata.sqlite').as_posix()+'?mode=ro',uri=True) as db:
        after={r[0]:hashlib.sha256(r[1].encode()).hexdigest() for r in db.execute("select run_id,payload from inv_quant_research_run where status='COMPLETED'")}
    assert all(after.get(k)==v for k,v in baseline.items())
    save(args.output/'manual-original-crosscheck.json',dict(windows=manual,independent_price_verification='NOT_DONE',event_scope='bounded catalog titles only',adjustment='raw',causal_identification='NOT_IDENTIFIED'))
    acceptance=dict(run_id=rid,model_calls=state['model_calls'],tokens=state['tokens'],outlook=report['opinion']['outlook'],industry=report['industry_analysis'],attribution=report['attribution_analysis'],unchanged_completed=len(baseline),event_count=len(attribution['timeline']),gaps=industry['gaps'],prediction_validity='UNVERIFIED',user_acceptance='PENDING')
    save(args.output/'acceptance.json',acceptance)
    print(json.dumps(acceptance,ensure_ascii=True),flush=True)

if __name__=='__main__':main()
