import test from 'node:test'
import assert from 'node:assert/strict'
import {deepResearch,researchDepth,structuredResearch} from '../src/alpha/researchDepth.js'
import {advisorResearchRequest} from '../src/alpha/advisorResearchRequest.js'

test('standard is default; explicit deep changes only new research; historical followups keep original mode',()=>{
  assert.equal(deepResearch(),false)
  assert.equal(structuredResearch().enhanced_research,undefined)
  researchDepth.value='DEEP'
  assert.equal(structuredResearch().enhanced_research,true)
  assert.equal(advisorResearchRequest({question:'新问题'}).enhanced_research,true)
  assert.equal(advisorResearchRequest({previous_run_id:'saved'},{}).enhanced_research,undefined)
  assert.equal(advisorResearchRequest({previous_run_id:'saved',refresh_research:true},{}).enhanced_research,true)
  researchDepth.value='STANDARD'
  assert.equal(advisorResearchRequest({previous_run_id:'saved'},{enhanced_research:true}).enhanced_research,true)
  assert.equal(advisorResearchRequest({question:'新问题'}).enhanced_research,undefined)
})
