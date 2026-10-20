# AlphaAgent 知识库：实际内容与文件入口

> 导出时间：2026-10-20T09:23:23+08:00。本目录是当前知识的只读阅读副本；修改这些导出文件不会修改正式知识库。

## 先打开这几个文件

- [全部30条正式参考规则](rules.md)：来源主张、系统规则、适配、数据要求、条件、步骤、反例、禁止推断、参数和引用。
- [原文资料目录](sources/index.md)：逐项打开19份当前归档TXT，或查看原始出处、物理文件和固定版本。
- [实际定位的原文片段](source-excerpts.md)：38处定位片段的阅读汇总。
- [规则完整JSON](rules.json)和[资料目录JSON](catalog.json)：正式只读接口的原样结果。
- [历史资料版本目录](source-versions.json)：全部38个资料版本的引用，旧版正文继续保存在正式档案，导出不重复拷贝。
- [补充候选目录](candidate-index.md)：候选及其准入状态，与已发布规则分开。
- [统计口径与机器结果](inventory.json)。

## 里面有多少内容

- 不同资料：**19份**；历史资料版本：**38个**；来源族：**11个**。
- 当前归档规范正文：**516,668个Unicode字符**。含英文、中文、空白等，不是中文字数，也不是Token数；按19份当前资料各计一次，不累加历史版本。
- 已定位片段：**38处**；按同一资料内字符区间去重后约**17,291字符**。这是归档定位覆盖量，不是全部经过蒸馏、也不是每次都发送给模型的上下文。
- 已发布参考规则：**30条**；规则解释字段合计**9,612字符**。此处只累计来源主张、系统规则、适配、所需数据、条件、步骤、反例和禁止推断，不计引用原句、ID、版本、关键词、参数及元数据。
- 补充候选：**8条**；其中允许正式检索：**0条**。部分候选思想另经审核成为规则，不能重复计数。

不要把51万字符的归档量当作51万字符均已充分理解，更不能说已读完对应书籍。正式可调用的方法核心目前就是这30条有出处的参考规则；其状态仍为REFERENCE_ONLY／UNVALIDATED。

## 规则类型

- 风险：7条
- 筛选：8条
- 市场环境：3条
- 容量：3条
- 持有：3条
- 估值：3条
- 入场：2条
- 退出：1条

## 正式知识实际在哪里

- 元数据数据库：[metadata.sqlite](<D:/deepsearch/.phase3-quant-o/alpha-history-real-02/metadata.sqlite>)。这是整个应用共用的数据库，包含历史；不是只有知识资料。
- 原文与规则档案目录：`D:\deepsearch\.phase3-quant-o\alpha-history-real-02\blobs`。文件以SHA-256分目录保存，没有人类可读的书名文件名。
- 知识元数据表：`inv_quant_knowledge_document`、`inv_quant_knowledge_rule`、`inv_quant_knowledge_review`、`inv_quant_knowledge_publication`、`inv_quant_knowledge_audit`。
- 补充候选表：`inv_quant_knowledge_supplement`、`inv_quant_knowledge_supplement_decision`。
- [知识结构与调用说明](../../alphaagent-knowledge-and-request-flow.md)。

## 相关代码，和知识正文分开看

- [原文来源定义](../../../src/marketpulse/quant/knowledge/sources.py)。
- [结构化规则字段合同](../../../src/marketpulse/quant/knowledge/rules.py)。
- [规则存储、审核与发布](../../../src/marketpulse/quant/knowledge/rule_store.py)。
- [执行路线及条件](../../../src/marketpulse/quant/knowledge/routing.py)：四条目录，其中趋势波段、质量成长有计算实现。
- [定向政策原文入口](../../../src/marketpulse/quant/knowledge/context_policies.py)：政策按研究任务另行封存，不全包含在这19份方法资料中。
- [技术指标和解释边界](../../../src/marketpulse/quant/knowledge/technicals.py)：MACD、RSI、ATR等由程序计算，不是新增书籍规则。

本次导出逐一校验了正文哈希、字符数、片段位置、规则身份及知识表前后摘要。没有修改正式知识、应用功能或研究历史，没有调用外部模型。
