<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useTheme } from '../composables/useTheme.js'
import { cssChartPalette, prototypeOptions } from '../utils/prototypeChartOptions.js'
const props = defineProps({ block: { type: Object, required: true }, height: { type: Number, default: 300 }, empty: String })
const emit = defineEmits(['point'])
const { theme } = useTheme(), root = ref(null), error = ref('')
let chart, observer, media, disposed = false
function render() {
  if (!chart) return
  chart.setOption(prototypeOptions(props.block, { palette: cssChartPalette(getComputedStyle(document.documentElement)), reducedMotion: media.matches }), true)
  const option = chart.getOption()
  root.value.dataset.chartAnimation = String(option.animation)
  root.value.dataset.chartStyle = JSON.stringify({ color: option.color, grid: option.grid, legend: option.legend[0], textStyle: option.textStyle, dataZoom: option.dataZoom, series: option.series.map(s => ({ name: s.name, type: s.type, lineStyle: s.lineStyle, data: s.data })) })
}
onMounted(async () => {
  media = matchMedia('(prefers-reduced-motion: reduce)'); media.addEventListener('change', render)
  try {
    const { mountChart } = await import('../utils/quantEcharts.js')
    if (disposed) return
    chart = mountChart(root.value); chart.on('click', params => { if (props.block.x?.[params.dataIndex]) emit('point', props.block.x[params.dataIndex]) }); observer = new ResizeObserver(() => chart?.resize()); observer.observe(root.value); render()
  } catch { error.value = '图表加载失败。请刷新页面重试。' }
})
watch([() => props.block, theme], render)
onUnmounted(() => { disposed = true; observer?.disconnect(); media?.removeEventListener('change', render); chart?.dispose() })
</script>
<template><div class="prototype-chart" :data-prototype-chart="block.kind" :style="{ height: height + 'px' }"><div ref="root" class="prototype-chart-canvas" role="img" :aria-label="block.title || block.kind" :style="{ height: height + 'px' }"></div><div v-if="empty || error" class="prototype-chart-empty" role="status">{{ error || empty }}</div></div></template>
