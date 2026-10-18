import {test} from 'node:test'
import assert from 'node:assert/strict'
import {createKnowledgeRelease,armLabel} from '../src/alpha/useKnowledgeRelease.js'
test('release clears stale results on verification failure and ignores late response',async()=>{
  let finish,n=0
  const ctl=createKnowledgeRelease({api:async()=>{n++;if(n===1)return new Promise(r=>finish=r);if(n===2)return {status:'COMPLETED'};throw Error('固定版本校验失败')}})
  const late=ctl.load();await ctl.load();finish({status:'STALE'});await late
  assert.equal(ctl.data.value.status,'COMPLETED');await ctl.load();assert.equal(ctl.data.value,null);assert.match(ctl.error.value,/校验失败/)
  ctl.dispose()
})
test('control label states what is ablated, never implies an untrained model',()=>{
  assert.equal(armLabel.NO_METHOD,'无入库方法与路线')
})
