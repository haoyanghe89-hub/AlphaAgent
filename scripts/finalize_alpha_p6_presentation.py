from pathlib import Path
p=Path('frontend/src/alpha/MediumTermResearch.vue')
s=p.read_text(encoding='utf8')
old='<VersionComparison :id="id"/><ResearchOpinion :report="report" :subject="state.subject" @evidence="citation=$event"/>'
assert old in s
s=s.replace(old,'<ResearchOpinion :report="report" :subject="state.subject" @evidence="citation=$event"/><VersionComparison :id="id"/>')
s=s.replace('<p>{{ state.error }}</p>','<p>{{ developer?state.error:\'本次未能完成资料核查与复核，未发布新判断。已取得的资料和旧观点保留，可重试或重新发起。\' }}</p>')
p.write_text(s,encoding='utf8')
p=Path('frontend/src/alpha/IndustryCandidates.vue')
s=p.read_text(encoding='utf8').replace('<p>{{ state.error }}</p>','<p>{{ developer?state.error:\'本次行业资料或意见复核未完成，尚未发布候选。失败记录保留，可重试。\' }}</p>')
p.write_text(s,encoding='utf8')
p=Path('frontend/src/alpha/MarketResearch.vue')
s=p.read_text(encoding='utf8').replace('<span class="market-index">','<span v-if="data.selection.ranking.length" class="market-index">')
p.write_text(s,encoding='utf8')
