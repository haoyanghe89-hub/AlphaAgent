<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'
import { useEvidencePopover } from './useEvidencePopover.js'
const props = defineProps({ id: String })
const emit = defineEmits(['close'])
const dialog = ref(null), data = ref(null), loading = ref(true), error = ref('')
let disposed = false
async function load() {
  loading.value = true; error.value = ''; data.value = null
  try { const result = await request('/citations/' + encodeURIComponent(props.id)); if (!disposed) data.value = result }
  catch(e) { if (!disposed) error.value = e.message }
  finally { if (!disposed) loading.value = false }
}
const outside = useEvidencePopover(dialog, () => emit('close'))
onMounted(load)
onUnmounted(() => { disposed = true })
</script>
<template>
  <dialog ref="dialog" class="ev-pop evidence-dialog" aria-labelledby="citation-title" @cancel.prevent="emit('close')" @pointerdown="outside"><div class="dialog-head"><h2 id="citation-title">证据溯源</h2><button class="icon-button" aria-label="关闭证据溯源" @click="emit('close')">×</button></div><p v-if="loading" role="status">正在读取引用…</p><p v-if="error" class="error-text" role="alert">{{ error }} <button class="btn" @click="load">重试</button></p><template v-if="data"><h3>{{ data.claim?.statement || '证据记录' }}</h3><span class="tag tag-warn">{{ data.claim?.validation_status === 'VERIFIED' ? '已核验' : '未验证' }}</span><p class="evidence-excerpt">{{ data.evidence?.content }}</p><p v-if="data.evidence?.locator_type === 'COMPUTATION_CELL'" class="notice">冻结计算单元格，可核对公式、输入和复算记录。可复现不等于口径已校准。</p><dl class="evidence-rows"><dt>定位类型</dt><dd>{{ data.evidence?.locator_type }}</dd><dt>来源</dt><dd>{{ data.source?.title || data.source?.url || '计算产物与冻结输入' }}</dd></dl><details><summary>完整证据与验证记录</summary><pre>{{ JSON.stringify(data, null, 2) }}</pre></details></template></dialog>
</template>
