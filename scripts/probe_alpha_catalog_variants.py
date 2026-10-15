"""Bounded transport diagnostic against endpoints observed in the source UI."""
import json
import sys
from pathlib import Path
import requests
sys.stdout.reconfigure(encoding='utf8')
stage=Path('.phase3-quant-o/decision-p6-real-01')
options=[
 ('installed-catalog','https://17.push2.eastmoney.com/api/qt/clist/get',dict(pn=1,pz=100,po=1,np=1,ut='bd1d9ddb04089700cf9c27f6f7426281',fltt=2,invt=2,fid='f12',fs='m:90+s:8+f:!50',fields='f12,f14')),
 ('installed-members','https://29.push2.eastmoney.com/api/qt/clist/get',dict(pn=1,pz=100,po=1,np=1,ut='bd1d9ddb04089700cf9c27f6f7426281',fltt=2,invt=2,fid='f12',fs='b:BK1328',fields='f12,f13,f14,f20,f9')),
 ('guest-browser','https://pushguest.eastmoney.com/api/qt/clist/get',dict(timil=1,np=1,fltt=1,invt=2,fs='m:90+s:8+f:!50',fields='f12,f14',fid='f3',pn=1,pz=20,po=1,dect=1,ut='fa5fd1943c7b386f172d6893dbfba10b',wbp2u='|0|0|0|0|web')),
 ('push-browser','https://push2.eastmoney.com/api/qt/clist/get',dict(np=1,fltt=2,invt=2,fs='m:90+t:2+f:!50',fields='f12,f14',fid='f3',pn=1,pz=20,po=1,ut='bd1d9ddb04089700cf9c27f6f7426281')),
 ('push-prior','https://push2.eastmoney.com/api/qt/clist/get',dict(pn=3,pz=100,fs='m:90+t:2+f:!50',fid='f12',po=1,fields='f12,f14')),
]
for key,url,params in options:
 try:
  response=requests.get(url,params=params,headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'},timeout=12)
  (stage/(key+'.raw')).write_bytes(response.content)
  value=json.loads(response.content)
  print(key,response.status_code,str(value)[:300],flush=True)
 except Exception as error:
  print(key,type(error).__name__,str(error)[:180],flush=True)
