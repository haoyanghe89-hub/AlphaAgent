<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import request from '../api/request.js'
import DecisionEvidencePanel from './DecisionEvidencePanel.vue'
defineProps({ id:String })
const data = ref(null), cases = ref([]), loading = ref(false), error = ref(''), showCases = ref(false)
const view = ref('development')
const companies = computed(() => [
  { name:'中微公司', security:'688012.SH', ids:['S07','daily_688012','margin_688012'] },
  { name:'北方华创', security:'002371.SZ', ids:['S08','daily_002371','margin_002371'] },
].map(company => {
  const inputs = company.ids.map((id, index) => ({
    label:['公司半年报','历史日行情','融资序列'][index],
    source:data.value?.audit.sources.find(source => source.source_id === id),
  }))
  return { ...company, inputs, ready:inputs.every(({ source }) => source?.status === 'AVAILABLE' && source.archive_status === 'HASH_CHECKED') }
}))
let generation = 0, disposed = false
const statuses = { AVAILABLE:'样本已取得', PARTIAL:'部分核实', UNAVAILABLE:'本次未取得', UNTESTED:'未探测', LINK_ONLY:'仅入口可读' }
function stamp(value) { return value ? new Date(value).toLocaleString('zh-CN', { timeZone:'Asia/Shanghai', hour12:false }) : '未记录' }
async function load() {
  const ticket = ++generation; loading.value = true; error.value = ''; data.value = null
  try {
    const result = await request('/quant/decision/capabilities')
    if (!disposed && ticket === generation) data.value = result
  } catch(e) { if (!disposed && ticket === generation) error.value = e.message }
  finally { if (!disposed && ticket === generation) loading.value = false }
}
async function openCases() {
  showCases.value = !showCases.value
  if (!showCases.value || cases.value.length) return
  try { const result = await request('/quant/decision/evaluation-cases'); if (!disposed) cases.value = result }
  catch(e) { if (!disposed) error.value = e.message }
}
onMounted(load)
onUnmounted(() => { disposed = true; generation++ })
</script>
<template>
  <div class="page decision-page">
    <section class="panel panel-pad">
      <div class="section-heading"><h1>{{ view === 'results' ? '单股研究与预测' : '开发验收' }}</h1><button v-if="view === 'development'" class="btn" :disabled="loading" @click="load">{{ loading ? '读取中…' : '重读验收记录' }}</button></div>
      <nav class="decision-views" aria-label="查看内容">
        <button class="decision-view" :aria-pressed="view === 'results'" @click="view = 'results'">分析结果</button>
        <button class="decision-view" :aria-pressed="view === 'development'" @click="view = 'development'">开发验收</button>
      </nav>
      <p v-if="view === 'results'" class="condition-note">查看资料与依据；下一交易日预测接通后在顶部展示方向、理由和核对结果。</p>
      <p v-else class="condition-note">本阶段验收真实输入是否取得、来源能否追溯。分析报告与预测签发尚未完成。</p>
      <p v-if="error" class="notice error-text" role="alert">{{ error }}</p>
      <p v-if="loading" role="status">{{ view === 'results' ? '正在读取功能状态…' : '正在核验来源快照及归档哈希…' }}</p>
    </section>
    <section v-if="data && view === 'results'" class="panel rep-section decision-result-empty">
      <span class="tag tag-warn">尚未签发</span>
      <h2>下一交易日预测尚未接通</h2>
      <p class="condition-note">先在下方查看单股取证结果。模型签发接通后，这里展示涨跌方向、支持与反对理由；当前没有预测或准确率。</p>
    </section>
    <DecisionEvidencePanel v-if="view === 'results'" :id="id?.startsWith('RUN-DEC-') ? id : undefined" developer />
    <template v-if="data && view === 'development'">
      <section class="panel rep-section"><h2>单股定向取证（P1）</h2><p>已接通动态公司输入、三类披露检索、行情与融资取数、引用原文、任务保存及历史回看。</p><p class="condition-note">切到「分析结果」，输入公司并开始取证；打开报告中的引用，再刷新回看。实际请求、来源失败、方法适用性在报告底部「开发记录」查看。当前没有运行模型判断或日预测签发。</p></section>
      <section class="panel rep-section">
        <h2>P0 具体完成了什么</h2>
        <p>两家公司的真实半年报、历史日行情和融资序列已取得，原始响应已归档，当前页面会核对文件哈希。</p>
        <p class="condition-note">本阶段尚未运行新的分析链路，没有生成分析结论或涨跌预测。现在可验收的是输入准备与可追溯性，预测质量留到真实签发和次日核对时验收。</p>
        <div class="decision-acceptance">
          <h3>你现在只需检查这三点</h3>
          <ol class="bt-limitations"><li>展开下面任一公司，确认主体、报告期和行情日期准确。</li><li>打开公司半年报原文，确认确有对应材料；查看归档校验状态。</li><li>确认「分析结果」如实显示尚未生成预测，没有把数据探测当作分析结果。</li></ol>
        </div>
      </section>
      <section class="panel rep-section">
        <div class="section-heading"><h2>本次取得的真实输入</h2><span class="tag">取数记录</span></div>
        <p class="condition-note">探测完成：{{ stamp(data.audit.audited_at) }}。重读验收记录只核对本机归档，不重新请求行情。</p>
        <details v-for="company in companies" :key="company.security" class="decision-company">
          <summary><span><strong>{{ company.name }}</strong><small>{{ company.security }}</small></span><span :class="['tag', company.ready ? 'tag-cyan' : 'tag-warn']">{{ company.ready ? '三类输入已取得并校验' : '输入或归档待检查' }}</span></summary>
          <article v-for="input in company.inputs" :key="input.label" class="decision-input">
            <h3>{{ input.label }}</h3>
            <template v-if="input.source">
              <p>{{ statuses[input.source.status] }} · {{ input.source.sample_count }} {{ input.source.source_id.startsWith('S') ? '页' : '条' }} · {{ input.source.data_start || '起日未知' }} 至 {{ input.source.data_end || '末日未知' }}</p>
              <p class="condition-note">归档：{{ input.source.archive_status === 'HASH_CHECKED' ? '文件哈希已核对' : input.source.archive_status }}{{ input.source.units.length ? '；原始单位：' + input.source.units.join(' / ') : '' }}。{{ input.source.source_id.startsWith('margin') ? '融资是独立资金渠道，不等于全市场净流入。' : '' }}</p>
              <a :href="input.source.url" target="_blank" rel="noopener noreferrer">{{ input.source.source_id.startsWith('S') ? '打开半年报原文' : '打开数据来源' }}</a>
              <details class="decision-input-detail"><summary>查看取得说明与文件校验值</summary><p>{{ input.source.reason }}</p><code class="decision-hash">SHA256 {{ input.source.content_hash }}</code></details>
            </template>
            <p v-else class="condition-note">本次记录中没有这项输入。</p>
          </article>
        </details>
      </section>
      <section class="panel rep-section">
        <h2>下一阶段会交付什么</h2>
        <p>单股定向取证已接通。下一阶段优先完成日预测：真实取数、封存输入、模型明确预测涨或跌、给出理由与反证、审核后保存，并在页面展示。</p>
        <p class="condition-note">目标日收盘后再验收真实核对与基线统计。方法配置、接口合同和测试清单可在下方技术资料中核查。</p>
      </section>
      <details class="panel panel-pad decision-technical">
        <summary>技术核查资料：全部来源、方法卡、实验协议与测试清单</summary>
      <section class="panel rep-section">
        <div class="section-heading"><h2>下一交易日实验协议</h2><span class="tag tag-warn">未开启 · P1-N 待接入</span></div>
        <p>固定篮子：中微公司 688012.SH、北方华创 002371.SZ。交易日 {{ data.experiment.window_start.slice(0,5) }}–{{ data.experiment.window_end.slice(0,5) }} 封存并签发，Asia/Shanghai。</p>
        <p class="condition-note">当前没有预测记录或真实准确率。窗口外预测将单列探索组；输入或审核失败保留记录；首个正式判断不可覆盖。</p>
        <div class="decision-grid"><div><strong>预测与核对</strong><p>只预测涨 / 跌。次日用真实收盘价与交易所参考价核对；实际平盘在主准确率中算未命中。</p></div><div><strong>同时展示三项基线</strong><p>永远涨、永远跌、昨日方向延续。停牌、缺数据、冲突和未成熟分开记录。</p></div></div>
        <details><summary>查看冻结的实验配置</summary><pre class="decision-json">{{ JSON.stringify(data.experiment, null, 2) }}</pre></details>
      </section>
      <section class="panel rep-section">
        <div class="section-heading"><h2>全部来源与覆盖</h2><span class="tag tag-cyan">实测快照</span></div>
        <p class="condition-note">探测完成：{{ stamp(data.audit.audited_at) }}。本页面刷新不重新请求行情。下列“已取得”指本次小样本；原始响应在本机归档，行业版权资料仅保留入口与方法笔记。</p>
        <div class="decision-source-list">
          <details v-for="source in data.audit.sources" :key="source.source_id" class="decision-source">
            <summary><span><strong>{{ source.name }}</strong><small>{{ source.source_id }}</small></span><span :class="['tag',source.status === 'AVAILABLE' ? 'tag-cyan' : 'tag-warn']">{{ statuses[source.status] }}</span></summary>
            <p>{{ source.reason }}</p><p class="condition-note">{{ source.sample_count ? '样本数 ' + source.sample_count + '；' : '' }}数据期间：{{ source.data_start || '未知' }} ～ {{ source.data_end || '未知' }}<br>取得时间：{{ stamp(source.tested_at) }}；新鲜度：{{ source.freshness === 'FRESHNESS_UNKNOWN' ? '无已核定发布计划，未知' : source.freshness }}</p>
            <p class="condition-note">来源族：{{ source.source_family }}；单位：{{ source.units.join(' / ') || '非数值或未核定' }}；归档：{{ source.archive_status }}<br>{{ source.license_note }}</p>
            <code v-if="source.content_hash" class="decision-hash">SHA256 {{ source.content_hash }}</code>
            <a :href="source.url" target="_blank" rel="noopener noreferrer">打开来源原入口 ↗</a>
          </details>
        </div>
      </section>
      <section class="panel rep-section">
        <div class="section-heading"><h2>八张方法卡</h2><span class="tag tag-warn">AI方法审核 · 预测价值未验证</span></div>
        <details v-for="card in data.methods" :key="card.method_id" class="decision-source">
          <summary><strong>{{ card.method_id }} {{ card.title }}</strong><span class="tag">{{ card.horizon === 'MEDIUM_TERM' ? '中期研究' : '日频观察' }}</span></summary>
          <p class="condition-note">版本 {{ card.version }}；所需输入：{{ card.input_requirements.join('、') }}。缺必要输入即停用相应方法。</p>
          <ol class="bt-limitations"><li v-for="step in card.procedure" :key="step">{{ step }}</li></ol>
          <p><strong>反例：</strong>{{ card.counterexamples.join('；') }}</p><p><strong>禁止推断：</strong>{{ card.forbidden_inferences.join('；') }}</p>
          <p class="condition-note">来源：{{ card.source_refs.join('、') }}<br>审核：{{ card.review_records[0].identity }} / {{ card.review_records[0].reviewer }} · {{ stamp(card.review_records[0].reviewed_at) }}</p>
        </details>
      </section>
      <section class="panel rep-section">
        <h2>首行业与尚缺能力</h2><p>{{ data.selection.selection_reason }}</p>
        <p class="condition-note">开发选题 provisional={{ data.selection.provisional }}；没有同口径完整20日市场分母与行业成分历史，热度排名关闭。</p>
        <div class="decision-grid"><div v-for="item in data.industry_templates.templates" :key="item.industry_id"><strong>{{ item.title }}</strong><p>{{ item.configured ? '模板已配置，业务解释尚未启用' : '独立模板尚未建立' }}</p><small>{{ item.enable_after }}</small></div></div>
        <ul class="bt-limitations"><li v-for="note in data.limitations" :key="note">{{ note }}</li></ul>
      </section>
      <section class="panel rep-section">
        <div class="section-heading"><h2>合同与48例验收清单</h2><span class="tag tag-warn">{{ data.evaluation.executed ? '已执行 '+data.evaluation.executed+' 例，详见封存记录' : '仅已登记，未跑完模型评测' }}</span></div>
        <p>{{ data.evaluation.total }} 例：24基础 + 12领域 + 12日预测；开发 {{ data.evaluation.development }} / 留出 {{ data.evaluation.holdout }}。合成夹具只验证软件门禁，不代表真实预测准确率。</p>
        <a class="btn" href="?developer=1#/delivery">查看统一验收与消融结果</a><div class="decision-actions"><a class="btn" href="/api/quant/decision/schema" target="_blank" rel="noopener noreferrer">查看 / 保存 JSON 合同</a><a class="btn" href="/api/quant/decision/evaluation-cases" target="_blank" rel="noopener noreferrer">查看 / 保存48例JSON</a><button class="btn" @click="openCases">{{ showCases ? '收起验收清单' : '展开48例清单' }}</button></div>
        <div v-if="showCases" class="decision-cases"><article v-for="item in cases" :key="item.case_id" class="bt-check-item"><strong>{{ item.case_id }} · {{ item.family }} · {{ item.split === 'development' ? '开发' : '留出' }}</strong><p>必须：{{ item.allowed.join('；') }}<br>禁止：{{ item.forbidden.join('；') }}</p><small>{{ item.material_ref }} · {{ item.review_identity }} · 已登记</small></article></div>
      </section>
      </details>
    </template>
  </div>
