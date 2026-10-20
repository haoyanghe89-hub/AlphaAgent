import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'
import {renderToString} from '@vue/server-renderer'

test('valuation citations display original multiples and ten-thousand-yuan units with bound downloads',async()=>{
  const {descriptor}=parse(readFileSync(new URL('../src/alpha/InvestmentOpinion.vue',import.meta.url),'utf8'))
  const code=compileScript(descriptor,{id:'opinion-test',inlineTemplate:true,genDefaultAs:'Component'}).content
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"]/g,(_,names)=>'const {'+names.replace(/\s+as\s+/g,':')+'}=Vue')
  const Component=Function('Vue',code+';return Component')(Vue)
  const source='blob://sha256/'+'a'.repeat(64)
  const evidence=[{evidence_id:'R-pe',value:'15.25',unit:'倍',metric_key:'pe_ttm',source_refs:[source]},
    {evidence_id:'R-mv',value:'1234567.89',unit:'万元',metric_key:'total_mv',source_refs:[source]}]
  const opinion={direction:'FAVORABLE',summary:'合成展示测试',claims:[{claim_id:'C1',text:'合成展示',evidence_ids:['R-pe','R-mv']}],opposing:[],invalidation:[],next_checks:[],method_rule_ids:[]}
  const html=await renderToString(Vue.createSSRApp(Component,{opinion,evidence,fragments:[],runId:'RUN-IR-synthetic'}))
  assert.match(html,/15\.25 倍/)
  assert.match(html,/1,234,567\.89 万元/)
  assert.match(html,new RegExp('/api/quant/investment-research/runs/RUN-IR-synthetic/sources/'+'a'.repeat(64)))
})
