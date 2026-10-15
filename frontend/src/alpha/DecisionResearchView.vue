<script setup>
import NextSessionPrediction from './NextSessionPrediction.vue'
import DecisionDevelopmentView from './DecisionDevelopmentView.vue'
import MediumTermResearch from './MediumTermResearch.vue'
const props=defineProps({id:String})
const developer=new URLSearchParams(window.location.search).get('developer')==='1'
const medium=props.id?props.id.startsWith('RUN-DEC-'):new URLSearchParams(window.location.search).get('research')==='1'
const viewHref=value=>{const url=new URL(window.location.href);value?url.searchParams.set('research','1'):url.searchParams.delete('research');url.hash='#/decision';return url.pathname+url.search+url.hash}
const toggle=()=>{const url=new URL(window.location.href);developer?url.searchParams.delete('developer'):url.searchParams.set('developer','1');return url.pathname+url.search+url.hash}
</script>
<template>
  <div class="page decision-horizons"><nav aria-label="选择研究期限"><a :class="['btn',!medium&&'btn-primary']" :href="viewHref(false)">下一交易日</a><a :class="['btn',medium&&'btn-primary']" :href="viewHref(true)">未来一至三个月</a></nav></div>
  <MediumTermResearch v-if="medium" :id="id?.startsWith('RUN-DEC-')?id:undefined" :developer="developer"/>
  <NextSessionPrediction v-else :id="id?.startsWith('RUN-NS-') ? id : undefined" :developer="developer" />
  <div class="page"><details v-if="developer" class="panel rep-section"><summary>前期 P0/P1 验收留档（当时状态）</summary><DecisionDevelopmentView :id="id" /></details><p class="condition-note"><a :href="toggle()">{{ developer?'返回用户视图':'开发者入口' }}</a></p></div>
</template>
<style>.decision-horizons{padding-bottom:0!important}.decision-horizons nav{display:flex;gap:12px;flex-wrap:wrap}</style>
