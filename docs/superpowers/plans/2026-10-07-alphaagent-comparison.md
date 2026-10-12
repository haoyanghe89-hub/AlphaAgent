# AlphaAgent 双标的比较 Implementation Plan

**Goal:** 工作台双标的提问 → 同窗口确认 → 两条真实研究支路 → 五维比较报告 → 证据、导出与历史回看。

**Architecture:** Supervisor 只组合已有确定性计算的单元格；Data / Analyst / Verifier 在两条单股支路继续执行。比较材料与子报告绑定、内容寻址归档，每次展示校验子报告与材料哈希。沿用现有报告治理，比较完成不升级指标验证或发布状态。

**Tech Stack:** FastAPI / SQLAlchemy / Pydantic / 离线量化执行器 / Vue3 / ECharts。

**Spec:** `docs/superpowers/specs/2026-10-06-alphaagent-redesign.md`，阶段 4；原型 `ReportPage.tsx`、`ReportBody.tsx` 与比较报告展示契约。

## 约束

- 原型计算代码不得移植或复制。比较层不产生新的财务指标，不做胜场计数或投资排名。
- 冻结期间、asof、复权与财务单元格口径逐项对齐；财报期间、单位、公式不同显示 METHOD_DIFFERENCE；缺失显示 UNAVAILABLE。
- 五维结构完整：成长性、盈利能力、盈利质量、估值水位、一年动量。单季、净现比、历史分位或完整一年缺失必须写明，不能用近似指标冒充。
- 图表复用已经对照原型的样式；原有真实财务、估值、价格风险图作为补充保留。
- 不提交、不推送；在现有 codex 分支实施。旧预览 8834 保留，新预览使用独立数据库和新任务。

## Task 1：比较契约、执行与完整性

Files: `src/marketpulse/quant/comparison.py`、`quant/api.py`、报告 assembler / chinese_writer；测试 `tests/unit/quant/test_comparison.py`、`tests/integration/quant/test_comparison_api.py`。

- [x] 用固定单元格测试：同期间／单位／公式 MATCH，错期 METHOD_DIFFERENCE，空值 UNAVAILABLE；revision 身份保留但不作为公式差异。
- [x] `QuantComparisonIn(subjects: tuple[QuantRunIn, QuantRunIn])` 限制恰好两只不同股票、冻结输入、相同 asof / 起止日 / 复权；逐支路调用 `validate_pins`。
- [x] `/comparisons` 创建父研究和两条支路；父任务等待支路并记录真实阶段，取消时停止子任务，部分失败可读但不编造数值。
- [x] `compose_comparison(subjects)` 引用原单元格，不重算财务指标；`presentation` 对冻结组合和当前支路完整性逐项核对，漂移返回 409。
- [x] 标准报告快照绑定比较材料哈希；Markdown 导出含原值、期间、公式、来源 citation 链接及验证状态。

## Task 2：原型页面与范围确认

Files: `frontend/src/alpha/model.js`、`ScopeDialog.vue`、`ComparisonSections.vue`、`ReportView.vue`、`ResearchView.vue`、`Workbench.vue`、`LibraryView.vue`；测试 `frontend/tests/alpha.test.js`。

- [x] 双股票识别保留问题与顺序；一只股票加“对比”不能降级为单股，三只不静默截取。
- [x] 两组窗口取精确交集，不默认改写用户指定年份；显示两股票及统一口径，无法对齐时不提交。
- [x] 报告左栏双标的、场景和目录；结论速览、五维表、双标的小结和财务图、风险与局限；数字点击传入对应支路证据。
- [x] 历史标签显示对比；过程卡显示支路实际状态、可回看原报告。比较追问不伪装为单股重算。

## Task 3：自测与页面交付

Files: `scripts/verify_alpha_comparison.py` 与本阶段 results 文档。

- [x] 量化相关 pytest、报告测试、前端测试／构建、Ruff、diff whitespace。
- [x] 从既有真实冻结数据复制到新的私有数据库，源码稳定后重新执行两股研究与比较。
- [x] 浏览器验证完整提交→报告→证据→刷新→历史→导出；375 / 768 / 1440 宽度、浅深两主题；与原型布局及图表样式对照，记录控制台错误。
- [x] 交付新预览链接和点击步骤，明确仍缺失的数据域，等待用户验收再进入回测阶段。
