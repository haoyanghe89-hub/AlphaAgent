// Presentation contract adapted from the prototype's EChart, ChartBlock and StockPage.
// Values are supplied by the caller; this module does not compute financial results.
export const chartPalette = {
  accent: '#e8b046', cyan: '#4cc2ff', up: '#f04a5e', down: '#1fbf75',
  purple: '#8b7ff0', warn: '#f0a13a', muted: '#5c6675', secondary: '#8b95a5',
  foreground: '#d5dbe4', raised: '#161b25', border: '#1f2733', strong: '#2a3546', grid: '#1a212c',
}
const font = 'JetBrains Mono, Cascadia Mono, Consolas, monospace'
export function prototypeChartBase(palette = {}, reducedMotion = false) {
  const p = { ...chartPalette, ...palette }
  return {
    backgroundColor: 'transparent', animation: !reducedMotion,
    color: [p.accent, p.cyan, p.up, p.down, p.purple, p.warn],
    textStyle: { fontFamily: font, fontSize: 10.5, color: p.muted },
    grid: { left: 52, right: 16, top: 30, bottom: 26 },
    tooltip: {
      trigger: 'axis', renderMode: 'richText', confine: true,
      backgroundColor: p.raised, borderColor: p.strong,
      textStyle: { color: p.foreground, fontSize: 11.5, fontFamily: 'JetBrains Mono, Consolas, monospace' },
      axisPointer: { type: 'cross', label: { backgroundColor: p.border } },
      valueFormatter: value => typeof value === 'number' ? value.toFixed(2) : String(value ?? '—'),
    },
    legend: { top: 0, right: 0, textStyle: { color: p.secondary, fontSize: 11 }, itemWidth: 14, itemHeight: 8 },
  }
}
export function prototypeAxis(palette = {}) {
  const p = { ...chartPalette, ...palette }
  return {
    axisLine: { lineStyle: { color: p.border } },
    axisLabel: { color: p.muted, fontSize: 10 },
    splitLine: { lineStyle: { color: p.grid } },
  }
}
export function cssChartPalette(style) {
  const value = (token, fallback) => style.getPropertyValue(token).trim() || fallback
  return Object.fromEntries(Object.entries({ accent: '--accent', cyan: '--accent-cyan', up: '--up', down: '--down', warn: '--warn', muted: '--text-3', secondary: '--text-2', foreground: '--text-1', raised: '--bg-raised', border: '--border', strong: '--border-strong', grid: '--chart-grid' }).map(([name, token]) => [name, value(token, chartPalette[name])]))
}
export const plotValue = value => value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value)

