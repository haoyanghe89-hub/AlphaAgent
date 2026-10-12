<script setup>
import { computed, ref } from 'vue'
const props = defineProps({ nodes: Array, status: String })
const count = computed(() => (props.nodes || []).filter(n => ['COMPLETED','DEGRADED'].includes(n.status)).length)
const expanded = ref(false)
const labels = { NOT_RECORDED: '无记录', RUNNING: '执行中', COMPLETED: '已完成', DEGRADED: '已降级' }
</script>
<template>
  <section class="panel proc-card" aria-label="研究过程">
    <div class="proc-head" :class="{ collapsed: status === 'COMPLETED' && !expanded }"><span v-if="status === 'COMPLETED'" class="proc-dot" style="width:14px;height:14px;font-size:9px">✓</span><span class="proc-title" :style="{ color: status === 'COMPLETED' ? 'var(--text-2)' : 'var(--accent)' }">研究过程{{ status === 'COMPLETED' ? ' · 已完成' : '' }}</span><span v-if="status === 'COMPLETED'" class="proc-counter">{{ count }}/{{ nodes?.length }} 步</span><button v-if="status === 'COMPLETED'" class="text-button" @click="expanded = !expanded">{{ expanded ? '收起过程' : '展开过程' }}</button></div>
    <div v-if="status !== 'COMPLETED' || expanded" class="proc-steps"><div v-for="node in nodes" :key="node.stage" class="proc-step" :class="[node.status.toLowerCase(), { done: node.status === 'COMPLETED' }] "><span class="proc-dot">{{ node.status === 'COMPLETED' ? '✓' : node.status === 'DEGRADED' ? '!' : '' }}</span><div class="proc-body"><div class="proc-name">{{ node.title }}<span v-if="['RUNNING','DEGRADED'].includes(node.status)" class="tag" :class="node.status === 'RUNNING' ? 'tag-gold' : 'tag-warn'">{{ labels[node.status] }}</span></div><p class="proc-detail">{{ node.detail }}</p></div></div></div>
  </section>
</template>
