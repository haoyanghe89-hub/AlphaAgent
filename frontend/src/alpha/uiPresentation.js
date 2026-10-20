// Preserve the selected report and horizon when switching presentation modes.
export function developerHref(value, enabled=true, page) {
  const url=new URL(value,'http://localhost')
  if(enabled)url.searchParams.set('developer','1')
  else url.searchParams.delete('developer')
  if(page)url.hash=page==='workbench'?'#/':'#/'+page
  return url.pathname+url.search+url.hash
}

export function investmentIntent(text,selected) {
  if(/投资计划|仓位安排|资金安排/.test(text))return true
  return selected==='investment'&&!/明日|明天|下一交易日|三个月|3\s*个月|市场观察|行业候选|持仓|成本价|减仓|补仓|加仓|卖|止盈|止损|调仓|集中度|相关性|回测|选股|对比|比较|\bvs\b/i.test(text)
}

export function userHistoryItem(item) {
  return item.scenario!=='backtest'&&item.scenario!=='research_evaluation'&&!item.title?.includes('定向取证')
}
