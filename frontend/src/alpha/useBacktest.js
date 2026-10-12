import { ref } from 'vue'
import request from '../api/request.js'

// Conditions and the retry key stay paired after a lost response. No fake progress.
export function useBacktest({ api = request, key = () => crypto.randomUUID(), started = () => {} } = {}) {
  const data = ref(null), error = ref(''), busy = ref(false)
  let pending, historyPending, inputsPending, filingsPending, benchmarkPending, lifecyclePending, noticesPending, timer, disposed = false, generation = 0
  function schedule(id) {
    clearTimeout(timer)
    if (!disposed && data.value?.check_status === 'RUNNING') timer = setTimeout(() => load(id), 2000)
  }
  async function load(id) {
    if (busy.value) return
    busy.value = true
    const ticket = ++generation
    try {
      const result = await api('/quant/backtests/' + encodeURIComponent(id))
      if (disposed || ticket !== generation) return
      data.value = result; error.value = ''
      return result
    } catch (e) {
      if (!disposed && ticket === generation) { data.value = null; error.value = e.message }
    } finally { if (!disposed && ticket === generation) { busy.value = false; schedule(id) } }
  }
  async function submit(conditions) {
    if (busy.value) return
    const fingerprint = JSON.stringify(conditions)
    if (!pending || pending.fingerprint !== fingerprint) pending = { fingerprint, request_key: key() }
    busy.value = true; error.value = ''; data.value = null
    const ticket = ++generation
    try {
      const result = await api('/quant/backtests', { method: 'POST', body: JSON.stringify({ ...conditions, request_key: pending.request_key }) })
      if (disposed || ticket !== generation) return
      started(result)
      return result
    } catch (e) {
      if (!disposed && ticket === generation) error.value = e.message
    } finally { if (!disposed && ticket === generation) busy.value = false }
  }
  async function acquire(id) {
    if (busy.value) return
    if (!historyPending || historyPending.id !== id) historyPending = { id, request_key:key() }
    busy.value = true; error.value = ''
    clearTimeout(timer)
    const ticket = ++generation
    try {
      const result = await api('/quant/backtests/' + encodeURIComponent(id) + '/history', { method:'POST', body:JSON.stringify({request_key:historyPending.request_key}) })
      if (!disposed && ticket === generation) started(result)
      return result
    } catch (e) { if (!disposed && ticket === generation) error.value = e.message }
    finally { if (!disposed && ticket === generation) busy.value = false }
  }
  async function stop(id) {
    if (busy.value) return
    busy.value = true; clearTimeout(timer)
    const ticket = ++generation
    try {
      const result = await api('/quant/backtests/' + encodeURIComponent(id) + '/stop', {method:'POST'})
      if (!disposed && ticket === generation) { data.value = result; error.value = '' }
    } catch (e) { if (!disposed && ticket === generation) error.value = e.message }
    finally { if (!disposed && ticket === generation) { busy.value = false; schedule(id) } }
  }
  async function audit(id, instrument_id, signal_year) {
    if (busy.value) return
    const fingerprint = JSON.stringify([id, instrument_id, signal_year])
    if (!inputsPending || inputsPending.fingerprint !== fingerprint) inputsPending = { fingerprint, request_key:key() }
    busy.value = true; error.value = ''; clearTimeout(timer)
    const ticket = ++generation
    try {
      const result = await api('/quant/backtests/' + encodeURIComponent(id) + '/inputs', {
        method:'POST', body:JSON.stringify({ instrument_id, signal_year, request_key:inputsPending.request_key })
      })
      if (!disposed && ticket === generation) started(result)
      return result
    } catch (e) { if (!disposed && ticket === generation) error.value = e.message }
    finally { if (!disposed && ticket === generation) busy.value = false }
  }
  function dispose() { disposed = true; generation++; clearTimeout(timer) }
  async function filings(id) {
    if (busy.value) return
    if (!filingsPending || filingsPending.id !== id) filingsPending = {id, request_key:key()}
    busy.value = true; error.value = ''; clearTimeout(timer)
    const ticket = ++generation
    try {
      const result = await api('/quant/backtests/' + encodeURIComponent(id) + '/filings', {
        method:'POST', body:JSON.stringify({request_key:filingsPending.request_key})
      })
      if (!disposed && ticket === generation) started(result)
      return result
    } catch (e) { if (!disposed && ticket === generation) error.value = e.message }
    finally { if (!disposed && ticket === generation) busy.value = false }
  }
  async function benchmark(id) {
    if (busy.value) return
    if (!benchmarkPending || benchmarkPending.id !== id) benchmarkPending = {id, request_key:key()}
    busy.value = true; error.value = ''; clearTimeout(timer)
    const ticket = ++generation
    try {
      const result = await api('/quant/backtests/' + encodeURIComponent(id) + '/benchmark', {
        method:'POST', body:JSON.stringify({request_key:benchmarkPending.request_key})
      })
      if (!disposed && ticket === generation) started(result)
      return result
    } catch (e) { if (!disposed && ticket === generation) error.value = e.message }
    finally { if (!disposed && ticket === generation) busy.value = false }
  }
  async function lifecycles(id) {
    if (busy.value) return
    if (!lifecyclePending || lifecyclePending.id !== id) lifecyclePending = {id, request_key:key()}
    busy.value = true; error.value = ''; clearTimeout(timer)
    const ticket = ++generation
    try {
      const result = await api('/quant/backtests/' + encodeURIComponent(id) + '/lifecycles', {
        method:'POST', body:JSON.stringify({request_key:lifecyclePending.request_key})
      })
      if (!disposed && ticket === generation) started(result)
      return result
    } catch (e) { if (!disposed && ticket === generation) error.value = e.message }
    finally { if (!disposed && ticket === generation) busy.value = false }
  }
  async function notices(id) {
    if (busy.value) return
    if (!noticesPending || noticesPending.id !== id) noticesPending = {id, request_key:key()}
    busy.value = true; error.value = ''; clearTimeout(timer)
    const ticket = ++generation
    try {
      const result = await api('/quant/backtests/' + encodeURIComponent(id) + '/notices', {
        method:'POST', body:JSON.stringify({request_key:noticesPending.request_key})
      })
      if (!disposed && ticket === generation) started(result)
      return result
    } catch (e) { if (!disposed && ticket === generation) error.value = e.message }
    finally { if (!disposed && ticket === generation) busy.value = false }
  }
  return { data, error, busy, load, submit, acquire, audit, filings, benchmark, lifecycles, notices, stop, dispose }
}
