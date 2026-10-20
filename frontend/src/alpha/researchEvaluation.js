import {ref} from 'vue'
import request from '../api/request.js'
import {postSavedTask} from '../api/savedTask.js'
import {createInvestmentPlan} from './useInvestmentPlan.js'

export const evaluationRoot='/quant/research-evaluation/runs'
export const armLabel=arm=>({ORIGINAL:'原流程',METHODS:'增加动态方法',TOOLS:'再增加工具',ENHANCED:'增强流程',NO_METHOD:'无方法片段',FIXED_TREND:'固定趋势路线',ADAPTIVE_ROUTE:'适配投资路线'})[arm]||arm
export function evaluationStatus(status){if(status?.includes(' / '))return status.split(' / ').map(evaluationStatus).join(' / ');if(status?.startsWith('RESEARCH_'))return '研究未完成（'+(({RUNNING:'执行中',FAILED:'未通过',INTERRUPTED:'已中断',CANCELLED:'已取消',PENDING:'等待执行',NOT_STARTED:'尚未开始'})[status.slice(9)]||'详情见原研究')+'）';return ({RUNNING:'评测进行中',COMPLETED:'评测运行完成',FAILED:'本次未完成',INTERRUPTED:'等待继续',NOT_STARTED:'尚未开始',NOT_OBSERVED:'尚未核对',PAIRED_MATURED:'配对已成熟',FLAT:'平盘，单列',PENDING:'尚未到核对日',MATURED:'已到期核对',APPROVED:'复核通过',PENDING_OBSERVATION:'等待真实核对',NOT_COMPLETED:'尚未完成',PENDING_CALENDAR:'等待目标日历',CALENDAR_CHANGED_UNSCORABLE:'日历变更，不计成绩',DATA_MISSING_UNSCORABLE:'核对数据缺失',LATE_ISSUE_UNSCORABLE:'签发过晚，不计成绩',SUSPENDED_UNSCORABLE:'停牌，不计成绩',ACQUISITION_FAILED:'核对取数未完成'})[status]||status||'尚未提供'}
export function evaluationStage(stage){if(!stage)return '已保存评测请求';if(stage.includes(':')){const [period,arm]=stage.split(':');return (period==='MEDIUM60'?'60日中期':period==='SWING20'?'20日波段':period)+' · '+armLabel(arm)}return ({REGISTERED:'实验条件已登记',SHARED_INPUTS:'取得并封存共享输入',METHOD_ABLATION:'运行方法与路线消融',COMPLETED:'评测结果已保存'})[stage]||stage}
export function paragraphs(value){return (Array.isArray(value)?value:typeof value==='string'?[value]:[]).filter(item=>typeof item==='string'&&item.trim())}
export function finiteNumber(value){if(value===null||value===undefined||value==='')return null;const n=Number(value);return Number.isFinite(n)?n:null}
export function formatValue(value,digits=2){const n=finiteNumber(value);return n===null?'—':n.toLocaleString('zh-CN',{maximumFractionDigits:digits})}
export function formatPercent(value){const n=finiteNumber(value);return n===null?'尚无成熟样本':formatValue(n*100,1)+'%'}
export function archiveAccuracy(accuracy,group){const denominator=finiteNumber(accuracy?.denominator);if(denominator===null)return '尚未提供';if(denominator===0){if(group?.registered===0)return '暂无样本';return (group?.records||[]).some(row=>['PENDING','PENDING_DATA'].includes(row.status))?'尚未到期':'尚无可计样本'}const value=finiteNumber(accuracy?.value);return value===null?'尚未提供':formatValue(value*100,1)+'%（'+formatValue(accuracy.numerator,0)+'/'+formatValue(denominator,0)+'）'}
export function costRange(cost){const lower=finiteNumber(cost?.lower_cny),upper=finiteNumber(cost?.upper_cny);if(lower===null||upper===null)return '尚不能估算';return lower===upper?'¥'+formatValue(lower,4):'¥'+formatValue(lower,4)+'–'+formatValue(upper,4)}
export function comparisonMetrics(rows,metric){return (rows||[]).map(row=>{const value=finiteNumber(metric==='tokens'?(finiteNumber(row.input_tokens)===null||finiteNumber(row.output_tokens)===null?null:Number(row.input_tokens)+Number(row.output_tokens)):metric==='cost'?row.cost?.upper_cny:row.elapsed_seconds);return {key:row.case_id+':'+row.arm,label:row.horizon_sessions+'日 · '+armLabel(row.arm),arm:row.arm,value,lower:metric==='cost'?finiteNumber(row.cost?.lower_cny):value}})}
export function barWidth(value,rows){const n=finiteNumber(value),max=Math.max(0,...rows.map(row=>finiteNumber(row.value)||0));return n===null||max===0?0:Math.max(0,Math.min(100,n/max*100))}
export function createResearchEvaluation(options={}){
  const api=options.api||request,items=ref([]),listError=ref(''),listing=ref(false)
  const ctl=createInvestmentPlan({...options,api,root:evaluationRoot})
  let disposed=false,revision=0
  async function list(){const ticket=++revision;listing.value=true;listError.value='';try{const result=await api(evaluationRoot);if(!disposed&&ticket===revision)items.value=Array.isArray(result)?result:result.items||result.runs||[]}catch(e){if(!disposed&&ticket===revision)listError.value=e.message}finally{if(!disposed&&ticket===revision)listing.value=false}}
  async function seal(){if(disposed||ctl.busy.value||!ctl.state.value||ctl.state.value.status!=='INTERRUPTED')return;const id=ctl.state.value.run_id;ctl.busy.value=true;ctl.error.value='';try{await postSavedTask(api,evaluationRoot+'/'+encodeURIComponent(id)+'/seal',{method:'POST'},{disposed:()=>disposed,waiting:message=>{ctl.error.value=message}});if(!disposed){await ctl.load(id);await list()}}catch(e){if(!disposed)ctl.error.value=e.message}finally{if(!disposed)ctl.busy.value=false}}
  return {...ctl,items,listError,listing,list,seal,dispose(){disposed=true;revision++;ctl.dispose()}}
}
