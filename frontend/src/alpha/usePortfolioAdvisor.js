import {createInvestmentPlan} from './useInvestmentPlan.js'

export const createPortfolioAdvisor=options=>createInvestmentPlan({...options,root:'/quant/portfolio-advisor/turns'})
export const portfolioIntent=(text,selected)=>/账户调仓|组合持仓|多只持仓|行业集中度|持仓相关性|整体(?:减仓|加仓|调仓)/.test(text)||(selected==='portfolio'&&!/明日|明天|下一交易日|三个月|3\s*个月|市场观察|行业候选/.test(text))
export function exposureChart(data,kind='stocks'){
  let labels,before,after
  if(kind==='industries'){
    labels=data.before.industries.map(r=>r.name)
    before=data.before.industries.map(r=>Number(r.weight)*100)
    after=data.before.industries.map(r=>Number(data.after.industries.find(v=>v.code===r.code)?.weight||0)*100)
  }else{
    labels=[...data.changes.map(r=>r.name),'现金']
    before=[...data.changes.map(r=>Number(r.before_weight)*100),100-Number(data.before.equity_weight)*100]
    after=[...data.changes.map(r=>Number(r.after_weight)*100),100-Number(data.after.equity_weight)*100]
  }
  return {kind:'portfolio_allocation',title:kind==='industries'?'行业占账户总资产':'持仓与现金占比',x:labels,series:[{name:'当前',data:before},{name:'全部条件执行后',data:after}]}
}
export function correlationChart(data){
  return {kind:'portfolio_correlation',title:'共同60日收益相关性',x:data.correlation.ids.map(id=>data.changes.find(r=>r.security_id===id)?.name||id),matrix:data.correlation.matrix}
}
