"""Read public original source layouts; preserve completed-run baseline first."""
import hashlib
import json
import sqlite3
from pathlib import Path
import requests
from html.parser import HTMLParser

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.current=None
    def handle_starttag(self, tag, attrs):
        if tag == 'a': self.current=[dict(attrs).get('href',''),'']
    def handle_data(self, data):
        if self.current: self.current[1]+=data
    def handle_endtag(self, tag):
        if tag == 'a' and self.current:
            self.links.append(self.current); self.current=None

stage=Path('.phase3-quant-o/decision-p4-20261013-01')
stage.mkdir(exist_ok=True)
dbpath=Path('.phase3-quant-o/alpha-history-real-02/metadata.sqlite').resolve()
if not (stage/'baseline.json').exists():
    with sqlite3.connect('file:'+dbpath.as_posix()+'?mode=ro',uri=True) as db:
        baseline={r[0]:hashlib.sha256(r[1].encode()).hexdigest() for r in db.execute("select run_id,payload from inv_quant_research_run where status='COMPLETED'")}
        with sqlite3.connect(stage/'before.sqlite') as target: db.backup(target)
    (stage/'baseline.json').write_text(json.dumps(baseline,indent=2),encoding='utf8')
for key,url in [('wsts','https://www.wsts.org/76/PRESS-ARCHIVE'),('semi','https://www.semi.org/en/taxonomy/term/45726')]:
    response=requests.get(url,timeout=25,headers={'User-Agent':'Mozilla/5.0'})
    response.raise_for_status(); response.encoding='utf8'
    (stage/(key+'.html')).write_text(response.text,encoding='utf8')
    parser=Links(); parser.feed(response.text)
    print(key,[(u,t.strip()) for u,t in parser.links if 'Global Semiconductor' in t or 'Global semiconductor' in t][:10])
