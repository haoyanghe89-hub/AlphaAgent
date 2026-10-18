import assert from 'node:assert/strict'
import test from 'node:test'
import {workbenchIntent,decisionConditions,investmentEntities,investmentDraft} from '../src/alpha/workbenchIntent.js'
import {nextSessionChartBlocks,accuracyChartBlock,marketChartBlocks,candidateChartBlocks} from '../src/utils/decisionChartOptions.js'
import {prototypeOptions} from '../src/utils/prototypeChartOptions.js'

test('investment questions preserve stock names including embedded command characters',()=>{
  for(const text of ['美的集团投资计划','请帮我生成美的集团的投资计划','投资计划 美的集团']) assert.equal(investmentEntities(text),'美的集团')
  assert.equal(investmentEntities('请制定 美的集团 600036 的资金安排'),'美的集团 600036')
})

test('a natural investment request carries verified names and the stated horizon to the form',async()=>{
  const names=['美的集团','招商银行','和而泰','同和药业']
  const search=async q=>names.filter(name=>name===q).map(name=>({name}))
  assert.deepEqual(await investmentDraft('请为美的集团和招商银行制定未来三个月的投资计划',search),{entities:'美的集团 招商银行',horizon:'MEDIUM',horizon_sessions:60})
  assert.deepEqual(await investmentDraft('请给和而泰和同和药业生成波段投资计划',search),{entities:'和而泰 同和药业',horizon:'SWING',horizon_sessions:20})
  assert.equal((await investmentDraft('同和药业投资计划',search)).entities,'同和药业')
})

test('directory suggestions never invent a replacement company and lookup errors remain explicit',async()=>{
  const draft=await investmentDraft('同和药业投资计划',async()=>[{name:'同花顺'}])
  assert.equal(draft.entities,'同和药业')
  await assert.rejects(investmentDraft('美的集团和招商银行投资计划',async()=>{throw Error('证券目录连接失败')}),/证券目录连接失败/)
})

test('joined-name lookup stays bounded even for an oversized question',async()=>{
  let calls=0
  await investmentDraft(Array(15).fill('同和药业').join('和')+'投资计划',async name=>{calls++;return name==='同和药业'?[{name}]:[]})
  assert.ok(calls<=16)
})

test('workbench routes four questions and a selected bare stock',()=>{
  for(const text of ['贵州茅台明日预测','明日预测 贵州茅台','预测贵州茅台下一交易日涨跌','贵州茅台明天会涨吗','明天预测一下贵州茅台','贵州茅台明天是涨还是跌'])assert.deepEqual(workbenchIntent(text,'market'),{mode:'next',entity:'贵州茅台',question:text})
  assert.equal(workbenchIntent('三一重工明天怎么走').entity,'三一重工')
  assert.equal(workbenchIntent('帮我看下宁德时代三个月以后走势').entity,'宁德时代')
  assert.equal(workbenchIntent('600519.SH 明天涨跌').entity,'600519.SH')
  assert.equal(workbenchIntent('宁德时代未来三个月怎么看','next').mode,'medium')
  assert.equal(workbenchIntent('宁德时代未来三个月怎么看').entity,'宁德时代')
  assert.equal(workbenchIntent('未来三个月预测美的集团').entity,'美的集团')
  assert.equal(workbenchIntent('市场观察','next').mode,'market')
  assert.equal(workbenchIntent('集成电路制造行业候选','next').entity,'集成电路制造')
  assert.equal(workbenchIntent('美的集团','next').entity,'美的集团')
  assert.equal(workbenchIntent('比较两个行业的市场行情','next').mode,'market')
  assert.equal(workbenchIntent('全面分析美的集团'),null)
})
test('missing stock and unsupported industries ask for a specific entity',()=>{
  for(const text of ['明日预测','未来三个月预测','明天涨还是跌'])assert.throws(()=>workbenchIntent(text,'next'),/股票名称/)
  assert.throws(()=>workbenchIntent('银行行业候选','candidates'),/其他行业尚未接入/)
})
test('workbench preserves separate research protocols and bounded budgets',()=>{
  const medium=decisionConditions(workbenchIntent('美的集团未来三个月怎么看'))
  assert.equal(medium.entity,'美的集团');assert.equal(medium.horizon_months,3);assert.equal(medium.include_methods,true)
  assert.deepEqual(medium.budget_profile,{max_rounds:1,max_evidence_calls:1})
  const market=decisionConditions(workbenchIntent('市场观察'))
  assert.equal(market.research_mode,'market_scan');assert.equal(market.scope,'market')
  const candidates=decisionConditions(workbenchIntent('半导体设备行业候选'))
  assert.equal(candidates.budget_profile.max_candidates,3)
})
test('charts only convert sealed values and preserve gaps, zero and negative cash',()=>{
  const blocks=nextSessionChartBlocks({daily_prices:[{session:'2026-10-14',close:'12.30',turnover:'100000000'},{session:'2026-10-15',close:null,turnover:'0'}]})
  assert.deepEqual(blocks[0].series[0].data,[12.3,null]);assert.deepEqual(blocks[1].series[0].data,[1,0])
  const board=accuracyChartBlock({statistics:{accuracy:{value:null},baselines:{ALWAYS_UP:{value:0.5},ALWAYS_DOWN:{value:0},PERSISTENCE:{value:1}}}})
  assert.deepEqual(board.series[0].data,[null,50,0,100])
  assert.equal(prototypeOptions(board).yAxis.length,1)
  assert.deepEqual(marketChartBlocks([{name:'设备',breadth:null,top3_turnover_share:'0.4'}])[1].series[0].data,[null])
  assert.equal(candidateChartBlocks([{name:'甲',metrics:{NETCASH_OPERATE:'-200000000'}}])[0].series[2].data[0],-2)
})
