import {deepResearch} from './researchDepth.js'

export function advisorResearchRequest(body, previousRequest) {
  const payload = {...body}
  payload.semantic_understanding = true
  payload.structured_research = true
  delete payload.enhanced_research
  // Reusing a saved public study must keep its original research mode.
  const enhanced=body.previous_run_id&&!body.refresh_research ? previousRequest?.enhanced_research === true : (body.enhanced_research ?? deepResearch())
  if (enhanced) {
    payload.enhanced_research = true
  }
  return payload
}
