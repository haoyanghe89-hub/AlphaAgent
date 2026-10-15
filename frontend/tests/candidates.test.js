import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'
import {renderToString} from '@vue/server-renderer'
import {candidateChartBlocks} from '../src/utils/decisionChartOptions.js'
import {parseRoute} from '../src/alpha/navigation.js'

const {descriptor}=parse(readFileSync(new URL('../src/alpha/IndustryCandidates.vue',import.meta.url),'utf8'))
const compiled=compileScript(descriptor,{id:'candidate-test',inlineTemplate:true,genDefaultAs:'Component'}).content
const ResearchDataCharts={props:['blocks'],render(){return Vue.h('div',{'data-chart-count':this.blocks.length})}}
const code=compiled.replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"]/g,(_,names)=>'const {'+names.replace(/\s+as\s+/g,':')+'}=Vue').replace(/^import .*$/gm,'')
function data(){
  const opinion={security_id:'688012.SH',stance:'WAIT_FOR_CONFIRMATION',summary:'倾向看多，等待现金回收验证。',reason:'经营与风险分别核查。',supporting_reasons:['公司有业务原文。'],opposing_reasons:['风险覆盖未完整核定。'],funding:'融资渠道资料不足。',valuation:'不因市盈率快照判断便宜。',comparison:'不同设备业务仅做合并报表对照。',verification_conditions:['现金回收改善。'],invalidation_conditions:['风险扩大。'],evidence_ids:['EV-OWN'],counter_ids:[],peer_ids:[]}
  return {report:{stage:'P5',summary:'当前有限候选。',cutoff_at:'2026-10-13T10:00:00Z',candidates:[opinion]},coverage:{universe_total:2,available:1,excluded:0,missing:1},members:[{security_id:'688012.SH',name:'合成甲',period:'2026-06-30',leader_type:'LARGE_REPRESENTATIVE',group:'资料不足',gaps:[],metrics:{TOTAL_OPERATE_INCOME:'100000000',PARENT_NETPROFIT:'20000000',NETCASH_OPERATE:'5000000',median_20_turnover:'100000000'}},{security_id:'688082.SH',name:'合成乙',gaps:['未深研，不表示经营差'],metrics:{}}],selected:['688012.SH'],limitations:['供应商转录未独立核验。'],screening_result:{},universe:{}}
}
async function render(value,developer=false){
  const controller={state:Vue.ref({status:'COMPLETED',data:value}),history:Vue.ref([]),error:Vue.ref(''),submitting:Vue.ref(false),loading:Vue.ref(false),active:()=>false}
  const Component=Function('Vue','useDecisionResearch','DecisionCitation','VersionComparison','ResearchDataCharts','candidateChartBlocks','location',code+';return Component')(Vue,()=>controller,{},{},ResearchDataCharts,candidateChartBlocks, {search:developer?'?developer=1':'',href:'http://127.0.0.1:8839/#/candidates/RUN-DEC-synthetic'})
  return renderToString(Vue.createSSRApp(Component,{id:'RUN-DEC-synthetic'}))
}
test('candidate routes preserve existing comparison and decision routes',()=>{
  assert.deepEqual(parseRoute('#/candidates/RUN-DEC-x'),{page:'candidates',id:'RUN-DEC-x'})
  assert.deepEqual(parseRoute('#/candidates'),{page:'candidates'})
  assert.deepEqual(parseRoute('#/compare'),{page:'compare'})
  assert.deepEqual(parseRoute('#/decision/RUN-DEC-x'),{page:'decision',id:'RUN-DEC-x'})
})
test('ordinary candidate results show evidence, missing coverage and limits without developer records',async()=>{
  const html=await render(data())
  assert.match(html,/倾向看多/)
  assert.match(html,/等待验证/)
  assert.match(html,/经营地位仍待独立证据核定/)
  assert.match(html,/为什么没有选其他公司/)
  assert.match(html,/未深研，不表示经营差/)
  assert.match(html,/动态市盈率快照/)
  assert.doesNotMatch(html,/candidate-development|原始成分、排序、调用与审核/)
})
test('zero candidate result stays empty and developer details remain separate',async()=>{
  const empty=data();empty.report.candidates=[];empty.selected=[];empty.coverage.available=0
  assert.match(await render(empty),/本轮没有符合证据要求的候选/)
  assert.doesNotMatch(await render(empty),/data-candidate-id/)
  assert.match(await render(data(),true),/原始成分、排序、调用与审核/)
})
