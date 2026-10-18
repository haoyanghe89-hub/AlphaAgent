<script setup>
import {computed} from 'vue'
import PrototypeChart from './PrototypeChart.vue'
import {technicalCharts} from './useInvestmentTechnicals.js'
const props=defineProps({stock:Object})
const charts=computed(()=>props.stock?technicalCharts(props.stock):[])
const fmt=v=>v==null?'尚无连续窗口':Number(v).toFixed(2)
const states={CROSS_UP:'刚上穿信号线',CROSS_DOWN:'刚下穿信号线',ABOVE_SIGNAL:'动量在信号线上方',BELOW_SIGNAL:'动量在信号线下方',ON_SIGNAL:'与信号线持平',INSUFFICIENT:'连续窗口不足'}
</script>
<template><details v-if="stock" class="technical-view"><summary>技术辅助 · {{ states[stock.macd_state] }} · RSI {{ fmt(stock.metrics.rsi_14.value) }} · 波动 {{ stock.metrics.atr_ratio.value==null?'—':fmt(Number(stock.metrics.atr_ratio.value)*100)+'%' }}</summary><PrototypeChart v-for="chart in charts" :key="chart.kind" :block="chart" :height="180"/><p class="condition-note">MACD柱为 DIF−DEA，不乘二。与均线同属价格证据；震荡交叉可能反复，RSI极端不代表即刻反转，ATR不判断涨跌。辅助指标尚未验证预测增益，入场与退出纪律仍按原路线执行。</p></details></template>
<style scoped>.technical-view{border-top:1px solid var(--border);margin-top:12px;padding-top:12px;font-size:12px}summary{cursor:pointer;line-height:1.8}summary:focus-visible{outline:2px solid var(--accent)}.condition-note{color:var(--text-2)}</style>
