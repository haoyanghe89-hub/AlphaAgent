<script setup>
import { computed, ref, watch } from 'vue'
import { useDecisionResearch } from './useDecisionResearch.js'
import DecisionCitation from './DecisionCitation.vue'
const props = defineProps({ id:String, developer:Boolean })
const entity = ref('中微公司'), question = ref('核查近期经营变化、产品需求与现金回收的依据和风险'), mode = ref('latest'), cutoff = ref('')
const { state, history, error, submitting, loading, start, load, action, active } = useDecisionResearch(props.id)
const citation = ref(null)
const report = computed(() => state.value?.data?.report)
const evidence = computed(() => state.value?.data?.bundle?.items || [])
const documents = computed(() => state.value?.data?.documents || [])
const names = { periodic:'定期报告', earnings:'业绩预告/快报', investor:'投资者关系', daily:'日行情', margin:'融资渠道' }
const statuses = {RUNNING:'取证中',PENDING:'等待取证',COMPLETED:'取证完成',FAILED:'取证失败',INTERRUPTED:'取证中断',CANCELLED:'已停止'}
const qualityLabel = computed(() => state.value?.status === 'COMPLETED' ? ({PARTIAL:'资料有缺口',INSUFFICIENT:'资料不足'}[report.value?.research_status] || '取证完成') : statuses[state.value?.status])
function stamp(v) { return v ? new Date(v).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}) : '未知' }
watch(state, value => { if (value?.request) { entity.value = value.request.entity; question.value = value.request.question; mode.value = value.request.mode; cutoff.value = value.request.cutoff_at ? new Date(value.request.cutoff_at).toLocaleString('sv-SE', {timeZone:'Asia/Shanghai'}).replace(' ', 'T').slice(0,16) : '' } })
function submit() {
  if (!entity.value.trim() || !question.value.trim() || mode.value === 'historical' && !cutoff.value) return
  start({ entity:entity.value.trim(), question:question.value.trim(), horizon_months:3, mode:mode.value, cutoff_at:mode.value === 'historical' ? new Date(cutoff.value + '+08:00').toISOString() : null })
}
</script>
<template>
  <section class="panel rep-section">
    <div class="section-heading"><h2>单股定向取证</h2><span class="tag">资料与依据</span></div>
    <p class="condition-note">输入公司和研究问题，取得相关原文、行情及融资资料。当前阶段整理事实与缺口，尚未接通模型分析结论。</p>
    <details :open="!id" class="decision-query"><summary>{{ id ? '查看或调整取证条件' : '输入公司与研究问题' }}</summary>
    <form class="decision-evidence-form" @submit.prevent="submit">
      <label>公司名称或代码<input v-model="entity" required maxlength="80" placeholder="如：中微公司、002371" /></label>
      <label class="evidence-question">要核查的问题<textarea v-model="question" required maxlength="2000" rows="2" /></label>
      <label>资料时点<select v-model="mode"><option value="latest">本次最新取证</option><option value="historical">历史归档核查</option></select></label>
      <label v-if="mode === 'historical'">截止时间（北京时间）<input v-model="cutoff" type="datetime-local" required /></label>
      <button class="btn btn-primary" :disabled="submitting || active(state) || mode === 'historical' && !cutoff" type="submit">{{ submitting ? '提交中…' : '开始取证' }}</button>
    </form>
    <p class="condition-note">最新取证在完成后封存资料时点；历史核查只读当时已取得的归档，不用今天抓取的旧报告补历史。</p>
    </details>
    <p v-if="error" class="notice error-text" role="alert">{{ error }} <button v-if="id" class="btn" @click="load">重读记录</button></p>
  </section>
  <section v-if="state" class="panel rep-section decision-evidence-result">
    <div class="section-heading"><h2>{{ report?.title || state.title }}</h2><span :class="['tag', report?.research_status === 'AVAILABLE' ? 'tag-cyan' : 'tag-warn']">{{ qualityLabel || state.status }}</span></div>
    <template v-if="report">
      <h3>{{ report.headline }}</h3><p>{{ report.summary }}</p>
      <p class="condition-note">资料截止：{{ stamp(report.cutoff_at) }} · {{ state.subject?.security_id }} · {{ report.scope_note }}</p>
      <div class="evidence-materials"><span v-for="(count, type) in report.material_counts" :key="type">{{ names[type] }} {{ count }} 份</span></div>
      <h3>原文与引用</h3>
      <article v-for="doc in documents" :key="doc.document_id" class="decision-document">
        <strong>{{ doc.title }}</strong><p class="condition-note">{{ names[doc.category] }} · {{ doc.page_count }} 页 · 报告期 {{ doc.period_start || '未核定' }} 至 {{ doc.period_end || '未核定' }}<br>公告日期 {{ doc.published_at ? stamp(doc.published_at).split(' ')[0] : '未知' }}（日精度）；本次取得 {{ stamp(doc.fetched_at) }}</p>
        <div class="evidence-links"><a :href="doc.url" target="_blank" rel="noopener noreferrer">来源原文</a><a :href="'/api/quant/decision/runs/' + state.run_id + '/documents/' + doc.document_id + '/raw'" target="_blank" rel="noopener noreferrer">本次归档</a><button v-for="(item,index) in evidence.filter(e => e.raw_ref === doc.raw_ref)" :key="item.evidence_id" class="btn" @click="citation = item.evidence_id">引用 {{ index + 1 }} · 第 {{ item.locator.page }} 页</button></div>
      </article>
      <p v-if="!documents.length" class="condition-note">未取得满足时点与身份要求的披露原文，请查看下方缺口。</p>
      <h3 v-if="evidence.some(e => e.kind !== 'disclosure')">行情与融资观察</h3>
      <div v-for="item in evidence.filter(e => e.kind !== 'disclosure')" :key="item.evidence_id" class="evidence-observation"><span>{{ item.text }}</span><button class="btn" @click="citation = item.evidence_id">查看依据</button></div>
      <p class="condition-note">行情为未复权收盘观察；融资是独立渠道，不合计为“主力资金”。</p>
      <h3>尚缺哪些资料</h3><ul class="bt-limitations"><li v-for="(gap, i) in report.gaps" :key="i"><strong>{{ gap.need }}</strong>：{{ gap.reason }}</li><li v-if="!report.gaps.length">本次五类输入已取得；有限检索仍不代表完整资料或独立验证。</li></ul>
      <div class="evidence-links"><a class="btn" :href="'/api/quant/decision/runs/' + state.run_id + '/export'">导出本次取证报告</a><a class="btn" href="#/decision">新建取证</a></div>
    </template>
    <template v-else><p>{{ state.error || '正在核对证券身份、检索原文并保存资料。完成前不展示分析结论。' }}</p><ol class="bt-limitations"><li v-for="node in state.process" :key="node.key">{{ node.detail }} · {{ node.status }}</li></ol></template>
    <div class="evidence-links"><button v-if="active(state)" class="btn" :disabled="submitting" @click="action('cancel')">停止取证</button><button v-if="['FAILED','INTERRUPTED'].includes(state.status)" class="btn" :disabled="submitting" @click="action('resume')">继续未完成步骤</button></div>
    <details v-if="developer" class="decision-process"><summary>开发记录：取证过程与方法适用性</summary>
      <p class="condition-note">{{ state.run_id }}<br>本阶段模型调用 {{ state.model_calls }} 次；没有用程序文案冒充 Agent 分析。{{ state.elapsed_seconds != null ? '累计耗时 ' + state.elapsed_seconds.toFixed(1) + ' 秒。' : '' }}</p>
      <ol class="bt-limitations"><li v-for="node in state.process" :key="node.key">{{ node.detail }} · {{ node.status }}</li></ol>
      <p v-for="(call,i) in state.calls" :key="i" class="condition-note">{{ call.tool }} · {{ call.status }} · {{ stamp(call.finished_at || call.started_at) }}{{ call.error ? ' · ' + call.error : '' }}</p>
      <template v-if="report"><p>{{ report.method_context.note }}</p><p v-for="method in report.method_context.methods" :key="method.method_id" class="condition-note">{{ method.method_id }} · {{ method.applicability }}：{{ method.reason }}</p><p v-for="row in report.catalog_coverage" :key="row.category" class="condition-note">{{ row.title }}：源报告 {{ row.total_reported }} 条，本次检查 {{ row.inspected }} 条，选取 {{ row.selected }} 条。{{ row.reason }}</p></template>
    </details>
  </section>
  <section class="panel rep-section"><h2>取证历史</h2><p v-if="!history.length" class="condition-note">{{ loading ? '正在读取…' : '还没有取证记录。' }}</p><a v-for="run in history" :key="run.run_id" class="decision-history-row" :href="'#/decision/' + run.run_id"><span>{{ run.title }}</span><span>{{ statuses[run.status] || run.status }} · {{ stamp(run.created_at) }}</span></a></section>
  <DecisionCitation v-if="citation && state" :key="citation" :run-id="state.run_id" :evidence-id="citation" :developer="developer" @close="citation = null" />
