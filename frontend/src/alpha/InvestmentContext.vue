<script setup>
import PrototypeChart from './PrototypeChart.vue'
import {fundingChart,policyState} from './useInvestmentContext.js'
defineProps({context:Object,gaps:Array})
const cash=v=>v==null?'缺少完整窗口':(Number(v)/1e8).toLocaleString('zh-CN',{maximumFractionDigits:2})+' 亿'
</script>
<template>
<div class="investment-context" v-if="context">
  <div class="section-heading"><h3>资金与政策</h3><span class="muted">本次封存证据</span></div>
  <template v-if="context.daily"><div class="flow-summary"><span v-for="w in context.daily.windows" :key="w.sessions">{{w.sessions}}日净额 <strong>{{cash(w.value)}}</strong></span><small>日频截至 {{context.daily.asof||'未取得'}} · 大单＋特大单代理</small></div><PrototypeChart v-if="context.daily.points.length" :block="fundingChart(context.daily)" :height="155" /></template>
  <details v-if="context.minute" class="minute"><summary>查看腾讯分钟资金 · {{context.minute.asof}} {{['STALE','DELAYED'].includes(context.minute.freshness)?'（过时或延迟，不参与当前判断）':''}}</summary><p>当日累计 {{cash(context.minute.latest.main)}}；5分钟增量 {{cash(context.minute.windows.find(w=>w.minutes===5)?.main)}}；15分钟增量 {{cash(context.minute.windows.find(w=>w.minutes===15)?.main)}}。</p><PrototypeChart :block="fundingChart(context.minute,true)" :height="155" /><p class="muted">仅本次取得的供应商当日序列，日频与分钟口径分列；非此前连续监测历史。</p></details>
  <details v-if="context.policies.length" class="policies"><summary>相关政策 {{context.policies.length}} 项 · 公司资格待核</summary><article v-for="p in context.policies" :key="p.policy_id"><a :href="p.source.url" target="_blank" rel="noopener noreferrer">{{p.title}}</a><p><b>{{p.jurisdiction==='CN'?'中国国家级':'海外业务相关'}} · {{policyState(p)}}</b><small>发布 {{p.published_at||'原文未注明'}} · 生效 {{p.effective_at||'待核'}} · 修订覆盖未穷尽</small></p><blockquote v-for="c in p.clauses" :key="c.key+c.start">{{c.text}}</blockquote><p class="policy-limit">以上是政策原文事实。公司产品、项目、地域与资格尚未核定；对经营的影响是假设，不能直接认定政策导致股价变化。</p></article></details>
  <p v-else class="muted">本次未取得匹配政策原文，方向仍依据已有行情与经营证据。</p>
  <p v-if="gaps?.length" class="context-gaps">本次覆盖：{{[...new Set(gaps.map(g=>g.reason))].join('；')}}。</p>
  <p class="context-note">资金为供应商估算代理，不能识别机构身份，也不等于买入指令。</p>
</div>
</template>
<style scoped>
.investment-context{border-top:1px solid var(--border);margin-top:14px;padding-top:8px}.investment-context h3{font-size:14px;margin:8px 0}.investment-context .section-heading{margin-bottom:4px}.flow-summary{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:12px}.flow-summary small,.policies small{display:block;flex-basis:100%;color:var(--text-3)}.flow-summary strong{font-size:14px;margin-left:4px}summary{cursor:pointer;font-size:12px;line-height:1.8}.minute,.policies{margin-top:8px;font-size:12px}.minute p,.policies p{line-height:1.6}.policies article{padding:10px 0;border-bottom:1px solid var(--border)}.policies a{font-weight:600}.policies blockquote{margin:6px 0;padding-left:9px;border-left:2px solid var(--border);color:var(--text-2);overflow-wrap:anywhere;line-height:1.65}.policy-limit,.context-note,.context-gaps{font-size:11px;color:var(--text-3);line-height:1.65}.context-gaps{color:var(--text-2)}summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
</style>
