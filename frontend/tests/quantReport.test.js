import test from 'node:test'
import assert from 'node:assert/strict'
import { chartOptions } from '../src/utils/quantChartOptions.js'
test('chart presentation uses supplied cells, preserves gaps and respects reduced motion', () => {
  const exhibit = {kind:'price',series:[{name:'chart_price_raw',points:[{x:'a',value:'10.123456789'},{x:'b',value:null}]},{name:'chart_drawdown_raw',points:[{x:'a',value:'-0.3'}]},{name:'chart_price_qfq',points:[{x:'a',value:'999'}]}]}
  const option = chartOptions(exhibit,{reducedMotion:true})
  assert.equal(option.animation,false)
  assert.equal(option.tooltip.renderMode,'richText')
  assert.equal(option.series.length,2)
  assert.deepEqual(option.series[0].data,[10.123456789,null])
  assert.equal(option.series[1].yAxisIndex,1)
  assert.deepEqual(option.series[1].data, [-30, null])
  assert.equal(option.series[0].connectNulls,false)
})
test('supplier disclosure never becomes independent PE or PB', () => {
  const option = chartOptions({kind:'valuation',series:[{name:'pe',points:[{x:'a',value:'3'}]},{name:'disclosed_pe_ttm',points:[{x:'a',value:'9'}]}]})
  assert.deepEqual(option.series[0].data,[3,9])
  assert.match(option.xAxis.data[0],/独立/)
  assert.match(option.xAxis.data[1],/披露/)
})
