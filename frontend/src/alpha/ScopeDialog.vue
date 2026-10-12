<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'
import { commonWindows } from './model.js'
const props = defineProps({ question: String, instrument: Object, instruments: Array, window: { type: String, default: '1y' }, dateStart: String, dateEnd: String })
const emit = defineEmits(['close', 'started'])
const dialog = ref(null), windows = ref([]), selected = ref(''), start = ref(props.dateStart || ''), end = ref(props.dateEnd || ''), adjustment = ref('raw'), depth = ref('quick'), loading = ref(true), busy = ref(false), error = ref('')
let disposed = false
const frozen = computed(() => windows.value.find(w => w.window_id === selected.value))
const subjects = computed(() => props.instruments || [props.instrument])
const comparing = computed(() => subjects.value.length === 2)
const allowedBasis = basis => !frozen.value || frozen.value.adjustments.includes(basis)
onMounted(async () => {
  await nextTick(); dialog.value.showModal()
  try {
    const groups = await Promise.all(subjects.value.map(s => request('/quant/instruments/' + s.instrument_id + '/windows')))
    if (!disposed) {
      windows.value = comparing.value ? commonWindows(groups).filter(w => !props.dateStart || (w.date_start === props.dateStart && w.date_end === props.dateEnd)) : groups[0]
    }
  }
  catch (e) { if (!disposed) error.value = e.message }
  finally { if (!disposed) loading.value = false }
})
onUnmounted(() => { disposed = true })
function close() { if (!busy.value) emit('close') }
async function submit() {
  if (busy.value || loading.value || (comparing.value && !frozen.value)) return
  busy.value = true; error.value = ''
  const body = { instrument: props.instrument.instrument_id, question: props.question, window: props.window, adjustment: adjustment.value, depth: depth.value }
  if (frozen.value) Object.assign(body, { date_start: frozen.value.date_start, date_end: frozen.value.date_end, asof: frozen.value.asof, frozen_snapshot_ids: frozen.value.frozen_snapshot_ids })
  else if (start.value && end.value) Object.assign(body, { date_start: start.value, date_end: end.value })
  const payload = comparing.value ? {question:props.question, subjects:subjects.value.map((s,i) => ({...body, instrument:s.instrument_id, frozen_snapshot_ids:frozen.value.branches[i].frozen_snapshot_ids}))} : body
  try { const result = await request(comparing.value ? '/quant/comparisons' : '/quant/runs', { method: 'POST', body: JSON.stringify(payload) }); if (!disposed) emit('started', result) }
  catch (e) { if (!disposed) error.value = e.message }
  finally { if (!disposed) busy.value = false }
}
</script>
<template>
  <dialog ref="dialog" class="scope-dialog" aria-labelledby="scope-title" @cancel.prevent="close">
    <form @submit.prevent="submit">
      <div class="dialog-head"><h2 id="scope-title">确认研究范围</h2><button type="button" class="icon-button" aria-label="关闭范围确认" :disabled="busy" @click="close">×</button></div>
      <p class="scope-question">{{ question }}</p>
      <div v-for="s in subjects" :key="s.code" class="scope-stock"><strong>{{ s.name }}</strong><span class="num muted">{{ s.code }} · {{ s.exchange }}</span><span class="tag tag-gold">{{ comparing ? '对比分析' : '单股研究' }}</span></div>
      <p v-if="loading" role="status" class="muted">正在读取可用数据窗口…</p>
      <label class="field">数据窗口<select v-model="selected" aria-label="数据窗口" :disabled="loading || busy"><option value="">{{ comparing ? '请选择两股共同的历史冻结窗口' : start && end ? '问题指定区间 / 自定义' : '当前截至昨日 · ' + (window === '3y' ? '近三年' : '近一年') + ' / 自定义' }}</option><option v-for="w in windows" :key="w.window_id" :value="w.window_id">{{ w.date_start }} — {{ w.date_end }} · {{ w.sample_count }} 日 · 历史冻结</option></select></label>
      <p v-if="frozen" class="notice">使用已冻结历史数据，研究时点 {{ frozen.asof.slice(0,10) }}；结果不是最新行情。</p>
      <p v-else-if="comparing" class="notice">两股必须使用相同期间、研究时点与价格口径。{{ !loading && !windows.length ? '当前没有符合所提区间的共同窗口，不能自动换期比较。' : '本步比较仅接入明确选择的历史冻结数据。' }}</p>
      <div v-else class="scope-grid"><label class="field">开始日<input v-model="start" type="date" :disabled="busy" /></label><label class="field">结束日<input v-model="end" type="date" :disabled="busy" /></label></div>
      <div class="scope-grid"><label class="field">价格口径<select aria-label="价格口径" v-model="adjustment" :disabled="busy"><option value="raw" :disabled="!allowedBasis('raw')">未复权 · 价格收益</option><option value="qfq" :disabled="!allowedBasis('qfq')">前复权 · 冻结锚点代理</option><option value="hfq" :disabled="!allowedBasis('hfq')">后复权 · 冻结锚点代理</option></select></label><label class="field">研究深度<select aria-label="研究深度" v-model="depth" :disabled="busy"><option value="quick">快速核对</option><option value="standard">标准研究</option><option value="deep">深入核对</option></select></label></div>
      <p class="muted small">本次核对历史价格、收益风险、财务与估值口径。缺失数据会列入局限；数值由确定性工具计算，未校准指标保留未验证标识。</p>
      <p v-if="error" class="error-text" role="alert">{{ error }}</p>
      <div class="dialog-actions"><button type="button" class="btn" :disabled="busy" @click="close">取消</button><button class="btn btn-primary" :disabled="busy || loading || (comparing && !frozen) || !allowedBasis(adjustment) || (!frozen && Boolean(start) !== Boolean(end))">{{ busy ? '启动中…' : '确认并开始研究' }}</button></div>
    </form>
  </dialog>
</template>
