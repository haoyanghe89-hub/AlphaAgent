"""Read the source page's published client configuration, no authentication or bypass."""
from pathlib import Path
import re
import requests
stage=Path('.phase3-quant-o/decision-p6-real-01')
url='https://quote.eastmoney.com/center/static/build/index.js'
response=requests.get(url,timeout=20)
response.raise_for_status()
(stage/'provider-index.js').write_bytes(response.content)
text=response.content.decode('utf8')
for pattern in (r'.{0,120}pushguest.{0,180}',r'.{0,100}clist/get.{0,160}',r'.{0,100}industry_board_3.{0,160}'):
 for match in re.findall(pattern,text)[:10]:print(match)
