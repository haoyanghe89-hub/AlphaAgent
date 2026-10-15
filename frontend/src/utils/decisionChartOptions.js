// Only unit conversion and date alignment. Financial statistics come from sealed server output.
export function flowChartBlocks(report, window) {
  const x = window.sessions || []
  const aligned = (key, divisor = 1) => {
    const byDate = new Map((report.series[key] || []).map(p => [p.session, p.value]))
    return x.map(d => byDate.get(d) == null ? null : Number(byDate.get(d)) / divisor)
  }
  return [
    { kind: 'flow_balance', title: '融资余额（亿元）', chart_type: 'line', x, series: [{ name: '融资余额（亿元）', data: aligned('margin_balance', 1e8), symbol: 'circle' }] },
    { kind: 'flow_margin', title: '融资买入与偿还（亿元）', chart_type: 'bar', x, series: [{ name: '融资买入（亿元）', data: aligned('margin_buy', 1e8) }, { name: '融资偿还（亿元）', data: aligned('margin_repay', 1e8) }] },
    { kind: 'flow_price', title: '个股未复权收盘（元）', chart_type: 'line', x, series: [{ name: '未复权收盘（元）', data: aligned('security_close'), symbol: 'circle' }] },
    { kind: 'flow_vendor', title: '供应商估算大单净额（亿元）', chart_type: 'bar', x, series: [{ name: '供应商估算（亿元）', data: aligned('vendor_estimate', 1e8), signed: true }] },
  ]
}

export function flowNumber(value, unit) {
  if (value == null) return '缺项'
  const n = Number(value)
  if (!Number.isFinite(n)) return '缺项'
  if (unit === 'percentage_points') return (n * 100).toFixed(2) + ' 个百分点'
  if (unit === 'ratio') return (n * 100).toFixed(2) + '%'
  if (unit === 'CNY') return (n / 1e8).toFixed(2) + ' 亿元'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' 份'
}

export const divergenceLabel = {
  PRICE_UP_MARGIN_DOWN: '价格上涨，融资余额减少', PRICE_DOWN_MARGIN_UP: '价格下跌，融资余额增加',
  ALIGNED_UP: '价格与融资余额同向增加', ALIGNED_DOWN: '价格与融资余额同向减少',
  MIXED: '价格与融资变化没有一致方向', UNAVAILABLE: '缺少完整窗口，暂不比较背离',
}

const numeric = (v, divisor=1) => v==null || v==='' || !Number.isFinite(Number(v)) ? null : Number(v)/divisor
export function nextSessionChartBlocks(data) {
  const prices=data?.daily_prices || []
  return [
    {kind:'prediction_close',title:'近二十一个交易日 · 未复权收盘（元）',chart_type:'line',x:prices.map(p=>p.session),series:[{name:'实际收盘（元）',data:prices.map(p=>numeric(p.close)),symbol:'circle'}]},
    {kind:'prediction_turnover',title:'同期成交额（亿元）',chart_type:'bar',x:prices.map(p=>p.session),series:[{name:'成交额（亿元）',data:prices.map(p=>numeric(p.turnover,1e8))}]},
  ]
}
export function accuracyChartBlock(group) {
  const stats=group.statistics
  return {kind:'prediction_accuracy',title:'同样本准确率对照（%）',chart_type:'bar',x:['模型判断','始终看涨','始终看跌','延续前日'],series:[{name:'准确率（%）',type:'bar',axis:0,data:[stats.accuracy,...['ALWAYS_UP','ALWAYS_DOWN','PERSISTENCE'].map(k=>stats.baselines[k])].map(v=>numeric(v?.value,0.01))}]}
}
export function marketChartBlocks(rows) {
  const x=rows.map(r=>r.name)
  return [
    {kind:'market_share',title:'本次行业范围 · 成交占比（%）',chart_type:'bar',x,series:[{name:'二十日平均成交占比（%）',type:'bar',axis:0,data:rows.map(r=>numeric(r.mean_20_share,0.01))}]},
    {kind:'market_breadth',title:'最近一日 · 上涨比例与成交集中度（%）',chart_type:'bar',x,series:[{name:'上涨成员（%）',type:'bar',axis:0,data:rows.map(r=>numeric(r.breadth,0.01))},{name:'前三只成交贡献（%）',type:'bar',axis:0,data:rows.map(r=>numeric(r.top3_turnover_share,0.01))}]},
  ]
}
export function candidateChartBlocks(members) {
  const x=members.map(m=>m.name)
  return [{kind:'candidate_financial',title:'同期间经营规模对照（亿元）',chart_type:'bar',x,series:[['营业收入','TOTAL_OPERATE_INCOME'],['归母净利润','PARENT_NETPROFIT'],['经营现金流','NETCASH_OPERATE']].map(([name,key])=>({name,type:'bar',data:members.map(m=>numeric(m.metrics[key],1e8))}))}]
}
