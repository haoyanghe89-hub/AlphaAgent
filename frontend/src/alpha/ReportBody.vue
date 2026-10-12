<script setup>
import { computed } from 'vue'
import { partitionReportSections } from '../utils/reportPresentation.js'
const props = defineProps({ report: Object, citations: Array })
defineEmits(['citation'])
const sections = computed(() => partitionReportSections(props.report?.sections))
const titles = { EXECUTIVE_STATUS: '结论速览', EXECUTIVE_SUMMARY: '结论速览', SEARCH_AND_SOURCE_SUMMARY: '证据基础', SOURCE_COVERAGE: '证据基础', QUANTITATIVE_FINDINGS: '量化发现', AVAILABLE_FINDINGS: '核心发现', BLOCKING_GAPS_AND_LIMITATIONS: '风险与局限', LIMITATIONS_AND_RESEARCH_GAPS: '风险与局限', CORE_FINDINGS: '核心发现与分析', EVIDENCE_BASE: '证据基础' }
const title = s => titles[s.section_type] || s.title || s.section_type
const refs = (section, unit) => (props.citations || []).filter(c => c.section_key === section && c.unit_key === unit)
defineExpose({ sections, title })
</script>
<template>
  <section v-for="section in sections.body" :id="'sec-' + section.section_id" :key="section.section_id" class="panel rep-section" data-report-section>
    <h2>{{ title(section) }}</h2>
    <template v-if="['EXECUTIVE_STATUS','EXECUTIVE_SUMMARY'].includes(section.section_type)"><div v-for="(unit,index) in section.content?.units || []" :key="unit.unit_key" class="conclusion-item"><span class="conclusion-number">{{ String(index+1).padStart(2,'0') }}</span><div class="conclusion-body"><div class="conclusion-tags"><span class="tag">置信度 —</span><span class="tag tag-warn">{{ report?.report_type === 'STATUS_ONLY' ? '资料不足' : '部分报告' }}</span></div><p class="rep-block-text">{{ unit.text }}<button v-for="citation in refs(section.section_type, unit.unit_key)" :key="citation.citation_id" class="citation-link ev-num" @click="$emit('citation', citation.citation_id)">[{{ citation.display_ordinal+1 }}]</button></p></div></div></template>
    <ul v-else-if="/LIMITATIONS|GAPS/.test(section.section_type)" style="margin:0;padding-left:18px;color:var(--text-2);font-size:13px;line-height:1.8"><li v-for="unit in section.content?.units || []" :key="unit.unit_key">{{ unit.text }}<button v-for="citation in refs(section.section_type, unit.unit_key)" :key="citation.citation_id" class="citation-link ev-num" @click="$emit('citation', citation.citation_id)">[{{ citation.display_ordinal+1 }}]</button></li></ul>
    <div v-else style="display:flex;flex-direction:column;gap:16px"><p v-for="unit in section.content?.units || []" :key="unit.unit_key" class="rep-block-text">{{ unit.text }}<button v-for="citation in refs(section.section_type, unit.unit_key)" :key="citation.citation_id" class="citation-link ev-num" :aria-label="'证据引用 ' + (citation.display_ordinal + 1)" @click="$emit('citation', citation.citation_id)">[{{ (citation.display_ordinal + 1) }}]</button></p></div>
  </section>
  <details v-if="sections.appendices.length" class="panel rep-section report-appendices"><summary>方法与诊断附录</summary><section v-for="section in sections.appendices" :key="section.section_id"><h3>{{ title(section) }}</h3><p v-for="unit in section.content?.units || []" :key="unit.unit_key" class="rep-block-text">{{ unit.text }}<button v-for="citation in refs(section.section_type, unit.unit_key)" :key="citation.citation_id" class="citation-link ev-num" @click="$emit('citation', citation.citation_id)">[{{ (citation.display_ordinal + 1) }}]</button></p></section></details>
</template>
