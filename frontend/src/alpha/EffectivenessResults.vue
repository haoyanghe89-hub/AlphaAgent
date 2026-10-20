<script setup>
import {computed} from 'vue'
import {armLabel,costRange,evaluationStatus,formatValue} from './researchEvaluation.js'
const props=defineProps({report:Object})
const quality=computed(()=>props.report.quality_followup?.quality||props.report.quality)
const dimensions={facts:'事实与引用',counterevidence:'反证核查',time_scope:'期限与口径',actionability:'可观察条件',useful_information:'有效信息'}
const stock={'000333.SZ':'美的集团','600036.SH':'招商银行','601088.SH':'中国神华','600009.SH':'上海机场','300750.SZ':'宁德时代','600519.SH':'贵州茅台'}
const status=v=>({SCHEDULED:'等待当日窗口',ISSUED:'已签发，等待收盘核对',FAILED:'未完成，保留失败',MISSED_WINDOW:'错过窗口，未补签',MODEL_CHANGED:'模型变化，未执行',OUTCOME_CONFLICT:'核对来源冲突',DUPLICATE:'重复样本，不计分',INCOMPLETE_PAIRS:'部分配对缺失'})[v]||evaluationStatus(v)
</script>
<template>
  <section class="panel rep-section">
    <h2>额外成本买到了什么</h2><p class="notice"><strong>当前默认：标准研究。</strong>以下是预先登记的组件试验。工程通过、模型质量分和未来方向成绩分别验收；本轮不会自动开启全量增强。</p>
    <div class="evaluation-table-wrap"><table class="evaluation-table"><thead><tr><th>逐步增加的组件</th><th>完成</th><th>真实调用</th><th>Token</th><th>累计耗时</th><th>费用估算</th></tr></thead><tbody><tr v-for="r in report.components" :key="r.arm"><td>{{ armLabel(r.arm) }}</td><td>{{ r.completed }} / {{ r.declared }}</td><td>{{ r.model_calls }}</td><td>{{ formatValue(r.tokens,0) }}<small v-if="r.unknown_usage_calls">另有 {{ r.unknown_usage_calls }} 次用量未知</small></td><td>{{ formatValue(r.elapsed_seconds) }} 秒</td><td>{{ costRange({lower_cny:r.cost_lower,upper_cny:r.cost_upper}) }}</td></tr></tbody></table></div>
    <p v-if="report.components.some(r=>r.completed<r.declared)" class="notice">存在未完成组，失败及预算消耗保留。本轮评测封存不表示所有研究通过，也不会把失败项替换为新的成功项。</p><p class="condition-note">三案例轮换运行顺序；底层知识版本与审核门禁固定。组件差异仍受模型随机性和交互影响，不能据一次生成直接判断因果增益。失败消耗计入，费用区间不是账单。</p>
  </section>
  <section class="panel rep-section">
    <h2>回答质量 · 隐藏流程身份评阅</h2><p class="notice">{{ quality.limitation }} 每项 0–2 分，总分上限 10；更长的回答或更多引用不会自动加分。</p>
    <p v-if="report.quality_followup" class="condition-note">当前为完整公开输出复评：已包含本地计算的入退场条件。原正文评分未覆盖数值条件，保留在下方；不是另一批独立样本。</p>
    <p>{{ status(quality.status) }} · {{ quality.completed_cases }} / {{ quality.declared_cases }} 个案例 · 本次评阅 {{ quality.model_calls }} 次调用，费用估算 {{ costRange(quality.cost) }}</p><p v-if="quality.error" class="notice">{{ quality.error }}</p>
    <div class="summary-metrics"><span v-for="g in quality.groups" :key="g.arm">{{ armLabel(g.arm) }} · {{ g.count }} 份回答<b>{{ formatValue(g.mean) }} / 10</b></span></div>
    <details><summary>逐股票查看五项评分、理由与引用，供人工核查</summary><article v-for="r in quality.rows" :key="r.security_id+r.arm"><h3>{{ stock[r.security_id]||r.security_id }} · {{ armLabel(r.arm) }} · {{ r.total }} 分</h3><p v-for="d in r.dimensions" :key="d.key"><strong>{{ dimensions[d.key] }} {{ d.score }}/2：</strong>{{ d.reason }} <small>{{ d.evidence_ids.join('、') }}</small></p></article></details>
    <details v-if="report.quality_followup"><summary>原正文评分与额外复评消耗（全部保留）</summary><p v-for="g in report.quality.groups" :key="g.arm">原正文评分 · {{ armLabel(g.arm) }} {{ formatValue(g.mean) }}/10</p><p>原正文评阅 {{ report.quality.model_calls }} 次调用，费用估算 {{ costRange(report.quality.cost) }}；以上补充复评费用另计，未扣除。</p></details>
  </section>
  <section class="panel rep-section">
    <h2>明日预测 · 连续交易日前瞻对照</h2><p class="condition-note">{{ report.forward_pairs.note }} 每日19:45开始准备，20:15停止签发；现有每日任务在目标日收盘后核对。</p>
    <div class="summary-metrics"><span>预登记配对<b>{{ report.forward_pairs.registered_pairs }}</b></span><span>成熟非平盘配对<b>{{ report.forward_pairs.paired_matured }}</b></span><span>独立起点日期<b>{{ report.forward_pairs.independent_dates }}</b></span><span>增强减标准准确率差<b>{{ report.forward_pairs.accuracy_delta==null?'等待真实结果':formatValue(report.forward_pairs.accuracy_delta*100)+' 个百分点' }}</b></span></div>
    <p class="notice">{{ report.forward_pairs.paired_matured?'小样本描述，仍不足以证明稳定增益。':'尚无成熟样本；当前不能声称明日预测更准。' }}</p>
    <p v-if="report.forward_pairs.paired_matured">标准正确 {{ report.forward_pairs.correct.ORIGINAL }}，增强正确 {{ report.forward_pairs.correct.ENHANCED }}；同样本始终看涨 {{ report.forward_pairs.baseline_correct.ALWAYS_UP }}，始终看跌 {{ report.forward_pairs.baseline_correct.ALWAYS_DOWN }}，前日方向延续 {{ report.forward_pairs.baseline_correct.PERSISTENCE }}。</p>
    <details><summary>查看全部预登记日期、签发和失败记录</summary><div class="evaluation-table-wrap"><table class="evaluation-table"><thead><tr><th>输入日 → 目标日</th><th>股票</th><th>状态</th><th>预测与原记录</th></tr></thead><tbody><tr v-for="r in report.forward_pairs.rows" :key="r.base_session+r.security_id"><td>{{ r.base_session }} → {{ r.target_session }}</td><td>{{ stock[r.security_id]||r.security_id }}</td><td>{{ status(r.status) }}</td><td><span v-for="(id,arm) in r.runs" :key="id"><a :href="'#/decision/'+id">{{ armLabel(arm) }} {{ r.predictions?.[arm]==='UP'?'涨':r.predictions?.[arm]==='DOWN'?'跌':'' }}</a> </span></td></tr></tbody></table></div></details>
  </section>
</template>
<style scoped>.rep-section{margin-bottom:18px}table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:12px 8px;text-align:left;border-bottom:1px solid var(--border)}small{display:block;font-size:11px;color:var(--text-2);overflow-wrap:anywhere}.evaluation-table-wrap{overflow:auto}summary{cursor:pointer}article{padding:10px 0;border-bottom:1px solid var(--border)}article p{line-height:1.8}.summary-metrics{margin:16px 0}.notice{line-height:1.8}</style>
