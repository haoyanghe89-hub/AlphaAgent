"""Inspect archived public role outputs without changing failed research records."""
import json,sys,re
from pathlib import Path
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.decision import agents
from marketpulse.quant.contracts import canonical
sys.stdout.reconfigure(encoding='utf8')
state=json.loads(Path(sys.argv[1]).read_text('utf8'))
blobs=LocalContentAddressedBlobStorage(Path('.phase3-quant-o/alpha-history-real-02/blobs'))
def value(ref):return json.loads(blobs.get_bytes(BlobRef.from_uri(ref)))
def scan(obj,path=''):
    if isinstance(obj,dict):
        for key,v in obj.items():scan(v,path+'.'+key)
    elif isinstance(obj,list):
        for i,v in enumerate(obj):scan(v,path+str(i))
    elif isinstance(obj,str) and re.search('超预期|不及预期|独立印证|贡献率|必然受益|已确认订单|已证实',obj):print(path,obj)
for call in state['calls']:
    if call.get('role')=='Analyst' and call.get('response_ref'):
        print('attempt',call['attempt'],'charged',call.get('charged_tokens'))
        scan(value(call['response_ref']))
        context=value(call['request_ref'])['context']
        try:
            agents.guard_analysis(agents.IndustryAnalysis.model_validate(value(call['response_ref'])),context)
            print('current deterministic guard passed; original record remains unchanged')
        except ValueError as error:print('current guard:',str(error))
        proposal=value(call['response_ref'])
        cited={i for c in proposal['claims'] for i in c['support_ids']+c['counter_ids']}
        cited.update(proposal['industry_assessment']['evidence_ids'])
        for a in proposal['flow_assessments']:cited.update(a['evidence_ids'])
        context['evidence']=[e for e in context['evidence'] if e['evidence_id'] in cited or e['kind']=='disclosure']
        context.pop('focus_questions',None);context.pop('question',None)
        context['proposal']=proposal
        print('trimmed review reserve',len((canonical(context)+agents.INDUSTRY_PROMPTS['Verifier']+canonical(agents.IndustryReview.model_json_schema())).encode())+3500)
