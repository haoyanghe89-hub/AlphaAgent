<script setup>
import {computed,ref,watch} from 'vue'
import PrototypeChart from './PrototypeChart.vue'
import {flowChartBlocks,flowNumber,divergenceLabel} from '../utils/decisionChartOptions.js'
const props=defineProps({data:{type:Object,required:true},developer:Boolean})
const emit=defineEmits(['evidence'])
const selected=ref(20),point=ref(null)
const report=computed(()=>props.data.flow_report)
const window=computed(()=>report.value.windows.find(w=>w.window===selected.value))
const assessment=computed(()=>props.data.report.flow_analysis?.find(a=>a.window===selected.value))
const metrics=computed(()=>window.value.metrics.filter(m=>['MARGIN_CHANGE','MARGIN_BUY','MARGIN_REPAY','MARGIN_INCREASE_RATIO','SECURITY_RETURN','RELATIVE_RETURN','VENDOR_NET'].includes(m.metric_id)))
const channels=computed(()=>window.value.metrics.filter(m=>['VENDOR_NET','ETF_ESTIMATE','INDUSTRY_SHARE'].includes(m.metric_id)))
const charts=computed(()=>flowChartBlocks(report.value,window.value))
const reconciliation=computed(()=>window.value.metrics.find(m=>m.metric_id==='MARGIN_RECONCILIATION'))
const maxResidual=computed(()=>window.value.metrics.find(m=>m.metric_id==='MARGIN_MAX_RESIDUAL'))
const details=computed(()=>Object.entries(report.value.series).flatMap(([key,values])=>values.filter(p=>p.session===point.value).map(p=>({key,...p}))))
const sourceFor=ref=>props.data.sources.find(s=>s.blob_ref===ref)
watch(selected,()=>{point.value=null})
const labels={margin_balance:'融资余额',margin_buy:'融资买入',margin_repay:'融资偿还',security_close:'个股收盘',security_turnover:'个股成交额',market_close:'沪深三百收盘',vendor_estimate:'供应商大单估算',etf_shares:'ETF份额',etf_nav:'ETF单位净值'}
</script>
<template>
  <section class="panel rep-section flow-analysis" data-flow-analysis>
    <div class="section-heading"><h2>资金是否支持这个判断</h2><span class="tag tag-warn">分渠道观察</span></div>
    <p class="condition-note">截至 {{ report.end_date }} 的已收盘交易日。融资、供应商估算和 ETF 分开看，成交额只表示活跃度。</p>
    <p v-if="data.flow_alignment" class="condition-note">资金比较对齐日 {{ data.flow_alignment.session }}；最新已收盘行情日 {{ data.flow_alignment.latest_closed_session }}。{{ data.flow_alignment.note }}</p>
    <div class="flow-tabs" role="tablist" aria-label="资金观察窗口"><button v-for="n in [5,20,60]" :key="n" class="btn" role="tab" :aria-selected="selected===n" @click="selected=n">近 {{ n }} 个交易日</button></div>
    <p class="flow-period">{{ window.sessions[0] || '日历缺项' }} — {{ report.end_date }}<span v-if="window.baseline_date"> · 余额与价格基准 {{ window.baseline_date }}</span></p>
    <p v-if="assessment" class="flow-summary">{{ assessment.summary }}</p>
    <div class="flow-metrics"><article v-for="m in metrics" :key="m.metric_id"><span>{{ m.label }}</span><strong :class="m.value!=null&&Number(m.value)!==0?Number(m.value)>0?'positive':'negative':''">{{ flowNumber(m.value,m.metric_id==='RELATIVE_RETURN'?'percentage_points':m.unit) }}</strong><small v-if="m.status==='CONFLICT'">来源字段冲突，未签发数值</small><small v-else-if="m.value==null">需要 {{ m.expected_count }} 日，当前 {{ m.actual_count }} 日</small><button v-else class="prediction-source" @click="emit('evidence',`EV-FLOW-${selected}-${m.metric_id}`)">查看计算依据</button></article></div>
    <p v-if="Number(maxResidual?.value)>0" class="condition-note">余额差与买入减偿还存在未解释差额：期间 {{ Number(reconciliation.value).toLocaleString('zh-CN') }} 元，最大单日差额绝对值 {{ Number(maxResidual.value).toLocaleString('zh-CN') }} 元。保留各原始字段，不强行配平；差额原因尚未核查。</p>
    <div class="flow-reading" v-if="assessment"><p><b>持续性</b>{{ assessment.persistence }}</p><p><b>价与融资</b>{{ divergenceLabel[window.divergence] }}。{{ assessment.divergence }}</p><p><b>广度</b>{{ assessment.breadth }}</p><p><b>还可能是什么</b>{{ assessment.alternatives.join('；') }}</p></div>
    <div class="flow-charts"><article v-for="block in charts" :key="block.kind"><h3>{{ block.title }}</h3><PrototypeChart :block="block" :height="245" :empty="block.series.every(s=>s.data.every(v=>v==null))?'本窗口无完整渠道数据，缺项保留':''" @point="point=$event"/></article></div>
    <p class="condition-note">点击曲线上的日期查看当日原始字段与来源；图表中的空位表示缺失，不代表零。</p>
    <div v-if="point" class="flow-point" aria-live="polite"><div class="section-heading"><h3>{{ point }} · 当日观察</h3><button class="btn" @click="point=null">收起</button></div><p v-if="!details.length">该日没有可用字段。</p><p v-for="p in details" :key="p.key"><b>{{ labels[p.key] || p.key }}</b> {{ p.value }} {{ p.unit }} <a v-if="sourceFor(p.source_ref)?.url" :href="sourceFor(p.source_ref).url" target="_blank" rel="noopener noreferrer">{{ sourceFor(p.source_ref).source_family }} 原始来源</a></p><p class="condition-note">来源链接可能更新；本次取得的原始响应已经封存。</p></div>
    <details class="flow-definitions"><summary>数据覆盖与计算口径</summary><p v-for="m in channels" :key="m.metric_id"><b>{{ m.label }}：{{ flowNumber(m.value,m.unit) }}</b> · {{ m.note }}</p><p v-for="m in window.metrics.filter(m=>m.value!=null)" :key="m.metric_id">{{ m.label }}：{{ m.formula }}</p><p v-for="text in report.limitations" :key="text">{{ text }}</p><p>当前没有连续 ETF 份额与已核定拆并因子，不计算申赎额。行业成员与市场分母未核定，不展示行业热度或广度数值。</p></details>
    <details v-if="developer" class="flow-development"><summary>开发核验：原始覆盖、供应商核对与封存</summary><pre>{{ JSON.stringify({method_version:report.method_version,coverage:data.flow_coverage,gaps:data.flow_gaps,checks:data.flow_checks,window,calculation_ref:data.flow_calculation_ref,snapshot:data.flow_snapshot},null,2) }}</pre></details>
  </section>
