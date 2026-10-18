import { onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'
const root='/quant/knowledge'
export const rulePath=row=>root+'/rules/'+encodeURIComponent(row.rule_id)+'/versions/'+encodeURIComponent(row.version)

export function createMethodRules({api=request}={}) {
  const catalog=ref(null),items=ref([]),selected=ref(null),reviews=ref([]),error=ref(''),detailError=ref(''),loading=ref(false),detailLoading=ref(false)
  let disposed=false,generation=0,detailGeneration=0
  async function select(row) {
    const ticket=++detailGeneration
    selected.value=null;reviews.value=[];detailError.value='';detailLoading.value=!!row
    if(!row||disposed)return
    try {
      const detail=await api(rulePath(row))
      const audit=await Promise.all(detail.review_ids.map(id=>api(root+'/reviews/'+encodeURIComponent(id))))
      if(!disposed&&ticket===detailGeneration){selected.value=detail;reviews.value=audit}
    }catch(e){if(!disposed&&ticket===detailGeneration)detailError.value=e.message}
    finally{if(ticket===detailGeneration)detailLoading.value=false}
  }
  async function load(query='') {
    const ticket=++generation
    loading.value=true;error.value=''
    try {
      const value=await api(root+'/rules')
      const result=query.trim()?await api(root+'/rules/search?q='+encodeURIComponent(query.trim())+'&diversify=false'):value
      if(!disposed&&ticket===generation){catalog.value=value;items.value=result.items;await select(result.items[0])}
    }catch(e){if(!disposed&&ticket===generation){error.value=e.message;catalog.value=null;items.value=[];await select(null)}}
    finally{if(ticket===generation)loading.value=false}
  }
  return {catalog,items,selected,reviews,error,detailError,loading,detailLoading,load,select,dispose(){disposed=true;generation++;detailGeneration++}}
}
export function useMethodRules(){const ctl=createMethodRules();onMounted(()=>ctl.load());onUnmounted(ctl.dispose);return ctl}
