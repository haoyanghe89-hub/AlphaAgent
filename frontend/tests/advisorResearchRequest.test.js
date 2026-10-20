import test from 'node:test'
import assert from 'node:assert/strict'
import {createHoldingAdvisor} from '../src/alpha/useHoldingAdvisor.js'
import {createPortfolioAdvisor} from '../src/alpha/usePortfolioAdvisor.js'

for (const [name,create,root,privateFields] of [
  ['holding',createHoldingAdvisor,'/quant/holding-advisor/turns',{position:{entity:'000333',cost_price:'80',shares:100,sellable_shares:100,total_assets:'100000'}}],
  ['portfolio',createPortfolioAdvisor,'/quant/portfolio-advisor/turns',{assets:[{entity:'000333',shares:100},{entity:'600036',shares:100}],available_cash:'30000'}],
]) {
  test(`${name}: new conversations default to standard research and retain retry identity and local inputs`,async()=>{
    const posts=[]
    const ctl=create({api:async(path,options)=>{
      assert.equal(path,root)
      posts.push(JSON.parse(options.body))
      throw Error('暂时断网')
    }})
    const payload={...privateFields,question:'这笔仓位应该如何调整？'}
    await ctl.submit(payload)
    await ctl.submit(payload)
    assert.equal(posts[0].enhanced_research,undefined)
    assert.equal(posts[0].structured_research,true)
    assert.equal(posts[0].request_key,posts[1].request_key)
    for (const [key,value] of Object.entries(privateFields)) assert.deepEqual(posts[0][key],value)
    assert.equal('enhanced_research' in payload,false)
    ctl.dispose()
  })

  for (const enhanced of [false,true]) {
    test(`${name}: restored ${enhanced?'enhanced':'legacy'} followups preserve their research mode and refresh defaults to standard research`,async()=>{
      const original={...privateFields,question:'上一轮问题',...(enhanced?{enhanced_research:true}:{})}
      const posts=[]
      const ctl=create({api:async(path,options)=>{
        if (!options) return {run_id:'saved',status:'COMPLETED',request:original}
        posts.push(JSON.parse(options.body))
        throw Error('暂时断网')
      }})
      await ctl.load('saved')
      const followup={...privateFields,previous_run_id:'saved',question:'继续分析',refresh_research:false}
      await ctl.submit(followup)
      assert.equal(posts[0].enhanced_research,enhanced?true:undefined)
      assert.equal(posts[0].previous_run_id,'saved')
      await ctl.submit({...followup,refresh_research:true})
      assert.equal(posts[1].enhanced_research,undefined)
      assert.notEqual(posts[0].request_key,posts[1].request_key)
      assert.deepEqual(ctl.state.value.request,original)
      assert.equal(original.enhanced_research,enhanced?true:undefined)
      ctl.dispose()
    })
  }
}
