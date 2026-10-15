import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'
import {renderToString} from '@vue/server-renderer'
import {marketChartBlocks} from '../src/utils/decisionChartOptions.js'
import {parseRoute} from '../src/alpha/navigation.js'
import {createDecisionResearch} from '../src/alpha/useDecisionResearch.js'
import {deliveryPage} from '../src/alpha/useDelivery.js'

const {descriptor}=parse(readFileSync(new URL('../src/alpha/MarketResearch.vue',import.meta.url),'utf8'))
const compiled=compileScript(descriptor,{id:'market-test',inlineTemplate:true,genDefaultAs:'Component'}).content
const ResearchDataCharts={props:['blocks'],render(){return Vue.h('div',{'data-chart-count':this.blocks.length})}}
const code=compiled.replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"]/g,(_,names)=>'const {'+names.replace(/\s+as\s+/g,':')+'}=Vue').replace(/^import .*$/gm,'')
async function render(developer=false,missing=false,partial=false){
 const data={report:{stage:'P6_MARKET',summary:'合成范围观察，不是实时结果',cutoff_at:'2026-10-13T10:00:00Z'},selection:{ranking:missing?[]:[{}]},heat_inputs:{sessions:['2026-10-13']},coverage:{members_total:2,members_available:missing?1:2,scope_note:'固定两行业范围，不是全市场占比',catalog_note:'同一分类范围',unconnected:[{f14:'未接入合成行业'}]},metrics:[{industry_id:'synthetic',name:'集成电路制造',members_total:2,available:missing?1:2,mean_20_share:missing?null:'0.25',change_5_share:missing?null:'0.01',breadth:'0.5',top3_turnover_share:'1',median_member_return_20:'0.1',missing:missing?['688981']:[]}]}
 if(partial){data.market_index={first_session:'2026-09-07',last_session:'2026-10-13',return_20:'0',close:'10'};data.heat_inputs=null;data.metrics=[];data.coverage.members_total=null;data.report.summary='合成市场价格观察，行业目录缺失。'}
 const controller={state:Vue.ref({status:'COMPLETED',data}),history:Vue.ref([]),error:Vue.ref(''),submitting:Vue.ref(false),loading:Vue.ref(false),active:()=>false}
 const Component=Function('Vue','useDecisionResearch','VersionComparison','ResearchDataCharts','marketChartBlocks','request','location',code+';return Component')(Vue,()=>controller,{},ResearchDataCharts,marketChartBlocks,async()=>[],{search:developer?'?developer=1':'',href:'http://127.0.0.1:8839/#/market/RUN-DEC-synthetic'})
 return renderToString(Vue.createSSRApp(Component,{id:'RUN-DEC-synthetic'}))
}
test('decision market route and history preserve old paths',()=>{
 assert.deepEqual(parseRoute('#/market/RUN-DEC-x'),{page:'market',id:'RUN-DEC-x'})
 assert.equal(deliveryPage({scenario:'decision',page:'market'}),'market')
 assert.equal(deliveryPage({scenario:'decision',page:'candidates'}),'candidates')
 assert.equal(deliveryPage({scenario:'live'}),'live')
})
test('decision market shows scope and industry links; development is separate',async()=>{
 const user=await render()
 assert.match(user,/不是全市场占比/)
 assert.match(user,/范围内二十日平均成交占比/)
 assert.match(user,/进入集成电路制造候选研究/)
 assert.doesNotMatch(user,/开发核验：成分/)
 assert.match(await render(true),/开发核验：成分/)
 assert.match(await render(false,true),/固定分母未缩小/)
})
test('lost refresh response reuses one key and navigates to the new version',async()=>{
 const calls=[],navigation=[]
 const api=async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});if(calls.length===1)throw new Error('lost response');return {run_id:'RUN-DEC-new'}}
 const controller=createDecisionResearch('RUN-DEC-parent',{api,navigate:id=>navigation.push(id)})
 await controller.refresh();await controller.refresh()
 assert.equal(calls[0].body.request_key,calls[1].body.request_key)
 assert.deepEqual(navigation,['RUN-DEC-new'])
 assert.equal(calls[0].path,'/quant/decision/runs/RUN-DEC-parent/refresh')
 controller.dispose()
})
test('missing industry data keeps a separately labeled price observation and no invented rank',async()=>{
 const html=await render(false,false,true)
 assert.match(html,/沪深三百，近期实际怎么走/)
 assert.match(html,/行业成分数量尚未取得/)
 assert.match(html,/不是未来方向预测/)
 assert.doesNotMatch(html,/近五日占比较前五日/)
})
