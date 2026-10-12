<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { useFollowup } from './useFollowup.js'
import { href } from './navigation.js'
import { metricValue, number, movement } from './model.js'
const props = defineProps({ data: Object })
const emit = defineEmits(['cell'])
const dcf = computed(() => props.data?.dcf)
const controls = [
  { id:'g1', name:'阶段1增速（5年）', min:-20, max:40, step:1 },
  { id:'g2', name:'阶段2增速（5年）', min:0, max:15, step:0.5 },
  { id:'g_term', name:'永续增长率', min:0, max:4, step:0.25 },
  { id:'discount', name:'折现率(WACC)', min:5, max:15, step:0.5 },
]
const vals = ref({})
watch(() => dcf.value?.assumptions, a => { vals.value = Object.fromEntries(controls.map(c => [c.id, a?.[c.id] == null ? null : Number(a[c.id]) * 100])) }, { immediate:true })
const available = computed(() => dcf.value?.cells?.per_share?.value != null)
const rateColumns = computed(() => [...new Set((dcf.value?.sensitivity || []).map(c => c.row_keys[1]))])
const growthRows = computed(() => [...new Set((dcf.value?.sensitivity || []).map(c => c.row_keys[0]))].map(g => ({ g, cells:rateColumns.value.map(r => dcf.value.sensitivity.find(c => c.row_keys[0] === g && c.row_keys[1] === r)) })))
const invalid = computed(() => Number(vals.value.discount) <= Number(vals.value.g_term))
const { busy, error, submit, dispose } = useFollowup(props.data?.run?.run_id, { started:result => { location.hash = href('research', result.run_id) } })
onUnmounted(dispose)
const proof = cell => cell && emit('cell', cell.metric_path)
async function recalc() {
  if (!available.value || invalid.value) return
  const assumptions = Object.fromEntries(controls.map(c => [c.id, String(Number(vals.value[c.id]) / 100)]))
  await submit('DCF 按当前假设重算', { dcf:assumptions })
}
</script>
<template>
  <div class="dcf-playground">
    <div class="report-chart-title">假设与参数（可修改后重算）</div>
    <div class="dcf-controls"><div v-for="c in controls" :key="c.id" class="dcf-ctl"><label :for="'dcf-' + c.id"><span>{{ c.name }}</span><span class="val">{{ vals[c.id] == null ? '—' : vals[c.id] + '%' }}</span></label><input :id="'dcf-' + c.id" v-model.number="vals[c.id]" type="range" :min="c.min" :max="c.max" :step="c.step" :aria-label="c.name" :disabled="!available || busy" /></div></div>
    <div class="dcf-action"><button class="btn btn-primary" :disabled="!available || busy || invalid" @click="recalc">{{ busy ? '重算中…' : '按当前假设重算' }}</button><span>假设显式可改，重算生成新版本；这是测算工具，不是预测。</span></div>
    <p v-if="error || invalid" class="error-text" role="alert">{{ error || '折现率必须高于永续增长率' }}</p>
    <p v-if="!available" class="muted small" role="status">缺少适用的已归档官方现金流、债务与股数输入；金融企业需单独估值模型，不能套用工业企业 FCFF。</p>
    <div v-else class="dcf-result">
      <div><div class="muted small">每股情景估算（两阶段 DCF，元）</div><button class="big ev-num num" @click="proof(dcf.cells.per_share)">{{ metricValue(dcf.cells.per_share) }}</button></div>
      <div><div class="muted small">参考收盘价（{{ dcf.cells.reference_price?.end || '—' }}）</div><button class="big ev-num num" style="font-size:15px" @click="proof(dcf.cells.reference_price)">{{ metricValue(dcf.cells.reference_price) }} 元</button></div>
      <div><div class="muted small">较参考价（非预期收益）</div><button class="big ev-num num" :class="movement(dcf.cells.reference_difference?.value)" @click="proof(dcf.cells.reference_difference)">{{ metricValue(dcf.cells.reference_difference) }}</button></div>
      <div><div class="muted small">年报估值基准</div><span class="num">{{ dcf.base_date }}</span></div>
      <div><span class="tag tag-warn">情景测算 · 尚未验证</span><span class="tag">{{ dcf.input_check === 'OFFICIAL_INPUT_MATCH' ? '年报输入核对一致' : '输入待核对' }}</span></div>
      <p class="muted small dcf-note">{{ dcf.note }} <a v-if="dcf.source_url" :href="dcf.source_url" target="_blank" rel="noopener">查看官方年报</a></p>
      <div class="report-table-scroll dcf-matrix"><div class="report-chart-title">DCF 敏感性矩阵（每股价值，元）</div><table class="data"><thead><tr><th>增速 \ 折现率</th><th v-for="r in rateColumns" :key="r">{{ number(Number(r) * 100, 1) }}%</th></tr></thead><tbody><tr v-for="row in growthRows" :key="row.g"><td>增速 {{ number(Number(row.g) * 100, 1) }}%</td><td v-for="(c,i) in row.cells" :key="i"><button class="ev-num num" :class="c?.value != null && dcf.cells.reference_price?.value != null ? (Number(c.value) < Number(dcf.cells.reference_price.value) ? 'down' : 'up') : 'flat'" :disabled="!c" @click="proof(c)">{{ c ? metricValue(c) : '—' }}</button></td></tr></tbody></table></div>
      <details class="dcf-inputs"><summary>现金流基数与企业价值到股权价值的转换</summary><table class="data"><tbody><tr v-for="[key,label] in [['fcff','基准 FCFF（元）'],['delta_ncwc','营运资本变动代理（元）'],['enterprise','企业情景价值（元）'],['cash','现金及等价物（元）'],['debt','有息债务账面值（元）'],['minority','少数权益账面值（元）'],['shares','发行股份减回购专户股数（股）'],['equity','情景股权价值（元）']]" :key="key"><td>{{ label }}</td><td><button class="ev-num num" @click="proof(dcf.cells[key])">{{ metricValue(dcf.cells[key]) }}</button></td></tr></tbody></table></details>
    </div>
  </div>
</template>
