import assert from 'node:assert/strict'
import test from 'node:test'
import { parseRoute } from '../src/alpha/navigation.js'
import { createDecisionResearch } from '../src/alpha/useDecisionResearch.js'

test('P0 capability link survives refresh without changing legacy routes', () => {
  assert.deepEqual(parseRoute('#/decision'), { page:'decision' })
  assert.deepEqual(parseRoute('#/decision/RUN-DEC-123'), { page:'decision', id:'RUN-DEC-123' })
  assert.deepEqual(parseRoute('#/strategy'), { page:'strategy' })
  assert.deepEqual(parseRoute('#/live/RUN-LIVE-123'), { page:'live', id:'RUN-LIVE-123' })
})

test('lost decision submission reuses its key and a changed question gets a new key', async () => {
  const bodies = [], links = []
  const ctl = createDecisionResearch(null, {api:async (path, options) => {
    bodies.push(JSON.parse(options.body))
    if (bodies.length === 1) throw new Error('lost response')
    return {run_id:'RUN-DEC-test'}
  },navigate:id => links.push(id)})
  await ctl.start({entity:'synthetic',question:'a'})
  await ctl.start({entity:'synthetic',question:'a'})
  assert.equal(bodies[0].request_key,bodies[1].request_key)
  await ctl.start({entity:'synthetic',question:'b'})
  assert.notEqual(bodies[1].request_key,bodies[2].request_key)
  assert.equal(links.length,2)
  ctl.dispose()
})

test('decision double click cannot duplicate a request and late disposal cannot restore a report', async () => {
  let finish, calls=0
  const ctl = createDecisionResearch(null, {api:() => { calls++; return new Promise(resolve => { finish=resolve }) },navigate:()=>assert.fail('disposed navigation')})
  const pending=ctl.start({entity:'synthetic',question:'a'})
  await ctl.start({entity:'synthetic',question:'a'})
  assert.equal(calls,1)
  ctl.dispose(); finish({run_id:'RUN-DEC-test'}); await pending
  assert.equal(ctl.state.value,null)
})

test('decision integrity failures hide prior evidence and a late GET cannot survive leaving', async () => {
  let fail=false, finish
  const ctl=createDecisionResearch('RUN-DEC-test',{api:async () => {
    if (fail) throw Object.assign(new Error('integrity'),{status:409})
    return {status:'COMPLETED',data:{report:{headline:'synthetic'}}}
  }})
  await ctl.load(); assert.ok(ctl.state.value.data)
  fail=true; await ctl.load(); assert.equal(ctl.state.value,null)
  assert.equal(ctl.error.value,'integrity'); ctl.dispose()
  const late=createDecisionResearch('RUN-DEC-test',{api:()=>new Promise(resolve=>{finish=resolve})})
  const pending=late.load(); late.dispose(); finish({status:'COMPLETED',data:{}}); await pending
  assert.equal(late.state.value,null)
})

test('continuing a failed research reuses retry key, keeps materials on network error and navigates once',async()=>{
  const bodies=[],links=[]
  const ctl=createDecisionResearch('RUN-DEC-failed',{api:async(path,options)=>{
    assert.ok(path.endsWith('/retry'))
    bodies.push(JSON.parse(options.body));if(bodies.length===1)throw new Error('lost response')
    return {run_id:'RUN-DEC-recovered'}
  },navigate:id=>links.push(id)})
  ctl.state.value={status:'FAILED',available_materials:{name:'合成公司'}}
  await ctl.retry();assert.equal(ctl.state.value.available_materials.name,'合成公司')
  await ctl.action('resume')
  assert.equal(bodies[0].request_key,bodies[1].request_key)
  assert.deepEqual(links,['RUN-DEC-recovered']);ctl.dispose()
})
