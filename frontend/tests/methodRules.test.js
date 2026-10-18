import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createMethodRules } from '../src/alpha/useMethodRules.js'
const row=id=>({rule_id:id,version:'a'.repeat(64),review_ids:[id],rule:{title:id}})

test('fixed-version rule and both review responsibilities load together',async()=>{
  const ctl=createMethodRules({api:async path=>path.endsWith('/rules')?{items:[row('ONE')]}:path.includes('/reviews/')?{reviews:[{role:'SupportReviewer'},{role:'CounterexampleReviewer'}]}:row('ONE')})
  await ctl.load();assert.equal(ctl.selected.value.rule_id,'ONE');assert.equal(ctl.reviews.value[0].reviews.length,2)
  ctl.dispose()
})
test('late detail or review responses cannot overwrite a later selected rule',async()=>{
  let finish
  const ctl=createMethodRules({api:path=>path.includes('/reviews/OLD')?new Promise(resolve=>{finish=resolve}):Promise.resolve(path.includes('/rules/OLD/')?row('OLD'):row('NEW'))})
  const old=ctl.select(row('OLD'));await new Promise(resolve=>setTimeout(resolve,0));await ctl.select(row('NEW'));finish({old:true});await old
  assert.equal(ctl.selected.value.rule_id,'NEW');assert.equal(ctl.reviews.value[0].rule_id,'NEW');ctl.dispose()
})
test('failed or corrupted catalog clears stale rules and disposed selection stays empty',async()=>{
  let fail=false
  const ctl=createMethodRules({api:async path=>{if(fail)throw Object.assign(new Error('校验失败'),{status:409});return path.endsWith('/rules')?{items:[row('ONE')]}:row('ONE')}})
  await ctl.load();fail=true;await ctl.load();assert.equal(ctl.items.value.length,0);assert.equal(ctl.selected.value,null);assert.equal(ctl.reviews.value.length,0)
  ctl.dispose();await ctl.select(row('ONE'));assert.equal(ctl.selected.value,null)
})
