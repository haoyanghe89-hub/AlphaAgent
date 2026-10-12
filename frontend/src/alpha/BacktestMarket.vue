<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useMarket } from './useMarket.js'
const props = defineProps({ parentId:String, available:Boolean })
const {data, error, busy, load, start, stop, dispose} = useMarket()
const batches = ref(1)
const cov = computed(() => data.value?.coverage)
const latest = computed(() => data.value?.batches?.[0])
const running = computed(() => data.value?.acquisition_status === 'RUNNING')
const labels = {PENDING:'尚未采集',RUNNING:'正在采集',COMPLETED:'本次采集完成',PARTIAL:'本次部分失败',FAILED:'本次采集失败',CANCELLED:'已停止，可继续',INTERRUPTED:'服务已中断，可继续'}
onMounted(() => load(props.parentId))
onUnmounted(dispose)
</script>
<template><section class="panel rep-section market-coverage" aria-label="全市场历史数据采集">
  <h2>全市场历史数据采集 <span class="tag tag-warn">5.2 · 未通过准入</span></h2>
  <p class="condition-note">按当前报告冻结的沪深 A 股历史目录分区取数，保留退市股与未知身份。停止后可续采，已冻结响应不重复获取。</p>
  <div class="bt-actions market-controls">
    <label for="market-batches">本次批数</label><select id="market-batches" v-model.number="batches" :disabled="busy || running"><option :value="1">1 批</option><option :value="5">5 批</option><option :value="20">20 批</option></select>
    <button class="btn btn-primary" :disabled="busy || running || !available || data?.observation_complete" @click="start(parentId,batches)">{{ data?.exists ? '继续采集缺失分区' : '建立全市场清单并采集' }}</button>
    <button v-if="running" class="btn" :disabled="busy" @click="stop(parentId)">停止全市场采集</button>
    <button class="btn" :disabled="busy" @click="load(parentId,cov?.offset || 0)">刷新采集状态</button>
    <a v-if="data?.exists" class="btn" :href="'/api/quant/backtests/' + encodeURIComponent(parentId) + '/market/export'" download>导出全市场覆盖</a>
  </div>
  <p v-if="error" class="error-text" role="alert">{{ error }}</p>
  <template v-if="data?.exists">
    <p class="condition-note">{{ data.start }} ~ {{ data.end }} · {{ cov.stock_count }} 个证券代码，关联 {{ cov.observed_identity_count }} 个身份观察；其中区间内退市观察 {{ cov.delisted_count }} 个、改码退出 {{ cov.symbol_change_exit_count }} 个、身份缺失 {{ cov.unknown_identity_count }} 个。{{ labels[data.acquisition_status] }}。</p>
    <div class="market-progress" aria-live="polite"><strong class="num">{{ cov.frozen_count }} / {{ cov.query_count }}</strong><span>响应已冻结</span><span>{{ cov.observed_stock_count }} 只完成公开源计划</span><span class="tag tag-warn">严格准入分区 {{ data.strict_partition_count }}</span></div>
    <p v-if="latest" class="condition-note">本次已发出 {{ latest.calls }} 次查询，冻结 {{ latest.frozen }} 份响应；{{ latest.failures.length }} 项错误。每批最多 {{ data.budgets.calls_per_batch }} 次 / {{ data.budgets.seconds_per_batch }} 秒 / 16 MiB，连续来源失败会停止。</p>
    <div class="market-table"><table class="data"><thead><tr><th>数据类别</th><th>计划分区</th><th>已冻结</th><th>空响应</th><th>原始行数</th></tr></thead><tbody><tr v-for="d in cov.domains" :key="d.kind"><td>{{ d.label }}</td><td>{{ d.planned }}</td><td>{{ d.frozen }}</td><td>{{ d.empty }}</td><td>{{ d.rows }}</td></tr></tbody></table></div>
    <p class="condition-note">空响应仅表示供应商未返回记录，不能证明没有分红或财报。公开源计划完成后仍须补齐下列严格档案：</p>
    <ul class="bt-limitations"><li v-for="gap in data.gaps" :key="gap">{{ gap }}</li></ul>
    <details class="bt-source-details"><summary>逐股覆盖明细（{{ cov.stock_count }} 只）</summary>
      <div class="market-table"><table class="data"><thead><tr><th>代码 / 名称</th><th>观察期间</th><th>已冻结 / 计划</th><th>空响应</th><th>已查行情缺日</th><th>采集状态</th></tr></thead><tbody><tr v-for="s in cov.stocks" :key="s.code"><td>{{ s.code }}<br>{{ s.name }} <span v-if="s.symbol_change_exit" class="tag">改码退出</span><span v-else-if="s.delisted_observed" class="tag">退市观察</span></td><td class="num">{{ s.start }}<br>{{ s.end }}</td><td>{{ s.frozen }} / {{ s.planned }}</td><td>{{ s.empty }}</td><td>{{ s.missing_sessions ?? '—' }}</td><td>{{ {OBSERVED:'公开源已取',PARTIAL:'部分已取',PENDING:'待采集'}[s.status] }}</td></tr></tbody></table></div>
      <div class="bt-actions"><button class="btn" :disabled="busy || cov.offset === 0" @click="load(parentId,Math.max(0,cov.offset-cov.limit))">上一页</button><span class="muted">{{ cov.offset+1 }}–{{ Math.min(cov.offset+cov.limit,cov.stock_count) }} / {{ cov.stock_count }}</span><button class="btn" :disabled="busy || cov.offset+cov.limit >= cov.stock_count" @click="load(parentId,cov.offset+cov.limit)">下一页</button></div>
    </details>
    <details class="bt-source-details"><summary>冻结清单哈希</summary><p class="num market-hash">{{ data.plan_hash }}</p></details>
  </template>
</section></template>
<style scoped>
.market-controls {flex-wrap:wrap}.market-controls select {background:var(--bg-panel);color:var(--text-1);border:1px solid var(--border);border-radius:6px;padding:7px}
.market-progress {display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin:16px 0;font-size:12px;color:var(--text-2)}.market-progress strong {font-size:21px;color:var(--accent)}
.market-table {overflow-x:auto;max-width:100%}.market-table table {min-width:560px}.market-hash {overflow-wrap:anywhere}
</style>
