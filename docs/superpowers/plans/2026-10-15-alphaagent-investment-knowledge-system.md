# AlphaAgent 跨行业投资知识与投资路线 Implementation Plan

> For agentic workers: 用户审阅本方案并明确授权开发后，使用 executing-plans 在当前会话内按任务执行；未得到用户另行授权不得派生子 Agent。全部复选项表示计划工作，不表示已经完成。

**Goal:** 将有出处的方法知识转成可验证的规则和适配不同用户约束的跨行业投资路线，并接入现有工作台。

**Architecture:** 复用 SQLite、内容寻址原文归档和四角色研究流程，新增版本化知识模块与独立条件计划合同。先交付两个跨行业路线的真实闭环，再扩展事件政策、完整估值、因子与配置。

**Tech Stack:** 现有 Python／Pydantic／SQLAlchemy／SQLite／SourceClient／Vue 3／ECharts；首版不预置外部向量数据库或微调。

**Spec:** [已确认设计](../specs/2026-10-15-alphaagent-investment-knowledge-system-design.md)。2026-10-16 用户已授权依次开发；K1–K5已实现并分别自测交付；技术指标及K6首轮前瞻观察闭环已完成自测，用户亲验待进行，方法效果尚未验证。详见 [K6交付记录](2026-10-16-alphaagent-technicals-k6-results.md)。

## Global Constraints

- 市场：首版 A 股，方法来源可参考全球投资体系。
- 周期：首版波段与中期；后续扩展短线、长期配置。旧下一交易日预测实验继续独立运行。
- 2026-10-15 整理会话只交付方案；2026-10-16 已获开发授权，允许执行有界采集、代码、迁移和本地服务验收，不自动定时采集或交易。
- 不自动清理、覆盖既有工作区、提交或推送；不建新会话，不自动定时采集、通知或交易。
- 旧研究、预测和验证记录不可改写；旧 COMMON 的自由数字及买卖指令约束保留。
- 沿用原 5.2 正式回测数据准入及独立全收益核对；数据未过门禁时只报告功能、机制和前瞻观察，不发布“验证盈利”或胜率。
- 保留原 24 次模型调用、60,000 tokens、900 秒预算上限。
- 功能通过、计算正确、方法有效分别报告；不伪造实时、概率、策略收益或已阅读范围。
- 每个交付阶段先自测、再给用户页面、操作及预期结果；不等整个知识系统完成才交付。

## 任务边界与顺序

K1 版本化知识库 → K2 规则蒸馏与审核 → K3 跨行业路线及资金约束 → K4 工作台闭环 → K5 事件政策与市场资金扩展 → K6 前瞻复盘和方法迭代。

K1–K4 是首个可用增量，K5、K6 是可独立验收的后续增量。完整因子组合、ETF 配置、短线和长期投资分别制定后续子计划，不混入首个增量的完成承诺。

2026-10-17 用户进一步授权验证并完成K7收尾。K7为统一门禁、同输入方法对照、备份恢复、历史与凭据边界验证及交付说明，见 [K7执行记录](2026-10-17-alphaagent-k7-closeout.md) 与 [K7结果](2026-10-17-alphaagent-k7-results.md)。本节原K1–K6分期保留；不能把软件交付等同供应商当前无故障或投资效果已验证。

## 文件地图

新增 `src/marketpulse/quant/knowledge/`：

- `contracts.py`：SourceDocument、RuleVersion、RouteVersion、InvestorProfile、FeatureResult、ConditionalPlan 合同。
- `models.py`、`store.py`：版本表、审核和运行使用日志，原文引用及哈希检查。
- `ingestion.py`：白名单来源、有界取得、去重、定位和读取范围。
- `distillation.py`、`review.py`：提炼候选、作者原意与系统改写、反例与审核准入。
- `retrieval.py`：标签过滤、中文检索及片段排序，检索投影可重建。
- `features.py`：指标合同及 Decimal 计算，行情／财务适配器。
- `routing.py`：市场、业务、期限、数据和用户约束的硬准入，路线与规则冲突处理。
- `playbook.py`：条件、观察状态、数量和资金约束；不执行订单。
- `service.py`、`api.py`：有界流程和版本化接口。

K1 新增迁移 `migrations/versions/20261016_12_investment_knowledge.py`，前驱为 `20261012_11`；实施时已核对原迁移头，未覆盖既有迁移。

