import {ref} from 'vue'
import request from '../api/request.js'
export function createKnowledgeRelease({api=request}={}){
  const data=ref(null),comparison=ref(null),error=ref(''),busy=ref(false)
  let generation=0,disposed=false
  async function load(){const ticket=++generation;data.value=null;comparison.value=null;error.value='';busy.value=true
    try{const value=await api('/quant/knowledge/release');if(!disposed&&ticket===generation)data.value=value}
    catch(e){if(!disposed&&ticket===generation){data.value=null;error.value=e.message}}
    finally{if(!disposed&&ticket===generation)busy.value=false}
  }
  async function audit(){const ticket=generation;comparison.value=null;error.value=''
    try{const value=await api('/quant/knowledge/release/comparison');if(!disposed&&ticket===generation)comparison.value=value}
    catch(e){if(!disposed&&ticket===generation){comparison.value=null;error.value=e.message}}
  }
  return {data,comparison,error,busy,load,audit,dispose(){disposed=true;generation++}}
}
export const armLabel={NO_METHOD:'无入库方法与路线',FIXED_TREND:'固定趋势路线',ADAPTIVE_ROUTE:'按期限适配路线'}
export const directionLabel={FAVORABLE:'倾向看多',UNFAVORABLE:'倾向看空'}
