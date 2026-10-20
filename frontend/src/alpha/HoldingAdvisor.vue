<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {createHoldingAdvisor,advisorChart} from './useHoldingAdvisor.js'
import {dateTime} from './model.js'
import PrototypeChart from './PrototypeChart.vue'
import AdvisorDialogue from './AdvisorDialogue.vue'
import {pendingTaskDraft} from './questionUnderstanding.js'
import ResearchDepth from './ResearchDepth.vue'
const props=defineProps({id:String}),emit=defineEmits(['started'])
const ctl=createHoldingAdvisor({started:r=>emit('started',r)}),{state,error,busy}=ctl
const developer=new URLSearchParams(location.search).get('developer')==='1'
const initialLoading=ref(Boolean(props.id)),parentId=ref(null),inputOrigin=ref('USER_DECLARED')
watch(state,s=>{if(s?.status==='COMPLETED')parentId.value=s.run_id})
const entity=ref(''),cost=ref(''),shares=ref(''),sellable=ref(''),boughtToday=ref('0'),buyDate=ref(''),assets=ref('')
const horizon=ref('SWING'),sessions=ref(20),route=ref('AUTO'),singleCap=ref('10'),risk=ref('0.5'),trim=ref('50')
const commission=ref('0.03'),minimum=ref('5'),otherCost=ref('0.1'),question=ref('这只股票现在该继续持有，还是减仓？到什么条件可以卖？'),scenario=ref(''),formError=ref(''),refresh=ref(false)
const data=computed(()=>state.value?.status==='COMPLETED'?state.value.data:null)
let hydrated=false
watch(state,value=>{if(props.id&&!hydrated&&value?.request){hydrated=true;fill(value.request);question.value='';scenario.value=''}})
const running=computed(()=>initialLoading.value||busy.value||state.value?.status==='RUNNING')
const past=computed(()=>(state.value?.conversation||[]).filter(t=>t.run_id!==state.value?.run_id))
const suggestions=['到什么条件可以分批卖？','如果继续下跌怎么办？','现在可以补仓吗？','为什么这样判断，依据是什么？']
const fmt=(v,d=2)=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:d})
watch(horizon,v=>{sessions.value=v==='SWING'?20:60},{flush:'sync'})
function fill(r){const p=r.position;inputOrigin.value=p.input_origin;parentId.value=state.value.status==='COMPLETED'?state.value.run_id:r.previous_run_id;entity.value=p.entity;cost.value=p.cost_price;shares.value=String(p.shares);sellable.value=String(p.sellable_shares);boughtToday.value=String(p.bought_today);buyDate.value=p.buy_date||'';assets.value=p.total_assets;singleCap.value=String(Number(p.single_cap)*100);risk.value=String(Number(p.risk_budget)*100);trim.value=String(Number(p.trim_fraction)*100);commission.value=String(Number(p.commission_rate)*100);minimum.value=p.minimum_commission;otherCost.value=String(Number(p.other_sell_cost_rate)*100);horizon.value=r.horizon;sessions.value=r.horizon_sessions;route.value=r.route_id}
async function submit(text){
  formError.value=''
  if(typeof text==='string')question.value=text
  try{
    for(const [name,value] of [['成本',cost.value],['持股数',shares.value],['可卖股数',sellable.value],['总资产',assets.value]])if(value==='')throw Error('请填写'+name+'。')
    const position={input_origin:inputOrigin.value,entity:entity.value.trim(),cost_price:cost.value,shares:Number(shares.value),sellable_shares:Number(sellable.value),bought_today:Number(boughtToday.value),buy_date:buyDate.value||null,total_assets:assets.value,single_cap:String(Number(singleCap.value)/100),risk_budget:String(Number(risk.value)/100),trim_fraction:String(Number(trim.value)/100),commission_rate:String(Number(commission.value)/100),minimum_commission:minimum.value,other_sell_cost_rate:String(Number(otherCost.value)/100)}
    await ctl.submit({position,horizon:horizon.value,horizon_sessions:Number(sessions.value),route_id:route.value,question:question.value,scenario_price:scenario.value||null,refresh_research:refresh.value,previous_run_id:parentId.value})
  }catch(e){formError.value=e.message}
}
onMounted(async()=>{
  if(props.id)await ctl.load(props.id)
  else{try{const seed=JSON.parse(sessionStorage.getItem('alpha-holding-question')||'null')||pendingTaskDraft(sessionStorage,'holding');if(seed){entity.value=seed.entity||'';question.value=seed.question;if(seed.horizon)horizon.value=seed.horizon;if(seed.horizon_sessions)sessions.value=seed.horizon_sessions}}catch{}sessionStorage.removeItem('alpha-holding-question')}
  initialLoading.value=false
})
onUnmounted(ctl.dispose)
</script>
<template>
<div class="page holding-advisor" :class="{'has-result':data}">
  <div class="section-heading"><div><h1>持仓顾问</h1><p class="muted">按原持有路线看价格、仓位与退出条件</p></div><div class="bt-actions"><a class="btn" href="#/holding-advisor">新咨询</a><a href="#/">工作台</a></div></div>
  <div class="advisor-layout">
    <section class="panel rep-section holding-inputs">
      <details class="input-disclosure" :open="!data"><summary>{{ data?'更新持仓并继续咨询':'填写这笔持仓' }}</summary>
      <form @submit.prevent="submit()">
        <ResearchDepth :continuing="Boolean(props.id)" />
        <div class="fields"><label class="wide">股票名称或代码<input v-model="entity" required maxlength="30" placeholder="如：美的集团 / 000333" :readonly="Boolean(props.id)"></label><label>每股成本（元）<input v-model="cost" required type="number" min="0.001" step="any"></label><label>总持股数<input v-model="shares" required type="number" min="1" step="1"></label><label>当前可卖股数<input v-model="sellable" required type="number" min="0" step="1"></label><label>今日买入股数<input v-model="boughtToday" type="number" min="0" step="1"></label><label class="wide">账户总资产（元，含其他持仓）<input v-model="assets" required type="number" min="0.01" step="0.01"></label><label>持有周期<select v-model="horizon" :disabled="Boolean(props.id)"><option value="SWING">波段</option><option value="MEDIUM">中期</option></select></label><label>交易日数<input v-model="sessions" required type="number" :min="horizon==='SWING'?10:41" :max="horizon==='SWING'?40:120" :readonly="Boolean(props.id)"></label></div>
        <details><summary>风控与分批偏好</summary><div class="fields"><label class="wide">原持仓路线<select v-model="route" :disabled="Boolean(props.id)"><option value="AUTO">按统一知识体系匹配</option><option value="TREND_SWING">趋势波段</option><option value="QUALITY_GROWTH">质量成长</option></select></label><label>单股上限（%）<input v-model="singleCap" type="number" min="0.01" max="100" step="0.01"></label><label>本笔风险预算（%）<input v-model="risk" type="number" min="0.01" max="5" step="0.01"></label><label>分批减仓比例（%）<input v-model="trim" type="number" min="0.01" max="100" step="0.01"></label><label>首次买入日<input v-model="buyDate" type="date"></label><label>佣金假设（%）<input v-model="commission" type="number" min="0" max="1" step="0.001"></label><label>最低佣金（元）<input v-model="minimum" type="number" min="0" max="100" step="0.01"></label><label class="wide">卖出其他费用假设（%）<input v-model="otherCost" type="number" min="0" max="1" step="0.001"></label></div><p class="condition-note">默认单股上限 10%、本笔风险预算 0.5%、分批比例 50%；这些是可修改的执行偏好，费用以实际券商账单为准。</p></details>
        <p class="condition-note">成本、股数、资金和原问题留在本地；模型接收脱敏后的问题语义、公开股票资料与已审核方法。成交或调仓后，请修改资料再咨询。</p>
        <label>本轮问题<textarea v-model="question" required rows="3" maxlength="2000" placeholder="例如：现在仓位是否偏大？到多少可以卖？"></textarea></label>
        <label>假设价格（可选，元）<input v-model="scenario" type="number" min="0.001" step="any" placeholder="只做情景试算，不作为真实报价"></label>
        <label v-if="props.id" class="checkbox"><input v-model="refresh" type="checkbox">更新公开资料，重新形成研究</label>
        <button class="btn btn-primary send" :disabled="running||Boolean(state&&state.status==='INTERRUPTED')">{{ running?'正在核查…':props.id?'发送追问并保存':'分析持仓并保存' }}</button>
      </form></details>
      <p v-if="formError||error" class="notice error-text" role="alert">{{ formError||error }}</p>
    </section>
    <div class="advisor-content">
      <section v-if="initialLoading" class="panel rep-section" role="status"><h2>正在读取已保存的咨询</h2><p>正在核对原始资料和知识版本，稍后展示本轮建议与历史对话。</p></section>
      <section v-else-if="state?.status==='RUNNING'" class="panel rep-section" role="status"><h2>正在形成持仓建议</h2><p>先核查公开资料和原持有路线，再计算本地仓位。完成后自动展示并保存。</p><p class="muted">可以离开页面，稍后从历史库继续查看。</p></section>
      <section v-else-if="state?.status==='INTERRUPTED'" class="panel rep-section"><h2>本轮资料需要继续核查</h2><p>{{ state.error }}</p><button class="btn" :disabled="busy" @click="ctl.resume">保留输入，继续核查</button></section>
      <section v-else-if="!data" class="panel rep-section"><h2>把股票分析落到这笔持仓</h2><p>填写成本、持股数、可卖股数和总资产，即可查看持有建议、仓位是否超限、下行防守与上行观察位。</p><p class="muted">支持波段和中期。系统复用已审核投资知识，每轮建议与原始资料长期保留。</p></section>
      <template v-if="data">
        <p v-if="data.request.position.input_origin==='SYNTHETIC_ACCEPTANCE'" class="notice">示例持仓：使用真实行情，成本和股数为假设。</p>
        <AdvisorDialogue :dialogue="data.dialogue" />
        <section class="panel rep-section conclusion">
          <div class="section-heading"><h2>{{ data.name }} <small class="muted">{{ data.security_id }}</small></h2><span class="tag">{{ data.route_title }}</span></div>
          <p class="condition-note">你的问题：{{ state.request.question }}</p><h2 class="headline">{{ data.headline }}</h2>
          <p class="muted">本轮报价 {{ dateTime(data.quote.quoted_at) }} · 研究收盘日期 {{ data.market_date }} · 建议保存于 {{ dateTime(state.issued_at) }}</p>
          <div class="numbers"><span>本轮价格<b>¥{{ fmt(data.facts.price) }}</b></span><span>成本<b>¥{{ fmt(data.facts.cost_price) }}</b></span><span>持仓占比<b>{{ fmt(Number(data.facts.weight)*100) }}%</b></span><span>浮动盈亏<b :class="Number(data.facts.floating_pnl)>=0?'up':'down'">{{ fmt(data.facts.floating_pnl) }} 元</b></span><span>至风险参考的损失<b>{{ fmt(data.facts.loss_to_risk) }} 元</b></span><span>本笔风险预算<b>{{ fmt(data.facts.risk_budget) }} 元</b></span></div>
          <p v-for="line in data.answer" :key="line">{{ line }}</p><p v-for="warning in data.warnings" :key="warning" class="notice">{{ warning }}</p>
          <PrototypeChart :block="advisorChart(data)" :height="230" /><p class="condition-note">复权序列锚定研究收盘日；成本和参考线为本轮条件，不代表历史买卖信号。报价更新不会覆盖旧建议。</p>
        </section>
        <section class="panel rep-section"><h2>什么时候考虑卖，卖多少</h2><p class="condition-note">各项按本轮持仓分别试算，股数不能相加；任何成交后，请更新持仓再咨询。</p><div class="gate-grid"><article v-for="gate in data.gates" :key="gate.kind" class="gate"><strong>{{ gate.title }}</strong><b class="gate-price">¥{{ fmt(gate.price) }}</b><p>{{ gate.condition }}</p><span v-if="gate.shares!=null">条件满足时，数量参考 {{ fmt(gate.shares,0) }} 股</span></article></div><p v-if="!data.gates.length">本轮价格口径需要更新；仓位与研究理由仍可查看，请勾选“更新公开资料”再咨询。</p></section>
        <section v-if="data.scenario" class="panel rep-section"><h2>假设价格 ¥{{ fmt(data.scenario.price) }}</h2><p><strong>{{ data.scenario.conclusion }}</strong></p><div class="numbers"><span>条件数量<b>{{ fmt(data.scenario.shares,0) }} 股</b></span><span>情景浮盈亏<b>{{ fmt(data.scenario.floating_pnl) }} 元</b></span><span>情景持仓占比<b>{{ fmt(Number(data.scenario.portfolio_weight)*100) }}%</b></span><span>卖出净额试算<b>{{ fmt(data.scenario.estimated_net_proceeds) }} 元</b></span></div><p class="condition-note">这是你提出的假设价格；其他资产、可卖股数保持本轮值，估算卖出费用 {{ fmt(data.scenario.estimated_sell_cost) }} 元，不包含额外滑点。</p></section>
        <details class="panel rep-section research-details"><summary>理由、反证与改变判断的条件</summary><p v-for="claim in data.opinion.claims" :key="claim.claim_id">{{ claim.text }}</p><div class="counter"><strong>反证</strong><p v-for="claim in data.opinion.opposing" :key="claim.claim_id">{{ claim.text }}</p></div><p v-for="condition in data.invalidation" :key="condition" class="condition-note">{{ condition }}</p><details><summary>本轮采用的投资方法</summary><div v-for="method in data.knowledge" :key="method.rule_id" class="method"><strong>{{ method.title }}</strong><p>{{ method.system_rule }}</p><a v-if="method.url" :href="method.url" target="_blank" rel="noopener noreferrer">{{ method.authors.join('、') }} · {{ method.source_title }}</a></div></details><p v-for="note in data.caveats" :key="note" class="condition-note">{{ note }}</p><div class="bt-actions"><a class="btn" :href="'#/investment-research/'+data.research_run_id">查看完整股票研究</a><a class="btn" :href="'/api/quant/holding-advisor/turns/'+state.run_id+'/export'">导出本地咨询记录</a></div></details>
        <section class="panel rep-section"><h2>继续围绕这笔持仓追问</h2><div class="ask-chips"><button v-for="text in suggestions" :key="text" class="chip" :disabled="running" @click="submit(text)">{{ text }}</button></div><p class="condition-note">也可以展开上方持仓资料，修改问题或假设价格后发送；每轮另存，不改写旧建议。</p><details v-if="past.length"><summary>查看此前 {{ past.length }} 轮对话</summary><article v-for="turn in past" :key="turn.run_id" class="past-turn"><p><strong>你：</strong>{{ turn.question }}</p><p><strong>顾问：</strong>{{ turn.data?.headline||'该轮尚未完成' }}</p><p v-for="line in turn.data?.answer||[]" :key="line">{{ line }}</p><p v-if="turn.data?.scenario">假设 {{ turn.data.scenario.price }} 元：{{ turn.data.scenario.conclusion }}</p><a :href="'#/holding-advisor/'+turn.run_id">回看该轮完整输入和建议 · {{ dateTime(turn.issued_at||turn.created_at) }}</a></article></details></section>
        <details v-if="developer&&state.understanding" class="panel rep-section"><summary>开发者：语义理解与脱敏投影</summary><pre>{{ state.understanding }}</pre></details>
        <details v-if="developer" class="panel rep-section"><summary>开发者：固定知识版本与本地计算</summary><pre>{{ {run_id:state.run_id,previous_run_id:state.request.previous_run_id,research_run_id:data.research_run_id,research_sha256:data.research_sha256,knowledge:data.knowledge,privacy:data.privacy,engine_version:data.engine_version,trading_rules:data.trading_rules} }}</pre></details>
      </template>
    </div>
  </div>
