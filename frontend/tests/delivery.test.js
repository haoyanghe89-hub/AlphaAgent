import assert from 'node:assert/strict'
import test from 'node:test'
import { comparisonEntities, deliveryPage, useDelivery } from '../src/alpha/useDelivery.js'
import { parseRoute } from '../src/alpha/navigation.js'

test('dynamic comparison preserves both arbitrary entities and new history links', () => {
  assert.deepEqual(comparisonEntities('美的集团和格力电器对比'),['美的集团','格力电器'])
  assert.deepEqual(comparisonEntities('比较宁德时代与比亚迪'),['宁德时代','比亚迪'])
  assert.deepEqual(comparisonEntities('000333 vs 000651，比较估值'),['000333','000651'])
  assert.equal(deliveryPage({scenario:'compare_live'}),'compare')
  assert.equal(deliveryPage({scenario:'strategy'}),'strategy')
  assert.deepEqual(parseRoute('#/strategy/RUN-STRAT-1'),{page:'strategy',id:'RUN-STRAT-1'})
})

test('delivery lost-response retry preserves UUID; a new successful request gets a new UUID', async () => {
  const requests = [], timers = []
  let fail = true
  const view = useDelivery(() => {},{schedule:fn => timers.push(fn),unschedule:()=>{},api: async (path,opts) => {
    requests.push(JSON.parse(opts.body))
    if (fail) {fail=false; throw new Error('lost response')}
    return {run_id:'RUN',status:'RUNNING'}
  }})
  await view.submit('comparisons',{entities:['A','B']})
  await view.submit('comparisons',{entities:['A','B']})
  assert.equal(requests[0].request_key,requests[1].request_key)
  await view.submit('comparisons',{entities:['A','B']})
  assert.notEqual(requests[1].request_key,requests[2].request_key)
  view.dispose()
})

test('delivery integrity failures hide old numeric results; disposed responses cannot return', async () => {
  let fail = false
  const view = useDelivery(() => {},{api: async () => {
    if(fail) throw Object.assign(new Error('integrity failure'),{status:409})
    return {run_id:'RUN',status:'COMPLETED',data:{metrics:{total_ret:1}}}
  }})
  await view.load('RUN'); assert.ok(view.data.value.data.metrics)
  fail=true; await view.load('RUN'); assert.equal(view.data.value,null)
  let finish
  const late = useDelivery(() => {},{api:()=>new Promise(resolve=>{finish=resolve})})
  const running = late.load('RUN'); late.dispose(); finish({status:'COMPLETED',data:{metrics:{total_ret:99}}})
  await running; assert.equal(late.data.value,null)
})
