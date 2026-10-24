# Langfuse 可读输入输出验收 · 2026-10-23

已激活到原 AlphaAgent 本地服务，沿用 `.phase3-quant-o/alpha-history-real-02`。本轮为公开投资研究补齐内容观测；不表示业务质量评测、人工 Judge 校准或投资效果评估已经完成。

## 实际任务

- 新任务：`RUN-IR-bfb3d0ffe6db450aa14178d7607cf5b8`。
- [本地海康威视报告](http://127.0.0.1:8839/#/investment-research/RUN-IR-bfb3d0ffe6db450aa14178d7607cf5b8)。
- [Langfuse Trace](https://cloud.langfuse.com/project/cmvkg7msf0393ad0jiipdgfg9/traces/cccdc877d86d84c05d1c6fbf82fc32b5)。
- 使用页面标准研究默认合同：investment-research-v4、20 个交易日、investment-structured-v2、无私有 profile，原有 24 次模型调用／60,000 token／900 秒预算。
- 任务 COMPLETED，约 22 秒，4 次实际模型调用；输入 14,300、输出 1,251，共 15,551 token。
- 15 个逻辑工具节点、16 次实际来源请求（一次来源连接超时后重试），加 4 次模型调用；20 条本地调用映射均能定位 Cloud 节点。
- Cloud 回读 73 个 Observation、一个根节点，无孤立节点、无缺失调用映射，全部用量一致，无 OBSERVABILITY_GAP。

## 阅读顺序

打开上面的新 Trace，选节点的 **Preview → Input / Output**。Attributes 继续用于身份、版本和统计信息。

1. `alphaagent.investment_research`：Input 是本次公开任务参数；Output 是交付摘要、最终方法引用、支持观点／反证与证据 ID。
2. `external_context.collect`：实际归档来源的解析展示，包含主营业务、日频资金、分钟资金、政策条款、数据日期、URL、参数和原文引用。本次 Output 为 7,237 字符；长分钟序列明确抽样，不伪装成完整原始响应。
3. `knowledge.snapshot`：本次加载的 8 条方法规则、原文摘录、执行规则、适配说明、反例、文档版本和字符定位。本次 Output 为 8,449 字符。
4. `context.analyst_pack`／`context.verifier_pack`：实际整理后的角色上下文，包括保留的方法、证据正文及窗口。它们与知识候选集合分别记录。
5. 各角色下的 `model.call`：实际 system/user 消息及解析后的 assistant 结构化响应，附真实 token 用量和本地调用归档引用。没有记录私有思维链。
6. `output.validate` 与具体校验节点：通过结果或受控的失败原因。本次分析曾因正文含未绑定数字等约束而失败，纠错后的另一次 generation 与校验均保留。
7. `publication.render`：标准合同的本地确定性发布，输入为已批准的公共内容，输出为发布结构，不计作 generation。

“知识加载”“送入模型”“最终引用”并不等价。本次最终方法引用为 KR-TREND-ENTRY-DISCIPLINE、KR-TREND-MARKET、KR-GROWTH-RETURN，可沿主张的 evidence_ids 回查角色输入和原文归档。

## 验证与保护

- 隔离工作树完整量化／观测回归：798 passed，1 条已有 Starlette/AnyIO 弃用提示。
- 最终观测及真实投研代码专项：33 passed；Ruff 和 diff 检查通过，主工作区编译通过。
- 激活前后 1,244 条既有投研记录 payload 哈希一致；8 个受保护文件哈希一致，包括用户已有行情修改。
- 两层隐私策略测试覆盖普通文本、消息中的嵌套 JSON、凭据、私有账户字段、截断、直接 SDK 调用和默认严格策略。公开投资研究 opt-in，未知工作流保持原摘要模式。
- Outbox 改用持久 HTTPX 连接，超时可配置，租约覆盖请求阶段，投递错误与本地导出错误分开计数，退出顺序先完成最终 drain 再关闭连接。

验收脚本及 JSON 证据保存在 `.phase3-quant-o/langfuse-readable-io-20261023/`，特别是 `live-result.json`、`live-traces.json`、`cloud-observations.json`、`cloud-verification.json` 和 `regression.txt`。本次新 Trace 的完整内容由正常后台自动投递，无手动替换 transport，也未为补传重跑模型。

## 验收限制

内置浏览器两次访问 Langfuse Cloud 超时，因此未完成 Preview 的目视渲染验收；实际 Input/Output 是从 Cloud Observations API 回读确认。本地新报告页面已检查。回读时 outbox 另有 10 批旧记录因 ConnectTimeout 仍在重试，它们没有造成本次业务 Trace 缺口；网络投递仍为至少一次语义。

旧 Trace 保持原貌。资金、持仓、股数、成本、账户报告和用户自由分析文本仍不上传；原始全量资料保留在本地 CAS，Cloud 的公开内容有大小限制和显式截断标记。
