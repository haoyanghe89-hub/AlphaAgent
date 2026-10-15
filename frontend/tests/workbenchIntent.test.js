import assert from 'node:assert/strict'
import test from 'node:test'
import {workbenchIntent,decisionConditions} from '../src/alpha/workbenchIntent.js'
import {nextSessionChartBlocks,accuracyChartBlock,marketChartBlocks,candidateChartBlocks} from '../src/utils/decisionChartOptions.js'
import {prototypeOptions} from '../src/utils/prototypeChartOptions.js'

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
