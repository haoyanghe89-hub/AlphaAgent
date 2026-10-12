import { ref } from 'vue'
import request from '../api/request.js'

// A lost HTTP response must keep the same key on retry; switching pages retires it.
export function useFollowup(parent, { api = request, key = () => crypto.randomUUID(), started = () => {} } = {}) {
  const busy = ref(false), error = ref('')
  let disposed = false, attempt = null
  async function submit(question, parameters = {}) {
    const text = question.trim()
    if (!text || busy.value || disposed) return
    const fingerprint = JSON.stringify({ question: text, ...parameters })
    if (!attempt || JSON.stringify({ question: attempt.question, ...(attempt.dcf ? { dcf: attempt.dcf } : {}) }) !== fingerprint) attempt = { question: text, ...parameters, request_key: key() }
    busy.value = true; error.value = ''
    try {
      const result = await api('/quant/runs/' + parent + '/followups', { method: 'POST', body: JSON.stringify(attempt) })
      if (!disposed) { attempt = null; started(result) }
      return result
    } catch(e) {
      if (!disposed) { error.value = e.message; if (e.status >= 400 && e.status < 500) attempt = null }
    } finally { if (!disposed) busy.value = false }
  }
  return { busy, error, submit, dispose() { disposed = true } }
}
