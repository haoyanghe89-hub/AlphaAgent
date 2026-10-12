<script setup>
import { ref } from 'vue'
import { dateTime, metricValue } from './model.js'
import { href } from './navigation.js'
import CellEvidence from './CellEvidence.vue'
import PrototypeChart from './PrototypeChart.vue'
import QuantChart from '../components/quant/QuantChart.vue'
const props = defineProps({ data:Object, report:Object, id:String })
const selected = ref(null)
const names = {MATCH:'口径对齐', METHOD_DIFFERENCE:'口径不同', UNAVAILABLE:'数据缺失'}
const labels = {revenue_growth:'累计营业收入同比', net_profit_growth:'累计归母净利同比', roe:'TTM 平均归母权益 ROE', pe:'PE（归母总额口径）'}
const financial = {kind:'financial_quarterly', chart_type:'bar', title:'单季营收/净利（亿元）', series:[{name:'单季营收(亿)'},{name:'单季净利(亿)'}]}
function select(source, cell) { if (cell) selected.value = {cell, data:source} }
function chartCell(source, path) { select(source, source.data.values.find(c => c.metric_path === path)) }
function scrollTo(id) { document.getElementById(id)?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'}) }
</script>
<template>
  <div v-if="data.comparison" class="report-grid comparison-report">
    <aside class="report-rail">
      <div class="panel rail-box"><div class="section-title">报告信息</div><div class="kv"><span class="k">标的</span><span><a v-for="s in data.subjects" :key="s.run_id" :href="href('stock',s.subject.code)" style="margin-left:4px">{{ s.subject.name }}</a></span></div><div class="kv"><span class="k">场景</span><span>对比分析</span></div><div class="kv"><span class="k">数据截至</span><span class="num">{{ data.plan.date_end }}</span></div><div class="kv"><span class="k">生成时间</span><span class="num">{{ dateTime(report.created_at) }}</span></div><div class="kv"><span class="k">版本</span><span class="num">v{{ report.version }}</span></div><div class="kv"><span class="k">证据数</span><span class="num">{{ data.evidence_count }}</span></div></div>
      <nav class="panel rail-box rail-toc" aria-label="报告目录"><div class="section-title">目录</div><a href="#sec-conclusions" @click.prevent="scrollTo('sec-conclusions')">结论速览</a><a href="#sec-overview" @click.prevent="scrollTo('sec-overview')">关键指标对照</a><a v-for="s in data.subjects" :key="s.run_id" :href="'#sec-detail-' + s.subject.code" @click.prevent="scrollTo('sec-detail-' + s.subject.code)">{{ s.subject.name }}深研要点</a><a href="#sec-risks" @click.prevent="scrollTo('sec-risks')">风险与局限</a></nav>
      <div class="panel rail-box muted" style="font-size:11.5px;line-height:1.7">带虚线下划线的数字可点击查看来源、时间戳与计算口径。<div class="rail-actions"><a :href="href('research',id)">查看研究过程</a><a :href="'/api/quant/runs/' + id + '/comparison-export'">导出 Markdown</a></div></div>
    </aside>
    <article class="report-main">
      <h1 class="rep-h1">{{ data.plan.question }}</h1>
      <section id="sec-conclusions" class="panel rep-section"><h2>结论速览</h2>
        <div class="conclusion-item"><span class="conclusion-number">01</span><div><div class="conclusion-tags"><span class="tag">综合</span><span class="tag tag-warn">未证实</span></div><p class="rep-block-text">两股采用 {{ data.plan.date_start }} 至 {{ data.plan.date_end }} 的同一冻结区间，按{{ {raw:'未复权价格',qfq:'前复权冻结代理',hfq:'后复权冻结代理'}[data.plan.adjustment] }}并列核对。五维中的缺项保留，不计算领先维度数量或综合排名。</p></div></div>
        <div v-for="(s,index) in data.subjects" :key="s.run_id" class="conclusion-item"><span class="conclusion-number">{{ String(index+2).padStart(2,'0') }}</span><div><div class="conclusion-tags"><span class="tag">{{ s.subject.name }}</span><span class="tag tag-warn">未证实</span></div><p class="rep-block-text">区间价格收益 <button class="ev-num num" @click="select(s,s.data.values.find(c => c.name === 'return'))">{{ metricValue(s.data.values.find(c => c.name === 'return')) }}</button>。财务与估值观察见对应深研要点；历史表现不代表未来收益。</p></div></div>
      </section>
      <section id="sec-overview" class="panel rep-section"><h2>关键指标对照</h2><div class="report-blocks">
        <div class="report-table-scroll"><table class="data comparison-dimensions"><thead><tr><th>维度</th><th v-for="s in data.subjects" :key="s.run_id">{{ s.subject.name }}</th><th>该维度结论</th></tr></thead><tbody><tr v-for="row in data.comparison.dimensions" :key="row.key"><td>{{ row.label }}<div class="comparison-caption">{{ row.caption }}</div></td><td v-for="(c,index) in row.cells" :key="index"><button v-if="c" class="ev-num num" @click="select(data.subjects[index],c)">{{ metricValue(c) }}</button><span v-else class="muted">缺失</span><div v-if="c" class="comparison-caption">{{ c.start }} — {{ c.end }}</div></td><td><span :class="['tag',row.alignment === 'MATCH' ? 'tag-cyan' : 'tag-warn']">{{ names[row.alignment] }}</span><div class="comparison-caption">{{ row.note }}</div></td></tr></tbody></table></div>
        <p class="muted small">口径对齐仅检查期间、单位和定义；指标仍保留原验证状态。行业与会计政策差异尚未调整。</p>
        <details class="comparison-extras"><summary>补充观察 · 保留既有真实指标</summary><div class="report-table-scroll"><table class="data"><thead><tr><th>指标</th><th v-for="s in data.subjects" :key="s.run_id">{{ s.subject.name }}</th><th>口径核对</th></tr></thead><tbody><tr v-for="row in data.comparison.supplementary" :key="row.key"><td>{{ row.label }}</td><td v-for="(c,index) in row.cells" :key="index"><button v-if="c" class="ev-num num" @click="select(data.subjects[index],c)">{{ metricValue(c) }}</button><span v-else>—</span></td><td :title="row.note">{{ names[row.alignment] }}</td></tr></tbody></table></div></details>
      </div></section>
      <section v-for="s in data.subjects" :id="'sec-detail-' + s.subject.code" :key="s.run_id" class="panel rep-section comparison-detail"><h2>{{ s.subject.name }}（{{ s.subject.code }}）深研要点</h2><div class="report-blocks">
        <p class="rep-block-text"><template v-for="name in ['revenue_growth','net_profit_growth','roe','pe']" :key="name">{{ labels[name] }} <button class="ev-num num" @click="select(s,s.data.values.find(c => c.name === name))">{{ metricValue(s.data.values.find(c => c.name === name)) }}</button>；</template></p>
        <div class="report-chart-block"><div class="report-chart-title">单季营收/净利（亿元）</div><PrototypeChart :block="financial" empty="缺少统一口径的单季营收与归母净利金额；不以累计金额替代" /></div>
        <QuantChart v-for="chart in s.data.charts" :key="chart.kind" :exhibit="chart" @cell="chartCell(s,$event)" />
        <a :href="href('report',s.run_id)">查看 {{ s.subject.name }} 的完整原研究、K线与DCF适用状态 →</a>
        <details><summary>依据链与缺项</summary><p class="muted small">研究时点 {{ s.plan.asof }}；价格口径 {{ s.plan.adjustment }}。完整来源、期间与公式见可点击数值。</p><ul class="comparison-limits"><li v-for="gap in s.data.gaps" :key="gap[0]">{{ gap[1] }}</li></ul></details>
      </div></section>
      <section id="sec-risks" class="panel rep-section"><h2>风险与分析局限</h2><ul class="comparison-limits"><li v-for="note in data.comparison.limitations" :key="note">{{ note }}</li></ul></section>
      <div class="disclaimer">本工具为投研参考，不构成投资建议，不提供买卖指令。历史数据不代表实时行情或未来表现。</div>
    </article>
    <CellEvidence v-if="selected" :cell="selected.cell" :data="selected.data" @close="selected=null" />
  </div>
  <section v-else class="panel panel-pad"><h2>比较材料未完整生成</h2><p class="muted">{{ data.run.interruption_reason || '支路或证据核验受阻；不展示不完整的比较数值。' }}</p><a :href="href('research',id)">回看研究支路</a></section>
</template>
