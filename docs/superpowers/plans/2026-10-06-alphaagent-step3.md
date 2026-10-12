# AlphaAgent 原型校准与追问版本实施计划

> 使用 executing-plans 在当前隔离分支逐项实施；用户已要求继续本步，自测后交付页面。

**Goal:** 按原型源码校准页面，并将追问绑定原任务、冻结数据和假设，生成保留旧报告的新版本。

**Architecture:** Vue 3 按原型结构移植纯展示 CSS，不复制计算；量化追问复用现有运行与 ReportRow 版本契约。使用 origin_run_id 和审计事件记录关联与幂等请求，不新增数据库迁移。只开放现有引擎支持的同区间复算、复权切换和显式零无风险利率；缺少输入或不支持的假设明确拒绝。

**Tech Stack:** Vue 3、Vite、ECharts、FastAPI、SQLAlchemy、pytest、Node test runner、Playwright。

**Spec:** ../specs/2026-10-06-alphaagent-redesign.md；原型 global.css、Workbench、ResearchFlow、ReportPage、StockPage、BacktestPage、LibraryPage 和用户回测报告截图。

## 全局约束

- 禁止复制原型计算；不新增财务公式，不升级尚未权威校准的指标。
- 原型深色 token、1320px 容器、44px 顶栏、250px 报告栏及其间距／字号直接复用；浅色切换和窄屏适配保留。
- 原始报告、计算产物与引用保持不可变；追问复用实际输入快照和期间，不取“最新”覆盖原输入。
- 同一研究只允许一项未结束的版本任务；幂等请求可在响应丢失后取回同一任务。
- 本步仍不实现交易账本回测、DCF、多股或自由模型语义推理；这些请求不静默转成普通单股复算。

## Task 1：冻结追问、幂等与版本契约

Files: src/marketpulse/quant/followups.py、api.py；tests/integration/quant/test_followups.py。

- [x] 写真实冻结输入集成测试：父报告 → 显式零利率复算 → 同 investigation、origin_run_id、输入一致、报告 v2；再次相同请求不重复启动，原报告字节与引用不变。
- [x] 测试尚未结束的父任务、缺失输入、非法假设、已存在活动子任务、幂等键内容冲突和价格口径缺失均拒绝；取消子任务后可重试创建后续版本。
- [x] `POST /api/quant/runs/{parent}/followups` 接收 question、request_key，解析现有支持口径，完整性检查父任务后冻结其实际 input_snapshot_ids。
- [x] 在创建事务中记录 `origin_run_id`、审计请求指纹和关联，串行分配同研究任务；`GET /runs/{id}` 返回版本列表与父任务信息，历史条目返回报告版本。

## Task 2：原型界面校准及版本交互

Files: frontend/src/styles/alpha.css、App.vue、alpha/{Workbench,ResearchView,ReportView,ReportBody,StockView,BacktestView,ProcessCard}.vue、FollowupBar.vue、useFollowup.js；frontend/tests/alpha.test.js。

- [x] 将原型 global.css 作为未经修改的展示基础；单独追加浅色、真实缺项、弹窗与窄屏适配，避免覆盖原型桌面几何。
- [x] 恢复原型行情条结构、工作台卡片尺寸、报告信息 kv／编号结论卡、研究摘要卡、折叠过程和 sticky 追问输入框；回测恢复原型五字段与三个预设，执行仍禁用。
- [x] 追问通过真实接口发起；loading、防重复、失败保留文字、同一请求重试复用 request_key、离页忽略迟到响应。
- [x] 报告信息显示标的、版本、证据数；版本菜单切换已落盘旧报告，父版本链接可回看，不重启任务。
- [x] 用 Node 验证提交去重、丢响应重试和离页响应隔离。

## Task 3：自测与人工验收

Files: scripts/verify_alpha_ui.py、scripts/verify_alpha_followups.py、本步 results.md。

- [x] 跑量化测试、前端测试／构建、Ruff、diff check；共享报告输入修改时补跑报告相关集成测试。
- [x] 使用独立真实历史验收库和新端口，真实浏览器提交追问、完成 v2、切换 v1、刷新、历史恢复和拒绝不支持假设。
- [x] 复查 30 组宽度／主题／页面矩阵，对照原型关键 token 和 computed layout，并保存截图；缺项与阶段功能不能声称像素或功能 100% 已完成。
- [x] 记录实测结果、剩余原型功能与点击路径，交付本步页面后等用户反馈。

自动化与页面自测已完成，结果见 [本步验收记录](2026-10-07-alphaagent-step3-results.md)。人工点击验收待用户反馈。
