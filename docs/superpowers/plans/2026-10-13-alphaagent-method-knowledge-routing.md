# AlphaAgent Method and Policy Routing Implementation Plan

> **For agentic workers:** Use executing-plans and the current session's delegated tasks to implement this approved increment. Do not commit, push, clean, or replace the shared workspace.

**Goal:** 真实股票研究按业务选择可执行方法，读取有原文定位的方法笔记与政策资料，并保存实际执行和复核记录。

**Architecture:** 复用四角色与 CAS / SQLite。在原流程旁追加 opt-in `include_methods` 和新提示版本 P4K；旧请求默认 false，旧结果按原版本校验。方法知识不计作当前公司事实。政策原文作为独立、受时间和适用性约束的证据。

**Tech Stack:** Python / Pydantic / SQLite / 现有 SourceClient / Vue 3 / 原型图表；不新增外部数据库或依赖。

**Spec:** [获准设计](../specs/2026-10-13-alphaagent-method-knowledge-routing-design.md)

## Global Constraints

- 用户已授权开发；政策：中国国家级为主，相关海外政策按业务关联加入。
- 真实数据与模型，明确方向，原输入和观点不可变；保留旧 5.2 准入及下一交易日协议。
- 方法取证、政策取证共享既有请求和模型预算；52 次来源请求上限、24 次角色调用 / 60,000 tokens / 900 秒不因本增量提高。
- 无新定时任务；无提交 / 推送 / 清理。完整回归运行期间不修改 src 树，防止旧 5.2 指纹检查受并行编辑干扰。
- 普通用户看结果，开发者看选择理由、执行日志、覆盖与缺项。未取得的政策和未读的书籍部分不宣称已入库。

## Task 1: 方法与政策原始来源（并行、自测）

**Files:** 新 `decision/knowledge.py`、`knowledge_notes.json`、`policy_sources.py`；单元 `test_knowledge.py` / `test_policy_sources.py`。

**Interfaces:** 两模块均为 `collect(repo, client, subject) -> snapshot`、`attach(repo, data, snapshot) -> data`、`verify(repo, data)`、`model_context(data) -> dict`。政策提供 `verify_item(repo, item)`；知识提供 `catalog()`。

- [x] 实际读取作者公开方法资料和中国 / 海外官方政策，保存读取范围和获取结果。
- [x] 每次研究有界抓取并封存 raw_ref / sha256 / fetched_at / 定位；失败是 gap。
- [x] 政策正式 / 征求意见 / 生效 / 修订 / 废止 / 未核定分别表示；不能判断公司资格时保留条件。
- [x] 方法笔记不加入 `bundle.items`；政策用新来源族的可定位原文证据。
- [x] 自测 HTML 正文定位、修改检测、日期状态、来源去重和主体绑定。合成夹具不当真实政策。

## Task 2: 可执行方法选择和业务画像

**Files:** 新 `decision/routing.py`、`tests/unit/quant/decision/test_routing.py`；不重写旧 M01–M08。

**Interfaces:** `prepare(repo,data,subject,horizon) -> data` 产生 `method_routing`；`select(data,plan) -> data` 检查 Planner 建议；`model_context(data) -> dict`；`finish(data,proposal,review) -> data`；`verify(repo,data)`；`guard_plan(plan,data)`。

- [x] 从封存身份资料和公司 PDF 形成多业务 / 未知可表示的画像。
- [x] 建立带 executor、角色、期限、输入及输出合同的方法变体；完整财务 / 逆向估值输入缺失则禁用对应计算，经营原文分析可有限执行。
- [x] 先确定性筛候选，再接受模型主方法和辅助方法建议；设备与制造分别选路，未知角色不能冒用模板。
- [x] 输入核查含主体、时点和真实来源；不接受单纯同名输入标志。
- [x] 测试要求：设备不能选制造主方法、制造不能选设备传导、次日不能继承中期方向、重复方法拒绝、缺估值拒绝、空方法知识不作为经营事实。

```python
assert equipment['profile']['primary_role'] == 'EQUIPMENT'
assert manufacturing['profile']['primary_role'] == 'MANUFACTURING'
assert 'EQ-TRANSMISSION' not in manufacturing_allowed_primary
with pytest.raises(ValueError):
    guard_plan(invalid_method_plan, equipment_data)
```

## Task 3: 四角色、封存与兼容

**Files:** `decision/contracts.py` / `api.py` / `tools.py` / `agents.py` / `medium.py` / `store.py`；新集成 `test_routing_integration.py`。

- [x] `include_methods=false` 默认；true 仅 latest / medium_term 且带既有行业与资金流程；旧幂等请求补默认字段。
- [x] 新 Planner schema 保存实际选择；新 Analyst 含 method_assessment 与 policy_assessment，Verifier 必须审核 METHODS / POLICY。
- [x] 先知识 / 政策取证，再取得固定价格资金截止；所有材料不得越封存截止。
- [x] 分析和复核输入保留准确方法合同、政策条款和公司反证；追加未知来源不能解除检查。
- [x] 首次签发保存实际执行记录与方法版本，不用阶段固定清单冒充执行。旧 P4 提示及读取语义保留。
- [x] 集成验证拒绝未选方法、伪造政策利益、缺 METHODS / POLICY 复核、执行状态改写、来源 / 选择 / 输入篡改；验证旧 P4 不变、导出一致与补证闭环。

## Task 4: 用户与开发者页面（并行）

**Files:** 新 `MethodPolicyAnalysis.vue`、`frontend/tests/methodPolicy.test.js`；现有 `MediumTermResearch.vue` / `DecisionCitation.vue`。

- [x] 新请求启用 flag，P4K 结果可识别；旧报告不新增伪造内容。
- [x] 用户显示业务研究重点与政策影响、反对风险、下一核查和可点击原文；无大面积算法卡。
- [x] 开发者折叠区显示业务证据、选择与停用理由、实际执行、知识读取范围、政策版本与覆盖。
- [x] 原型主题、390px 手机、引用弹窗和历史记录回归。

## Task 5: 真实运行与交付

**Files:** 新 `scripts/verify_alpha_decision_routing.py`、阶段结果文档；私有 `.phase3-quant-o/decision-routing-20261013-01/`。

- [x] 阶段起点 SQLite 在线备份与已完成记录 payload 哈希；核对运行 PID 后才重启本阶段服务。
- [x] 单元 / 集成与 Ruff，前端测试 / build；全 src 冻结后完整 quant 回归，包括旧 5.2。
- [x] 创建真实新版研究，读取真实模型输入与响应，核对政策原文与方法笔记、实际角色调用和执行记录。原有 P4 与全部完成记录哈希不变。
- [x] 同一接口实际核对设备与制造选路；制造专业能力仍明确有限，不宣布 P6 已完成。
- [x] 浏览器真实点击、桌面 / 手机 / 主题、普通 / 开发者分离、原文链接及导出；提供用户链接、步骤与预期结果。

本计划直接执行，不再要求用户选择执行方式。数据缺项按原协议降级；若发布门禁不能通过则保留失败与原因，不用假输出填充。


开发与自测完成，用户验收待进行；真实运行和最终测试证据见 [本阶段结果](2026-10-13-alphaagent-method-knowledge-routing-results.md)。原计划 P5 尚未开始。
