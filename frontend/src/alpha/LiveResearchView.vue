<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'
import PrototypeChart from './PrototypeChart.vue'
import ConclusionOverview from './ConclusionOverview.vue'
import ReaderDcf from './ReaderDcf.vue'
import { href } from './navigation.js'
import { dateTime, movement, number } from './model.js'

const developer = new URLSearchParams(location.search).get('developer') === '1'
const props = defineProps({ id: String, code: String, quotes: Array, quoteTime: String, quoteError: String, quoteLoading: Boolean })
const emit = defineEmits(['refresh-quotes','subject'])
const state = ref(null), error = ref(''), busy = ref(false), source = ref(null), sourceDialog = ref(null)
const reading = ref(null), readingError = ref('')
const form = ref({ g1: 8, g2: 4, g_term: 2.5, discount: 9, cost_pct: 0.13, fast: 20, slow: 60 })
const data = computed(() => state.value?.data)
const subject = computed(() => data.value?.subject || state.value?.subject || {name:props.code || '正在研究',code:props.code || '',exchange:''})
const quote = computed(() => props.quotes?.find(q => q.code === subject.value.code))
const followup = ref(''), clarified = ref('')
let followupRequest, emittedSymbol
const defs = [{ key:'total_ret', name:'区间收益', unit:'%' }, { key:'cagr', name:'年化收益', unit:'%' }, { key:'vol', name:'年化波动', unit:'%' }, { key:'sharpe', name:'Sharpe', unit:'' }, { key:'max_dd', name:'最大回撤', unit:'%' }]
const inputFields = [{key:'g1',label:'前 5 年增长率 %',min:-20,max:40},{key:'g2',label:'后 5 年增长率 %',min:0,max:15},{key:'g_term',label:'永续增长率 %',min:0,max:4},{key:'discount',label:'折现率 %',min:5,max:15}]
const sourceNames = { quotes:'腾讯最新报价',prices:'腾讯前复权日 K',benchmark:'腾讯沪深300价格指数',lrb:'新浪合并利润表',fzb:'新浪合并资产负债表',llb:'新浪合并现金流量表',valuation:'东方财富历史估值',margin:'东方财富融资融券',pledge:'东方财富质押记录',unlock:'东方财富解禁批次',income_check:'东方财富披露业绩',cash_em:'东方财富现金流量表及补充项',income_em:'东方财富合并利润表',balance_em:'东方财富合并资产负债表',profile:'公司行业与证券资料',security_search:'证券目录解析',announcements:'公告目录'  }
const roles = { NEEDS_INPUT:'需要补充',UNSUPPORTED:'范围不支持',INTERRUPTED:'已中断', PENDING:'等待',RUNNING:'执行中',COMPLETED:'完成',FAILED:'失败' }
const dcfLabels = { profit_before_tax:'利润总额',income_tax:'所得税费用',interest_expense:'财务利息费用',interest_income:'财务利息收入',investment_income:'投资收益',fair_value_gain:'公允价值变动收益',nonoperating_income:'营业外收入',nonoperating_expense:'营业外支出',depreciation:'折旧摊销供应商补充项',capex:'资本开支',inventory_decrease:'存货减少',receivables_increase:'经营应收增加',payables_increase:'经营应付增加',cash:'现金及等价物',short_debt:'短期借款',long_debt:'长期借款',bonds:'应付债券',leases:'租赁负债',current_debt:'一年内到期非流动负债',minority_equity:'少数股东权益',issued_shares:'最新发行股数',treasury_shares:'模型假设：不扣库存股数量' }
let disposed = false, timer, requestKey = crypto.randomUUID(), scenarioRequest
async function start() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const result = await request('/quant/research/runs', { method:'POST', body:JSON.stringify({request_key:requestKey,code:subject.value.code || '000333',question:'全面分析' + (subject.value.code || '美的集团')}) })
    if (!disposed) location.hash = href('live', result.run_id)
  } catch (e) { if (!disposed) error.value = e.message }
  finally { if (!disposed) busy.value = false }
}
async function load() {
  try {
    const result = await request('/quant/research/runs/' + props.id)
    if (disposed) return
    state.value = result; error.value = ''
    const sym = result.subject?.symbol || result.data?.subject?.symbol
    if (sym && sym !== emittedSymbol) { emittedSymbol = sym; emit('subject',sym) }
    if (result.status === 'RUNNING') timer = setTimeout(load, 1500)
    if (result.data) {
      form.value = { ...form.value, ...Object.fromEntries(Object.entries(result.data.dcf.assumptions || {}).map(([k,v]) => [k,Number(v)*100])), cost_pct:result.data.simulation.cost_pct, fast:result.data.simulation.fast, slow:result.data.simulation.slow }
    }
    if (result.status === 'COMPLETED') {
      try {
        const overview = await request('/quant/research/runs/' + props.id + '/overview')
        if (!disposed) { reading.value = overview; readingError.value = '' }
      } catch(e) { if (!disposed) { reading.value = null; readingError.value = '白话解读暂未加载' } }
    }
  } catch(e) { if (!disposed) error.value = e.message }
}
async function recalc() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const parameters = { dcf:Object.fromEntries(inputFields.map(f => [f.key,(form.value[f.key]/100).toFixed(6)])), cost_pct:Number(form.value.cost_pct), fast:Number(form.value.fast), slow:Number(form.value.slow) }
    const signature = JSON.stringify(parameters)
    if (scenarioRequest?.signature !== signature) scenarioRequest = { signature, payload:{ request_key:crypto.randomUUID(), ...parameters } }
    const result = await request('/quant/research/runs/' + props.id + '/scenario', { method:'POST', body:JSON.stringify(scenarioRequest.payload) })
    if (!disposed) location.hash = href('live',result.run_id)
  } catch(e) { if (!disposed) error.value = e.message }
  finally { if (!disposed) busy.value = false }
}
function jump(id) { document.getElementById(id)?.scrollIntoView({block:'start'}) }
async function showSource(key) { source.value = data.value.sources.find(s => s.key === key.split(' / ')[0]); await nextTick(); sourceDialog.value?.showModal() }
async function showEvidence(id) {
  const evidence = data.value.evidence?.find(e => e.id === id)
  if (!evidence) return
  const key = Array.isArray(evidence.source) ? evidence.source[0] : evidence.source
  const meta = data.value.sources.find(s => s.key === key)
  source.value = {...(meta || {}),key,title:evidence.title || evidence.id,evidence}
  await nextTick(); sourceDialog.value?.showModal()
}
async function showDerived(proof) {
  const key = proof.source?.[0]
  const meta = data.value.sources.find(s => s.key === key)
  source.value = {...(meta || {}),key,title:proof.title,evidence:proof}
  await nextTick(); sourceDialog.value?.showModal()
}
async function resume() {
  busy.value = true; error.value = ''
  try { await request('/quant/research/runs/'+props.id+'/resume',{method:'POST'}); await load() }
  catch(e) { error.value = e.message } finally { busy.value = false }
}
async function askFollowup() {
  if (!followup.value.trim() || busy.value) return
  busy.value = true; error.value = ''
  try {
    const signature = props.id + followup.value
    if (followupRequest?.signature !== signature) followupRequest = {signature,key:crypto.randomUUID()}
    const result = await request('/quant/research/runs',{method:'POST',body:JSON.stringify({request_key:followupRequest.key,question:followup.value,parent_run_id:props.id})})
    location.hash = href('live',result.run_id)
  } catch(e) { error.value = e.message } finally { busy.value = false }
}
async function clarify() {
  if (!clarified.value.trim() || busy.value) return
  busy.value = true; error.value = ''
  try {
    const result = await request('/quant/research/runs',{method:'POST',body:JSON.stringify({request_key:crypto.randomUUID(),question:clarified.value})})
    location.hash = href('live',result.run_id)
  } catch(e) { error.value=e.message } finally { busy.value=false }
}
function fresh() { requestKey = crypto.randomUUID(); start() }
onMounted(() => props.id === 'new' ? start() : load())
onUnmounted(() => { disposed = true; clearTimeout(timer) })
</script>
<template>
  <div class="page live-research">
    <div class="stock-heading"><div><div class="stock-name-line"><h1>{{ subject.name }}</h1><span class="num muted">{{ subject.code }} · {{ subject.exchange }}</span><span class="tag tag-gold">实时研究</span></div><div class="stock-price-line"><span :class="['stock-price num',movement(quote?.change)]">{{ number(quote?.price) }}</span><span :class="['num',movement(quote?.change)]">{{ quote?.change > 0 ? '+' : '' }}{{ number(quote?.change) }}%</span></div><p class="muted small">腾讯报价 {{ quote ? dateTime(quote.quoted_at) : '读取中' }} · 获取 {{ quoteTime ? dateTime(quoteTime) : '读取中' }} · 每 30 秒刷新，休市显示最近交易报价</p></div><div class="live-actions"><button class="btn" :disabled="quoteLoading" @click="emit('refresh-quotes')">{{ quoteLoading ? '刷新中…' : '刷新行情' }}</button><button class="btn btn-primary" :disabled="busy" @click="fresh">重新采集并研究</button></div></div>
    <p v-if="quoteError" class="notice error-text" role="alert">{{ quoteError }}</p>
    <p v-if="error" class="notice error-text" role="alert">{{ error }} <button class="btn" @click="id === 'new' ? start() : load()">重试</button></p>
    <div v-if="!state && !error" class="panel empty-state" role="status">正在启动现场取数与研究…</div>
    <p v-if="!developer && state?.status==='RUNNING'" class="panel rep-section" role="status">正在读取并核对公司资料，完成后显示结论与图表。离开页面后可从历史库回看。</p>
    <section v-if="developer && state" class="panel panel-pad live-process" aria-label="研究过程"><div v-for="node in state.process" :key="node.role"><span class="tag" :class="node.status === 'COMPLETED' ? 'tag-gold' : ''">{{ roles[node.status] }}</span><strong>{{ node.role }}</strong><p>{{ node.detail }}</p></div><p class="muted small">{{ data?.sources.length || state.sources_received || 0 }} 份响应 · {{ state.calls?.filter(c => c.kind === 'model' && c.status === 'COMPLETED').length || 0 }} 次成功模型调用 · {{ data?.process_note || 'Supervisor 按依赖编排角色与确定性工具' }}</p></section>
    <p v-if="state?.error" class="notice error-text" role="alert">{{ state.error }} <button v-if="['FAILED','INTERRUPTED'].includes(state.status) && state.calls" class="btn" :disabled="busy" @click="resume">继续未完成的研究</button><button class="btn" :disabled="busy" @click="fresh">重新发起</button></p>
    <form v-if="['NEEDS_INPUT','UNSUPPORTED'].includes(state?.status)" class="panel panel-pad ask-box" @submit.prevent="clarify"><input v-model="clarified" aria-label="补充研究问题" placeholder="请明确沪深 A 股名称或代码，例如：分析格力电器" maxlength="2000" /><button class="btn btn-primary" :disabled="busy || !clarified.trim()">重新规划</button></form>
    <details v-if="developer && state?.calls?.length" class="panel panel-pad agent-trace"><summary>查看实际执行记录 · {{ state.calls.length }} 次模型 / 工具调用</summary><div class="live-table-scroll"><table class="data"><thead><tr><th>角色</th><th>调用</th><th>状态</th><th>模型 / 用量</th><th>请求 / 结果哈希</th></tr></thead><tbody><tr v-for="(call,i) in state.calls" :key="i"><td>{{ call.role }}</td><td>{{ call.name }}</td><td>{{ roles[call.status] || call.status }}</td><td>{{ call.model || 'Python 工具' }}<small v-if="call.kind === 'model'"> · {{ call.input_tokens ?? '—' }} / {{ call.output_tokens ?? '—' }} tokens</small></td><td class="num">{{ call.request_ref?.slice(-12) }} / {{ call.response_ref?.slice(-12) || '—' }}</td></tr></tbody></table></div></details>
    <div v-if="data" class="report-grid">
      <aside class="report-rail"><div class="panel rail-box"><div class="section-title">报告信息</div><div class="kv"><span class="k">标的</span><span>{{ subject.name }}</span></div><div class="kv"><span class="k">日线截至</span><span class="num">{{ data.data_dates.price }}</span></div><div class="kv"><span class="k">最新财报</span><span class="num">{{ data.data_dates.financial }}</span></div><div class="kv"><span class="k">两融截至</span><span class="num">{{ data.data_dates.margin }}</span></div><p class="small muted">研究输入采集于 {{ dateTime(data.acquired_at) }}。顶部行情持续刷新；报告计算使用本次采集输入，重新采集会生成新版本。</p><span class="tag">{{ state.kind === 'scenario' ? '同批数据 · 参数重算' : '现场采集记录' }}</span></div>
        <nav class="panel rail-box rail-toc" aria-label="实时报告目录"><a v-for="item in [['live-summary','结论速览'],['live-financial','基本面分析'],['live-valuation','估值与 DCF'],['live-market','技术面与资金面'],['live-risks','A股特色风险扫描'],['live-simulation','单股策略模拟'],['live-sources','数据与来源'] ]" :key="item[0]" href="#" @click.prevent="jump(item[0])">{{ item[1] }}</a></nav>
        <div class="panel rail-box rail-actions"><a :href="href('library')">查看历史记录</a><a v-if="state.parent_run_id" :href="href('live',state.parent_run_id)">查看重算前版本</a><a :href="'/api/quant/research/runs/' + id + '/export'" target="_blank" rel="noopener">导出研究报告</a></div>
      </aside>
      <article class="report-main">
        <h1 class="rep-h1">{{ subject.name }} · 最新数据研究报告</h1>
        <ConclusionOverview :data="data" :reading="reading" :reading-error="readingError" @evidence="showEvidence" @source="showSource" @derived="showDerived" />
        <section id="live-financial" class="panel rep-section"><h2>基本面分析</h2><PrototypeChart v-if="data.financial.kind" :block="data.financial" :height="300" /><p v-else class="notice">{{ data.financial.reason }}</p><p class="condition-note">{{ data.financial.title }}。同年累计金额相减得到单季；同比比较上年同季。财报公告日期 {{ data.data_dates.disclosed }}。</p></section>
        <section id="live-valuation" class="panel rep-section"><h2>估值分析</h2><div class="report-chart-title">{{ data.valuation.title }}</div><PrototypeChart v-if="data.valuation.kind" :block="data.valuation" /><p v-else class="notice">{{ data.valuation.reason }}</p><p class="condition-note">自算采用当日收盘 × 当日总发行股数 / 最近已披露 TTM 归母净利。历史财报取本次返回版本，缺少可得财报的日期留空；不作严格 PIT 历史选股依据。</p>
          <ReaderDcf :reading="reading" @derived="showDerived" />
          <h3>DCF 假设与重算</h3><template v-if="data.dcf.per_share != null"><p class="condition-note">现金流基准 {{ data.dcf.period }}；股数截至 {{ data.dcf.shares_date }}。</p><form @submit.prevent="recalc"><div class="live-inputs"><label v-for="item in inputFields" :key="item.key" class="field">{{ item.label }}<input v-model.number="form[item.key]" type="number" :min="item.min" :max="item.max" step="0.1" required /></label></div><div class="live-dcf-result"><div><span class="muted">每股条件情景估算</span><strong class="num">¥ {{ number(data.dcf.per_share) }}</strong></div><div><span class="muted">终值现值占企业价值</span><strong class="num">{{ number(data.dcf.terminal_weight) }}%</strong></div><button class="btn btn-primary" :disabled="busy">{{ busy ? '重算中…' : '重算并保存新版本' }}</button></div></form><PrototypeChart :block="data.dcf.chart" :height="260" /><div class="live-table-scroll"><table class="data"><thead><tr><th>折现率</th><th v-for="s in data.dcf.sensitivity" :key="s.discount">{{ number(s.discount,1) }}%</th></tr></thead><tbody><tr><td>情景估算（元/股）</td><td v-for="s in data.dcf.sensitivity" :key="s.discount">{{ number(s.per_share) }}</td></tr></tbody></table></div><p class="condition-note">{{ data.dcf.note }}</p><details><summary>查看 DCF 公式与实际输入</summary><p class="condition-note">FCFF = 调整后 EBIT × (1 − 有效税率) + 折旧摊销 − 资本开支 − 营运资本增加；股权价值 = 现金流折现 + 现金 − 有息债务 − 少数股东权益。</p><div class="live-table-scroll"><table class="data"><tbody><tr v-for="(v,k) in data.dcf.inputs" :key="k"><td>{{ dcfLabels[k] }}</td><td class="num">{{ number(v,k.includes('shares') ? 0 : 2) }}</td><td>{{ k.includes('shares') ? '股' : '元' }}</td></tr></tbody></table></div></details></template><p v-else class="notice">{{ data.dcf.status === 'NOT_APPLICABLE' ? '方法不适用：' : '输入未齐备：' }}{{ data.dcf.reason }}</p>
        </section>
        <section id="live-market" class="panel rep-section"><h2>技术面与资金面</h2><div class="report-chart-title">{{ data.kline.title }}</div><PrototypeChart :block="data.kline" :height="320" /><p class="condition-note">完整日线截至 {{ data.data_dates.price }}；均线按日收盘计算，成交量单位为手。</p><div class="report-chart-title">{{ data.margin.title }}</div><PrototypeChart v-if="data.margin.kind" :block="data.margin" :height="280" /><p v-else class="notice">{{ data.margin.reason }}</p><p class="condition-note">交易所日终两融数据，经东方财富提供，截至 {{ data.data_dates.margin }}；不等同于盘中资金流。</p></section>
        <section id="live-risks" class="panel rep-section"><h2>A股特色风险扫描</h2><div v-for="risk in data.risks" :key="risk.title" class="prototype-risk"><span class="tag tag-warn">资料观察</span><div><strong>{{ risk.title }}</strong><p>{{ risk.text }}</p></div></div></section>
        <section v-if="developer" class="panel rep-section"><h2>多源交叉核对</h2><div class="report-table-scroll"><div class="live-table-scroll"><table class="data"><thead><tr><th>项目</th><th>自算 / 新浪</th><th>东方财富</th><th>结果</th></tr></thead><tbody><tr v-for="check in data.checks" :key="check.title"><td>{{ check.title }}</td><td class="num">{{ number(check.left) }}</td><td class="num">{{ number(check.right) }}</td><td><span class="tag" :class="check.status === 'MATCH' ? 'tag-gold' : 'tag-warn'">{{ check.status }}</span></td></tr></tbody></table></div></div><p v-for="check in data.checks" :key="check.title" class="condition-note">{{ check.title }}：{{ check.note }}；绝对容差 {{ check.tolerance }}。</p></section>
        <section id="live-simulation" class="panel rep-section"><h2>单股策略研究模拟</h2><div class="condition-tags"><span class="tag tag-gold">{{ subject.name }} · 1 只股票</span><span class="tag">MA{{ data.simulation.fast }} / MA{{ data.simulation.slow }}</span><span class="tag">前日信号 / 次日开盘</span><span class="tag">单侧成本 {{ number(data.simulation.cost_pct) }}%</span></div><form class="live-simulation-form" @submit.prevent="recalc"><label class="field">快线天数<input v-model.number="form.fast" type="number" min="2" max="119" required /></label><label class="field">慢线天数<input v-model.number="form.slow" type="number" min="3" max="120" required /></label><label class="field">单侧成本 %<input v-model.number="form.cost_pct" type="number" min="0" max="2" step="0.01" required /></label><button class="btn" :disabled="busy">重算策略并保存</button></form><p class="condition-note">{{ data.simulation.note }}</p>
          <h3>绩效对比</h3><div class="live-table-scroll"><table class="data"><thead><tr><th>指标</th><th>策略</th><th>沪深300</th><th>差值</th></tr></thead><tbody><tr v-for="d in defs" :key="d.key"><td>{{ d.name }}</td><td :class="movement(data.simulation.metrics[d.key])">{{ number(data.simulation.metrics[d.key]) }}{{ d.unit }}</td><td :class="movement(data.simulation.benchmark_metrics[d.key])">{{ number(data.simulation.benchmark_metrics[d.key]) }}{{ d.unit }}</td><td>{{ number(data.simulation.metrics[d.key] - data.simulation.benchmark_metrics[d.key]) }}{{ d.unit }}</td></tr></tbody></table></div>
        </section>
        <section class="panel rep-section"><h2>{{ data.simulation.chart.title }}</h2><PrototypeChart :block="data.simulation.chart" :height="340" /></section>
        <section class="panel rep-section"><h2>分年度表现与交易明细</h2><div class="live-table-scroll"><table class="data"><thead><tr><th>年份</th><th>策略</th><th>沪深300</th><th>超额</th><th>调仓次数</th></tr></thead><tbody><tr v-for="a in data.simulation.annual" :key="a.year"><td>{{ a.year }}</td><td :class="movement(a.strategy)">{{ number(a.strategy) }}%</td><td :class="movement(a.benchmark)">{{ number(a.benchmark) }}%</td><td :class="movement(a.excess)">{{ number(a.excess) }}%</td><td>{{ a.trade_count }}</td></tr></tbody></table></div><details><summary>展开全部调仓明细（{{ data.simulation.trades.length }}）</summary><div class="report-table-scroll"><div class="live-table-scroll"><table class="data"><thead><tr><th>信号日期</th><th>模拟成交日</th><th>方向</th><th>前复权开盘价</th><th>扣减净值</th></tr></thead><tbody><tr v-for="(t,i) in data.simulation.trades" :key="i"><td>{{ t.signal_date }}</td><td>{{ t.date }}</td><td>{{ t.side }}</td><td>{{ number(t.price) }}</td><td>{{ number(t.cost_nav,6) }}</td></tr></tbody></table></div></div></details></section>
        <section id="live-sources" class="panel rep-section"><h2>数据与来源总览</h2><p class="condition-note">数据时间与原文可点击查看；重算使用本报告保存的原始资料。</p><div v-for="s in data.sources" :key="s.key" class="live-source-row"><button class="text-button" @click="showSource(s.key)">{{ sourceNames[s.key] || (s.key.startsWith('notice_') ? '公告正文 · ' + s.key.slice(7) : s.key) }}</button><time class="num muted small">{{ dateTime(s.retrieved_at) }}</time><span v-if="developer" class="num muted small">{{ s.sha256?.slice(0,12) }}…</span></div><p v-for="note in data.notes" :key="note" class="condition-note">{{ note }}</p></section>
        <section class="panel rep-section"><h2>继续研究</h2><p class="condition-note">追问关联本报告，重新获取最新数据。沿用父版本参数；旧数值不作为本轮事实。参数调整请使用上方重算表单。</p><form class="ask-box" @submit.prevent="askFollowup"><input v-model="followup" aria-label="研究追问" placeholder="例如：结合公告解释盈利变化与风险" maxlength="2000" /><button class="btn btn-primary" :disabled="busy || !followup.trim()">提交追问</button></form><p v-if="data.memory" class="small muted">已使用父任务研究上下文，当前事实均来自本轮输入。</p></section>
        <div class="disclaimer">本工具为投研参考，不构成投资建议。DCF 是参数情景，研究模拟不代表实盘或未来收益。</div>
      </article>
    </div>
    <dialog v-if="source" ref="sourceDialog" class="scope-dialog live-source-dialog" aria-label="数据来源" @close="source = null"><div class="dialog-head"><h2>{{ source.title || sourceNames[source.key] || source.key }}</h2><button class="icon-button" aria-label="关闭数据来源" @click="sourceDialog.close()">×</button></div><p>获取时间：{{ dateTime(source.retrieved_at) }}</p><p v-if="developer">原始响应大小：{{ source.byte_count }} 字节</p><p v-if="developer" class="num live-hash">SHA256 {{ source.sha256 }}</p><template v-if="source.evidence"><p class="condition-note">定位：{{ source.evidence.locator || ('字符 ' + source.evidence.start + '–' + source.evidence.end) }}</p><p class="evidence-text">{{ source.evidence.text }}</p></template><a :href="source.url" target="_blank" rel="noopener">打开上游来源</a><p v-if="developer" class="condition-note">哈希证明本次保存的响应未被更改，不证明供应商数值必然正确。</p></dialog>
  </div>
