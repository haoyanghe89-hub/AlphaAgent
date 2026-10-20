<script setup>
import { computed, ref, watch } from 'vue'
import { useNextSessionPrediction, nextSessionRoot } from './useNextSessionPrediction.js'
import DecisionCitation from './DecisionCitation.vue'
import ResearchDataCharts from './ResearchDataCharts.vue'
import ResearchDepth from './ResearchDepth.vue'
import {nextSessionChartBlocks,accuracyChartBlock} from '../utils/decisionChartOptions.js'
const props=defineProps({id:String,developer:Boolean})
const {state,status,scoreboard,history,error,busy,active,refresh,start,action}=useNextSessionPrediction(props.id,{developer:props.developer})
const citation=ref(null)
const entity=ref(new URLSearchParams(location.search).get('entity') || '')
watch(state,value=>{if(!entity.value&&value?.request?.entity)entity.value=value.request.entity})
const generate=()=>start(entity.value)
const names={'688012.SH':'中微公司','002371.SZ':'北方华创'}
const direction={UP:'涨',DOWN:'跌',FLAT:'平盘'}
const support={STRONG:'较强',MODERATE:'一般',WEAK:'较弱'}
const present=text=>text?.replace(/\b(STRONG|MODERATE|WEAK)\b/g,value=>support[value])
const groups=computed(()=>scoreboard.value?.groups || [])
const failed=computed(()=>state.value?.attempts?.filter(a=>!['ISSUED','AUXILIARY'].includes(a.state)) || [])
const outcomeLabels={PENDING:'等待目标日收盘',PENDING_DATA:'等待真实行情',CONFLICT:'来源冲突，暂停核对',UNSCORABLE:'全日停牌，不计分'}
const batchLabels={RUNNING:'分析中',COMPLETED:'已结束',FAILED:'未能完成',INTERRUPTED:'已停止'}
const stamp=v=>v?new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚未记录'
const percent=ratio=>ratio?.value==null?'—':(Number(ratio.value)*100).toFixed(1)+'%'
const evidence=(row,eid)=>{citation.value={pid:row.prediction.prediction_id,eid}}
</script>
<template>
  <div class="page prediction-page">
    <section class="panel rep-section prediction-intro">
      <div v-if="developer" class="prediction-eyebrow">ALPHAAGENT · 每日观察</div>
      <div class="section-heading"><h1>明日预测</h1><span v-if="developer" class="tag tag-warn">开发者视图</span></div>
      <p class="prediction-lead">明确的涨跌判断，以及支持它和可能推翻它的依据。</p>
      <details class="input-disclosure" :open="!state?.predictions?.length"><summary>选择股票，生成下一交易日预测</summary><form class="prediction-start" @submit.prevent="generate"><label>股票名称或代码<input v-model.trim="entity" required maxlength="80" placeholder="例如：贵州茅台 / 600519" aria-label="预测股票名称或代码"></label><button class="btn btn-primary" :disabled="busy || active() || !status || !entity">{{ busy ? '正在提交…' : active() ? '正在分析…' : '预测这只股票' }}</button></form>
      <ResearchDepth /><p class="condition-note">支持任意可核实的沪深 A 股，收盘后生成下一交易日判断；上市历史不足或关键资料缺失会说明原因。所选股票单独统计，原预测保留。</p></details>
      <details v-if="developer" class="prediction-limits"><summary>固定样本实验</summary><p>中微公司 · 北方华创，正式签发窗口 20:00–20:15；与自主选股记录分开统计。</p><button class="btn" :disabled="busy||active()||!status" @click="start()">运行固定样本实验</button></details>
      <p v-if="error" class="notice error-text" role="alert">{{ error }} <button class="btn" @click="refresh">重新读取</button></p>
      <p v-if="!status && !error" role="status">正在读取已保存的预测…</p>
    </section>
    <section v-if="state && active()" class="panel rep-section" aria-live="polite"><span class="tag">分析中</span><h2>{{ state.progress }}</h2><p class="condition-note">每家公司完成复核后显示结果。未完成的判断不会提前展示。</p><button class="btn" :disabled="busy" @click="action('cancel')">停止本次分析</button></section>
    <section v-if="!state && status && !error" class="panel rep-section prediction-empty">
      <h2>{{ history.length ? '查看最近一次预测' : '第一份判断，从真实资料开始' }}</h2>
      <p class="condition-note">{{ history.length ? '打开下面的历史记录，查看当时的方向、理由与后续核对。' : '输入股票名称或代码，生成下一交易日方向。现在尚无预测或准确率。' }}</p>
      <a v-if="status.latest_run_id" class="btn" :href="'#/decision/'+status.latest_run_id">打开最近预测 →</a>
    </section>
    <section v-if="state && !active() && !state.predictions.length" class="panel rep-section"><h2>本次尚未形成可展示的预测</h2><p>{{ state.error || '资料或理由复核未通过，可查看下方说明后重新生成。' }}</p></section>
    <article v-for="row in state?.predictions || []" :key="row.prediction.prediction_id" class="panel rep-section prediction-card">
      <div class="section-heading"><div><h2>{{ row.name }}</h2><small class="condition-note">{{ row.prediction.security_id }}</small></div><span class="tag" :class="row.prediction.cohort==='PRIMARY'?'tag-cyan':'tag-warn'">{{ developer?(row.prediction.cohort==='PRIMARY'?'正式记录':'探索记录'):'已保存判断' }}</span></div>
      <div class="prediction-call"><span class="prediction-day">{{ row.prediction.target_session }} 方向判断</span><strong :class="['prediction-direction',row.prediction.prediction.toLowerCase()]">看{{ direction[row.prediction.prediction] }}</strong><p>{{ present(row.prediction.summary) }}</p></div>
      <p class="condition-note">依据支持程度：{{ support[row.prediction.support_level] }} · {{ stamp(row.prediction.issued_at) }} 发布</p>
      <ResearchDataCharts v-if="row.chart_data" :blocks="nextSessionChartBlocks(row.chart_data)"/>
      <div class="prediction-reasons">
        <section><h3>为什么这样判断</h3><ol><li v-for="(reason,index) in row.prediction.reasons" :key="index"><p>{{ reason.text }}</p><p class="condition-note">{{ reason.next_session_mechanism }}</p><button v-for="(eid,n) in reason.evidence_ids" :key="eid" class="prediction-source" @click="evidence(row,eid)">查看依据 {{ n+1 }} ↗</button></li></ol></section>
        <section class="prediction-counter"><h3>什么可能推翻判断</h3><div v-for="(reason,index) in row.prediction.opposing_reasons" :key="index"><p>{{ reason.text }}</p><p class="condition-note">{{ reason.next_session_mechanism }}</p><button v-for="(eid,n) in reason.evidence_ids" :key="eid" class="prediction-source" @click="evidence(row,eid)">查看反对依据 {{ n+1 }} ↗</button></div></section>
      </div>
      <details class="prediction-limits"><summary>这份判断有哪些局限</summary><ul><li v-for="note in row.prediction.limitations" :key="note">{{ note }}</li></ul></details>
      <div class="prediction-outcome"><strong>真实核对</strong><template v-if="row.outcome?.status==='SCORABLE'"><span>{{ row.outcome.correct?'方向命中':'方向未命中' }} · 实际{{ direction[row.outcome.return_direction] }}</span><span>收盘 {{ row.outcome.close }} 元 / 参考价 {{ row.outcome.reference_price }} 元</span></template><template v-else><span>{{ outcomeLabels[row.outcome?.status] || '等待核对' }}</span><span>{{ row.prediction.target_session }} 收盘后核对</span></template></div>
      <p v-if="row.outcome?.status && row.outcome.status!=='PENDING'" class="condition-note">{{ row.outcome.limitations.join('；') }}</p>
      <details v-if="developer" class="prediction-limits"><summary>签发与结果版本</summary><pre>{{ JSON.stringify(row,null,2) }}</pre></details>
    </article>
    <section v-if="failed.length" class="panel rep-section"><h2>未形成判断的公司</h2><p v-for="attempt in failed" :key="attempt.attempt_id"><strong>{{ names[attempt.slot_key[1]] || attempt.slot_key[1] }}</strong> · {{ attempt.reason }}</p><p class="condition-note">失败尝试保留，不作为已签发预测计分。</p></section>
    <section v-if="state?.prediction_ids?.length || state?.predictions?.length" class="panel rep-section"><div class="section-heading"><h2>目标日收盘后，回来看结果</h2><button class="btn" :disabled="busy || active()" @click="action('observe')">{{ busy?'正在核对…':'核对真实结果' }}</button></div><p class="condition-note">系统只用真实目标日收盘与参考价核对。平盘记未命中；停牌不计分；数据缺失或来源冲突时继续等待。</p></section>
    <details v-if="scoreboard" class="panel rep-section performance-details" :open="developer"><summary>历史表现与简单基线</summary><div class="section-heading"><h2>判断经得起多少次验证</h2><span class="tag">持续观察</span></div><p class="condition-note">正式与探索分别统计。准确率按已取得真实结果的样本计算；同一天的两家公司不等于两次独立市场检验。</p>
      <div v-if="developer" class="prediction-coverage"><div><strong>{{ scoreboard.planned || 0 }}</strong><span>计划正式预测</span></div><div><strong>{{ scoreboard.coverage?.numerator || 0 }}</strong><span>已正式签发</span></div><div><strong>{{ scoreboard.missed || 0 }}</strong><span>错过签发</span></div><div><strong>{{ scoreboard.failed || 0 }}</strong><span>正式尝试失败</span></div></div>
      <p v-if="!groups.length" class="condition-note">尚无预测样本。准确率 —，简单基线 —。</p>
      <article v-for="group in groups" :key="group.group_id" class="prediction-stat-group"><h3>{{ group.universe_version?.startsWith('user-selected-')?'自主选股 · '+group.records[0]?.security_id:group.cohort==='PRIMARY'?'固定样本正式组':'固定样本探索组' }} · {{ group.timing_group }}</h3><p class="condition-note"><span v-if="developer">{{ group.model_version }} · {{ group.prompt_version }} · </span>{{ group.records.length }} 份判断 · {{ group.statistics.independent_dates }} 个已核对交易日</p>
        <div class="prediction-score"><strong>{{ percent(group.statistics.accuracy) }}</strong><span>准确率 · {{ group.statistics.accuracy?.numerator || 0 }}/{{ group.statistics.accuracy?.denominator || 0 }} 命中</span></div>
        <ResearchDataCharts :blocks="[accuracyChartBlock(group)]" :height="170"/>
        <div class="prediction-table-wrap"><table><thead><tr><th>同样本对照</th><th>准确率</th><th>命中 / 可计分</th></tr></thead><tbody><tr v-for="(label,key) in {ALWAYS_UP:'永远看涨',ALWAYS_DOWN:'永远看跌',PERSISTENCE:'昨日方向延续'}" :key="key"><td>{{ label }}</td><td>{{ percent(group.statistics.baselines?.[key]) }}</td><td>{{ group.statistics.baselines?.[key]?.numerator || 0 }} / {{ group.statistics.baselines?.[key]?.denominator || 0 }}</td></tr></tbody></table></div>
        <p class="condition-note">涨样本命中 {{ percent(group.statistics.recalls?.UP) }} · 跌样本命中 {{ percent(group.statistics.recalls?.DOWN) }} · 平衡准确率 {{ group.statistics.balanced_accuracy==null?'—':(Number(group.statistics.balanced_accuracy)*100).toFixed(1)+'%' }}</p>
        <p class="condition-note">等待收盘 {{ group.outcome_counts.PENDING }} · 等待行情 {{ group.outcome_counts.PENDING_DATA }} · 来源冲突 {{ group.outcome_counts.CONFLICT }} · 不可计分 {{ group.outcome_counts.UNSCORABLE }}。观察结果仅供描述，预测能力尚未验证。</p>
      </article>
    </details>
    <details v-if="history.length" class="panel rep-section research-history"><summary>已保存的预测记录 · {{ history.length }} 份</summary><a v-for="run in [...history].reverse()" :key="run.run_id" class="prediction-history" :href="'#/decision/'+run.run_id"><span>{{ run.title || '下一交易日预测' }} · {{ run.target_session || '目标日核定中' }}</span><span>{{ stamp(run.created_at) }} · {{ batchLabels[run.status] }} →</span></a></details>
    <section v-if="developer && state?.development" class="panel rep-section"><h2>开发记录</h2><a class="btn" :href="'/api'+nextSessionRoot+'/runs/'+state.run_id+'/export'" target="_blank" rel="noopener noreferrer">导出封存输入与完整记录</a><details class="prediction-limits"><summary>实际取数、模型调用与审核轨迹</summary><pre>{{ JSON.stringify(state.development,null,2) }}</pre></details></section>
    <DecisionCitation v-if="citation && state" :key="citation.pid+citation.eid" :run-id="state.run_id" :prediction-id="citation.pid" :evidence-id="citation.eid" :developer="developer" @close="citation=null" />
  </div>
