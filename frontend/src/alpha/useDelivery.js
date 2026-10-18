import { ref } from 'vue'
import request from '../api/request.js'

export function useDelivery(started = () => {}, deps = {}) {
  const api = deps.api || request, schedule = deps.schedule || setTimeout, unschedule = deps.unschedule || clearTimeout
  const data = ref(null), error = ref(''), busy = ref(false)
  let timer, generation = 0, disposed = false, retry
  function later(id) { if (!disposed) { unschedule(timer); timer = schedule(() => load(id), 1500) } }
  async function load(id) {
    const ticket = ++generation
    try {
      const value = await api('/quant/deliveries/' + encodeURIComponent(id))
      if (disposed || ticket !== generation) return
      data.value = value; error.value = ''
      if (value.status === 'RUNNING') later(id)
    } catch (e) { if (!disposed && ticket === generation) { error.value = e.message; if ([404,409].includes(e.status)) data.value = null } }
  }
  async function submit(path, values) {
    if (busy.value) return
    const identity = JSON.stringify({ path, values })
    if (retry?.identity !== identity) retry = { identity, key: crypto.randomUUID() }
    busy.value = true; error.value = ''
    try {
      const value = await api('/quant/' + path, { method:'POST', body:JSON.stringify({ ...values, request_key:retry.key }) })
      if (disposed) return
      data.value = value; retry = undefined; started(value); later(value.run_id)
    } catch (e) { if (!disposed) error.value = e.message }
    finally { busy.value = false }
  }
  async function resume(id) {
    busy.value = true; error.value = ''
    try { await api('/quant/deliveries/' + encodeURIComponent(id) + '/resume', { method:'POST' }); await load(id) }
    catch(e) { error.value = e.message }
    finally { busy.value = false }
  }
  function dispose() { disposed = true; generation++; unschedule(timer) }
  return { data, error, busy, load, submit, resume, dispose }
}

export function comparisonEntities(question) {
  const clean = question.trim().replace(/^(请|帮我|麻烦|比较一下|对比一下|比较|对比|分析|全面分析|研究)+/, '')
    .replace(/(的)?(对比|比较|谁更好|哪个好|怎么样|分析)(.*)$/, '')
  const parts = clean.split(/\s+vs\.?\s+|和|与|、|对比|比较/i).map(x => x.trim().replace(/[，。！？,;；?!.]+$/, '').trim()).filter(Boolean)
  return parts.length === 2 ? parts : ['', '']
}

export function deliveryPage(item) {
  if (item.scenario === 'portfolio_advisor') return 'portfolio-advisor'
  if (item.scenario === 'holding_advisor') return 'holding-advisor'
  if (item.scenario === 'morning_brief') return 'morning'
  if (item.scenario === 'investment_research') return 'investment-research'
  if (item.scenario === 'investment') return 'investment'
  if (item.scenario === 'next_session') return 'decision'
  if (item.scenario === 'decision') return item.page || 'decision'
  return item.scenario === 'compare_live' ? 'compare' : item.scenario === 'strategy' ? 'strategy'
    : item.scenario === 'live' ? 'live' : item.scenario === 'backtest' ? 'backtest'
      : item.status === 'COMPLETED' ? 'report' : 'research'
}
