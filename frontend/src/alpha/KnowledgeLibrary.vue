<script setup>
import { ref } from 'vue'
import { documentPath, useKnowledgeLibrary } from './useKnowledgeLibrary.js'
import { dateTime } from './model.js'
const ctl=useKnowledgeLibrary()
const query=ref(''), category=ref('')
const developer=new URLSearchParams(location.search).get('developer')==='1'
const {catalog,items,selected,error,detailError,loading,detailLoading}=ctl
function filter(value){category.value=value;ctl.search(query.value,value)}
</script>
<template>
  <div class="page knowledge-library">
    <section class="panel rep-section">
      <div class="section-heading"><div><h1>投资知识库</h1><p class="muted">按投资问题查资料，沿原文理解方法与适用条件。</p></div><a class="btn" href="#/">返回工作台</a></div>
      <div v-if="catalog" class="knowledge-stats"><span><strong>{{ catalog.document_count }}</strong>份原始资料</span><span><strong>{{ catalog.source_families }}</strong>个来源族</span><span><strong>{{ catalog.categories.length }}</strong>类研究主题</span></div>
      <form class="knowledge-search" @submit.prevent="ctl.search(query,category)"><input v-model="query" aria-label="搜索方法资料" maxlength="120" placeholder="搜索资本回报、入场时机、回撤控制、因子投资…"><button class="btn btn-primary" :disabled="loading">{{ loading?'正在检索…':'搜索资料' }}</button><button class="btn" type="button" :disabled="loading" @click="query='';category='';ctl.search('','')">全部资料</button></form>
      <div class="knowledge-filters" role="group" aria-label="按研究主题筛选"><button class="chip" :aria-pressed="!category" @click="filter('')">全部</button><button v-for="item in catalog?.categories||[]" :key="item" class="chip" :aria-pressed="category===item" @click="filter(item)">{{ item }}</button></div>
      <p class="condition-note">当前收录公开原文的定位段落。具体书籍的正文覆盖、方法规则和投资效果分别记录。</p>
      <p v-if="error" class="error-text" role="alert">{{ error }} <button class="btn" @click="ctl.load()">重新读取</button></p>
    </section>
    <div class="knowledge-layout">
      <section class="panel knowledge-results" aria-label="资料检索结果">
        <div class="section-heading"><h2>方法资料</h2><span class="muted">{{ items.length }}份匹配</span></div>
        <p v-if="!items.length&&!loading" class="muted">没有匹配的已归档资料，可更换关键词或查看全部资料。</p>
        <button v-for="doc in items" :key="doc.document_id+doc.version" class="knowledge-row" :class="{selected:selected?.document_id===doc.document_id&&selected?.version===doc.version}" @click="ctl.select(doc)"><span class="tag">{{ doc.category }}</span><strong>{{ doc.title }}</strong><span>{{ doc.authors.join(' · ') }}</span><small>{{ doc.publication_label }} · {{ doc.excerpt_count }}处原文定位</small></button>
      </section>
      <section class="panel knowledge-detail" aria-label="原文与读取范围" aria-live="polite">
        <p v-if="detailLoading" class="muted">正在核对封存原文…</p>
        <p v-else-if="detailError" class="error-text" role="alert">{{ detailError }}</p>
        <template v-else-if="selected">
          <span class="tag tag-gold">{{ selected.source.category }}</span><h2>{{ selected.source.title }}</h2><p class="muted">{{ selected.source.authors.join(' · ') }} · {{ selected.source.publication_label }}</p>
          <div class="knowledge-tags"><span v-for="tag in selected.source.tags" :key="tag" class="tag">{{ tag }}</span></div>
          <p>{{ selected.source.reading_note }}</p>
          <dl class="knowledge-meta"><dt>读取范围</dt><dd>公开段落定位，未标记为整书阅读</dd><dt>原文取得</dt><dd>{{ dateTime(selected.fetched_at) }}</dd><dt>已归档</dt><dd>{{ selected.body_characters.toLocaleString() }}个正文字符 · {{ selected.excerpts.length }}处定位</dd></dl>
          <div class="bt-actions"><a class="btn" :href="selected.source.url" target="_blank" rel="noopener noreferrer">打开作者原文 ↗</a><a class="btn" :href="'/api'+documentPath(selected)+'/original'">下载封存原文</a></div>
          <p class="muted knowledge-rights">{{ selected.source.rights_note }}</p>
          <div v-for="(passage,index) in selected.excerpts" :key="index" class="knowledge-passage"><div><strong>原文定位 {{ index+1 }}</strong><span class="muted">字符 {{ passage.start }}–{{ passage.end }}</span></div><blockquote>{{ passage.text }}</blockquote></div>
          <details v-if="developer" class="knowledge-audit"><summary>开发者：版本与原文核验</summary><dl><dt>资料ID</dt><dd>{{ selected.document_id }}</dd><dt>版本哈希</dt><dd>{{ selected.version }}</dd><dt>原文归档</dt><dd>{{ selected.raw_ref }}</dd><dt>检索</dt><dd>{{ catalog.search_mode }}</dd><dt>读取与效果</dt><dd>这是不可变的原文读取记录；发布规则请查看方法规则，投资有效性单独验证。</dd></dl><a :href="'/api'+documentPath(selected)" target="_blank" rel="noopener noreferrer">查看固定版本记录</a></details>
        </template>
        <p v-else class="muted">选择左侧资料查看原文与读取范围。</p>
      </section>
    </div>
  </div>
