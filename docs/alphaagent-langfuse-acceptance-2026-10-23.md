# AlphaAgent Langfuse 接入交付记录

日期：2026-10-23，Asia/Shanghai。

Langfuse 基础接入已经应用到 `D:/deepsearch` 并在当前本地实例启用。它提供真实 SDK span、业务运行映射、模型/工具边界、持久化投递、隐私过滤和 Dataset/Scores 接口。**本轮不代表整套评测体系或设计第 9.7 节全部生产验收已经完成。**

## 当前实例

- 服务地址：`http://127.0.0.1:8839/`，健康检查 HTTP 200。
- Langfuse：`enabled=true`、`configured=true`；实际服务 Cloud 回读后 `pending=0`、`export_errors=0`、`dropped=0`。
- 原每日运行功能保持启用，使用原 `alphaagent.local.json` 和 `.phase3-quant-o/alpha-history-real-02/metadata.sqlite`，没有创建替代历史的新业务库。
- 重启前核对准确服务进程、数据目录及无活动任务。重启后原 1,142 条业务记录逐条 payload 哈希一致。
- 用户原有行情面板源码与测试改动哈希一致；`.env` 中用户填写的密钥没有被覆盖或输出。

## 可点击的真实 Cloud 证据

1. [主工作区合成并行链路](https://cloud.langfuse.com/project/cmvkg7msf0393ad0jiipdgfg9/traces/adb2533779a7fa8d2950ebe10c48029e)：1 个根 agent、2 个并行本地工具，回读 3 个 Observation，父子关系通过；零模型调用。
2. [真实 DeepSeek 诊断调用](https://cloud.langfuse.com/project/cmvkg7msf0393ad0jiipdgfg9/traces/c6e480091d91da83eb32ff5f4a344832)：回读 4 个 Observation，包含真实 generation 和独立校验节点；供应商返回输入 95、输出 5 token。只要求返回 OK，没有执行投资判断。
3. [当前服务 HTTP Trace](https://cloud.langfuse.com/project/cmvkg7msf0393ad0jiipdgfg9/traces/5f35bf855d3eb1ee96cb4ec282f589a3)：来自重启后实际服务的补传接口，HTTP 响应携带 Trace ID；Cloud 回读到无虚假父节点的 `http.request`。

证据来自服务端 API 回读，没有声称已人工浏览全部 Cloud 页面。原始验收 JSON 保存在 `.phase3-quant-o/langfuse-20261023/`；开发诊断 SQLite 台账通过一致性备份保存在其中的 `development-journals/`。

## 已接入的代码边界

业务服务入口覆盖投资研究、明日预测、定向研究、计划、持仓顾问、账户调仓、问题理解、盘前简报及已有评测执行。真实代码中的上下文采集、词法检索阶段、角色、模型请求、工具和 HTTP attempt、输出校验及本地计算按调用关系嵌套。

HTTP 请求和后台业务有独立生命周期，通过 `request_trace_id` 关联；恢复执行保留业务运行身份并创建关联执行段。模型/工具调用前落地 observation 映射；已复用检查点不会伪造新的 generation。

出站内容采用结构化摘要与引用。原始提示、完整模型输出、自由文本和账户值继续保存在本地；白名单投影及 SDK 导出过滤共同生效。非流式模型适配器没有真实 TTFT；供应商缺少的用量不填零。交付完成分数不代表答案正确或收益有效。

## 验证命令和结果

主工作区量化全量回归，测试进程显式关闭真实 Cloud 上传：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH='src'
$env:ALPHAAGENT_OBSERVABILITY_ENABLED='false'
python -m pytest tests/unit/observability tests/unit/quant tests/integration/quant -q -p no:cacheprovider --basetemp .pytest-langfuse-primary-final
```

结果：**1,045 passed，1 warning，552.89s，退出码 0**。警告来自现有 Starlette/AnyIO 弃用接口。

收尾发现并修复了诊断命令的链接查询超时错误：链接查询失败会保留已回读的验收结果，不让它整体退出；同时移除无活动 span 时不必要的 SDK 查询日志。修复后专项命令：

```powershell
python -m pytest tests/unit/observability tests/unit/quant/knowledge/test_langfuse_workflow.py -q -p no:cacheprovider --basetemp .pytest-langfuse-final-timeout
```

结果：**20 passed，1 warning，9.93s，退出码 0**。专项使用真实 SDK/OTLP 和本地接收器，覆盖异步、线程、恢复、失败补传、隐私、模型调用身份、数据集隔离及真实投研服务的合成执行；不把这些软件测试算作业务质量样本。

所有本轮 Python 文件 Ruff 检查、模块编译和 `git diff --check` 通过。`uv pip check` 确认 123 个已安装包兼容；实际版本为 Langfuse 5.0.4、OTel API 1.45.1、googleapis-common-protos 1.75.5。

局部开销测量使用同一合成检索材料，各取 100 次样本：关闭时中位数 0.0242ms，启用时 0.5829ms，增加 0.5587ms。这只测量局部检索与本地 SDK/outbox，不作为端到端生产 P95 或负载验收结论。

## 评测集状态和剩余验收

Cloud 已登记 `alphaagent-core-regression-v1` 的 B01–B20 候选案例。集合版本为 `597f82bcf714bc0be9168fc211aa8379bb63f7c629719babea0dd8a2b8987861`。重复登记回读 20 条、新建 0 条，不覆盖同版本已有条目和人工修订。Input 与 Expected Output 独立保存。

这 20 条仍标为 `REGISTERED_NOT_EXECUTED`，人工校准为 `NOT_COMPLETED`。以下工作不能计为本轮已完成：

- 20 例完整行为断言、离线/审计 baseline 和逐例失败分类。
- 六类业务各一条真实完整 Cloud 执行，以及强制进程重启后的业务恢复验收。
- 120 例跨功能业务集合、80 条人工 Judge 校准、配对 Experiment 和长期预测有效性。
- 端到端负载、长期缺失率与开销 SLO 验收。

投递提供有界持久化与至少一次重试；强杀可能丢失 SDK 内存中尚未导出的 span，网络确认不明也可能重投。没有宣称零丢失或 exactly-once。本轮没有调整业务预算、自动交易、创建新的调度任务或推送代码。

运行配置、CLI、状态接口及恢复操作见 [接入说明](alphaagent-langfuse.md)；完整目标见 [评测体系设计](superpowers/specs/2026-10-23-alphaagent-evaluation-system-design.md)。
