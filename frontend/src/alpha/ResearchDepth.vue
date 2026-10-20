<script setup>
import {researchDepth} from './researchDepth.js'
defineProps({continuing:Boolean})
</script>
<template>
  <div class="research-depth">
    <label>研究深度 <select v-model="researchDepth" aria-label="研究深度"><option value="STANDARD">标准研究</option><option value="DEEP">深入研究</option></select></label>
    <span>{{ continuing?'继续追问沿用原研究；更新资料时采用此选择。':researchDepth==='DEEP'?'增加方法选择与补充核查，耗时会增加；效果仍在验证。':'使用大模型与统一知识体系；需要补充核查时可选择深入研究。' }}</span>
  </div>
</template>
<style scoped>.research-depth{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:12px 0;font-size:12px;color:var(--text-2)}.research-depth label{display:flex;align-items:center;gap:8px}.research-depth select{width:auto;padding:6px 8px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text-1);border-radius:4px}</style>
