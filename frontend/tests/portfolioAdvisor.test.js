import test from 'node:test'
import assert from 'node:assert/strict'
import {createPortfolioAdvisor,portfolioIntent,exposureChart,correlationChart} from '../src/alpha/usePortfolioAdvisor.js'
import {parseRoute} from '../src/alpha/navigation.js'
import {deliveryPage} from '../src/alpha/useDelivery.js'
import {prototypeOptions} from '../src/utils/prototypeChartOptions.js'

test('portfolio has independent routing and preserves ordinary stock questions',()=>{
  assert.equal(portfolioIntent('多只持仓一起分析'),true)
  assert.equal(portfolioIntent('贵州茅台明日预测'),false)
  assert.equal(portfolioIntent('贵州茅台明日预测','portfolio'),false)
  assert.equal(deliveryPage({scenario:'portfolio_advisor'}),'portfolio-advisor')
  assert.deepEqual(parseRoute('#/portfolio-advisor/RUN-PA-test'),{page:'portfolio-advisor',id:'RUN-PA-test'})
  assert.deepEqual(parseRoute('#/holding-advisor/RUN-HA-test'),{page:'holding-advisor',id:'RUN-HA-test'})
})
test('exposure charts show provided projections and retain cash without computing trades',()=>{
  const d={before:{equity_weight:'0.8',industries:[{code:'A',name:'测试',weight:'0.8'}]},after:{equity_weight:'0.4',industries:[{code:'A',weight:'0.4'}]},changes:[{name:'甲',before_weight:'0.8',after_weight:'0.4'}]}
  const c=exposureChart(d)
  assert.deepEqual(c.x,['甲','现金'])
  assert.equal(c.series[0].data[0],80)
  assert.equal(c.series[1].data[1],60)
  assert.equal(prototypeOptions(c).xAxis.min,0)
  assert.equal(exposureChart(d,'industries').series[1].data[0],40)
})
test('correlation heatmap preserves actual zeros and hides unavailable pairs',()=>{
  const block=correlationChart({changes:[{security_id:'A',name:'甲'},{security_id:'B',name:'乙'}],correlation:{ids:['A','B'],matrix:[['1',null],['0','1']]}})
  const option=prototypeOptions(block)
  assert.deepEqual(option.series[0].data,[[0,0,1],[0,1,0],[1,1,1]])
  assert.equal(option.visualMap.min,-1)
  assert.equal(option.visualMap.max,1)
})
test('account retries retain identity, full positions and previous turn',async()=>{
  const calls=[]
  const ctl=createPortfolioAdvisor({api:async(path,options)=>{calls.push([path,JSON.parse(options.body)]);throw Error('断网')}})
  const payload={assets:[{entity:'000333',shares:100},{entity:'600036',shares:200}],available_cash:'12345.67',previous_run_id:'RUN-PA-parent'}
  await ctl.submit(payload);await ctl.submit(payload)
  assert.equal(calls[0][0],'/quant/portfolio-advisor/turns')
  assert.equal(calls[0][1].request_key,calls[1][1].request_key)
  assert.equal(calls[0][1].assets.length,2)
  assert.equal(calls[1][1].previous_run_id,'RUN-PA-parent')
  ctl.dispose()
})
