import test from 'node:test'
import assert from 'node:assert/strict'
import { useMarket } from '../src/alpha/useMarket.js'

test('market lost response reuses request key; new successful batch gets a new key', async () => {
  const calls = []; let n = 0
  const flow = useMarket({key:() => String(++n),api:async (path,opts) => {
    if (!opts) return {exists:true,acquisition_status:'COMPLETED',coverage:{offset:0}}
    calls.push({path,body:JSON.parse(opts.body)})
    if (calls.length === 1) throw new Error('response lost')
    return {batch_id:'b'}
  }})
  await flow.start('A'); await flow.start('A'); await flow.start('A')
  assert.equal(calls[0].body.request_key,calls[1].body.request_key)
  assert.notEqual(calls[1].body.request_key,calls[2].body.request_key)
  assert.equal(flow.data.value.acquisition_status,'COMPLETED')
  flow.dispose()
})

test('market suppresses duplicate start, ignores disposed response and clears corrupt state', async () => {
  let release, calls = 0
  const flow = useMarket({key:() => 'k',api:async () => {calls++; return new Promise(r => {release=r})}})
  const first = flow.start('A',5); await flow.start('A',5)
  assert.equal(calls,1)
  flow.dispose(); release({batch_id:'late'}); await first
  assert.equal(flow.data.value,null)
  let fail = false
  const state = useMarket({api:async () => {if (fail) throw new Error('integrity'); return {exists:true}}})
  await state.load('A'); fail = true; await state.load('A')
  assert.equal(state.data.value,null); assert.equal(state.error.value,'integrity')
  state.dispose()
})
