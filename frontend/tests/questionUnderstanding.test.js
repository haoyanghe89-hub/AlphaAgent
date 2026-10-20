import test from 'node:test'
import assert from 'node:assert/strict'
import {createQuestionUnderstanding,readUnderstanding,saveUnderstanding,showUnderstandingForRoute,publicTaskQuestion,taskDraft,taskLabel,pendingTaskDraft} from '../src/alpha/questionUnderstanding.js'
import {advisorResearchRequest} from '../src/alpha/advisorResearchRequest.js'

const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)}}
const completed=(tasks,run_id='RUN-QU-test')=>({run_id,status:'COMPLETED',data:{model_used:true,summary:'先研究，再比较',tasks}})
const tasks=[{mode:'investment',entities:['美的集团','格力电器'],horizon:'MEDIUM',horizon_sessions:60,focuses:['BASIS','CASHFLOW']},{mode:'compare',entities:['美的集团','格力电器'],horizon:'MEDIUM',focuses:['INVALIDATION']}]

test('unrelated historical reports never display the latest question as their own context',()=>{
  const saved={tasks:[{mode:'investment',result_id:'RUN-IR-current'},{mode:'holding'}]}
  assert.equal(showUnderstandingForRoute(saved,{page:'workbench'}),true)
  assert.equal(showUnderstandingForRoute(saved,{page:'investment-research',id:'RUN-IR-current'}),true)
  assert.equal(showUnderstandingForRoute(saved,{page:'investment-research',id:'RUN-IR-older'}),false)
  assert.equal(showUnderstandingForRoute(saved,{page:'holding-advisor'}),true)
  assert.equal(showUnderstandingForRoute(saved,{page:'library'}),false)
})

test('model understanding polls the local service and preserves every task and prior turn',async()=>{
  const saved=storage(),calls=[]
  saveUnderstanding(saved,{run_id:'RUN-QU-parent',tasks:[]})
  const understand=createQuestionUnderstanding(async(path,options)=>{calls.push({path,options});return options?.method==='POST'?{run_id:'RUN-QU-test',status:'RUNNING'}:completed(tasks)},{storage:saved,wait:async()=>{},uuid:()=> 'fixed-key'})
  const result=await understand('先判断美的集团中期，再和格力电器对比现金回收','next')
  assert.equal(calls[0].path,'/quant/questions/understand')
  assert.deepEqual(JSON.parse(calls[0].options.body),{request_key:'fixed-key',question:'先判断美的集团中期，再和格力电器对比现金回收',channel:'workbench',selected_mode:'next',previous_run_id:'RUN-QU-parent'})
  assert.equal(calls[1].path,'/quant/questions/RUN-QU-test')
  assert.deepEqual(result.tasks,tasks)
  assert.deepEqual(readUnderstanding(saved),result)
  assert.equal(result.active,0)
})

test('failed local requests retain retry identity and never claim model success',async()=>{
  const saved=storage(),keys=[]
  const understand=createQuestionUnderstanding(async(_path,options)=>{keys.push(JSON.parse(options.body).request_key);throw Error('本地服务不可用')},{storage:saved,uuid:()=> 'same-key'})
  await assert.rejects(understand('那第二只呢？'),/本地服务不可用/)
  await assert.rejects(understand('那第二只呢？'),/本地服务不可用/)
  assert.deepEqual(keys,['same-key','same-key'])
  assert.equal(readUnderstanding(saved),null)
  const fallback=createQuestionUnderstanding(async()=>({...completed(tasks),data:{tasks,model_used:false}}),{storage:saved})
  await assert.rejects(fallback('看看这两只'),/问题理解服务暂时不可用/)
})

test('incomplete task outputs do not replace previous context',async()=>{
  const saved=storage(),before={run_id:'RUN-QU-old',tasks}
  saveUnderstanding(saved,before)
  const understand=createQuestionUnderstanding(async()=>completed([{mode:'invented',entities:[]}]),{storage:saved})
  await assert.rejects(understand('继续'),/问题理解结果不完整/)
  assert.deepEqual(readUnderstanding(saved),before)
})

