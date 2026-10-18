import test from 'node:test'
import assert from 'node:assert/strict'
import {init, use} from 'echarts/core'
import {SVGRenderer} from 'echarts/renderers'
import '../src/utils/quantEcharts.js'
import {prototypeOptions} from '../src/utils/prototypeChartOptions.js'

test('registered chart runtime renders correlation cells and color scale',()=>{
  use([SVGRenderer])
  const chart=init(null,null,{renderer:'svg',ssr:true,width:500,height:350})
  try{
    chart.setOption(prototypeOptions({kind:'portfolio_correlation',x:['A','B'],matrix:[[1,.25],[.25,1]]},{reducedMotion:true}))
    const svg=chart.renderToSVGString()
    assert.match(svg,/>0\.25<\/text>/)
    assert.match(svg,/>正相关<\/text>/)
    assert.match(svg,/>负相关<\/text>/)
  }finally{chart.dispose()}
})
