<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'
import ReportBody from './ReportBody.vue'
import CitationDialog from './CitationDialog.vue'
const props = defineProps({ id: String })
const archive = ref(null), report = ref(null), citations = ref([]), citation = ref(''), error = ref(''), loading = ref(true)
let disposed = false
async function load() {
  loading.value = true; error.value = ''
  try {
    const [detail, runs] = await Promise.all([request('/investigations/' + props.id), request('/investigations/' + props.id + '/runs')])
    if (disposed) return
    archive.value = detail
    for (const run of runs) {
      const reports = await request('/runs/' + run.run_id + '/reports')
      if (disposed) return
      if (!reports.length) continue
      const [body, refs] = await Promise.all([request('/reports/' + reports[0].report_id), request('/reports/' + reports[0].report_id + '/citations')])
      if (disposed) return
      report.value = { ...body.report, sections: body.sections }; citations.value = refs; break
    }
  } catch(e) { if (!disposed) error.value = e.message }
  finally { if (!disposed) loading.value = false }
}
onMounted(load); onUnmounted(() => { disposed = true })
</script>
<template><div class="page legacy-page"><div class="section-heading"><h1>{{ archive?.title || '既有调查档案' }}</h1><span class="tag">只读回看</span></div><p v-if="error" class="notice error-text" role="alert">{{ error }} <button class="btn" @click="load">重试</button></p><div v-if="loading" class="panel empty-state" role="status">正在读取归档…</div><div v-else-if="!report" class="panel empty-state">该档案暂无报告。</div><template v-if="report"><ReportBody :report="report" :citations="citations" @citation="citation = $event" /><a class="btn" :href="'/api/reports/' + report.report_id + '/export?format=markdown'">导出归档报告</a></template><CitationDialog v-if="citation" :key="citation" :id="citation" @close="citation = ''" /></div></template>
