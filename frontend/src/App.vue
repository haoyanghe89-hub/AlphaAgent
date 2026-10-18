<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import request from './api/request.js'
import {postSavedTask} from './api/savedTask.js'
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
import DecisionResearchView from './alpha/DecisionResearchView.vue'
import IndustryCandidates from './alpha/IndustryCandidates.vue'
import MarketResearch from './alpha/MarketResearch.vue'
import KnowledgeAudit from './alpha/KnowledgeAudit.vue'
import InvestmentPlan from './alpha/InvestmentPlan.vue'
import DailyWorkspace from './alpha/DailyWorkspace.vue'
import HoldingAdvisor from './alpha/HoldingAdvisor.vue'
import PortfolioAdvisor from './alpha/PortfolioAdvisor.vue'
import DeveloperWorkspace from './alpha/DeveloperWorkspace.vue'
import {developerHref,investmentIntent} from './alpha/uiPresentation.js'
import { portfolioIntent } from './alpha/usePortfolioAdvisor.js'
import { holdingIntent } from './alpha/useHoldingAdvisor.js'
import { useLiveQuotes } from './alpha/useLiveQuotes.js'
import { workbenchIntent, decisionConditions, investmentDraft } from './alpha/workbenchIntent.js'
import { nextSessionRoot } from './alpha/useNextSessionPrediction.js'
const { route, go } = useNavigation(), { theme, toggleTheme } = useTheme()
const developer = new URLSearchParams(location.search).get('developer') === '1'
const developerHome=developerHref(location.href,true,'developer')
const modeHref=computed(()=>{route.value;return developerHref(location.href,!developer,developer&&['developer','knowledge','delivery'].includes(route.value.page)?'workbench':undefined)})
const selectedSymbol = ref('sz000333')
const quoteSymbols = computed(() => route.value.page === 'workbench' ? 'sz000333,sz000651,sh600519,sz000001' : selectedSymbol.value)
const live = useLiveQuotes(quoteSymbols)
const asking = ref(false)
let questionRequest
const stocks = ref([]), indices = ref([]), history = ref([]), archives = ref([]), loading = ref(false), error = ref(''), questionError = ref(''), pending = ref(null), hasMore = ref(false)
const quoteNames = ['上证指数','深证成指','创业板指','沪深300','科创50']
const quotes = computed(() => quoteNames.map(name => live.quotes.value.find(i => i.name === name) || { name }))
let generation = 0, disposed = false
let historyCursor=null, legacyOffset=0, legacyMore=false, historyQuery=''
async function load(more = false) {
  const ticket = ++generation
  loading.value = true; error.value = ''
  const previous=more?[...history.value]:[],cursor=more?historyCursor:null,offset=more?legacyOffset:0
  const query=historyQuery
  let records=[],runs=[]
  if(!more){historyCursor=null;legacyOffset=0;legacyMore=false;hasMore.value=false}
  function show(){
    history.value=[...new Map([...previous,...records,...runs].map(item=>[item.run_id,item])).values()].sort((a,b)=>new Date(b.created_at)-new Date(a.created_at))
    hasMore.value=Boolean(historyCursor)||legacyMore
  }
  try {
    const tasks=[(more&&!cursor?Promise.resolve({items:[],next_cursor:null}):request('/quant/daily/history?limit=50&q='+encodeURIComponent(query)+(cursor?'&cursor='+encodeURIComponent(cursor):''))).then(value=>{
      if(disposed||ticket!==generation)return
      records=value.items;historyCursor=value.next_cursor;show()
    })]
    if(!query)tasks.push(
      request('/quant/overview').then(overview => {
        if (disposed || ticket !== generation) return
        stocks.value = overview.stocks
        indices.value = overview.indices || []
      }),
      request('/investigations').then(old=>{if(!disposed&&ticket===generation)archives.value=old}),
      request('/quant/runs?limit=100&offset='+offset).then(value => {
        if (disposed || ticket !== generation) return
        const titles = new Map(archives.value.map(i => [i.investigation_id, i.title]))
        runs=value.map(run=>({...run,title:run.question||titles.get(run.investigation_id)||'单股研究 · '+run.run_id}))
        legacyOffset=offset+value.length;legacyMore=value.length===100;show()
      }),
    )
    const outcomes = await Promise.allSettled(tasks)
    const failures = outcomes.filter(outcome => outcome.status === 'rejected')
    if (failures.length) throw new Error(failures.map(outcome => outcome.reason.message).join('；'))
  } catch(e) { if (!disposed && ticket === generation) error.value = e.message }
  finally { if (!disposed && ticket === generation) loading.value = false }
}
function searchHistory(query){historyQuery=query;load()}
const legacy = computed(() => archives.value.filter(i => !history.value.some(run => run.investigation_id === i.investigation_id || run.branch_investigation_ids?.includes(i.investigation_id))))
const stock = computed(() => stocks.value.find(i => i.code === route.value.id))
async function ask(text, selected) {
  questionError.value = ''
  if (asking.value || !text.trim()) return
  if(investmentIntent(text,selected)){
    asking.value=true
    const origin=location.hash
    const deadline=Date.now()+30000
    try{
      const draft=await investmentDraft(text,q=>{
        const timeoutMs=deadline-Date.now()
        if(timeoutMs<=0)throw Error('证券目录读取超时，请重试或用空格分隔准确股票名称。')
        return request('/quant/research/securities?q='+encodeURIComponent(q),{timeoutMs})
      })
      if(disposed||location.hash!==origin)return
      sessionStorage.setItem('alpha-investment-draft',JSON.stringify(draft));go('investment-research')
    }catch(e){questionError.value=e.message}
    finally{asking.value=false}
    return
  }
  if(portfolioIntent(text,selected)){sessionStorage.setItem('alpha-portfolio-question',text);go('portfolio-advisor');return}
  const holding=holdingIntent(text,selected)
  if(holding){sessionStorage.setItem('alpha-holding-question',JSON.stringify(holding));go('holding-advisor');return}
  if (/回测|年度调仓|全市场选股/.test(text)) { go('strategy'); return }
  if (/\bvs\b|对比|比较/i.test(text) && !/行业|市场/.test(text)) {
    sessionStorage.setItem('alpha-comparison-question',text)
    go('compare'); return
  }
  asking.value = true
  try {
    const intent=workbenchIntent(text,selected)
    const signature=JSON.stringify({text,mode:intent?.mode})
    if (questionRequest?.signature !== signature) questionRequest = { signature, key:crypto.randomUUID() }
    let path='/quant/research/runs',body={question:text},page='live'
    if(intent?.mode==='next') {
      const status=await request(nextSessionRoot+'/status')
      if(!status.enabled)await request(nextSessionRoot+'/enable',{method:'POST',body:'{}',timeoutMs:60000})
      path=nextSessionRoot+'/runs';page='decision'
      body={experiment_id:status.experiment_id,entity:intent.entity,exploratory:true}
    } else if(intent) {
      path='/quant/decision/runs';page=intent.mode==='medium'?'decision':intent.mode==='market'?'market':'candidates'
      body=decisionConditions(intent)
    }
    const options={method:'POST',body:JSON.stringify({...body,request_key:questionRequest.key}),timeoutMs:45000}
    const result=await postSavedTask(request,path,options,{disposed:()=>disposed,waiting:m=>{questionError.value=m}})
    if(disposed)return
    questionRequest=null;go(page,result.run_id); load()
  } catch(e) { questionError.value = e.message }
  finally { asking.value = false }
}
function started(result) { pending.value = null; questionError.value = ''; go('research', result.run_id); load() }
watch(() => route.value.page, page => { questionError.value = ''; if (['workbench', 'library'].includes(page)) {historyQuery='';load()} })
onMounted(() => load()); onUnmounted(() => { disposed = true; generation++ })
</script>
<template>
  <div class="alpha-app">
    <header class="topbar"><a class="brand" :href="href('workbench')"><span class="brand-mark">α</span> AlphaAgent</a><nav class="topnav" aria-label="主导航"><a :href="href('workbench')" :class="{ active: ['workbench','decision','market','candidates','morning','investment','investment-research','holding-advisor','portfolio-advisor'].includes(route.page) }">工作台</a><a :href="href('library')" :class="{ active: route.page === 'library' }">历史库</a></nav><div class="quote-strip" aria-label="指数行情"><span v-for="quote in quotes" :key="quote.name" class="qs-item" :title="quote.quoted_at ? '腾讯报价时间：' + quote.quoted_at : live.error.value || '行情读取中'"><span class="qs-name">{{ quote.name }}</span><span :class="['num',quote.change > 0 ? 'up' : 'down']">{{ quote.price ?? '—' }}</span><span class="num qs-change">{{ quote.change != null ? (quote.change > 0 ? '+' : '') + quote.change + '%' : '—' }}</span></span></div><a class="developer-shortcut" :href="developerHome" :aria-current="route.page === 'developer' ? 'page' : undefined">{{ developer?'开发者工作区':'开发者' }}</a><button class="theme-button" :aria-label="theme === 'dark' ? '切换浅色' : '切换深色'" @click="toggleTheme"><span aria-hidden="true">{{ theme === 'dark' ? '☀' : '☾' }}</span><span>{{ theme === 'dark' ? '浅色' : '深色' }}</span></button></header>
    <main id="main"><div v-if="questionError" class="page question-error"><p class="notice error-text" role="alert">{{ questionError }}<button class="icon-button" aria-label="关闭问题提示" @click="questionError = ''">×</button></p></div>
      <Workbench v-if="route.page === 'workbench'" :stocks="stocks" :live-quotes="live.quotes.value" :quote-time="live.retrievedAt.value" :quote-error="live.error.value" :history="history" :loading="asking" :history-loading="loading" :error="error" @ask="ask" @retry="load()" />
      <div v-else-if="route.page==='morning'" class="page"><DailyWorkspace :key="route.id" :id="route.id" /></div>
      <HoldingAdvisor v-else-if="route.page==='holding-advisor'" :key="route.id||'new'" :id="route.id" @started="r=>{go('holding-advisor',r.run_id);load()}" />
      <PortfolioAdvisor v-else-if="route.page==='portfolio-advisor'" :key="route.id||'new'" :id="route.id" @started="r=>{go('portfolio-advisor',r.run_id);load()}" />
      <LiveResearchView v-else-if="route.page === 'live' || route.page === 'stock'" :key="'live-' + route.id" :id="route.page === 'stock' ? 'new' : route.id" :code="route.page === 'stock' ? route.id : undefined" :quotes="live.quotes.value" :quote-time="live.retrievedAt.value" :quote-error="live.error.value" :quote-loading="live.loading.value" @refresh-quotes="live.refresh" @subject="value => { selectedSymbol = value; live.refresh() }" />
      <LiveComparison v-else-if="route.page === 'compare'" :key="'compare-' + (route.id || 'new')" :id="route.id" @started="r => { go('compare',r.run_id); load() }" />
      <StrategyView v-else-if="route.page === 'strategy'" :key="'strategy-' + (route.id || 'new')" :id="route.id" @started="r => { go('strategy',r.run_id); load() }" />
      <DeveloperWorkspace v-else-if="route.page==='developer'||route.page==='delivery'&&!developer" :enabled="developer" />
      <DeliveryView v-else-if="route.page === 'delivery'&&developer" />
      <KnowledgeAudit v-else-if="route.page === 'knowledge' && developer" />
      <InvestmentPlan v-else-if="['investment','investment-research'].includes(route.page)" :key="route.page+'-'+(route.id||'new')" :id="route.id" :research="route.page==='investment-research'" @started="r=>{go(route.page,r.run_id);load()}" />
      <div v-else-if="route.page === 'knowledge'" class="page"><div class="panel rep-section"><p>投资知识作为系统内置能力维护。</p><a class="btn" href="#/">返回工作台</a></div></div>
      <DecisionResearchView v-else-if="route.page === 'decision'" :key="'decision-' + (route.id || 'new')" :id="route.id" />
      <MarketResearch v-else-if="route.page === 'market'" :key="'market-' + (route.id || 'new')" :id="route.id" />
      <IndustryCandidates v-else-if="route.page === 'candidates'" :key="'candidates-' + (route.id || 'new')" :id="route.id" />
      <ResearchView v-else-if="route.page === 'research'" :key="'research-' + route.id" :id="route.id" />
      <ReportView v-else-if="route.page === 'report'" :key="'report-' + route.id" :id="route.id" />
      <StockView v-else-if="route.page === 'stock' && stock" :key="stock.code + stock.run_id" :stock="stock" @ask="ask" />
      <LibraryView v-else-if="route.page === 'library'" :items="history" :legacy="legacy" :loading="loading" :error="error" :has-more="hasMore" @retry="load()" @more="load(true)" @search="searchHistory" />
      <LegacyView v-else-if="route.page === 'legacy'" :key="route.id" :id="route.id" />
      <BacktestView v-else-if="route.page === 'backtest'" :key="'backtest-' + (route.id || 'new')" :id="route.id" @started="result => { go('backtest', result.run_id); load() }" />
      <div v-else class="page"><div class="panel empty-state">{{ loading ? '正在读取…' : route.page === 'stock' ? '标的尚未接入或没有匹配的证券主数据。' : '页面不存在。' }}<a :href="href('workbench')">返回工作台</a><p v-if="error" class="error-text" role="alert">{{ error }}</p></div></div>
    </main>
    <footer class="app-footer"><span>AlphaAgent · 研究与历史持续保存</span><a :href="modeHref">{{ developer?'返回用户视图':'本页分析与核验' }}</a><a v-if="!developer" :href="developerHome">开发者入口</a><a v-else href="#/developer">开发者工作区</a></footer>
    <ScopeDialog v-if="pending" :question="pending.question" :instrument="pending.instrument" :instruments="pending.instruments" :window="pending.window" :date-start="pending.date_start" :date-end="pending.date_end" @close="pending = null" @started="started" />
  </div>
</template>
