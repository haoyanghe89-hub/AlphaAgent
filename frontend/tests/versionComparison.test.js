import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'

const {descriptor}=parse(readFileSync(new URL('../src/alpha/VersionComparison.vue',import.meta.url),'utf8'))
const code=compileScript(descriptor,{id:'version-test',inlineTemplate:true,genDefaultAs:'Component'}).content
  .replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"]/g,(_,names)=>'const {'+names.replace(/\s+as\s+/g,':')+'}=Vue')
  .replace(/^import .*$/gm,'')

async function mount(parentId){
  let mounted
  const calls=[]
  const runtime={...Vue,onMounted:fn=>{mounted=fn},onUnmounted:()=>{}}
  const api=async(path,options)=>{calls.push({path,options});return {parent:parentId,changes:[]}}
  const component=Function('Vue','request',code+';return Component')(runtime,api)
  component.setup({id:'RUN-DEC-test',page:'candidates',parentId})
  await mounted()
  return calls
}

test('a verified root research does not request an empty version comparison',async()=>{
  assert.deepEqual(await mount(null),[])
})

test('a child research still requests its original version comparison',async()=>{
  const calls=await mount('RUN-DEC-parent')
  assert.equal(calls.length,1)
  assert.equal(calls[0].path,'/quant/decision/runs/RUN-DEC-test/comparison')
  assert.equal(calls[0].options.timeoutMs,60000)
})

test('a caller without parent metadata keeps the existing comparison lookup',async()=>{
  assert.equal((await mount(undefined)).length,1)
})
