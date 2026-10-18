import assert from 'node:assert/strict'
import test from 'node:test'
import { createKnowledgeLibrary } from '../src/alpha/useKnowledgeLibrary.js'
import { parseRoute } from '../src/alpha/navigation.js'
const doc=(id)=>({document_id:id,version:'a'.repeat(64),category:'合成主题'})

test('knowledge read-only route coexists with the workbench and old history',()=>{
  assert.deepEqual(parseRoute('#/knowledge'),{page:'knowledge'})
  assert.deepEqual(parseRoute('#/'),{page:'workbench'})
  assert.deepEqual(parseRoute('#/library'),{page:'library'})
})
test('late detail and search responses never replace the selected current source',async()=>{
  let oldDetail,oldSearch
  const ctl=createKnowledgeLibrary({api:path=>{
    if(path.includes('/documents/OLD/'))return new Promise(resolve=>{oldDetail=resolve})
    if(path.includes('q=old'))return new Promise(resolve=>{oldSearch=resolve})
    return Promise.resolve(path.includes('/search')?{items:[doc('NEW')]}:doc('NEW'))
  }})
  const first=ctl.select(doc('OLD'));await ctl.select(doc('NEW'));oldDetail(doc('OLD'));await first
  assert.equal(ctl.selected.value.document_id,'NEW')
  const search=ctl.search('old');await ctl.search('new');oldSearch({items:[doc('OLD')]});await search
  assert.equal(ctl.items.value[0].document_id,'NEW')
  ctl.dispose()
})
test('integrity failure clears old library contents and disposal ignores late arrivals',async()=>{
  let fail=false,finish
  const ctl=createKnowledgeLibrary({api:async path=>{
    if(fail)throw Object.assign(new Error('integrity'),{status:409})
    return path.endsWith('/catalog')?{documents:[doc('ONE')]}:doc('ONE')
  }})
  await ctl.load();assert.equal(ctl.items.value.length,1)
  fail=true;await ctl.load();assert.equal(ctl.items.value.length,0);assert.equal(ctl.selected.value,null)
  const late=createKnowledgeLibrary({api:()=>new Promise(resolve=>{finish=resolve})})
  const pending=late.select(doc('ONE'));late.dispose();finish(doc('ONE'));await pending
  assert.equal(late.selected.value,null);ctl.dispose()
})