</template>
<style scoped>
.decision-evidence-form{display:flex;flex-wrap:wrap;align-items:end;gap:16px;margin:24px 0}.decision-evidence-form label{display:flex;flex-direction:column;gap:8px;min-width:180px;font-size:13px}.evidence-question{flex:1;min-width:260px!important}.decision-evidence-form input,.decision-evidence-form textarea,.decision-evidence-form select{font:inherit;color:inherit;background:var(--bg,#8881);border:1px solid var(--border,#8883);border-radius:4px;padding:10px;max-width:100%;box-sizing:border-box}.evidence-materials,.evidence-links{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.evidence-materials span{border-left:2px solid var(--gold,#b89b5e);padding-left:10px}.decision-document{padding:20px 0;border-bottom:1px solid var(--border,#8883)}.decision-evidence-result h3{margin-top:24px}.decision-history-row,.evidence-observation{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 0;border-bottom:1px solid var(--border,#8883);line-height:1.8}.decision-history-row span:last-child{font-size:12px;opacity:.65}.decision-process{margin-top:20px;overflow-wrap:anywhere}.decision-process summary{cursor:pointer;line-height:1.8}.decision-evidence-result .condition-note{line-height:1.8}@media(max-width:600px){.decision-evidence-form{display:block}.decision-evidence-form label{margin-bottom:16px;min-width:0!important}.decision-history-row,.evidence-observation{align-items:start;flex-direction:column}.decision-document strong{overflow-wrap:anywhere}}
</style>
