import assert from 'node:assert/strict'
import test from 'node:test'
import { commonWindows, historyStatus, isHistoryActive, isHistoryDone, metricValue, number, prepareQuestion, workbenchExamples } from '../src/alpha/model.js'
import { parseRoute } from '../src/alpha/navigation.js'
import { useQuantRun } from '../src/alpha/useQuantRun.js'
import { useFollowup } from '../src/alpha/useFollowup.js'

const stocks = [{ code: '000001', name: '平安银行' }, { code: '600519', name: '贵州茅台' }]

test('workbench examples use the available catalog and never invent missing subjects', () => {
  assert.deepEqual(workbenchExamples([]), [])
  for (const catalog of [stocks.slice(0, 1), stocks]) {
    const examples = workbenchExamples(catalog)
    assert.ok(examples.length >= 2)
    for (const question of examples) assert.ok(!prepareQuestion(question, catalog).error, question)
    assert.equal(examples.some(q => q.includes(' vs ')), catalog.length > 1)
  }
})

test('history distinguishes a saved input check from a successful numerical backtest', () => {
  const check = {scenario:'backtest',status:'FAILED',check_status:'COMPLETED',execution_status:'BLOCKED'}
  assert.equal(historyStatus(check),'检查完成 · 回测受阻')
  assert.equal(isHistoryDone(check),true)
  assert.equal(isHistoryActive(check),false)
  assert.equal(historyStatus({...check,check_status:'INTEGRITY_FAILED'}),'检查完整性失败')
  assert.equal(isHistoryDone({...check,check_status:'INTEGRITY_FAILED'}),false)
  assert.equal(isHistoryActive({...check,check_status:'RUNNING'}),true)
  assert.equal(historyStatus({status:'COMPLETED'}),'已成稿')
})
test('DCF structured assumptions survive retries and changed assumptions get a new request key', async () => {
  const calls = []; let keys = 0
  const flow = useFollowup('PARENT', { key:() => 'DCF-' + ++keys, api:async (url, options) => { calls.push(JSON.parse(options.body)); throw new Error('lost response') } })
  const parameters = { dcf:{g1:'0.08',g2:'0.04',g_term:'0.025',discount:'0.09'} }
  await flow.submit('DCF 按当前假设重算', parameters)
  await flow.submit('DCF 按当前假设重算', parameters)
  assert.deepEqual(calls[0].dcf,parameters.dcf)
  assert.equal(calls[0].request_key,calls[1].request_key)
  await flow.submit('DCF 按当前假设重算',{dcf:{...parameters.dcf,g1:'0.12'}})
  assert.notEqual(calls[2].request_key,calls[1].request_key)
  assert.equal(prepareQuestion('查看平安银行的DCF',stocks).instrument.code,'000001')
  flow.dispose()
})
test('followups deduplicate clicks, retry lost responses with the same key, and retire late responses', async () => {
  const calls = [], seen = []
  let keys = 0, release
  const flow = useFollowup('PARENT', { key: () => 'KEY-' + ++keys, started: value => seen.push(value), api: async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) })
    if (calls.length === 1) throw new Error('connection lost')
    return new Promise(resolve => { release = resolve })
  } })
  await flow.submit('重新核对收益与风险')
  assert.equal(flow.error.value, 'connection lost')
  const pending = flow.submit('重新核对收益与风险')
  await flow.submit('重新核对收益与风险')
  assert.equal(calls.length, 2)
  assert.equal(calls[0].body.request_key, calls[1].body.request_key)
  assert.equal(calls[0].url, '/quant/runs/PARENT/followups')
  flow.dispose(); release({ run_id: 'CHILD' }); await pending
  assert.equal(seen.length, 0)
})
test('questions retain intent and both comparison subjects in question order', () => {
  assert.equal(prepareQuestion('全面分析平安银行', stocks).instrument.code, '000001')
  assert.equal(prepareQuestion('研究000001的历史收益', stocks).question, '研究000001的历史收益')
  const compared = prepareQuestion('贵州茅台 vs 平安银行',stocks)
  assert.equal(compared.scenario,'compare')
  assert.deepEqual(compared.instruments.map(s => s.code),['600519','000001'])
  for (const text of ['平安银行 vs 陌生公司', '平安银行按20%增长率重算', '平安银行近5年收益', '平安银行回测策略', '1000001', '陌生公司']) {
    assert.ok(prepareQuestion(text, stocks).error, text)
  }
})
test('comparison windows intersect exact periods, instants and adjustments without choosing a period', () => {
  const a = {window_id:'A',date_start:'2023-06-15',date_end:'2024-06-14',asof:'2024-06-15T00:00:00Z',sample_count:241,adjustments:['raw','qfq'],frozen_snapshot_ids:['left']}
  const b = {...a,window_id:'B',asof:'2024-06-15T08:00:00+08:00',adjustments:['raw'],frozen_snapshot_ids:['right']}
  const common = commonWindows([[a],[b]])
  assert.deepEqual(common[0].adjustments,['raw'])
  assert.deepEqual(common[0].branches.map(w => w.frozen_snapshot_ids),[['left'],['right']])
  assert.deepEqual(commonWindows([[a],[{...b,date_end:'2024-06-13'}]]),[])
  assert.deepEqual(commonWindows([[a],[{...b,asof:'2024-06-16T00:00:00Z'}]]),[])
})
test('missing financial values remain missing while genuine zero displays as zero', () => {
  assert.equal(number(null), '—')
  assert.equal(number(''), '—')
  assert.equal(metricValue({ value: null, unit: 'fraction' }), '—')
  assert.equal(metricValue({ value: '0', unit: 'fraction' }), '0.00%')
  assert.equal(metricValue({ value: '-0.10548', unit: 'fraction' }), '-10.55%')
  assert.equal(metricValue({ value: '0.25570967', unit: 'fraction/sqrt(year)' }), '25.57%')
  assert.equal(metricValue({ value: '-0.803677', unit: 'fraction/year' }), '-80.37%')
  assert.equal(metricValue({ value: '', unit: 'fraction' }), '—')
})
test('requested windows are explicit and calendar years are not mistaken for five-year ranges', () => {
  assert.equal(prepareQuestion('平安银行近三年的风险', stocks).window, '3y')
  assert.equal(prepareQuestion('平安银行近2年收益', stocks).date_end.length, 10)
  const annual = prepareQuestion('平安银行2024年的估值', stocks)
  assert.equal(annual.date_start, '2024-01-01')
  assert.equal(annual.date_end, '2024-12-31')
})
test('deep links survive refresh and invalid encoding is rejected', () => {
  assert.deepEqual(parseRoute('#/report/RUN-QUANT-123'), { page: 'report', id: 'RUN-QUANT-123' })
  assert.deepEqual(parseRoute('#/stock/000001'), { page: 'stock', id: '000001' })
  assert.deepEqual(parseRoute('#/report/%ZZ'), { page: 'missing' })
})
test('late run response cannot restore a disposed page', async () => {
  let resolve
  const pending = new Promise(r => { resolve = r })
  const view = useQuantRun('A', { api: () => pending, unmount: () => {}, unschedule: () => {} })
  const loading = view.load()
  view.dispose()
  resolve({ run: { status: 'COMPLETED' }, values: [{ value: '999' }] })
  await loading
  assert.equal(view.data.value, null)
})
test('integrity failure clears numbers and reports instead of retaining stale evidence', async () => {
  let fail = false
  const view = useQuantRun('A', {
    api: async path => {
      if (fail) throw Object.assign(new Error('integrity drift'), { status: 409 })
      return path.includes('/reports/') ? { report: { report_id: 'R' }, sections: [] } : { run: { status: 'COMPLETED' }, report_id: 'R' }
    }, unmount: () => {}, unschedule: () => {},
  })
  await view.load()
  assert.equal(view.report.value.report_id, 'R')
  fail = true
  await view.load()
  assert.equal(view.report.value, null)
  assert.equal(view.data.value, null)
  assert.equal(view.error.value, 'integrity drift')
})
