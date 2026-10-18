import {test} from 'node:test'
import assert from 'node:assert/strict'
import {createDailyWorkspace} from '../src/alpha/useDailyWorkspace.js'
import {deliveryPage} from '../src/alpha/useDelivery.js'
import {parseRoute} from '../src/alpha/navigation.js'

test('daily source failure retains status but never keeps an unverified briefing',async()=>{
  let fail=false
  const ctl=createDailyWorkspace({api:async path=>{if(path.endsWith('status'))return {enabled:true};if(fail)throw Error('原文校验失败');return {latest:{run_id:'saved',status:'COMPLETED'}}}})
  await ctl.load();assert.equal(ctl.briefing.value.run_id,'saved')
  fail=true;await ctl.load();assert.equal(ctl.briefing.value,null);assert.equal(ctl.status.value.enabled,true)
  assert.match(ctl.error.value,/校验失败/);ctl.dispose()
})
test('daily history opens the same persisted record and disposed responses do not replace it',async()=>{
  assert.equal(deliveryPage({scenario:'morning_brief'}),'morning')
  assert.deepEqual(parseRoute('#/morning/RUN-MB-saved'),{page:'morning',id:'RUN-MB-saved'})
  let finish
  const ctl=createDailyWorkspace({id:'old',api:async p=>p.endsWith('status')?{}:new Promise(r=>finish=r)})
  const pending=ctl.load();ctl.dispose();finish({run_id:'old'});await pending
  assert.equal(ctl.briefing.value,null)
})
test('starting a briefing invalidates an older empty homepage response',async()=>{
  let finish
  const ctl=createDailyWorkspace({api:async(path,options)=>path.endsWith('status')?{}:options?.method==='POST'?{run_id:'new',status:'RUNNING'}:new Promise(r=>finish=r)})
  const old=ctl.load();await ctl.generate();finish({latest:null});await old
  assert.equal(ctl.briefing.value.run_id,'new');assert.equal(ctl.briefing.value.status,'RUNNING')
})
