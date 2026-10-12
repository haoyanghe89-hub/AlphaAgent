import { ref } from 'vue'
import request from '../api/request.js'

export function useMarket({ api = request, key = () => crypto.randomUUID() } = {}) {
  const data = ref(null), error = ref(''), busy = ref(false)
  let disposed = false, generation = 0, pending, timer
  async function load(id, offset = 0) {
    if (busy.value || disposed) return
    busy.value = true
    const ticket = ++generation
    clearTimeout(timer)
    try {
      const result = await api(`/quant/backtests/${encodeURIComponent(id)}/market?offset=${offset}&limit=25`)
      if (disposed || ticket !== generation) return
      data.value = result; error.value = ''
    } catch (e) {
      if (!disposed && ticket === generation) { data.value = null; error.value = e.message }
    } finally {
      if (!disposed && ticket === generation) {
        busy.value = false
        if (data.value?.acquisition_status === 'RUNNING') timer = setTimeout(() => load(id, offset), 5000)
      }
    }
  }
  async function start(id, batches = 1) {
    if (busy.value || disposed) return
    const fingerprint = JSON.stringify([id, batches])
    if (!pending || pending.fingerprint !== fingerprint) pending = { fingerprint, request_key:key() }
    busy.value = true; clearTimeout(timer)
    const ticket = ++generation
    let refresh = false
    try {
      await api(`/quant/backtests/${encodeURIComponent(id)}/market`, {
        method:'POST', body:JSON.stringify({request_key:pending.request_key, batches}), timeoutMs:60000,
      })
      if (!disposed && ticket === generation) { pending = null; refresh = true; error.value = '' }
    } catch (e) { if (!disposed && ticket === generation) error.value = e.message }
    finally { if (!disposed && ticket === generation) busy.value = false }
    if (refresh) await load(id, data.value?.coverage?.offset || 0)
  }
  async function stop(id) {
    if (busy.value || disposed) return
    busy.value = true; clearTimeout(timer)
    const ticket = ++generation
    try {
      const result = await api(`/quant/backtests/${encodeURIComponent(id)}/market/stop`, {method:'POST'})
      if (!disposed && ticket === generation) { data.value = result; error.value = '' }
    } catch (e) { if (!disposed && ticket === generation) error.value = e.message }
    finally { if (!disposed && ticket === generation) busy.value = false }
  }
  function dispose() { disposed = true; generation++; clearTimeout(timer) }
  return {data, error, busy, load, start, stop, dispose}
}
