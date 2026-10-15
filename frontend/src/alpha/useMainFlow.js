import { onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'

export function createMainFlow({api=request}={}){
  const daily=ref(null),stock=ref(null),symbol=ref(''),dailyError=ref(''),stockError=ref(''),loading=ref(false),dailyLoading=ref(false),searching=ref(false),matches=ref([]),searchError=ref('')
  let disposed=false,generation=0,searchGeneration=0,pending=false
  async function refreshDaily(){
    if(disposed||dailyLoading.value)return
    dailyLoading.value=true
    try{const value=await api('/quant/research/main-flow',{timeoutMs:120000});if(!disposed){daily.value=value;dailyError.value='';if(!symbol.value){const first=[...value.stocks].sort((a,b)=>b.main-a.main)[0];if(first)select(first.symbol)}}}
    catch(e){if(!disposed)dailyError.value=e.message}finally{dailyLoading.value=false}
  }
  async function refreshStock(){
    if(disposed||!symbol.value)return
    if(pending)return
    const ticket=generation,requested=symbol.value
    pending=true;loading.value=true
    try{const value=await api('/quant/research/main-flow/'+requested,{timeoutMs:120000});if(!disposed&&ticket===generation){stock.value=value;stockError.value=''}}
    catch(e){if(!disposed&&ticket===generation)stockError.value=e.message}
    finally{pending=false;loading.value=false;if(!disposed&&ticket!==generation)refreshStock()}
  }
  function select(value){searchGeneration++;searching.value=false;if(value===symbol.value)return refreshStock();generation++;symbol.value=value;stock.value=null;stockError.value='';matches.value=[];searchError.value='';return refreshStock()}
  async function search(text){
    const ticket=++searchGeneration
    if(!text.trim()||disposed)return
    searching.value=true;searchError.value='';matches.value=[]
    try{const list=await api('/quant/research/securities?q='+encodeURIComponent(text.trim()),{timeoutMs:30000});if(disposed||ticket!==searchGeneration)return
      if(list.length===1)select(list[0].symbol);else if(list.length)matches.value=list;else searchError.value='未找到沪深 A 股，请输入名称或六位代码。'
    }catch(e){if(!disposed&&ticket===searchGeneration)searchError.value=e.message}finally{if(ticket===searchGeneration)searching.value=false}
  }
  return{daily,stock,symbol,dailyError,stockError,loading,dailyLoading,searching,matches,searchError,refreshDaily,refreshStock,select,search,dispose(){disposed=true;generation++;searchGeneration++}}
}
export function useMainFlow(){const state=createMainFlow();let minuteTimer,dailyTimer
  const visible=()=>{if(!document.hidden)state.refreshStock()}
  onMounted(()=>{state.refreshDaily();minuteTimer=setInterval(visible,30000);dailyTimer=setInterval(()=>{if(!document.hidden)state.refreshDaily()},600000);document.addEventListener('visibilitychange',visible)})
  onUnmounted(()=>{state.dispose();clearInterval(minuteTimer);clearInterval(dailyTimer);document.removeEventListener('visibilitychange',visible)})
  return state
}
