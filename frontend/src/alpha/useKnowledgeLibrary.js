import { onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'

export const knowledgeRoot = '/quant/knowledge'
export const documentPath = doc => knowledgeRoot + '/documents/' + encodeURIComponent(doc.document_id) + '/versions/' + encodeURIComponent(doc.version)

export function createKnowledgeLibrary({ api=request }={}) {
  const catalog=ref(null), items=ref([]), selected=ref(null), error=ref(''), detailError=ref(''), loading=ref(false), detailLoading=ref(false)
  let disposed=false, generation=0, detailGeneration=0, latestQuery='', latestCategory=''
  async function select(doc) {
    const ticket=++detailGeneration
    selected.value=null; detailError.value=''
    if (!doc || disposed) { detailLoading.value=false; return }
    detailLoading.value=true
    try { const value=await api(documentPath(doc)); if (!disposed && ticket===detailGeneration) selected.value=value }
    catch(e) { if (!disposed && ticket===detailGeneration) detailError.value=e.message }
    finally { if(ticket===detailGeneration) detailLoading.value=false }
  }
  function apply(list) {
    items.value=list
    if (!selected.value || !list.some(doc=>doc.document_id===selected.value.document_id && doc.version===selected.value.version)) select(list[0])
  }
  async function search(query='', category='') {
    latestQuery=query.trim(); latestCategory=category
    const ticket=++generation, q=latestQuery, c=latestCategory
    loading.value=true; error.value=''
    try {
      const list=q ? (await api(knowledgeRoot+'/search?q='+encodeURIComponent(q)+(c?'&category='+encodeURIComponent(c):''))).items : (catalog.value?.documents||[]).filter(doc=>!c || doc.category===c)
      if (!disposed && ticket===generation) apply(list)
    } catch(e) { if(!disposed && ticket===generation) { error.value=e.message; if(e.status===409){items.value=[];select(null)} } }
    finally { if(ticket===generation)loading.value=false }
  }
  async function load() {
    const ticket=++generation
    loading.value=true; error.value=''
    try {
      const value=await api(knowledgeRoot+'/catalog')
      if(!disposed && ticket===generation) { catalog.value=value; await search(latestQuery,latestCategory) }
    } catch(e) { if(!disposed && ticket===generation) { error.value=e.message; if(e.status===409){catalog.value=null;items.value=[];select(null)} } }
    finally { if(ticket===generation)loading.value=false }
  }
  return {catalog,items,selected,error,detailError,loading,detailLoading,load,search,select,dispose(){disposed=true;generation++;detailGeneration++}}
}
export function useKnowledgeLibrary(){const ctl=createKnowledgeLibrary();onMounted(ctl.load);onUnmounted(ctl.dispose);return ctl}
