<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'
import { useEvidencePopover } from './useEvidencePopover.js'
const props = defineProps({ runId:String, evidenceId:String, predictionId:String, developer:Boolean })
const emit = defineEmits(['close'])
const dialog = ref(null), data = ref(null), error = ref(''), loading = ref(true)
const excerpt=computed(()=>{
  const item=data.value?.evidence
  if(item?.source_family==='DECISION_ATTRIBUTION_CALC')return item.text.split('：')[0]+'：'+(Number(item.value)*100).toFixed(2)+(item.evidence_id.includes('relative_')?' 个百分点':'%')+'。未复权价格观察，不是事件贡献。'
  if(item?.kind!=='calculation')return item?.text
  const label=item.text.split('：')[0],value=Number(item.value)
  return label+'：'+(item.evidence_id==='NS-TURN'?value.toFixed(3)+' 倍':(value*100).toFixed(2)+(item.evidence_id==='NS-REL5'?' 个百分点':'%'))
})
let disposed = false
const outside = useEvidencePopover(dialog, () => emit('close'))
onMounted(async () => {
  try { const value = await request(props.predictionId ? '/quant/decision/experiments/next-session/runs/' + encodeURIComponent(props.runId) + '/evidence/' + encodeURIComponent(props.predictionId) + '/' + encodeURIComponent(props.evidenceId) : '/quant/decision/runs/' + encodeURIComponent(props.runId) + '/evidence/' + encodeURIComponent(props.evidenceId)); if (!disposed) data.value = value }
  catch(e) { if (!disposed) error.value = e.message }
  finally { if (!disposed) loading.value = false }
})
onUnmounted(() => { disposed = true })
</script>
<template>
  <dialog ref="dialog" class="ev-pop evidence-dialog" aria-labelledby="decision-citation-title" @cancel.prevent="emit('close')" @pointerdown="outside">
    <div class="dialog-head"><h2 id="decision-citation-title">引用原文</h2><button class="icon-button" aria-label="关闭引用原文" @click="emit('close')">×</button></div>
    <p v-if="loading" role="status">正在核对引用位置…</p><p v-if="error" class="error-text" role="alert">{{ error }}</p>
    <template v-if="data">
      <h3>{{ data.document?.title || ({TENCENT:'腾讯行情',SINA:'新浪行情',DETERMINISTIC_CALCULATION:'根据已保存行情计算',DECISION_FLOW_CALC:'根据本次封存数据分渠道计算',DECISION_ATTRIBUTION_CALC:'根据同期封存行情计算',WSTS:'WSTS 原始发布',SEMI:'SEMI 原始发布',CNINFO_EVENT:'公司公告目录标题',EASTMONEY_MARGIN:'融资渠道观察',POLICY_CN:'中国官方政策原文',POLICY_US:'美国官方政策原文'}[data.evidence.source_family] || '资料依据') }}</h3>
      <p class="condition-note">{{ data.evidence.locator.page ? 'PDF 第 ' + data.evidence.locator.page + ' 页' : '资料时点：' + new Date(data.evidence.observed_at || data.evidence.published_at || data.evidence.available_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) }}</p>
      <p class="evidence-excerpt">{{ excerpt }}</p>
      <p class="condition-note">{{ data.evidence.limitations.join('；') }}</p>
      <a v-if="data.document" class="btn" :href="'/api/quant/decision/runs/' + (data.evidence_run_id || runId) + '/documents/' + data.document.document_id + '/raw#page=' + data.evidence.locator.page" target="_blank" rel="noopener noreferrer">打开本次归档原文</a>
      <a class="btn" :href="data.evidence.url" target="_blank" rel="noopener noreferrer">{{ data.evidence.source_family==='DECISION_FLOW_CALC'?'查看交易所口径说明':'打开来源网站' }}</a>
      <details v-if="developer"><summary>开发校验信息</summary><pre>{{ JSON.stringify(data.evidence, null, 2) }}</pre></details>
    </template>
  </dialog>
</template>
