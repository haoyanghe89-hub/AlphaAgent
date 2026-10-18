import {describe,it} from 'node:test'
import assert from 'node:assert/strict'
import {technicalCharts} from '../src/alpha/useInvestmentTechnicals.js'
describe('sealed technical charts',()=>{
  it('keeps missing points and fixed histogram multiplier, converts only ATR ratio to percent',()=>{
    const charts=technicalCharts({points:[{date:'a',macd_dif:null,macd_dea:null,macd_histogram:null,rsi_14:null,atr_ratio:null},{date:'b',macd_dif:'2',macd_dea:'1',macd_histogram:'1',rsi_14:'65',atr_ratio:'0.02'}]})
    assert.deepEqual(charts[0].series[2].data,[null,1])
    assert.deepEqual(charts[1].series[0].data,[null,65])
    assert.deepEqual(charts[2].series[0].data,[null,2])
  })
})
