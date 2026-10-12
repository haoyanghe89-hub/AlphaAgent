<script setup>
import { number, dateTime } from './model.js'
defineProps({ audit:Object, acquisition:Object, status:String, inherited:Boolean })
const states = { RUNNING:'采集中', COMPLETED:'采集核对完成', FAILED:'采集失败', CANCELLED:'已停止', INTERRUPTED:'已中断' }
</script>
<template><section v-if="audit" class="panel rep-section" aria-label="官方基准核对结果">
  <h2>沪深300官方基准核对 <span class="tag tag-warn">整体未验证</span></h2>
  <p v-if="inherited" class="condition-note">沿用父版本核对 · {{ audit.sources.length }} 份官方响应；日历 {{ audit.expected_session_count }} 个交易日，结果与来源保持原版本。</p>
  <p v-else class="condition-note">{{ audit.start }} ~ {{ audit.end }}。{{ states[status] || status }} · 已请求 {{ acquisition?.calls_started ?? 0 }} 次，冻结 {{ audit.sources.length }} 份官方响应；日历 {{ audit.expected_session_count }} 个交易日。</p>
  <p v-if="!inherited && acquisition?.error" class="error-text" role="alert">采集未完成：{{ acquisition.error }}。成功冻结的来源已保留。</p>
  <div class="report-table-scroll"><table class="data"><thead><tr><th>基准口径</th><th>代码</th><th>交易日</th><th>缺少</th><th>范围</th><th>独立核对</th></tr></thead><tbody><tr v-for="series in audit.series" :key="series.code"><td>{{ series.return_basis === 'price_index' ? '价格指数' : '全收益指数' }}</td><td class="num">{{ series.code }}</td><td>{{ series.row_count }}</td><td>{{ series.missing_session_count }}</td><td>{{ series.start || '—' }} ~ {{ series.end || '—' }}</td><td><span class="tag" :class="series.independent_check === 'MATCH' ? '' : 'tag-warn'">{{ series.independent_check }}</span></td></tr></tbody></table></div>
  <p class="condition-note">价格指数与父版本BaoStock收盘价逐日核对，容差预设0.005指数点。MATCH {{ audit.check_counts.MATCH }} 日／CONFLICT {{ audit.check_counts.CONFLICT }} 日／缺基准 {{ audit.check_counts.UNAVAILABLE }} 日。全收益包含分红再投资，单独冻结，不与价格指数混拼，也不当作策略净值。</p>
  <details class="bt-source-details"><summary>查看逐日价格核对（{{ audit.checks.length }}）</summary><div class="report-table-scroll"><table class="data"><thead><tr><th>交易日</th><th>父版本价格</th><th>官方价格</th><th>状态</th></tr></thead><tbody><tr v-for="check in audit.checks" :key="check.date"><td>{{ check.date }}</td><td>{{ check.value == null ? '—' : number(check.value, 4) }}</td><td>{{ number(check.reference, 2) }}</td><td>{{ check.status }}</td></tr></tbody></table></div></details>
  <details class="bt-source-details"><summary>查看官方来源与哈希（{{ audit.sources.length }}）</summary><div v-for="source in audit.sources" :key="source.packet_sha256" class="bt-check-item"><div>{{ source.query.code }} · {{ source.query.start }} ~ {{ source.query.end }} · {{ source.row_count }} 行</div><p><a :href="source.source_url + '?indexCode=' + source.query.code + '&amp;startDate=' + source.query.start.replaceAll('-', '') + '&amp;endDate=' + source.query.end.replaceAll('-', '')" target="_blank" rel="noopener noreferrer">中证指数官方原始响应</a> · 抓取 {{ dateTime(source.retrieved_at) }}</p><p class="num">原文 SHA256：{{ source.document_sha256 }}<br />响应 SHA256：{{ source.packet_sha256 }}</p></div></details>
  <ul class="bt-limitations"><li v-for="gap in audit.gaps" :key="gap">{{ gap }}</li></ul>
</section></template>
