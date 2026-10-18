<script setup>
import { onMounted, ref } from 'vue'
import request from '../api/request.js'
import KnowledgeRelease from './KnowledgeRelease.vue'
const data = ref(null), error = ref('')
const acceptance = ref(null)
const developer = new URLSearchParams(location.search).get('developer')==='1'
async function load() { try { data.value = await request('/quant/delivery'); error.value = '' } catch(e) { error.value = e.message } }
onMounted(load)
onMounted(async()=>{try{acceptance.value=await request('/quant/decision/acceptance')}catch(e){error.value=e.message}})
</script>
<template><div class="page"><section class="panel panel-pad"><div class="section-heading"><h1>功能与数据接入</h1><button class="btn" @click="load">刷新</button></div><p class="condition-note">功能可用与数据完整分开说明。免费来源按证券、期间及模块返回实际覆盖；缺项可后续接入，不用空值或演示数字替代。</p><p v-if="error" class="error-text" role="alert">{{ error }}</p></section>
  <section class="panel rep-section" style="margin-top:20px"><h2>从头演示一遍</h2><ol class="bt-limitations"><li><a href="#/">工作台：输入“全面分析美的集团”</a>，查看真实 Planner、工具调用与五角色过程。</li><li>在研究页查看结论高亮与依据，展开财务、K线、估值、两融及风险；缺项会说明原因。</li><li>调整 DCF / 均线情景，查看新版本；追问“利润和现金流是否匹配”，检查新研究与父版本。</li><li><a href="#/compare">最新双股比较</a>：输入两只名称或代码；查看双方新取数、报告日期和口径对照。</li><li><a href="#/strategy">完整策略执行</a>：确认筛选、成本与无风险假设；有审核包计算，无包保存缺项。<a href="#/backtest">历史取证工作台</a>可继续采集免费档案。</li><li><a href="#/library">历史库</a>刷新、回看、导出原报告；旧版本不随数据更新而改变。</li></ol></section>
  <section class="panel rep-section"><h2>下一交易日预测</h2><p>查看中微公司、北方华创的下一交易日涨跌判断、支持与反对依据，并在目标日收盘后核对真实结果。</p><a class="btn" href="#/decision">打开明日预测</a></section>
  <section v-if="acceptance" class="panel rep-section"><h2>决策研究交付状态</h2><p>{{ acceptance.summary || '本机尚未保存统一验收记录。' }}</p><ul class="bt-limitations"><li>软件：{{ acceptance.software || '待统一验收' }}</li><li>来源：{{ acceptance.sources || '按每份研究实际覆盖查看' }}</li><li>方法审核：程序检查与同模型 AI 复核；外部独立审核尚未进行。</li><li>预测价值：未验证。功能用例通过与报告生成成功不等于优于基线。</li><li>旧 5.2：完整历史档案和独立全收益核对仍未通过。</li><li>用户验收：{{ acceptance.user_acceptance==='PENDING'?'待您点击确认':acceptance.user_acceptance }}</li></ul><a v-if="acceptance.links?.market" class="btn" :href="acceptance.links.market">查看市场观察</a> <a v-if="acceptance.links?.candidates" class="btn" :href="acceptance.links.candidates">查看候选研究</a><details v-if="developer"><summary>开发验收：48例与补证/方法卡对照</summary><p>模型功能用例 {{ acceptance.executed || 0 }} 例；{{ acceptance.status }}。审核身份为 AI，同一模型复核不等于独立专家。</p><a class="btn" href="/api/quant/decision/acceptance" target="_blank" rel="noopener noreferrer">导出封存验收记录</a><pre style="white-space:pre-wrap;overflow-wrap:anywhere">{{ JSON.stringify(acceptance.evaluation,null,2) }}</pre></details></section>
  <KnowledgeRelease :developer="developer" />
  <section class="panel rep-section"><h2>未来一至三个月研究</h2><p>读取公司披露，核查经营原因、反对理由与未来观察条件；首次观点保存后，更新另存版本。估值、资金与行业验证缺项明确展示。</p><a class="btn" href="?research=1#/decision">打开中期研究</a></section>
  <section v-if="data" class="panel rep-section"><h2>当前功能</h2><div v-for="f in data.features" :key="f.key" class="bt-check-item"><div><strong>{{ f.title }}</strong><span class="tag tag-cyan">{{ f.software }}</span></div><p>{{ f.detail }}</p><a v-if="f.href" :href="f.href">打开功能 →</a></div></section>
  <section v-if="data" class="panel rep-section"><h2>后续数据接入位置</h2><div v-for="p in data.data_slots" :key="p.key" class="bt-check-item"><div><strong>{{ p.title }}</strong><span class="tag tag-warn">{{ p.status }}</span></div><p>{{ p.requires }}</p><p>{{ p.effect }}</p></div><p class="condition-note">服务端已安装历史包 {{ data.packages.length }} 份；安装不代表审核通过，按本次选股条件重新准入。</p><a class="btn" href="/api/quant/strategies/schema" download="historical-package-schema.json">下载历史包接入合同</a><a class="btn" href="/api/quant/research/comparisons/schema" download="comparison-adapter-schema.json">下载比较指标接入合同</a></section>
  <section v-if="data" class="panel rep-section"><h2>可靠性与边界</h2><ul class="bt-limitations"><li v-for="n in data.reliability" :key="n">{{ n }}</li></ul></section>
</div></template>
