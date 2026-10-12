<script setup>
import { metricNames, metricValue, movement } from './model.js'
defineProps({ cells: { type: Array, default: () => [] } })
defineEmits(['cell'])
</script>
<template><div class="metric-grid"><div v-for="cell in cells" :key="cell.metric_path" class="metric-cell"><div class="m-label">{{ metricNames[cell.name] || cell.name }}</div><button class="m-value ev-num num" :class="['return','cagr','net_profit_growth','revenue_growth'].includes(cell.name) ? movement(cell.value) : ''" :aria-label="(metricNames[cell.name] || cell.name) + '数值依据'" @click="$emit('cell', cell)">{{ metricValue(cell) }}</button><span class="m-status">{{ cell.value == null ? '数据缺失' : cell.status === 'VERIFIED' ? '已核验' : '未验证' }}</span></div></div></template>
