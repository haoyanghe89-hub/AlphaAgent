export const metricNames = { return: '区间价格收益', cagr: '年化价格收益', volatility: '年化波动率', drawdown: '最大回撤', pe: 'TTM 市盈率', pb: '市净率', roe: '平均权益 ROE', net_profit_growth: '净利润同比', revenue_growth: '收入同比', sharpe: '夏普比率', beta: 'Beta', disclosed_pe_ttm: '供应商披露 PE', disclosed_pb_mrq: '供应商披露 PB', disclosed_roe: '供应商披露 ROE' }
Object.assign(metricNames, { dcf_per_share:'DCF 每股条件情景估算', dcf_fcff:'基准自由现金流 FCFF', dcf_adjusted_ebit:'调整后的税前经营利润', dcf_effective_tax:'年报有效所得税率', dcf_delta_ncwc:'非现金营运资本变动代理', dcf_enterprise:'DCF 企业情景价值', dcf_equity:'DCF 股权情景价值', dcf_cash:'现金及等价物', dcf_debt:'有息债务账面值', dcf_minority:'少数权益账面值', dcf_shares:'发行股数减回购专户股数', dcf_terminal:'第10年末永续终值', dcf_terminal_pv:'永续终值折现', dcf_reference_price:'冻结参考收盘价', dcf_reference_difference:'相对参考价差异（非预期收益）', chart_dcf_sensitivity:'DCF 敏感性情景' })
Object.assign(metricNames, Object.fromEntries(Object.entries({ profit_before_tax:'利润总额', income_tax:'所得税费用', interest_expense:'财务利息费用', interest_income:'财务利息收入', investment_income:'投资收益', fair_value_gain:'公允价值变动损益', nonoperating_income:'营业外收入', nonoperating_expense:'营业外支出', depreciation:'折旧与摊销', capex:'购建长期资产支出', inventory_decrease:'存货现金调节减少额', receivables_increase:'经营性应收现金调节增加额', payables_increase:'经营性应付现金调节增加额', cash:'年末现金及等价物', short_debt:'短期借款', long_debt:'长期借款', bonds:'应付债券', leases:'非流动租赁负债', current_debt:'一年内到期的非流动负债', minority_equity:'少数股东权益', issued_shares:'年末发行股数', treasury_shares:'公司回购专户股数' }).map(([key,label]) => ['dcf_input_' + key, '年报输入 · ' + label])))
export const runLabels = { CREATED: '待执行', RUNNING: '执行中', VERIFYING: '核验中', COMPLETED: '已成稿', READY_FOR_REPORT: '正在成稿', BLOCKED: '受阻', FAILED: '失败', CANCELLED: '已取消', INTERRUPTED: '已中断', TIMED_OUT: '超时' }
export const isActive = status => ['CREATED', 'PENDING', 'RUNNING', 'WAITING_FOR_EXECUTION', 'VERIFYING', 'READY_FOR_REPORT', 'BLOCKED'].includes(status)
export const statusLabel = status => runLabels[status] || status || '未记录'
export const isHistoryDone = item => item.scenario === 'backtest' ? item.check_status === 'COMPLETED' : item.status === 'COMPLETED'
export const isHistoryActive = item => isActive(item.scenario === 'backtest' ? item.check_status || item.status : item.status)
export const historyStatus = item => item.scenario === 'backtest'
  ? item.check_status === 'INTEGRITY_FAILED' ? '检查完整性失败'
    : (item.check_status === 'COMPLETED' ? '检查完成' : '检查' + statusLabel(item.check_status || item.status)) + (item.execution_status === 'BLOCKED' ? ' · 回测受阻' : '')
  : item.scenario === 'strategy' && item.status === 'COMPLETED'
    ? item.execution_status === 'BLOCKED' ? '检查完成 · 回测受阻' : '计算完成 · 待独立核验'
    : statusLabel(item.status)

