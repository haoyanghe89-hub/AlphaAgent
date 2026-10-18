import test from 'node:test'
import assert from 'node:assert/strict'
import {holdingIntent,advisorChart,createHoldingAdvisor} from '../src/alpha/useHoldingAdvisor.js'
import {parseRoute} from '../src/alpha/navigation.js'
import {deliveryPage} from '../src/alpha/useDelivery.js'

test('holding questions use a separate route without changing explicit forecast routing',()=>{
  assert.equal(holdingIntent('美的集团持仓咨询','next').entity,'美的集团')
  assert.equal(holdingIntent('我持有600519成本1500元，到多少可以卖？').entity,'600519')
  assert.equal(holdingIntent('美的集团到多少可以卖？').entity,'美的集团')
  assert.equal(holdingIntent('美的集团明日预测','next'),null)
  assert.equal(holdingIntent('美的集团明日预测','holding'),null)
  assert.equal(holdingIntent('000333','holding').entity,'000333')
  assert.deepEqual(parseRoute('#/holding-advisor'),{page:'holding-advisor'})
  assert.deepEqual(parseRoute('#/holding-advisor/RUN-HA-123'),{page:'holding-advisor',id:'RUN-HA-123'})
  assert.equal(deliveryPage({scenario:'holding_advisor'}),'holding-advisor')
})

test('holding chart keeps research history and observed quote separate from cost and conditions',()=>{
  const data={chart:[{date:'20261016',close:'100'}],quote:{quoted_at:'2026-10-19T10:00:00+08:00',price:'101'},facts:{cost_price:'95'},gates:[{kind:'DEFENSE',title:'下行防守参考',price:'90'}]}
  const chart=advisorChart(data)
  assert.deepEqual(chart.x,['20261016','20261019 报价'])
  assert.deepEqual(chart.series[0].data,[100,101])
  assert.deepEqual(chart.series[1].data,[95,95])
  assert.equal(data.chart.length,1)
})

test('holding submission retains parent, local private inputs and idempotent retry key',async()=>{
  const calls=[]
  const ctl=createHoldingAdvisor({api:async(path,o)=>{calls.push([path,JSON.parse(o.body)]);throw Error('暂时断网')}})
  const input={previous_run_id:'RUN-HA-parent',question:'如果跌到85元怎么办？',position:{cost_price:'95',shares:100,total_assets:'100000'}}
  await ctl.submit(input);await ctl.submit(input)
  assert.equal(calls[0][0],'/quant/holding-advisor/turns')
  assert.equal(calls[0][1].request_key,calls[1][1].request_key)
  assert.equal(calls[1][1].previous_run_id,'RUN-HA-parent')
  assert.equal(calls[1][1].position.shares,100)
  ctl.dispose()
})
