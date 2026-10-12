<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useTheme } from '../../composables/useTheme.js'
import { chartOptions, seriesLabel } from '../../utils/quantChartOptions.js'
import { cssChartPalette } from '../../utils/prototypeChartOptions.js'
const props = defineProps({ exhibit: { type: Object, required: true } })
const emit = defineEmits(['cell'])
const root = ref(null), error = ref(''), adjustment = ref('raw')
const { theme } = useTheme()
const adjustments = computed(() => ['raw','qfq','hfq'].filter(a => props.exhibit.series.some(s => s.name === 'chart_price_' + a)))
const selectedSeries = computed(() => props.exhibit.series.filter(s => props.exhibit.kind !== 'price' || s.name.endsWith('_' + adjustment.value)))
let chart, observer, media, disposed = false
function render() {
  if (!chart) return
  const style = getComputedStyle(document.documentElement)
  chart.setOption(chartOptions(props.exhibit, { adjustment: adjustment.value, reducedMotion: media.matches, palette: cssChartPalette(style) }), true)
  root.value.dataset.chartAnimation = String(chart.getOption().animation)
  const option = chart.getOption()
  root.value.dataset.chartStyle = JSON.stringify({ color: option.color, grid: option.grid[0], legend: option.legend[0], textStyle: option.textStyle, tooltip: option.tooltip[0] })
}
onMounted(async () => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  media.addEventListener('change', render)
  try {
    const { mountChart } = await import('../../utils/quantEcharts.js')
    if (disposed) return
    chart = mountChart(root.value)
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(root.value); render()
  } catch { error.value = '图表加载失败，请展开下方原值与定位表。' }
})
watch([() => props.exhibit, adjustment, theme], render)
onUnmounted(() => { disposed = true; observer?.disconnect(); media?.removeEventListener('change', render); chart?.dispose(); chart = null })
</script>
<template>
  <figure class="quant-exhibit">
    <figcaption><span class="exhibit-number">{{ exhibit.exhibit_id }}</span><h3>{{ exhibit.title }}</h3><span class="badge unverified">UNVERIFIED · 尚未证实</span><p>{{ exhibit.note }}</p><small>研究时点 {{ exhibit.asof }}</small></figcaption>
    <div v-if="exhibit.kind === 'price'" class="adjustments" role="group" aria-label="冻结价格口径"><button v-for="a in adjustments" :key="a" class="quiet-button" :aria-pressed="adjustment === a" @click="adjustment = a">{{ a }}</button></div>
    <p v-if="exhibit.drawdown" class="muted">当前运行口径的最大回撤峰值 {{ exhibit.drawdown.peak_date || '缺失' }} · 谷值 {{ exhibit.drawdown.trough_date || '缺失' }} · {{ exhibit.drawdown.recovery_date ? '恢复于 ' + exhibit.drawdown.recovery_date : '未观察到恢复日（或无回撤）' }}</p>
    <div ref="root" class="chart-canvas" role="img" :aria-label="exhibit.title + '；可展开原值表进行键盘访问'" :data-chart-kind="exhibit.kind"></div>
    <p v-if="error" role="alert">{{ error }}</p>
    <details class="chart-data"><summary>原值与证据定位</summary><p class="muted">{{ exhibit.note }} · 研究时点 {{ exhibit.asof }}</p><div class="chart-table-scroll" tabindex="0" aria-label="原值数据表，可水平滚动"><table><thead><tr><th>序列 / 报告期</th><th>冻结原值 / 单位</th><th>定位</th></tr></thead><tbody><template v-for="series in selectedSeries" :key="series.name"><tr v-for="point in series.points" :key="point.metric_path"><td>{{ seriesLabel(series.name) }}<br>{{ point.x }}</td><td>{{ point.value ?? '缺失' }} {{ series.unit }}<small v-if="point.missing_reason"><br>{{ point.missing_reason }}</small></td><td><button class="quiet-button" @click="emit('cell', point.metric_path)">{{ point.metric_path }}</button></td></tr></template></tbody></table></div></details>
  </figure>
</template>
<style scoped>
.quant-exhibit { margin: var(--space-10) 0; padding-block: var(--space-6); border-block: 1px solid var(--border-default); min-width: 0; }
.exhibit-number { color: var(--accent); font-family: var(--font-mono); font-size: var(--font-size-overline); letter-spacing: var(--letter-spacing-overline); }
figcaption h3 { margin: var(--space-2) 0; }figcaption p, .muted { color: var(--text-secondary); font-size: var(--font-size-small); }figcaption small { color: var(--text-muted); }
.chart-canvas { width: 100%; height: 320px; min-width: 0; }.adjustments { display: flex; gap: var(--space-3); margin-block: var(--space-3); }.adjustments button[aria-pressed='true'] { color: var(--accent); text-decoration: underline; }
.chart-table-scroll { max-width: 100%; overflow: auto; }table { width: 100%; border-collapse: collapse; font-size: var(--font-size-caption); }td,th { text-align: left; padding: var(--space-2); border-bottom: 1px solid var(--border-default); }td:nth-child(2) { font-family: var(--font-mono); white-space: nowrap; }
@media(max-width:480px) { .chart-canvas { height: 260px; } }
@media(prefers-reduced-motion:reduce) { * { transition: none !important; animation: none !important; } }
</style>
