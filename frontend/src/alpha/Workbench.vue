<script setup>
import { computed, ref } from 'vue'
import { href } from './navigation.js'
import { deliveryPage } from './useDelivery.js'
import { dateTime, historyStatus } from './model.js'
import { researchModes } from './workbenchIntent.js'
import { userHistoryItem } from './uiPresentation.js'
import MarketDashboard from './MarketDashboard.vue'
import MainFlowMonitor from './MainFlowMonitor.vue'
import DailyWorkspace from './DailyWorkspace.vue'
const props=defineProps({ stocks:Array, history:Array, loading:Boolean, historyLoading:Boolean, error:String, liveQuotes:Array, quoteTime:String, quoteError:String })
const emit=defineEmits(['ask','retry'])
const developer=new URLSearchParams(location.search).get('developer')==='1'
const question=ref(''),mode=ref('next'),input=ref(null)
const current=computed(()=>researchModes.find(item=>item.id===mode.value))
const recent=computed(()=>(props.history||[]).filter(item=>developer || (['COMPLETED','RUNNING'].includes(item.status)&&userHistoryItem(item))).slice(0,6))
const examples=['贵州茅台明日预测','宁德时代未来三个月怎么看','市场观察','半导体设备行业候选','美的集团持仓咨询']
function ask(text=question.value){question.value=text;emit('ask',text,mode.value)}
function selectStock(q){if(!['next','medium'].includes(mode.value))mode.value='next';question.value=q.name+(mode.value==='medium'?'未来三个月怎么看':'明日预测');input.value?.focus();input.value?.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})}
const label=item=>item.scenario==='portfolio_advisor'?'账户调仓':item.scenario==='holding_advisor'?'持仓顾问':item.scenario==='morning_brief'?'盘前清单':['investment','investment_research'].includes(item.scenario)?'投资计划':item.scenario==='next_session'?'明日预测':item.page==='market'?'市场观察':item.page==='candidates'?'行业候选':item.scenario==='decision'?'中期研究':item.scenario==='live'?'单股研究':item.scenario==='strategy'?'策略回测':'研究'
</script>
<template>
  <div class="page workbench">
    <section class="panel ask-hero">
      <div class="hero-heading"><h1>今天，关注什么</h1><span class="muted">市场、机会与持仓，一处查看</span></div>
      <div class="workbench-modes" role="group" aria-label="选择研究内容"><button v-for="item in researchModes" :key="item.id" class="btn" :class="{'btn-primary':mode===item.id}" :aria-pressed="mode===item.id" @click="mode=item.id;input?.focus()">{{ item.label }}</button></div>
      <form class="ask-box" @submit.prevent="ask()"><input ref="input" v-model="question" aria-label="投研问题" :placeholder="current.placeholder" autocomplete="off" maxlength="2000"><button class="btn btn-primary" :disabled="!question.trim()||loading">{{ loading?'正在读取…':'开始分析' }}</button></form>
      <p class="condition-note mode-hint">{{ current.hint }} <a href="#/investment-research">按路线与资金生成投资计划 →</a></p>
      <div class="ask-chips"><button v-for="example in examples" :key="example" class="chip" :disabled="loading" @click="ask(example)">{{ example }}</button></div>
      <p class="hero-disclaimer">方向是待验证的判断。预测保存后不覆盖，目标交易日收盘后核对真实结果。</p>
    </section>
    <div v-if="error" class="notice error-text" role="alert">{{ error }} <button class="btn" @click="emit('retry')">重新加载</button></div>
    <MarketDashboard @select="selectStock" />
    <DailyWorkspace />
    <MainFlowMonitor @select="selectStock" />
    <section class="workspace-section">
      <div class="section-heading"><h2>最近研究</h2><a :href="href('library')">查看全部 →</a></div>
      <div v-if="recent.length" class="panel"><div v-for="item in recent" :key="item.run_id" class="lib-row"><span class="tag tag-gold">{{ label(item) }}</span><a class="lib-q" :href="href(deliveryPage(item),item.run_id)">{{ item.title }}</a><span v-if="item.status!=='COMPLETED'||['strategy','backtest'].includes(item.scenario)" class="tag">{{ historyStatus(item) }}</span><time class="lib-date">{{ dateTime(item.created_at) }}</time></div></div>
      <div v-else class="panel empty-state">{{ historyLoading?'正在读取已保存的研究…':'尚无研究记录，在上方输入股票或问题。' }}</div>
    </section>

    <details class="panel rep-section workbench-more"><summary>更多研究工具</summary><div class="bt-actions"><a class="btn" href="#/live/new">单股数据与估值</a><a class="btn" href="#/compare">双股比较</a><a class="btn" href="#/strategy">策略回测</a><a v-if="developer" class="btn" href="?developer=1#/knowledge">内置知识核验</a><a v-if="developer" class="btn" href="?developer=1#/delivery">开发验收</a></div></details>
  </div>
</template>
<style scoped>
.workbench-modes{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 12px}.mode-hint{margin:8px 0 12px}.workbench .stock-card{padding:14px 18px}.workbench .sc-price{font-size:22px;margin:10px 0}.sc-price span{font-size:13px;margin-left:12px}.workbench-more{margin-top:16px}.workbench-more summary{cursor:pointer;color:var(--text-2)}.workbench-quotes{margin-top:18px}.workbench :is(input,button,summary,a):focus-visible{outline:2px solid var(--accent);outline-offset:3px}@media(max-width:640px){.workbench-modes .btn{flex:1;min-width:42%;padding:8px}.workbench .stock-grid{grid-template-columns:1fr}.ask-box .btn{width:100%}}
</style>
