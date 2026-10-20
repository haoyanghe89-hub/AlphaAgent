<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {createInvestmentPlan,investmentChart} from './useInvestmentPlan.js'
import request from '../api/request.js'
import InvestmentOpinion from './InvestmentOpinion.vue'
import InvestmentContext from './InvestmentContext.vue'
import InvestmentTechnicals from './InvestmentTechnicals.vue'
import InvestmentObservations from './InvestmentObservations.vue'
import PrototypeChart from './PrototypeChart.vue'
import {pendingTaskDraft} from './questionUnderstanding.js'
import ResearchDepth from './ResearchDepth.vue'
import {structuredResearch} from './researchDepth.js'
const props=defineProps({id:String,research:Boolean})
const emit=defineEmits(['started'])
const ctl=createInvestmentPlan({started:r=>emit('started',r),root:props.research?'/quant/investment-research/runs':'/quant/investment-plans'})
const {state,error,busy}=ctl
const developer=new URLSearchParams(location.search).get('developer')==='1'
const entities=ref(''),horizon=ref('SWING'),personal=ref(false),capital=ref(''),cash=ref(''),reserve=ref('0'),holdingText=ref(''),formError=ref('')
const totalRisk=ref('2'),risk=ref('0.5'),single=ref('10'),industry=ref('25'),trend=ref('40'),quality=ref('40'),liquidity=ref('0.1'),commission=ref('0.03'),minimumFee=ref('5'),otherFee=ref('0.1')
const data=computed(()=>state.value?.status==='COMPLETED'?state.value.data:null)
const sessions=ref(20),audit=ref(null),auditError=ref(''),analysisFocus=ref([])
watch(horizon,v=>{sessions.value=v==='MEDIUM'?60:20},{flush:'sync'})
const opinion=c=>data.value?.research_opinions?.find(o=>o.security_id===c.security_id)
async function loadAudit(){auditError.value='';try{audit.value=await request('/quant/investment-research/runs/'+state.value.run_id+'/audit')}catch(e){auditError.value=e.message}}
const selected=c=>c.routes.find(r=>r.route_id===c.selected_route)
const quantity=c=>data.value?.portfolio.plans.find(p=>p.security_id===c.security_id)
const fmt=(v,d=2)=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:d})
const metric=(c,k)=>c.metrics[k]?.value
const regime=r=>({UPTREND:'均线上行',DOWNTREND:'均线下行',RANGE:'区间整理'})[r]||r
async function submit(){
  formError.value=''
  try{
    const list=entities.value.split(/[\s,，、;；]+/).filter(Boolean)
    if(!list.length||list.length>4)throw new Error('请输入一至四只股票，名称或代码以空格分隔。')
    const profile=personal.value?{capital:capital.value,available_cash:cash.value,reserve_cash:reserve.value,risk_per_trade:String(Number(risk.value)/100),total_new_risk:String(Number(totalRisk.value)/100),single_cap:String(Number(single.value)/100),industry_cap:String(Number(industry.value)/100),trend_allocation:String(Number(trend.value)/100),quality_allocation:String(Number(quality.value)/100),liquidity_cap:String(Number(liquidity.value)/100),commission_rate:String(Number(commission.value)/100),minimum_commission:minimumFee.value,other_cost_rate:String(Number(otherFee.value)/100),holdings:holdingText.value.trim()?holdingText.value.trim().split('\n').map(line=>{const p=line.trim().split(/[,，\s]+/);if(p.length!==4)throw new Error('每行持仓须填写：证券代码 路线 行业代码 市值。');return {security_id:p[0],route_id:p[1],industry_code:p[2],market_value:p[3]}}):[]}:null
    await ctl.submit({entities:list,horizon:horizon.value,profile,...(props.research?{schema_version:'investment-research-v4',horizon_sessions:Number(sessions.value),...structuredResearch(),...(analysisFocus.value.length?{analysis_focus:analysisFocus.value}:{})}:{})})
  }catch(e){formError.value=e.message}
}
function fill(r){analysisFocus.value=r.analysis_focus||[];entities.value=r.entities.join(' ');horizon.value=r.horizon;sessions.value=r.horizon_sessions||(r.horizon==='MEDIUM'?60:20);if(r.profile){const p=r.profile;personal.value=true;capital.value=p.capital;cash.value=p.available_cash;reserve.value=p.reserve_cash;risk.value=String(Number(p.risk_per_trade)*100);totalRisk.value=String(Number(p.total_new_risk)*100);single.value=String(Number(p.single_cap)*100);industry.value=String(Number(p.industry_cap)*100);trend.value=String(Number(p.trend_allocation)*100);quality.value=String(Number(p.quality_allocation)*100);liquidity.value=String(Number(p.liquidity_cap)*100);commission.value=String(Number(p.commission_rate)*100);minimumFee.value=p.minimum_commission;otherFee.value=String(Number(p.other_cost_rate)*100);holdingText.value=p.holdings.map(h=>[h.security_id,h.route_id,h.industry_code,h.market_value].join(' ')).join('\n')}}
let hydrated=false
watch(state,value=>{if(props.id&&!hydrated&&value?.request){hydrated=true;fill(value.request)}})
onMounted(async()=>{
  if(props.id)await ctl.load(props.id)
  else{
    const pending=props.research?pendingTaskDraft(sessionStorage,'investment'):null
    const saved=sessionStorage.getItem('alpha-investment-draft')||(pending?JSON.stringify(pending):null)
    if(saved){try{const draft=JSON.parse(saved);analysisFocus.value=draft.analysis_focus||[];entities.value=draft.entities||'';horizon.value=draft.horizon==='MEDIUM'?'MEDIUM':'SWING';sessions.value=draft.horizon_sessions||(horizon.value==='MEDIUM'?60:20)}catch{entities.value=''}}
    else entities.value=sessionStorage.getItem('alpha-investment-entities')||''
    sessionStorage.removeItem('alpha-investment-draft');sessionStorage.removeItem('alpha-investment-entities')
  }
})
onUnmounted(ctl.dispose)
</script>
<template>
<div class="page investment-plan">
  <section class="panel rep-section">
    <div class="section-heading"><h1>投资计划</h1><a href="#/">回到工作台</a></div>
    <p class="muted">先看方向与反证，再看条件和资金。不同投资路线分别遵守纪律。</p>
    <details class="input-disclosure" :open="!data"><summary>{{ data?'调整股票、周期与资金':'设置本次投资计划' }}</summary>
    <form @submit.prevent="submit">
      <div class="plan-inputs"><label>股票（最多四只）<input v-model="entities" placeholder="如：美的集团 600036 601088" required maxlength="150"></label><label>持有周期<select v-model="horizon"><option value="SWING">波段</option><option value="MEDIUM">中期</option></select></label><label v-if="research">研究期限（交易日）<input v-model="sessions" type="number" :min="horizon==='SWING'?10:41" :max="horizon==='SWING'?40:120" required></label><button class="btn btn-primary" :disabled="busy||state?.status==='RUNNING'">{{ busy?'正在保存…':research?'生成投资计划':'生成条件计划' }}</button></div>
      <ResearchDepth v-if="research" />
      <label class="profile-toggle"><input v-model="personal" type="checkbox">结合我的资金与持仓试算</label>
      <div v-if="personal" class="profile-fields">
        <label>总资产（元）<input v-model="capital" type="number" min="1" step="0.01" required></label><label>可用现金（元）<input v-model="cash" type="number" min="0" step="0.01" required></label><label>保留现金（元）<input v-model="reserve" type="number" min="0" step="0.01" required></label>
        <label>每笔风险预算（%）<input v-model="risk" type="number" min="0.01" max="2" step="0.01"></label><label>新增头寸总风险预算（%）<input v-model="totalRisk" type="number" min="0.01" max="5" step="0.01"></label><label>单股上限（%）<input v-model="single" type="number" min="1" max="30"></label><label>行业上限（%）<input v-model="industry" type="number" min="1" max="50"></label>
        <label>趋势路线预算（%）<input v-model="trend" type="number" min="0" max="100"></label><label>质量路线预算（%）<input v-model="quality" type="number" min="0" max="100"></label>
        <details class="holdings"><summary>已有持仓与费用假设</summary><p class="condition-note">现金加持仓市值须等于总资产。每行填写：完整证券代码 路线 申万一级行业代码 市值。路线填写 TREND_SWING 或 QUALITY_GROWTH；无法确认时先不启用个性化试算。</p><textarea v-model="holdingText" rows="3" aria-label="已有持仓" placeholder="此处填写你真实的持仓；无持仓时留空"></textarea><div class="profile-fields"><label>日成交额参与上限（%）<input v-model="liquidity" type="number" min="0.001" max="1" step="0.001"></label><label>单边佣金假设（%）<input v-model="commission" type="number" min="0" max="1" step="0.001"></label><label>最低佣金假设（元）<input v-model="minimumFee" type="number" min="0" max="100"></label><label>单边其他费用假设（%）<input v-model="otherFee" type="number" min="0" max="1" step="0.01"></label></div></details>
      </div>
    </form></details>
    <p v-if="formError||error" role="alert" class="notice error-text">{{ formError||error }}</p>
  </section>
  <section v-if="state?.status==='RUNNING'" class="panel rep-section" role="status"><h2>{{ research?'正在核查资料并形成投资计划':'正在读取资料并计算条件' }}</h2><p>行情、复权、行业与财报逐项保存；分析与反证核对后自动展示。</p><span class="muted">已记录 {{ state.calls?.length||0 }} 次来源调用</span></section>
  <section v-if="state?.status==='INTERRUPTED'" class="panel rep-section"><h2>需要继续完成研究</h2><p>{{ state.error }}</p><button class="btn" :disabled="busy" @click="ctl.resume">重试并保留已取得资料</button></section>
  <template v-if="data">
    <p v-if="data.request.profile?.input_origin==='SYNTHETIC_ACCEPTANCE'" class="notice">示例资金：使用真实行情，资金和持仓为假设。</p>
    <section class="panel rep-section plan-conclusion"><div class="section-heading"><h2>{{ data.conclusion }}</h2><a v-if="research&&developer" class="btn" :href="'/api/quant/investment-research/runs/'+state.run_id+'/export'">导出核验包</a></div><p v-if="research" class="muted">未来 {{ data.horizon_sessions }} 个交易日的条件性判断 · 方向与入场条件分别核对</p><p class="muted">行情截至 {{ data.market_date }} 收盘 · 沪深300 {{ regime(data.candidates.find(c=>c.market_regime)?.market_regime) }}</p><p>条件触发价与风险价均为研究参考。当前未满足的条件列在各股票下方。</p><details class="result-notes"><summary>判断范围与风险说明</summary><p v-for="note in data.caveats" :key="note" class="condition-note">{{ note }}</p></details></section>
    <section class="plan-results">
      <article v-for="c in data.candidates" :key="c.security_id" class="panel rep-section">
        <div class="section-heading"><h2>{{ c.name }} <small class="muted">{{ c.security_id }}</small></h2><span class="tag">{{ c.industry_name }}</span></div>
        <template v-if="c.status==='COMPUTED'">
          <p class="stock-outlook" :class="opinion(c)?.direction==='FAVORABLE'?'up':'down'">{{ opinion(c)?.summary || c.action }}</p>
          <p v-if="opinion(c)?.opposing?.length" class="outlook-risk"><b>反证：</b>{{ opinion(c).opposing[0].text }}</p><p class="plan-action"><strong>{{ c.action }}</strong><span class="muted">{{ selected(c).title }}</span></p>

          <div class="plan-numbers"><span>收盘 <b>{{ fmt(metric(c,'close')) }}</b></span><span>触发参考 <b>{{ fmt(selected(c).entry_reference) }}</b></span><span>风险参考 <b>{{ fmt(selected(c).risk_reference) }}</b></span><span>20日相对沪深300 <b>{{ fmt(Number(metric(c,'relative_return_20'))*100) }}%</b></span></div>
          <PrototypeChart :block="investmentChart(c)" :height="235" />
          <p class="condition-note">参考线按本次封存数据计算，横线不是历史买卖信号。</p>
          <InvestmentTechnicals :stock="data.technicals?.stocks.find(s=>s.security_id===c.security_id)" />
          <ul class="condition-list"><li v-for="(condition,i) in selected(c).conditions" :key="i"><span :class="condition.passed?'passed':'waiting'">{{ condition.passed?'已满足':'待满足' }}</span>{{ condition.label }}</li></ul>
          <p class="condition-note">{{ selected(c).invalidation }}</p>
          <div v-if="quantity(c)" class="quantity"><strong>{{ quantity(c).shares ? '条件额度：'+fmt(quantity(c).shares,0)+' 股' : c.research_direction==='UNFAVORABLE'?'本次不安排新增仓位':'当前额度不足最小买入单位' }}</strong><span>预留现金 {{ fmt(quantity(c).reserved_cash) }} 元</span><p>{{ quantity(c).reason }}</p><details v-if="quantity(c).shares"><summary>跳空、无法退出时的压力试算</summary><p v-for="s in quantity(c).stress_scenarios" :key="s.assumed_price_drop">假设价格下跌 {{ fmt(Number(s.assumed_price_drop)*100) }}% 且不能退出：估计损失 {{ fmt(s.estimated_loss) }} 元。</p><p>压力情景不是预测、概率或最大损失承诺。</p></details></div>
          <details class="research-details"><summary>支持理由、完整反证与资金政策依据</summary><InvestmentOpinion v-if="opinion(c)" :opinion="opinion(c)" :rationale="c.route_rationale" :evidence="data.evidence" :fragments="data.knowledge_usage.fragments" :run-id="state.run_id" /><InvestmentContext v-if="data.external_context" :context="data.external_context.stocks.find(s=>s.security_id===c.security_id)" :gaps="data.external_context.gaps.filter(g=>g.security_id===c.security_id)" /></details>
          <details class="route-alternative"><summary>查看另一条路线与经营指标</summary><p v-for="r in c.routes.filter(r=>r.route_id!==c.selected_route)" :key="r.route_id">{{ r.title }}：{{ r.action }}</p><p v-if="c.financials.scope==='FINANCIAL_NOT_APPLICABLE'">金融企业不套用非金融现金回收指标。</p><p v-else>财报期 {{ c.financials.period||'缺少同口径财报' }} · 收入TTM增速 {{ c.financials.metrics.revenue_growth_ttm?.value==null?'—':fmt(Number(c.financials.metrics.revenue_growth_ttm.value)*100)+'%' }} · 经营现金流/合并利润 {{ fmt(c.financials.metrics.cash_conversion?.value) }}</p></details>
        </template><p v-else class="error-text">{{ c.reason }}。该股票未生成交易数量。</p>
      </article>
    </section>
    <InvestmentObservations v-if="research" :run-id="state.run_id" :report="data" :developer="developer" />
    <section class="panel rep-section"><h2>组合安排</h2><template v-if="data.portfolio.status==='NO_PROFILE'"><p>{{ data.portfolio.message }}</p></template><template v-else><div class="plan-numbers"><span>条件预留 <b>{{ fmt(data.portfolio.reserved_cash) }} 元</b></span><span>剩余可分配 <b>{{ fmt(data.portfolio.remaining_cash) }} 元</b></span></div><p v-for="note in data.portfolio.caveats" :key="note" class="condition-note">{{ note }}</p><p v-for="h in data.holdings_review" :key="h.security_id+h.route_id">{{ h.security_id }} · {{ h.route_id==='TREND_SWING'?'趋势波段':'质量成长' }}：{{ h.action }}。{{ h.invalidation }}</p></template></section>
    <details v-if="developer&&research" class="panel rep-section"><summary>开发者：模型分析、工具与公开上下文</summary><p>资金和持仓留在本地；同模型职责复核，未宣称外部独立验证。</p><p v-if="state.request.enhanced_research">增强研究：动态选择方法，模型提出工具请求；按角色启用推理，按已批准摘要发布。</p><p>模型调用 {{ state.model_calls }} 次 · 已计费或预留 {{ state.charged_tokens }} 令牌 · 活跃耗时 {{ fmt(state.elapsed_seconds) }} 秒</p><button class="btn" @click="loadAudit">读取分析与复核记录</button><p v-if="auditError" class="error-text">{{ auditError }}</p><pre v-if="audit">{{ audit }}</pre></details><details v-if="developer" class="panel rep-section"><summary>开发者：输入、规则版本与计算核验</summary><p>系统研究参数，尚未证明盈利效果；来源规则的 REFERENCE_ONLY 状态不变。</p><pre>{{ {run_id:state.run_id,input_digest:data.input_digest,route_version:data.route_version,knowledge:data.knowledge,trading_rules:data.trading_rules,features:data.features.stocks.map(c=>({security_id:c.security_id,metrics:c.metrics,financials:c.financials}))} }}</pre></details>
  </template>