export function prototypeOptions(block, { palette = {}, reducedMotion = false } = {}) {
  const p = { ...chartPalette, ...palette }, base = prototypeChartBase(p, reducedMotion), axis = prototypeAxis(p)
  const x = block.x || [], series = block.series || []
  if(block.kind==='portfolio_allocation')return {
    ...base,grid:{left:90,right:24,top:30,bottom:26},
    xAxis:{type:'value',min:0,max:100,...axis,axisLabel:{...axis.axisLabel,formatter:'{value}%'}},
    yAxis:{type:'category',inverse:true,data:x,...axis,splitLine:{show:false}},
    tooltip:{...base.tooltip,valueFormatter:v=>typeof v==='number'?v.toFixed(2)+'%':'—'},
    series:series.map((s,i)=>({name:s.name,type:'bar',data:s.data.map(plotValue),barMaxWidth:12,itemStyle:{color:i?p.accent:p.secondary}})),
  }
  if(block.kind==='portfolio_correlation')return {
    ...base,grid:{left:80,right:18,top:18,bottom:96},legend:{show:false},
    xAxis:{type:'category',data:x,...axis,axisLabel:{...axis.axisLabel,rotate:35}},
    yAxis:{type:'category',data:x,inverse:true,...axis,splitLine:{show:false}},
    visualMap:{min:-1,max:1,calculable:false,orient:'horizontal',left:'center',bottom:0,itemWidth:12,itemHeight:120,inRange:{color:[p.cyan,p.raised,p.accent]},text:['正相关','负相关'],textStyle:{color:p.secondary}},
    tooltip:{...base.tooltip,trigger:'item',formatter:params=>x[params.data[0]]+' / '+x[params.data[1]]+'：'+Number(params.data[2]).toFixed(2)},
    series:[{type:'heatmap',data:(block.matrix||[]).flatMap((row,i)=>row.flatMap((v,j)=>plotValue(v)==null?[]:[[j,i,plotValue(v)]])),label:{show:x.length<=5,formatter:params=>Number(params.data[2]).toFixed(2),fontSize:10,color:p.foreground},itemStyle:{borderWidth:1,borderColor:p.border},emphasis:{itemStyle:{borderWidth:2,borderColor:p.foreground}}}],
  }
  if (block.kind === 'sector_rank' || block.kind === 'flow_rank') return {
    ...base, grid: { left:76, right:30, top:26, bottom:24 },
    xAxis: { type:'value', ...axis, axisLabel:{...axis.axisLabel, formatter:block.kind==='flow_rank'?'{value}亿':'{value}%'} },
    yAxis: { type:'category', inverse:true, data:x, ...axis, splitLine:{show:false} },
    tooltip:{...base.tooltip, valueFormatter:v=>typeof v==='number'?v.toFixed(2)+(block.kind==='flow_rank'?'亿':'%'):'—'},
    series:series.map(s=>({name:s.name,type:'bar',data:s.data.map(plotValue),barMaxWidth:14,itemStyle:{color:params=>Number(params.value)>=0?p.up:p.down}})),
  }
  if (block.kind === 'nav') return {
    ...base, color: [p.accent, p.cyan, p.muted, p.warn],
    legend: { ...base.legend, data: ['策略净值', '沪深300', '策略(0成本)', '策略(0.3%成本)'] },
    tooltip: { ...base.tooltip, valueFormatter: v => typeof v === 'number' ? v.toFixed(3) : '—' },
    xAxis: { type: 'category', data: x, ...axis }, yAxis: { type: 'value', scale: true, ...axis },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 14, bottom: 0, borderColor: p.border }],
    series: ['策略净值', '沪深300', '策略(0成本)', '策略(0.3%成本)'].map((name, i) => ({
      name, type: 'line', data: (series[i]?.data || []).map(plotValue), symbol: 'none', connectNulls: false,
      lineStyle: { width: i === 0 ? 2 : i === 1 ? 1.5 : 1, ...(i > 1 ? { type: 'dashed' } : {}) }, ...(i > 1 ? { opacity: 0.5 } : {}),
    })),
  }
  if (block.kind === 'kline') return {
    ...base, animation: false, axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: { ...base.legend, data: ['MA20', 'MA60'] },
    grid: [{ left: 56, right: 16, top: 28, height: 190 }, { left: 56, right: 16, top: 238, height: 44 }],
    xAxis: [{ type: 'category', data: x, ...axis, boundaryGap: true, gridIndex: 0, axisLabel: { show: false } }, { type: 'category', data: x, ...axis, boundaryGap: true, gridIndex: 1 }],
    yAxis: [{ type: 'value', scale: true, gridIndex: 0, ...axis, splitArea: { show: false } }, { type: 'value', gridIndex: 1, ...axis, axisLabel: { show: false }, splitLine: { show: false } }],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 55, end: 100 }, { type: 'slider', xAxisIndex: [0, 1], bottom: 0, height: 14, borderColor: p.border, start: 55, end: 100 }],
    series: [
      { name: 'K线', type: 'candlestick', data: (block.ohlc || []).map(row => row?.length === 4 && row.every(v => plotValue(v) != null && Number(v) > 0) ? row.map(plotValue) : ['-','-','-','-']), itemStyle: { color: p.up, color0: p.down, borderColor: p.up, borderColor0: p.down } },
      { name: 'MA20', type: 'line', data: block.ma20 || [], symbol: 'none', lineStyle: { width: 1, color: p.accent } },
      { name: 'MA60', type: 'line', data: block.ma60 || [], symbol: 'none', lineStyle: { width: 1, color: p.cyan } },
      { name: '成交量(手)', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: block.volume || [], itemStyle: { color: p.strong } },
    ],
  }
  const hasPct = series.some(s => s.axis !== 0 && /同比|yoy|增速|%/i.test(s.name))
  return {
    ...base, grid: { ...base.grid, right: hasPct || block.kind === 'margin' ? 52 : 16 },
    xAxis: { type: 'category', data: x, ...axis, boundaryGap: block.chart_type === 'bar' },
    yAxis: [{ type: 'value', scale: true, ...axis, ...(block.kind === 'margin' ? { axisLabel: { ...axis.axisLabel, formatter: '{value}亿' } } : {}) }, ...(hasPct || block.kind === 'margin' ? [{ type: 'value', scale: true, ...axis, splitLine: { show: false }, axisLabel: { ...axis.axisLabel, ...(hasPct ? { formatter: '{value}%' } : {}) } }] : [])],
    ...(block.kind === 'pe_history' ? { dataZoom: [{ type: 'inside' }] } : {}),
    series: series.map(s => {
      const pct = /同比|yoy|增速|%/i.test(s.name)
      const marginBar = (block.kind === 'margin' && s.type === 'bar') || s.signed
      return { name: s.name, type: s.type || (block.chart_type === 'bar' && !pct ? 'bar' : 'line'), yAxisIndex: s.axis ?? (pct ? 1 : 0), data: (s.data || []).map(plotValue), smooth: block.chart_type === 'line' && block.kind !== 'pe_history', symbol: s.symbol || 'none', ...(s.symbol === 'circle' ? { symbolSize:4 } : {}), connectNulls: false, barMaxWidth: s.barMaxWidth || 22, lineStyle: { width: pct ? 1.5 : 2, ...s.lineStyle }, itemStyle: { ...(s.color ? { color: s.color } : {}), ...(s.opacity != null ? { opacity:s.opacity } : {}), ...(marginBar ? { color: params => Number(params.value) >= 0 ? p.up : p.down } : {}) }, ...(s.areaStyle ? { areaStyle: s.areaStyle } : {}) }
    }),
  }
}
