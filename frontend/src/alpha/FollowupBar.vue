<script setup>
import { onUnmounted, ref } from 'vue'
import { useFollowup } from './useFollowup.js'
import { href } from './navigation.js'
const props = defineProps({ id: String, running: Boolean })
const question = ref('')
const { busy, error, submit, dispose } = useFollowup(props.id, { started: result => { location.hash = href('research', result.run_id) } })
onUnmounted(dispose)
</script>
<template>
  <div class="flow-input-bar">
    <form class="flow-input" @submit.prevent="submit(question)"><input v-model="question" aria-label="研究追问" :placeholder="running ? '研究进行中，可先输入追问…' : '追问：如「现金流再细看」「增长率按 12% 重算」'" :disabled="busy" maxlength="2000" /><button class="btn btn-primary" :disabled="busy || running || !question.trim()">{{ busy ? '启动中…' : '追问' }}</button></form>
    <p v-if="error" class="error-text" role="alert">{{ error }}</p>
    <div class="flow-note">追问会开启新任务，历史版本保留在历史库</div>
    <details class="flow-note"><summary>当前可用追问范围</summary>原数据复算：“重新核对收益与风险”“按前复权重算”“无风险利率按0重算”。其他分析与假设会提示尚未接入。</details>
  </div>
</template>