</div>
</template>
<style scoped>
.plan-inputs{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:12px;align-items:end;margin:16px 0}.investment-plan label{display:grid;gap:6px;color:var(--text-2)}.investment-plan input,.investment-plan select,.investment-plan textarea{width:100%;padding:9px;border:1px solid var(--border);background:var(--bg-raised);color:var(--text-1);border-radius:4px}.investment-plan .profile-toggle{display:flex;align-items:center;gap:8px}.profile-toggle input{width:auto}.profile-fields{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:14px 0}.holdings{grid-column:1/-1}.plan-results{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.plan-results .rep-section{margin:0}.plan-action{display:flex;justify-content:space-between;gap:12px}.plan-numbers{display:flex;gap:18px;flex-wrap:wrap;font-size:12px}.plan-numbers b{font-size:15px;margin-left:4px}.plan-conclusion{border-left:3px solid var(--accent)}.condition-list{list-style:none;padding:0;font-size:13px}.condition-list li{display:flex;gap:8px;margin:6px 0}.passed{color:var(--down)}.waiting{color:var(--accent)}.quantity{border-top:1px solid var(--border);padding-top:12px}.quantity span{display:block;margin-top:6px}.quantity p,.route-alternative{font-size:12px;color:var(--text-2)}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;max-height:600px;overflow:auto}.investment-plan :is(input,button,select,textarea,a,summary):focus-visible{outline:2px solid var(--accent);outline-offset:2px}@media(max-width:750px){.plan-inputs,.plan-results{grid-template-columns:1fr}.profile-fields{grid-template-columns:repeat(2,minmax(0,1fr))}.plan-action{flex-wrap:wrap}}
</style>
