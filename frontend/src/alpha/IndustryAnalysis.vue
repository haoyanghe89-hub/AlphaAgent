<script setup>
import {computed,ref} from 'vue'
import PrototypeChart from './PrototypeChart.vue'
import {flowNumber} from '../utils/decisionChartOptions.js'
const props=defineProps({data:{type:Object,required:true},developer:Boolean})
const emit=defineEmits(['evidence'])
const selected=ref(20)
const industry=computed(()=>props.data.industry_report)
const attribution=computed(()=>props.data.attribution_report)
const analysis=computed(()=>props.data.report.industry_analysis)
const explanation=computed(()=>props.data.report.attribution_analysis)
const window=computed(()=>attribution.value.windows.find(w=>w.window===selected.value))
const comparison=computed(()=>[
  {key:'security_return',label:'个股'}, {key:'market_return',label:'沪深三百'}, {key:'proxy_return',label:'半导体主题ETF代理'},
].map(s=>({...s,value:window.value[s.key]})))
const chart=computed(()=>({kind:'industry_comparison',title:'个股、市场与半导体主题代理的同期涨跌比较',chart_type:'bar',x:['同期涨跌（%）'],series:comparison.value.map(s=>({name:s.label,data:[s.value==null?null:Number(s.value)*100],signed:true}))}))
const displayDate=value=>new Date(value).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'})
const gapLabel=channel=>({SEMI:'本次未取得设备销售原始发布',WSTS:'本次未取得芯片销售原始发布',CNINFO_DEMAND:'制造端公司原文覆盖不足',CNINFO_EVENTS:'同窗公司披露目录覆盖不足',THEME_PROXY:'主题代理价格窗口不完整'}[channel] || '此项资料覆盖不足')
</script>
<template>
  <section class="panel rep-section industry-analysis" data-industry-analysis>
    <div class="section-heading"><h2>行业变化，能否传到这家公司</h2><span class="tag tag-warn">条件传导</span></div>
    <p class="industry-summary">{{ analysis.summary }}</p>
    <p class="industry-reading"><b>公司连接</b>{{ analysis.company_link }}</p>
    <p class="industry-reading"><b>资金对照</b>{{ analysis.funding_crosscheck }}</p>
    <div class="industry-chain"><article v-for="edge in industry.chain" :key="edge.edge_id"><span class="chain-path">{{ edge.from_label }} <span aria-hidden="true">→</span> {{ edge.to_label }}</span><span class="chain-status">待验证机制</span><p>{{ edge.mechanism }}</p><p class="condition-note">{{ edge.next_check }}</p><button v-for="id in edge.evidence_ids.slice(0,2)" :key="id" class="prediction-source" @click="emit('evidence',id)">查看相关披露</button><small v-if="!edge.evidence_ids.length">本环节直接证据仍缺项</small></article></div>
    <p class="industry-reading"><b>接下来验证</b>{{ analysis.next_check }}</p>
    <details class="industry-sources"><summary>行业背景与资料范围</summary><p>制造资料：{{ industry.manufacturer }}。传导连接是条件性假说，端点有披露不代表整条链已经成立。</p><article v-for="fact in industry.facts" :key="fact.evidence_id"><b>{{ fact.label }}</b> · {{ fact.basis==='ACTUAL_STATISTICS'?'已发布统计':'预测或实际值替换版本' }}<p>{{ fact.geography }} · {{ fact.unit_scope }}。{{ fact.aggregation }}</p><button class="prediction-source" @click="emit('evidence',fact.evidence_id)">查看原始发布</button></article><p v-for="gap in industry.gaps" :key="gap.channel">{{ gapLabel(gap.channel) }}。缺项不作为公司受益或价格归因的依据。</p><p v-for="text in industry.limitations" :key="text">{{ text }}</p></details>
  </section>
  <section class="panel rep-section attribution-analysis" data-attribution-analysis>
    <div class="section-heading"><h2>最近涨跌，与行业还是公司有关</h2><span class="tag tag-warn">竞争解释</span></div>
    <p class="industry-summary">{{ explanation.summary }}</p>
    <div class="flow-tabs" role="tablist" aria-label="价格比较窗口"><button v-for="n in [5,20,60]" :key="n" class="btn" role="tab" :aria-selected="selected===n" @click="selected=n">近 {{ n }} 个交易日</button></div>
    <p class="condition-note">{{ window.sessions[0] || '日历缺项' }} — {{ window.sessions.at(-1) || '缺项' }} · 收盘基准 {{ window.baseline_date || '缺项' }} · 未复权价格</p>
    <div class="price-comparison"><article v-for="item in comparison" :key="item.key"><span>{{ item.label }}</span><strong>{{ flowNumber(item.value,'ratio') }}</strong><button v-if="item.value!=null" class="prediction-source" @click="emit('evidence',`EV-ATTR-${selected}-${item.key}`)">查看计算依据</button></article></div>
    <PrototypeChart :block="chart" :height="225" :empty="comparison.every(s=>s.value==null)?'本窗口无完整价格数据':''"/>
    <p class="condition-note">主题 ETF 只是跨环节价格代理，对应细分行业指数仍缺项。个股减市场的观察差：{{ flowNumber(window.relative_market,'percentage_points') }}；差值不能称为公司事件贡献。除权息尚未核定。</p>
    <p class="industry-reading"><b>可能的解释</b>{{ explanation.main_explanation }}</p>
    <p v-for="(text,index) in explanation.alternatives" :key="text" class="industry-reading"><b>{{ index===0?'还可能是':'另一个解释' }}</b>{{ text }}</p>
    <details class="event-timeline" open><summary>同一窗口还有哪些公司披露（{{ window.events.length }}）</summary><p v-if="!window.events.length" class="condition-note">本次有限目录未发现可在此窗口开始观察的披露，不代表没有其他消息。</p><article v-for="event in window.events" :key="event.event_id"><div><time>{{ displayDate(event.published_at) }}</time><button class="prediction-source" @click="emit('evidence',event.evidence_ids[0])">{{ event.title }}</button></div><p class="condition-note">从 {{ event.effective_session }} 开始观察；仅有公告日期，不能解释发布前的涨跌。标题不代表正文结论。</p></article></details>
    <p class="industry-reading"><b>如何检验</b>{{ explanation.next_check }}</p>
    <details v-if="developer" class="industry-development"><summary>开发核验：原始覆盖、传导与事件窗口</summary><pre>{{ JSON.stringify({industry,attribution,analysis,explanation,snapshot:data.industry_snapshot,calculation_ref:data.industry_calculation_ref},null,2) }}</pre></details>
  </section>
