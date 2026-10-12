<script setup>
import { computed } from 'vue'
import { interpretiveCards, formattedClaim } from './conclusions.js'
import { number } from './model.js'

const props = defineProps({ data: { type: Object, required: true }, reading: Object, readingError: String })
const emit = defineEmits(['evidence', 'source', 'derived'])
const cards = computed(() => interpretiveCards(props.data,props.reading))
function proof(part) {
  if (part.proof) emit('derived', part.proof)
  else if (part.evidence) emit('evidence', part.evidence)
  else if (part.source) emit('source', part.source)
}
</script>

<template>
  <section id="live-summary" class="panel rep-section conclusion-overview" aria-label="结论速览">
    <h2>结论速览</h2>
    <div v-if="reading" class="reader-intro"><p class="reader-summary">{{ reading.summary }}</p><p class="small muted">判断日期：财报 {{ reading.data_dates.financial || '未取得' }} · 日线 {{ reading.data_dates.price || '未取得' }} · 估值 {{ reading.data_dates.valuation || '未取得' }}</p></div>
    <p v-if="readingError" class="condition-note" role="status">{{ readingError }}，以下保留原始指标，可刷新重试。</p>
    <ol class="conclusion-list">
      <li v-for="(card,index) in cards" :key="card.key" class="conclusion-item">
        <span class="conclusion-number num" aria-hidden="true">{{ String(index + 1).padStart(2, '0') }}</span>
        <div class="conclusion-content">
          <div class="conclusion-tags"><span class="tag">{{ card.title }}</span><span class="tag" :class="card.tone === 'cyan' ? 'tag-cyan' : 'tag-gold'">{{ card.badge }}</span></div>
          <p class="conclusion-text"><template v-for="(part,i) in card.parts" :key="i"><button v-if="part.evidence || part.source || part.proof" class="ev-num num conclusion-value" :aria-label="'查看' + part.label + '的依据'" @click="proof(part)">{{ part.text }}</button><span v-else>{{ part.text }}</span></template></p>
          <p v-if="card.meaning" class="reader-meaning">{{ card.meaning }}</p>
          <p v-if="card.watch" class="reader-watch"><span>后续看</span>{{ card.watch }}</p>
        </div>
      </li>
    </ol>
    <p class="conclusion-note">{{ reading ? reading.note : '重点数值可点击溯源。标签表示数据口径，尚未评定结论置信度。' }}</p>
    <details v-if="reading" class="reader-details"><summary>判断依据、未来观察条件与资料缺口</summary>
      <p class="condition-note">行业：{{ reading.company.industry || '未取得' }}。{{ reading.company.note }}</p>
      <p v-if="reading.company.description" class="condition-note">公司做什么（供应商简介）：{{ reading.company.description }} <button class="text-button" @click="emit('source','profile')">查看公司资料来源</button></p>
      <p v-if="reading.growth.quarter_comparison" class="condition-note">单季对照：{{ reading.growth.quarter_comparison.period }} 营收同比 {{ number(reading.growth.quarter_comparison.current) }}%，{{ reading.growth.quarter_comparison.direction }}上年同季（{{ number(reading.growth.quarter_comparison.previous) }}%）。这是单季增速对照，与上方累计同比分开看。</p>
      <ul class="reader-watch-list"><li v-for="w in reading.watch" :key="w.horizon"><strong>{{ w.horizon }}</strong>：{{ w.condition }}，{{ w.implication }}</li></ul>
      <p class="condition-note">尚缺的判断依据：{{ reading.gaps.join('；') }}。</p>
      <p class="condition-note">经营与价格解读采用公开规则整理；完整模型解读保留在下方。数值可点击查看样本、公式与来源。</p>
    </details>
    <details class="conclusion-details">
      <summary>完整模型解读与指标<span v-if="data.agent_report"> · {{ data.agent_report.claims.length }} 条结论</span></summary>
      <template v-if="data.agent_report">
        <h3>{{ data.agent_report.heading }}</h3>
        <div v-for="(claim,i) in data.agent_report.claims" :key="i" class="conclusion-analysis">
          <p>{{ formattedClaim(claim,data) }}</p><button v-for="id in claim.evidence_ids" :key="id" class="chip" @click="emit('evidence',id)">证据 {{ id }}</button>
        </div>
        <p class="condition-note">{{ data.agent_report.note }}</p>
      </template>
      <p v-else class="condition-note">此版本保留真实输入和计算结果，尚无模型解读。</p>
      <div class="conclusion-metrics"><div v-for="metric in data.metrics" :key="metric.name"><span class="muted small">{{ metric.name }}</span><button class="num ev-num" @click="emit('source',metric.source)">{{ number(metric.value) }}<small> {{ metric.unit }}</small></button></div></div>
    </details>
  </section>
</template>

<style scoped>
.conclusion-list { list-style:none; padding:0; margin:0 }
.conclusion-item { gap:12px; padding:12px 14px; margin:0 0 10px; align-items:flex-start }
.conclusion-item:last-child { margin-bottom:0 }
.conclusion-number { flex:0 0 24px; padding-top:3px; color:var(--accent); font-size:12px; font-weight:700 }
.conclusion-content { min-width:0; flex:1 }
.conclusion-tags { display:flex; align-items:center; flex-wrap:wrap; gap:6px; margin-bottom:7px }
.conclusion-text { margin:0; color:var(--text-1); font-size:13.5px; line-height:1.75; overflow-wrap:anywhere }
.conclusion-value { display:inline; padding:0; margin:0; border:0; border-bottom:1px dashed currentColor; border-radius:0; background:none; font:inherit; font-family:var(--font-num); font-weight:600; vertical-align:baseline }
.conclusion-value:focus-visible { outline:2px solid var(--accent-cyan); outline-offset:3px }
.reader-intro { margin-bottom:14px }.reader-summary { font-size:15px; line-height:1.7; font-weight:600; margin:0 0 6px; color:var(--text-1) }
.reader-intro > p:last-child { margin:0; line-height:1.6 }
.reader-meaning { font-size:12px; line-height:1.65; margin:5px 0 0; color:var(--text-2) }
.reader-watch { font-size:12px; line-height:1.65; margin:5px 0 0; color:var(--text-2) }.reader-watch > span { color:var(--accent); margin-right:8px }
.reader-details { border-top:1px solid var(--border); padding-top:12px; margin-top:12px }.reader-details > summary { font-size:12px; color:var(--text-2) }
.reader-watch-list { padding-left:20px; font-size:13px; line-height:1.8; color:var(--text-2) }.reader-watch-list li { margin:8px 0 }
.conclusion-note { font-size:11.5px; color:var(--text-3); margin:10px 0 0; line-height:1.6 }
.conclusion-details { border-top:1px solid var(--border); padding-top:12px; margin-top:12px }
.conclusion-details > summary { cursor:pointer; color:var(--text-2); font-size:12px }
.conclusion-details h3 { font-size:14px; margin:16px 0 10px }
.conclusion-analysis { margin:14px 0 }.conclusion-analysis p { line-height:1.8; overflow-wrap:anywhere; margin-bottom:8px }
.conclusion-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; padding:16px 0 }
.conclusion-metrics button { display:block; font-size:18px; margin-top:6px }.conclusion-metrics small { font-size:12px }
@media(max-width:760px) { .conclusion-item { padding:12px 10px; gap:8px }.conclusion-number { flex-basis:21px }.conclusion-metrics { grid-template-columns:repeat(2,minmax(0,1fr)) } }
</style>
