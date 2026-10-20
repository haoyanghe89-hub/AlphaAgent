import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'
import {renderToString} from '@vue/server-renderer'
import {archiveAccuracy,armLabel,barWidth,comparisonMetrics,costRange,createResearchEvaluation,evaluationRoot,evaluationStage,evaluationStatus,finiteNumber,formatPercent,formatValue,paragraphs} from '../src/alpha/researchEvaluation.js'
import {href,parseRoute} from '../src/alpha/navigation.js'
import {deliveryPage} from '../src/alpha/useDelivery.js'
import {userHistoryItem} from '../src/alpha/uiPresentation.js'
import {dateTime,historyStatus,isHistoryActive,isHistoryDone} from '../src/alpha/model.js'

test('evaluation route keeps exact experiment identity across refresh',()=>{
  assert.deepEqual(parseRoute('#/evaluation'),{page:'evaluation'})
  assert.deepEqual(parseRoute('#/evaluation/RUN-EV-123'),{page:'evaluation',id:'RUN-EV-123'})
  assert.deepEqual(parseRoute('#/portfolio-advisor/RUN-PA-prior'),{page:'portfolio-advisor',id:'RUN-PA-prior'})
  assert.equal(deliveryPage({scenario:'research_evaluation',status:'COMPLETED'}),'evaluation')
  assert.equal(deliveryPage({scenario:'research_evaluation',status:'INTERRUPTED'}),'evaluation')
})
test('missing usage and immature accuracy stay missing, never zero',()=>{
  for(const value of [null,undefined,'',NaN,Infinity])assert.equal(finiteNumber(value),null)
  assert.equal(formatValue(null),'—')
  assert.equal(formatPercent(null),'尚无成熟样本')
  assert.equal(formatPercent('0'),'0%')
  assert.equal(costRange({lower_cny:null,upper_cny:'0.2'}),'尚不能估算')
  assert.equal(costRange({lower_cny:'0',upper_cny:'0'}),'¥0')
  assert.equal(costRange({lower_cny:'0.02',upper_cny:'0.06'}),'¥0.02–0.06')
  assert.equal(archiveAccuracy({numerator:1,denominator:2,value:'0.5'},{}),'50%（1/2）')
  assert.equal(archiveAccuracy({numerator:0,denominator:0,value:null},{registered:1,records:[{status:'PENDING'}]}),'尚未到期')
  assert.equal(archiveAccuracy({numerator:0,denominator:0,value:null},{registered:1,records:[{status:'UNSCORABLE'}]}),'尚无可计样本')
  assert.equal(archiveAccuracy({numerator:0,denominator:0,value:null},{registered:0,records:[]}),'暂无样本')
})
test('paired charts keep missing rows and separate cost lower and upper bounds',()=>{
  const rows=[{case_id:'60',horizon_sessions:60,arm:'ORIGINAL',input_tokens:100,output_tokens:10,cost:{lower_cny:'0.01',upper_cny:'0.03'},elapsed_seconds:'12'},{case_id:'60',horizon_sessions:60,arm:'ENHANCED',input_tokens:null,output_tokens:20,cost:{lower_cny:null,upper_cny:null},elapsed_seconds:null}]
  const tokens=comparisonMetrics(rows,'tokens')
  assert.equal(tokens[0].value,110);assert.equal(tokens[1].value,null)
  const costs=comparisonMetrics(rows,'cost')
  assert.equal(costs[0].lower,.01);assert.equal(costs[0].value,.03);assert.equal(costs[1].value,null)
  assert.equal(barWidth(costs[0].lower,costs),100/3);assert.equal(barWidth(null,costs),0)
  assert.equal(armLabel('ENHANCED'),'增强流程');assert.equal(evaluationStatus('PENDING'),'尚未到核对日')
  assert.equal(evaluationStage('MEDIUM60:ENHANCED'),'60日中期 · 增强流程')
  assert.equal(evaluationStage('COMPLETED'),'评测结果已保存')
  assert.equal(evaluationStatus('NOT_OBSERVED / PENDING'),'尚未核对 / 尚未到核对日')
  assert.equal(evaluationStatus('RESEARCH_INTERRUPTED'),'研究未完成（已中断）')
  assert.equal(evaluationStatus('RESEARCH_BUDGET_EXHAUSTED'),'研究未完成（详情见原研究）')
  assert.deepEqual(paragraphs('一整段历史说明。'),['一整段历史说明。'])
  assert.deepEqual(paragraphs(['第一段','第二段']),['第一段','第二段'])
})
test('saved evaluation resumes and loads without creating another experiment',async()=>{
  const calls=[],timers=[]
  const api=async(path,options)=>{calls.push({path,options});if(path===evaluationRoot)return {items:[{run_id:'RUN-EV-prior',status:'COMPLETED'}]};if(path.endsWith('/resume'))return {status:'RUNNING'};return {run_id:'RUN-EV-prior',status:'RUNNING',stage:'配对研究'}}
  const ctl=createResearchEvaluation({api,schedule:(fn,ms)=>{timers.push({fn,ms});return timers.length},unschedule:()=>{}})
  await ctl.list();await ctl.load('RUN-EV-prior');await ctl.resume()
  assert.equal(ctl.items.value[0].run_id,'RUN-EV-prior')
  assert.equal(calls.filter(c=>c.options?.method==='POST').length,1)
  assert.equal(calls.find(c=>c.options?.method==='POST').path,evaluationRoot+'/RUN-EV-prior/resume')
  assert.equal(timers.at(-1).ms,2500)
  ctl.dispose()
})
test('late history response cannot overwrite a newer list or disposed component',async()=>{
  const pending=[];const ctl=createResearchEvaluation({api:()=>new Promise(resolve=>pending.push(resolve))})
  const first=ctl.list(),second=ctl.list();pending[1]([{run_id:'latest'}]);await second;pending[0]([{run_id:'stale'}]);await first
  assert.equal(ctl.items.value[0].run_id,'latest')
  const third=ctl.list();ctl.dispose();pending[2]([{run_id:'after-dispose'}]);await third
  assert.equal(ctl.items.value[0].run_id,'latest')
})

