<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {archiveAccuracy,armLabel,barWidth,comparisonMetrics,costRange,createResearchEvaluation,evaluationRoot,evaluationStage,evaluationStatus,formatValue,paragraphs} from './researchEvaluation.js'
import EffectivenessResults from './EffectivenessResults.vue'
const props=defineProps({id:String})
const emit=defineEmits(['started'])
const ctl=createResearchEvaluation({started:r=>emit('started',r)})
const {state,error,busy,items,listError,listing}=ctl
const initialLoading=ref(Boolean(props.id))
const report=computed(()=>state.value?.data)
const rows=computed(()=>report.value?.rows||[])
const charts=computed(()=>[{key:'seconds',title:'共享取数后研究耗时',unit:'秒'},{key:'tokens',title:'已记录输入＋输出 Token',unit:'Token'},{key:'cost',title:'调用费用估算范围',unit:'人民币元'}].map(chart=>({...chart,rows:comparisonMetrics(rows.value,chart.key)})))
const prediction=computed(()=>report.value?.prediction)
const round=computed(()=>report.value?.round_summary)
const noPublishedPair=computed(()=>state.value?.status==='COMPLETED'&&round.value?.verdict==='COMPLETE_WITH_FAILURES'&&report.value?.pairs?.length>0&&report.value.pairs.every(pair=>pair.complete===false))
const names={'000333.SZ':'美的集团','600036.SH':'招商银行','601088.SH':'中国神华','600009.SH':'上海机场','300750.SZ':'宁德时代','600519.SH':'贵州茅台'}
const direction=value=>({FAVORABLE:'看多',UNFAVORABLE:'看空',UP:'上涨',DOWN:'下跌',FLAT:'平盘'})[value]||value||'未签发'
const stamp=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—'
const number=(_value,digits=2)=>formatValue(_value,digits)
function delta(value,digits=2){const n=value==null?null:Number(value);return n===null||!Number.isFinite(n)?'—':(n>0?'+':'')+formatValue(n,digits)}
function count(value){return Array.isArray(value)?value.length:formatValue(value,0)}
function usageGaps(arm){return rows.value.filter(row=>row.arm===arm).reduce((sum,row)=>sum+(Number(row.unknown_usage_calls)||0),0)}
async function submit(){await ctl.submit({protocol_version:'research-effectiveness-v1'});await ctl.list()}
watch(()=>state.value?.status,(value,old)=>{if(old==='RUNNING'&&value!=='RUNNING')ctl.list()})
onMounted(async()=>{try{await Promise.allSettled([ctl.list(),props.id?ctl.load(props.id):Promise.resolve()])}finally{initialLoading.value=false}})
onUnmounted(ctl.dispose)
</script>
<template>
  <div class="page evaluation-comparison">
    <header class="page-heading"><div><h1>原流程与增强流程 · 实源对照</h1><p class="muted">同一封存输入，比较方向、论证、耗时与调用费用；到期后核对真实表现。</p></div><a class="btn" href="#/developer">开发者工作区</a></header>
    <section class="panel rep-section">
      <div class="section-heading"><h2>效果与成本验收</h2><button class="btn btn-primary" :disabled="busy||initialLoading||state?.status==='RUNNING'" @click="submit">{{ busy?'正在保存…':'开始效果与组件对照' }}</button></div>
      <p v-if="initialLoading" role="status">正在核对封存报告、原文与已保存成绩，不会重新调用模型。</p>
      <p>新实验预登记美的集团、招商银行、中国神华、上海机场、宁德时代和贵州茅台，比较标准、动态方法、补充工具与推理；原有各轮记录继续保留。</p><p class="condition-note">每轮12份研究、3组隐藏流程身份质量评阅；另登记10个交易日每日两只股票的明日预测配对。本机运行时自动执行晚间窗口，会产生真实调用费用；未到期不计成绩。</p>
      <p class="condition-note">会产生真实模型调用费用。失败尝试、输入、预算与结果均保留；重复刷新不会重新提交实验。费用区间是按公开价目与实际用量估算，实际扣费以平台账单为准。</p>
      <p v-if="error" class="error-text" role="alert">{{ error }}</p>
      <div v-if="state" class="evaluation-progress" role="status"><strong>{{ state.status==='COMPLETED'&&round?.verdict==='COMPLETE_WITH_FAILURES'?'评测已封存（包含失败）':evaluationStatus(state.status) }}</strong><span>{{ evaluationStage(state.stage) }}</span><button v-if="!['RUNNING','COMPLETED'].includes(state.status)" class="btn" :disabled="busy" @click="ctl.resume">继续原实验（原预算）</button><button v-if="state.status==='INTERRUPTED'" class="btn" :disabled="busy" @click="ctl.seal">封存本轮含失败结果</button><button class="btn" :disabled="busy" @click="ctl.load(state.run_id)">刷新核对结果</button><a class="btn" :href="'/api'+evaluationRoot+'/'+encodeURIComponent(state.run_id)+'/export'" target="_blank" rel="noopener">导出本轮记录</a></div>
      <p v-if="state?.error" class="notice" role="alert">{{ state.error }} 已有结果保留，可继续同一实验。</p>
      <p v-if="state" class="condition-note">登记时间 {{ stamp(state.created_at) }} · 本轮累计运行 {{ number(state.elapsed_seconds) }} 秒 · 共享取数 {{ number(report?.shared_capture_seconds) }} 秒 · {{ state.run_id }}</p>
    </section>

    <template v-if="report">
      <EffectivenessResults v-if="report.components" :report="report" />
      <section v-if="round&&!report.components" class="panel rep-section round-summary" :class="{failed:round.verdict==='COMPLETE_WITH_FAILURES'}" aria-label="本轮结果摘要">
        <h2>本轮结果</h2>
        <div class="summary-metrics"><span v-for="arm in round.arms||[]" :key="arm.arm">{{ armLabel(arm.arm) }}研究完成<b>{{ number(arm.completed,0) }} / {{ number(arm.declared,0) }}</b></span><span>全部正式研究完成<b>{{ number(round.completed_studies,0) }} / {{ number(round.declared_studies,0) }}</b></span><span>方法消融批准<b>{{ number(round.completed_method_arms,0) }} / {{ number(round.declared_method_arms,0) }}</b></span></div>
        <p v-if="round.verdict==='COMPLETE_WITH_FAILURES'" class="notice"><strong>存在未完成组，不能宣称增强提升。</strong>本轮正式研究 {{ number(round.completed_studies,0) }} / {{ number(round.declared_studies,0) }} 完成，附加方法消融 {{ number(round.completed_method_arms,0) }} / {{ number(round.declared_method_arms,0) }} 获批。失败、复核及全部调用消耗均保留。评测结束或报告封存不代表各组研究成功。</p>
        <p v-else class="notice">各登记组已完成运行；预测提升仍须等待真实成熟样本核对，方法消融不代表收益提升。</p>
        <div class="evaluation-table-wrap"><table class="evaluation-table"><thead><tr><th>流程合计</th><th>模型调用</th><th>已记录 Token</th><th>费用估算</th><th>共享取数后累计耗时</th></tr></thead><tbody><tr v-for="arm in round.arms||[]" :key="arm.arm"><td>{{ armLabel(arm.arm) }}</td><td>{{ number(arm.model_calls,0) }}</td><td>{{ number(arm.input_tokens==null||arm.output_tokens==null?null:Number(arm.input_tokens)+Number(arm.output_tokens),0) }}</td><td>{{ costRange(arm.cost) }}</td><td>{{ number(arm.elapsed_seconds) }} 秒</td></tr></tbody></table></div>
        <template v-for="arm in round.arms||[]" :key="arm.arm"><p v-if="usageGaps(arm.arm)" class="condition-note">{{ armLabel(arm.arm) }}有 {{ usageGaps(arm.arm) }} 次调用用量未知，上表 Token 仅为已记录部分；费用范围含预留预算，不能作为完整账单。</p></template>
      </section>
      <section class="panel rep-section">
        <h2>预测表现 · 非平盘成熟配对</h2>
        <div class="summary-metrics"><span>登记配对<b>{{ number(prediction?.registered_pairs,0) }}</b></span><span>非平盘成熟配对<b>{{ number(prediction?.paired_matured,0) }}</b></span><span>独立登记日期<b>{{ number(prediction?.independent_dates,0) }}</b></span><span>不同目标日期<b>{{ number(prediction?.unique_target_dates,0) }}</b></span><span>非平盘准确率差<b>{{ prediction?.paired_matured?delta(prediction.accuracy_delta==null?null:Number(prediction.accuracy_delta)*100,1)+' 个百分点':'尚无可计样本' }}</b></span></div>
        <p class="notice">{{ prediction?.paired_matured?'成熟样本仅作本轮描述。同一登记起点的股票与重叠周期存在相关性，不同目标日期不能当作相互独立的实验日期。':'目前没有非平盘成熟配对，方向准确率和收益提升尚不能确认。研究完成和复核通过不代表预测已经正确。' }}</p>
        <p v-if="noPublishedPair" class="notice"><strong>本轮没有双方都成功发布的配对，单纯等待到期不能形成增强准确率差；失败组不得回填，修复后须另建新实验。</strong></p>
        <p v-if="prediction?.paired_matured">原流程正确 {{ number(prediction.original_correct,0) }} / {{ number(prediction.paired_matured,0) }}；增强流程正确 {{ number(prediction.enhanced_correct,0) }} / {{ number(prediction.paired_matured,0) }}。</p>
        <div v-if="Object.keys(prediction?.status_counts||{}).length" class="ask-chips"><span v-for="(value,key) in prediction.status_counts" :key="key" class="chip">{{ evaluationStatus(key) }} {{ value }}</span></div>
        <p v-if="Object.keys(prediction?.baseline_correct||{}).length" class="condition-note">同样本简单基线：<span v-for="(value,key) in prediction.baseline_correct" :key="key">{{ ({ALWAYS_UP:'始终看涨',ALWAYS_DOWN:'始终看跌',PAST_20_SESSION_SIGN:'过去20日方向'})[key]||key }} 正确 {{ number(value,0) }}； </span></p>
      </section>

      <section v-if="report.next_session_history" class="panel rep-section">
        <h2>既有明日预测 · 非配对历史</h2>
        <p class="notice">按证券和目标日选择最早签发记录去重。这些预测来自不同输入、版本和时间，不能归因增强效果，也不能和上面的同输入配对成绩混合。</p>
        <p class="condition-note">仅纳入本轮登记截止前已存在的预测，截止 {{ stamp(report.next_session_history.cutoff) }}；真实核对可以随后更新。正确率括号内为正确数／可核对数。</p>
        <div class="evaluation-table-wrap"><table class="evaluation-table"><thead><tr><th>流程</th><th>去重登记</th><th>方向正确率</th><th>始终看涨</th><th>始终看跌</th><th>前日方向延续</th><th>独立起点日期</th></tr></thead><tbody><tr v-for="group in report.next_session_history.groups||[]" :key="group.arm"><td>{{ armLabel(group.arm) }}</td><td>{{ number(group.registered,0) }}</td><td><strong>{{ archiveAccuracy(group.accuracy,group) }}</strong></td><td>{{ archiveAccuracy(group.baselines?.ALWAYS_UP,group) }}</td><td>{{ archiveAccuracy(group.baselines?.ALWAYS_DOWN,group) }}</td><td>{{ archiveAccuracy(group.baselines?.PERSISTENCE,group) }}</td><td>{{ number(group.independent_dates,0) }}</td></tr></tbody></table></div>
        <p v-for="note in paragraphs(report.next_session_history.limitations)" :key="note" class="condition-note">{{ note }}</p>
      </section>

      <section class="evaluation-charts" aria-label="原流程与增强流程耗时、令牌与费用对照图">
        <article v-for="chart in charts" :key="chart.key" class="panel rep-section"><h2>{{ chart.title }}</h2><p class="condition-note">{{ chart.key==='cost'?'实线为估算下限，浅色延伸为估算上限；用量未知时上限含预留预算。':chart.key==='tokens'?'包含输入计数、审核、修正与失败调用中的已知实际用量；缺失调用另列。':'共享取数单列；各组包含模型、复核及额外取证的运行时间。' }}</p><div v-for="item in chart.rows" :key="item.key" class="metric-bar-row"><div class="metric-bar-label"><span>{{ item.label }}</span><b>{{ chart.key==='cost'?costRange({lower_cny:item.lower,upper_cny:item.value}):item.value===null?'尚无数据':number(item.value,chart.key==='tokens'?0:2)+' '+chart.unit }}</b></div><div class="metric-track"><div class="metric-upper" :class="item.arm.toLowerCase()" :style="{width:barWidth(item.value,chart.rows)+'%'}"></div><div class="metric-solid" :class="item.arm.toLowerCase()" :style="{width:barWidth(item.lower,chart.rows)+'%'}"></div></div></div></article>
      </section>

      <section v-if="report.pairs?.length" class="panel rep-section"><h2>逐项配对差异</h2><p class="condition-note">差值均为增强流程减去原流程，正数表示增加。只有同输入并完成的配对才具备直接比较条件。</p><div class="evaluation-table-wrap"><table class="evaluation-table"><thead><tr><th>期限</th><th>输入匹配</th><th>研究完成</th><th>耗时差（秒）</th><th>Token 差</th><th>费用差范围（元）</th></tr></thead><tbody><tr v-for="pair in report.pairs||[]" :key="pair.case_id"><td>{{ rows.find(r=>r.case_id===pair.case_id)?.horizon_sessions||pair.case_id }} 日</td><td>{{ pair.matched_inputs?'同一封存输入':'尚未核验匹配' }}</td><td>{{ pair.complete?'两组已完成':'保留未完成组' }}</td><td>{{ delta(pair.latency_delta_seconds) }}</td><td>{{ delta(pair.token_delta,0) }}</td><td>{{ delta(pair.cost_delta_lower,4) }} 至 {{ delta(pair.cost_delta_upper,4) }}</td></tr></tbody></table></div></section>

      <section class="panel rep-section">
        <h2>研究方向与论证</h2>
        <div v-for="row in rows" :key="row.case_id+row.arm" class="evaluation-study">
          <div class="section-heading"><h3>{{ row.horizon_sessions }} 个交易日 · {{ armLabel(row.arm) }}</h3><a v-if="row.run_id&&row.status!=='NOT_STARTED'" class="btn" :href="'#/investment-research/'+row.run_id">打开原研究</a></div>
          <p class="condition-note">{{ row.status==='COMPLETED'?'研究完成':evaluationStatus(row.status) }} · {{ row.matched_inputs?'输入匹配已核验':'输入匹配待核验' }} · {{ number(row.model_calls,0) }} 次模型调用（含输入计数 {{ number(row.input_count_calls,0) }} 次、失败 {{ number(row.failed_calls,0) }} 次） · {{ number(row.tool_calls,0) }} 次工具调用 · {{ number(row.review_rounds,0) }} 轮复核 · 新增证据 {{ count(row.additional_evidence) }} · 模型耗时 {{ number(row.model_seconds) }} 秒</p>
          <p v-if="row.error" class="notice" role="alert">此组未完成的原因：{{ row.error }}</p>
          <details v-if="row.failed_checks?.length" class="failed-checks"><summary>开发者：{{ row.failed_checks.length }} 项契约与复核失败</summary><ul><li v-for="(check,index) in row.failed_checks" :key="index"><strong>{{ check.role||'核验' }}</strong> · 调用 {{ check.call_index }}：{{ check.issue }}</li></ul></details>
          <p v-if="row.unknown_usage_calls" class="notice">{{ row.unknown_usage_calls }} 次调用缺少完整用量；图中 Token 仅为已知部分，费用上限含预留预算，不能作为完整账单。</p>
          <p v-if="row.unknown_model_calls" class="notice">{{ row.unknown_model_calls }} 次调用的模型缺少本轮固定报价，费用尚不能估算。</p>
          <div class="evaluation-table-wrap"><table class="evaluation-table"><thead><tr><th>股票</th><th>方向</th><th>结论</th><th>支持／反证</th><th>证据／方法</th></tr></thead><tbody><tr v-for="opinion in row.directions||[]" :key="opinion.security_id"><td>{{ names[opinion.security_id]||opinion.security_id }}<small>{{ opinion.security_id }}</small></td><td><strong>{{ direction(opinion.direction) }}</strong></td><td class="opinion-summary">{{ opinion.summary }}</td><td>{{ count(opinion.claims) }} / {{ count(opinion.opposing) }}</td><td>{{ number(opinion.evidence_count,0) }} / {{ count(opinion.method_rule_ids) }}</td></tr><tr v-if="!row.directions?.length"><td colspan="5">此组尚未签发通过复核的结论，运行与失败记录保留。</td></tr></tbody></table></div>
          <p v-if="row.prediction?.last_attempt?.status==='ACQUISITION_FAILED'" class="notice" role="alert">最近一次核对取数未完成（{{ stamp(row.prediction.last_attempt.created_at) }}）：{{ row.prediction.last_attempt.error||'数据连接暂时不可用' }}。{{ row.prediction.latest_valid_at?'下方仍保留最近有效核对，不能当作本次新核对结果。':'此前没有有效核对，暂不计入准确率。' }}</p>
          <p v-if="row.prediction?.latest_valid_at" class="condition-note">最近有效核对 {{ stamp(row.prediction.latest_valid_at) }}<template v-if="row.prediction.through"> · 行情截至 {{ row.prediction.through }}</template></p>
          <ul v-if="row.prediction?.stocks?.length" class="condition-note"><li v-for="stock in row.prediction.stocks" :key="stock.security_id">{{ names[stock.security_id]||stock.security_id }}：{{ evaluationStatus(stock.status) }}，目标日 {{ stock.target_date||'待日历确认' }}<template v-if="stock.correct!==null&&stock.correct!==undefined">，方向{{ stock.correct?'正确':'不符' }}</template></li></ul>
        </div>
      </section>

      <section v-if="report.ablation" class="panel rep-section"><h2>知识与路线消融</h2><p v-if="/investment-method-ablation-v[3-5]$/.test(report.ablation.report?.version||'')" class="condition-note">各组分析输入相互隔离；复核阶段共用原始证据并逐组审核，属于同模型复核，不是六次独立盲审。</p><p>同一公开证据下，仅改变入库方法片段与路线条件。无方法组仍包含模型预训练知识，不能称为“完全没有知识”。</p><p class="condition-note">完成 {{ number(report.ablation.report?.completed,0) }} / {{ number(report.ablation.report?.declared,0) }} 组 · {{ number(report.ablation.model_calls,0) }} 次调用（含输入计数 {{ number(report.ablation.input_count_calls,0) }} 次、失败 {{ number(report.ablation.failed_calls,0) }} 次） · {{ number(report.ablation.elapsed_seconds) }} 秒 · 费用估算 {{ costRange(report.ablation.cost) }}</p><p v-if="report.ablation.unknown_usage_calls||report.ablation.unknown_model_calls" class="notice">消融调用中有 {{ number(report.ablation.unknown_usage_calls,0) }} 次用量缺失、{{ number(report.ablation.unknown_model_calls,0) }} 次缺少固定报价，费用仅保留可估算范围。</p><div class="evaluation-table-wrap"><table class="evaluation-table"><thead><tr><th>股票</th><th>对照组</th><th>复核</th><th>方向</th><th>结论与条件</th></tr></thead><tbody><tr v-for="item in report.ablation.report?.rows||[]" :key="item.case_id+item.arm"><td>{{ names[item.security_id]||item.security_id }}</td><td>{{ armLabel(item.arm) }}</td><td>{{ evaluationStatus(item.status) }}</td><td>{{ direction(item.direction) }}</td><td class="opinion-summary">{{ item.summary||'尚未形成通过复核的意见' }}</td></tr></tbody></table></div><p v-for="note in report.ablation.report?.limitations||[]" :key="note" class="condition-note">{{ note }}</p></section>

      <section class="panel rep-section"><h2>本轮结论的边界</h2><ul><li v-for="note in report.limitations||[]" :key="note">{{ note }}</li></ul><p v-if="state.manifest?.pricing?.source_url" class="condition-note">费用依据：<a :href="state.manifest.pricing.source_url" target="_blank" rel="noopener noreferrer">模型供应商公开价目</a>。缓存命中等未完整提供时按区间估算。</p><details><summary>固定实验协议与调用依据</summary><pre>{{ state.manifest }}</pre></details></section>
    </template>

    <section class="panel rep-section"><div class="section-heading"><h2>已保存评测</h2><button class="btn" :disabled="listing" @click="ctl.list">{{ listing?'读取中…':'刷新列表' }}</button></div><p v-if="listError" role="alert" class="error-text">{{ listError }}</p><p v-if="!items.length&&!listing" class="muted">尚无评测记录。正式研究和原有历史保留在历史库。</p><ul class="evaluation-history"><li v-for="item in items" :key="item.run_id"><a :href="'#/evaluation/'+item.run_id">{{ stamp(item.created_at) }} · {{ evaluationStatus(item.status) }}</a><small>{{ item.run_id }}</small></li></ul></section>
  </div>
