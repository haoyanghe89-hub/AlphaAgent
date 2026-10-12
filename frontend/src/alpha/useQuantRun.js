import { onUnmounted, ref } from 'vue'
import request from '../api/request.js'
import { isActive } from './model.js'

export function useQuantRun(id, { api = request, schedule = setTimeout, unschedule = clearTimeout, unmount = onUnmounted } = {}) {
  const data = ref(null), report = ref(null), error = ref(''), loading = ref(true)
  let timer, ticket = 0, disposed = false, failures = 0
  async function load() {
    unschedule(timer)
    const current = ++ticket
    try {
      const result = await api('/quant/runs/' + encodeURIComponent(id))
      if (disposed || current !== ticket) return
      data.value = result
      error.value = ''
      failures = 0
      if (result.report_id && report.value?.report_id !== result.report_id) {
        const body = await api('/reports/' + encodeURIComponent(result.report_id))
        if (disposed || current !== ticket) return
        report.value = { ...body.report, sections: body.sections }
      }
      if (isActive(result.run.status)) timer = schedule(load, 2000)
    } catch (e) {
      if (disposed || current !== ticket) return
      error.value = e.message
      if ([404, 409].includes(e.status)) { data.value = null; report.value = null }
      else timer = schedule(load, Math.min(15000, 2000 * 2 ** Math.min(failures++, 3)))
    } finally { if (!disposed && current === ticket) loading.value = false }
  }
  function dispose() { disposed = true; ticket++; unschedule(timer) }
  unmount(dispose)
  return { data, report, error, loading, load, dispose }
}
