import { onMounted, onUnmounted, ref } from 'vue'
import {deepResearch} from './researchDepth.js'
import request from '../api/request.js'

export function createDecisionResearch(id, { api = request, navigate = rid => { window.location.hash = '#/decision/' + rid } } = {}) {
  const state = ref(null), history = ref([]), error = ref(''), submitting = ref(false), loading = ref(false)
  let disposed = false, generation = 0, timer, pending
  const active = value => ['PENDING', 'RUNNING'].includes(value?.status)
  async function loadHistory() {
    try { const value = await api('/quant/decision/runs'); if (!disposed) history.value = value }
    catch(e) { if (!disposed) error.value = e.message }
  }
  async function load() {
    if (!id || disposed) return
    clearTimeout(timer)
    const ticket = ++generation
    loading.value = true
    try {
      const value = await api('/quant/decision/runs/' + encodeURIComponent(id), {timeoutMs:60000})
      if (disposed || ticket !== generation) return
      state.value = value; error.value = ''
      if (active(value)) timer = setTimeout(load, 1500)
      else loadHistory()
    } catch(e) {
      if (disposed || ticket !== generation) return
      state.value = null; error.value = e.message
      if (!e.status || e.status >= 500) timer = setTimeout(load, 3000)
    } finally { if (!disposed && ticket === generation) loading.value = false }
  }
  async function start(conditions) {
    if (submitting.value || disposed) return
    clearTimeout(timer); generation++
    submitting.value = true; error.value = ''; state.value = null
    const values = ['market_scan','candidates'].includes(conditions.research_mode) ? {...conditions,enhanced_research:conditions.enhanced_research??deepResearch()} : conditions
    const signature = JSON.stringify(values)
    if (pending?.signature !== signature) pending = { signature, body:{ ...values, request_key:crypto.randomUUID() } }
    try {
      const value = await api('/quant/decision/runs', { method:'POST', body:JSON.stringify(pending.body), timeoutMs:45000 })
      if (!disposed) { pending = undefined; navigate(value.run_id) }
    } catch(e) { if (!disposed) error.value = e.message }
    finally { if (!disposed) submitting.value = false }
  }
  async function action(name) {
    if (name==='resume') return retry()
    if (!id || submitting.value || disposed) return
    clearTimeout(timer); generation++
    submitting.value = true
    try { await api('/quant/decision/runs/' + encodeURIComponent(id) + '/' + name, {method:'POST'}); if (!disposed) await load() }
    catch(e) { if (!disposed) { state.value = null; error.value = e.message } }
    finally { if (!disposed) submitting.value = false }
  }
  let refreshKey
  let retryKey
  async function retry() {
    if (!id || submitting.value || disposed || active(state.value)) return
    clearTimeout(timer); generation++; submitting.value=true; error.value=''
    retryKey ||= crypto.randomUUID()
    try {
      const value=await api('/quant/decision/runs/'+encodeURIComponent(id)+'/retry', {method:'POST',body:JSON.stringify({request_key:retryKey}),timeoutMs:45000})
      if(!disposed){retryKey=undefined;navigate(value.run_id)}
    } catch(e) {if(!disposed)error.value=e.message}
    finally {if(!disposed)submitting.value=false}
  }
  async function refresh() {
    if (!id || submitting.value || disposed) return
    submitting.value = true; error.value = ''
    refreshKey ||= crypto.randomUUID()
    try {
      const body = {request_key:refreshKey}
      if (['market_scan','candidates'].includes(state.value?.request?.research_mode)) body.enhanced_research=deepResearch()
      const value = await api('/quant/decision/runs/' + encodeURIComponent(id) + '/refresh', { method:'POST', body:JSON.stringify(body), timeoutMs:45000 })
      if (!disposed) { refreshKey = undefined; navigate(value.run_id) }
    } catch(e) { if (!disposed) error.value = e.message }
    finally { if (!disposed) submitting.value = false }
  }
  const mount = () => { loadHistory(); load() }
  const dispose = () => { disposed = true; generation++; clearTimeout(timer) }
  return { state, history, error, submitting, loading, start, load, action, refresh, retry, active, mount, dispose }
}

export function useDecisionResearch(id, options) {
  const controller = createDecisionResearch(id, options)
  onMounted(controller.mount)
  onUnmounted(controller.dispose)
  return controller
}
