<script setup>
import { computed, onMounted, ref } from 'vue'
import { useQuantRun } from './useQuantRun.js'
import { number, scalarMetrics } from './model.js'
import { href } from './navigation.js'
import MetricGrid from './MetricGrid.vue'
import CellEvidence from './CellEvidence.vue'
import QuantChart from '../components/quant/QuantChart.vue'
import PrototypeChart from './PrototypeChart.vue'
import KlineChart from './KlineChart.vue'
const props = defineProps({ stock: Object })
const emit = defineEmits(['ask'])
const { data, error, loading, load } = useQuantRun(props.stock.run_id)
const cell = ref(null)
onMounted(() => { if (props.stock.run_id) load() })
const cells = computed(() => scalarMetrics(data.value).filter(c => ['return','volatility','drawdown','pe','pb','roe','revenue_growth','net_profit_growth'].includes(c.name)))
function selectCell(path) { cell.value = data.value.values.find(c => c.metric_path === path) }
const prototypeCharts = [
  { kind:'kline', title:'日K（近250日，前复权）', height:300, missing:'缺少已核对的开高低收、成交量与 MA20/MA60' },
  { kind:'financial_quarterly', title:'单季财务拆分（近12期）', height:240, chart_type:'bar', series:[{name:'单季营收(亿)',barMaxWidth:18,opacity:0.85},{name:'单季净利(亿)',barMaxWidth:18,opacity:0.85},{name:'营收同比%',symbol:'circle'}], missing:'缺少已核对的单季营收与归母净利金额' },
  { kind:'pe_history', title:'PE-TTM 全历史序列（自算口径）', height:220, chart_type:'line', series:[{name:'PE-TTM',lineStyle:{width:1.2}}], missing:'尚无时点财报与股本绑定的历史 PE 序列' },
  { kind:'margin', title:'融资融券（两融余额与净买入）', height:220, series:[{name:'融资余额(亿)',lineStyle:{width:1.5},areaStyle:{opacity:0.08}},{name:'融资净买入(亿)',type:'bar',axis:1,barMaxWidth:6}], missing:'暂无已核对的融资融券数据' },
]
</script>
<template>
  <div class="page stock-page">
    <div class="stock-heading"><div><div class="stock-name-line"><h1>{{ stock.name }}</h1><span class="num">{{ stock.code }} · {{ stock.exchange }}</span></div><div class="stock-price-line"><button v-if="stock.quote" class="stock-price num ev-num" aria-label="冻结收盘数值依据" @click="cell = stock.quote">{{ number(stock.quote.value) }}</button><span v-else class="stock-price num">—</span><span class="num muted" style="font-size:14px">—</span><span class="tag">历史冻结 · 非实时</span></div></div><div class="stock-actions"><span class="muted" style="font-size:11.5px">数据截至 {{ stock.quote?.row_keys?.[0] || '—' }}</span><button class="btn btn-primary" @click="emit('ask', '全面分析' + stock.name)">深入分析 →</button></div></div>
    <p v-if="stock.error || error" class="notice error-text" role="alert">{{ stock.error || error }} <button v-if="stock.run_id" class="btn" @click="load">重试</button></p>
    <div v-if="data" class="panel panel-pad"><MetricGrid :cells="cells" @cell="cell = $event" /></div>
    <div v-else class="panel empty-state" role="status">{{ stock.run_id && loading ? '正在读取成稿数据…' : '暂无成稿数据，点击深入分析并确认研究窗口。' }}</div>
    <div v-if="data" class="stock-charts"><div v-for="chart in prototypeCharts" :key="chart.kind" class="panel panel-pad"><KlineChart v-if="chart.kind === 'kline'" :data="data" @cell="selectCell" /><template v-else><div class="section-title">{{ chart.title }}</div><PrototypeChart :block="chart" :height="chart.height" :empty="chart.missing" /></template></div></div>
    <div v-if="data" class="stock-charts existing-stock-charts"><div v-for="chart in data.charts" :key="chart.exhibit_id" class="panel panel-pad"><QuantChart :exhibit="chart" @cell="selectCell" /></div></div>
    <div class="stock-bottom"><section class="panel panel-pad"><h2 class="section-title">A 股特色风险扫描</h2><p v-if="!data?.gaps?.length" class="muted">尚无完整风险扫描结果；缺少风险记录不能解释为低风险。</p><div v-for="(gap,index) in data?.gaps || []" :key="index" class="risk-row"><span class="tag tag-warn">未覆盖</span><p>{{ Array.isArray(gap) ? gap[1] : gap }}</p></div></section><section class="panel panel-pad"><h2 class="section-title">资金面与筹码</h2><table class="data"><tbody><tr v-for="label in ['股东户数','股东户数变化','融资余额','近5日融资净买入','融资余额20日变化']" :key="label"><td>{{ label }}</td><td>—</td></tr></tbody></table><p class="muted small">暂无已校验数据，暂不形成分析结论。</p></section><section class="panel panel-pad"><h2 class="section-title">多源交叉校验</h2><span class="tag tag-warn">尚未完成权威口径核对</span><p class="muted small">复算一致只证明可复现，未验证指标不能升级为已核验。</p><a v-if="stock.run_id" :href="href('report', stock.run_id)">阅读已有报告</a></section></div>
    <div class="disclaimer">本工具为投研参考，不构成投资建议。估值与财务数值仍需核对原始披露及计算口径。</div>
    <CellEvidence v-if="cell" :cell="cell" :data="data" @close="cell = null" />
  </div>
</template>
