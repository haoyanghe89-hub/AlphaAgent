<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import request from './api/request.js'
import { useTheme } from './composables/useTheme.js'
import { href, useNavigation } from './alpha/navigation.js'
import Workbench from './alpha/Workbench.vue'
import ResearchView from './alpha/ResearchView.vue'
import ReportView from './alpha/ReportView.vue'
import StockView from './alpha/StockView.vue'
import LibraryView from './alpha/LibraryView.vue'
import LegacyView from './alpha/LegacyView.vue'
import BacktestView from './alpha/BacktestView.vue'
import ScopeDialog from './alpha/ScopeDialog.vue'
import LiveResearchView from './alpha/LiveResearchView.vue'
import LiveComparison from './alpha/LiveComparison.vue'
import StrategyView from './alpha/StrategyView.vue'
import DeliveryView from './alpha/DeliveryView.vue'
import { useLiveQuotes } from './alpha/useLiveQuotes.js'
const { route, go } = useNavigation(), { theme, toggleTheme } = useTheme()
const selectedSymbol = ref('sz000333')
const quoteSymbols = computed(() => route.value.page === 'workbench' ? 'sz000333,sz000651,sh600519,sz000001' : selectedSymbol.value)
const live = useLiveQuotes(quoteSymbols)
const asking = ref(false)
let questionRequest
const stocks = ref([]), indices = ref([]), history = ref([]), archives = ref([]), loading = ref(false), error = ref(''), questionError = ref(''), pending = ref(null), hasMore = ref(false)
const quoteNames = ['上证指数','深证成指','创业板指','沪深300','科创50']
const quotes = computed(() => quoteNames.map(name => live.quotes.value.find(i => i.name === name) || { name }))
let generation = 0, disposed = false
async function load(more = false) {
  const ticket = ++generation
  loading.value = true; error.value = ''
  try {
    const outcomes = await Promise.allSettled([
      request('/quant/overview').then(overview => {
        if (disposed || ticket !== generation) return
        stocks.value = overview.stocks
        indices.value = overview.indices || []
      }),
      Promise.all([request('/quant/runs?limit=100&offset=' + (more ? history.value.filter(i => !['live','compare_live','strategy'].includes(i.scenario)).length : 0)), request('/investigations'), request('/quant/research/runs')]).then(([runs, old, fresh]) => {
        if (disposed || ticket !== generation) return
        archives.value = old
        const titles = new Map(old.map(i => [i.investigation_id, i.title]))
        const result = runs.map(run => ({ ...run, title: run.question || titles.get(run.investigation_id) || '单股研究 · ' + run.run_id }))
        history.value = (more ? [...history.value, ...result] : [...fresh, ...result]).sort((a,b) => new Date(b.created_at) - new Date(a.created_at))
        hasMore.value = runs.length === 100
      }),
    ])
    const failures = outcomes.filter(outcome => outcome.status === 'rejected')
    if (failures.length) throw new Error(failures.map(outcome => outcome.reason.message).join('；'))
  } catch(e) { if (!disposed && ticket === generation) error.value = e.message }
  finally { if (!disposed && ticket === generation) loading.value = false }
}
const legacy = computed(() => archives.value.filter(i => !history.value.some(run => run.investigation_id === i.investigation_id || run.branch_investigation_ids?.includes(i.investigation_id))))
const stock = computed(() => stocks.value.find(i => i.code === route.value.id))
async function ask(text) {
  questionError.value = ''
  if (asking.value || !text.trim()) return
  if (/回测|年度调仓|全市场选股/.test(text)) { go('strategy'); return }
  if (/\bvs\b|对比|比较/i.test(text)) {
    sessionStorage.setItem('alpha-comparison-question',text)
    go('compare'); return
  }
  asking.value = true
  try {
    if (questionRequest?.text !== text) questionRequest = { text, key:crypto.randomUUID() }
    const result = await request('/quant/research/runs', {method:'POST', body:JSON.stringify({request_key:questionRequest.key,question:text})})
    go('live',result.run_id); load()
  } catch(e) { questionError.value = e.message }
  finally { asking.value = false }
}
function started(result) { pending.value = null; questionError.value = ''; go('research', result.run_id); load() }
watch(() => route.value.page, page => { questionError.value = ''; if (['workbench', 'library'].includes(page)) load() })
onMounted(() => load()); onUnmounted(() => { disposed = true; generation++ })
</script>
<template>
  <div class="alpha-app">
    <header class="topbar"><a class="brand" :href="href('workbench')"><span class="brand-mark">α</span> AlphaAgent</a><nav class="topnav" aria-label="主导航"><a :href="href('workbench')" :class="{ active: route.page === 'workbench' }">工作台</a><a :href="href('strategy')" :class="{ active: ['backtest','strategy'].includes(route.page) }">策略回测</a><a :href="href('library')" :class="{ active: route.page === 'library' }">历史库</a></nav><div class="quote-strip" aria-label="指数行情"><span v-for="quote in quotes" :key="quote.name" class="qs-item" :title="quote.quoted_at ? '腾讯报价时间：' + quote.quoted_at : live.error.value || '行情读取中'"><span class="qs-name">{{ quote.name }}</span><span :class="['num',quote.change > 0 ? 'up' : 'down']">{{ quote.price ?? '—' }}</span><span class="num qs-change">{{ quote.change != null ? (quote.change > 0 ? '+' : '') + quote.change + '%' : '—' }}</span></span></div><button class="theme-button" :aria-label="theme === 'dark' ? '切换浅色' : '切换深色'" @click="toggleTheme"><span aria-hidden="true">{{ theme === 'dark' ? '☀' : '☾' }}</span><span>{{ theme === 'dark' ? '浅色' : '深色' }}</span></button></header>
    <main id="main"><div v-if="questionError" class="page question-error"><p class="notice error-text" role="alert">{{ questionError }}<button class="icon-button" aria-label="关闭问题提示" @click="questionError = ''">×</button></p></div>
      <Workbench v-if="route.page === 'workbench'" :stocks="stocks" :live-quotes="live.quotes.value" :quote-time="live.retrievedAt.value" :quote-error="live.error.value" :history="history" :loading="loading || asking" :error="error" @ask="ask" @retry="load()" />
      <LiveResearchView v-else-if="route.page === 'live' || route.page === 'stock'" :key="'live-' + route.id" :id="route.page === 'stock' ? 'new' : route.id" :code="route.page === 'stock' ? route.id : undefined" :quotes="live.quotes.value" :quote-time="live.retrievedAt.value" :quote-error="live.error.value" :quote-loading="live.loading.value" @refresh-quotes="live.refresh" @subject="value => { selectedSymbol = value; live.refresh() }" />
      <LiveComparison v-else-if="route.page === 'compare'" :key="'compare-' + (route.id || 'new')" :id="route.id" @started="r => { go('compare',r.run_id); load() }" />
      <StrategyView v-else-if="route.page === 'strategy'" :key="'strategy-' + (route.id || 'new')" :id="route.id" @started="r => { go('strategy',r.run_id); load() }" />
      <DeliveryView v-else-if="route.page === 'delivery'" />
      <ResearchView v-else-if="route.page === 'research'" :key="'research-' + route.id" :id="route.id" />
      <ReportView v-else-if="route.page === 'report'" :key="'report-' + route.id" :id="route.id" />
      <StockView v-else-if="route.page === 'stock' && stock" :key="stock.code + stock.run_id" :stock="stock" @ask="ask" />
      <LibraryView v-else-if="route.page === 'library'" :items="history" :legacy="legacy" :loading="loading" :error="error" :has-more="hasMore" @retry="load()" @more="load(true)" />
      <LegacyView v-else-if="route.page === 'legacy'" :key="route.id" :id="route.id" />
      <BacktestView v-else-if="route.page === 'backtest'" :key="'backtest-' + (route.id || 'new')" :id="route.id" @started="result => { go('backtest', result.run_id); load() }" />
      <div v-else class="page"><div class="panel empty-state">{{ loading ? '正在读取…' : route.page === 'stock' ? '标的尚未接入或没有匹配的证券主数据。' : '页面不存在。' }}<a :href="href('workbench')">返回工作台</a><p v-if="error" class="error-text" role="alert">{{ error }}</p></div></div>
    </main>
    <ScopeDialog v-if="pending" :question="pending.question" :instrument="pending.instrument" :instruments="pending.instruments" :window="pending.window" :date-start="pending.date_start" :date-end="pending.date_end" @close="pending = null" @started="started" />
  </div>
</template>
