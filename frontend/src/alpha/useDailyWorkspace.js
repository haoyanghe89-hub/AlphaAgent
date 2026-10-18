import { onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'

export function createDailyWorkspace({api=request,id}={}) {
  const status=ref(null),briefing=ref(null),error=ref(''),busy=ref(false)
  let disposed=false,generation=0
  async function load(){
    const ticket=++generation
    const results=await Promise.allSettled([api('/quant/daily/status'),api(id?'/quant/daily/briefings/'+id:'/quant/daily/briefings')])
    if(disposed||ticket!==generation)return
    if(results[0].status==='fulfilled')status.value=results[0].value
    if(results[1].status==='fulfilled')briefing.value=id?results[1].value:results[1].value.latest
    else briefing.value=null
    error.value=results.filter(r=>r.status==='rejected').map(r=>r.reason.message).join('；')
  }
  async function resume(){
    if(busy.value||!briefing.value)return
    busy.value=true
    try{await api('/quant/daily/briefings/'+briefing.value.run_id+'/resume',{method:'POST'});await load()}
    catch(e){error.value=e.message}finally{busy.value=false}
  }
  async function generate(){
    if(busy.value)return
    const ticket=++generation
    busy.value=true
    try{const value=await api('/quant/daily/briefings',{method:'POST',timeoutMs:45000});if(!disposed&&ticket===generation){briefing.value=value;error.value=''}}
    catch(e){error.value=e.message}finally{busy.value=false}
  }
  return{status,briefing,error,busy,load,resume,generate,dispose(){disposed=true;generation++}}
}
export function useDailyWorkspace(id){
  const state=createDailyWorkspace({id});let timer
  const refresh=()=>{if(!document.hidden)state.load()}
  onMounted(()=>{state.load();timer=setInterval(refresh,30000);document.addEventListener('visibilitychange',refresh)})
  onUnmounted(()=>{clearInterval(timer);document.removeEventListener('visibilitychange',refresh);state.dispose()})
  return state
}
