<script setup>
defineProps({dialogue:Object})
</script>
<template>
  <section v-if="dialogue" class="panel rep-section focused-answer" aria-label="本轮追问回答">
    <div class="section-heading"><h2>针对这次问题</h2><span v-if="dialogue.inherited" class="tag">衔接上轮</span></div>
    <p v-if="dialogue.target_entities?.length" class="muted">{{ dialogue.target_entities.join('、') }}</p>
    <p v-if="dialogue.summary"><strong>{{ dialogue.summary }}</strong></p>
    <p v-for="(line,index) in dialogue.lines||[]" :key="index">{{ line }}</p>
    <p v-if="dialogue.model_used===false" class="condition-note">本轮使用本地规则回答；模型理解暂未完成，原研究及持仓计算保留。</p>
  </section>
</template>
<style scoped>
.focused-answer{border-left:3px solid var(--accent)}.focused-answer p{line-height:1.75;margin:8px 0}.focused-answer h2{margin:0}
</style>