test('research uses verified task fields and never carries the private original question',()=>{
  const task={...tasks[0],question:'我的成本83.719、1379股、现金987654.31',private_marker:'PRIVATE-SENTINEL'}
  const draft=taskDraft(task),question=publicTaskQuestion(task)
  assert.deepEqual(draft,{entities:'美的集团 格力电器',horizon:'MEDIUM',horizon_sessions:60,analysis_focus:['BASIS','CASHFLOW']})
  assert.match(question,/现金回收/)
  assert.doesNotMatch(JSON.stringify(draft)+question,/83\.719|1379|987654\.31|PRIVATE-SENTINEL|我的成本/)
  assert.equal(taskLabel({mode:'candidates',entities:[],industry:'equipment'}),'行业候选 · 半导体设备')
})

test('advisor understanding is additive and never overwrites local account values',()=>{
  const input={previous_run_id:'RUN-HA-old',question:'先别说补仓，什么会推翻判断',position:{cost_price:'83.719',shares:1379,total_assets:'987654.31'},semantic_understanding:false}
  const result=advisorResearchRequest(input,{})
  assert.equal(result.semantic_understanding,true)
  assert.equal(result.enhanced_research,undefined)
  assert.deepEqual(result.position,input.position)
  assert.equal(input.semantic_understanding,false)
  assert.equal(result.question,input.question)
})

test('only explicitly allowed model metadata is saved for developer inspection',async()=>{
  const saved=storage(),metadata={public_projection:{question:'匿名股票，现金回收'},model_calls:2,tokens:4520,elapsed_seconds:12.3}
  const understand=createQuestionUnderstanding(async()=>({...completed(tasks),...metadata,raw_private_state:{token:'DO-NOT-COPY'},request:{question:'DO-NOT-COPY'},calls:[{secret:'DO-NOT-COPY'}],source_ref:'DO-NOT-COPY'}),{storage:saved})
  const result=await understand('公开研究')
  for(const [key,value] of Object.entries(metadata))assert.deepEqual(result[key],value)
  assert.equal('raw_private_state' in result,false)
  assert.equal('calls' in result,false)
  assert.equal('request' in result,false)
  assert.equal('source_ref' in result,false)
  assert.doesNotMatch(JSON.stringify(readUnderstanding(saved)),/DO-NOT-COPY/)
})

test('pending task restores its current form after the one-time seed was consumed',()=>{
  const saved=storage()
  saveUnderstanding(saved,{tasks,active:0,question:'我的成本83.719，先研究中期'})
  assert.deepEqual(pendingTaskDraft(saved,'investment'),taskDraft(tasks[0]))
  assert.equal(pendingTaskDraft(saved,'holding'),null)
  const holding={mode:'holding',entities:['招商银行'],horizon:'MEDIUM',horizon_sessions:60,focuses:['INVALIDATION']}
  saveUnderstanding(saved,{tasks:[holding],active:0,question:'我的成本83.719，什么会推翻原判断？'})
  assert.deepEqual(pendingTaskDraft(saved,'holding'),{entity:'招商银行',question:'我的成本83.719，什么会推翻原判断？',horizon:'MEDIUM',horizon_sessions:60})
  const portfolio={...holding,mode:'portfolio',entities:['招商银行','美的集团']}
  saveUnderstanding(saved,{tasks:[portfolio],active:0,question:'现金987654.31，哪个行业最集中？'})
  assert.deepEqual(pendingTaskDraft(saved,'portfolio'),{entities:['招商银行','美的集团'],question:'现金987654.31，哪个行业最集中？',horizon:'MEDIUM',horizon_sessions:60})
  assert.equal('available_cash' in pendingTaskDraft(saved,'portfolio'),false)
  assert.equal('cost_price' in pendingTaskDraft(saved,'portfolio'),false)
})

test('completed, other-mode and invalid active tasks never restore a new draft',()=>{
  const saved=storage()
  saveUnderstanding(saved,{tasks:[{...tasks[0],result_id:'RUN-IR-completed'}],active:0})
  assert.equal(pendingTaskDraft(saved,'investment'),null)
  saveUnderstanding(saved,{tasks,active:1})
  assert.equal(pendingTaskDraft(saved,'investment'),null)
  saveUnderstanding(saved,{tasks,active:9})
  assert.equal(pendingTaskDraft(saved,'investment'),null)
})
