<script setup>
import { computed } from 'vue'
import QuantChart from '../components/quant/QuantChart.vue'
import PrototypeChart from './PrototypeChart.vue'
import DcfPlayground from './DcfPlayground.vue'
import KlineChart from './KlineChart.vue'
import { metricNames, metricValue } from './model.js'
const props = defineProps({ data: Object })
const emit = defineEmits(['cell'])
const chart = kind => props.data?.charts?.find(c => c.kind === kind)
const metrics = names => (props.data?.values || []).filter(c => names.includes(c.name))
const sourceCells = computed(() => (props.data?.values || []).filter(c => !c.name.startsWith('chart_')))
const blocks = {
  financial: { kind: 'financial_quarterly', title: '单季营收 / 归母净利（亿元）与营收同比', chart_type: 'bar', series: [{ name: '单季营收(亿)' }, { name: '单季净利(亿)' }, { name: '营收同比%' }] },
  pe: { kind: 'pe_history', title: 'PE-TTM 历史序列（自算口径）', chart_type: 'line', series: [{ name: 'PE-TTM', lineStyle: { width: 1.2 } }] },
  kline: { kind: 'kline', title: '日K线（近一年，前复权）+ MA20/MA60' },
  margin: { kind: 'margin', title: '融资余额与融资净买入（亿元）', series: [{ name: '融资余额(亿)', lineStyle: { width: 1.5 }, areaStyle: { opacity: 0.08 } }, { name: '融资净买入(亿)', type: 'bar', axis: 1, barMaxWidth: 6 }] },
}
const risks = ['股权质押', '商誉与减值', '限售股解禁', 'ST / 退市风险', '财务异常与审计意见']
</script>
<template>
  <section id="sec-fundamental" class="panel rep-section prototype-research-section"><h2>基本面分析</h2>
    <div class="report-blocks"><table class="data"><thead><tr><th>指标</th><th>数值</th><th>报告期</th><th>核对状态</th></tr></thead><tbody><tr v-for="c in metrics(['roe','revenue_growth','net_profit_growth'])" :key="c.metric_path"><td>{{ metricNames[c.name] }}</td><td><button class="ev-num num" @click="emit('cell', c.metric_path)">{{ metricValue(c) }}</button></td><td>{{ c.end || '—' }}</td><td>未证实</td></tr></tbody></table>
      <div class="report-chart-block"><div class="report-chart-title">{{ blocks.financial.title }}</div><PrototypeChart :block="blocks.financial" empty="缺少经过口径核对的单季营收、归母净利与同比数据" /></div>
      <QuantChart v-if="chart('financial')" :exhibit="chart('financial')" @cell="emit('cell', $event)" />
    </div>
  </section>
  <section id="sec-valuation" class="panel rep-section prototype-research-section"><h2>估值分析</h2><div class="report-blocks">
    <div class="report-chart-block"><div class="report-chart-title">{{ blocks.pe.title }}</div><PrototypeChart :block="blocks.pe" empty="尚无按日绑定时点财报与股本的 PE-TTM 历史序列" /></div>
    <QuantChart v-if="chart('valuation')" :exhibit="chart('valuation')" @cell="emit('cell', $event)" />
    <DcfPlayground :data="data" @cell="emit('cell', $event)" />
  </div></section>
  <section id="sec-market" class="panel rep-section prototype-research-section"><h2>技术面与资金面</h2><div class="report-blocks">
    <KlineChart :data="data" @cell="emit('cell', $event)" />
    <div class="report-chart-block"><div class="report-chart-title">{{ blocks.margin.title }}</div><PrototypeChart :block="blocks.margin" empty="暂无已核对的融资余额与融资净买入数据" /></div>
    <QuantChart v-if="chart('price')" :exhibit="chart('price')" @cell="emit('cell', $event)" />
  </div></section>
  <section id="sec-prototype-risks" class="panel rep-section prototype-research-section"><h2>A股特色风险扫描</h2><div v-for="risk in risks" :key="risk" class="prototype-risk"><span class="tag tag-warn">未覆盖</span><div><div>{{ risk }}</div><p>缺少截至研究时点的可追溯资料，尚不能判断风险等级。</p></div></div></section>
  <section id="sec-evidence" class="panel rep-section prototype-research-section"><h2>数据与来源总览</h2><div class="report-chart-title">本次研究引用的全部数值及其来源（点击数字查看同源信息）</div><div class="report-table-scroll"><table class="data"><thead><tr><th>指标</th><th>数值</th><th>来源</th><th>数据时点</th><th>计算方式</th></tr></thead><tbody><tr v-for="c in sourceCells" :key="c.metric_path"><td>{{ metricNames[c.name] || c.name }}</td><td><button class="ev-num num" @click="emit('cell', c.metric_path)">{{ metricValue(c) }}</button></td><td>{{ c.provider || '独立计算产物' }}</td><td>{{ c.end || data.asof }}</td><td><button class="text-button" @click="emit('cell', c.metric_path)">查看口径与输入</button></td></tr></tbody></table></div></section>
</template>
