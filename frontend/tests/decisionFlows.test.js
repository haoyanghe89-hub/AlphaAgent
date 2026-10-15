import test from 'node:test'
import assert from 'node:assert/strict'
import {flowChartBlocks,flowNumber} from '../src/utils/decisionChartOptions.js'
test('flow charts align exact fixed dates, convert units and retain gaps',()=>{
  const report={series:{margin_balance:[{session:'2026-10-09',value:'120000000'},{session:'2026-10-12',value:'100000000'}],security_close:[{session:'2026-10-09',value:'300.5'}]}}
  const blocks=flowChartBlocks(report,{sessions:['2026-10-09','2026-10-10','2026-10-12']})
  assert.deepEqual(blocks[0].series[0].data,[1.2,null,1])
  assert.deepEqual(blocks[2].series[0].data,[300.5,null,null])
  assert.deepEqual(blocks[3].series[0].data,[null,null,null])
  assert.equal(blocks[0].series[0].name,'融资余额（亿元）')
})
test('zero is distinct from missing, ratios are observations not probabilities',()=>{
  assert.equal(flowNumber(null,'CNY'),'缺项')
  assert.equal(flowNumber('0','CNY'),'0.00 亿元')
  assert.equal(flowNumber('-.03','ratio'),'-3.00%')
  assert.equal(flowNumber('-.015','percentage_points'),'-1.50 个百分点')
  assert.equal(flowNumber('NaN','CNY'),'缺项')
})
