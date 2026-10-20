import { createInvestmentPlan } from './useInvestmentPlan.js'
import { advisorResearchRequest } from './advisorResearchRequest.js'

export function createHoldingAdvisor(options) {
  const ctl = createInvestmentPlan({ ...options, root:'/quant/holding-advisor/turns' })
  return {...ctl, submit:body=>ctl.submit(advisorResearchRequest(body, ctl.state.value?.request))}
}

export function holdingIntent(text, selected) {
  const explicit=/持仓顾问|持仓咨询|成本价|我(?:持有|买了|有).{0,30}股|到多少.*卖|止盈|止损|减仓|补仓|加仓|该不该卖|什么时候卖|能卖吗/.test(text)
  if (!explicit && (selected !== 'holding' || /明日|明天|下一交易日|三个月|3\s*个月|市场观察|行业候选/.test(text))) return null
  const code = text.match(/(?<!\d)\d{6}(?:\.(?:SH|SZ))?(?!\d)/i)
  const entity = code?.[0].toUpperCase() || text.trim()
    .replace(/^(?:(?:请|帮我|咨询|分析一下|分析|持仓顾问|持仓咨询|我持有|我买了|我有|持有)\s*)+/,'')
    .split(/的?(?:成本|持仓|仓位|股数|现在|目前|在这个|到多少|该不该|什么时候|怎么|止盈|止损|卖|减仓|补仓|加仓|[，,。？?：:\s])/)[0]
  return { entity:entity.length <= 30 ? entity : '', question:text }
}

export function advisorChart(data) {
  const rows = data.chart.slice(-60).map(p=>({date:p.date,price:p.close}))
  const day = data.quote.quoted_at.slice(0,10).replaceAll('-','')
  if(data.price_comparable===false)rows.splice(0,rows.length,{date:day+' 报价',price:data.quote.price})
  if (day > rows.at(-1)?.date) rows.push({date:day+' 报价',price:data.quote.price})
  const series = [{name:'复权收盘 / 本轮报价',data:rows.map(p=>Number(p.price))},
    {name:'持仓成本',data:rows.map(()=>Number(data.facts.cost_price)),lineStyle:{type:'dashed'}}]
  for(const gate of data.gates.filter(g=>['DEFENSE','UPSIDE'].includes(g.kind))) series.push({name:gate.title,data:rows.map(()=>Number(gate.price)),lineStyle:{type:'dashed'}})
  return {kind:'investment_price',title:'价格位置与持仓参考（元）',x:rows.map(p=>p.date),series}
}
