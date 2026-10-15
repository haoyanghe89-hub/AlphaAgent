<script setup>
import {ref} from 'vue'
defineProps({chain:Object,outcomes:Object,busy:Boolean})
const emit=defineEmits(['observe','withdraw'])
const reason=ref('')
const labels={FAVORABLE:'偏有利',UNFAVORABLE:'偏不利',MIXED:'多空交织',UNDETERMINED:'旧版未给方向'}
const verdict=record=>['medium-term-2026-10-12-direction-v2','medium-term-2026-10-12-direction-v3','medium-term-2026-10-12-direction-v4','medium-term-2026-10-13-flows-v1','medium-term-2026-10-13-flows-v2','medium-term-2026-10-13-industry-v1','medium-term-2026-10-13-method-policy-v1'].includes(record.prompt_version) ? ({FAVORABLE:'倾向看多',UNFAVORABLE:'倾向看空'}[record.outlook] || '结论记录异常') : labels[record.outlook]
const stamp=value=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})
const latest=values=>values?.observations?.filter(r=>r.outcomes?.length).at(-1)
const withdrawal=values=>values?.observations?.find(r=>r.kind==='USER_WITHDRAWAL')
const status=(values,w)=>latest(values)?.outcomes.find(o=>o.window===w)
const percentage=v=>v==null?'—':(Number(v)*100).toFixed(2)+'%'
</script>
<template>
  <section v-if="chain?.current" class="panel rep-section"><div class="section-heading"><h2>保存观点，等待事实检验</h2><button class="btn" :disabled="busy" @click="emit('observe')">{{ busy?'正在核对…':'查看当前观察结果' }}</button></div><p class="condition-note">{{ stamp(chain.current.issued_at) }} 发布。原观点保留，更新研究将另存一份；未到期不显示实现收益。</p>
    <div class="forecast-windows"><section v-for="plan in outcomes?.schedule || []" :key="plan.window"><h3>{{ plan.window }} 个交易日</h3><p>{{ plan.due_session ? '观察日 '+plan.due_session : '观察日待核定' }}</p><strong>{{ ({PENDING:'等待观察到期',UNAVAILABLE:'到期数据不足',MATURE:'已有价格观察'}[status(outcomes,plan.window)?.maturity]) || '等待观察' }}</strong><p v-if="status(outcomes,plan.window)?.maturity==='MATURE'">价格变化 {{ percentage(status(outcomes,plan.window).security_return) }}<br>同期沪深三百 {{ percentage(status(outcomes,plan.window).market_return) }}</p><p v-if="plan.calendar_note" class="condition-note">{{ plan.calendar_note }}</p></section></div>
    <p class="condition-note">当前按未复权价格观察；行业基准尚缺，未完成全收益独立核对，不能当作正式策略绩效。经营催化是否兑现还需实际披露。</p>
    <p v-if="withdrawal(outcomes)" class="notice">已于 {{ stamp(withdrawal(outcomes).observed_at) }} 手动标记撤回：{{ withdrawal(outcomes).reason }}。原判断和后续观察继续保留。</p>
    <details v-else><summary>需要撤回这份观点时，留下原因</summary><form @submit.prevent="emit('withdraw',reason)"><label>撤回原因<input v-model.trim="reason" required maxlength="500" placeholder="说明你为什么撤回当前观点"></label><p class="condition-note">记录本次提交时间。该标记来自用户，事实是否已发生仍需原文核查。</p><button class="btn" :disabled="busy||!reason">标记撤回，保留原观点</button></form></details>
    <details><summary>查看最初观点与后续版本（{{ chain.versions.length }}）</summary><a v-for="record in chain.versions" :key="record.forecast_id" class="prediction-history" :href="'#/decision/'+record.run_id"><span>{{ stamp(record.issued_at) }}</span><strong>{{ verdict(record) }}</strong></a></details>
  </section>
</template>
<style>.forecast-windows{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;margin:24px 0}.forecast-windows section{padding:16px 0;border-top:1px solid var(--border)}.forecast-windows strong{color:var(--accent)}@media(max-width:700px){.forecast-windows{grid-template-columns:1fr;gap:8px}}</style>
