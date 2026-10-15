<script setup>
defineProps({report:Object,subject:Object})
const emit=defineEmits(['evidence'])
const outlook={FAVORABLE:'偏有利',UNFAVORABLE:'偏不利',MIXED:'多空交织',UNDETERMINED:'旧版未给方向'}
const stance={PRIORITIZE_RESEARCH:'值得优先研究',WAIT_FOR_CONFIRMATION:'等待验证',RISK_FIRST:'先核查风险',INSUFFICIENT_EVIDENCE:'需要更多依据'}
const kind={BASE:'基准情景',IMPROVEMENT:'改善情景',DETERIORATION:'恶化情景'}
const clean=value=>value.replace(/[。；]+$/,'')
</script>
<template>
  <article class="panel rep-section medium-opinion">
    <div class="section-heading"><h2>{{ subject.name }}</h2><span class="tag tag-cyan">未来 {{ report.opinion.horizon_months }} 个月</span></div>
    <div class="medium-verdict"><strong>{{ report.conclusion ? (report.opinion.outlook==='FAVORABLE'?'倾向看多':'倾向看空') : outlook[report.opinion.outlook] }}</strong><span class="tag">{{ report.conclusion ? (report.conclusion.support_level==='WEAK'?'判断依据有限':'判断依据一般') : stance[report.opinion.stance] }}</span><span v-if="report.conclusion" class="tag">未来方向的研究倾向</span></div>
    <p class="medium-summary">{{ report.summary }}</p>
    <section v-if="report.conclusion" class="medium-decision"><h3>为什么作这个判断</h3><p>{{ report.conclusion.reason }}</p><p class="condition-note">{{ report.conclusion.limitations }}</p></section>
    <div class="medium-columns">
      <section><h3>经营变化，依据是什么</h3><div v-for="claim in report.claims" :key="claim.claim_id" class="medium-reason"><p>{{ claim.text }}</p><p v-if="claim.mechanism" class="condition-note">{{ claim.mechanism }}</p><button v-for="(eid,n) in claim.support_ids" :key="eid" class="prediction-source" @click="emit('evidence',eid)">查看依据 {{ n+1 }} ↗</button></div><p v-if="!report.claims.length">目前的资料还不足以形成可靠经营判断。</p></section>
      <section class="medium-counter"><h3>还要警惕什么</h3><ul><li v-for="text in report.opinion.opposing_reasons" :key="text">{{ text }}</li></ul><template v-for="claim in report.claims" :key="claim.claim_id"><button v-for="(eid,n) in claim.counter_ids" :key="eid" class="prediction-source" @click="emit('evidence',eid)">查看反对依据 {{ n+1 }} ↗</button></template></section>
    </div>
    <div class="medium-position"><section><h3>价格位置</h3><p>{{ report.price_position }}</p></section><section><h3>估值与预期差</h3><p>{{ report.valuation_position }}</p><p class="condition-note">{{ report.expectations.comparison }}</p></section></div>
  </article>
  <section class="panel rep-section"><h2>接下来，什么会改变判断</h2><div class="medium-columns"><section><h3>继续观察</h3><ul><li v-for="text in report.opinion.next_checks" :key="text">{{ text }}</li></ul><p v-if="report.opinion.catalysts.length" class="condition-note">关注：{{ report.opinion.catalysts.map(clean).join('；') }}。具体披露日尚未确认。</p></section><section><h3>重新评估的条件</h3><ul><li v-for="text in report.opinion.invalidation_conditions" :key="text">{{ text }}</li></ul></section></div></section>
  <section v-if="report.scenarios.length" class="panel rep-section"><h2>三种可能的走向</h2><div class="medium-scenarios"><section v-for="scenario in report.scenarios" :key="scenario.scenario_id"><h3>{{ kind[scenario.kind] }}</h3><p>{{ scenario.text }}</p><ul><li v-for="condition in scenario.conditions" :key="condition">{{ condition }}</li></ul></section></div><p class="condition-note">这些是有条件的情景，尚无校准概率；经营变化与股价方向仍可能分歧。</p></section>
  <details class="panel rep-section"><summary>这份研究的范围与局限</summary><ul><li v-for="text in report.limitations" :key="text">{{ text }}</li></ul></details>
</template>
<style>
.medium-verdict{display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin:24px 0 16px}.medium-verdict strong{font-size:32px;color:var(--accent)}.medium-summary{font-size:18px;line-height:1.9;max-width:52em}.medium-columns,.medium-position{display:grid;grid-template-columns:1.2fr 1fr;gap:32px;margin-top:28px}.medium-reason{margin-bottom:24px}.medium-counter{border-left:2px solid var(--accent);padding-left:24px}.medium-position{border-top:1px solid var(--border);padding-top:20px}.medium-position p{line-height:1.8}.medium-decision{border-top:1px solid var(--border);margin-top:24px;padding-top:16px}.medium-decision p{line-height:1.9;max-width:60em}.medium-scenarios{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px;margin-top:24px}.medium-scenarios p,.medium-columns li{line-height:1.9}.medium-columns ul,.medium-scenarios ul{padding-left:20px}.medium-columns li{margin-bottom:12px}@media(max-width:700px){.medium-columns,.medium-position,.medium-scenarios{grid-template-columns:1fr;gap:18px}.medium-counter{padding-left:16px}.medium-verdict strong{font-size:28px}}
</style>
