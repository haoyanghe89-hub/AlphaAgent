<script setup>
import { dateTime } from './model.js'
defineProps({ audit:Object, acquisition:Object, status:String })
const states = {RUNNING:'采集中',COMPLETED:'公告核对完成',FAILED:'采集失败',CANCELLED:'已停止',INTERRUPTED:'已中断'}
const families = {issuer_disclosure:'发行人披露 · 深交所原文',cni_index_notice:'国证指数调整公告'}
</script>
<template><section v-if="audit" class="panel rep-section notice-audit" aria-label="历史改码公告核对结果">
  <h2>历史改码与身份延续 <span class="tag tag-warn">PARTIAL · 单项事件取证</span></h2>
  <p class="condition-note">{{ states[status] || status }} · 已请求 {{ acquisition?.calls_started ?? 0 }} 次，冻结 {{ audit.documents.length }} 份原文。只有两类来源共同确认，才关联新旧代码的观察计划。</p>
  <p v-if="acquisition?.error" class="error-text" role="alert">采集未完成：{{ acquisition.error }}。已取得的原文保留，单一来源不确认身份延续。</p>
  <div class="report-table-scroll"><table class="data"><thead><tr><th>旧代码</th><th>新代码</th><th>生效日</th><th>交叉核对</th><th>时间精度</th></tr></thead><tbody><tr v-for="event in audit.events" :key="event.old_code"><td class="num">{{ event.old_code }}</td><td class="num">{{ event.new_code }}</td><td>{{ event.effective_day || '待核实' }}</td><td><span :class="['tag',event.status === 'MATCH' ? 'tag-good' : 'tag-warn']">{{ event.status }}</span></td><td>披露日 · 未取得准确时刻</td></tr></tbody></table></div>
  <details class="bt-source-details"><summary>查看公告原文、页码与哈希（{{ audit.documents.length }}）</summary><div v-for="source in audit.documents" :key="source.packet_sha256" class="bt-check-item"><a :href="source.source_url" target="_blank" rel="noopener noreferrer">{{ families[source.source_family] }}</a><p>披露日 {{ source.publication_date }} · 取证页 {{ source.source_pages.join('、') }} · 抓取 {{ dateTime(source.retrieved_at) }}</p><p class="num notice-hash">原文 SHA256：{{ source.document_sha256 }}<br>响应 SHA256：{{ source.packet_sha256 }}</p></div></details>
  <ul class="bt-limitations"><li v-for="gap in audit.gaps" :key="gap">{{ gap }}</li></ul>
</section></template>
<style scoped>.notice-audit table {min-width:540px}.notice-hash {overflow-wrap:anywhere}</style>