</template>
<style scoped>
.prediction-page h1{font-size:clamp(26px,4vw,38px);margin:16px 0}.prediction-eyebrow{font-size:11px;letter-spacing:.18em;color:var(--gold,#b89b5e)}.prediction-lead{font-size:16px;line-height:1.8}.prediction-start{display:flex;align-items:center;gap:20px;margin:28px 0 16px;flex-wrap:wrap}.prediction-intro>.condition-note{max-width:740px;line-height:1.8}.prediction-call{margin:28px 0 12px;max-width:850px}.prediction-day{font-size:13px;display:block;opacity:.65}.prediction-direction{font-size:42px;display:block;margin:8px 0 18px;letter-spacing:.06em}.prediction-direction.up{color:var(--red,#c76755)}.prediction-direction.down{color:var(--green,#629785)}.prediction-call p{font-size:17px;line-height:1.95}.prediction-reasons{display:grid;grid-template-columns:1.15fr 1fr;gap:40px;margin-top:32px}.prediction-reasons h3{font-size:15px;margin:0 0 18px}.prediction-reasons p{line-height:1.9;margin:10px 0}.prediction-reasons ol{padding-left:20px}.prediction-reasons li{padding-left:6px;margin-bottom:26px}.prediction-counter{border-left:1px solid var(--border,#8883);padding-left:28px}.prediction-counter>div{margin-bottom:26px}.prediction-source{background:none;border:0;border-bottom:1px solid var(--gold,#b89b5e);color:inherit;font:inherit;font-size:12px;cursor:pointer;margin:4px 14px 8px 0;padding:6px 0}.prediction-limits{margin-top:22px;line-height:1.9}.prediction-limits summary{cursor:pointer;font-size:13px}.prediction-limits pre{font-size:11px;max-height:480px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}.prediction-outcome{display:flex;gap:18px;flex-wrap:wrap;padding:20px 0 0;border-top:1px solid var(--border,#8883);margin-top:26px;font-size:13px}.prediction-coverage{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin:28px 0}.prediction-coverage strong{display:block;font-size:24px;margin-bottom:8px}.prediction-coverage span{font-size:12px;opacity:.65}.prediction-stat-group{margin:28px 0;padding-top:18px;border-top:1px solid var(--border,#8883)}.prediction-score{display:flex;align-items:baseline;gap:16px;margin:20px 0}.prediction-score strong{font-size:32px}.prediction-score span{font-size:13px;opacity:.65}.prediction-table-wrap{overflow-x:auto}.prediction-table-wrap table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}.prediction-table-wrap td,.prediction-table-wrap th{padding:12px 8px;border-bottom:1px solid var(--border,#8883);font-weight:normal}.prediction-history{display:flex;justify-content:space-between;gap:16px;padding:16px 0;border-bottom:1px solid var(--border,#8883);line-height:1.8}.prediction-history span:last-child{font-size:12px;opacity:.65}.prediction-page button:focus-visible,.prediction-page a:focus-visible,.prediction-page summary:focus-visible{outline:2px solid var(--gold,#b89b5e);outline-offset:4px}@media(max-width:700px){.prediction-reasons{grid-template-columns:1fr;gap:16px}.prediction-counter{border-left:0;border-top:1px solid var(--border,#8883);padding:24px 0 0}.prediction-coverage{grid-template-columns:repeat(2,1fr)}.prediction-history{flex-direction:column;gap:4px}.prediction-page .section-heading{flex-wrap:wrap;gap:12px}.prediction-outcome{flex-direction:column;gap:10px}}
</style>
