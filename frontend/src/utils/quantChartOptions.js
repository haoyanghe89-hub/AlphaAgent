import { prototypeAxis, prototypeChartBase, plotValue } from './prototypeChartOptions.js'
const names = { chart_price_raw: '未复权价格', chart_price_qfq: '前复权价格', chart_price_hfq: '后复权价格', chart_drawdown_raw: '回撤', chart_drawdown_qfq: '回撤', chart_drawdown_hfq: '回撤', pe: '独立 PE', pb: '独立 PB', disclosed_pe_ttm: '披露 PE', disclosed_pb_mrq: '披露 PB', chart_parent_net_profit_growth: '归母净利润同比%', chart_revenue_growth: '营业收入同比%' }
export const seriesLabel = name => names[name] || name
// Only presentation conversion for ECharts; no returns, ratios or valuation formulas here.
export function chartOptions(exhibit, { adjustment = 'raw', reducedMotion = false, palette = {} } = {}) {
  const selected = exhibit.series.filter(s => exhibit.kind !== 'price' || s.name.endsWith('_' + adjustment))
  const categories = [...new Set(selected.flatMap(s => s.points.map(p => p.x)))].sort()
  const valuation = exhibit.kind === 'valuation'
  const values = valuation ? selected.flatMap(s => s.points.map((p,i) => ({ label: `${seriesLabel(s.name)} · ${p.label?.split(' · ')[1] || i+1}`, value: p.value }))) : []
  const base = prototypeChartBase(palette, reducedMotion), axis = prototypeAxis(palette)
  const percentage = exhibit.kind === 'financial'
  return {
    ...base, legend: { ...base.legend, show: !valuation },
    grid: { ...base.grid, right: exhibit.kind === 'price' ? 52 : 16, ...(valuation ? { bottom: 90 } : {}) },
    xAxis: { type: 'category', data: valuation ? values.map(v => v.label) : categories, ...axis, boundaryGap: valuation, axisLabel: { ...axis.axisLabel, rotate: valuation ? 35 : 0 } },
    yAxis: [{ type: 'value', scale: true, ...axis, axisLabel: { ...axis.axisLabel, ...(percentage ? { formatter: '{value}%' } : {}) } }, ...(exhibit.kind === 'price' ? [{ type: 'value', scale: true, ...axis, axisLabel: { ...axis.axisLabel, formatter: '{value}%' }, splitLine: { show: false } }] : [])],
    series: valuation ? [{ type: 'bar', data: values.map(v => plotValue(v.value)), barMaxWidth: 22 }] : selected.map(s => ({ name: seriesLabel(s.name), type: 'line', yAxisIndex: s.name.startsWith('chart_drawdown') ? 1 : 0,
      connectNulls: false, symbol: 'none', smooth: percentage, lineStyle: { width: percentage || s.name.startsWith('chart_drawdown') ? 1.5 : 2 },
      data: categories.map(x => {
        const value = plotValue(s.points.find(p => p.x === x)?.value)
        return value == null ? null : percentage || s.name.startsWith('chart_drawdown') ? value * 100 : value
      }) })),
  }
}
