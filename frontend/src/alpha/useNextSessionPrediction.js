import { onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'
import {postSavedTask} from '../api/savedTask.js'

export const nextSessionRoot = '/quant/decision/experiments/next-session'
export function createNextSessionPrediction(id, { api=request, navigate=rid => { window.location.hash='#/decision/'+rid }, developer=false }={}) {
  const state=ref(null), status=ref(null), scoreboard=ref(null), history=ref([]), error=ref(''), busy=ref(false)
  let disposed=false, generation=0, timer, pending
  const active=() => state.value?.status==='RUNNING'
  async function refresh() {
    const ticket=++generation; clearTimeout(timer)
    try {
      const [s, board, runs, run]=await Promise.all([
        api(nextSessionRoot+'/status'),api(nextSessionRoot+'/scoreboard'),api(nextSessionRoot+'/runs'),
        id?.startsWith('RUN-NS-') ? api(nextSessionRoot+'/runs/'+id+(developer?'?developer=true':'')) : Promise.resolve(null),
      ])
      if(disposed || ticket!==generation)return
      status.value=s;scoreboard.value=board;history.value=runs;state.value=run;error.value=''
      if(active())timer=setTimeout(refresh,2000)
    }catch(e){
      if(disposed || ticket!==generation)return
      if(e.status===0 || e.status>=500){
        error.value='连接暂时中断，正在自动重连；已保存预测保留。'+e.message
        timer=setTimeout(refresh,4000)
      }else{state.value=null;scoreboard.value=null;error.value=e.message}
    }
  }
  async function start(entity) {
    if(busy.value || disposed || active())return
    busy.value=true;error.value=''
    try {
      if(!status.value?.enabled)await api(nextSessionRoot+'/enable',{method:'POST',body:'{}',timeoutMs:60000})
      const selection = typeof entity==='string' ? entity.trim() : ''
      if ((pending?.entity || '') !== selection) pending=null
      pending ||= {request_key:crypto.randomUUID(),experiment_id:status.value?.experiment_id || 'ALPHA_NEXT_SESSION_V1',...(selection?{entity:selection,exploratory:true}:{})}
      const options={method:'POST',body:JSON.stringify(pending),timeoutMs:45000}
      const run=await postSavedTask(api,nextSessionRoot+'/runs',options,{disposed:()=>disposed,waiting:m=>{error.value=m}})
      if(!disposed){pending=null;navigate(run.run_id)}
    }catch(e){if(!disposed)error.value=e.message}
    finally{if(!disposed)busy.value=false}
  }
  async function action(name) {
    if(!id || busy.value || disposed)return
    busy.value=true;error.value=''
    try{await api(nextSessionRoot+'/runs/'+id+'/'+name,{method:'POST',body:'{}',timeoutMs:150000});if(!disposed)await refresh()}
    catch(e){if(!disposed){state.value=null;scoreboard.value=null;error.value=e.message}}
    finally{if(!disposed)busy.value=false}
  }
  const dispose=()=>{disposed=true;generation++;clearTimeout(timer)}
  return {state,status,scoreboard,history,error,busy,active,refresh,start,action,dispose}
}
export function useNextSessionPrediction(id,options){
  const controller=createNextSessionPrediction(id,options)
  onMounted(controller.refresh);onUnmounted(controller.dispose);return controller
}
