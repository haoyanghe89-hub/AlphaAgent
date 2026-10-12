<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import BacktestReport from './BacktestReport.vue'
import { useBacktest } from './useBacktest.js'
const props = defineProps({ id:String })
const emit = defineEmits(['started'])
const { data, error, busy, load, submit, acquire, audit, filings, benchmark, lifecycles, notices, stop, dispose } = useBacktest({ started:r => emit('started',r) })
const noticeParent = computed(() => data.value?.notice_audit ? data.value.parent_run_id : data.value?.run_id)
const lifecycleParent = computed(() => data.value?.lifecycle_audit ? data.value.parent_run_id : data.value?.run_id)
const benchmarkParent = computed(() => data.value?.benchmark_audit ? data.value.parent_run_id : data.value?.run_id)
const filingParent = computed(() => data.value?.filing_audit ? data.value.parent_run_id : data.value?.run_id)
const instrument = ref(''), signalYear = ref('')
const auditParent = computed(() => data.value?.input_audit ? data.value.parent_run_id : data.value?.run_id)
const auditAvailable = computed(() => data.value?.check_status === 'COMPLETED' || (data.value?.input_audit && ['FAILED','CANCELLED','INTERRUPTED'].includes(data.value.check_status)))
watch(() => data.value?.run_id, () => {
  const report = data.value
  instrument.value = report?.input_audit?.scope.instrument_id || report?.data_check?.instruments?.[0]?.instrument_id || ''
  signalYear.value = report?.input_audit?.scope.signal_year || report?.historical_data?.annual?.at(-1)?.year || ''
})
const presets = [{ name:'低PE高ROE', pe:30, roe:15, rev:null }, { name:'低PE高ROE高增长', pe:30, roe:15, rev:15 }, { name:'放宽版', pe:40, roe:10, rev:null }]
const form = ref({ ...presets[0], years:5, cost:0.13 })
function preset(value) { form.value = { ...value, years:5, cost:0.13 } }
const conditions = computed(() => ({ pe_max: form.value.pe === '' ? null : form.value.pe, roe_min: form.value.roe === '' ? null : form.value.roe, rev_yoy_min: form.value.rev === '' ? null : form.value.rev, cost_pct_per_side: form.value.cost }))
async function run() { await submit({ ...conditions.value, years:form.value.years }) }
onMounted(async () => { if (props.id) { const r = await load(props.id); if (r) form.value = { pe:r.conditions.pe_max, roe:r.conditions.roe_min, rev:r.conditions.rev_yoy_min, years:r.conditions.years, cost:r.conditions.cost_pct_per_side } } })
onUnmounted(dispose)
</script>
<template><div class="page backtest-page"><section class="panel panel-pad" style="margin-bottom:20px"><div class="section-heading"><h2>单股实时演示</h2><a class="btn btn-primary" href="#/live/new">现场取数并运行完整研究</a></div><p class="muted">美的集团：真实日线、均线策略、沪深300对比与成本敏感性。下方保留全市场历史数据准入工作台。</p></section><section class="panel panel-pad bt-header">
  <div class="section-heading"><h1>历史数据取证工作台</h1><a class="btn btn-primary" href="#/strategy">执行完整策略回测</a><span class="muted">条件确认后检查历史数据，准入通过才执行年度调仓与绩效计算</span></div>
  <div class="ask-chips bt-presets"><button v-for="p in presets" :key="p.name" class="chip" @click="preset(p)">{{ p.name }}</button></div>
  <div class="bt-form"><div class="bt-field"><label for="bt-pe">PE 上限（≤）</label><input id="bt-pe" v-model="form.pe" type="number" step="1" /></div><div class="bt-field"><label for="bt-roe">ROE 下限（≥%）</label><input id="bt-roe" v-model="form.roe" type="number" step="1" /></div><div class="bt-field"><label for="bt-rev">营收同比下限（≥%，可空）</label><input id="bt-rev" v-model="form.rev" type="number" step="1" /></div><div class="bt-field"><label for="bt-years"><span>回测年数</span><span class="val">{{ form.years }} 年</span></label><input id="bt-years" v-model="form.years" type="number" min="3" max="8" step="1" /></div><div class="bt-field"><label for="bt-cost"><span>单侧交易成本（%）</span><span class="val">{{ form.cost }}%</span></label><input id="bt-cost" v-model="form.cost" type="number" min="0" max="1" step="0.01" /></div></div>
  <div class="bt-actions"><button class="btn btn-primary" :disabled="busy" @click="run">{{ busy ? '正在检查数据…' : '确认条件并执行' }}</button><span class="bt-condition">条件：PE≤{{ form.pe === '' || form.pe == null ? '∞' : form.pe }} / ROE≥{{ form.roe === '' || form.roe == null ? '—' : form.roe }}%{{ form.rev != null && form.rev !== '' ? ' / 营收同比≥' + form.rev + '%' : '' }}，近 {{ form.years }} 年 vs 沪深300</span></div>
  <p class="bt-description">本阶段执行真实历史输入检查；缺少历史股票池、财报版本或成交限制时停止计算，并保存缺项报告。修改上方条件后提交将创建新任务，下方保留当前任务的冻结条件。</p>
  <div v-if="data" class="bt-actions"><button v-if="data.check_status !== 'RUNNING' && data.check_status !== 'INTERRUPTED'" class="btn btn-primary" :disabled="busy" @click="acquire(data.run_id)">{{ data.historical_data ? '重新获取历史股票池与基准' : '获取历史股票池与基准' }}</button><button v-if="data.check_status === 'RUNNING'" class="btn" :disabled="busy" @click="stop(data.run_id)">停止采集</button><a v-if="data.parent_run_id" class="btn" :href="'#/backtest/' + data.parent_run_id">查看上一个冻结版本</a><span class="muted">仅采集当前报告冻结期间；编辑上方条件后请先提交新检查。</span></div>
  <div v-if="data?.historical_data" class="bt-input-controls">
    <div class="bt-form"><div class="bt-field"><label for="bt-instrument">核验证券</label><select id="bt-instrument" v-model="instrument" :disabled="busy || data.check_status === 'RUNNING'"><option v-for="stock in data.data_check.instruments" :key="stock.instrument_id" :value="stock.instrument_id">{{ stock.name }}</option></select></div><div class="bt-field"><label for="bt-signal-year">财报信号年度</label><select id="bt-signal-year" v-model.number="signalYear" :disabled="busy || data.check_status === 'RUNNING'"><option v-for="annual in data.historical_data.annual" :key="annual.year" :value="annual.year">{{ annual.year }} 年 · {{ annual.signal_day }}</option></select></div></div>
    <div class="bt-actions"><button class="btn btn-primary" :disabled="busy || !auditAvailable || !instrument || !signalYear" @click="audit(auditParent, instrument, signalYear)">核验股票历史输入</button><span class="muted">单股核验整个冻结区间的原始价格与分红，抽查所选年度三期财报；重新核验沿用父历史版本并保存新记录。</span></div>
  </div>
  <div v-if="data?.input_audit && !data?.benchmark_audit && !data?.lifecycle_audit" class="bt-actions"><button class="btn btn-primary" :disabled="busy || (data.check_status !== 'COMPLETED' && !data.filing_audit) || data.check_status === 'RUNNING'" @click="filings(filingParent)">核对官方财报原文</button><span class="muted">核对本报告冻结的 {{ data.input_audit.scope.name }} · {{ data.input_audit.scope.signal_year }} 年信号；新选择请先提交股票输入核验。原文及口径核对不等于历史版本准入。</span></div>
  <div v-if="data?.historical_data" class="bt-actions"><button class="btn btn-primary" :disabled="busy || data.check_status === 'RUNNING' || (data.check_status !== 'COMPLETED' &amp;&amp; !data.benchmark_audit)" @click="benchmark(benchmarkParent)">核对官方基准</button><span class="muted">按当前冻结日历分段取得价格／全收益指数，逐日核对并另存版本。</span></div>
  <div v-if="data?.historical_data" class="bt-actions"><button class="btn btn-primary" :disabled="busy || data.check_status === 'RUNNING' || (data.check_status !== 'COMPLETED' &amp;&amp; !data.lifecycle_audit)" @click="lifecycles(lifecycleParent)">核对免费交易所档案</button><span class="muted">冻结沪深官方上市／终止上市资料，逐年对比历史名单；回溯观察不等于完整PIT档案。</span></div>
  <div v-if="data?.lifecycle_audit" class="bt-actions"><button class="btn btn-primary" :disabled="busy || data.check_status === 'RUNNING' || (data.check_status !== 'COMPLETED' &amp;&amp; !data.notice_audit)" @click="notices(noticeParent)">核对历史改码公告</button><span class="muted">对已复核的改码事件冻结发行人与国证两份原文，核对生效日；不代表全部公司行为档案完整。</span></div>
  <p v-if="error" class="error-text" role="alert">{{ error }} <button v-if="id" class="btn" :disabled="busy" @click="load(id)">重新读取</button></p>
</section><BacktestReport :conditions="conditions" :report="data" :task-id="id" /></div></template>
