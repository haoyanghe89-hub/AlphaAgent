"""Apply the P6 connections without rewriting existing workspace components."""
from pathlib import Path


def edit(path, replacements):
    target = Path(path)
    value = target.read_text(encoding="utf8")
    for old, new in replacements:
        if old not in value:
            raise ValueError(path + ': missing intended edit ' + old[:60])
        value = value.replace(old, new)
    target.write_text(value, encoding="utf8")


edit('frontend/src/alpha/navigation.js', [
    ("'/decision', '/candidates'", "'/decision', '/candidates', '/market'"),
    ('strategy|decision|candidates)', 'strategy|decision|candidates|market)'),
])
edit('frontend/src/App.vue', [
    ("import IndustryCandidates from './alpha/IndustryCandidates.vue'", "import IndustryCandidates from './alpha/IndustryCandidates.vue'\nimport MarketResearch from './alpha/MarketResearch.vue'"),
    ("request('/quant/research/runs')]).then(([runs, old, fresh])", "request('/quant/research/runs'),request('/quant/decision/runs')]).then(([runs, old, fresh, decisions])"),
    ('[...fresh, ...result]', '[...decisions,...fresh, ...result]'),
    ("<a :href=\"href('candidates')\"", "<a :href=\"href('market')\" :class=\"{ active: route.page === 'market' }\">市场观察</a><a :href=\"href('candidates')\""),
    ('      <IndustryCandidates v-else-if=', '      <MarketResearch v-else-if="route.page === \'market\'" :key="\'market-\' + (route.id || \'new\')" :id="route.id" />\n      <IndustryCandidates v-else-if='),
])
edit('frontend/src/alpha/useDelivery.js', [
    ("export function deliveryPage(item) {\n  return", "export function deliveryPage(item) {\n  if (item.scenario === 'decision') return item.page || 'decision'\n  return"),
])
edit('frontend/src/alpha/MediumTermResearch.vue', [
    ("import ForecastTracking from './ForecastTracking.vue'", "import ForecastTracking from './ForecastTracking.vue'\nimport VersionComparison from './VersionComparison.vue'"),
    ('start,action,active,load}', 'start,action,active,load,refresh}'),
    ('const update=()=>start(conditions(state.value.subject.security_id,props.id))', 'const update=()=>refresh()'),
    ('<ResearchOpinion :report="report"', '<VersionComparison :id="id"/><ResearchOpinion :report="report"'),
])
edit('frontend/src/alpha/IndustryCandidates.vue', [
    ("import {computed,ref} from 'vue'", "import {computed,ref,watch} from 'vue'"),
    ("import DecisionCitation from './DecisionCitation.vue'", "import DecisionCitation from './DecisionCitation.vue'\nimport VersionComparison from './VersionComparison.vue'"),
    ('active,action,load}', 'active,action,load,refresh}'),
    ('const months=ref(3),citation=ref(null)', "const months=ref(3),citation=ref(null),industry=ref(new URLSearchParams(location.search).get('industry')==='集成电路制造'?'集成电路制造':'半导体设备')\nwatch(()=>state.value?.request,value=>{if(value){industry.value=value.entity;months.value=value.horizon_months}})"),
    ("history.value.filter(r=>r.title==='半导体设备 · 候选研究')", "history.value.filter(r=>r.page==='candidates'||r.title?.endsWith(' · 候选研究'))"),
    ("const generate=()=>start({entity:'半导体设备'", "const generate=()=>start({entity:industry.value"),
    ("question:'半导体设备未来几个月", "question:industry.value+'未来几个月"),
    ('<h1>半导体设备，哪些公司值得先研究</h1>', '<h1>{{ industry }}，哪些公司值得先研究</h1>'),
    ('<label>研究期限 <select', '<label>行业 <select v-model="industry"><option>半导体设备</option><option>集成电路制造</option></select></label><label>研究期限 <select'),
    ('范围为本次实际取得的半导体设备分类池', '范围为本次实际取得的{{ industry }}分类池'),
    ('      <article v-for="(opinion,index)', '      <VersionComparison :id="id" page="candidates"/><section class="panel rep-section"><button class="btn" :disabled="submitting||active(state)" @click="refresh">更新本行业研究，另存版本</button><a class="btn" href="#/market">返回市场观察</a><a class="btn" :href="\'/api/quant/decision/runs/\'+id+\'/export\'" target="_blank" rel="noopener noreferrer">导出这次研究</a></section>\n      <article v-for="(opinion,index)'),
])
