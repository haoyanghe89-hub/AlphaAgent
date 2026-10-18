<script setup>
import { ref } from 'vue'
import { useMethodRules, rulePath } from './useMethodRules.js'
const query=ref(''),ctl=useMethodRules()
const {catalog,items,selected,reviews,error,detailError,loading,detailLoading}=ctl
</script>
<template>
  <div class="page method-audit">
    <section class="panel rep-section">
      <h1>内置知识 · 开发者核验</h1>
      <p class="muted">检查原文如何变成规则、改写边界和失败条件。规则已发布仅表示可作研究参考，投资效果尚未验证。</p>
      <p v-if="catalog" class="audit-stats"><strong>{{catalog.rules_published}}</strong> 条发布规则 · {{catalog.source_families}} 个来源族 · {{new Set(catalog.items.map(r=>r.rule.kind)).size}} 类规则 · 可执行路线 {{catalog.routes_executable}}</p>
      <form class="audit-search" @submit.prevent="ctl.load(query)"><input v-model="query" aria-label="搜索方法规则" placeholder="资本回报、买点、资金量、因子投资…" maxlength="120"><button class="btn btn-primary" :disabled="loading">{{loading?'核对中…':'搜索规则'}}</button><button type="button" class="btn" :disabled="loading" @click="query='';ctl.load()">全部规则</button></form>
      <p class="muted small">检索为词法匹配及同义词扩展；研究上下文按来源族去重。同作者多条观点不算独立证据，当前规则不用于明日方向预测。</p>
      <p v-if="error" role="alert" class="error-text">{{error}} <button class="btn" @click="ctl.load(query)">重试</button></p>
    </section>
    <div class="audit-grid">
      <section class="panel audit-list" aria-label="规则列表"><h2>{{items.length}} 条{{query?'匹配规则（最多十二条）':'发布规则'}}</h2><p v-if="!items.length&&!loading" class="muted">没有匹配的已发布规则。</p><button v-for="row in items" :key="row.rule_id+row.version" class="audit-row" :class="{selected:selected?.version===row.version}" @click="ctl.select(row)"><span class="tag">{{row.rule.kind}}</span><strong>{{row.rule.title}}</strong><small>{{row.source_families.join(' · ')}} · {{row.rule.horizons.join(' / ')}}</small></button></section>
      <section class="panel audit-detail" aria-label="规则出处与反例" aria-live="polite">
        <p v-if="detailLoading" class="muted">正在核对固定版本与真实审核记录…</p><p v-else-if="detailError" role="alert" class="error-text">{{detailError}}</p>
        <template v-else-if="selected">
          <span class="tag tag-gold">参考规则 · 效果未验证</span><h2>{{selected.rule.title}}</h2>
          <h3>作者主张</h3><p>{{selected.rule.source_claim}}</p><h3>系统研究规则</h3><p>{{selected.rule.system_rule}}</p><p class="condition-note">{{selected.rule.adaptation}}</p>
          <h3>必要输入与适用条件</h3><p>{{selected.rule.required_inputs.join(' · ')}}</p><ul><li v-for="s in selected.rule.conditions" :key="s">{{s}}</li></ul>
          <h3>检查步骤</h3><ol><li v-for="s in selected.rule.steps" :key="s">{{s}}</li></ol>
          <h3>反例与禁止推断</h3><ul><li v-for="s in [...selected.rule.counterexamples,...selected.rule.forbidden_inferences]" :key="s">{{s}}</li></ul>
          <h3>参数出处</h3><p v-if="!selected.rule.parameters.length" class="muted">没有新增数值阈值；交易参数需另行核算及验证。</p><div v-for="p in selected.rule.parameters" :key="p.name" class="audit-block"><strong>{{p.name}}：{{p.value}}</strong><p>{{p.meaning}}</p><small>{{p.origin==='AUTHOR_ORIGINAL'?'作者原始值 · 仅来源市场参考':'系统提案 · 未验证'}} · {{p.applicability}}</small></div>
          <h3>封存正文定位</h3><div v-for="c in selected.rule.citations" :key="c.document_id+c.start" class="audit-block"><strong>{{c.document_id}}</strong><small>字符 {{c.start}}–{{c.end}}</small><blockquote>{{c.quote}}</blockquote><a :href="'/api/quant/knowledge/documents/'+c.document_id+'/versions/'+c.version+'/original'">下载该版本原文</a></div>
          <h3>真实模型职责审核</h3><p class="muted small">提炼候选由 Codex 阅读封存原文形成；以下两种职责调用同一模型，不是外部专家或独立效果验证。</p><div v-for="review in reviews" :key="review.review_id"><div v-for="check in review.reviews" :key="check.role" class="audit-block"><strong>{{check.role==='SupportReviewer'?'原文支持审核':'反例与适用性复核'}} · {{check.verdict}}</strong><p>{{check.reason}}</p><small v-if="check.response_scope==='VALIDATED_ITEM_FROM_FAILED_BATCH'">该项按严格契约通过；同批其他不合格项已单独重审，原失败批次日志保留。</small></div><a :href="'/api/quant/knowledge/audits/'+review.audit_id" target="_blank" rel="noopener noreferrer">查看真实调用归档 ↗</a></div>
          <details class="audit-block"><summary>固定版本与生命周期</summary><dl><dt>规则</dt><dd>{{selected.rule_id}}</dd><dt>版本</dt><dd>{{selected.version}}</dd><dt>状态</dt><dd>{{selected.status}} · REFERENCE_ONLY · UNVALIDATED</dd><dt>冲突组</dt><dd>{{selected.rule.conflict_group}}</dd><dt>发布时间</dt><dd>{{selected.events.find(e=>e.action==='PUBLISHED')?.created_at}}</dd></dl><a :href="'/api'+rulePath(selected)" target="_blank" rel="noopener noreferrer">固定版本 JSON ↗</a></details>
        </template><p v-else class="muted">选择规则查看对应原文和审核。</p>
      </section>
    </div>
  </div>
