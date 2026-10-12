<script setup>
import { ref } from 'vue'
import { href } from './navigation.js'
import { deliveryPage } from './useDelivery.js'
import { dateTime, historyStatus, number } from './model.js'
defineProps({ stocks: Array, history: Array, loading: Boolean, error: String, liveQuotes: Array, quoteTime: String, quoteError: String })
const emit = defineEmits(['ask', 'retry'])
const question = ref('')
const examples = ['全面分析美的集团', '分析格力电器的盈利能力和风险', '查看贵州茅台的DCF估值', '美的集团和格力电器对比']
const featured = [{code:'000333',name:'美的集团'},{code:'000651',name:'格力电器'},{code:'600519',name:'贵州茅台'}]
function ask(text = question.value) { question.value = text; emit('ask', text) }
</script>
<template>
  <div class="page workbench">
    <section class="panel ask-hero">
      <div class="hero-heading"><h1>提出一个投研问题</h1><span class="muted">每个数字来自真实数据接口，可溯源、可复算</span></div>
      <form class="ask-box" @submit.prevent="ask()"><input v-model="question" aria-label="投研问题" :placeholder="examples.length ? '例：' + examples[0] : '请输入沪深 A 股名称或六位代码'" autocomplete="off" maxlength="2000" /><button class="btn btn-primary" :disabled="!question.trim() || loading">开始研究</button></form>
      <div class="ask-chips"><button v-for="example in examples" :key="example" class="chip" :disabled="loading" @click="ask(example)">{{ example }}</button></div>
      <p class="hero-disclaimer">本工具为投研参考，不构成投资建议，不提供买卖指令。</p>
    </section>
    <div v-if="error" class="notice error-text" role="alert">{{ error }} <button class="btn" @click="emit('retry')">重新加载</button></div>
    <section class="workspace-section">
      <div class="section-title">重点标的 · 最新行情与完整研究</div>
      <p v-if="quoteError" class="notice error-text" role="alert">{{ quoteError }}</p>
      <div class="stock-grid"><a v-for="item in featured" :key="item.code" class="panel stock-card" :href="href('stock',item.code)">
        <div class="sc-head"><span class="sc-name">{{ item.name }}</span><span class="sc-code num">{{ item.code }} · 实时</span></div>
        <template v-for="q in [(liveQuotes || []).find(q => q.code === item.code)]" :key="item.code">
          <div class="sc-price num">{{ number(q?.price) }}<span class="sc-price-label">{{ q?.change > 0 ? '+' : '' }}{{ number(q?.change) }}%</span></div>
          <div class="sc-meta"><span>PE <b>{{ number(q?.pe) }}</b></span><span>PB <b>{{ number(q?.pb) }}</b></span><span>市值 <b>{{ number(q?.market_cap / 1e8) }} 亿</b></span></div>
          <p class="muted small">报价 {{ q ? dateTime(q.quoted_at) : '读取中' }}</p>
        </template><strong>现场取数 · 开始研究 →</strong>
      </a></div>
      <p class="muted small">支持查询沪深 A 股名称或代码；模型规划、取数、分析、核验和成稿。图表按实际覆盖展示，金融企业禁用工业 DCF。行情每 30 秒刷新；休市显示最近交易报价。</p>
    </section>
    <div class="bt-actions"><a class="btn" href="#/compare">最新双股对比</a><a class="btn" href="#/delivery">完整演示与数据接入</a></div><div class="quick-links"><a class="panel panel-pad" :href="href('live','new')"><strong>实时研究与单股模拟 →</strong><p>现场采集最新数据，查看研究图表、Agent 解读、证据与参数情景</p></a><a class="panel panel-pad" :href="href('strategy')"><strong>策略回测 →</strong><p>年度选股、公司行为账本、绩效与成本敏感性；完整准入通过才计算收益</p></a><a class="panel panel-pad" :href="href('library')"><strong>历史研究库 →</strong><p>过往报告与回测记录，随时回看、核对结论与依据</p></a></div>
    <section class="workspace-section"><div class="section-title">最近研究</div><div v-if="history?.length" class="panel"><div v-for="item in history.slice(0,6)" :key="item.run_id" class="lib-row"><span class="tag tag-gold">{{ item.scenario === 'live' ? '实时研究' : item.scenario === 'backtest' ? '回测检查' : item.scenario === 'compare_live' || item.scenario === 'compare' ? '对比' : item.scenario === 'strategy' ? '策略回测' : '深研' }}</span><a class="lib-q" :href="href(deliveryPage(item), item.run_id)">{{ item.title }}</a><span v-if="item.status !== 'COMPLETED' || ['strategy','backtest'].includes(item.scenario)" class="tag">{{ historyStatus(item) }}</span><time class="lib-date">{{ dateTime(item.created_at) }}</time></div></div><div v-else class="panel empty-state">尚无研究记录，从上方提出第一个问题。</div></section>
  </div>
</template>
