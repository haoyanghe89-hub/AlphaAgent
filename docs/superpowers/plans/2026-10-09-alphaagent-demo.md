# AlphaAgent 连续功能演示实施计划

> **For agentic workers:** 使用 executing-plans 在当前会话逐项执行；用户已授权继续开发并要求自测后页面验收，不另起并行任务，不提交已有工作区改动。

**Goal:** 在同一服务中，从首页连续演示已实现的研究、证据、DCF、K线、对比、历史与真实回测输入核对。

**Architecture:** 保留 Vue 3 原型页面及现有后端职责边界；首页示例从接入目录产生。沿用真实冻结输入和历史核对档案，在最终源码下重新执行量化研究，保留源码、输入与产物完整性门禁。

**Tech Stack:** Vue 3、FastAPI、SQLite、确定性量化工具、Playwright。

**Spec:** `docs/superpowers/specs/2026-10-06-alphaagent-redesign.md`。

## Global Constraints

- 原型计算代码不得移植或复制。
- 免费来源；历史冻结结果明确期间与研究时点，不假装实时行情。
- 5.2整体未完成、5.3未开始；回测输入检查完成不等于策略收益可计算。
- 不修改旧报告、旧哈希或门禁，不把工具职责记录说成五次模型调用。
- 保留既有未提交修改；实际自测后提供页面及点击顺序。

### Task 1: 让首页问题与当前目录匹配

**Files:** 修改 `frontend/src/alpha/model.js`、`Workbench.vue`、`frontend/tests/alpha.test.js`。

**Interfaces:** `workbenchExamples(stocks)` 返回能被 `prepareQuestion` 识别的单股、双股与DCF问题；无目录时不提供虚构标的问题。

- [x] 增加目录为空、仅一股、多股的测试，保证示例主体来自目录、股票问题可解析。
- [x] 实现动态示例与输入提示；保留原型组件和布局。
- [x] 前端测试与构建通过。

### Task 2: 分开展示数据检查与策略执行

**Files:** 修改 `src/marketpulse/quant/api.py`、`frontend/src/alpha/model.js`、`Workbench.vue`、`LibraryView.vue` 和回测API测试。

**Interfaces:** 历史列表增加 `check_status`，来源为冻结的检查报告；保留原有 `status` 与 `execution_status`。状态筛选识别已保存检查与正在采集的检查。

- [x] API回归断言 `status=FAILED`、`check_status=COMPLETED`、`execution_status=BLOCKED` 同时保留，不返回绩效。
- [x] 检查记录损坏时列表标识完整性失败，不能标成检查完成；报告页仍执行完整源链验证。
- [x] 页面显示“检查完成 · 回测受阻”，不改成成功回测。
- [x] 定向后端与前端回归通过。

### Task 3: 同一环境准备并点击演示

**Files:** 新增 `scripts/prepare_alpha_demo.py`、`scripts/verify_alpha_demo.py`、`docs/alphaagent-demo.md`；更新本计划验收结果。

**Interfaces:** 准备脚本只调用真实服务、显式指定冻结窗口，生成实际报告链接清单；浏览器脚本从首页发起新研究并检查父子版本、真实证据、K线、对比、回测检查和历史回看。

- [x] 完成源码修改后重启现有8839服务，核对实际进程命令后仅停止本项目预览进程。
- [x] 用既有真实冻结窗口重建三只股票报告；保留真实官方历史核对档案及原始期间。
- [x] 从首页点击完成连续流程，核对API输出、父报告不变、缺失与受阻状态、刷新恢复及深浅色/窄屏。
- [x] 记录新鲜测试结果、真实链接、已实现范围和5.2缺项，提供用户手动点击验收。

### 演示验收发现的性能与加载修正

- [x] 同一产物按批次离线复算一次，逐项验证结果和缺项继续独立生成；篡改任一单元格先拒绝。
- [x] 引用构建与引用验证各建立独立批次，各自重新读取原始输入和产物；不跨请求缓存信任，成功后损坏输入仍被发现。
- [x] 历史列表和行情摘要分开更新，实际浏览器阻塞 overview 响应时历史记录仍可读取。
- [x] 最终源码81项定向后端回归、57项前端测试、构建和本轮Ruff通过。
