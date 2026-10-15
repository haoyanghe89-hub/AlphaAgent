import { onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'

export function createMarketDashboard({ api=request }={}) {
  const data=ref(null), loading=ref(false), error=ref('')
  let disposed=false
  async function refresh() {
    if(disposed || loading.value)return
    loading.value=true
    try { const value=await api('/quant/research/market-dashboard',{timeoutMs:120000});if(!disposed){data.value=value;error.value=''} }
    catch(e){if(!disposed)error.value=e.message}
    finally{loading.value=false}
  }
  return {data,loading,error,refresh,dispose(){disposed=true}}
}
export function useMarketDashboard(){
  const state=createMarketDashboard();let timer
  const visible=()=>{if(!document.hidden)state.refresh()}
  onMounted(()=>{state.refresh();timer=setInterval(visible,30000);document.addEventListener('visibilitychange',visible)})
  onUnmounted(()=>{state.dispose();clearInterval(timer);document.removeEventListener('visibilitychange',visible)})
  return state
}
