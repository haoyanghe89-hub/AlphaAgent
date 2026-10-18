import {ref} from 'vue'
import request from '../api/request.js'
export function createObservations({api=request}={}){
  const view=ref(null),summary=ref(null),error=ref(''),busy=ref(false),candidate=ref(null)
  let generation=0,disposed=false
  async function load(id){const ticket=++generation;error.value='';view.value=null;summary.value=null
    try{const value=await api('/quant/investment-research/runs/'+id+'/observations');if(disposed||ticket!==generation)return;view.value=value;const stats=await api('/quant/investment-research/evaluation');if(!disposed&&ticket===generation)summary.value=stats}
    catch(e){if(!disposed&&ticket===generation){view.value=null;summary.value=null;error.value=e.message}}
  }
  async function observe(id){if(busy.value)return;busy.value=true;error.value='';const ticket=generation
    try{await api('/quant/investment-research/runs/'+id+'/observe',{method:'POST',timeoutMs:200000});if(!disposed&&ticket===generation)await load(id)}
    catch(e){if(!disposed&&ticket===generation)error.value=e.message}
    finally{if(!disposed)busy.value=false}
  }
  async function suggest(id){if(busy.value)return;busy.value=true;error.value='';const ticket=generation
    try{const value=await api('/quant/investment-research/runs/'+id+'/review-candidate',{method:'POST'});if(!disposed&&ticket===generation)candidate.value=value}
    catch(e){if(!disposed&&ticket===generation)error.value=e.message}
    finally{if(!disposed)busy.value=false}
  }
  return {view,summary,error,busy,candidate,load,observe,suggest,dispose(){disposed=true;generation++}}
}
export const observationLabels={PENDING:'等待到期',PENDING_CALENDAR:'等待完整交易日历',MATURED:'已到期核对',SUSPENDED_UNSCORABLE:'含停牌，保留但不可评分',DATA_MISSING_UNSCORABLE:'行情缺项，保留待重核',CALENDAR_CHANGED_UNSCORABLE:'日历变更，保留但不可评分',LATE_ISSUE_UNSCORABLE:'签发晚于观察开始，不计前瞻成绩',PENDING_OBSERVATION:'尚未核对',RESEARCH_INTERRUPTED:'研究未完成，样本保留',RESEARCH_RUNNING:'研究进行中'}
export function observationChart(stock,report){
  const candidate=report.candidates.find(c=>c.security_id===stock.security_id)
  return {kind:'investment_forward',title:stock.name+' · 原收盘与后续复权价格（元）',x:[report.market_date,...stock.points.map(p=>p.date)],series:[{name:'价格代理',data:[Number(candidate.metrics.close.value),...stock.points.map(p=>p.price==null?null:Number(p.price))],symbol:'circle'}]}
}
