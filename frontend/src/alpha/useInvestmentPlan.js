import { ref } from 'vue'
import request from '../api/request.js'
import {postSavedTask} from '../api/savedTask.js'
export function createInvestmentPlan({api=request,schedule=setTimeout,unschedule=clearTimeout,started=()=>{},root='/quant/investment-plans'}={}) {
  const state=ref(null),error=ref(''),busy=ref(false)
  let disposed=false,generation=0,timer,key,signature,readFailures=0
  function later(id){unschedule(timer);if(state.value?.status==='RUNNING')timer=schedule(()=>load(id),2500)}
  async function load(id){
    const ticket=++generation;unschedule(timer);error.value=''
    if(state.value?.run_id!==id)state.value=null
    try{const result=await api(root+'/'+encodeURIComponent(id));if(disposed||ticket!==generation)return;readFailures=0;state.value=result;later(id)}
    catch(e){if(!disposed&&ticket===generation){
      if(e.status===0||e.status>=500){
        error.value='连接暂时中断，正在自动重连；已保存任务会继续处理。'+e.message
        timer=schedule(()=>load(id),Math.min(15000,2500*++readFailures))
      }else{state.value=null;error.value=e.message}
    }}
  }
  async function submit(body){
    if(busy.value||disposed)return
    const serialized=JSON.stringify(body)
    if(signature!==serialized){signature=serialized;key=crypto.randomUUID()}
    busy.value=true;error.value='';generation++;unschedule(timer)
    try{
      const options={method:'POST',body:JSON.stringify({...body,request_key:key})}
      const result=await postSavedTask(api,root,options,{disposed:()=>disposed,waiting:m=>{error.value=m}})
      if(disposed)return
      error.value='';state.value=result;key=undefined;signature=undefined;started(result);later(result.run_id)
    }
    catch(e){if(!disposed){if(e.status===409&&/校验|完整性/.test(e.message))state.value=null;error.value=e.message}}
    finally{if(!disposed)busy.value=false}
  }
  async function resume(){
    if(busy.value||!state.value)return
    busy.value=true;error.value='';const id=state.value.run_id
    try{await postSavedTask(api,root+'/'+encodeURIComponent(id)+'/resume',{method:'POST'},{disposed:()=>disposed,waiting:m=>{error.value=m}});if(!disposed)await load(id)}
    catch(e){if(!disposed)error.value=e.message}
    finally{if(!disposed)busy.value=false}
  }
  return {state,error,busy,load,submit,resume,dispose(){disposed=true;generation++;unschedule(timer)}}
}
export function investmentChart(candidate){return {kind:'investment_price',title:candidate.name+'复权收盘与条件参考（元）',x:candidate.chart.map(p=>p.date),series:[{name:'复权收盘',data:candidate.chart.map(p=>Number(p.close))},...candidate.routes.filter(r=>r.route_id===candidate.selected_route).flatMap(r=>[{name:'条件触发参考',data:candidate.chart.map(()=>Number(r.entry_reference))},{name:'风险参考',data:candidate.chart.map(()=>r.risk_reference==null?null:Number(r.risk_reference))}])]}}
