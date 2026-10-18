export const policyState=p=>({NOT_YET_EFFECTIVE:'尚未生效',EXPIRED:'已超出声明适用期',REPEALED:'已废止',CONSULTATION:'征求意见',WITHIN_DECLARED_WINDOW:'在声明适用期内'})[p.temporal_status]||'现行状态待核'
export function fundingChart(view,minute=false){
  const points=view?.points||[]
  const clock=p=>Number(p.at.slice(11,13))*60+Number(p.at.slice(14,16))
  let x,data
  if(minute&&points.length){
    const byTime=new Map(points.map(p=>[clock(p),p.main]));const times=[]
    for(let t=clock(points[0]);t<=clock(points.at(-1));t++)if((t>=570&&t<=690)||(t>=780&&t<=900))times.push(t)
    x=times.map(t=>String(Math.floor(t/60)).padStart(2,'0')+':'+String(t%60).padStart(2,'0'));data=times.map(t=>byTime.get(t))
  }else{x=view?.expected_sessions||points.map(p=>p.date);const byDate=new Map(points.map(p=>[p.date,p.main]));data=x.map(d=>byDate.get(d))}
  return {kind:minute?'investment_minute_flow':'investment_daily_flow',chart_type:minute?'line':'bar',title:minute?'供应商当日累计主力代理（亿元）':'日频大单与特大单净额（亿元）',x,series:[{name:minute?'腾讯分钟估算（亿）':'Tushare日频代理（亿）',type:minute?'line':'bar',signed:!minute,data:data.map(v=>v==null?null:Number(v)/1e8)}]}
}
