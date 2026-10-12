<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import request from '../api/request.js'
import { useQuantRun } from './useQuantRun.js'
import { dateTime, scalarMetrics } from './model.js'
import { href } from './navigation.js'
import { partitionReportSections } from '../utils/reportPresentation.js'
import ReportBody from './ReportBody.vue'
import MetricGrid from './MetricGrid.vue'
import CellEvidence from './CellEvidence.vue'
import ComparisonReport from './ComparisonReport.vue'
import CitationDialog from './CitationDialog.vue'
import PrototypeResearchSections from './PrototypeResearchSections.vue'
const props = defineProps({ id: String })
const { data, report, error, loading, load } = useQuantRun(props.id)
const citations = ref([]), citationError = ref(''), citation = ref(''), cell = ref(null)
const sections = computed(() => partitionReportSections(report.value?.sections).body)
const conclusionTypes = new Set(['EXECUTIVE_STATUS', 'EXECUTIVE_SUMMARY'])
const conclusions = computed(() => ({ ...report.value, sections: sections.value.filter(s => conclusionTypes.has(s.section_type)) }))
const analysis = computed(() => ({ ...report.value, sections: (report.value?.sections || []).filter(s => !conclusionTypes.has(s.section_type)) }))
const conclusionSections = computed(() => sections.value.filter(s => conclusionTypes.has(s.section_type)))
const analysisSections = computed(() => sections.value.filter(s => !conclusionTypes.has(s.section_type)))
const titles = { EXECUTIVE_STATUS: '结论速览', EXECUTIVE_SUMMARY: '结论速览', SEARCH_AND_SOURCE_SUMMARY: '证据基础', SOURCE_COVERAGE: '证据基础', QUANTITATIVE_FINDINGS: '量化发现', AVAILABLE_FINDINGS: '核心发现', BLOCKING_GAPS_AND_LIMITATIONS: '风险与局限', LIMITATIONS_AND_RESEARCH_GAPS: '风险与局限' }
let version = 0
async function loadCitations(id) {
  const ticket = ++version
  citations.value = []; citationError.value = ''
  if (!id) return
  try { const result = await request('/reports/' + id + '/citations'); if (ticket === version) citations.value = result }
  catch (e) { if (ticket === version) citationError.value = e.message }
}
watch(() => report.value?.report_id, loadCitations)
onMounted(load)
onUnmounted(() => { version++ })
function scrollTo(id) { document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }) }
function selectCell(path) { cell.value = data.value.values.find(c => c.metric_path === path) }
function changeVersion(event) { location.hash = href('report', event.target.value) }
</script>
<template>
  <div class="page">
    <div v-if="error" class="notice error-text" role="alert">{{ error }} <button class="btn" @click="load">重试读取</button></div>
    <div v-if="!report" class="panel empty-state" role="status">{{ loading ? '正在读取报告…' : '报告尚未成稿，可查看研究过程。' }} <a :href="href('research', id)">研究过程</a></div>
    <p v-if="data?.environment && !data.environment.matches_current" class="notice">{{ data.environment.note }} 当前代码或依赖与生成时不同，本页展示原始保存结果。</p>
    <ComparisonReport v-else-if="data.scenario === 'compare'" :data="data" :report="report" :id="id" />
    <div v-else class="report-grid">
      <aside class="report-rail">
        <div class="panel rail-box"><div class="section-title">报告信息</div><div class="kv"><span class="k">标的</span><a v-if="data.subject" :href="href('stock', data.subject.code)">{{ data.subject.name }}</a><span v-else>—</span></div><div class="kv"><span class="k">场景</span><span>个股深研</span></div><div class="kv"><span class="k">数据截至</span><span class="num">{{ data.plan?.date_end || '—' }}</span></div><div class="kv"><span class="k">生成时间</span><span class="num">{{ dateTime(report.created_at) }}</span></div><div class="kv"><span class="k">版本</span><span class="num">v{{ report.version }}</span></div><div class="kv"><span class="k">证据数</span><span class="num">{{ data.evidence_count }}</span></div>
          <select v-if="data.versions?.length > 1" class="version-select" aria-label="报告版本" :value="id" @change="changeVersion"><option v-for="version in data.versions" :key="version.run_id" :value="version.run_id">v{{ version.version }}{{ version.run_id === id ? ' · 当前查看' : ' · 历史版本' }}</option></select>
        </div>
        <nav class="panel rail-box rail-toc" aria-label="报告目录"><div class="section-title">目录</div><a v-for="section in conclusionSections" :key="section.section_id" :href="'#sec-' + section.section_id" @click.prevent="scrollTo('sec-' + section.section_id)">{{ titles[section.section_type] }}</a><a v-for="section in [{id:'fundamental',title:'基本面分析'},{id:'valuation',title:'估值分析'},{id:'market',title:'技术面与资金面'},{id:'prototype-risks',title:'A股特色风险扫描'},{id:'evidence',title:'数据与来源总览'}]" :key="section.id" :href="'#sec-' + section.id" @click.prevent="scrollTo('sec-' + section.id)">{{ section.title }}</a><a href="#alpha-metrics" @click.prevent="scrollTo('alpha-metrics')">核心指标</a><a href="#sec-fundamental" @click.prevent="scrollTo('sec-fundamental')">价格、估值与财务</a><a v-for="section in analysisSections" :key="section.section_id" :href="'#sec-' + section.section_id" @click.prevent="scrollTo('sec-' + section.section_id)">{{ titles[section.section_type] || section.title }}</a></nav>
        <div class="panel rail-box muted" style="font-size:11.5px;line-height:1.7">带虚线下划线的数字可点击查看来源、时间戳与计算口径。<div class="rail-actions"><a :href="href('research', id)">查看研究过程</a><a :href="'/api/reports/' + report.report_id + '/export?format=markdown'">导出 Markdown</a><a v-if="data.parent_run_id" :href="href('report', data.parent_run_id)">查看父版本</a></div></div>
      </aside>
      <article class="report-main">
        <h1 class="rep-h1">{{ data.plan?.question || '单股历史数据研究报告' }}</h1>
        <ReportBody :report="conclusions" :citations="citations" @citation="citation = $event" />
        <PrototypeResearchSections :data="data" @cell="selectCell" />
        <section id="alpha-metrics" class="panel rep-section"><h2>核心指标</h2><MetricGrid :cells="scalarMetrics(data).filter(c => ['return','drawdown','pe','pb','roe','volatility','sharpe'].includes(c.name))" @cell="cell = $event" /></section>
        <p v-if="citationError" class="error-text" role="alert">引用读取失败：{{ citationError }} <button class="btn" @click="loadCitations(report.report_id)">重试引用</button></p>
        <ReportBody :report="analysis" :citations="citations" @citation="citation = $event" />
        <div class="disclaimer">本工具为投研参考，不构成投资建议，不提供买卖指令。历史数据不代表实时行情或未来表现。</div>
      </article>
    </div>
    <CellEvidence v-if="cell" :cell="cell" :data="data" @close="cell = null" /><CitationDialog v-if="citation" :key="citation" :id="citation" @close="citation = ''" />
  </div>
</template>
