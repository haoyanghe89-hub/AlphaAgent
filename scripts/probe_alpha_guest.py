"""Probe the current public endpoint observed in Eastmoney's industry page."""
import json
import sys
from pathlib import Path
import requests
sys.stdout.reconfigure(encoding='utf8')
stage=Path('.phase3-quant-o/decision-p6-real-01')
url='https://pushguest.eastmoney.com/api/qt/clist/get'
response=requests.get(url,params=dict(pn=1,pz=100,np=1,fltt=2,invt=2,fs='m:90+s:8+f:!50',fid='f12',po=1,fields='f12,f14',ut='fa5fd1943c7b386f172d6893dbfba10b'),headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'},timeout=20)
(stage/'guest.raw').write_bytes(response.content)
value=json.loads(response.content)
rows=value.get('data',{}).get('diff',[])
rows=list(rows.values()) if isinstance(rows,dict) else rows
print(response.status_code,value.get('data',{}).get('total'),len(rows),[r for r in rows if '半导体' in r['f14'] or '集成电路' in r['f14']])
