# 统一研究与多 Agent 实施计划

> **For agentic workers:** 使用 executing-plans 在当前分支内顺序执行；用户已授权第 1、3 项一起完成。不自动提交或推送。

**Goal:** 动态证券实时研究与真实多 Agent 协作在同一可回看、可恢复的服务内完成。

**Architecture:** 参数化现有实时源与确定性计算；共享研究 API、事务登记与内容寻址证据；复用现有模型适配器，Supervisor 调度五个结构化角色。

**Tech Stack:** FastAPI / Pydantic / SQLAlchemy / existing OpenAI-compatible adapter / Vue 3 / pytest / Playwright。

**Spec:** docs/superpowers/specs/2026-10-10-alphaagent-unified-agents-design.md

## 全局约束

- 免费市场数据；不购买、不新增密钥，不输出已有凭据。
- Vue 3 与原型图表；不复制原型计算代码。
- 数据缺失、方法不适用、模型失败及未获权威核验分别呈现。
- 旧输入和结果不可修改；历史回看与新执行分别检查环境。

### A：动态证券与计算适用性

**Files:** live/securities.py, sources.py, calculate.py；tests/unit/quant/test_research_securities.py。

**Interface:** resolve(query) -> subject；collect(..., subject=subject) -> values,sources；build(...,subject=subject) -> report。

- [x] 编写指数/同代码股票筛选、歧义与非法代码、错误证券、银行 DCF 禁用、可选缺项测试。
- [x] 参数化行情、三表、财务类型和风险来源；不同失败按模块记录，不使用旧报告代替。
- [x] 真实采集四类标的，检查证据和每个适用图表。

### B：共享版本存储与兼容回放

**Files:** live/store.py；migrations/versions/20261010_10_quant_research.py；execution/service.py；charts.py。

**Interface:** ResearchStore.get/put/list/recover；旧 live 文件保留读取；input_for(job_id, require_current_environment=True)。

- [x] 用隔离数据库验证幂等、版本不变、调用哈希、重启恢复及结果篡改拒绝。
- [x] 新研究接入既有事务数据库与 blob 存储；旧文件只读兼容。
- [x] 历史报告读取仍校验冻结描述符和数值产物，环境漂移保留提示；新执行/复算保留严格检查。

### C：真实角色、工具、证据检索和记忆

**Files:** live/agents.py, evidence.py, service.py；tests/integration/quant/test_research_workflow.py；tests/unit/quant/test_research_report.py。

**Interface:** ResearchWorkflow.run(state, settings, model_port)；typed Planner/Data/Analyst/Verifier/Writer outputs；allowlisted dispatch。

- [x] 验证未知引用、模型伪造数字、不合法工具、不支持问题、模型失败及预算耗尽被拒绝。
- [x] 复用现有模型适配器与重试分类；调用日志记录实际模型/用量/输入输出哈希。
- [x] 披露资料和公告文本分块、定位、检索；父任务上下文有界传递；所有结果以本轮证据为准。
- [x] 成功步骤可重放，中断恢复不重复成功请求；缺少模型不能显示 Agent 完成。

### D：统一 API 和 Vue 页面

**Files:** live/research_api.py；investigation/server.py；scripts/serve_quant_o.py；App.vue；Workbench.vue；LiveResearchView.vue；useLiveQuotes.js。

- [x] 首页、股票页与追问使用共享入口；live 路由兼容旧报告。
- [x] 展示动态证券、适用方法/缺项、Agent 结论/证据、实际调用和恢复入口；沿用原型布局与图表。
- [x] DCF/策略重算新版本明确旧解读不沿用；自然语言追问调用 Agent，父任务可回看。

### E：真实验收和交付

- [x] 针对性 Python、前端测试、生产构建、相关 Ruff 与 diff 检查。
- [x] 真实四类标的研究及追问；浏览器从首页到结果、来源、重算、历史、导出；深浅及窄屏。
- [x] 更新演示与验收文档，列明支持范围、调用证据和剩余 5.2 边界；提供用户点击入口。

## 验收记录

已实现本轮第 1、3 项；结果见 [统一研究与多 Agent 验收](2026-10-10-alphaagent-unified-agents-results.md)。正式全市场回测、任意证券实时比较及权威数值核验仍保留后续边界。未提交或推送。
