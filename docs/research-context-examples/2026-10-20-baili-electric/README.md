# 百利电气：一次已完成研究的真实模型上下文

这是本地完成调用档案的只读导出，不是按当前代码重新拼接的示例，也不是新增模型运行。只新增本阅读版、四份JSON和校验清单，业务代码、原研究和正式档案不改。

## 研究身份

- 股票：百利电气，600468.SH；标准投资研究，20个交易日。
- 任务：`RUN-IR-0bbb76ffce754d44847ce8dc76b08807`；状态：`COMPLETED`；模型：`deepseek-flash`。
- 任务创建：2026-10-20T10:04:50.103599+08:00；Planner调用：2026-10-20T10:05:03.684689+08:00；Analyst调用：2026-10-20T10:05:04.986836+08:00。
- 研究日线日期：`20261019`；上下文另含当时的分钟资料时间，不能把不同频率混作同一时刻，也不是现在的实时行情。
- [原研究页面](http://127.0.0.1:8839/#/investment-research/RUN-IR-0bbb76ffce754d44847ce8dc76b08807)；[本机只读审计接口](http://127.0.0.1:8839/api/quant/investment-research/runs/RUN-IR-0bbb76ffce754d44847ce8dc76b08807/audit)。审计接口与本阅读版用途不同，不必为查看材料重新发起研究。

## 先看这张位置表

JSON本体没有人为插入注释或额外解释字段。下列标注是阅读说明，不是模型收到的额外内容。

| 实际位置 | Planner | Analyst | 含义 |
| --- | --- | --- | --- |
| `messages[0].content` | 有 | 有 | 该角色当时真正收到的system提示词 |
| `messages[1].content` | 有 | 有 | 实际user消息字符串；本例是序列化后的context，没有纠错尾注 |
| `context.methods` | 8条 | 8条 | **知识库投资方法**：规则ID、标题、系统规则、反例及本例发送的原文片段 |
| `context.stocks` | 1只 | 1只 | **股票概况与程序结果**：身份、行业、市场状态、准入路线和已计算条件 |
| `context.evidence` | **不存在此字段** | 20条 | **股票事实证据**：行情、财务、技术、资金或政策等本次实际取得的证据；完整清单以JSON为准 |
| `context.windows` | 不存在此字段 | 11组 | 证据时间窗口字典，例如证据中的W3/W4指向这里 |
| `context.plan` | 不存在此字段 | 有 | Planner的真实输出，传给Analyst作为路线与核查重点 |
| `context.citation_requirements` | 不存在此字段 | 有 | 程序给出的实际可用引用类别与ID，不能当成额外事实 |
| `context.review_feedback` | 不存在此字段 | 本例为空 | 本例选最终获准的Analyst-0；没有把被拒绝草稿当成最终分析 |

**实际看到的区别：Planner这次拿到知识方法和股票摘要，没有收到Analyst那20条完整证据。Analyst在知识方法之外，还收到事实、时间窗口和规划结果。** 两者都不是只收到一个“分析这只股票”的空提示词。

## Planner实际收到什么

完整上下文：[planner.context.json](planner.context.json)。`methods`从第9行开始，`stocks`从第83行开始。

实际system/user两条消息：[planner.messages.json](planner.messages.json)。请注意user的content是JSON字符串；context文件只是把这个对象展开排版，便于阅读，没有重写内容。

下面只摘录部分真实字段，省略的知识方法和路线条件见完整JSON。没有用虚构数值替换真实股票数据：

```json
{
  "horizon": "SWING",
  "horizon_sessions": 20,
  "stocks": [
    {
      "security_id": "600468.SH",
      "name": "百利电气",
      "industry": "电力设备",
      "market_regime": "DOWNTREND",
      "eligible_routes": [
        "TREND_SWING"
      ]
    }
  ]
}
```

`stocks[].routes[].conditions[].passed`是程序先算出的条件状态，不是Planner临时计算的结果。Planner基于已提供候选选择路线；本例实际仅趋势波段准入，条件状态为等待。

## Analyst实际收到什么

完整上下文：[analyst.context.json](analyst.context.json)。`evidence`从第21行开始，`methods`从第170行开始，`plan`从第244行开始，`windows`从第339行开始。

实际system/user两条消息：[analyst.messages.json](analyst.messages.json)。归档中的Planner响应与Analyst的`plan`字段完全一致，已核对。

下面的摘录来自完整上下文：`methods`只展示其中一条的四个真实字段，`evidence`只展示两条真实证据，`windows`仅保留它们实际引用的两个窗口。片段不是完整模型请求。

```json
{
  "methods": [
    {
      "rule_id": "KR-TREND-MARKET",
      "title": "趋势入场先检查整体市场环境",
      "system_rule": "A股趋势研究先检查有真实数据的市场状态，再评估个股信号；不把IBD专有指标冒充本地已实现指标。",
      "counterexamples": [
        "市场总体转弱时个股突破可能失败；总体走强也不能替代个股条件核查。"
      ]
    }
  ],
  "evidence": [
    {
      "evidence_id": "F-600468SH-close",
      "unit": "CNY/share",
      "value": "5.6",
      "window": "W4"
    },
    {
      "evidence_id": "F-600468SH-cash_conversion",
      "unit": "ratio",
      "value": "2.326616455672320078430156612304899",
      "window": "W3"
    }
  ],
  "windows": {
    "W4": {
      "benchmark_id": "000300.SH",
      "end": "20261019",
      "return_sessions": 20,
      "start": "20261019"
    },
    "W3": "20260630"
  }
}
```

- `methods[].system_rule`告诉模型**应该怎样分析**；`counterexamples`限制不当推断。`quote`在完整JSON中保留，摘录没有展示。
- `evidence[].value`是**本次股票事实或程序派生指标**，不是模型生成的预测数字。`F-600468SH-close`的单位是每股人民币；`cash_conversion`是比值，不应当作未来收益率。
- `evidence[].window`需要展开`windows`读取。股价窗口与财报期间不同，不能只看数值就假设同期。
- `plan.selections`是Planner已返回的路线、理由和核查重点，属于上一步模型输出，不能与原始行情证据混为一类。
- 本例证据主要是结构化事实与指标，不能据此宣称Analyst逐页读完了公司整份财报。

## 本次实际进入两位模型上下文的8条规则

| 规则ID | 实际标题 |
| --- | --- |
| KR-AVOID-RUIN | 先排除难以恢复和难以逆转的损失 |
| KR-CAPACITY-LIQUIDITY | 用现金需求日期约束可承受的流动性 |
| KR-DCF-DISTRESS | 困境与周期企业需要调整持续经营假设 |
| KR-TREND-ENTRY-DISCIPLINE | 突破入场需要量价证据并防止追高 |
| KR-TREND-MARKET | 趋势入场先检查整体市场环境 |
| KR-EXPECTATION-EXIT | 退出理由同时考虑价值、替代机会和成本 |
| KR-GROWTH-RETURN | 增长要与增量资本回报一起判断 |
| KR-QUALITY-ECONOMICS | 先看企业经济特征，再看便宜程度 |

本例`methods`的实际字段为`counterexamples`、`quote`、`rule_id`、`system_rule`、`title`，没有额外插入adaptation或version字段。规则版本在本地knowledge快照中固定；对应版本关系见manifest，不冒充模型消息本身也包含这些元数据。

## 原始请求依据、脱敏与完整性

- Planner请求档案：`blob://sha256/dc89161aae936ae323c6e621c3b6a281fdea2edb9d53b699095ce57ddd4ba7ca`。
- Analyst请求档案：`blob://sha256/85405d67e00eda925142bed681d22475aa8ceff4d2f450ec05f1933cccd51aa0`。
- 两个请求均对应已完成模型调用及对应的检查点，读取CAS时逐个核对SHA-256；实际消息的system内容与归档instructions一致，user内容与context的规范序列化一致。
- 选用的任务原始profile为null。导出只包含这两位模型的context/messages，不复制账户表单、传输headers、供应商response ID或完整任务请求。
- 已检查私密字段和常见凭据形式，本例没有发现需替换的真实敏感值，实际替换次数为0。公共股票名称、代码、数据、日期和知识正文保留原值；提示词中“禁止输出股数”等约束文字不是用户的实际持仓。
- 四份JSON是UTF-8美化排版文件，文件字节哈希不同于整个原始请求的CAS哈希。这里同时记录原始请求引用与导出文件哈希，二者不能混用。
- [manifest.json](manifest.json)记录来源、角色、规则版本、字节/结构校验和导出文件哈希；没有新的模型费用。
