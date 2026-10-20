<script setup>
import {ref,onMounted,onUnmounted} from 'vue'
import request from '../api/request.js'
const props=defineProps({id:String,page:{type:String,default:'decision'},parentId:{type:String,default:undefined}})
const comparison=ref(null),error=ref('')
let disposed=false
onMounted(async()=>{if(props.parentId===null)return;try{const value=await request('/quant/decision/runs/'+props.id+'/comparison',{timeoutMs:60000});if(!disposed)comparison.value=value}catch(e){if(!disposed)error.value=e.message}})
onUnmounted(()=>{disposed=true})
function text(value){
  if(value==null)return '此版本未提供'
  if(typeof value==='string')return value
  if(Array.isArray(value))return value.map(text).join('\n\n')
  const direction={FAVORABLE:'倾向看多',UNFAVORABLE:'倾向看空',MIXED:'旧版多空交织',UNDETERMINED:'旧版未给方向',UP:'倾向看多',DOWN:'倾向看空'}
  return [value.summary,value.reason,value.direction_reason,value.rationale,direction[value.outlook||value.direction],...(value.supporting_reasons||[]),...(value.opposing_reasons||[]),...(value.invalidation_conditions||[])].filter(Boolean).join('\n') || '请打开原版本查看完整意见'
}
const fields={summary:'摘要',conclusion:'方向与取舍',opinion:'研究意见与条件',candidates:'候选与理由'}
</script>
<template>
  <section v-if="comparison?.parent||error" class="panel rep-section version-comparison">
    <h2>这次更新，观点有什么变化</h2><p v-if="error" role="alert">{{ error }}</p>
    <template v-if="comparison?.parent"><p>{{ comparison.explanation }}</p><p class="condition-note">上次资料截止 {{ comparison.before_cutoff }}<br>本次资料截止 {{ comparison.after_cutoff }}</p><a class="btn" :href="'#/'+page+'/'+comparison.parent">查看保留的上一版</a><details v-if="comparison.changes.length"><summary>逐项对照新旧结论</summary><article v-for="change in comparison.changes" :key="change.field"><h3>{{ fields[change.field] }}</h3><div class="version-columns"><section><b>上一版</b><pre>{{ text(change.before) }}</pre></section><section><b>本次</b><pre>{{ text(change.after) }}</pre></section></div></article></details></template>
  </section>
</template>
<style scoped>.version-comparison{margin-bottom:20px}.version-comparison p{line-height:1.9}.version-comparison details{margin:20px 0}.version-columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}.version-columns pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;max-height:50vh;overflow:auto}@media(max-width:700px){.version-columns{grid-template-columns:1fr}}</style>
