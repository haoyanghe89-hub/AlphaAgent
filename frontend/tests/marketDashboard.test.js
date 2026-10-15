import assert from 'node:assert/strict'
import test from 'node:test'
import { createMarketDashboard } from '../src/alpha/useMarketDashboard.js'
import { prototypeOptions } from '../src/utils/prototypeChartOptions.js'

test('market feed prevents double requests, retains dated snapshot on failure, retires late responses',async()=>{
  let finish,calls=0,fail=false
  const value={retrieved_at:'2026-10-15T06:00:00Z',industries:[{name:'合成行业'}]}
  const feed=createMarketDashboard({api:()=>{calls++;if(fail)throw Error('行情源不可用');return new Promise(resolve=>{finish=resolve})}})
  const loading=feed.refresh();await feed.refresh();assert.equal(calls,1)
  finish(value);await loading;assert.deepEqual(feed.data.value,value)
  fail=true;await feed.refresh();assert.equal(feed.error.value,'行情源不可用');assert.equal(feed.data.value.retrieved_at,value.retrieved_at)
  fail=false;const late=feed.refresh();feed.dispose();finish({industries:[]});await late
  assert.equal(feed.data.value.retrieved_at,value.retrieved_at)
})
test('industry chart displays every label in ranking order and preserves signed zero and gaps',()=>{
  const options=prototypeOptions({kind:'sector_rank',x:['甲','乙','丙'],series:[{name:'涨跌幅 %',data:[2,0,-1,null]}]})
  assert.equal(options.yAxis.inverse,true);assert.deepEqual(options.yAxis.data,['甲','乙','丙'])
  assert.deepEqual(options.series[0].data,[2,0,-1,null]);assert.equal(options.tooltip.valueFormatter(-1),'-1.00%')
})
