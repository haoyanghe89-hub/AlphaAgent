export function technicalCharts(stock) {
  const x=stock.points.map(p=>p.date)
  const line=(key,name)=>({name,data:stock.points.map(p=>p[key]==null?null:Number(p[key]))})
  return [
    {kind:'investment_macd',title:'MACD · 价格动量（元）',x,series:[line('macd_dif','DIF'),line('macd_dea','DEA'),{...line('macd_histogram','柱：DIF−DEA'),type:'bar'}]},
    {kind:'investment_rsi',title:'RSI14 · 相对强弱（0–100）',x,series:[line('rsi_14','RSI14')]},
    {kind:'investment_atr',title:'ATR14 · 日波动占收盘价（%）',x,series:[{name:'ATR14 / 收盘',data:stock.points.map(p=>p.atr_ratio==null?null:Number(p.atr_ratio)*100)}]},
  ]
}
