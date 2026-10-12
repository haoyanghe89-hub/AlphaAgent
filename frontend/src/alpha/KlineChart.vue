<script setup>
import { computed, ref, watch } from 'vue'
import PrototypeChart from './PrototypeChart.vue'
import { metricValue } from './model.js'
const props = defineProps({ data:Object })
const emit = defineEmits(['cell'])
const adjustment = ref('qfq'), selected = ref('')
const blocks = computed(() => props.data?.klines || [])
const block = computed(() => blocks.value.find(b => b.adjustment === adjustment.value) || blocks.value[0])
watch(block, b => { selected.value = b?.x?.at(-1) || '' }, { immediate:true })
const cells = computed(() => Object.entries(block.value?.cells || {}).map(([key,items]) => [key,items.find(c => c.row_keys[0] === selected.value)]))
const labels = { open:'开盘', close:'收盘', high:'最高', low:'最低', volume:'成交量（股）', ma20:'MA20', ma60:'MA60' }
</script>
<template><div class="kline-block">
  <div class="report-chart-title">{{ block?.title || '日K线（冻结历史）+ MA20/MA60' }}</div>
  <div v-if="blocks.length" class="kline-actions"><label>价格口径 <select v-model="adjustment" aria-label="K线价格口径"><option v-for="b in blocks" :key="b.adjustment" :value="b.adjustment">{{ {raw:'未复权',qfq:'前复权',hfq:'后复权'}[b.adjustment] }}</option></select></label><span class="muted small">{{ block.x[0] }} — {{ block.x.at(-1) }} · {{ block.x.length }} 日 · 非实时</span></div>
  <PrototypeChart :block="block || { kind:'kline' }" :empty="block ? undefined : '缺少冻结的真实开高低收与成交量数据'" @point="selected = $event" />
  <p v-if="block" class="muted small">{{ block.note }}</p>
  <details v-if="block" class="kline-evidence"><summary>逐日 OHLC、成交量与均线依据</summary><label>交易日 <select v-model="selected" aria-label="K线证据交易日"><option v-for="day in block.x" :key="day">{{ day }}</option></select></label><table class="data"><tbody><tr v-for="[key,c] in cells" :key="key"><td>{{ labels[key] }}</td><td><button class="ev-num num" @click="c && emit('cell', c.metric_path)">{{ metricValue(c) }}</button></td><td class="muted small">{{ c?.missing_reason || c?.unit }}</td></tr></tbody></table></details>
</div></template>