定向修改现有：`decision/medium.py`、`decision/api.py`、`decision/versions.py`、`investigation/server.py`、相应归档／垃圾回收引用枚举位置，以及 `frontend/src/alpha/Workbench.vue`、`workbenchIntent.js`、`MethodPolicyAnalysis.vue`。

旧 `decision/agents.py`、`decision/contracts.py`、方法 JSON 和下一交易日实验默认合同保持原义；新路线通过独立合同与适配器接入。现有模块若无需改变则不修改。

新增前端 `InvestmentProfile.vue`、`InvestmentRouteResult.vue`、`KnowledgeDevelopment.vue` 和 `useInvestmentRoutes.js`，图表沿用原型组件。

## 拟定接口合同

以下为实施接口定义，不是当前已上线接口。

接口签名与结果字段固定如下，具体实现属于后续授权开发：

- `KnowledgeStore.ingest_document(source_id: str, raw: bytes, metadata: dict) -> dict`：返回 `document_id、version、sha256、reading_scope、locators`。
- `KnowledgeStore.document(document_id: str, version: str) -> dict`：读取固定版本；`document_versions(document_id: str) -> list[dict]` 按版本创建时间列出。
- `KnowledgeStore.review_rule(rule: dict) -> dict`：返回 `review_id、verdict、reasons、rule_hash`，不直接发布。
- `KnowledgeStore.publish_rule(rule_id: str, version: str, review_id: str) -> dict`：只接受完整正文引用、匹配审核哈希的固定版本；结果包含独立的 `validation_status`。
- `retrieve_rules(query: str, context: dict, *, limit: int = 12) -> list[dict]`：只返回截止时点前、满足硬过滤的已发布规则、版本及确切来源片段。
- `route_candidates(profile: dict | None, subject: dict, horizon_sessions: int, features: dict) -> list[dict]`：返回 `route_id、version、admission、reasons、missing_inputs`；未配置画像不输出个人资金建议。
- `evaluate_plan(route_snapshot: dict, features: dict, profile: dict | None, market_rules: dict) -> dict`：返回 `condition_status、input_refs、calculations、constraints、profile_status、position_amount、position_shares、liquidity_limit_shares、cash_limit_shares、quantity_increment`。未配置画像时金额与股数为 null，市场约束和通用条件仍可展示。

在实现中将这些 dict 换成上述合同类，外部 JSON 保持字段和语义一致。所有对象保存版本、时间及哈希。接口内部不接受任意 Python、SQL、工具 URL 或可执行阈值表达式；指标和谓词必须引用受控枚举。

计划 API：

- `GET /api/quant/knowledge/catalog`：来源、规则、路线的范围与状态。
- `GET /api/quant/knowledge/search`：关键词、标签与截止时点检索。
- `GET /api/quant/knowledge/rules/{rule_id}/versions/{version}`：固定版本及引用。
- `POST /api/quant/investment-research`：新请求版本 `investment-research-v1`，证券、问题、`horizon_sessions`、可选画像和路线偏好。
- `GET /api/quant/investment-research/{run_id}`：方向、路线、条件、证据、运行状态和导出入口。

开发者入库、审核及发布首先用本机 CLI；可视化写接口在明确本机写权限校验之后建设。`developer=1` 仅控制展示，不能授予写权限。旧 `/api/quant/decision` 默认行为不变。

## K1：有来源、可检索、可复现的知识库

**Files:** 新知识合同、存储、取得、检索和迁移；新 `tests/unit/quant/knowledge/test_store.py`、`test_ingestion.py`、`test_retrieval.py`；新增 `scripts/verify_alpha_knowledge_sources.py`。

**Consumes:** 现有 SQLite sessions、原文 CAS、SourceClient 和设计中的来源候选。

**Produces:** 固定版本文档、可定位原文及中文检索；不输出策略收益或交易条件。

