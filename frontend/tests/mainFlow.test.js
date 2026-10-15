import assert from 'node:assert/strict'
import test from 'node:test'
import {createMainFlow} from '../src/alpha/useMainFlow.js'
import {prototypeOptions} from '../src/utils/prototypeChartOptions.js'
const tick=()=>new Promise(resolve=>setImmediate(resolve))

test('funding selections reject late data and keep original stock on refresh failure',async()=>{
  const calls=[];let fail=false
  const state=createMainFlow({api:(path)=>{if(fail)throw Error('资金源暂不可用');return new Promise(resolve=>calls.push({path,resolve}))}})
  const first=state.select('sh600000');await state.select('sz000001')
  calls[0].resolve({symbol:'sh600000'});await first;await tick()
  assert.equal(state.stock.value,null);assert.equal(calls[1].path,'/quant/research/main-flow/sz000001')
  calls[1].resolve({symbol:'sz000001',asof:'2026-10-15T15:00:00+08:00'});await tick()
  fail=true;await state.refreshStock();assert.equal(state.stock.value.symbol,'sz000001');assert.equal(state.stockError.value,'资金源暂不可用')
  state.dispose()
})
test('a late security search cannot override a stock chosen from the funds list',async()=>{
  let finishSearch
  const state=createMainFlow({api:path=>path.includes('securities')?new Promise(resolve=>finishSearch=resolve):Promise.resolve({symbol:path.split('/').pop()})})
  const lookup=state.search('合成甲');await state.select('sz000001');finishSearch([{symbol:'sh600000'}]);await lookup
  assert.equal(state.symbol.value,'sz000001');assert.equal(state.stock.value.symbol,'sz000001')
  state.dispose()
})
test('disposed funding page ignores late arrivals and cash charts retain negative and zero values',async()=>{
  let finish
  const state=createMainFlow({api:()=>new Promise(resolve=>finish=resolve)})
  const pending=state.select('sh600000');state.dispose();finish({symbol:'sh600000'});await pending
  assert.equal(state.stock.value,null)
  const chart=prototypeOptions({kind:'flow_rank',x:['合成甲','合成乙'],series:[{name:'净额',data:[-1,0]}]})
  assert.deepEqual(chart.series[0].data,[-1,0]);assert.equal(chart.tooltip.valueFormatter(-1),'-1.00亿')
})
