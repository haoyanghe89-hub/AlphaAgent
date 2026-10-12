import { onMounted, onUnmounted, ref, watch, unref } from 'vue'
import request from '../api/request.js'

export function useLiveQuotes(symbol) {
  const quotes = ref([]), retrievedAt = ref(''), error = ref(''), loading = ref(false)
  let disposed = false, timer, queued = false
  async function refresh() {
    if (disposed) return
    if (loading.value) { queued = true; return }
    const requestedSymbol = unref(symbol)
    loading.value = true
    try {
      const data = await request('/quant/research/quotes' + (requestedSymbol ? '?symbol=' + encodeURIComponent(requestedSymbol) : ''), { timeoutMs: 25000 })
      if (!disposed && requestedSymbol === unref(symbol)) { quotes.value = data.quotes; retrievedAt.value = data.retrieved_at; error.value = '' }
    } catch (e) { if (!disposed && requestedSymbol === unref(symbol)) { error.value = e.message; quotes.value = [] } }
    finally { loading.value = false; if (queued && !disposed) { queued = false; refresh() } }
  }
  if (symbol) watch(symbol, () => { quotes.value = []; retrievedAt.value = ''; error.value = ''; refresh() })
  onMounted(() => { refresh(); timer = setInterval(() => { if (!document.hidden) refresh() }, 30000) })
  onUnmounted(() => { disposed = true; clearInterval(timer) })
  return { quotes, retrievedAt, error, loading, refresh }
}
