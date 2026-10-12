# 真实 K 线与 DCF 实施步骤

本轮按用户已批准的原型继续实现，Vue 3 与 deepsearch 多 agent 底盘不变。原型计算代码不可移植，仅复用展示结构与样式。

- [x] `compute/exhibits.py`：真实 OHLC、成交量及 MA20/MA60；缺失与预热不足不填零，不由收盘价合成 K 线。
- [x] `domain.py`、`compute/dcf.py`、`compute/engine.py`：独立 Decimal FCFF 情景模型，5+5 年预测与永续终值，企业价值加现金、减债务与少数股东权益，再除有明确口径的股数。折现率必须高于永续增速。
- [x] 官方年报输入独立归档，明确披露时间、合并口径、元/千元、股数、债务/少数权益账面代理及非现金营运资本代理局限；金融企业及缺项不套用模型。
- [x] `api.py`、`followups.py`、`charts.py`：假设、输入及敏感性输出进入不可变产物；结构化参数重算创建子版本，原版本不覆盖。
- [x] `DcfPlayground.vue`、报告/个股页：原型滑杆、结果、敏感性矩阵及 K 线样式；点击数值查看依据；前端不计算估值或均线。
- [x] 固定手算样本、NYU 公开算例、年报输入核对、同输入复算分别验收；未完成独立权威核对与归一化的估值保持 UNVERIFIED，不以可复现替代正确性。
- [x] 新建隔离预览数据库和 8834 页面，真实冻结行情与美的官方年报跑通；验证缩放、证据、参数重算与旧版本不变，再交用户点击测试。

DCF 展示为基于历史年报的条件情景测算。历史现金流不等于未来预测，账面债务及少数权益不等于市值；不能把经营现金流直接除以股数，也不能宣称为已核验目标价。新增真实财报输入不足时明确缺项，保留现有结果。

方法核对：[NYU FCFF](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/cashflows.htm)，[公开 DCF 算例](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/problems/dcfprob.htm)。输入：[美的 2023 年报](https://static.cninfo.com.cn/finalpage/2024-03-28/1219429374.PDF)。
