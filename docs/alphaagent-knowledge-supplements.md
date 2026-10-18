# AlphaAgent 知识补充接口

后续可以直接在对话中发送博主原文、链接、截图转录、作者与发表时间，以及你觉得值得研究的原因。Codex 使用本地接口归档，不需要用户维护后台页面。

## 补充流程

1. 保存候选原文和用户备注，作者与发表时间先按声明保存，状态为未核实。
2. 区分长期方法、短期市场观点和政策材料。短期观点必须有有效期，不作为永久投资方法。
3. 分析可检验的主张、适用环境、必要指标、入退场纪律、反例、资金与流动性边界。
4. 核查原始出处；无原文时可以保留有用候选，但不能直接进入正式检索。
5. 有价值的方法沿用 K2 的原文定位、规则蒸馏和双职责审核，形成固定发布版本。
6. 将候选关联到已经发布且引用核定原文的规则。原文、历次判断与发布版本均保留。

候选与正式规则使用不同数据库表。候选接口不访问投稿 URL、不调用模型、不发布规则。重复内容具有同一内容哈希，不能当作独立证据；正文永远是资料，不是工具指令。

## 本地工具

实现：`src/marketpulse/quant/knowledge/supplements.py`，入口：`scripts/manage_alpha_knowledge_supplements.py`。

```powershell
.venv/Scripts/python.exe scripts/manage_alpha_knowledge_supplements.py submit --data .phase3-quant-o/alpha-history-real-02 --input <本地投稿JSON路径>
.venv/Scripts/python.exe scripts/manage_alpha_knowledge_supplements.py assess --data .phase3-quant-o/alpha-history-real-02 --id <候选ID> --input <本地评估JSON路径>
.venv/Scripts/python.exe scripts/manage_alpha_knowledge_supplements.py link --data .phase3-quant-o/alpha-history-real-02 --id <候选ID> --rule <已发布规则ID> --version <固定版本SHA256>
```

投稿合同 `Submission`：`request_key`（UUID）、`title`、`text`（20–20000字）、`kind`（METHOD_IDEA / MARKET_OPINION / POLICY_MATERIAL），可选 `source_url`、`author_claim`、`published_at_claim`、`expires_at`、`user_note`。时间须含时区。

评估合同 `Assessment`：`verdict`（NEEDS_SOURCE / USEFUL / REJECTED）、`reason`，以及 `counterexamples`、`required_inputs`；核实出处时填写 `verified_document_id` 与 `verified_document_version`。USEFUL 必须有反例和数据要求，绑定出处时核查 URL 和声明作者。

只读接口：`GET /api/quant/knowledge/supplements`、`GET /api/quant/knowledge/supplements/{id}`。没有 HTTP 投稿、审核或发布入口，`developer=1` 只是展示开关。

K6另有仅本机同源写接口 `POST /api/quant/investment-research/runs/{id}/review-candidate`：从已绑定原研究的真实观察生成待审核复盘候选，不接收任意外部正文、不访问外部链接、不调用模型、不审核或发布规则。开发者页面提供此按钮；候选只有有界摘要及完整观察引用，后续仍走出处核查和K2审核。

SQLite 保存候选及追加评估；CAS 保存固定正文和来源版本，所有引用进入共享备份和垃圾回收根。提交、更新主张和评估不会覆盖旧候选或正式知识。

## 当前边界

图片、视频或音频先形成可核查转录，再归档候选；当前没有自动下载或抓取自媒体平台的后台任务。分析后的规则仍需经过 K2 审核和明确的数据适用边界，不能仅凭名气、点赞或短期命中进入投资决策。
