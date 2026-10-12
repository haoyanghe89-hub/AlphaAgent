<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useDelivery, comparisonEntities } from './useDelivery.js'
import PrototypeChart from './PrototypeChart.vue'
import { number, dateTime } from './model.js'
const props = defineProps({ id:String })
const emit = defineEmits(['started'])
const left = ref(''), right = ref(''), selected = ref(null), evidenceDialog = ref(null)
async function showMetric(row,index) {
  const subject = data.value.data.subjects[index]
  const cell = row.cells[index]
  const fact = cell.proof || subject.evidence?.find(e => e.locator === 'metrics/' + cell.name)
  const keys = fact?.source || cell.source.split(' / ')
  selected.value = {subject,cell,fact,date:row.dates[index],sources:subject.sources.filter(s => keys.includes(s.key))}
  await nextTick(); evidenceDialog.value?.showModal()
}
const { data, error, busy, load, submit, resume, dispose } = useDelivery(v => emit('started', v))
onMounted(async () => {
  const q = sessionStorage.getItem('alpha-comparison-question')
  if (q) { [left.value,right.value] = comparisonEntities(q); sessionStorage.removeItem('alpha-comparison-question') }
  if (props.id) { await load(props.id); if (data.value?.request?.entities) [left.value,right.value] = data.value.request.entities }
})
onUnmounted(dispose)
const summaryRows = computed(() => [...(data.value?.presentation?.dimensions || []).filter(r => ['growth','valuation','momentum'].includes(r.key)),...(data.value?.data?.rows || []).filter(r => r.key === 'risk')])
const tables = computed(() => [{title:'关键指标对照 · 五维',rows:data.value?.presentation?.dimensions || []},{title:'补充观察 · 保留已有指标',rows:data.value?.data?.rows || []}])
const label = { ALIGNED:'口径对齐', UNAVAILABLE:'缺少数据', METHOD_DIFFERENCE:'口径不同' }
</script>
<template><div class="page">
  <section class="panel panel-pad"><div class="section-heading"><h1>最新数据 · 双股对比</h1><span class="muted">沪深 A 股名称或代码 · 双方重新取数</span></div>
    <form class="bt-form" @submit.prevent="submit('research/comparisons',{entities:[left.trim(),right.trim()]})">
      <div class="bt-field"><label for="compare-left">第一只股票</label><input id="compare-left" v-model="left" placeholder="例如：美的集团 / 000333" maxlength="80" required /></div>
      <div class="bt-field"><label for="compare-right">第二只股票</label><input id="compare-right" v-model="right" placeholder="例如：格力电器 / 000651" maxlength="80" required /></div>
      <div class="bt-actions"><button class="btn btn-primary" :disabled="busy || data?.status === 'RUNNING' || !left.trim() || !right.trim()">开始最新对比</button></div>
    </form><p class="condition-note">双方分别运行 Planner → Data → Analyst → Verifier → Writer；同一请求时间不代表原子同步行情，结果逐模块标注实际日期。</p>
    <p v-if="error || data?.error" class="error-text" role="alert">{{ error || data.error }}</p>
    <div v-if="data" class="bt-actions"><span class="tag">{{ {RUNNING:'研究进行中',COMPLETED:'比较报告已生成',FAILED:'比较未完成',INTERRUPTED:'研究中断'}[data.status] || data.status }}</span><button class="btn" @click="load(data.run_id)">刷新状态</button><button v-if="['FAILED','INTERRUPTED'].includes(data.status)" class="btn btn-primary" :disabled="busy" @click="resume(data.run_id)">继续未完成部分</button><a class="btn" :href="'/api/quant/deliveries/' + data.run_id + '/export'" download="comparison.json">导出记录与依据</a></div>
  </section>
  <section v-if="data?.branches?.length" class="panel rep-section" style="margin-top:20px"><h2>双方研究过程</h2><div v-for="b in data.branches" :key="b.run_id" class="bt-check-item"><div><a :href="'#/live/' + b.run_id">{{ b.subject?.name || '证券确认中' }} · 查看完整研究 →</a><span class="tag">{{ b.status }}</span></div><p>{{ b.process?.map(p => p.role + ' ' + p.status).join(' → ') }}</p><p v-if="b.error" class="error-text">{{ b.error }}</p></div></section>
  <div v-if="data?.data" class="report-grid comparison-report" style="margin-top:20px">
    <aside class="report-rail"><div class="panel rail-box"><div class="section-title">比较信息</div><div v-for="s in data.data.subjects" :key="s.run_id" class="bt-check-item"><a :href="'#/live/' + s.run_id">{{ s.subject.name }}（{{ s.subject.code }}）</a><p>{{ s.subject.industry }}</p><p>财报 {{ s.data_dates.financial || '—' }}<br />价格 {{ s.data_dates.price || '—' }}</p></div><p class="small muted">请求 {{ dateTime(data.created_at) }}</p></div></aside>
    <article class="report-main"><h1 class="rep-h1">{{ data.title }}</h1>
      <section class="panel rep-section"><h2>结论速览</h2><div v-for="(row,i) in summaryRows" :key="row.key" class="conclusion-item"><span class="conclusion-number num">0{{ i+1 }}</span><div><div class="conclusion-tags"><span class="tag">{{ row.label }}</span><span class="tag" :class="row.alignment === 'ALIGNED' ? 'tag-cyan' : 'tag-warn'">{{ label[row.alignment] }}</span></div><p class="conclusion-text"><template v-for="(c,j) in row.cells" :key="j">{{ data.data.subjects[j].subject.name }} <button v-if="c" class="ev-num num" @click="showMetric(row,j)">{{ number(c.value,2) }}{{ row.unit }}</button><span v-else>数据待补</span>{{ j === 0 ? '；' : '。' }}</template></p><p class="comparison-caption">{{ row.caption || '商誉 / 归母权益，属于风险敞口观察' }}。差异需结合增长、行业和会计口径；不直接判断哪只股票更好。</p></div></div><p class="condition-note">成长看单季，估值看各自历史样本，动量看同一52周窗口。历史位置不等于便宜；模型解释与指标的外部独立核验分别记录。</p></section>
      <section v-for="table in tables" :key="table.title" class="panel rep-section"><h2>{{ table.title }}</h2><div class="table-scroll"><table class="data"><thead><tr><th>维度</th><th v-for="s in data.data.subjects" :key="s.run_id">{{ s.subject.name }}</th><th>差值（左 − 右）</th><th>口径</th></tr></thead><tbody><tr v-for="row in table.rows" :key="row.key"><td>{{ row.label }}<div class="comparison-caption">{{ row.caption }}</div></td><td v-for="(c,i) in row.cells" :key="i"><button v-if="c" class="ev-num num" @click="showMetric(row,i)">{{ number(c.value,2) }} {{ row.unit }}</button><span v-else>—</span><div class="comparison-caption">{{ row.dates[i] || '同口径输入未取得' }}</div></td><td class="num">{{ row.difference == null ? '—' : number(row.difference,2) + (row.unit === '%' ? ' 个百分点' : ' 倍') }}</td><td><span class="tag" :class="row.alignment === 'ALIGNED' ? 'tag-cyan' : 'tag-warn'">{{ label[row.alignment] }}</span><div class="comparison-caption">{{ row.note }}</div></td></tr></tbody></table></div></section>
      <section class="panel rep-section"><h2>{{ data.data.chart.title }}</h2><PrototypeChart :block="data.data.chart" :height="340" :empty="data.data.chart.x.length ? '' : '共同交易观察不完整，未插值补零'" /></section>
      <section v-for="s in data.data.subjects" :key="s.run_id" class="panel rep-section"><h2>{{ s.subject.name }} · 深研要点</h2><p v-for="(claim,i) in s.claims" :key="i" class="condition-note">{{ claim.text }} <a class="text-button" :href="'#/live/' + s.run_id">依据 {{ claim.evidence_ids?.join(' / ') }} →</a></p><a class="btn" :href="'#/live/' + s.run_id">查看财务图表、K线、DCF与风险扫描</a></section>
      <section class="panel rep-section"><h2>风险与分析局限</h2><ul class="bt-limitations"><li v-for="note in data.data.notes" :key="note">{{ note }}</li></ul></section>
      <div class="disclaimer">本工具为投研参考，不构成投资建议；历史数据不代表未来表现。</div>
    </article>
  </div>
<dialog v-if="selected" ref="evidenceDialog" class="scope-dialog live-source-dialog" aria-label="比较数值依据" @close="selected = null"><div class="dialog-head"><h2>{{ selected.subject.subject.name }} · {{ selected.cell.name }}</h2><button class="icon-button" aria-label="关闭比较依据" @click="evidenceDialog.close()">×</button></div><p class="ev-num num">{{ number(selected.cell.value,6) }} {{ selected.cell.unit }}</p><p>指标日期：{{ selected.date }} · 尚未完成官方独立核验</p><p>{{ selected.fact?.text }}</p><p class="small muted">定位 {{ selected.fact?.locator || selected.cell.name }}</p><div v-for="s in selected.sources" :key="s.key" class="bt-check-item"><strong>{{ s.key }}</strong><p>获取 {{ dateTime(s.retrieved_at) }}</p><p class="num live-hash">SHA256 {{ s.sha256 }}</p><a :href="s.url" target="_blank" rel="noopener">打开上游来源</a></div><a :href="'#/live/' + selected.subject.run_id">查看公式、交叉核对和完整研究 →</a></dialog>
</div></template>