- [x] 在线备份数据库、保存已有完成记录哈希；保留进程及服务状态，不无故重启。原 143 条研究记录哈希复核未变。
- [x] 原样复跑三项旧 MemoryError 用例，3 passed；不修改断言、缓冲或回测门禁。源码指纹由最终回归的既有测试验证。
- [x] 来源版本、重复导入幂等、异内容新版本、原文损坏拒绝和旧研究哈希保持均已测试。
- [x] 追加版本迁移与存储；注册共享 Base 元数据以进入既有归档、垃圾回收引用扫描和配对备份。引用闭包测试通过。
- [x] 实测支持 FTS5，采用中文字符二元检索及英文词元；检验关键词、截止时点旧版本和无 FTS 关键词降级。
- [x] 封存 14 份真实正文、8 个来源族、5 类主题。导航入口和字符编码失败保留采集记录，修正后实际取得正文。
- [x] 保存四个不同行业证券的身份、日线、利润表与现金流表真实能力样本。现有资金粒度引用上期实源合同；复权和交易制度完整正文未在此阶段验真，明确列为 K3 准入前置。
- [x] K1测试集中在 `tests/unit/quant/knowledge/test_library.py`，17项全部纳入最终629项通过回归；前端105项通过并构建完成。证据见[K1结果](2026-10-16-alphaagent-investment-knowledge-k1-results.md)。
- [ ] 用户验收：开发者入口搜索方法，点开原始段落、版本、实际读取范围；普通工作台现有研究仍可使用。

关键验证模式：以下 fixture 由对应测试模块建立真实临时存储，禁止连接用户生产库。

```python
def test_repeated_source_is_idempotent(store, source_packet):
    first = store.ingest_document(**source_packet)
    again = store.ingest_document(**source_packet)
    assert first == again
    assert store.document_versions(first['document_id']) == [first]

def test_changed_bytes_create_new_version_without_rewriting_old(
    store, source_packet, changed_source_packet
):
    first = store.ingest_document(**source_packet)
    newer = store.ingest_document(**changed_source_packet)
    assert first['sha256'] != newer['sha256']
    assert store.document(first['document_id'], first['version']) == first
```

## K2：方法提炼、审核和发布

**Files:** 新 `distillation.py`、`review.py`；新 `tests/unit/quant/knowledge/test_distillation.py`、`test_review.py`；新增 `scripts/distill_alpha_investment_rules.py`。

**Consumes:** K1 文档版本及已定位原文。

**Produces:** 至少二十四条审核规则，包含指标、条件、反例、参数出处和执行／效果状态。

- [x] 建立筛选、入场、持有、退出、风险、容量、估值、市场环境等规则类别，保存来源至规则的映射。
- [x] 有界离线提炼：提炼者仅引用取得正文，反例审核者检查冲突、误归因、遗漏前提与 A 股适用性；运行日志真实保留，不记为外部专家审核。
- [x] 验证作者原始阈值与系统改写参数分栏；定义来源无法支持的具体阈值只能是待验证提案。
- [x] 实现 `DRAFT → REVIEWED → PUBLISHED → RETIRED`，旧版不可覆盖；效果状态与发布状态独立。
- [x] 转载与同一作者的重复观点合并来源族；同模型职责复核不称为独立验证。
- [x] 测试原文指令注入、乱码、OCR 定位不可靠、重复来源、元数据页冒充正文、无反例规则、凭空阈值、已撤回规则和越权发布均被拒绝。
- [x] 自测：`python -m pytest tests/unit/quant/knowledge/test_distillation.py tests/unit/quant/knowledge/test_review.py -q`。
- [ ] 用户验收：开发者可看到某条规则如何从原文形成、改写了哪里、什么情况下会失败；用户页面不显示大面积方法卡。

K2 于 2026-10-16 完成开发、自测及真实 API 验证，等待用户亲自验收。候选由 Codex 阅读封存原文提炼，再由现有模型执行两种审核职责；已发布二十四条参考规则。按用户要求，知识库仅作为内置能力维护，普通工作台隐藏入口，保留开发者只读核验页。实际结果与边界见 [K2 验收记录](2026-10-16-alphaagent-investment-knowledge-k2-results.md)。

验收断言：

```python
def test_catalog_metadata_cannot_publish_a_rule(store, catalog_only_rule):
    result = store.review_rule(catalog_only_rule)
    assert result['verdict'] == 'REJECT'
    assert 'SOURCE_BODY_REQUIRED' in result['reasons']

def test_published_rule_is_not_marked_as_profitable(store, reviewed_rule):
    result = store.publish_rule(**reviewed_rule)
    assert result['validation_status'] == 'UNVALIDATED'
```

## K3：路线、指标与资金约束

**Files:** 新 `features.py`、`routing.py`、`playbook.py`；新 `tests/unit/quant/knowledge/test_features.py`、`test_routing.py`、`test_playbook.py`。

**Consumes:** K2 发布规则，K1 真实输入能力与当前交易规则。

**Produces:** 至少两条跨行业可运行路线；四条首版路线均具有明确的适用性和数据需求。