</template>
<style scoped>
.industry-summary{font-size:18px;line-height:1.9;max-width:950px}.industry-reading{line-height:1.9;margin:16px 0}.industry-reading b{display:inline-block;min-width:100px;color:var(--text-2);font-weight:500}.industry-chain{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin:24px 0}.industry-chain article{border:1px solid var(--border);padding:20px;line-height:1.8}.chain-path{display:block;font-size:15px}.chain-status{font-size:11px;color:var(--text-3)}.industry-chain p{font-size:13px}.industry-chain small{color:var(--text-3)}.industry-sources,.industry-development,.event-timeline{border-top:1px solid var(--border);padding:18px 0;font-size:12px;line-height:1.9}.industry-sources summary,.event-timeline summary,.industry-development summary{cursor:pointer;color:var(--text-2)}.industry-sources article,.event-timeline article{padding:14px 0;border-bottom:1px solid var(--border)}.event-timeline time{display:inline-block;min-width:95px;color:var(--text-3)}.price-comparison{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;padding:20px 0}.price-comparison article{display:flex;flex-direction:column;align-items:flex-start;gap:10px}.price-comparison span{font-size:12px;color:var(--text-2)}.price-comparison strong{font:24px 'JetBrains Mono',Consolas,monospace}@media(max-width:700px){.industry-chain{grid-template-columns:1fr}.industry-chain article{padding:16px}.price-comparison{gap:10px}.price-comparison strong{font-size:19px}.industry-reading b{display:block}.industry-summary{font-size:16px}.event-timeline time{display:block}.price-comparison .prediction-source{font-size:11px}}
</style>
