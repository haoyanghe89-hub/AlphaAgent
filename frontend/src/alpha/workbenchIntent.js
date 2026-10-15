// Route explicit horizons first; a bare stock uses the selected workbench mode.
export const researchModes = [
  { id:'next', label:'明日预测', placeholder:'贵州茅台明天涨还是跌？', hint:'输入任意沪深 A 股名称或代码，收盘后预测下一交易日涨跌' },
  { id:'medium', label:'未来三个月', placeholder:'宁德时代未来三个月怎么看？', hint:'经营、资金、行业与政策，综合给出方向和反证' },
  { id:'market', label:'市场观察', placeholder:'观察市场，哪个行业交易更活跃？', hint:'当前覆盖半导体设备与集成电路制造，不代表全市场排名' },
  { id:'candidates', label:'行业候选', placeholder:'半导体设备有哪些值得研究的公司？', hint:'当前支持半导体设备、集成电路制造，最多深研三家' },
]

export function workbenchIntent(text, selected) {
  const question = text.trim()
  const mode = /明日|明天|下一交易日|次日/.test(question) ? 'next'
    : /三个月|3\s*个月|中期/.test(question) ? 'medium'
      : /候选|哪些.*公司|推荐.*公司/.test(question) ? 'candidates'
        : /市场观察|市场行情|观察市场|行业热度/.test(question) ? 'market' : selected
  if (!mode) return null
  if (mode === 'market') return { mode, question }
  if (mode === 'candidates') {
    const entity = /集成电路制造|晶圆制造/.test(question) ? '集成电路制造'
      : /半导体设备/.test(question) ? '半导体设备' : ''
    if (!entity) throw new Error('请注明“半导体设备”或“集成电路制造”；其他行业尚未接入候选研究。')
    return { mode, entity, question }
  }
  const code = question.match(/(?<!\d)(\d{6})(?:\.(SH|SZ))?(?!\d)/i)
  const entity = code ? code[0].toUpperCase() : question
    .replace(/^(?:请|帮我|麻烦|看一下|看看|分析一下|分析|预测一下|预测|研究|全面分析)+\s*/g,'')
    .replace(/(?:下一交易日|(?:未来\s*)?(?:三|3)\s*个月(?:以后|后)?|明日|明天|次日|中期)(?:的)?(?:(?:预测|分析|研究)(?:一下)?)?/g,' ')
    .replace(/^\s*(?:请|帮我|预测一下|预测|分析一下|分析|研究一下|研究|看一下|看下|看看|判断一下|判断|股票)+\s*/g,'')
    .replace(/(?:未来|的)?(?:股价|走势|涨跌|是涨还是跌|看涨还是看跌|会涨|会跌|涨还是跌|涨不涨|怎么看|怎么样|怎么走|能涨|能不能|是否|会不会|会如何|表现|如何|情况).*$/,'')
    .replace(/[\s，。！？?：:]+$/,'').trim()
  if (!entity || /^(预测|股票|分析|研究|涨|跌)$/.test(entity)) throw new Error('请输入要研究的股票名称或六位代码，例如“贵州茅台明日预测”。')
  if (entity.length > 80) throw new Error('请用准确的股票名称或六位代码指定公司。')
  return { mode, entity, question }
}

export function decisionConditions(intent) {
  if (intent.mode === 'market') return { scope:'market', research_mode:'market_scan', entity:'设备与制造观察范围', question:intent.question }
  if (intent.mode === 'candidates') return { scope:'industry', research_mode:'candidates', entity:intent.entity, question:intent.question, horizon_months:3, budget_profile:{max_candidates:3} }
  return { entity:intent.entity, question:intent.question, horizon_months:3, research_mode:'medium_term', include_flows:true, include_industry:true, include_methods:true, budget_profile:{max_rounds:1,max_evidence_calls:1} }
}
