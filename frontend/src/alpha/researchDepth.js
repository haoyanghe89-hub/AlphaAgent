import {ref} from 'vue'

// A new browser session starts with the standard LLM + knowledge workflow.
export const researchDepth=ref('STANDARD')
export const deepResearch=()=>researchDepth.value==='DEEP'
export const structuredResearch=()=>({execution_contract:'investment-structured-v2',input_reservation:'deepseek-v4-tokenizer-v1',...(deepResearch()?{enhanced_research:true}:{})})
