<script setup>
import { dateTime } from './model.js'
defineProps({ audit:Object, acquisition:Object, status:String, inherited:Boolean })
const observations = { BEFORE_SIGNAL:'披露日期早于信号日 · 原版本待核实', SAME_DAY_TIME_UNKNOWN:'与信号日同日 · 披露时间未知', AFTER_SIGNAL:'晚于信号日 · 不可用于该信号', MISSING_DATE:'披露日期缺失' }
const queryNames = { stock_daily:'不复权日线', dividends:'分红送转（除权年份）', profit:'季度财报样本' }
const statuses = { RUNNING:'正在核验', COMPLETED:'核验完成', FAILED:'核验失败', CANCELLED:'核验已停止', INTERRUPTED:'核验已中断' }
const errors = { TimeoutError:'公开数据源查询超时。已保留成功获取的输入，可重新核验并保存新版本。', CancelledError:'采集已停止，保留此前成功获取的输入。' }
const show = value => value == null || value === '' ? '—' : value
</script>
<template><section v-if="audit" class="panel rep-section" aria-label="股票历史输入核验结果">
  <h2>股票历史输入核验 <span class="tag tag-warn">PARTIAL · 未验证</span></h2>
  <p class="condition-note">{{ audit.scope.name }}（{{ audit.scope.provider_code }}） · {{ audit.scope.start }} ~ {{ audit.scope.end }}。{{ audit.scope.signal_year }} 年信号日 {{ audit.scope.signal_day }}，拟执行日 {{ audit.scope.execution_day }}。只核验一只股票及一个年度的财报样本。</p>
  <p v-if="inherited" class="condition-note">沿用父版本已完成的股票输入核验，以下 {{ audit.sources.length }} 份冻结来源及结果保持不变；官方原文核对进度见下一节。</p>
  <p v-else class="condition-note">{{ statuses[status] || status }} · 已请求 {{ acquisition?.calls_started ?? 0 }} / {{ acquisition?.max_calls ?? 24 }} 次，冻结 {{ acquisition?.sources_frozen ?? 0 }} 份响应。完整覆盖仍不等于数据已通过准入。</p>
  <p v-if="!inherited && acquisition?.error" class="error-text" role="alert">{{ errors[acquisition.error] || '采集未完成：' + acquisition.error }}（{{ queryNames[acquisition.current_query?.kind] || '查询' }} {{ acquisition.current_query?.year || acquisition.current_query?.start || '' }}）</p>
  <h3 class="input-subtitle">原始价格与交易状态</h3>
  <div class="table-scroll"><table class="data"><thead><tr><th>日线记录</th><th>日历应有</th><th>缺少日期</th><th>停牌日</th><th>交易状态未知</th><th>ST状态未知</th></tr></thead><tbody><tr><td>{{ audit.prices.row_count }}</td><td>{{ audit.prices.expected_session_count }}</td><td>{{ audit.prices.missing_session_count }}</td><td>{{ audit.prices.suspended_count }}</td><td>{{ audit.prices.unknown_status_count }}</td><td>{{ audit.prices.unknown_st_count }}</td></tr></tbody></table></div>
  <p class="condition-note">{{ {COVERED:'日期覆盖与冻结日历一致',INCOMPLETE:'日期覆盖不足',UNAVAILABLE:'尚无原始价格'}[audit.prices.coverage] }} · 不复权 · 价格：人民币元，成交量：股。OHLC、停牌或ST标记不能证明开盘可成交。</p>
  <h3 class="input-subtitle">分红与送转明细</h3>
  <div class="table-scroll"><table class="data input-events"><thead><tr><th>除权日</th><th>登记日</th><th>现金支付日</th><th>股票上市日</th><th>税前现金（元/股）</th><th>送股（股/股）</th><th>转增（股/股）</th></tr></thead><tbody><tr v-for="(event,index) in audit.actions.events" :key="index"><td>{{ show(event.ex_date) }}</td><td>{{ show(event.register_date) }}</td><td>{{ show(event.pay_date) }}</td><td>{{ show(event.stock_listing_date) }}</td><td>{{ show(event.cash_per_share_before_tax) }}</td><td>{{ show(event.bonus_shares_per_share) }}</td><td>{{ show(event.reserve_shares_per_share) }}</td></tr><tr v-if="!audit.actions.events.length"><td colspan="7">供应商暂未返回区间内分红送转记录；不证明无公司行为。</td></tr></tbody></table></div>
  <p class="condition-note">{{ audit.actions.event_count }} 条区间内记录，{{ audit.actions.empty_query_count }} 次查询返回空记录。缺值显示“—”，不补零；上述记录未覆盖完整配股、拆合股、退市及税款处理。</p>
  <h3 class="input-subtitle">财报披露与可得性观察</h3>
  <div class="table-scroll"><table class="data input-financials"><thead><tr><th>报告期末</th><th>供应商披露日期</th><th>相对信号日</th><th>最初及修订版本</th></tr></thead><tbody><tr v-for="(item,index) in audit.financials.observations" :key="index"><td>{{ item.period_end }}</td><td>{{ show(item.publication_date) }}</td><td>{{ observations[item.date_observation] }}</td><td>档案未接入</td></tr><tr v-if="!audit.financials.observations.length"><td colspan="4">尚无财报样本；不证明信号日可得。</td></tr></tbody></table></div>
  <p class="condition-note">样本为信号年前一年Q1、全年Q4及信号年Q1；{{ audit.financials.empty_query_count }} 次查询返回空记录。日期早于信号日也不能替代最初披露及修订档案，没有升级为严格时点数据，未自算PE、ROE、TTM。</p>
  <details class="bt-source-details"><summary>查看股票输入来源与哈希（{{ audit.sources.length }}）</summary><div v-for="source in audit.sources" :key="source.sha256" class="bt-check-item"><div>{{ source.provider }} / {{ queryNames[source.query.kind] }} <span class="tag">PARTIAL · 未验证</span></div><p>{{ source.query.code }} · {{ source.query.start ? source.query.start + ' ~ ' + source.query.end : source.query.year + ' 年' + (source.query.quarter ? ' Q' + source.query.quarter : '') }} · {{ source.row_count }} 行 · 抓取 {{ dateTime(source.retrieved_at) }}</p><p class="num">{{ source.sha256 }}</p></div></details>
  <ul class="bt-limitations"><li v-for="gap in audit.gaps" :key="gap">{{ gap }}</li></ul>
</section></template>
