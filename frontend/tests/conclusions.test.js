import assert from 'node:assert/strict'
import test from 'node:test'
import { conclusionCards, formattedClaim, interpretiveCards } from '../src/alpha/conclusions.js'

function report() {
  const metrics = [
    {name:'营收累计同比',value:3.456122,unit:'%',source:'lrb'},
    {name:'归母净利累计同比',value:1.661998,unit:'%',source:'lrb'},
    {name:'自算 PE-TTM',value:14.177769,unit:'倍',source:'lrb / valuation'},
    {name:'供应商 PB',value:2.97088,unit:'倍',source:'valuation'},
    {name:'商誉 / 归母权益',value:14.834381,unit:'%',source:'fzb'},
  ]
  return {metrics,data_dates:{financial:'20260630',margin:'2026-10-08'},
    evidence:metrics.map((m,i) => ({id:`F${i}`,locator:`metrics/${m.name}`,text:`${m.name}：${m.value} ${m.unit}`})),
    dcf:{per_share:129.825006},quote:{price:900},
    kline:{ohlc:[[80,82.46,79,83]],ma20:[84],ma60:[86]},
    margin:{status:'AVAILABLE',series:[{name:'融资净买入(亿)',data:[-0.10208]}]},
    risks:[{title:'股权质押'},{title:'限售股解禁'}],
  }
}
const content = card => card.parts.map(p => p.text).join('')

test('four concise themes format actual metrics and retain cumulative period and scenario meaning', () => {
  const data=report(), before=structuredClone(data), cards=conclusionCards(data)
  assert.deepEqual(cards.map(c => c.title),['成长性','估值','技术与资金','风险'])
  assert.match(content(cards[0]),/上半年.*营收同比 3\.46%.*净利同比 1\.66%.*利润增速低于/)
  assert.match(content(cards[1]),/14\.18倍.*2\.97倍.*129\.83元\/股（非目标价）/)
  assert.ok(cards.every(c => content(c).length<130))
  assert.ok(cards.every(c => !c.badge.includes('置信度')))
  assert.deepEqual(data,before)
})

test('highlights resolve to the precise existing evidence or original source', () => {
  const cards=conclusionCards(report())
  assert.equal(cards[0].parts.find(p => p.text==='3.46%').evidence,'F0')
  assert.equal(cards[1].parts.find(p => p.text==='14.18倍').evidence,'F2')
  assert.equal(cards[2].parts.find(p => p.text==='82.46元').source,'prices')
  assert.equal(cards[2].parts.find(p => p.text==='-0.10亿元').source,'margin')
  assert.match(content(cards[2]),/2026-10-08/)
  assert.ok(!content(cards[2]).includes('900')) // Fresh quote cannot replace archived daily price.
})

test('missing or inapplicable inputs stay explicit and never become zero or a rating', () => {
  const cards=conclusionCards({dcf:{status:'NOT_APPLICABLE'}})
  assert.match(content(cards[0]),/同比数据尚不齐备/)
  assert.match(content(cards[1]),/工业 DCF 不适用/)
  assert.match(content(cards[2]),/均线数据不足.*融资数据未取得/)
  assert.ok(cards.every(c => !content(c).includes('0.00')))
  assert.ok(!content(cards[3]).includes('无风险。') || content(cards[3]).includes('不能'))
})

test('genuine zero is highlighted; missing latest margin is not replaced by an older value', () => {
  const d=report();d.metrics[0].value=0;d.metrics[1].value=-2
  assert.match(content(conclusionCards(d)[0]),/同比 0\.00%.*同比 -2\.00%/)
  d.margin.series[0].data=[9,null]
  assert.match(content(conclusionCards(d)[2]),/融资数据未取得/)
  d.metrics[0].value=null
  assert.match(content(conclusionCards(d)[0]),/另一项同比数据尚缺/)
})

test('full analysis removes only known duplicated labels and preserves qualifications and original text', () => {
  const d=report(), e=d.evidence[2]
  const claim={text:`估值口径：自算 PE-TTM 为 ${e.text}；不能据此认定低估。`,evidence_ids:[e.id]}
  const before=structuredClone(claim)
  assert.equal(formattedClaim(claim,d),'估值口径：自算 PE-TTM：14.18 倍；不能据此认定低估。')
  assert.deepEqual(claim,before)
  const unrelated={text:'公告原文：9,940,207,657 元；不含交易费用。',evidence_ids:['J0']}
  assert.equal(formattedClaim(unrelated,d),unrelated.text)
})

test('reader cards present server judgments and bind calculated values without using live quotes', () => {
  const data=report(), reading={
    growth:{judgment:'收入、利润均增长',meaning:'利润增速低于收入，原因待分析。'},
    price:{status:'AVAILABLE',position:'区间偏上',trend:'近期偏弱',low:67.6,high:88.95,close:82.46,ma20:84.123,ma60:84.587333,
      proof:{title:'价格位置',text:'实际样本与公式',source:['prices'],locator:'reader/price'}},
    valuation:{status:'AVAILABLE',judgment:'历史样本偏低，低估待确认',meaning:'缺同行比较。',percentile:12.345,
      proof:{source:['valuation'],text:'样本不是严格 PIT'}},
    dcf:{status:'NOT_APPLICABLE'},risk:{judgment:'风险资料待补证'},
  }
  const before=structuredClone({data,reading}), cards=interpretiveCards(data,reading)
  assert.match(cards[2].badge,/区间偏上.*近期偏弱/)
  assert.match(content(cards[2]),/近 52 周.*67\.60～88\.95元.*82\.46元/)
  assert.match(content(cards[1]),/自算 PE-TTM 14\.18倍.*12\.35%/)
  assert.match(cards[1].meaning,/工业 DCF 不适用/)
  assert.equal(cards[2].parts.find(p => p.label==='本期收盘价').proof,reading.price.proof)
  assert.ok(cards.every(c => c.watch))
  assert.ok(!content(cards[2]).includes('900'))
  assert.deepEqual({data,reading},before)
  assert.deepEqual(interpretiveCards(data,null),conclusionCards(data))
})

test('partial price window is explicitly a local sample, never a year position', () => {
  const cards=interpretiveCards(report(),{growth:{},valuation:{},dcf:{},risk:{},
    price:{status:'PARTIAL',position:'一年位置待判断',trend:'近期走势待判断',low:10,high:20,close:15,proof:{}}})
  assert.match(content(cards[2]),/现有收盘样本区间/)
  assert.ok(!content(cards[2]).includes('近 52 周'))
})
