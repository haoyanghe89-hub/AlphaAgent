<script setup>
import { computed, onMounted, ref } from 'vue'
import request from '../api/request.js'
import { useQuantRun } from './useQuantRun.js'
import { href } from './navigation.js'
import { isActive, reportSummary, statusLabel } from './model.js'
import ProcessCard from './ProcessCard.vue'
import FollowupBar from './FollowupBar.vue'
const developer = new URLSearchParams(location.search).get('developer') === '1'
const props = defineProps({ id: String })
const { data, report, error, loading, load } = useQuantRun(props.id)
const cancelling = ref(false), cancelError = ref('')
const summary = computed(() => reportSummary(report.value))
onMounted(load)
async function cancel() {
  if (cancelling.value) return
  cancelling.value = true; cancelError.value = ''
  try { await request('/runs/' + props.id + '/cancel', { method: 'POST' }); await load() }
  catch (e) { cancelError.value = e.message }
  finally { cancelling.value = false }
}
</script>
<template>
  <div class="page"><div class="flow-wrap">
    <div v-if="loading && !data" class="panel panel-pad" role="status">正在读取研究任务…</div>
    <div v-if="error" class="notice error-text" role="alert">{{ error }} <button class="btn" @click="load">重试</button></div>
    <template v-if="data">
      <div class="msg-user">{{ data.plan?.question || '单股历史价格、财务与估值核对' }}</div>
      <div v-if="isActive(data.run.status)" class="flow-meta"><span>{{ statusLabel(data.run.status) }}</span><span class="num">{{ data.plan?.date_start }} — {{ data.plan?.date_end }}</span><button class="text-button" :disabled="cancelling" @click="cancel">{{ cancelling ? '正在停止…' : '停止研究' }}</button></div>
      <p v-if="cancelError" class="error-text" role="alert">{{ cancelError }}</p>
      <ProcessCard v-if="developer" :nodes="data.process" :status="data.run.status" />
      <article v-if="report" class="panel panel-pad report-summary-card">
        <div class="summary-head"><span class="tag tag-gold">研究报告</span><span class="muted">{{ data.scenario === 'compare' ? data.plan?.comparison?.subjects?.map(s => s.name).join(' vs ') : data.subject?.name }} · 数据截至 {{ data.plan?.date_end }} · v{{ report.version }}</span></div>
        <div v-for="(text,index) in summary.slice(0,3)" :key="index" class="summary-line"><span>›</span><p class="rep-block-text">{{ text }}</p></div>
        <div class="report-card-foot"><a class="btn btn-primary" :href="href('report', id)">阅读完整报告</a><span class="muted">{{ report.sections?.length }} 个章节 · {{ data.evidence_count }} 条溯源证据</span></div>
      </article>
      <section v-else-if="data.run.status === 'COMPLETED'" class="panel panel-pad"><h2>任务已结束</h2><p class="muted">尚无可读报告，请检查任务记录或重试读取。</p><button class="btn" @click="load">重新读取</button></section>
      <div v-if="data.parent_run_id" class="flow-meta">基于原冻结数据的追问版本 <a :href="href('report', data.parent_run_id)">查看父版本</a></div>
      <div v-if="data.scenario === 'compare'" class="panel panel-pad"><div class="section-title">真实研究支路</div><p v-for="branch in data.branches" :key="branch.child_run_id"><a :href="href('research',branch.child_run_id)">回看支路 {{ branch.instrument }}</a></p><p class="muted small">调整比较范围请回到工作台重新发起，已归档报告保留。</p></div>
      <FollowupBar v-else :id="id" :running="isActive(data.run.status)" />
    </template>
  </div></div>
</template>
