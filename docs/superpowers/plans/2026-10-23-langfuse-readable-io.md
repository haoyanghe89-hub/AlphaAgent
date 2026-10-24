# Langfuse 可读输入输出实施计划

> 用户已批准六步方案并要求开始。在当前会话按 executing-plans 顺序执行，不另询执行方式，不创建子任务或提交代码。

**Goal:** 在真实 Langfuse Preview 中直接阅读本次投研的资料、知识、角色输入、模型响应、校验原因及交付。

**Architecture:** Metadata 保持身份白名单；显式 opt-in 的公共投研 Observation 使用版本化内容策略，应用与 SDK 导出两层同时脱敏并限制大小。阶段适配器只读取原调用参数、返回值及已归档公开来源，不改变业务结果或模型请求。模型端口以实际 messages/response 生成内容，其他工作流默认保留原策略。

**Tech Stack:** Python 3.13、现有 Langfuse 5.0.4、OTel、HTTPX、SQLite、pytest。

**Spec:** 已批准的会话六步方案及 ../specs/2026-10-23-alphaagent-evaluation-system-design.md 第 9.3、9.6 节。

## Global Constraints

- 保留历史报告、用户已有行情面板改动、业务预算与发布门禁。
- 不上传资金、持仓、股数、成本、凭据或私有异常原文；不记录模型私有思维链。
- 只在经过审查的公共投研边界启用内容策略；未知流程和直接 SDK 调用保持严格默认。
- 明确区分实际模型输入、格式化展示、候选资料与最终引用。截断和脱敏必须可见并保留原归档引用。
- 旧 Trace 保留原貌；新任务验收，不补造历史节点或重复计费。
- 观测构建失败不能改变业务执行。投递仍使用原 span ID 和 outbox 租约。

## Task 1：版本化公共内容策略

Files: telemetry/content.py（新）；telemetry/{tracing,privacy}.py；tests/unit/observability/test_content.py（新）。

接口：`PUBLIC_CONTENT_POLICY`；`public_payload(value) -> (value, diagnostics)`；`observation(..., content_policy=None)`。元数据继续使用 projection；内容策略只处理 opt-in 节点。保留文本与标准 role/content 消息列表，递归去除私人字段及凭据，限制深度、列表与总字符数并标注截断。

- [x] 先添加测试并确认未实现失败：

```python
with observation('public', content_policy=PUBLIC_CONTENT_POLICY, input=[{'role':'user','content':'公开问题'}]):
    pass
assert '公开问题' in decoded_export
assert 'PRIVATE-CAPITAL' not in decoded_export
```

- [x] 实现内容策略及应用/导出两层一致处理；保持现有默认隐私测试通过。
- [x] 测试文本、嵌套 JSON、消息、凭据、账户、截断及直接 SDK 绕过。

## Task 2：公共业务阶段与根节点

Files: telemetry/investment.py（新）；telemetry/tracing.py；quant/knowledge/{context,research_context,rule_retrieval,research_agents,research_service}.py。

接口：`stage(..., inputs=None, outputs=None, content_policy=None)`，回调参数为绑定后的 arguments，output 回调另接 result。适配器失败记录观测缺口并继续原业务。外部采集输出来源、日期、记录数量、样本及原文引用；assemble 输出实际解析资料；知识快照输出规则正文和文档定位；上下文输出实际保留材料；根节点输出公共报告摘要及主张证据。确定性发布使用 span，不创建 generation。

- [x] 测试阶段适配器不能更改返回值或增加业务调用。
- [x] 补齐外部资料、知识快照、模型上下文、校验和公共交付的内容适配器。
- [x] 用原有合成投研服务执行，断言知识正文、证据内容与角色上下文均在真实 OTLP，私有 profile 不在。

## Task 3：模型内容与可靠投递

Files: telemetry/{model,outbox,config}.py；quant/decision/next_session/agents.py；tests/unit/observability/{test_calls,test_runtime}.py。

- [x] 仅公共 investment-research 合同发布实际 messages 和结构化响应；普通包装器仍禁止任意原始提示出站。
- [x] `output.validate` 记录受控校验输入、结果和具体原因；保留每次 generation 与原调用索引。
- [x] Outbox 使用可复用 HTTPX 连接；网络超时可配置，租约覆盖超时，有界 flush；错误计数区分本地导出与投递失败。
- [x] 测试实际调用不增加、用量不双计、失败重试、连接复用和关闭顺序。

## Task 4：回归、激活与真实页面验收

- [x] 运行 observability、投研服务及相关业务回归，Ruff、编译、diff 检查。
- [x] 空闲时沿用原数据库重启；比较既有记录与用户改动哈希。
- [x] 创建一条新海康威视任务，使用原页面默认合同和预算；不得覆盖旧任务。
- [x] 回读 Cloud 对账所有实际调用与用量，确认采集、知识、模型、校验和根节点 Input/Output 内容。
- [ ] 实际检查 Cloud Preview 渲染（内置浏览器访问 Cloud 两次超时；API 内容对账及本地报告页面已通过）。
- [x] 保存证据、更新接入文档，明确本轮仍未实施的质量评测；不提交或推送。
