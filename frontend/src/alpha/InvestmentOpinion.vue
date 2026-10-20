<script setup>
const props=defineProps({opinion:Object,evidence:Array,fragments:Array,runId:String,rationale:String})
const facts=claim=>(claim.evidence_ids||[]).map(id=>props.evidence.find(e=>e.evidence_id===id)).filter(Boolean)
const methods=()=>props.fragments.filter(f=>props.opinion.method_rule_ids.includes(f.rule_id))
const value=e=>e.unit==='text'?e.text:e.unit==='ratio'?(Number(e.value)*100).toFixed(2)+'%':Number(e.value).toLocaleString('zh-CN',{maximumFractionDigits:2})+(['CNY/share','CNY'].includes(e.unit)?' 元':e.unit==='CNY/session'?' 元/日':['倍','万元'].includes(e.unit)?' '+e.unit:'')
const display=e=>e.metric_key==='cash_conversion'||e.metric_key==='turnover_ratio_5_20'?Number(e.value).toFixed(2)+' 倍':value(e)
</script>
<template>
<div class="investment-opinion">
  <p class="direction" :class="opinion.direction==='FAVORABLE'?'bull':'bear'">{{ opinion.summary }}</p>
  <p class="route-fit">{{ rationale }}</p>
  <div class="reason-list"><div v-for="claim in opinion.claims" :key="claim.claim_id" class="reason"><p>{{ claim.text }}</p><details><summary>数据依据</summary><div v-for="e in facts(claim)" :key="e.evidence_id" class="fact"><span>{{ e.label }}</span><strong>{{ display(e) }}</strong><small>{{ typeof e.window==='string'?e.window:e.window?.start+'—'+e.window?.end }}</small><details><summary>公式与来源</summary><p>{{ e.formula }}</p><a v-for="ref in e.source_refs.slice(0,3)" :key="ref" :href="'/api/quant/investment-research/runs/'+runId+'/sources/'+ref.split('/').pop()">下载封存来源</a></details></div></details></div></div>
  <div class="counter"><strong>反向证据与风险</strong><p v-for="claim in opinion.opposing" :key="claim.claim_id">{{ claim.text }}</p></div>
  <p class="invalid"><b>改变判断的条件：</b>{{ opinion.invalidation.join('；') }}</p>
  <details class="method-source"><summary>方法依据与后续观察</summary><p v-for="check in opinion.next_checks" :key="check">{{ check }}</p><div v-for="m in methods()" :key="m.rule_id"><p>{{ m.adaptation }}</p><a :href="m.url" target="_blank" rel="noopener noreferrer">{{ m.authors.join('、') }}：{{ m.source_title }}</a><blockquote>{{ m.quote }}</blockquote><small>公开原文节选，参数为系统研究改写，效果尚未验证。</small></div></details>
</div>
</template>
<style scoped>
.direction{font-size:17px;font-weight:650;line-height:1.65;margin:12px 0}.bull{color:var(--up)}.bear{color:var(--down)}.route-fit,.invalid{font-size:13px;color:var(--text-2);line-height:1.7}.reason-list{display:grid;gap:8px;margin:12px 0}.reason p{margin:0 0 4px;font-size:14px;line-height:1.65}.reason summary,.method-source summary{font-size:12px;color:var(--text-2);cursor:pointer}.counter{border-left:2px solid var(--accent);padding-left:10px;margin:12px 0;font-size:13px}.counter p{margin:6px 0;line-height:1.6}.fact{display:flex;gap:8px;flex-wrap:wrap;padding:8px 0;font-size:12px;border-bottom:1px solid var(--border)}.fact details{flex-basis:100%}.fact a{margin-right:12px}.method-source{margin:10px 0;font-size:12px;line-height:1.7}.method-source div{padding:8px 0}blockquote{margin:8px 0;padding-left:10px;border-left:2px solid var(--border);color:var(--text-2);overflow-wrap:anywhere}small{color:var(--text-3)}summary:focus-visible{outline:2px solid var(--accent)}
</style>
