import {test} from 'node:test'
import assert from 'node:assert/strict'
import {createInvestmentPlan,investmentChart} from '../src/alpha/useInvestmentPlan.js'
import {deliveryPage} from '../src/alpha/useDelivery.js'
import {parseRoute} from '../src/alpha/navigation.js'

test('investment plan route and history use independent namespace',()=>{
  assert.deepEqual(parseRoute('#/investment/RUN-IP-test'),{page:'investment',id:'RUN-IP-test'})
  assert.equal(deliveryPage({scenario:'investment'}),'investment')
  assert.equal(deliveryPage({scenario:'investment_research'}),'investment-research')
  assert.deepEqual(parseRoute('#/investment-research/RUN-IR-test'),{page:'investment-research',id:'RUN-IR-test'})
})

test('model investment research uses its own root and preserves trading-day contract',async()=>{
  const calls=[];const ctl=createInvestmentPlan({root:'/quant/investment-research/runs',api:async(path,options)=>{calls.push([path,JSON.parse(options.body)]);return {run_id:'RUN-IR-test',status:'COMPLETED'}}})
  await ctl.submit({schema_version:'investment-research-v1',entities:['000333'],horizon:'MEDIUM',horizon_sessions:60,profile:null})
  assert.equal(calls[0][0],'/quant/investment-research/runs');assert.equal(calls[0][1].horizon_sessions,60);ctl.dispose()
})
test('retry retains request key and changed profile gets a different key',async()=>{
  const keys=[];const ctl=createInvestmentPlan({api:async(_,o)=>{keys.push(JSON.parse(o.body).request_key);throw Error('网络失败')}})
  await ctl.submit({entities:['000333'],profile:null});await ctl.submit({entities:['000333'],profile:null})
  assert.equal(keys[0],keys[1]);await ctl.submit({entities:['000333'],profile:{capital:'50000'}});assert.notEqual(keys[1],keys[2]);ctl.dispose()
})
test('late polls and integrity failure cannot display stale results',async()=>{
  let finish
  const ctl=createInvestmentPlan({api:path=>path.endsWith('/old')?new Promise(r=>finish=r):path.endsWith('/bad')?Promise.reject(Error('校验失败')):Promise.resolve({run_id:'new',status:'COMPLETED',data:{name:'new'}})})
  const old=ctl.load('old');await ctl.load('new');finish({run_id:'old',status:'COMPLETED'});await old
  assert.equal(ctl.state.value.run_id,'new');await ctl.load('bad');assert.equal(ctl.state.value,null);ctl.dispose()
})
test('chart preserves actual dates, reference zeros and missing values',()=>{
  const chart=investmentChart({name:'合成',chart:[{date:'20260101',close:'10'}],selected_route:'T',routes:[{route_id:'T',entry_reference:'11',risk_reference:null}]})
  assert.deepEqual(chart.x,['20260101']);assert.equal(chart.series[0].data[0],10);assert.equal(chart.series[2].data[0],null)
})