test('sealing failures uses the same experiment once without creating or resuming studies',async()=>{
  const calls=[];let sealed=false
  const api=async(path,options)=>{calls.push({path,options});if(path.endsWith('/seal')){sealed=true;return {run_id:'RUN-EV-failed',status:'COMPLETED'}}if(path===evaluationRoot)return [];return {run_id:'RUN-EV-failed',status:sealed?'COMPLETED':'INTERRUPTED'}}
  const ctl=createResearchEvaluation({api,schedule:()=>{},unschedule:()=>{}})
  await ctl.load('RUN-EV-failed');await Promise.all([ctl.seal(),ctl.seal()]);await ctl.seal()
  assert.equal(calls.filter(call=>call.options?.method==='POST').length,1)
  assert.equal(calls.find(call=>call.options?.method==='POST').path,evaluationRoot+'/RUN-EV-failed/seal')
  assert.equal(ctl.state.value.status,'COMPLETED');ctl.dispose()
})

test('evaluation page presents pending results without claiming measured prediction improvement',async()=>{
  const {descriptor}=parse(readFileSync(new URL('../src/alpha/EvaluationComparison.vue',import.meta.url),'utf8'))
  const code=compileScript(descriptor,{id:'evaluation-test',inlineTemplate:true,genDefaultAs:'Component'}).content.replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"]/g,(_,names)=>'const {'+names.replace(/\s+as\s+/g,':')+'}=Vue').replace(/^import .*$/gm,'')
  const row={case_id:'20',horizon_sessions:20,arm:'ORIGINAL',run_id:'RUN-IR-synthetic',status:'COMPLETED',matched_inputs:true,model_calls:3,input_count_calls:1,failed_calls:1,input_tokens:100,output_tokens:20,unknown_usage_calls:1,unknown_model_calls:1,cost:{lower_cny:null,upper_cny:null},elapsed_seconds:20,model_seconds:14,tool_calls:0,review_rounds:1,additional_evidence:0,directions:[{security_id:'000333.SZ',direction:'FAVORABLE',summary:'合成测试结论，非真实投资结果',claims:3,opposing:1,evidence_count:4,method_rule_ids:['METHOD']}],prediction:{stocks:[{security_id:'000333.SZ',status:'PENDING',target_date:'20261113',correct:null}]}}
  const controller={state:Vue.ref({run_id:'RUN-EV-synthetic',status:'COMPLETED',created_at:'2026-10-19T00:00:00Z',manifest:{},data:{rows:[row],pairs:[],prediction:{paired_matured:0,registered_pairs:2,independent_dates:0,status_counts:{PENDING:2}},limitations:['合成边界']}}),error:Vue.ref(''),busy:Vue.ref(false),items:Vue.ref([]),listError:Vue.ref(''),listing:Vue.ref(false),dispose(){}}
  const Component=Function('Vue','archiveAccuracy','armLabel','barWidth','comparisonMetrics','costRange','createResearchEvaluation','evaluationRoot','evaluationStage','evaluationStatus','formatValue','paragraphs',code+';return Component')(Vue,archiveAccuracy,armLabel,barWidth,comparisonMetrics,costRange,()=>controller,evaluationRoot,evaluationStage,evaluationStatus,formatValue,paragraphs)
  const html=await renderToString(Vue.createSSRApp(Component,{id:'RUN-EV-synthetic'}))
  assert.match(html,/目前没有非平盘成熟配对/);assert.match(html,/方向准确率和收益提升尚不能确认/)
  assert.match(html,/合成测试结论/);assert.match(html,/尚不能估算/);assert.match(html,/次调用缺少完整用量/)
  assert.match(html,/含输入计数 1 次、失败 1 次/);assert.match(html,/次调用的模型缺少本轮固定报价/);assert.match(html,/共享取数/)
  assert.match(html,/#\/investment-research\/RUN-IR-synthetic/);assert.match(html,/20261113/)
  assert.doesNotMatch(html,/提升已证实/)
  controller.state.value.data.rows[0].prediction={...row.prediction,through:'20261019',latest_valid_at:'2026-10-19T07:50:00Z',last_attempt:{status:'ACQUISITION_FAILED',created_at:'2026-10-19T10:20:00Z',error:'合成取数失败'}}
  controller.state.value.data.prediction={paired_matured:2,registered_pairs:4,independent_dates:1,unique_target_dates:2,original_correct:1,enhanced_correct:2,accuracy_delta:.5,status_counts:{FLAT:1,PAIRED_MATURED:2}}
  const mature=await renderToString(Vue.createSSRApp(Component,{id:'RUN-EV-synthetic'}))
  assert.match(mature,/非平盘准确率差/);assert.match(mature,/\+50 个百分点/)
  assert.match(mature,/独立登记日期<b>1<\/b>/);assert.match(mature,/不同目标日期<b>2<\/b>/)
  assert.match(mature,/不同目标日期不能当作相互独立的实验日期/);assert.match(mature,/平盘，单列/)
  assert.match(mature,/合成取数失败/);assert.match(mature,/仍保留最近有效核对，不能当作本次新核对结果/);assert.match(mature,/行情截至 20261019/)
  assert.match(mature,/共享取数后研究耗时/)
  controller.state.value.data.next_session_history={cutoff:'2026-10-19T01:00:00Z',groups:[{arm:'ORIGINAL',registered:6,records:[{status:'SCORABLE'}],accuracy:{numerator:1,denominator:2,value:'0.5'},baselines:{ALWAYS_UP:{numerator:1,denominator:2,value:'0.5'},ALWAYS_DOWN:{numerator:1,denominator:2,value:'0.5'},PERSISTENCE:{numerator:0,denominator:2,value:'0'}},independent_dates:1},{arm:'ENHANCED',registered:1,records:[{status:'PENDING'}],accuracy:{numerator:0,denominator:0,value:null},baselines:{},independent_dates:0}],limitations:['合成测试：历史样本不足']}
  const archived=await renderToString(Vue.createSSRApp(Component,{id:'RUN-EV-synthetic'}))
  assert.match(archived,/既有明日预测 · 非配对历史/);assert.match(archived,/不能归因增强效果/)
  assert.match(archived,/50%（1\/2）/);assert.match(archived,/尚未到期/);assert.match(archived,/0%（0\/2）/)
  assert.match(archived,/前日方向延续/);assert.match(archived,/历史样本不足/)
  controller.state.value.data.next_session_history.limitations='历史核对只有一个起点日期。'
  const stringNote=await renderToString(Vue.createSSRApp(Component,{id:'RUN-EV-synthetic'}))
  assert.match(stringNote,/<p class="condition-note">历史核对只有一个起点日期。<\/p>/)
  assert.doesNotMatch(stringNote,/<p class="condition-note">历<\/p>/)
  assert.match(stringNote,/<th>独立起点日期<\/th>/)
  controller.state.value.status='INTERRUPTED'
  controller.state.value.data.round_summary={declared_studies:4,completed_studies:1,unsuccessful_studies:3,declared_method_arms:6,completed_method_arms:3,verdict:'COMPLETE_WITH_FAILURES',arms:[{arm:'ORIGINAL',declared:2,completed:1,model_calls:6,input_tokens:100,output_tokens:20,cost:{lower_cny:'.02',upper_cny:'.05'},elapsed_seconds:20},{arm:'ENHANCED',declared:2,completed:0,model_calls:9,input_tokens:200,output_tokens:30,cost:{lower_cny:'.03',upper_cny:'.08'},elapsed_seconds:40}]}
  controller.state.value.data.rows[0].failed_checks=[{role:'Verifier',issue:'合成根因：引用支持不足',call_index:3}]
  const failed=await renderToString(Vue.createSSRApp(Component,{id:'RUN-EV-synthetic'}))
  assert.match(failed,/原流程研究完成<b>1 \/ 2<\/b>/);assert.match(failed,/增强流程研究完成<b>0 \/ 2<\/b>/)
  assert.match(failed,/存在未完成组，不能宣称增强提升/);assert.match(failed,/方法消融批准<b>3 \/ 6<\/b>/)
  assert.match(failed,/封存本轮含失败结果/);assert.match(failed,/继续原实验（原预算）/)
  assert.match(failed,/合成根因：引用支持不足/);assert.match(failed,/评测结束或报告封存不代表各组研究成功/)
  assert.match(failed,/原流程有 1 次调用用量未知，上表 Token 仅为已记录部分/)
  controller.state.value.status='COMPLETED'
  controller.state.value.data.pairs=[{case_id:'20',complete:false},{case_id:'60',complete:false}]
  const sealed=await renderToString(Vue.createSSRApp(Component,{id:'RUN-EV-synthetic'}))
  assert.match(sealed,/评测已封存（包含失败）/);assert.doesNotMatch(sealed,/封存本轮含失败结果<\/button>/)
  assert.match(sealed,/本轮没有双方都成功发布的配对，单纯等待到期不能形成增强准确率差；失败组不得回填，修复后须另建新实验/)
  controller.state.value.data.pairs[0].complete=true
  const partialPair=await renderToString(Vue.createSSRApp(Component,{id:'RUN-EV-synthetic'}))
  assert.doesNotMatch(partialPair,/本轮没有双方都成功发布的配对/)
})

test('evaluation history links to the saved comparison only in developer history',async()=>{
  const {descriptor}=parse(readFileSync(new URL('../src/alpha/LibraryView.vue',import.meta.url),'utf8'))
  const code=compileScript(descriptor,{id:'evaluation-history-test',inlineTemplate:true,genDefaultAs:'Component'}).content.replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"]/g,(_,names)=>'const {'+names.replace(/\s+as\s+/g,':')+'}=Vue').replace(/^import .*$/gm,'')
  const items=[{scenario:'research_evaluation',run_id:'RUN-EV-synthetic-history',title:'合成流程评测记录',status:'COMPLETED',created_at:'2026-10-19T01:00:00Z'},{scenario:'holding_advisor',run_id:'RUN-HA-existing',title:'已有持仓咨询',status:'COMPLETED',created_at:'2026-10-18T01:00:00Z'}]
  async function render(developer){const Component=Function('Vue','dateTime','historyStatus','isHistoryActive','isHistoryDone','href','deliveryPage','userHistoryItem','location',code+';return Component')(Vue,dateTime,historyStatus,isHistoryActive,isHistoryDone,href,deliveryPage,userHistoryItem,{search:developer?'?developer=1':''});return renderToString(Vue.createSSRApp(Component,{items,legacy:[]}))}
  const user=await render(false),developer=await render(true)
  assert.equal(userHistoryItem(items[0]),false);assert.equal(userHistoryItem(items[1]),true)
  assert.doesNotMatch(user,/合成流程评测记录|RUN-EV-synthetic-history/)
  assert.match(user,/已有持仓咨询/);assert.match(developer,/流程评测/)
  assert.match(developer,/#\/evaluation\/RUN-EV-synthetic-history/)
  assert.doesNotMatch(developer,/#\/report\/RUN-EV-synthetic-history/)
})