</template>
<style scoped>
.evaluation-comparison{max-width:1440px}.evaluation-comparison>.panel,.evaluation-charts{margin-bottom:18px}.round-summary.failed{border-left:5px solid #b76b24}.failed-checks{margin:14px 0}.failed-checks li{overflow-wrap:anywhere}.evaluation-progress{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:16px 0}.evaluation-progress>span{flex:1;min-width:150px;color:var(--text-2)}.evaluation-charts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.evaluation-charts h2{font-size:18px}.metric-bar-row{margin:20px 0}.metric-bar-label{display:flex;justify-content:space-between;gap:8px;font-size:13px;margin-bottom:7px}.metric-bar-label b{text-align:right;font-variant-numeric:tabular-nums}.metric-track{height:12px;background:var(--bg-2,#edf1f5);border-radius:3px;position:relative;overflow:hidden}.metric-upper,.metric-solid{position:absolute;height:100%;left:0;top:0;background:#64748b}.metric-upper{opacity:.28}.metric-solid.enhanced,.metric-upper.enhanced{background:#ae7d26}.evaluation-table-wrap{overflow-x:auto}.evaluation-table{width:100%;border-collapse:collapse;font-size:14px}.evaluation-table th,.evaluation-table td{padding:13px 10px;border-bottom:1px solid var(--border,#dce3ed);text-align:left;vertical-align:top}.evaluation-table th{white-space:nowrap;color:var(--text-2)}.evaluation-table small,.evaluation-history small{display:block;color:var(--text-2);font-size:11px;margin-top:5px}.opinion-summary{min-width:240px;line-height:1.8}.evaluation-study{padding:18px 0;border-bottom:1px solid var(--border,#dce3ed)}.evaluation-study:last-child{border:0}.evaluation-study h3{margin:0}.evaluation-comparison pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:500px;overflow:auto}.evaluation-history{padding:0;list-style:none}.evaluation-history li{padding:13px 0;border-bottom:1px solid var(--border,#dce3ed)}.evaluation-comparison li{line-height:1.8}.evaluation-comparison .summary-metrics b{font-variant-numeric:tabular-nums}@media(max-width:1000px){.evaluation-charts{grid-template-columns:1fr}}@media(max-width:600px){.evaluation-progress .btn{font-size:12px}.metric-bar-label{font-size:12px}.evaluation-table{font-size:12px}}
</style>