</template>
<style scoped>
.agent-trace { margin-bottom:20px }.evidence-text { white-space:pre-wrap;overflow-wrap:anywhere }
.live-actions { display:flex; gap:8px; flex-wrap:wrap }
.live-process { display:grid; grid-template-columns:repeat(5,1fr); gap:16px; margin-bottom:20px }
.live-process strong { display:block; margin-top:8px }.live-process p { color:var(--text-2); font-size:12px }.live-process > p { grid-column:1/-1; margin:0 }
.live-inputs { display:grid; grid-template-columns:repeat(4,1fr); gap:12px }.live-dcf-result { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:16px 0 }.live-dcf-result strong { display:block; font-size:24px; color:var(--accent) }
.live-simulation-form { display:flex; align-items:end; gap:12px; margin:16px 0 }.live-simulation-form .field { flex:1; min-width:0 }.live-simulation-form input { width:100% }
.live-source-row { display:flex; gap:12px; flex-wrap:wrap; justify-content:space-between; padding:10px 0; border-bottom:1px solid var(--border) }.live-source-row button { text-align:left }
.live-table-scroll { max-width:100%; overflow-x:auto }
.live-hash { overflow-wrap:anywhere }.live-research section[id] { scroll-margin-top:64px }.live-research details { margin-top:14px }.live-research summary { cursor:pointer; color:var(--accent-cyan) }
@media(max-width:760px) { .live-process { grid-template-columns:repeat(2,1fr) }.live-metrics,.live-inputs { grid-template-columns:repeat(2,1fr) }.live-dcf-result,.live-simulation-form { flex-wrap:wrap }.live-simulation-form .field { flex-basis:40% }.live-dcf-result strong { font-size:20px } }
</style>