- [x] 首先实现趋势波段和质量／成长，使用真实数据可得的有限合同；成长／质量的规范化指标缺失时不得凭原文心算。
- [x] 为相对收益、均线、量比、波动、财务增长、现金回收等逐项定义窗口、单位、复权和公告时点；不把这些指标本身称为已验证交易优势。
- [x] 单股同窗相对强弱可先对声明的市场指数计算；全市场排名必须有完整同期样本，覆盖不足不发布百分位。
- [x] 用 Decimal、手算样例、同输入复算和独立明确口径参考核对数字。负利润、跨期、财务重述、金融与非金融业务分开处理。
- [x] 资金画像用五万、五十万、五百万元检验最小申报量、费率、现金、组合暴露与流动性；不按金额硬绑定路线。
- [x] 工具计算风险数量、现金、组合和流动性上限，按证券适用规则取整；跳空和无法退出压力单列，停损价不等于最大亏损保证。
- [x] 采用主路线一致的入退场逻辑，禁止用价值规则为趋势失败补仓、把未来一天和三个月的信号混成一个动作。
- [x] 自测：`python -m pytest tests/unit/quant/knowledge/test_features.py tests/unit/quant/knowledge/test_routing.py tests/unit/quant/knowledge/test_playbook.py -q`。
- [x] 用户验收：同一真实股票选择不同周期／约束，解释路线与容量差异；不同一级行业能进入通用路线，金融企业不会被套用非金融指标。

验收断言：

```python
def test_missing_profile_does_not_create_personal_position(engine, market_case):
    result = engine.evaluate_plan(profile=None, **market_case)
    assert result['position_amount'] is None
    assert result['position_shares'] is None
    assert result['profile_status'] == 'NOT_CONFIGURED'

def test_liquidity_and_rounding_are_hard_limits(engine, capacity_case):
    result = engine.evaluate_plan(**capacity_case)
    assert result['position_shares'] <= result['liquidity_limit_shares']
    assert result['position_shares'] <= result['cash_limit_shares']
    assert result['position_shares'] % result['quantity_increment'] == 0
```

上式 `quantity_increment` 由具体证券规则确定；科创板等存在不同最小数量和增量语义时，测试同时验证最小数量而不是套统一手数。

## K4：统一工作台真实闭环——首个完整交付

**Files:** 新知识 `service.py`、`api.py` 与前端四个组件；定向接入 `decision/medium.py`、`decision/api.py`、`decision/versions.py`、`investigation/server.py`、`Workbench.vue`、`workbenchIntent.js`；新 `tests/integration/quant/knowledge/test_workbench.py`、`test_compatibility.py`、`test_recovery.py` 和 `frontend/tests/investmentRoutes.test.js`。

**Consumes:** K1–K3 固定版本、画像、数据及条件计划。

**Produces:** 普通用户结果、开发者审计、真实运行、不可变快照、导出和失败恢复。

- [x] 增加独立请求合同 `investment-research-v1`，以交易日数量表达波段／中期期限；旧一至三个月请求和提示词保持原义。
- [x] Planner 只选程序已准入路线；Analyst 用工具结果解释，Verifier 核对来源、反证及冲突，Writer 仅输出通过的内容。每次研究不重新蒸馏全库。
- [x] 将研究方向与条件状态分栏：方向继续明确，等待触发或当前不适配可以与明确方向并存；数字和数量直接来自计算记录。
- [x] 模型失败保留真实失败状态、已有材料及恢复入口；重复点击幂等，续跑不覆盖既有完成记录。
- [x] 统一工作台提供可选画像；用户看方向、路线、关键指标、价格／条件图和反证，开发者看原文、阈值、准入、计算与版本。
- [x] 保存知识使用记录、输入截止、规则哈希和来源片段；升级知识后旧报告能按原版本读取和导出。
- [x] 自测：按实际文件布局运行 `tests/unit/quant/knowledge`、完整quant及存储／备份／迁移回归；`npm --prefix frontend test`；`npm --prefix frontend run build`。688后端及114前端通过，2项Windows符号链接权限跳过，1项既有Starlette警告。
- [x] 真实验收：从不少于四个不同一级行业各选一只股票，在数据身份核对后运行；至少覆盖金融与非金融的不同准入，不把金融指标停用记为通用路线失败或伪造分数。
- [x] 浏览器点验：名称／代码输入、重复请求、切换画像、条件图、原文引用、开发者视图、刷新恢复，桌面和手机无横向溢出。
- [x] 给用户最终 URL、点击步骤和预期现象；发布记录分别列出功能、来源覆盖、仍未启用方法和投资效果状态。