export function workbenchExamples(stocks = []) {
  if (!stocks.length) return []
  const first = stocks.find(s => s.code === '000333') || stocks[0]
  const second = stocks.find(s => s.code === '600519' && s !== first) || stocks.find(s => s !== first)
  return [
    '全面分析' + first.name,
    ...(second ? [first.name + ' vs ' + second.name + '，比较成长性、盈利能力和估值'] : []),
    '查看' + first.name + '的DCF估值',
    '分析' + (second || first).name + '的历史收益与风险',
  ]
}
export const number = (value, digits = 2) => value == null || value === '' || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
export function metricValue(cell) {
  if (cell?.value == null || cell.value === '' || !Number.isFinite(Number(cell.value))) return '—'
  const percent = ['fraction', 'fraction/year', 'fraction/sqrt(year)'].includes(cell.unit)
  return number(percent ? Number(cell.value) * 100 : cell.value) + (percent ? '%' : ['pe','pb','disclosed_pe_ttm','disclosed_pb_mrq'].includes(cell.name) ? ' 倍' : '')
}
export const dateTime = value => value ? new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : value + 'Z').toLocaleString('zh-CN', { hour12: false }) : '未记录'
export const movement = value => value == null ? 'flat' : Number(value) > 0 ? 'up' : Number(value) < 0 ? 'down' : 'flat'
export const scalarMetrics = data => (data?.values || []).filter(c => !c.name.startsWith('chart_') && !c.name.startsWith('disclosed_') && !c.name.includes('_relative_difference'))

// Bounded recognition. Unsupported scopes must never silently lose their conditions.
export function prepareQuestion(question, instruments) {
  const text = question.trim()
  if (!text) return { error: '请输入一个投研问题。' }
  if (text.length > 2000) return { error: '问题请控制在 2000 字以内。' }
  if (/回测|策略|调仓|跑赢|选股|沪深300/i.test(text)) return { error: '请在策略回测工作台确认条件并执行历史数据检查；准入通过后才能计算收益。', target: 'backtest' }
  if (/重算|假设|增长率|折现率|买入|卖出|值得买|目标价|预测|\d\s*[%％]/i.test(text)) return { error: 'DCF 可在单股报告中修改参数并重算；预测和交易指令尚未接入。' }
  const relative = text.match(/(?:近|最近|过去)\s*([0-9一二三四五六七八九十]+)\s*年/)
  const years = relative ? Number(relative[1]) || ({ 一: 1, 二: 2, 三: 3 }[relative[1]]) : null
  if (relative && ![1, 2, 3].includes(years)) return { error: '当前数据契约最多支持三年，请缩小区间。' }
  const matches = instruments.filter(item => text.includes(item.name) || new RegExp('(^|[^0-9])' + item.code + '([^0-9]|$)').test(text)).sort((a,b) => Math.min(...[a.name,a.code].map(s => text.indexOf(s)).filter(i => i >= 0)) - Math.min(...[b.name,b.code].map(s => text.indexOf(s)).filter(i => i >= 0)))
  if (matches.length > 2) return { error: '本阶段支持恰好两只股票比较，请明确选择两只。' }
  if (/\bvs\b|对比|比较/i.test(text) && matches.length !== 2) return { error: '比较需要恰好两只已接入股票，请写明双方名称或代码。' }
  if (!matches.length) return { error: '请在问题中写明已接入的股票名称或六位代码，也可从下方重点标的进入。' }
  const plan = { question: text, instrument: matches[0], instruments: matches, scenario: matches.length === 2 ? 'compare' : 'deep_research', window: years === 3 ? '3y' : '1y' }
  const annual = text.match(/(20\d{2})\s*年/)
  if (annual) { plan.date_start = annual[1] + '-01-01'; plan.date_end = annual[1] + '-12-31' }
  if (years === 2) {
    const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
    const end = new Date(today + 'T00:00:00Z'); end.setUTCDate(end.getUTCDate() - 1)
    const start = new Date(end); start.setUTCFullYear(start.getUTCFullYear() - 2)
    plan.date_start = start.toISOString().slice(0,10); plan.date_end = end.toISOString().slice(0,10)
  }
  return plan
}

export function commonWindows(groups) {
  if (groups.length !== 2) return []
  return groups[0].flatMap(a => groups[1].filter(b => a.date_start === b.date_start && a.date_end === b.date_end && new Date(a.asof).getTime() === new Date(b.asof).getTime()).map(b => ({ ...a, window_id:a.window_id + '-' + b.window_id, sample_count:Math.min(a.sample_count,b.sample_count), adjustments:a.adjustments.filter(v => b.adjustments.includes(v)), branches:[a,b] }))).filter(w => w.adjustments.length)
}

export function reportSummary(report) {
  const section = report?.sections?.find(s => ['EXECUTIVE_SUMMARY', 'EXECUTIVE_STATUS'].includes(s.section_type))
  return (section?.content?.units || []).map(u => u.text).filter(Boolean)
}