</template>
<style scoped>
.decision-page h1{margin-top:12px}.decision-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px;margin:20px 0}.decision-grid>div{border-left:2px solid var(--gold,#b89b5e);padding-left:16px}.decision-source{border-bottom:1px solid var(--border,#8883);padding:14px 0}.decision-source summary{display:flex;align-items:center;justify-content:space-between;gap:14px;cursor:pointer;line-height:1.6}.decision-source summary small{display:block;opacity:.6;font-size:11px}.decision-source p{line-height:1.8}.decision-source a{display:inline-block;margin:12px 0}.decision-hash{display:block;overflow-wrap:anywhere;opacity:.7;font-size:11px}.decision-json{max-height:420px;overflow:auto;font-size:12px;padding:16px;background:var(--bg,#8881);white-space:pre-wrap;overflow-wrap:anywhere}.decision-actions{display:flex;flex-wrap:wrap;gap:12px}.decision-cases{margin-top:20px}.decision-page details>summary:focus-visible{outline:2px solid var(--gold,#b89b5e);outline-offset:4px}@media(max-width:600px){.decision-source summary{align-items:flex-start;flex-wrap:wrap}.decision-page .section-heading{flex-wrap:wrap;gap:12px}.decision-grid{grid-template-columns:1fr}}
.decision-views{display:flex;gap:24px;border-bottom:1px solid var(--border,#8883);margin:20px 0}.decision-view{font:inherit;color:inherit;background:none;border:0;border-bottom:2px solid transparent;padding:12px 0;cursor:pointer;opacity:.65}.decision-view[aria-pressed="true"]{border-bottom-color:var(--gold,#b89b5e);opacity:1}.decision-view:focus-visible{outline:2px solid var(--gold,#b89b5e);outline-offset:3px}.decision-result-empty{padding-top:40px;padding-bottom:40px}.decision-result-empty h2{margin-top:20px}.decision-result-empty p{max-width:720px;line-height:1.9}.decision-result-empty .btn{margin-top:16px}.decision-acceptance{margin-top:24px;border-left:2px solid var(--gold,#b89b5e);padding-left:20px}.decision-company{border-bottom:1px solid var(--border,#8883);padding:20px 0}.decision-company>summary{display:flex;align-items:center;justify-content:space-between;gap:16px;cursor:pointer;line-height:1.7}.decision-company small{display:block;font-size:12px;opacity:.65}.decision-input{margin-top:20px;padding:0 0 20px 16px;border-left:1px solid var(--border,#8883)}.decision-input p{line-height:1.8}.decision-input a{display:inline-block;margin:8px 0}.decision-input-detail{margin-top:12px}.decision-input-detail summary{cursor:pointer;font-size:13px;opacity:.75}.decision-technical>summary{cursor:pointer;line-height:1.8;font-weight:600}.decision-technical>.panel{box-shadow:none;border:0;padding-left:0;padding-right:0}@media(max-width:600px){.decision-company>summary{align-items:flex-start;flex-direction:column}.decision-technical{padding:20px}.decision-acceptance{padding-left:14px}}
</style>
