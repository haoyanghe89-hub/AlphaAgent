# Langfuse 接入实施记录

本计划承接已经批准的 [评测体系设计](../specs/2026-10-23-alphaagent-evaluation-system-design.md)。本轮范围是可运行的 Trace 基础接入和首批 Dataset 登记；不把它等同于设计第 9.7 节的全部生产验收或全部业务质量评测。

## 实现与验证

- [x] 固定 `langfuse==5.0.4`，使用独立 OTel provider、真实 SDK Observation 和持久化 OTLP outbox。
- [x] `.env` 自动启用、地区/URL 校验、默认关闭、无密钥日志、项目隔离、容量上限和补传。
- [x] 投研、明日预测、定向研究、计划、持仓顾问、调仓、问题理解、简报及评测服务根节点。
- [x] 实际上下文、词法检索、角色、模型请求、工具请求、HTTP attempt、校验和本地计算节点。
- [x] HTTP 请求和后台任务独立生命周期，异步/线程传播、嵌套会话、恢复执行段关联。
- [x] 模型/工具调用前登记本地映射，保留未知结果，不重复调用或虚报用量。
- [x] 构建及导出双层字段过滤，去除自动异常事件内可能包含的私有内容。
- [x] Dataset 的 Input 与 Expected Output 分离；重复登记保留同版本已有条目。
- [x] Scores 明确区分执行成功和答案质量，缺失结果使用显式类别。
- [x] 提供 CLI check/status/flush/smoke/model-smoke/seed-regression 和本地状态/Trace 查询接口。
- [x] 真实 Cloud 回读零模型 Trace、一次真实 DeepSeek 诊断及 20 条候选案例。
- [x] 隔离工作区量化全量回归 1,030 项通过；最终专项 19 项通过；Ruff 和 diff 检查通过。
- [x] 同材料局部检索开销测量：100 次样本，中位数增加约 0.56ms；不代表端到端生产 SLO。
- [x] 合入主工作区前逐文件检查冲突、备份源码，保留用户已有行情面板改动。
- [x] 主工作区全量回归 1,045 项通过；收尾修复后的专项 20 项通过。
- [x] 确认当前实例空闲，沿用原库重启；实际服务 HTTP Trace 已在 Cloud 回读，1,142 条历史记录哈希一致。

## 实际接口

代码位于 `src/marketpulse/telemetry/`，避免改动已有 `marketpulse/observability.py` 旧日志接口。核心接口为 `configure()`、`observation()`、`@workflow`、`@stage`、`ObservedModelPort`、`runtime.score()`、`runtime.outbox.status()/drain()`。测试位于 `tests/unit/observability/`，真实投研服务的合成执行验收位于 `tests/unit/quant/knowledge/test_langfuse_workflow.py`。

实施用工作区为 `codex/langfuse-observability`；合入前与基线逐文件对比。配置和账号密钥仅留在主工作区 `.env`。未提交、推送或修改既有业务预算。

## 后续设计验收仍需完成

- [ ] 六类业务各至少一条真实完整执行的 Cloud 验收，以及强制进程重启恢复验收。
- [ ] 20 例完整行为断言、离线/审计 baseline 与逐例失败分类；当前 Dataset 登记不计为执行。
- [ ] 120 例跨业务集合、80 条人工 Judge 校准及配对 Experiment。
- [ ] 多日效果证据、完整性长期监控和端到端负载下的开销审查。

最终命令、结果、可点击 Trace 及当前实例状态见 [本轮交付记录](../../alphaagent-langfuse-acceptance-2026-10-23.md)。
