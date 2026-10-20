import {postSavedTask} from '../api/savedTask.js'

const modes=new Set(['next','investment','medium','market','candidates','holding','portfolio','compare','strategy','clarify'])
export const questionModes={next:'明日预测',investment:'投资计划',medium:'中期研究',market:'市场观察',candidates:'行业候选',holding:'持仓顾问',portfolio:'账户调仓',compare:'股票比较',strategy:'策略回测',clarify:'补充问题'}
const savedKey='alpha-question-understanding'

export function readUnderstanding(storage){
  try{const value=JSON.parse(storage.getItem(savedKey)||'null');return value&&Array.isArray(value.tasks)?value:null}catch{return null}
}
export function saveUnderstanding(storage,value){storage.setItem(savedKey,JSON.stringify(value))}
export function showUnderstandingForRoute(saved,route){
  if(!saved)return false
  if(route.page==='workbench')return true
  const pages={next:'decision',medium:'decision',investment:'investment-research',market:'market',candidates:'candidates',holding:'holding-advisor',portfolio:'portfolio-advisor',compare:'compare',strategy:'strategy'}
  return saved.tasks.some(task=>pages[task.mode]===route.page&&(route.id?task.result_id===route.id:!task.result_id))
}

// The original question is only posted to the local server. Research receives verified task fields.
export function createQuestionUnderstanding(api,{storage,wait=ms=>new Promise(resolve=>setTimeout(resolve,ms)),disposed=()=>false,waiting=()=>{},uuid=()=>crypto.randomUUID()}={}){
  let retry
  return async(question,selected)=>{
    const previous=readUnderstanding(storage)
    const identity=JSON.stringify({question,selected,previous:previous?.run_id})
    if(retry?.identity!==identity)retry={identity,key:uuid()}
    const body={request_key:retry.key,question,channel:'workbench',...(selected?{selected_mode:selected}:{}),...(previous?.run_id?{previous_run_id:previous.run_id}:{})}
    let state=await postSavedTask(api,'/quant/questions/understand',{method:'POST',body:JSON.stringify(body),timeoutMs:45000},{disposed,waiting,wait})
    const deadline=Date.now()+120000
    while(!disposed()&&state?.status==='RUNNING'){
      if(Date.now()>deadline)throw Error('问题理解仍在处理，原输入已保存；请稍后重试。')
      waiting('正在理解问题、条件与前文指代…')
      await wait(1000)
      state=await api('/quant/questions/'+encodeURIComponent(state.run_id),{timeoutMs:45000})
    }
    if(disposed())return null
    if(state?.status!=='COMPLETED'||!state.data?.model_used)throw Error(state?.error||'问题理解服务暂时不可用。')
    const tasks=state.data.tasks
    if(!Array.isArray(tasks)||!tasks.length||tasks.some(t=>!modes.has(t.mode)||!Array.isArray(t.entities)))throw Error('问题理解结果不完整，请补充明确的股票与研究目标。')
    const value={run_id:state.run_id,question,summary:state.data.summary||'已识别本次研究任务',tasks:tasks.map(task=>({...task})),active:0,...Object.fromEntries(['public_projection','model_calls','tokens','elapsed_seconds'].filter(key=>state[key]!==undefined).map(key=>[key,state[key]]))}
    saveUnderstanding(storage,value);retry=null
    return value
  }
}

export function publicTaskQuestion(task){
  const labels={DIRECTION:'方向判断',BASIS:'方法依据',ADD:'加仓条件',SELL:'减仓条件',DEFENSE:'下行情景',HOLD:'持有条件',INVALIDATION:'判断失效条件',CASHFLOW:'现金回收',VALUATION:'估值',TECHNICAL:'技术条件',POLICY:'政策依据',FLOWS:'资金流向',ENTRY:'入场条件',CONCENTRATION:'行业集中度',CORRELATION:'相关风险',GENERAL:'综合分析'}
  const focus=(task.focuses||[]).map(v=>labels[v]||v).join('、')
  return `${questionModes[task.mode]||'股票研究'}：${task.entities.join('、')}${focus?'；重点核查'+focus:''}。使用统一知识体系，列出支持、反证与失效条件。`
}

export function taskDraft(task){
  return {entities:task.entities.join(' '),horizon:task.horizon==='MEDIUM'?'MEDIUM':'SWING',horizon_sessions:task.horizon_sessions||(task.horizon==='MEDIUM'?60:20),...(task.focuses?.length?{analysis_focus:task.focuses}:{})}
}

export function taskFormDraft(task,question=''){
  if(task.mode==='investment')return taskDraft(task)
  const common={question:question||publicTaskQuestion(task),horizon:task.horizon==='MEDIUM'?'MEDIUM':'SWING',horizon_sessions:task.horizon_sessions||(task.horizon==='MEDIUM'?60:20)}
  if(task.mode==='holding')return {...common,entity:task.entities[0]||''}
  if(task.mode==='portfolio')return {...common,entities:[...task.entities]}
  return null
}

export function pendingTaskDraft(storage,mode){
  const saved=readUnderstanding(storage),task=saved?.tasks[saved.active]
  return task?.mode===mode&&!task.result_id?taskFormDraft(task,saved.question):null
}

export function taskLabel(task){return [questionModes[task.mode],task.entities.join('、'),task.industry==='equipment'?'半导体设备':task.industry==='manufacturing'?'集成电路制造':''].filter(Boolean).join(' · ')}
