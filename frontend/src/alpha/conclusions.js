import { number } from './model.js'

const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))
const text = value => ({ text: value })

// A presentation of this report's actual inputs, never today's quote or a new
// financial calculation. The original analysis and evidence remain unchanged.
export function conclusionCards(data = {}) {
  const metric = name => data.metrics?.find(m => m.name === name && finite(m.value))
  const evidenceAt = locator => data.evidence?.find(e => e.locator === locator)?.id
  const value = (label, amount, unit, locator, source) => ({
    text: `${number(amount)}${unit}`, label,
    evidence: evidenceAt(locator), source,
  })
  const metricValue = (m, label = m.name) => value(label, m.value, m.unit, `metrics/${m.name}`, m.source)
  const revenue = metric('营收累计同比'), profit = metric('归母净利累计同比')
  const pe = metric('自算 PE-TTM'), pb = metric('供应商 PB'), goodwill = metric('商誉 / 归母权益')
  const growth = []
  const period = data.data_dates?.financial
  if (/^\d{8}$/.test(period || '')) {
    const month = period.slice(4, 6)
    const label = { '03':'一季度', '06':'上半年', '09':'前三季度', '12':'全年' }[month]
    growth.push(text(`${period.slice(0, 4)} 年${label || `截至 ${month} 月`}，`))
  }
  if (revenue) growth.push(text('营收同比 '), metricValue(revenue))
  if (profit) growth.push(text(revenue ? '，归母净利同比 ' : '归母净利同比 '), metricValue(profit))
  if (revenue && profit) {
    const comparison = Number(profit.value) < Number(revenue.value) ? '低于' : Number(profit.value) > Number(revenue.value) ? '高于' : '持平于'
    growth.push(text(`；利润增速${comparison}营收增速。`))
  } else if (!revenue && !profit) {
    growth.push(text('同比数据尚不齐备，暂不判断增长趋势。'))
  } else growth.push(text('；另一项同比数据尚缺。'))

  const valuation = []
  if (pe) valuation.push(text('自算 PE-TTM '), metricValue(pe))
  if (pb) valuation.push(text(pe ? '，供应商 PB ' : '供应商 PB '), metricValue(pb))
  if (finite(data.dcf?.per_share)) {
    if (valuation.length) valuation.push(text('；'))
    valuation.push(text('DCF 情景 '), value('DCF 条件情景估算', data.dcf.per_share, '元/股', 'dcf/per_share', 'cash_em'), text('（非目标价）。'))
  } else valuation.push(text((valuation.length ? '；' : '') + (data.dcf?.status === 'NOT_APPLICABLE' ? '工业 DCF 不适用。' : 'DCF 输入未齐备。')))

  const technical = []
  const ma20 = data.kline?.ma20?.at(-1), ma60 = data.kline?.ma60?.at(-1)
  if (finite(ma20) && finite(ma60)) {
    technical.push(text(`MA20 ${Number(ma20) > Number(ma60) ? '位于 MA60 上方' : Number(ma20) < Number(ma60) ? '位于 MA60 下方' : '与 MA60 持平'}；`))
  } else technical.push(text('均线数据不足；'))
  const close = data.kline?.ohlc?.at(-1)?.[1]
  if (finite(close)) technical.push(text('日线收盘 '), value('前复权日线收盘', close, '元', 'kline/close', 'prices'), text('；'))
  const netSeries = data.margin?.series?.find(s => s.name === '融资净买入(亿)')
  const net = netSeries?.data?.at(-1)
  if (data.margin?.status === 'AVAILABLE' && finite(net)) {
    technical.push(text(`${data.data_dates?.margin || '最近一日'} 融资净买入 `), value('日终融资净买入', net, '亿元', 'margin/net_buy', 'margin'), text('。'))
  } else technical.push(text('日终融资数据未取得。'))

  const risks = []
  if (goodwill) risks.push(text('商誉 / 归母权益 '), metricValue(goodwill), text('；'))
  const topics = ['股权质押', '限售股解禁'].filter(title => data.risks?.some(r => r.title === title))
  risks.push(text(topics.length ? `${topics.map(t => t === '股权质押' ? '质押' : '解禁').join('、')}记录需核实当前状态，` : '风险资料尚需补证，'))
  risks.push(text('不能据现有资料认定无风险。'))
  return [
    { key:'growth', title:'成长性', badge:'累计口径', tone:'gold', parts:growth },
    { key:'valuation', title:'估值', badge:'口径待核验', tone:'gold', parts:valuation },
    { key:'technical', title:'技术与资金', badge:'日线 / 日终', tone:'cyan', parts:technical },
    { key:'risk', title:'风险', badge:'核验待补', tone:'gold', parts:risks },
  ]
}

