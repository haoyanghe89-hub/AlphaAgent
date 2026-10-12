<script setup>
import { number } from './model.js'
defineProps({ reading: Object })
defineEmits(['derived'])
</script>

<template>
  <div v-if="reading?.dcf.status === 'AVAILABLE'" class="reader-dcf" aria-label="DCF 假设解读">
    <h3>这个估值对假设有多敏感？</h3>
    <p class="condition-note">本情景设定前 5 年现金流每年增长 <strong>{{ number(reading.dcf.assumptions.g1) }}%</strong>、后 5 年增长 {{ number(reading.dcf.assumptions.g2) }}%、永续增长 {{ number(reading.dcf.assumptions.g_term) }}%，折现率 {{ number(reading.dcf.assumptions.discount) }}%。这些是计算假设，尚需业务和行业依据支持。</p>
    <p class="condition-note">固定其余输入，只调整前 5 年现金流增长率，结果如下。更低增长假设下的估值也更低，不能只看单个情景数字。</p>
    <div class="reader-dcf-scroll"><table class="data"><thead><tr><th>前 5 年现金流增长假设</th><th v-for="row in reading.dcf.growth_sensitivity" :key="row.g1">{{ number(row.g1) }}%</th></tr></thead><tbody><tr><td>每股情景估算</td><td v-for="row in reading.dcf.growth_sensitivity" :key="row.g1"><button class="ev-num num" :aria-label="'查看增长假设 ' + row.g1 + '% 的 DCF 依据'" @click="$emit('derived',reading.dcf.proof)">{{ number(row.per_share) }} 元</button></td></tr></tbody></table></div>
    <p class="condition-note">{{ reading.dcf.note }}</p>
  </div>
</template>

<style scoped>
.reader-dcf { border-top:1px solid var(--border); margin-top:20px; padding-top:4px }.reader-dcf-scroll { max-width:100%; overflow-x:auto }.reader-dcf table { min-width:440px }.reader-dcf strong { color:var(--accent-cyan) }.reader-dcf button { font:inherit; padding:0; background:none; border:0; border-bottom:1px dashed currentColor; cursor:pointer }.reader-dcf button:focus-visible { outline:2px solid var(--accent-cyan); outline-offset:3px }
</style>
