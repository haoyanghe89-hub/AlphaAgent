import assert from 'node:assert/strict'
import test from 'node:test'
import { parseRoute } from '../src/alpha/navigation.js'
import { createDecisionResearch } from '../src/alpha/useDecisionResearch.js'
import {decisionConditions,workbenchIntent} from '../src/alpha/workbenchIntent.js'

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

test('market and candidate starts default to standard and preserve explicit enhancement from both workbench and dedicated pages without changing other modes',async()=>{
  for (const [mode,research_mode] of [['market','market_scan'],['candidates','candidates']]) {
    const conditions=decisionConditions({mode,entity:'半导体设备',question:'合成界面请求'})
    assert.equal(conditions.enhanced_research,undefined)
    const posts=[]
    const ctl=createDecisionResearch(null,{api:async(path,options)=>{posts.push(JSON.parse(options.body));throw Error('lost response')}})
    const direct={research_mode,entity:'半导体设备',question:'合成界面请求'}
    await ctl.start(direct)
    await ctl.start({...direct,enhanced_research:false})
    assert.equal(posts[0].enhanced_research,false)
    assert.equal(posts[0].request_key,posts[1].request_key)
    assert.equal('enhanced_research' in direct,false)
    ctl.dispose()
  }
  assert.equal('enhanced_research' in decisionConditions({mode:'medium',entity:'合成股票',question:'合成中期问题'}),false)
  for (const research_mode of ['medium_term','evidence_only',undefined]) {
    const posts=[]
    const ctl=createDecisionResearch(null,{api:async(path,options)=>{posts.push(JSON.parse(options.body));return {run_id:'RUN-DEC-synthetic'}},navigate:()=>{}})
    await ctl.start({research_mode,entity:'合成股票',question:'合成原流程'})
    assert.equal('enhanced_research' in posts[0],false)
    ctl.dispose()
  }
})

test('refresh uses selected standard mode for market/candidate new versions and retains legacy medium payload',async()=>{
  for (const research_mode of ['market_scan','candidates','medium_term']) {
    const posts=[]
    const ctl=createDecisionResearch('RUN-DEC-old',{api:async(path,options)=>{
      posts.push(JSON.parse(options.body));return {run_id:'RUN-DEC-new'}
    },navigate:()=>{}})
    ctl.state.value={status:'COMPLETED',request:{research_mode}}
    await ctl.refresh()
    assert.equal(posts[0].enhanced_research,research_mode==='medium_term'?undefined:false)
    assert.equal(ctl.state.value.request.enhanced_research,undefined)
    ctl.dispose()
  }
})

test('industry candidate questions retain their industry intent when a holding horizon is included',()=>{
  const intent=workbenchIntent('分析半导体设备行业未来三个月的候选，比较公司业务与现金回收。','candidates')
  assert.equal(intent.mode,'candidates')
  assert.equal(intent.entity,'半导体设备')
  assert.equal(decisionConditions(intent).enhanced_research,undefined)
  assert.equal(workbenchIntent('集成电路制造中期哪些公司值得研究？','next').mode,'candidates')
  assert.equal(workbenchIntent('宁德时代未来三个月怎么看？','candidates').mode,'medium')
})
