import test from 'node:test'
import assert from 'node:assert/strict'
import {fundingChart,policyState} from '../src/alpha/useInvestmentContext.js'
test('funding charts keep separate clocks, missing values and negative net flow',()=>{
  const daily=fundingChart({points:[{date:'20261016',main:'-200000000'},{date:'20261019',main:null}]})
  const minute=fundingChart({points:[{at:'2026-10-16T14:59:00+08:00',main:300000000}]},true)
  assert.deepEqual(daily.series[0].data,[-2,null]);assert.deepEqual(daily.x,['20261016','20261019'])
  assert.deepEqual(minute.x,['14:59']);assert.deepEqual(minute.series[0].data,[3])
  assert.notEqual(daily.series[0].name,minute.series[0].name)
  const gaps=fundingChart({points:[{at:'2026-10-16T14:58:00+08:00',main:0},{at:'2026-10-16T15:00:00+08:00',main:200}]},true)
  assert.deepEqual(gaps.x,['14:58','14:59','15:00']);assert.equal(gaps.series[0].data[1],null)
})
test('policy lifecycle never displays unknown applicability as effective',()=>{
  assert.equal(policyState({temporal_status:'UNKNOWN'}),'现行状态待核')
  assert.equal(policyState({temporal_status:'EXPIRED'}),'已超出声明适用期')
})
