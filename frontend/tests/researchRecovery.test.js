import test from 'node:test'
import assert from 'node:assert/strict'
import {createInvestmentPlan} from '../src/alpha/useInvestmentPlan.js'

test('a transient poll failure retains the same task and recovers without another submission',async()=>{
  let fail=false,queued
  const paths=[]
  const ctl=createInvestmentPlan({schedule:fn=>{queued=fn;return 1},unschedule:()=>{},api:async path=>{
    paths.push(path)
    if(fail){fail=false;throw Object.assign(Error('临时断线'),{status:503})}
    return {run_id:'saved',status:paths.length>2?'COMPLETED':'RUNNING'}
  }})
  await ctl.load('saved');fail=true;await ctl.load('saved')
  assert.equal(ctl.state.value?.run_id,'saved')
  assert.match(ctl.error.value,/自动/)
  await queued()
  assert.equal(ctl.state.value.status,'COMPLETED');assert.equal(ctl.error.value,'')
  assert.deepEqual(paths,Array(3).fill('/quant/investment-plans/saved'));ctl.dispose()
})

test('initial network failure retries the requested record but never retains another record',async()=>{
  let queued,fail=true
  const ctl=createInvestmentPlan({schedule:fn=>{queued=fn;return 1},unschedule:()=>{},api:async path=>{
    if(fail)throw Object.assign(Error('offline'),{status:0})
    return {run_id:path.split('/').at(-1),status:'COMPLETED'}
  }})
  await ctl.load('saved');assert.equal(ctl.state.value,null)
  fail=false;await queued();assert.equal(ctl.state.value.run_id,'saved')
  fail=true;await ctl.load('other');assert.equal(ctl.state.value,null);ctl.dispose()
})

test('integrity rejection stops retries and hides all result data',async()=>{
  let fail=false,queued=null
  const ctl=createInvestmentPlan({schedule:fn=>{queued=fn},unschedule:()=>{queued=null},api:async()=>{
    if(fail)throw Object.assign(Error('原文校验失败'),{status:409})
    return {run_id:'saved',status:'COMPLETED',data:{summary:'saved'}}
  }})
  await ctl.load('saved');fail=true;await ctl.load('saved')
  assert.equal(ctl.state.value,null);assert.equal(queued,null);assert.equal(ctl.error.value,'原文校验失败');ctl.dispose()
})

test('a lost submission response is confirmed with the same request key automatically',async()=>{
  const bodies=[],saved=[]
  const ctl=createInvestmentPlan({started:value=>saved.push(value.run_id),api:async(_,options)=>{
    bodies.push(JSON.parse(options.body))
    if(bodies.length===1)throw Object.assign(Error('lost response'),{status:0})
    return {run_id:'same-saved-task',status:'COMPLETED'}
  }})
  await ctl.submit({entities:['000333'],profile:null})
  assert.equal(bodies.length,2);assert.equal(bodies[0].request_key,bodies[1].request_key)
  assert.deepEqual(saved,['same-saved-task']);assert.equal(ctl.error.value,'');ctl.dispose()
})

test('a rejected new question retains the prior verified answer and editable inputs',async()=>{
  const ctl=createInvestmentPlan({api:async(_,options)=>{
    if(options)throw Object.assign(Error('invalid new input'),{status:422})
    return {run_id:'old',status:'COMPLETED',data:{answer:'saved'}}
  }})
  await ctl.load('old');await ctl.submit({entities:['bad']})
  assert.equal(ctl.state.value?.run_id,'old');assert.equal(ctl.busy.value,false)
  assert.match(ctl.error.value,/invalid/);ctl.dispose()
})

test('a parent integrity failure hides even the previously displayed answer',async()=>{
  const ctl=createInvestmentPlan({api:async(_,options)=>{
    if(options)throw Object.assign(Error('资料或版本校验失败，已隐藏结果。'),{status:409})
    return {run_id:'old',status:'COMPLETED',data:{answer:'saved'}}
  }})
  await ctl.load('old');await ctl.submit({previous_run_id:'old'})
  assert.equal(ctl.state.value,null);ctl.dispose()
})
