# AlphaAgent Langfuse 接入与运行

这是第一阶段的可观测性基础接入。Langfuse 管理执行链路、Dataset 和 Scores；SQLite/CAS 继续保存业务真相、调用原文和私有账户资料。当前使用 `langfuse==5.0.4`，SDK 通过独立 OTel provider 创建真实 Observation，并由本地持久化 outbox 投递。

## 配置与启用

安装项目锁定依赖，保留已有 extra：

```powershell
uv sync --locked --extra dev --extra web --extra server --extra quant
```

在本地 `.env` 配置项目页面提供的三个值：

```dotenv
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_TRACING_ENVIRONMENT=development
ALPHAAGENT_TRACE_JOURNAL=data/observability/traces.sqlite3
ALPHAAGENT_TRACE_TIMEOUT_SECONDS=10
```

公钥、私钥和地区应属于同一项目。欧洲地址如上；其他地区照项目设置填写。两个 Key 均填写后自动启用，缺省无 Key 时不创建观测数据库或连接 Cloud。`ALPHAAGENT_OBSERVABILITY_ENABLED=false` 可在重启后停用；显式设 true 且不填 Key 时只生成本地待投递记录。

已有后台进程需要在安全空闲时重启才能读取新代码和配置；重复运行启动检查脚本会复用仍在运行的服务，并不会替它重新加载模块。沿用 `alphaagent.local.json` 和原 SQLite/CAS，不创建新业务库替代历史。

## 记录哪些链路

投资研究、明日预测、定向研究、投资计划、持仓顾问、账户调仓、问题理解、盘前简报和评测运行都有各自业务根 agent。上下文、知识/证据检索、实际角色、每次模型请求、工具及 HTTP attempt、校验和本地计算按真实调用嵌套。

- 短 HTTP 请求与后台业务使用各自真实生命周期，通过 request_trace_id 关联；后台业务根节点无虚假父节点，不在 HTTP 202 返回时结束。
- asyncio 并行和 to_thread 继承活动上下文；并行子步骤在同一父节点下记录。
- 模型请求只在统一 port 边界创建一次 generation。供应商没有提供的 token 明细和 TTFT 保留缺失；当前非流式适配器不能提供真实 TTFT。
- 结构校验发生在 generation 之后，失败保留；后续纠错创建新请求节点。
- 复用检查点不重新生成 generation，不重复计算 token 或费用。
- 同进程自动继续沿用活动 Trace。进程重启后创建新执行段，保留 run_id，并用 resumed_from_trace_id 关联旧段。
- 模型和工具发出前先在本地独立日志登记 call 与 observation 的映射；强杀时可能没有完成 span，但可以识别未结束调用，不伪造结束时间或零丢失保证。

旧历史不会被重写成一份看似当时就有的 Langfuse Trace。

## 隐私与数据范围

默认工作流采用结构化摘要和引用模式。公开投资研究（investment-research-v1 至 v4）在经过审查的阶段显式启用 `alphaagent-public-investment-v1` 内容策略：外部资料正文摘要、知识规则及文档定位、角色上下文、实际 system/user 消息、结构化模型响应、校验结果和公共交付可直接在 Preview 的 Input/Output 阅读。账户报告和用户自由分析文本不上传。

建议依次查看业务根节点 → `external_context.collect` → `knowledge.snapshot` → `context.analyst_pack` → 各角色下的 `model.call` → `output.validate` → `publication.render` → 根节点 Output。采集与知识快照是候选材料；上下文表示交给角色的内容；最终交付列出实际引用的方法、主张和证据 ID。不要把“加载了”当作“使用了”。

外部资料是已归档原文的解析展示，包含来源 URL、参数、获取时间及 `blob://sha256/...` 定位；长分钟序列展示样本并标出数量。模型 Input 保持实际消息形状，Output 是实际解析响应，不包含私有思维链。单个 I/O 上限约六万字符，超限有可见截断标记及 metadata 诊断，完整内容仍保留在本地 CAS。旧 Trace 不会补写内容。

资金、持仓、股数、成本和密钥不进入 Langfuse。Metadata 保持白名单投影；公共内容递归去除私有字段与凭据，应用构建时和 SDK 导出时各执行一次。已知校验错误显示受控原因，未知异常仅记录类别，不上传包含用户值的异常消息和堆栈。内容构建失败标为 OBSERVABILITY_GAP，不改变业务结果。

业务完成只生成 `delivery_success` 机制分数，不代表答案正确、预测准确或投资收益。需要完整证据的语义 Judge 应在本地读取 CAS 后判分，再把分数及受控证据引用挂回 Trace；本阶段未配置 Cloud 端直接读取私人资料的 Judge。

### 分析正文的数字引用

2026-10-24 起创建的投资研究任务启用 `evidence-numbers-v1`。程序从封存证据生成 `numeric_quotes`，随 Analyst 的实际上下文发送。模型可逐字引用本条 `evidence_ids` 对应的指标、数值、单位与窗口表达；摘要限于本证券判断已经引用的事实，失效条件与后续检查限于原路线窗口。不能因为其他股票或其他指标中恰好出现同一个数值就放行。

