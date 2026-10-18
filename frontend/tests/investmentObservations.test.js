import {test} from 'node:test'
import assert from 'node:assert/strict'
import {createObservations,observationChart} from '../src/alpha/useInvestmentObservations.js'
test('observation requests suppress late replies and duplicate writes',async()=>{
  let finish;const calls=[]
  const ctl=createObservations({api:async(path,options)=>{calls.push(path);if(path.includes('/old/'))return new Promise(resolve=>finish=resolve);if(options)return {};return path.endsWith('/evaluation')?{groups:[]}:{status:'ENROLLED'}}})
  const old=ctl.load('old');await ctl.load('new');finish({status:'STALE'});await old
  assert.equal(ctl.view.value.status,'ENROLLED');ctl.dispose()
})
test('observation charts preserve missing prices and original anchor',()=>{
  const chart=observationChart({security_id:'x',name:'合成',points:[{date:'b',price:null},{date:'c',price:'11'}]},{market_date:'a',candidates:[{security_id:'x',metrics:{close:{value:'10'}}}]})
  assert.deepEqual(chart.x,['a','b','c']);assert.deepEqual(chart.series[0].data,[10,null,11])
})
