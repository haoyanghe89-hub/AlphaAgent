from pathlib import Path

p=Path('frontend/src/alpha/MediumTermResearch.vue')
s=p.read_text(encoding='utf8')
needle="const conditions=(name,parent)=>"
assert needle in s
s=s.replace(needle,"async function withdraw(reason){if(observing.value)return;observing.value=true;try{await request('/quant/decision/runs/'+props.id+'/withdraw',{method:'POST',body:JSON.stringify({reason})});if(!disposed)await tracking()}catch(e){if(!disposed)trackingError.value=e.message}finally{if(!disposed)observing.value=false}}\n"+needle)
s=s.replace('@observe="observe"','@observe="observe" @withdraw="withdraw"')
p.write_text(s,encoding='utf8')