例如有相应引用时，“近20日相对沪深300表现偏强”可以通过；把窗口改成未提供的“25日”、把价格单位改成百分比、改变资金净额正负或挪作新目标价，仍会拒绝。百分比展示由程序用 Decimal 生成固定精度表达，模型不能自行换算、计算或调整精度。未支持的表达保留定性措辞；概率、收益承诺和新交易参数继续受限。

规则版本同时封存在任务 config 与公共上下文中，历史任务继续使用原提示词和原数字门禁，旧 Trace 的错误记录保持原貌。这里校验的是数字引用的来源与格式，因果、比较关系和方向判断仍需原有 Verifier 核查。离线验收记录位于 `.phase3-quant-o/evidence-numbers-20261024/`，未为此次修复新增真实模型调用。

## 运行检查

```powershell
python -m marketpulse.telemetry.cli check
python -m marketpulse.telemetry.cli status
python -m marketpulse.telemetry.cli flush
```

HTTP 开发者接口：

- `GET /api/quant/observability/status`：是否启用、凭据是否齐全、待投递批次/字节、丢弃、本地导出错误及持久化投递错误次数。
- `GET /api/quant/observability/runs/<run_id>`：业务运行的各执行段、父运行、Trace ID 和可取得的 Langfuse 链接。
- `POST /api/quant/observability/flush`：经过现有本机写操作检查后触发有界补传，不执行模型或重跑业务。

合成 Trace 验收不调用模型，上传根 agent 与两个并行本地工具；随后从 Cloud 回读 Observation 并检查父子关系：

```powershell
python -m marketpulse.telemetry.cli smoke --output work/langfuse-smoke.json
```

`model-smoke` 会真实调用一次当前配置的 DeepSeek，提示仅要求返回 OK，最多 128 输出 token、不重试、不进行投资判断。结果另报实际供应商用量。这是一条诊断调用，不是业务质量评测。

```powershell
python -m marketpulse.telemetry.cli model-smoke --output work/langfuse-model-smoke.json
```

Cloud 的接收确认与可查询存在时间差。OTLP 端点可能返回 protobuf 或异步队列 JSON 确认；Scores 创建可能返回 HTTP 202。验收等待有截止时间，不能把 HTTP 成功直接当作页面完整。

## 可靠投递与恢复

SDK 后台批次先写 SQLite，再由独立线程使用可复用 HTTPX 连接发送。每个目标地址/项目使用独立作用域。连接/读写等阶段超时默认各 10 秒，可用 `ALPHAAGENT_TRACE_TIMEOUT_SECONDS` 在大于 0 至 10 秒内配置；租约覆盖请求各阶段，单轮 drain 有批次数及启动时限约束，最后一个请求可能继续到其超时结束。失败采用退避，已接收批次清空 payload 并保留去重身份；重新投递保留原 span ID，不重新请求模型或来源。

本地待投递上限为 10,000 批次或 128 MiB；达到限制会报告丢弃，不能承诺无限缓冲。网络 ACK 不明时采用至少一次投递，需按 span ID 去重，不能宣称分布式 exactly-once。进程退出会做有界 flush；强杀仍可能损失尚未导出的 SDK 内存 span，业务调用和本地映射用于发现缺口。

Langfuse 故障不改变研究状态、预算或发布门禁。维护时应将 `data/observability/` 的 SQLite/WAL 按 SQLite 一致性备份方法纳入备份，不能只复制活动库的单个 sqlite 文件。本轮没有修改既有业务备份任务。

## 评测数据集的当前边界

```powershell
python -m marketpulse.telemetry.cli seed-regression --output work/langfuse-dataset.json
```

该命令登记原有 B01–B20 合成候选案例，ID 绑定集合内容哈希，Expected Output 与待测 Input 分开。数据集名为 `alphaagent-core-regression-v1`。其标签来源是原有 AI 方法审核，均明确标为 `REGISTERED_NOT_EXECUTED` 和 `NOT_COMPLETED` 人工校准状态。

重复登记会保留已有同版本条目和人工修订；版本不一致时要求另建版本，拒绝混写。

这 20 条不计为已通过的 Agent 评测，也不计为真实市场样本。120 例业务评测集、80 条人工 Judge 校准和多日预测效果仍按设计后续阶段实施；不能因为接入成功而宣称它们已经完成。

## 本轮证据

公开投研输入输出的新增验收见 [可读内容与海康威视完整任务](alphaagent-langfuse-readable-io-2026-10-23.md)，包含新 Trace 链接、阅读顺序、Cloud 内容对账及页面验收限制。

验收文件已保存在主工作区 `.phase3-quant-o/langfuse-20261023/`。`primary-cloud-smoke.json` 记录主工作区零模型的服务端回读，`real-model.json` 记录一次真实 DeepSeek 诊断，`live-http-readback.json` 记录重启后实际服务发出的 HTTP Trace。正式回归结果、历史保护和当前验收边界见 [本轮交付记录](alphaagent-langfuse-acceptance-2026-10-23.md)。
