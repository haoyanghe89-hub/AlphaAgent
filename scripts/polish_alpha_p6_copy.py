from pathlib import Path
p=Path('frontend/src/alpha/MarketResearch.vue')
s=p.read_text(encoding='utf8')
old='当前覆盖半导体设备与集成电路制造。完整沪深 A 股成交分母尚未核定，全市场占比暂缺；下方仅比较固定两行业联合池。'
assert old in s
s=s.replace(old,'研究范围为半导体设备与集成电路制造。只比较已取得的范围，实际覆盖和日期以下方结果为准。')
old='<p class="condition-note">{{ data.coverage.scope_note }} 成交活跃不能直接解释为资金流入或买入理由。</p>'
assert old in s
s=s.replace(old,'<p v-if="data.metrics.length" class="condition-note">只在本次两个行业范围内比较，不代表全市场排行；成交活跃不能直接解释为资金流入。</p>')
s=s.replace('行业目录本次缺失，未生成行业成交关注排序，也没有缩小分母。此前行业研究可在下方按保存日期回看。','本次未生成行业排序。此前行业研究可在下方按保存日期回看。')
s=s.replace('。未复权价格观察，属于近期走势，不是未来方向预测。','。来源为腾讯日行情，起始收盘 {{ data.market_index.base_close }}。这是近期价格观察，不是未来方向预测。')
s=s.replace('<p>{{ data.coverage.catalog_note }}</p>','<p>{{ data.coverage.scope_note }}</p><p>{{ data.coverage.catalog_note }}</p>')
p.write_text(s,encoding='utf8')
