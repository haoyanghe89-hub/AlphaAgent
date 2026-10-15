import assert from 'node:assert/strict'
import test from 'node:test'
import { createNextSessionPrediction } from '../src/alpha/useNextSessionPrediction.js'

test('prediction lost submission preserves key and prevents double clicks',async()=>{
  const bodies=[],links=[]
  const ctl=createNextSessionPrediction(null,{api:async(path,options)=>{
    if(path.endsWith('/enable'))return {enabled:true}
    bodies.push(JSON.parse(options.body));if(bodies.length===1)throw new Error('lost response')
    return {run_id:'RUN-NS-test'}
  },navigate:id=>links.push(id)})
  await ctl.start();await ctl.start()
  assert.equal(bodies[0].request_key,bodies[1].request_key);assert.equal(links.length,1);ctl.dispose()
})

test('integrity failure hides saved direction and statistics',async()=>{
  let fail=false
  const ctl=createNextSessionPrediction('RUN-NS-test',{api:async path=>{
    if(fail)throw Object.assign(new Error('integrity'),{status:409})
    return path.includes('/runs/')?{status:'COMPLETED',predictions:[{prediction:'DOWN'}]}:{}
  }})
  await ctl.refresh();assert.ok(ctl.state.value)
  fail=true;await ctl.refresh();assert.equal(ctl.state.value,null);assert.equal(ctl.scoreboard.value,null);ctl.dispose()
})

test('leaving prediction page suppresses late navigation and overlapping submit',async()=>{
  let finish,calls=0
  const ctl=createNextSessionPrediction(null,{api:async path=>{
    calls++;if(path.endsWith('/enable'))return {}
    return new Promise(resolve=>{finish=resolve})
  },navigate:()=>assert.fail('disposed navigation')})
  const pending=ctl.start();await Promise.resolve();await ctl.start()
  assert.equal(calls,2);ctl.dispose();finish({run_id:'RUN-NS-test'});await pending;assert.equal(ctl.state.value,null)
})

test('selected stock is sent explicitly and a changed stock gets a new retry key',async()=>{
  const bodies=[]
  const ctl=createNextSessionPrediction(null,{api:async(path,options)=>{
    if(path.endsWith('/enable'))return {enabled:true}
    bodies.push(JSON.parse(options.body));throw new Error('lost response')
  }})
  await ctl.start('600519');await ctl.start('600519');await ctl.start('宁德时代')
  assert.equal(bodies[0].entity,'600519');assert.equal(bodies[0].exploratory,true)
  assert.equal(bodies[0].request_key,bodies[1].request_key)
  assert.notEqual(bodies[0].request_key,bodies[2].request_key);assert.equal(bodies[2].entity,'宁德时代');ctl.dispose()
})