</template>
<style scoped>
.knowledge-library{max-width:1500px}.knowledge-library h1{font-size:28px;margin:0}.knowledge-stats{display:flex;gap:28px;margin:18px 0}.knowledge-stats span{display:flex;align-items:baseline;gap:8px;color:var(--text-2)}.knowledge-stats strong{font-size:26px;color:var(--text-1)}.knowledge-search{display:flex;gap:10px}.knowledge-search input{flex:1;min-width:0;padding:10px 12px;background:var(--bg-raised);color:var(--text-1);border:1px solid var(--border-strong);border-radius:4px;font:inherit}.knowledge-filters,.knowledge-tags{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.knowledge-filters [aria-pressed=true]{color:var(--accent);border-color:var(--accent)}.knowledge-layout{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);gap:18px;margin-top:18px;align-items:start}.knowledge-results,.knowledge-detail{min-width:0;padding:20px}.knowledge-row{display:grid;gap:7px;width:100%;text-align:left;padding:16px 10px;background:transparent;color:var(--text-1);border:0;border-bottom:1px solid var(--border);cursor:pointer;font:inherit}.knowledge-row .tag{justify-self:start}.knowledge-row strong{font-size:15px}.knowledge-row span:not(.tag),.knowledge-row small{color:var(--text-2)}.knowledge-row.selected{background:var(--bg-raised)}.knowledge-detail h2{margin:12px 0;font-size:21px}.knowledge-meta{display:grid;grid-template-columns:85px minmax(0,1fr);gap:8px;margin:20px 0}.knowledge-meta dt{color:var(--text-2)}.knowledge-meta dd{margin:0}.knowledge-passage{border-top:1px solid var(--border);padding-top:16px;margin-top:18px}.knowledge-passage>div{display:flex;justify-content:space-between;gap:12px}.knowledge-passage blockquote{margin:12px 0 0;padding-left:14px;border-left:2px solid var(--accent);font-size:14px;line-height:1.75;overflow-wrap:anywhere;white-space:pre-wrap}.knowledge-rights{font-size:12px}.knowledge-audit{border-top:1px solid var(--border);padding-top:18px;margin-top:20px}.knowledge-audit summary{cursor:pointer}.knowledge-audit dd{margin:4px 0 14px;overflow-wrap:anywhere;font-size:12px}.knowledge-library :is(button,a,input,summary):focus-visible{outline:2px solid var(--accent);outline-offset:3px}@media(max-width:900px){.knowledge-layout{grid-template-columns:minmax(0,1fr)}.knowledge-search{flex-wrap:wrap}.knowledge-search input{flex-basis:100%}.knowledge-stats{gap:14px;flex-wrap:wrap}.knowledge-stats strong{font-size:22px}.knowledge-library h1{font-size:24px}}
</style>
