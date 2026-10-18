<script setup>
import { computed } from 'vue'
import { useDailyWorkspace } from './useDailyWorkspace.js'
import { dateTime, number } from './model.js'
import PrototypeChart from './PrototypeChart.vue'
const props=defineProps({id:String})
const {status,briefing,error,busy,load,resume,generate}=useDailyWorkspace(props.id)
const developer=new URLSearchParams(location.search).get('developer')==='1'
const today=computed(()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date(status.value?.checked_at||Date.now())).replaceAll('-',''))
const data=computed(()=>briefing.value?.data)
const selected=computed(()=>data.value?.briefing.items.map(item=>({...item,...data.value.sectors.find(s=>s.industry_id===item.industry_id)}))||[])
const pct=v=>(Number(v)>0?'+':'')+number(Number(v)*100,2)+'%'
const sourceLabel=computed(()=>({TLS_FAILED:'历史数据接口安全连接异常',NETWORK_FAILED:'历史数据接口连接失败',TLS_READY:'历史数据接口安全连接可用',CHECKING:'正在检查数据连接'})[status.value?.source?.status]||'数据连接尚未检查')
const chart=computed(()=>({kind:'sector_rank',title:'重点行业：上个交易日成分股平均涨跌',chart_type:'bar',x:selected.value.map(s=>s.name),series:[{name:'涨跌幅 %',axis:0,type:'bar',signed:true,data:selected.value.map(s=>Number(s.mean_change)*100)}]}))
</script>
<template><section class="workspace-section daily-workspace">
  <div class="section-heading"><div><h2>盘前重点行业</h2><p class="condition-note">交易日 09:10 准备 · 09:25 展示 · 自动保存每日清单</p></div><button class="btn" @click="load">刷新状态</button></div>
  <p v-if="error" class="notice error-text" role="alert">{{ error }}</p>
  <div class="daily-status"><span :class="{'error-text':['TLS_FAILED','NETWORK_FAILED'].includes(status?.source?.status)}">{{ !status?'正在读取更新状态…':developer?sourceLabel:['TLS_FAILED','NETWORK_FAILED'].includes(status.source?.status)?'数据连接异常':'每日关注与行情持续更新' }}</span><span>{{ !status?'正在读取每日更新状态…':status.enabled?'后台每日更新已开启':'后台每日更新未开启，请使用项目启动脚本' }}</span><a href="#/library">历史研究与核对 →</a></div>
  <p v-if="['TLS_FAILED','NETWORK_FAILED'].includes(status?.source?.status)" class="notice">新财报、日线和盘后资金取数受影响，已有研究保留。{{ status.source.status==='TLS_FAILED'?'请联系 Tushare 确认有效的 HTTPS 接口及证书。':'请检查本机联网及数据平台状态。' }}行情图保留原报价时间。</p>
  <p v-if="!status&&!briefing" role="status" class="condition-note">正在读取每日清单与已保存记录…</p>
  <div v-else-if="!briefing" class="panel rep-section"><strong>{{ status?.morning_status==='MISSED_WINDOW'?'今日盘前窗口已错过，未补写推荐':'等待下一个交易日的盘前清单' }}</strong><p class="condition-note">保持服务在 09:10 前运行。休市日保留最近行情；你的明日预测和投资计划仍可从工作台发起并保存。</p><button class="btn" :disabled="busy" @click="generate">{{ busy?'正在启动分析…':'生成当前行业观察' }}</button><p class="condition-note">按实际时间生成，不计为准时盘前推荐。</p></div>
  <article v-else-if="data" class="panel rep-section">
    <div class="section-heading"><strong>{{ data.briefing.stance==='RISK_FIRST'?'先控制风险，观察相对强的行业':'关注相对强势行业，等待条件确认' }}</strong><a v-if="!id" :href="'#/morning/'+briefing.run_id">已保存 · 查看完整清单 →</a></div>
    <p v-if="briefing.request.trade_date!==today" class="condition-note">这是 {{ briefing.request.trade_date }} 的历史清单，今日尚无新清单。</p>
    <p><strong>{{ data.briefing.summary }}</strong></p><p class="condition-note">{{ data.timing==='PREOPEN'?'盘前生成':'按实际时间生成的观察，未计为准时盘前推荐' }} · 发布 {{ dateTime(data.issued_at) }} · 行情截止 {{ data.market_date }} · 关注不等于买入</p>
    <p class="condition-note">本次基于 {{ data.sectors.length }} / {{ data.sectors.length+data.excluded.length }} 个共同窗口完整的行业形成关注清单；缺项行业未参与筛选。</p><PrototypeChart :block="chart" :height="220" />
    <div class="focus-list"><article v-for="item in selected" :key="item.industry_id"><div class="focus-title"><strong>{{ item.name }}</strong><span class="tag">{{ item.action==='WATCH'?'重点关注':'等待确认' }}</span><span class="num">{{ pct(item.mean_change) }}</span></div><p>{{ item.reason }}</p><details><summary>确认条件与反证</summary><p class="condition-note"><b>确认条件：</b>{{ item.confirmation }}</p><p class="condition-note"><b>反证：</b>{{ item.counterargument }}</p></details><div class="focus-metrics"><span>上涨家数占比 <b>{{ number(Number(item.breadth)*100,1) }}%</b></span><span>二十日中位变化 <b>{{ pct(item.median_return_20) }}</b></span><span>五 / 二十日成交额 <b>{{ number(item.turnover_5_20,2) }} 倍</b></span><span>{{ item.members }} 只成分</span></div></article></div>
    <details><summary>数据范围与方法边界</summary><p>{{ data.basis }}。未包含今日竞价或最新新闻；方法效果仍需后续真实核对。</p><p>本次 {{ data.sectors.length }} 个行业具备共同窗口，另 {{ data.excluded.length }} 个行业保留缺项，未缩小分母填榜。</p><pre v-if="developer">{{ data.methods }}</pre></details>
  </article>
  <div v-else class="panel rep-section"><strong>{{ ['PREPARING','RUNNING'].includes(briefing.status)?'正在准备并审核行业清单':'本次盘前清单尚未发布' }}</strong><p>{{ briefing.error||'审核通过后在展示时间发布；封存资料和已完成步骤持续保留。' }}</p><button v-if="briefing.status==='INTERRUPTED'" class="btn" :disabled="busy" @click="resume">恢复本次审核</button></div>
  <details v-if="developer" class="condition-note"><summary>开发者：自动更新运行状态</summary><pre>{{ status }}</pre></details>
</section></template>
<style scoped>.daily-status{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;margin:12px 0;color:var(--text-2)}.daily-workspace h2{margin-bottom:5px}.focus-list{display:grid;grid-template-columns:1fr 1fr;gap:12px}.focus-list article{padding:12px;border:1px solid var(--border);min-width:0}.focus-title,.focus-metrics{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.focus-title .num{margin-left:auto}.focus-list p{font-size:13px;line-height:1.8;margin:8px 0}.focus-metrics{font-size:11px;color:var(--text-2)}details{font-size:12px;margin-top:12px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto}.section-heading{flex-wrap:wrap;gap:8px}@media(max-width:760px){.focus-list{grid-template-columns:1fr}}</style>