</template>
<style scoped>
.method-audit{max-width:1500px}.method-audit h1{margin:0;font-size:27px}.audit-stats strong{font-size:26px;color:var(--accent)}.audit-search{display:flex;gap:10px}.audit-search input{flex:1;min-width:0;padding:10px;background:var(--bg-raised);color:var(--text-1);border:1px solid var(--border-strong);border-radius:4px;font:inherit}.small{font-size:12px}.audit-grid{display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr);gap:18px;margin-top:18px;align-items:start}.audit-list,.audit-detail{min-width:0;padding:20px}.audit-row{display:grid;gap:8px;width:100%;text-align:left;padding:15px 10px;background:transparent;border:0;border-bottom:1px solid var(--border);font:inherit;color:var(--text-1);cursor:pointer}.audit-row .tag{justify-self:start}.audit-row small{color:var(--text-2);overflow-wrap:anywhere}.audit-row.selected{background:var(--bg-raised)}.audit-detail h2{font-size:22px;margin:14px 0}.audit-detail h3{font-size:15px;margin:22px 0 8px;color:var(--accent)}.audit-detail p,.audit-detail li{font-size:14px;line-height:1.7}.audit-detail :is(ul,ol){padding-left:22px}.audit-block{border-top:1px solid var(--border);padding-top:14px;margin-top:14px;overflow-wrap:anywhere}.audit-block small{display:block;color:var(--text-2);margin-top:6px}.audit-block blockquote{margin:12px 0;padding-left:12px;border-left:2px solid var(--accent);font-size:13px;line-height:1.7;white-space:pre-wrap}.audit-block dd{margin:4px 0 12px;font-size:12px}.audit-block summary{cursor:pointer}.method-audit :is(input,button,a,summary):focus-visible{outline:2px solid var(--accent);outline-offset:3px}@media(max-width:900px){.audit-grid{grid-template-columns:minmax(0,1fr)}.audit-search{flex-wrap:wrap}.audit-search input{flex-basis:100%}}
</style>