首个交付的验收问题：

1. 这只股票为何适合／不适合这条路线？
2. 当前最关键的三至五个指标是什么，数字从哪里来？
3. 入场条件满足了哪些，尚缺哪些，何时失效？
4. 我的资金、现金需求和组合暴露改变了哪些约束？
5. 规则依据谁的哪一段资料，本系统改写了什么？
6. 原始版本和随后观察能否导出复算？

## K5：事件政策、市场与资金知识扩展

**Files:** 新知识上下文适配模块 `context.py`；定向复用 `decision/policy_sources.py`、`industry_sources.py`、`flows.py` 和实时资金合同，不改变原供应商定义；新增 `tests/integration/quant/knowledge/test_context.py`。

**Consumes:** 已发布路线、来源版本、公司业务身份及事件／政策原文。

**Produces:** 市场环境、分口径资金和政策传导进入适合的路线。

- [x] 中国国家级政策为主，海外政策按业务关联检索；保存修订、生效、废止、资格和公司连接状态。
- [x] 区分事件事实、业务影响假设和价格反应；不凭新闻同日上涨认定因果。
- [x] 把行业、业务模式作为指标和规则选择约束，不建立行业必过门槛。
- [x] 原供应商分钟资金与 Tushare 日频分列；只有实际连续积累的时间段才能用于回放。当日供应商序列明确不是系统此前连续监测历史。
- [x] 测试未生效／失效政策、资格未知、重复／非官方来源、未来数据、来源冲突、日频伪装分钟和公司身份错配；四行业实际日频及分钟来源通过。
- [x] 独立自测并提供用户和开发者页面验收，不继承 K4 的“功能通过”作为该阶段结果。958后端及116前端通过、2项Windows权限跳过，生产构建与四行业138个Blob离线重建通过。

## K6：前瞻复盘与知识迭代

**Files:** 新知识 `observations.py`、`evaluation.py`；新 `tests/integration/quant/knowledge/test_observations.py`、`test_evaluation.py`；新增 `scripts/verify_alpha_investment_routes.py`。

**Consumes:** 固定路线版本、签发记录与后续真实行情。

**Produces:** 不可变观察台账、按路线与约束分组的复盘及待审核规则建议。

- [ ] 在取得结果前冻结观察目标、样本、期限、费用、基线和排除规则；研究方向、触发信号和模拟交易分开统计。
- [ ] 等待、未触发、缺失、失败、停牌及尚未到期全部保留，不事后挑盈利样例。
- [ ] 标记真实取得时间；当前知识和当前成分不得回填成历史已知资料。
- [ ] 比较无方法知识、单一路线和路线适配的功能／前瞻差异；正式回测必须通过原 5.2 门禁，未通过不发布收益优势。
- [ ] 复盘产生新版本候选，审核后发布；不自动改阈值追逐近期收益，不覆盖原记录。
- [ ] 用户验收：看到原条件、实际是否触发、方向与行动分别如何观察、原反证有没有发生，以及新旧版本具体差异。

## 2026-10-15 方案整理会话自审与产品选择

- 已按最新用户要求采用跨行业方法体系，行业知识仅作为上下文；不继续以半导体首发作为入口条件。
- 已按用户回答固定 A 股、波段与中期、资料由 Codex 筛选。
- 首版不要求用户提供作者名单、资金金额或付费资料；提供画像才计算个人资金约束。
- 文档内所有接口、测试和交付数量都是拟实施目标，没有上线或通过声明。
- 2026-10-15 方案整理会话没有业务代码、数据库、服务、模型调用和 Git 提交变更。
- 以上为方案整理及早期执行历史。2026-10-16 K1–K5已依次实现、自测并交付验收入口；K6仍待按顺序开发。功能通过不代表方法已验证盈利。

## 2026-10-17 最终验收补充

K6软件复验及K7统一工程收尾已完成：全仓1238项通过、10项环境／显式启用跳过，前端121项通过；同输入真实方法对照6/6，配对备份及隔离恢复通过。当前Tushare HTTPS证书过期，新取数验证未通过，封存研究及离线复算可用；用户亲验为PENDING，方法效果UNVALIDATED。详见 [K7结果](2026-10-17-alphaagent-k7-results.md)。此状态更新优先于早期“K6待开发”的过程记录，不覆盖原失败或原知识版本。