// Judgments and calculations come from the versioned Python read model.
export function interpretiveCards(data, reading) {
  const cards = conclusionCards(data)
  if (!reading) return cards
  const derived = (label, amount, unit, proof) => ({text:`${number(amount)}${unit}`,label,proof})
  const p = reading.price, v = reading.valuation
  if (reading.growth.status === 'AVAILABLE') cards[0].parts = cards[0].parts.slice(0,-1)
  cards[0] = {...cards[0],badge:reading.growth.judgment,
    meaning:reading.growth.meaning,watch:'下一期利润增速是否改善，现金流能否同步支持。'}
  cards[1] = {...cards[1],badge:v.judgment,meaning:v.meaning,
    watch:'补齐同行比较，并检验现金流增长假设。'}
  if (v.status === 'AVAILABLE') {
    const pe = cards[1].parts.find(part => part.label === '自算 PE-TTM')
    cards[1].parts = [text('自算 PE-TTM '),pe,text('；在本次历史样本的分位为 '),
      derived('历史 PE 样本分位',v.percentile,'%',v.proof),text('。')]
    if (reading.dcf.status === 'NOT_APPLICABLE') cards[1].meaning += ' 工业 DCF 不适用。'
  }
  const technical = []
  if (['AVAILABLE','PARTIAL'].includes(p.status)) {
    technical.push(text(p.status === 'AVAILABLE' ? '近 52 周收盘区间 ' : '现有收盘样本区间 '),
      derived('样本最低收盘价',p.low,'',p.proof),text('～'),
      derived('样本最高收盘价',p.high,'元',p.proof),text('，本期收盘 '),
      derived('本期收盘价',p.close,'元',p.proof),text('。'))
    if (finite(p.from_high)) technical.push(text('距最高收盘回落 '),
      derived('距区间最高收盘的回落幅度',Math.abs(p.from_high),'%',p.proof),text('。'))
  } else technical.push(text(p.reason + '，暂不判断价格高低。'))
  cards[2] = {...cards[2],badge:`${p.position} · ${p.trend}`,parts:technical,
    meaning:p.status === 'AVAILABLE' ? (p.trend_meaning || '') + '价格位置不代表估值贵便宜。' : '一年数据不足时，不把局部样本称为全年高位或低位。',
    watch:'价格与均线关系是否持续改善；单次突破不足以确认反转。'}
  cards[3] = {...cards[3],badge:reading.risk.judgment,
    watch:'核实质押、解禁等记录的当前状态，以及现金流和减值变化。'}
  return cards
}

// Collapse a repeated fact label only when it matches the known evidence text.
// No arbitrary sentence clipping: qualifiers in the original claim are kept.
export function formattedClaim(claim, data) {
  let result = claim.text || ''
  for (const id of claim.evidence_ids || []) {
    const evidence = data.evidence?.find(e => e.id === id)
    const m = data.metrics?.find(m => evidence?.locator === `metrics/${m.name}`)
    if (!m || !evidence?.text || !finite(m.value)) continue
    const index = result.indexOf(evidence.text)
    if (index < 0) continue
    const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const labelPattern = [...m.name.replace(/\s/g, '')].map(escape).join('\\s*')
    const before = result.slice(0, index).replace(new RegExp(`${labelPattern}\\s*(?:为|是)?\\s*$`), '')
    result = before + `${m.name}：${number(m.value)} ${m.unit}` + result.slice(index + evidence.text.length)
  }
  return result
}
