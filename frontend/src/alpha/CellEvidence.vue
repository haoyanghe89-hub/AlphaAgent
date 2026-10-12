<script setup>
import { ref } from 'vue'
import { useEvidencePopover } from './useEvidencePopover.js'
import { metricNames, metricValue } from './model.js'
const props = defineProps({ cell: Object, data: Object })
const emit = defineEmits(['close'])
const dialog = ref(null)
const outside = useEvidencePopover(dialog, () => emit('close'))
</script>
<template>
  <dialog ref="dialog" class="ev-pop evidence-dialog" aria-labelledby="cell-title" @cancel.prevent="emit('close')" @pointerdown="outside">
    <div class="dialog-head"><h2 id="cell-title">数值依据</h2><button class="icon-button" aria-label="关闭数值依据" @click="emit('close')">×</button></div>
    <h3>{{ metricNames[cell.name] || cell.name }}</h3><div class="evidence-value num">{{ metricValue(cell) }} <span class="tag tag-warn">{{ cell.status === 'VERIFIED' ? '已核验' : '尚未验证' }}</span></div>
    <dl class="evidence-rows"><dt>原值 / 单位</dt><dd class="num">{{ cell.value ?? '缺失' }} / {{ cell.unit }}</dd><dt>指标期间</dt><dd class="num">{{ cell.start }} — {{ cell.end }}</dd><dt>数据来源</dt><dd>{{ cell.provider || '冻结输入与确定性计算' }}</dd><dt>缺失原因</dt><dd>{{ cell.missing_reason || '无' }}</dd></dl>
    <p class="notice">复算一致证明可复现。未完成权威披露与口径核对的指标不能据此视为已证实。</p>
    <details><summary>计算口径与归档定位</summary><p>{{ cell.definition }}</p><pre>{{ JSON.stringify({ artifact_id: cell.artifact_id, metric_path: cell.metric_path, cell_hash: cell.cell_hash, row_keys: cell.row_keys, input_snapshot_ids: data?.input_snapshot_ids, manifest_hash: data?.manifest_hash }, null, 2) }}</pre></details>
    <a v-if="cell.citation_id" :href="'/api/citations/' + encodeURIComponent(cell.citation_id)" target="_blank" rel="noopener">查看完整验证记录</a>
  </dialog>
</template>
