import test from 'node:test'
import assert from 'node:assert/strict'
import {developerHref,investmentIntent,userHistoryItem} from '../src/alpha/uiPresentation.js'
import {parseRoute} from '../src/alpha/navigation.js'

test('presentation mode preserves the report identity and research horizon',()=>{
  const source='http://127.0.0.1:8839/?research=1#/decision/RUN-DEC-saved'
  assert.equal(developerHref(source),'/?research=1&developer=1#/decision/RUN-DEC-saved')
  assert.equal(developerHref('http://127.0.0.1:8839/?research=1&developer=1#/decision/RUN-DEC-saved',false),'/?research=1#/decision/RUN-DEC-saved')
  assert.equal(developerHref(source,true,'developer'),'/?research=1&developer=1#/developer')
  assert.deepEqual(parseRoute('#/developer'),{page:'developer'})
})

test('investment mode accepts names while explicit forecasts keep their original route',()=>{
  assert.equal(investmentIntent('美的集团 招商银行','investment'),true)
  assert.equal(investmentIntent('请生成美的集团的投资计划','next'),true)
  assert.equal(investmentIntent('美的集团明日预测','investment'),false)
  assert.equal(investmentIntent('宁德时代未来三个月怎么看','investment'),false)
})

test('investment selection never captures explicit holding, account or comparison requests',()=>{
  for(const question of ['美的集团持仓咨询','多只持仓一起分析，应该先减哪只','分析账户的行业集中度','美的集团和格力电器对比','低PE高ROE回测']) {
    assert.equal(investmentIntent(question,'investment'),false,question)
  }
  assert.equal(investmentIntent('请生成美的集团的投资计划，评估是否加仓','investment'),true)
})

test('user history retains unfinished requests and strategy reports while technical artifacts belong to developers',()=>{
  assert.equal(userHistoryItem({scenario:'holding_advisor',status:'FAILED',title:'我的持仓咨询'}),true)
  assert.equal(userHistoryItem({scenario:'strategy',status:'COMPLETED',title:'年度策略回测'}),true)
  assert.equal(userHistoryItem({scenario:'backtest',title:'官方档案取证'}),false)
  assert.equal(userHistoryItem({scenario:'decision',title:'中国平安 · 定向取证'}),false)
})
