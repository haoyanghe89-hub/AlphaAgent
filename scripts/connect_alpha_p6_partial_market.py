from pathlib import Path
p=Path('frontend/src/alpha/MarketResearch.vue')
s=p.read_text(encoding='utf8')
old='<h2>接下来，先看哪个行业</h2>'
assert old in s
s=s.replace(old,'<h2>{{ data.metrics.length?\'接下来，先看哪个行业\':\'当前市场观察\' }}</h2>')
old='<p class="condition-note">资料截止 {{ stamp(data.report.cutoff_at) }} · 行情 {{ data.heat_inputs.sessions.at(-1) }} · 共 {{ data.coverage.members_total }} 家成分，完整序列 {{ data.coverage.members_available }} 家</p>'
assert old in s
s=s.replace(old,'<p class="condition-note">资料截止 {{ stamp(data.report.cutoff_at) }} · 行情 {{ data.heat_inputs?.sessions.at(-1) || data.market_index?.last_session }}<template v-if="data.coverage.members_total!=null"> · 共 {{ data.coverage.members_total }} 家成分，完整序列 {{ data.coverage.members_available }} 家</template><template v-else> · 行业成分数量尚未取得</template></p>')
old='      <VersionComparison :id="id" page="market"/>'
assert old in s
s=s.replace(old,'      <section v-if="data.market_index" class="panel rep-section"><h2>沪深三百，近期实际怎么走</h2><div class="market-metrics"><section><span>近二十个交易日价格变化</span><strong>{{ pct(data.market_index.return_20) }}</strong></section><section><span>最近完整收盘</span><strong>{{ data.market_index.close }}</strong></section></div><p class="condition-note">{{ data.market_index.first_session }} — {{ data.market_index.last_session }}。未复权价格观察，属于近期走势，不是未来方向预测。</p><p class="notice">行业目录本次缺失，未生成行业成交关注排序，也没有缩小分母。此前行业研究可在下方按保存日期回看。</p><a class="btn" :href="industry(\'半导体设备\')">选择设备行业研究</a> <a class="btn" :href="industry(\'集成电路制造\')">选择制造行业研究</a></section>\n'+old)
p.write_text(s,encoding='utf8')
p=Path('frontend/src/alpha/IndustryAnalysis.vue')
s=p.read_text(encoding='utf8').replace('制造需求侧比较：{{ industry.manufacturer }}','制造资料：{{ industry.manufacturer }}').replace('设备行业指数仍缺项','对应细分行业指数仍缺项')
p.write_text(s,encoding='utf8')
