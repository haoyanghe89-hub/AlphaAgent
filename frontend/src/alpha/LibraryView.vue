<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { dateTime, historyStatus, isHistoryActive, isHistoryDone } from './model.js'
import { href } from './navigation.js'
import { deliveryPage } from './useDelivery.js'
import { userHistoryItem } from './uiPresentation.js'
const props = defineProps({ items: Array, legacy: Array, loading: Boolean, error: String, hasMore: Boolean })
const emit=defineEmits(['retry', 'more', 'search'])
const query = ref(''), status = ref('all'), kind = ref('all')
const developer = new URLSearchParams(location.search).get('developer') === '1'
let searchTimer
watch(query,value=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>emit('search',value.trim()),300)})
onUnmounted(()=>clearTimeout(searchTimer))
const filtered = computed(() => (props.items || []).filter(i =>
  (developer || userHistoryItem(i)) &&
  (kind.value === 'all' || (kind.value === 'investment' ? ['investment','investment_research'].includes(i.scenario) : i.scenario === kind.value)) &&
  (!query.value || (i.title + i.run_id).toLowerCase().includes(query.value.trim().toLowerCase())) &&
  (status.value === 'all' || (status.value === 'done' ? isHistoryDone(i) : status.value === 'active' ? isHistoryActive(i) : !isHistoryDone(i) && !isHistoryActive(i)))))
const older = computed(() => (props.legacy || []).filter(i => !query.value || i.title.toLowerCase().includes(query.value.trim().toLowerCase())))
</script>
<template><div class="page"><div class="section-heading"><h1>历史研究库</h1><span class="muted">研究报告与回测记录，可回看核对</span></div><div class="library-tools"><input v-model="query" type="search" aria-label="搜索历史研究" placeholder="搜索问题、标的或任务编号" /><select v-model="kind" aria-label="研究类型"><option value="all">全部研究类型</option><option value="next_session">明日预测</option><option value="investment">投资计划</option><option value="holding_advisor">持仓顾问</option><option value="portfolio_advisor">账户调仓</option><option value="morning_brief">每日清单</option><option value="decision">中期 / 市场 / 行业</option><option value="live">单股数据与估值</option><option value="strategy">策略回测</option></select><select v-model="status" aria-label="研究状态"><option value="all">全部状态</option><option value="done">已成稿 / 检查完成</option><option value="active">执行中 / 成稿中</option><option value="attention">需关注 / 已中断</option></select><button class="btn" :disabled="loading" @click="$emit('retry')">刷新</button></div><p v-if="error" class="notice error-text" role="alert">{{ error }}</p><p v-if="loading && !items?.length" class="panel empty-state" role="status">正在读取历史记录…</p><div v-else-if="filtered.length" class="panel"><div v-for="item in filtered" :key="item.run_id" class="lib-row"><span class="tag tag-gold">{{ item.scenario==='portfolio_advisor'?'账户调仓':item.scenario==='holding_advisor'?'持仓顾问':['investment','investment_research'].includes(item.scenario) ? '投资计划' : item.scenario === 'next_session' ? '明日预测' : item.scenario === 'morning_brief' ? '盘前清单' : item.scenario === 'live' ? '实时研究' : item.scenario === 'backtest' ? '回测检查' : item.scenario === 'compare_live' || item.scenario === 'compare' ? '对比' : item.scenario === 'strategy' ? '策略回测' : '深研' }}</span><a class="lib-q" :href="href(deliveryPage(item), item.run_id)">{{ item.title }}</a><span v-if="item.report_version" class="num lib-subject">v{{ item.report_version }}</span><span v-else class="tag">{{ historyStatus(item) }}</span><time class="lib-date">{{ dateTime(item.created_at) }}</time></div></div><div v-else class="panel empty-state">{{ query || status !== 'all' || kind !== 'all' ? '没有匹配的记录，尝试调整筛选条件。' : '还没有研究记录，去工作台运行第一个任务。' }} <a :href="href('workbench')">工作台</a></div><button v-if="hasMore" class="btn more-button" :disabled="loading" @click="$emit('more')">加载更多记录</button><section v-if="older.length && status === 'all' && kind === 'all'" class="workspace-section"><h2 class="section-title">既有调查档案 · 只读回看</h2><div class="panel"><div v-for="item in older" :key="item.investigation_id" class="lib-row"><span class="tag">归档</span><a class="lib-q" :href="href('legacy', item.investigation_id)">{{ item.title }}</a><time class="lib-date">{{ dateTime(item.created_at) }}</time></div></div></section></div></template>
