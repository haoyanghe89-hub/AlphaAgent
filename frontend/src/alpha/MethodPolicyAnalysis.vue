<script setup>
import {computed} from 'vue'
const props=defineProps({data:{type:Object,required:true},developer:Boolean})
const emit=defineEmits(['evidence'])
const routing=computed(()=>props.data.method_routing || {})
const profile=computed(()=>routing.value.profile || {})
const method=computed(()=>props.data.report?.method_assessment || {})
const policy=computed(()=>props.data.report?.policy_assessment || {})
const policyReport=computed(()=>props.data.policy_report || {})
const policies=computed(()=>policyReport.value.policies || [])
const notes=computed(()=>props.data.knowledge_notes || [])
const candidates=computed(()=>routing.value.candidates || [])
const executions=computed(()=>routing.value.execution_records || [])
const statusLabel=value=>({SELECTED:'已选择',PRIMARY:'主方法',AUXILIARY:'辅助核查',ELIGIBLE:'符合准入',AVAILABLE:'可使用',ENABLED:'已启用',EXECUTABLE_LIMITED:'已实现有限模块',REFERENCE_ONLY:'仅研究参考',CONDITIONAL:'条件启用',CONDITIONAL_ENABLED:'条件启用',OBSERVATION_ONLY:'仅观察核查',MISSING_INPUTS:'必要输入缺项',WRONG_ROLE:'业务类型不适用',WRONG_SCOPE:'研究范围不适用',NOT_SELECTED:'本次未选择',BLOCKED:'输入不满足',UNAVAILABLE:'资料未取得',NOT_APPLICABLE:'不适用',NOT_IMPLEMENTED:'尚未实现',EXECUTED:'已执行',EXECUTED_LIMITED:'已执行有限核查',EXECUTED_OBSERVATION:'已执行观察核查',COMPLETED:'已执行',APPROVED:'复核通过',PARTIAL:'部分覆盖',BACKGROUND_ONLY:'仅作背景',PUBLIC_EXCERPT_VERIFIED:'本次已封存公开片段',DEVELOPMENT_PUBLIC_EXCERPT:'开发时已读公开片段',UNKNOWN:'状态待核对',DRAFT:'征求意见',CONSULTATION:'征求意见',FORMAL:'正式文件',NOT_YET_EFFECTIVE:'尚未生效',REPEALED:'已废止',EFFECTIVE:'已生效',SUPERSEDED:'已替代',EXPIRED:'已到期'}[value] || value || '未记录')
const strings=value=>Array.isArray(value)?value.join('；'):value || '未记录'
const safeHref=value=>{try{const url=new URL(value);return url.protocol==='https:'?url.href:null}catch{return null}}
const sourcePolicy=id=>policies.value.find(row=>row.evidence_id===id || row.evidence_ids?.includes(id))
const sourceLabel=id=>({CN:'中国政策',US:'海外限制'}[sourcePolicy(id)?.jurisdiction] || '政策')
const noteSources=note=>Array.isArray(note.sources)?note.sources:note.source?[note.source]:[]
const policyKind=value=>({FORMAL_POLICY:'政策文件',AGENCY_EXPLANATION:'机构说明',REGULATORY_COMPILATION:'法规汇编',CONSULTATION:'征求意见',PORTAL:'官方入口'}[value] || '官方资料')
const selectionLabel=id=>routing.value.primary_method_id===id?'本次主方法':routing.value.auxiliary_method_ids?.includes(id)?'本次辅助':routing.value.primary_method_id?'本次未选':'尚未选择'
const noteCoverage=note=>note.read_scope || note.coverage_description || (note.coverage!=='PUBLIC_EXCERPT_VERIFIED'?note.coverage:null) || strings(note.limitations)
const noteLocation=value=>value && typeof value==='object' && !Array.isArray(value)?[value.section,...(value.anchors || [])].filter(Boolean).join('；'):strings(value)
</script>
<template>
  <section class="panel rep-section method-application" data-method-application>
    <h2>这家公司，重点怎么看</h2>
    <p v-if="profile.role_label" class="condition-note">业务画像：{{ profile.role_label }}</p>
    <p class="method-policy-summary">{{ method.summary || '本次方法应用尚无可展示的复核结论。' }}</p>
    <button v-for="(id,index) in method.evidence_ids || []" :key="id" class="prediction-source" @click="emit('evidence',id)">查看业务依据 {{ index+1 }} ↗</button>
  </section>
  <section class="panel rep-section policy-application" data-policy-application>
    <h2>政策会带来什么影响</h2>
    <p class="method-policy-summary">{{ policy.summary || '本次未取得可支持公司影响判断的政策条款。' }}</p>
    <p v-if="policy.company_link" class="method-policy-reading"><b>与公司有什么关系</b>{{ policy.company_link }}</p>
    <p v-if="policy.opposing_risk" class="method-policy-reading"><b>还要警惕什么</b>{{ policy.opposing_risk }}</p>
    <p v-if="policy.next_check" class="method-policy-reading"><b>接下来验证</b>{{ policy.next_check }}</p>
    <p v-if="!policies.length" class="condition-note policy-gap">本次政策原文覆盖不足，不能据此认定公司已受益或没有政策风险。</p>
    <button v-for="(id,index) in policy.evidence_ids || []" :key="id" :title="sourcePolicy(id)?.title" class="prediction-source policy-citation" @click="emit('evidence',id)">查看{{ sourceLabel(id) }}条款 {{ index+1 }} ↗</button>
    <details v-if="policies.length" class="method-policy-details policy-originals"><summary>政策原文与适用范围</summary><article v-for="(item,index) in policies" :key="item.policy_id || index" class="policy-review"><h3>{{ item.title }}</h3><p>{{ item.issuer }} · {{ item.jurisdiction }} · {{ policyKind(item.document_kind) }} · {{ statusLabel(item.document_status) }}</p><p>{{ strings(item.scope) }}</p><p class="condition-note">公司具体资格和条款适用性仍需核对，原文存在不代表影响已经兑现。</p><button v-for="id in item.evidence_ids || []" :key="id" class="prediction-source" @click="emit('evidence',id)">查看定位条款 ↗</button></article></details>
  </section>
  <section v-if="developer" class="panel rep-section method-policy-development" data-method-policy-development>
    <h2>方法与政策开发核验</h2>
    <p class="condition-note">方法笔记只覆盖实际读取的资料片段，不代表整本书已读完；适配与复核通过不代表投资胜率已验证。</p>
    <details class="method-policy-details"><summary>业务识别与方法选择</summary>
      <p>业务类型：{{ profile.role_label || '未知' }}；{{ strings(profile.limitations) }}</p>
      <button v-for="id in profile.evidence_ids || []" :key="id" class="prediction-source" @click="emit('evidence',id)">核对业务原文 {{ id }}</button>
      <p>选择理由：{{ routing.selection_reason || '未记录' }}</p>
      <div class="method-review-table"><table><caption>方法准入与本次选择</caption><thead><tr><th scope="col">方法</th><th scope="col">模块实现</th><th scope="col">本次角色</th><th scope="col">准入与原因</th><th scope="col">输入缺项</th></tr></thead><tbody><tr v-for="item in candidates" :key="item.method_id"><th scope="row">{{ item.title }}<small>{{ item.method_id }}</small></th><td>{{ statusLabel(item.implementation_status) }}</td><td>{{ selectionLabel(item.method_id) }}</td><td>{{ statusLabel(item.status) }}<p>{{ item.reason }}</p></td><td>{{ item.missing_inputs?.length?strings(item.missing_inputs):'无已记录缺项' }}</td></tr></tbody></table></div>
      <p v-if="!candidates.length">没有保存方法候选记录。</p>
    </details>
    <details class="method-policy-details"><summary>实际执行、输入和复核</summary>
      <article v-for="item in executions" :key="item.method_id" class="method-execution"><h3>{{ item.title }} · {{ statusLabel(item.status) }}</h3><p>输入证据：{{ strings(item.input_evidence_ids) }}</p><p>输出位置：{{ strings(item.output_paths) }}；复核记录：{{ strings(item.review_ids) }}</p><p>知识笔记：{{ strings(item.source_note_ids) }}</p><p>{{ strings(item.limitations) }}</p></article>
      <p v-if="!executions.length">没有保存实际执行记录，不能把方法登记视作执行完成。</p>
    </details>
    <details class="method-policy-details"><summary>知识覆盖、原始来源与政策状态</summary>
      <article v-for="(note,index) in notes" :key="note.note_id || note.knowledge_note_id || index" class="knowledge-note"><h3>{{ note.title || note.note_id || '方法笔记' }}</h3><p>{{ note.authors?.join('、') || note.author || note.source_author }} · {{ statusLabel(note.coverage_status || note.coverage || note.status) }}</p><p>{{ noteCoverage(note) }}</p><p v-if="note.edition">资料版本：{{ note.edition }}</p><p v-if="note.summary">提炼：{{ note.summary }}</p><p v-if="note.source_locator">原文范围：{{ noteLocation(note.source_locator) }}</p><p v-if="note.actual_locators?.length">本次已定位 {{ note.actual_locators.length }} 个原文片段。</p><p v-if="note.forbidden_inferences?.length">禁止推断：{{ strings(note.forbidden_inferences) }}</p><template v-for="(source,n) in noteSources(note)" :key="n"><a v-if="safeHref(typeof source==='string'?source:source.url)" class="prediction-source" :href="safeHref(typeof source==='string'?source:source.url)" target="_blank" rel="noopener noreferrer">{{ typeof source==='object'?(source.title || '查看笔记原始资料'):'查看笔记原始资料' }}</a></template><a v-if="safeHref(note.source_url || note.url)" class="prediction-source" :href="safeHref(note.source_url || note.url)" target="_blank" rel="noopener noreferrer">查看笔记原始资料</a></article>
      <p v-if="!notes.length">本次没有保存方法知识笔记。</p>
      <article v-for="(item,index) in policies" :key="item.policy_id || item.evidence_id || index" class="policy-review"><h3>{{ item.title || '政策原文' }}</h3><p>{{ item.issuer }} · {{ item.jurisdiction }} · {{ statusLabel(item.document_status) }}</p><p>当前有效性：{{ statusLabel(item.current_validity) }}；修订检查：{{ statusLabel(item.amendment_status) }}；公司资格：{{ statusLabel(item.qualification_status) }}</p><p>{{ strings(item.limitations) }}</p><button v-for="id in item.evidence_ids || (item.evidence_id?[item.evidence_id]:[])" :key="id" class="prediction-source" @click="emit('evidence',id)">核对政策条款 {{ id }}</button></article>
      <p v-if="!policies.length">本次没有保存政策条款，保留覆盖缺项。</p>
      <details><summary>原始路由、知识与政策核验记录</summary><pre>{{ JSON.stringify({routing,notes,knowledge_snapshot:data.knowledge_snapshot,policy:policyReport,method_assessment:method,policy_assessment:policy},null,2) }}</pre></details>
    </details>
  </section>
