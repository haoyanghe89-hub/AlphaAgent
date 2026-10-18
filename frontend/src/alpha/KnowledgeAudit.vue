<script setup>
import { ref } from 'vue'
import KnowledgeLibrary from './KnowledgeLibrary.vue'
import MethodRulesAudit from './MethodRulesAudit.vue'
import KnowledgeRelease from './KnowledgeRelease.vue'
const tab=ref('rules')
</script>
<template>
  <div class="page knowledge-audit-navigation"><div class="bt-actions"><button class="btn" :class="{'btn-primary':tab==='rules'}" @click="tab='rules'">方法规则</button><button class="btn" :class="{'btn-primary':tab==='sources'}" @click="tab='sources'">封存原文</button><a class="btn" href="#/">返回工作台</a></div></div>
  <KnowledgeRelease developer />
  <MethodRulesAudit v-if="tab==='rules'" />
  <KnowledgeLibrary v-else />
</template>
<style scoped>.knowledge-audit-navigation{padding-bottom:0}</style>
