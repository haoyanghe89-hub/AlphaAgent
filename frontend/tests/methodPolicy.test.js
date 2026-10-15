import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'
import {renderToString} from '@vue/server-renderer'

// Render the actual Vue component using the compiler already installed with Vue.
const {descriptor}=parse(readFileSync(new URL('../src/alpha/MethodPolicyAnalysis.vue',import.meta.url),'utf8'))
const compiled=compileScript(descriptor,{id:'method-policy-test',inlineTemplate:true,genDefaultAs:'Component'}).content
const code=compiled.replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"]/g,(_,names)=>'const {'+names.replace(/\s+as\s+/g,':')+'}=Vue')
const Component=Function('Vue',code+'; return Component')(Vue)
const render=(data,developer=false)=>renderToString(Vue.createSSRApp(Component,{data,developer}))

function fixture(){
  return {
    method_routing:{profile:{role_label:'设备供应商',evidence_ids:['COMPANY-1'],limitations:['产品占比尚未标准化']},primary_method_id:'TEST-QUALITY',auxiliary_method_ids:['TEST-POLICY'],selection_reason:'开发专用：准入检查已经完成',
      candidates:[{method_id:'TEST-QUALITY',title:'经营质量',implementation_status:'EXECUTABLE_LIMITED',status:'CONDITIONAL',reason:'当前原文可以核查现金回收',missing_inputs:[]},{method_id:'TEST-POLICY',title:'政策核查',implementation_status:'EXECUTABLE_LIMITED',status:'OBSERVATION_ONLY',reason:'只核查已封存条款',missing_inputs:[]},{method_id:'TEST-DCF',title:'逆向估值',implementation_status:'REFERENCE_ONLY',status:'MISSING_INPUTS',reason:'没有可审核的估值假设',missing_inputs:['规范化现金流']}],
      execution_records:[{method_id:'TEST-QUALITY',title:'经营质量',status:'APPROVED',input_evidence_ids:['COMPANY-1'],output_paths:['claims'],review_ids:['REVIEW-1'],source_note_ids:['NOTE-1'],limitations:['不代表完整质量因子模型']}]},
    report:{method_assessment:{summary:'本次重点核查设备验收与现金回收。',evidence_ids:['COMPANY-1']},policy_assessment:{summary:'产业支持构成背景，具体受益仍待确认。',company_link:'业务可能处于文件覆盖范围，资格尚未核定。',opposing_risk:'海外供应限制可能形成反向影响。',next_check:'核查具体产品与适用条件。',evidence_ids:['POLICY-1']}},
    policy_report:{status:'PARTIAL',policies:[{policy_id:'P-1',title:'测试官方政策',issuer:'测试发布机关',jurisdiction:'中国',document_kind:'FORMAL_POLICY',document_status:'FORMAL',current_validity:'UNKNOWN',qualification_status:'UNKNOWN',scope:'测试适用范围',evidence_ids:['POLICY-1'],limitations:['未核定当前公司资格']}],gaps:[{reason:'HTTPError: internal diagnostic'}]},
    knowledge_notes:[{note_id:'NOTE-1',title:'测试公开片段笔记',authors:['测试作者'],coverage:'PUBLIC_EXCERPT_VERIFIED',read_scope:'只读取公开介绍，不代表整本书已阅读',summary:'按输入条件核查方法适配。',source_locator:{section:'公开介绍中的方法部分',anchors:['测试定位原文']},actual_locators:[{start:0,end:6,anchor:'测试定位原文'}],source_url:'https://example.com/primary',forbidden_inferences:['不可视作已验证因子收益']}],
  }
}

test('user sees reviewed business and policy conclusions while method diagnostics stay private',async()=>{
  const data=fixture(),before=structuredClone(data),html=await render(data)
  assert.match(html,/这家公司，重点怎么看/)
  assert.match(html,/设备供应商/)
  assert.match(html,/本次重点核查设备验收与现金回收/)
  assert.match(html,/海外供应限制可能形成反向影响/)
  assert.match(html,/测试官方政策/)
  assert.match(html,/资格和条款适用性仍需核对/)
  assert.doesNotMatch(html,/开发专用|TEST-DCF|HTTPError|method-policy-development|原始路由/)
  assert.deepEqual(data,before)
})

test('developer can distinguish rejected methods, actual execution and partial reading coverage',async()=>{
  const html=await render(fixture(),true)
  assert.match(html,/没有可审核的估值假设/)
  assert.match(html,/规范化现金流/)
  assert.match(html,/模块实现/)
  assert.match(html,/本次主方法/)
  assert.match(html,/本次辅助/)
  assert.match(html,/本次未选/)
  assert.match(html,/已实现有限模块/)
  assert.match(html,/必要输入缺项/)
  assert.match(html,/REVIEW-1/)
  assert.match(html,/只读取公开介绍，不代表整本书已阅读/)
  assert.match(html,/本次已封存公开片段/)
  assert.match(html,/公开介绍中的方法部分/)
  assert.match(html,/本次已定位 1 个原文片段/)
  assert.match(html,/不可视作已验证因子收益/)
  assert.match(html,/https:\/\/example.com\/primary/)
  assert.match(html,/公司资格：状态待核对/)
  assert.match(html,/不代表投资胜率已验证/)
})

test('missing policy originals remain a visible coverage gap without leaking provider errors',async()=>{
  const data=fixture();data.report.policy_assessment={};data.policy_report={status:'UNAVAILABLE',policies:[],gaps:[{reason:'HTTPError: internal diagnostic'}]}
  const html=await render(data)
  assert.match(html,/本次未取得可支持公司影响判断的政策条款/)
  assert.match(html,/不能据此认定公司已受益或没有政策风险/)
  assert.doesNotMatch(html,/HTTPError|测试官方政策|查看定位条款/)
})

test('citation actions preserve the evidence identifier rather than linking method notes as facts',()=>{
  const received=[],renderVNode=Component.setup({data:fixture(),developer:false},{emit:(...args)=>received.push(args)})
  const tree=renderVNode({},[]),buttons=[]
  function visit(node){
    if(Array.isArray(node)){node.forEach(visit);return}
    if(!node || typeof node!=='object')return
    if(node.type==='button' && node.props?.onClick)buttons.push(node)
    visit(node.children)
  }
  visit(tree)
  buttons.forEach(button=>button.props.onClick())
  assert.ok(received.some(args=>args[0]==='evidence' && args[1]==='COMPANY-1'))
  assert.ok(received.some(args=>args[0]==='evidence' && args[1]==='POLICY-1'))
  assert.ok(received.every(args=>args[0]==='evidence' && !args[1].startsWith('NOTE-')))
})

test('developer source links accept only HTTPS and source prose is escaped',async()=>{
  const data=fixture();data.knowledge_notes=[{title:'<script>malicious()</script>',sources:[{url:'javascript:malicious()'},{url:'http://example.com/unencrypted'},{url:'https://example.com/primary',title:'原始资料'}]}]
  const html=await render(data,true)
  assert.match(html,/&lt;script&gt;malicious\(\)&lt;\/script&gt;/)
  assert.doesNotMatch(html,/href="javascript:|href="http:\/\//)
  assert.match(html,/href="https:\/\/example.com\/primary"/)
})