</template>
<style scoped>
.method-policy-summary{font-size:18px;line-height:1.9;max-width:58em}.method-policy-reading{line-height:1.9;margin:18px 0;max-width:65em}.method-policy-reading b{display:block;font-size:12px;font-weight:500;color:var(--text-2);margin-bottom:4px}.method-policy-details{border-top:1px solid var(--border);padding:18px 0;line-height:1.9;font-size:13px}.method-policy-details summary{cursor:pointer;color:var(--text-2)}.method-review-table{overflow-x:auto;max-width:100%;margin:20px 0}.method-review-table table{width:100%;border-collapse:collapse;text-align:left;font-size:12px}.method-review-table caption{text-align:left;padding-bottom:12px;color:var(--text-2)}.method-review-table :is(th,td){padding:12px;vertical-align:top;border-bottom:1px solid var(--border);min-width:130px;overflow-wrap:anywhere}.method-review-table th{font-weight:500}.method-review-table small{display:block;color:var(--text-3);font-weight:400}.method-review-table p{margin:6px 0}.method-execution,.knowledge-note,.policy-review{border-bottom:1px solid var(--border);padding:16px 0;overflow-wrap:anywhere}.method-execution h3,.knowledge-note h3,.policy-review h3{font-size:14px;font-weight:500}.method-policy-development pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:65vh;overflow:auto}@media(max-width:700px){.method-policy-summary{font-size:16px}.method-policy-reading{margin:16px 0}.method-review-table :is(th,td){padding:10px}.method-policy-development{min-width:0}}
</style>
