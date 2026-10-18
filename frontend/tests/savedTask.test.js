import test from 'node:test'
import assert from 'node:assert/strict'
import {postSavedTask} from '../src/api/savedTask.js'

test('busy service queues the same request and proceeds when the slot is free',async()=>{
  const calls=[],messages=[]
  const options={method:'POST',body:JSON.stringify({request_key:'same-key',entities:['000333']})}
  const result=await postSavedTask(async(path,input)=>{
    calls.push([path,input]);if(calls.length<3)throw {status:429};return {run_id:'saved'}
  },'/runs',options,{wait:async()=>{},waiting:m=>messages.push(m)})
  assert.equal(result.run_id,'saved');assert.equal(calls.length,3)
  assert.ok(calls.every(c=>c[1]===options));assert.equal(messages.length,2)
})

test('busy retries stop at the limit and disposed views cannot submit later',async()=>{
  let calls=0,disposed=false
  const options={method:'POST',body:JSON.stringify({request_key:'same-key'})}
  await assert.rejects(postSavedTask(async()=>{calls++;throw {status:429}},'/runs',options,{wait:async()=>{}}))
  assert.equal(calls,25)
  calls=0
  await postSavedTask(async()=>{calls++;throw {status:429}},'/runs',options,{wait:async()=>{disposed=true},disposed:()=>disposed})
  assert.equal(calls,1)
})
