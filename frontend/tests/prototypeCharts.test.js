import test from 'node:test'
import assert from 'node:assert/strict'
import { chartOptions } from '../src/utils/quantChartOptions.js'
import { prototypeOptions } from '../src/utils/prototypeChartOptions.js'

test('prototype navigation plot retains four supplied cost scenarios and zoom without inventing gaps', () => {
  const option = prototypeOptions({kind:'nav',x:['a','b'],series:[{data:['1.001',null]},{data:['0.995','0.98']},{data:['1.002',null]},{data:['1',null]}]}, {reducedMotion:true})
  assert.deepEqual(option.color, ['#e8b046','#4cc2ff','#5c6675','#f0a13a'])
  assert.deepEqual(option.series.map(s => s.lineStyle.width), [2,1.5,1,1])
  assert.deepEqual(option.series[0].data, [1.001,null])
  assert.equal(option.dataZoom[1].height, 14)
  assert.equal(option.legend.right,0)
  assert.equal(option.textStyle.fontSize,10.5)
  assert.equal(option.tooltip.axisPointer.type,'cross')
  assert.equal(option.animation,false)
  assert.deepEqual(prototypeOptions({kind:'nav'}).series.map(s => s.data), [[],[],[],[]])
})
test('candlestick chart consumes supplied OHLC and moving averages; it never synthesizes them from a close', () => {
  const option = prototypeOptions({kind:'kline',x:['a'],ohlc:[[10,11,9,12]],ma20:[10.2],ma60:[10.4],volume:[100]})
  assert.deepEqual(option.series[0].data, [[10,11,9,12]])
  assert.deepEqual(option.series[1].data, [10.2])
  assert.equal(option.series[3].yAxisIndex,1)
  assert.equal(option.dataZoom[1].start,55)
  assert.equal(option.series[0].itemStyle.color0,'#1fbf75')
  assert.deepEqual(prototypeOptions({kind:'kline',close:[12]}).series[0].data,[])
  assert.deepEqual(prototypeOptions({kind:'kline',ohlc:[[null,null,null,null]]}).series[0].data,[['-','-','-','-']])
})
test('financial chart presents fractions as percentages and preserves missing or invalid values', () => {
  const option = chartOptions({kind:'financial',series:[{name:'chart_revenue_growth',points:[{x:'a',value:'0.125'},{x:'b',value:null},{x:'c',value:'bad'}]}]})
  assert.deepEqual(option.series[0].data,[12.5,null,null])
  assert.equal(option.yAxis[0].axisLabel.formatter,'{value}%')
  const valuation = chartOptions({kind:'valuation',series:[{name:'pe',points:[{x:'a',value:null},{x:'b',value:0}]}]})
  assert.deepEqual(valuation.series[0].data,[null,0])
})