</template>
<style scoped>
.flow-tabs{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0 12px}.flow-tabs [aria-selected=true]{color:var(--accent);border-color:var(--accent);background:var(--bg-raised)}.flow-period{font-size:12px;color:var(--text-3)}.flow-summary{font-size:18px;line-height:1.9;max-width:900px}.flow-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;background:var(--border);border:1px solid var(--border);margin:24px 0}.flow-metrics article{padding:18px;background:var(--bg-base);display:flex;flex-direction:column;gap:10px}.flow-metrics span,.flow-metrics small{color:var(--text-2);font-size:12px}.flow-metrics strong{font:22px 'JetBrains Mono',Consolas,monospace}.positive{color:var(--up)}.negative{color:var(--down)}.flow-reading{padding:4px 0;line-height:1.9}.flow-reading b{display:inline-block;min-width:100px;color:var(--text-2);font-weight:500}.flow-charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px;margin:24px 0}.flow-charts article{min-width:0}.flow-charts h3{font-size:13px;font-weight:500;margin:0 0 14px}.flow-point{border:1px solid var(--border);padding:20px;margin:16px 0;font-size:13px;overflow-wrap:anywhere}.flow-point a{margin-left:12px;color:var(--accent);font-size:12px}.flow-definitions,.flow-development{padding:18px 0;border-top:1px solid var(--border);font-size:12px;line-height:1.8}.flow-definitions summary,.flow-development summary{cursor:pointer;color:var(--text-2)}@media(max-width:700px){.flow-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.flow-charts{grid-template-columns:1fr}.flow-tabs .btn{flex:1;padding:9px 6px;font-size:12px}.flow-reading b{display:block}.flow-metrics article{padding:13px}.flow-summary{font-size:16px}}
</style>