</div>
</template>
<style scoped>
.advisor-layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:16px;align-items:start}.holding-inputs,.advisor-content .rep-section{margin:0 0 14px}.holding-advisor h1{margin-bottom:6px}.holding-advisor label{display:grid;gap:6px;font-size:12px;color:var(--text-2);margin:10px 0}.holding-advisor input,.holding-advisor select,.holding-advisor textarea{width:100%;min-width:0;padding:9px;border:1px solid var(--border);border-radius:4px;background:var(--bg-raised);color:var(--text-1);font:inherit}.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 10px}.wide{grid-column:1/-1}.checkbox{display:flex!important;align-items:center}.checkbox input{width:auto}.send{width:100%;margin-top:8px}.headline{font-size:22px;line-height:1.5;color:var(--accent)}.numbers{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0;padding:12px;background:var(--bg-raised);font-size:12px;color:var(--text-2)}.numbers b{display:block;font-size:17px;margin-top:5px;color:var(--text-1)}.numbers .up{color:var(--up)}.numbers .down{color:var(--down)}.conclusion{border-left:3px solid var(--accent)}.gate-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.gate{border:1px solid var(--border);border-radius:4px;padding:12px;font-size:13px;line-height:1.7}.gate-price{display:block;font-size:23px;color:var(--accent);margin:8px 0}.gate span{color:var(--text-2)}.counter{border-left:2px solid var(--accent);padding-left:12px}.past-turn,.method{padding:12px 0;border-bottom:1px solid var(--border);font-size:13px}.advisor-content p{line-height:1.7}.holding-advisor summary{cursor:pointer;color:var(--text-2);font-size:13px;margin:10px 0}.holding-advisor pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:450px;overflow:auto;font-size:11px}.holding-advisor :is(input,button,select,textarea,a,summary):focus-visible{outline:2px solid var(--accent);outline-offset:2px}@media(max-width:950px){.advisor-layout{grid-template-columns:1fr}.gate-grid{grid-template-columns:1fr}.numbers{grid-template-columns:repeat(2,minmax(0,1fr))}.holding-inputs .fields{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
