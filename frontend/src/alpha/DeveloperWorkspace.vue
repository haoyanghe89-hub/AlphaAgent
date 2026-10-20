<script setup>
import {onMounted,ref} from 'vue'
import request from '../api/request.js'
import {developerHref} from './uiPresentation.js'
const props=defineProps({enabled:Boolean})
const daily=ref(null),health=ref(null),errors=ref([])
const enter=developerHref(location.href,true,'developer')
const userHome=developerHref(location.href,false,'workbench')
async function refresh(){
  const results=await Promise.allSettled([request('/health'),request('/quant/daily/status')])
  health.value=results[0].status==='fulfilled'?results[0].value:null
  daily.value=results[1].status==='fulfilled'?results[1].value:null
  errors.value=results.filter(r=>r.status==='rejected').map(r=>r.reason.message)
}
onMounted(()=>{if(props.enabled)refresh()})
</script>
<template>
  <div class="page developer-workspace">
    <header class="page-heading"><div><h1>开发者工作区</h1><p class="muted">运行状态、知识版本、研究过程与交付记录</p></div><a class="btn" href="#/">打开工作台</a></header>
    <section v-if="!enabled" class="panel rep-section"><h2>开发者工具</h2><p>分析过程、调用和工程核验在此集中查看。</p><a class="btn btn-primary" :href="enter">进入开发者视图</a></section>
    <template v-else>
      <section class="panel rep-section"><div class="section-heading"><h2>本地服务</h2><button class="btn" @click="refresh">刷新状态</button></div><p v-for="error in errors" :key="error" role="alert" class="error-text">{{ error }}</p><div class="summary-metrics"><span>服务<b>{{ health?.status||'读取中' }}</b></span><span>每日更新<b>{{ daily==null?'读取中':daily.enabled?'已开启':'未开启' }}</b></span><span>数据连接<b>{{ daily?.source?.status||'读取中' }}</b></span></div><details><summary>调度与数据通道详细状态</summary><pre>{{ daily }}</pre></details></section>
      <div class="developer-grid">
        <section class="panel rep-section"><h2>验收与数据接入</h2><p>查看既有交付记录、历史包合同、来源覆盖和回测准入。</p><a class="btn" href="#/delivery">交付与接入记录</a><a class="btn" href="#/backtest">历史数据取证</a><a class="btn" href="#/strategy">策略与准入检查</a></section>
        <section class="panel rep-section"><h2>内置知识体系</h2><p>查看已审核方法、原文、固定版本、知识发布与候选补充。</p><a class="btn" href="#/knowledge">知识与方法核验</a></section>
        <section class="panel rep-section"><h2>逐份研究过程</h2><p>从历史库打开任意研究，本模式显示对应的职责分析、调用预算、固定输入和计算记录。</p><a class="btn" href="#/library">打开完整历史库</a></section>
        <section class="panel rep-section"><h2>模型与知识效果对照</h2><p>以同一封存输入比较原流程和增强流程的方向、论证、耗时与调用费用；到期后核对真实预测表现。</p><a class="btn" href="#/evaluation">打开实源对照评测</a></section>
        <section class="panel rep-section"><h2>用户页面</h2><p>页面底部可切回同一份报告的用户视图。切换展示保留原ID、输入、结果与历史，不改变知识发布或回测准入权限。</p><a class="btn" :href="userHome">返回日常工作台</a></section>
      </div>
    </template>
  </div>
</template>
<style scoped>.developer-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.developer-grid .btn{margin:8px 8px 0 0}.developer-grid p{color:var(--text-2)}@media(max-width:700px){.developer-grid{grid-template-columns:1fr}}</style>
