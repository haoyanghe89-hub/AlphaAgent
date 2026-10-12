import test from 'node:test'
import assert from 'node:assert/strict'
import { useBacktest } from '../src/alpha/useBacktest.js'

for (const method of ['benchmark','lifecycles','notices']) test(`official ${method} retries bind parent and ignore a disposed response`, async () => {
  let n = 0, release; const calls = [], seen = []
  const flow = useBacktest({ key:() => String(++n), started:r => seen.push(r), api:async (path,options) => {
    calls.push({path,body:JSON.parse(options.body)})
    if (calls.length === 1) throw new Error('response lost')
    if (calls.length === 2) return {run_id:'B'}
    return new Promise(resolve => { release = resolve })
  } })
  await flow[method]('P'); await flow[method]('P')
  assert.equal(calls[0].path, '/quant/backtests/P/' + method)
  assert.equal(calls[0].body.request_key, calls[1].body.request_key)
  const pending = flow[method]('P2'); await flow[method]('P2')
  assert.equal(calls.length, 3)
  assert.notEqual(calls[1].body.request_key, calls[2].body.request_key)
  flow.dispose(); release({run_id:'late'}); await pending
  assert.equal(seen.length, 1)
})
import { parseRoute } from '../src/alpha/navigation.js'

test('official filing retry preserves UUID, binds parent and ignores disposed response', async () => {
  const calls = [], seen = []; let n = 0, release
  const flow = useBacktest({key:() => String(++n), started:r => seen.push(r), api:async (path,options) => {
    calls.push({path,body:JSON.parse(options.body)})
    if (calls.length < 3) throw new Error('response lost')
    return new Promise(resolve => { release = resolve })
  }})
  await flow.filings('P'); await flow.filings('P')
  assert.equal(calls[0].body.request_key,calls[1].body.request_key)
  const pending = flow.filings('P2'); await flow.filings('P2')
  assert.equal(calls.length,3)
  assert.notEqual(calls[1].body.request_key,calls[2].body.request_key)
  flow.dispose(); release({run_id:'old'}); await pending
  assert.deepEqual(seen,[])
})

test('input audit retry binds stock/year/parent and ignores disposed responses', async () => {
  let n = 0, release; const calls = [], seen = []
  const flow = useBacktest({ key:() => String(++n), started:r => seen.push(r), api:async (path,options) => {
    calls.push({path,body:JSON.parse(options.body)})
    if (calls.length < 4) throw new Error('lost response')
    return new Promise(resolve => { release = resolve })
  } })
  await flow.audit('P', 'ins_A', 2024); await flow.audit('P', 'ins_A', 2024)
  assert.equal(calls[0].body.request_key, calls[1].body.request_key)
  await flow.audit('P', 'ins_A', 2025)
  assert.notEqual(calls[1].body.request_key, calls[2].body.request_key)
  const waiting = flow.audit('P2', 'ins_B', 2025)
  await flow.audit('P2', 'ins_B', 2025)
  assert.equal(calls.length, 4)
  assert.equal(calls[3].path, '/quant/backtests/P2/inputs')
  assert.notEqual(calls[2].body.request_key, calls[3].body.request_key)
  flow.dispose(); release({run_id:'old'}); await waiting
  assert.deepEqual(seen, [])
})

test('backtest retries preserve conditions/key and suppress double click and disposed response', async () => {
  const calls = [], seen = []; let release, n = 0
  const flow = useBacktest({ key: () => 'retry-' + ++n, started: r => seen.push(r), api: async (_, options) => {
    calls.push(JSON.parse(options.body))
    if (calls.length === 1) throw new Error('response lost')
    return new Promise(resolve => { release = resolve })
  } })
  const conditions = { pe_max:30, roe_min:15, years:5, cost_pct_per_side:0.13 }
  await flow.submit(conditions)
  const pending = flow.submit(conditions)
  await flow.submit(conditions)
  assert.equal(calls.length, 2)
  assert.equal(calls[0].request_key, calls[1].request_key)
  flow.dispose(); release({run_id:'old'}); await pending
  assert.equal(seen.length, 0)
})
test('new conditions use a new key and integrity errors clear frozen results', async () => {
  let n = 0, fail = false; const calls = []
  const flow = useBacktest({ key: () => String(++n), api: async (_, options) => {
    if (options) { calls.push(JSON.parse(options.body)); throw new Error('network') }
    if (fail) throw Object.assign(new Error('source drift'), { status:409 })
    return {execution_status:'BLOCKED',conditions:{years:5}}
  } })
  await flow.submit({years:5}); await flow.submit({years:3})
  assert.notEqual(calls[0].request_key, calls[1].request_key)
  await flow.load('A'); assert.equal(flow.data.value.conditions.years,5)
  fail = true; await flow.load('A'); assert.equal(flow.data.value,null)
  assert.equal(flow.error.value,'source drift')
  assert.deepEqual(parseRoute('#/backtest/RUN-BT-abc'), {page:'backtest',id:'RUN-BT-abc'})
})

test('historical acquisition retries keep the parent binding and stop replaces progress', async () => {
  let n = 0; const calls = [], seen = []
  const flow = useBacktest({ key:() => String(++n), started:r => seen.push(r), api:async (path,options) => {
    calls.push({path,body:options?.body && JSON.parse(options.body)})
    if (path.endsWith('/history')) { if (calls.length === 1) throw new Error('lost response'); return {run_id:'H'} }
    return {check_status:'CANCELLED',execution_status:'BLOCKED',conditions:{years:5}}
  } })
  await flow.acquire('P'); await flow.acquire('P')
  assert.equal(calls[0].body.request_key,calls[1].body.request_key)
  assert.equal(seen[0].run_id,'H')
  await flow.acquire('P2'); assert.notEqual(calls[1].body.request_key,calls[2].body.request_key)
  await flow.stop('H'); assert.equal(flow.data.value.check_status,'CANCELLED')
  flow.dispose()
})
