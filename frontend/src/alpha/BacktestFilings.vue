<script setup>
import { number, dateTime } from './model.js'
defineProps({ audit:Object, acquisition:Object, status:String, inherited:Boolean })
const labels = { MATCH:'一致', CONFLICT:'数值冲突', METHOD_DIFFERENCE:'口径不同', UNAVAILABLE:'缺少基准' }
const dates = { BEFORE_SIGNAL:'日期早于信号日 · 版本待核实', SAME_DAY_TIME_UNKNOWN:'信号日同日 · 披露时间未知', AFTER_SIGNAL:'晚于信号日' }
const states = { RUNNING:'正在核对', COMPLETED:'核对完成', FAILED:'核对失败', CANCELLED:'核对已停止', INTERRUPTED:'核对已中断' }
const value = amount => amount == null ? '—' : number(amount, 2)
const comparison = (amount, basis) => amount == null ? '—' : basis.includes('roe') || basis.includes('weighted') || basis.includes('average') || basis.includes('ytd') ? number(Number(amount)*100, 4) + '%' : value(amount) + ' 元'
</script>
<template><section v-if="audit" class="panel rep-section" aria-label="官方财报口径核对结果">
  <h2>官方财报原文与口径核对 <span class="tag tag-warn">PARTIAL · 未验证</span></h2>
  <p class="condition-note">{{ audit.scope.name }} · {{ audit.scope.signal_year }} 年信号。<template v-if="inherited">沿用父版本核对 · {{ audit.sources.length }} 份原文响应。</template><template v-else>{{ states[status] || status }} · 已请求 {{ acquisition?.calls_started ?? 0 }} 次，冻结 {{ audit.sources.length }} 份原文响应。</template>官方来源定位、单项数值一致与严格历史可得是不同检查。</p>
  <p v-if="!inherited && acquisition?.error" class="error-text" role="alert">原文采集未完成：{{ acquisition.error }}。已保留成功取得的原文，可重新核对。</p>
  <h3 class="input-subtitle">官方披露基础数值（人民币元）</h3>
  <div class="table-scroll"><table class="data filing-data"><thead><tr><th>报告期末</th><th>合并净利润</th><th>归母净利润</th><th>少数股东损益</th><th>营业收入</th><th>原文定位</th></tr></thead><tbody><tr v-for="doc in audit.documents" :key="doc.profile_id"><td>{{ doc.period_end }}</td><td>{{ value(doc.consolidated_net_profit) }}</td><td>{{ value(doc.parent_net_profit) }}</td><td>{{ value(doc.minority_profit) }}</td><td>{{ value(doc.revenue) }}</td><td><a :href="doc.source_url + '#page=' + doc.source_pages.summary" target="_blank" rel="noopener noreferrer">摘要第{{ doc.source_pages.summary }}页</a><br /><a :href="doc.source_url + '#page=' + doc.source_pages.income" target="_blank" rel="noopener noreferrer">利润表第{{ doc.source_pages.income }}页</a></td></tr><tr v-if="!audit.documents.length"><td colspan="6">尚未冻结官方原文，未生成财务复算。</td></tr></tbody></table></div>
  <p class="condition-note">官方货币单位为人民币千元，上表统一乘1000换算为元；合并利润 = 归母利润 + 少数股东损益，分别保留，不互相替代。</p>
  <h3 class="input-subtitle">披露日期与版本档案</h3>
  <div v-for="doc in audit.documents" :key="doc.profile_id" class="bt-check-item"><div>{{ doc.period_end }} · {{ doc.publication_date }} <span class="tag tag-warn">{{ dates[doc.date_observation] }}</span></div><p>当前已定位官方PDF；最初及各次修订档案仍不完整，未取得严格可得时间。</p></div>
  <h3 class="input-subtitle">逐项口径核对</h3>
  <div class="table-scroll"><table class="data filing-checks"><thead><tr><th>报告期末／核对项</th><th>供应商值或自算值</th><th>官方参考值</th><th>状态</th></tr></thead><tbody><tr v-for="(check,index) in audit.checks" :key="index"><td>{{ check.period_end }}<br />{{ check.name }}</td><td>{{ comparison(check.value, check.basis) }}</td><td>{{ comparison(check.reference, check.reference_basis) }}</td><td><span class="tag" :class="check.status === 'MATCH' ? '' : 'tag-warn'">{{ labels[check.status] }} · {{ check.status }}</span></td></tr></tbody></table></div>
  <p class="condition-note">货币舍入容差预设为1000元；同比容差为0.005个百分点。口径不同直接标记，不因为数值接近就认定一致；单项MATCH不将整个指标升级为VERIFIED。</p>
  <h3 class="input-subtitle">官方基础数值复算（研究用，未用于历史选股）</h3>
  <div class="bt-check-item"><div><strong>归母净利润 TTM</strong><span class="tag tag-warn">独立TTM基准缺失 · UNVERIFIED</span></div><p>{{ audit.derived.ttm_parent_profit.formula }} = {{ value(audit.derived.ttm_parent_profit.value) }} 元</p><p>{{ audit.derived.ttm_parent_profit.reason }}</p></div>
  <div class="bt-check-item"><div><strong>营业收入同比</strong><span class="tag tag-warn">UNVERIFIED</span></div><p>{{ audit.derived.revenue_yoy.formula }} = {{ audit.derived.revenue_yoy.value == null ? '—' : number(Number(audit.derived.revenue_yoy.value)*100, 4) + '%' }}</p><p>使用所选三期原文数值，仅用于口径核验；信号日是否可得仍须独立检查。</p></div>
  <details class="bt-source-details"><summary>查看官方原文哈希与复算记录（{{ audit.sources.length }}）</summary><div v-for="source in audit.sources" :key="source.profile_id" class="bt-check-item"><div>{{ source.profile_id }} · 抓取 {{ dateTime(source.retrieved_at) }}</div><p class="num">PDF SHA256：{{ source.document_sha256 }}<br />响应 SHA256：{{ source.packet_sha256 }}</p></div><p class="num">输入哈希：{{ audit.derived.input_hash }}<br />输出哈希：{{ audit.derived.output_hash }}</p></details>
  <ul class="bt-limitations"><li v-for="gap in audit.gaps" :key="gap">{{ gap }}</li></ul>
</section></template>
