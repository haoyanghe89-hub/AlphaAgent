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
import EvaluationComparison from './alpha/EvaluationComparison.vue'
import {developerHref} from './alpha/uiPresentation.js'
import { portfolioIntent } from './alpha/usePortfolioAdvisor.js'
import { holdingIntent } from './alpha/useHoldingAdvisor.js'
import { useLiveQuotes } from './alpha/useLiveQuotes.js'
import { decisionConditions } from './alpha/workbenchIntent.js'
import { nextSessionRoot } from './alpha/useNextSessionPrediction.js'
import {deepResearch} from './alpha/researchDepth.js'
import {createQuestionUnderstanding,readUnderstanding,saveUnderstanding,showUnderstandingForRoute,publicTaskQuestion,taskFormDraft,taskLabel} from './alpha/questionUnderstanding.js'
const { route, go } = useNavigation(), { theme, toggleTheme } = useTheme()
const developer = new URLSearchParams(location.search).get('developer') === '1'
const developerHome=developerHref(location.href,true,'developer')
const modeHref=computed(()=>{route.value;return developerHref(location.href,!developer,developer&&['developer','knowledge','delivery'].includes(route.value.page)?'workbench':undefined)})
const selectedSymbol = ref('sz000333')
const quoteSymbols = computed(() => route.value.page === 'workbench' ? 'sz000333,sz000651,sh600519,sz000001' : selectedSymbol.value)
const live = useLiveQuotes(quoteSymbols)
const asking = ref(false)
const semantic=ref(readUnderstanding(sessionStorage)),understandingNotice=ref(''),fallbackAvailable=ref(false)
const showSemantic=computed(()=>showUnderstandingForRoute(semantic.value,route.value))
const dispatchRevision=ref(0)
const understand=createQuestionUnderstanding(request,{storage:sessionStorage,disposed:()=>disposed,waiting:message=>{understandingNotice.value=message}})
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
async function runTask(task,index=0,originalQuestion='') {
  originalQuestion=originalQuestion||semantic.value?.question||''
  if(task.mode==='clarify'){questionError.value='请补充要分析的股票、期限或具体目标，再发送问题。';return}
  if(semantic.value){semantic.value.active=index;saveUnderstanding(sessionStorage,semantic.value)}
  if(['investment','portfolio','holding','compare'].includes(task.mode))dispatchRevision.value++
  if(task.mode==='investment'){
    sessionStorage.setItem('alpha-investment-draft',JSON.stringify(taskFormDraft(task)));go('investment-research');return
  }
  if(task.mode==='portfolio'){sessionStorage.setItem('alpha-portfolio-question',originalQuestion||publicTaskQuestion(task));go('portfolio-advisor');return}
  if(task.mode==='holding'){
    sessionStorage.setItem('alpha-holding-question',JSON.stringify(taskFormDraft(task,originalQuestion)));go('holding-advisor');return
  }
  if(task.mode==='strategy'){go('strategy');return}
  if(task.mode==='compare'){
    if(task.entities.length!==2)throw Error('股票比较需要明确两只股票。')
    sessionStorage.setItem('alpha-comparison-question',task.entities.join(' vs '));go('compare');return
  }
  if(['next','medium'].includes(task.mode)&&!task.entities.length)throw Error('请补充要研究的准确股票名称或六位代码。')
  const question=publicTaskQuestion(task)
  const intent={mode:task.mode,entity:task.entities[0],question}
  if(task.mode==='candidates'){
    intent.entity=task.industry==='equipment'?'半导体设备':task.industry==='manufacturing'?'集成电路制造':''
    if(!intent.entity)throw Error('请指定半导体设备或集成电路制造；其他行业候选仍可从首页行情查看。')
  }
  let path='/quant/decision/runs',page=task.mode==='medium'?'decision':task.mode==='market'?'market':'candidates',body=decisionConditions(intent)
  if(task.mode==='next'){
    const status=await request(nextSessionRoot+'/status')
    if(!status.enabled)await request(nextSessionRoot+'/enable',{method:'POST',body:'{}',timeoutMs:60000})
    path=nextSessionRoot+'/runs';page='decision';body={experiment_id:status.experiment_id,entity:task.entities[0],exploratory:true,...(deepResearch()?{enhanced_research:true}:{})}
  }
  const signature=JSON.stringify({semantic:semantic.value?.run_id,index,body})
  if(questionRequest?.signature!==signature)questionRequest={signature,key:crypto.randomUUID()}
  const result=await postSavedTask(request,path,{method:'POST',body:JSON.stringify({...body,request_key:questionRequest.key}),timeoutMs:45000},{disposed:()=>disposed,waiting:message=>{understandingNotice.value=message}})
  if(disposed)return
  questionRequest=null
  if(semantic.value){semantic.value.tasks[index]={...task,result_page:page,result_id:result.run_id};saveUnderstanding(sessionStorage,semantic.value)}
  go(page,result.run_id);load()
}
async function openTask(task,index){
  if(asking.value)return
  if(task.result_id){go(task.result_page,task.result_id);return}
  asking.value=true;questionError.value=''
  try{await runTask(task,index)}catch(e){questionError.value=e.message}finally{asking.value=false}
}
function dismissUnderstanding(){semantic.value=null;sessionStorage.removeItem('alpha-question-understanding')}
function advisorStarted(page,result){
  if(semantic.value){const index=semantic.value.active,task=semantic.value.tasks[index],expected={holding:'holding-advisor',portfolio:'portfolio-advisor',investment:'investment-research',compare:'compare'};if(task&&expected[task.mode]===page){semantic.value.tasks[index]={...task,result_page:page,result_id:result.run_id};saveUnderstanding(sessionStorage,semantic.value)}}
  go(page,result.run_id);load()
}
async function ask(text,selected){
  if(asking.value||!text.trim())return
  asking.value=true;questionError.value='';fallbackAvailable.value=false;understandingNotice.value='正在理解问题、条件与前文指代…'
  const origin=location.hash
  let understood=false
  try{
    const result=await understand(text,selected)
    if(!result||disposed||location.hash!==origin)return
    semantic.value=result;understandingNotice.value='';understood=true
    await runTask(result.tasks[0],0,text)
  }catch(e){
    if(understood){questionError.value=e.message;return}
    fallbackAvailable.value=true
    understandingNotice.value='本次未使用模型理解：'+e.message+' 可使用原有入口继续。'
    // Private and ambiguous questions stay in local confirmation forms on fallback.
    if(portfolioIntent(text,selected)){sessionStorage.setItem('alpha-portfolio-question',text);go('portfolio-advisor')}
    else if(holdingIntent(text,selected)){sessionStorage.setItem('alpha-holding-question',JSON.stringify(holdingIntent(text,selected)));go('holding-advisor')}
    else questionError.value='请使用下方明确的功能入口，或补充股票名称与目标后重试。原输入未作为公开研究问题发送。'
  }finally{asking.value=false}
}
function started(result) { pending.value = null; questionError.value = ''; go('research', result.run_id); load() }
watch(() => route.value.page, page => { questionError.value = ''; if (['workbench', 'library'].includes(page)) {historyQuery='';load()} })
onMounted(() => load()); onUnmounted(() => { disposed = true; generation++ })
</script>
<template>
  <div class="alpha-app">
    <header class="topbar"><a class="brand" :href="href('workbench')"><span class="brand-mark">α</span> AlphaAgent</a><nav class="topnav" aria-label="主导航"><a :href="href('workbench')" :class="{ active: ['workbench','decision','market','candidates','morning','investment','investment-research','holding-advisor','portfolio-advisor'].includes(route.page) }">工作台</a><a :href="href('library')" :class="{ active: route.page === 'library' }">历史库</a></nav><div class="quote-strip" aria-label="指数行情"><span v-for="quote in quotes" :key="quote.name" class="qs-item" :title="quote.quoted_at ? '腾讯报价时间：' + quote.quoted_at : live.error.value || '行情读取中'"><span class="qs-name">{{ quote.name }}</span><span :class="['num',quote.change > 0 ? 'up' : 'down']">{{ quote.price ?? '—' }}</span><span class="num qs-change">{{ quote.change != null ? (quote.change > 0 ? '+' : '') + quote.change + '%' : '—' }}</span></span></div><a class="developer-shortcut" :href="developerHome" :aria-current="route.page === 'developer' ? 'page' : undefined">{{ developer?'开发者工作区':'开发者' }}</a><button class="theme-button" :aria-label="theme === 'dark' ? '切换浅色' : '切换深色'" @click="toggleTheme"><span aria-hidden="true">{{ theme === 'dark' ? '☀' : '☾' }}</span><span>{{ theme === 'dark' ? '浅色' : '深色' }}</span></button></header>
    <main id="main"><div v-if="questionError||understandingNotice||showSemantic" class="page question-error">
      <p v-if="questionError" class="notice error-text" role="alert">{{ questionError }}<button class="icon-button" aria-label="关闭问题提示" @click="questionError = ''">×</button></p>
      <p v-if="understandingNotice" class="notice" role="status">{{ understandingNotice }}</p>
      <div v-if="fallbackAvailable" class="ask-chips" aria-label="原有功能入口"><a class="chip" href="#/decision">明日预测 / 中期研究</a><a class="chip" href="#/investment-research">投资计划</a><a class="chip" href="#/holding-advisor">持仓顾问</a><a class="chip" href="#/portfolio-advisor">账户调仓</a><a class="chip" href="#/market">市场观察</a><a class="chip" href="#/candidates">行业候选</a></div>
      <div v-if="showSemantic" class="question-tasks panel rep-section" aria-label="本次问题的研究任务"><div class="section-heading"><strong>{{ semantic.summary }}</strong><button class="icon-button" aria-label="结束本次话题" @click="dismissUnderstanding">×</button></div><div class="ask-chips"><button v-for="(task,index) in semantic.tasks" :key="index" class="chip" :class="{active:index===semantic.active}" :disabled="asking" @click="openTask(task,index)">{{ taskLabel(task) }}{{ task.result_id?' · 已保存':'' }}</button></div><p v-if="semantic.tasks.length>1" class="condition-note">已保留全部任务；点击任务可切换，已生成的结果可随时回看。</p><details v-if="developer"><summary>开发者：问题理解</summary><pre>{{ semantic }}</pre></details></div>
    </div>
      <Workbench v-if="route.page === 'workbench'" :stocks="stocks" :live-quotes="live.quotes.value" :quote-time="live.retrievedAt.value" :quote-error="live.error.value" :history="history" :loading="asking" :history-loading="loading" :error="error" @ask="ask" @retry="load()" />
      <div v-else-if="route.page==='morning'" class="page"><DailyWorkspace :key="route.id" :id="route.id" /></div>
      <HoldingAdvisor v-else-if="route.page==='holding-advisor'" :key="route.id||'new-'+semantic?.active+'-'+dispatchRevision" :id="route.id" @started="r=>advisorStarted('holding-advisor',r)" />
      <PortfolioAdvisor v-else-if="route.page==='portfolio-advisor'" :key="route.id||'new-'+semantic?.active+'-'+dispatchRevision" :id="route.id" @started="r=>advisorStarted('portfolio-advisor',r)" />
      <LiveResearchView v-else-if="route.page === 'live' || route.page === 'stock'" :key="'live-' + route.id" :id="route.page === 'stock' ? 'new' : route.id" :code="route.page === 'stock' ? route.id : undefined" :quotes="live.quotes.value" :quote-time="live.retrievedAt.value" :quote-error="live.error.value" :quote-loading="live.loading.value" @refresh-quotes="live.refresh" @subject="value => { selectedSymbol = value; live.refresh() }" />
      <LiveComparison v-else-if="route.page === 'compare'" :key="'compare-' + (route.id || 'new-'+semantic?.active+'-'+dispatchRevision)" :id="route.id" @started="r => advisorStarted('compare',r)" />
      <StrategyView v-else-if="route.page === 'strategy'" :key="'strategy-' + (route.id || 'new')" :id="route.id" @started="r => { go('strategy',r.run_id); load() }" />
      <DeveloperWorkspace v-else-if="route.page==='developer'||route.page==='delivery'&&!developer" :enabled="developer" />
      <EvaluationComparison v-else-if="route.page==='evaluation'" :key="route.id||'new-evaluation'" :id="route.id" @started="r=>go('evaluation',r.run_id)" />
      <DeliveryView v-else-if="route.page === 'delivery'&&developer" />
      <KnowledgeAudit v-else-if="route.page === 'knowledge' && developer" />
      <InvestmentPlan v-else-if="['investment','investment-research'].includes(route.page)" :key="route.page+'-'+(route.id||'new-'+semantic?.active+'-'+dispatchRevision)" :id="route.id" :research="route.page==='investment-research'" @started="r=>advisorStarted(route.page,r)" />
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
