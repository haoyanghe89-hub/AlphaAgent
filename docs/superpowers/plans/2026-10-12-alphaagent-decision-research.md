# AlphaAgent 研究判断、资金分析与龙头筛选实施计划

> **For agentic workers:** 使用当前可用的 `executing-plans` 技能逐任务实施。用户将另开 thread 执行；本 thread 只生成方案。除非用户或适用指令明确授权，不派生子 Agent。各阶段有自测与用户页面验收检查点。

**Goal:** 先交付下一交易日涨跌预测与真实核对实验，再面向未来1–3个月完成“热点选择 → 方法与行业取证 → 经营/预期/资金分析 → 龙头候选 → 方向情景与条件化意见 → 不可变前瞻记录 → 新证据与结果跟踪”的真实可追溯闭环。

**Architecture:** 保留 deepsearch 底盘、Vue 3、多 Agent 与确定性计算。新增独立 decision 包，按版本化方法卡选择资料，在统一证据合同上实现有限补证循环、资金解释、预期差、方向情景和候选报告；复用旧存储与比较服务，追加不可变前瞻台账，保留旧 API。

**Tech Stack:** Python 3.12–3.13、FastAPI、Pydantic、SQLAlchemy/SQLite、现有内容寻址存储与模型适配器；Vue 3、Vite、ECharts；pytest、Ruff、Node test。

**Spec:** [完整设计与合同](../specs/2026-10-12-alphaagent-decision-research-design.md)。执行者必须先读设计，再读本计划。

**状态：**方案初稿中的接口与文件名是设计记录；实际进度以各阶段勾选、实现状态和对应结果文档为准。来源覆盖、用户验收与预测有效性分别记录。

**2026-10-12修订：**用户指定由Codex承担研究方法与案例审核；首行业改为半导体设备，制造/封测分阶段扩展。新增[已整理资料、8张方法卡与12例领域门禁](../../alphaagent-investment-methodology-and-semiconductor-research.md)，与本计划、设计一起阅读。先交付P0→P1→P1-N纵向闭环，再按P2–P7推进；不等待全市场数据齐备，也不降低数据门禁。此次仅修改文档。

**最新范围补充：**用户要求实验阶段最早提供“明天涨/跌”，以真实前瞻准确率检验。按[下一交易日实验协议](../../alphaagent-next-session-prediction-experiment.md)增加P1-N，交付顺序改为P0→P1→P1-N→P2→P3–P7；原36例加12例实验门禁，共48例。P1-N独立验收预测与次日核对，不等待中期全流程；概率仍不在首版范围。

## 0. 新 thread 启动说明（可直接复制）

完整资料目录、阅读顺序和包含最新实验要求的启动词见[新会话开发交接包](../../alphaagent-decision-research-handoff.md)。文档中的“本轮只写方案”描述资料整理会话；用户在新会话明确要求执行后，按授权进入开发。

```text
请在 D:\deepsearch 执行以下方案：
1. docs/superpowers/specs/2026-10-12-alphaagent-decision-research-design.md
2. docs/superpowers/plans/2026-10-12-alphaagent-decision-research.md
3. docs/alphaagent-investment-methodology-and-semiconductor-research.md
4. docs/alphaagent-next-session-prediction-experiment.md

目标：未来1–3个月的经营/行业原因、预期差、资金行为、龙头候选、方向情景及有证据的条件化意见，首次判断保存后不能覆盖，并按固定窗口跟踪。
另有最早交付要求：P1-N提供下一交易日收盘行情方向UP/DOWN与理由，先封存再核对；固定时点/名单、失败记录、真实准确率和永远涨/永远跌/昨日延续基线单列，不以中期看好代替明日判断。
先核对当前源码和工作区，从 P0、P1 开始，按纵向闭环推进。已确定免费来源优先、Vue 3、多 Agent、原型样式、不移植原型计算代码。不需要重新确认这些方向。
先以半导体设备为首个验收样例，可输入中微公司688012、北方华创002371并用制造端资料核查需求。它们不是写死支持名单或已确认推荐。部署时重新核对热度与覆盖，不宣称唯一最热。保留美的及既有单股、DCF、K线、比较、历史和策略功能。数字由工具计算，不能伪造数据/资金流/龙头依据/涨跌概率。
按M01–M08建立版本化方法库，不搞名人Agent投票。Codex负责研究和审核，AI自审不冒充外部独立验证；不要求用户先找专家。
原5.2真实全市场档案尚未整体通过；本次不降低正式回测门禁。
每阶段先自测并报告实际结果，再给我页面链接和点击步骤验收。来源缺失明确降级，不把部分完成写成整体完成。
重新检查当前Git状态，不假设干净或有大量既有改动；禁止 reset、覆盖、批量清理，不自动提交或推送。不为每阶段另开端口，优先沿用8839并识别已有进程，必要重启先保留记录。
先完成一个行业可演示闭环，再扩展市场扫描。按计划执行，不无限开放工具或自动交易。不要仅写计划后停下，开始已授权的 P0、P1 工作。
```

## 1. 全局约束

- 用户当前只要求方案；实施发生在新 thread。不得在本文交付时创建新 thread、启动开发或发起定时任务。
- 业务范围、证据合同、资金定义、预算及错误码以设计文档为准，所有阶段适用。
- 项目根 `D:\deepsearch`；读取适用 AGENTS.md 和当前技能。读取真实Git状态，若有既有改动应保留，不是可删除杂项。
- 不自动提交/推送。记录每阶段实际文件和差异，避免把无关改动归入当前任务。
- 新接口不占用旧路由；旧报告不重新编号证据、不回填修改结果。页面新能力通过 capability/feature flag 渐进启用。
- 数据收集用有限速、超时和退避的适配器。凭据不回显；下载URL仅允许可信源并校验重定向、大小和类型，不接受模型任意URL。
- 初始总预算24模型调用、60,000总token、900秒，覆盖子任务与重试；最多两轮/八次新增补证工具调用、三只候选深研。调用前预留输出预算。
- 数值算法不得复制原型计算代码；手算样本、独立口径核对、同输入复算分别记录。
- 演示使用新采集的真实数据；测试夹具仅存在隔离测试环境。原始响应和完整性记录可冻结作为回归输入，但不能冒充实时源。
- 首行业半导体设备；不同产业环节不能直接混排财务指标。动态扫描与专业模板覆盖分开显示，开发选题记录不冒充运行时热度计算。
- 方向FAVORABLE/UNFAVORABLE/MIXED/UNDETERMINED与研究优先级独立；不输出未校准概率、仓位或三个月DCF目标价。
- 方法及案例审核由Codex承担；记录AI_METHOD_REVIEWED/DETERMINISTIC_CHECKED/真实EXTERNAL_REVIEWED，外部审核不作为用户前置工作。
- 首次前瞻记录不可覆盖；20/40/60交易日窗口、基准、收益口径预先记录，未成熟/失败/撤回/退市不丢样本；研究观察不能替代5.2正式全收益核对。
- 下一交易日实验采用独立请求/协议，不向1–3个月合同塞horizon_months=0；默认收盘后20:00–20:15、固定两证券篮子，交易日历、参考价和标签规则以实验文档为准。
- 主预测只允许UP/DOWN，必要输入或审核失败记录失败slot；冲突观点可弱支持二选一，不能用拒答挑选好预测的日期。用户手动触发，未触发也记计划缺席，不添加自动化。

## 2. 文件与模块地图

以下路径为拟新增，实施前检查是否已被其他工作建立；同职责已有模块应复用并在计划中更新映射，不能重复建一套。

| 文件/目录 | 单一职责 |
| --- | --- |
| `src/marketpulse/quant/decision/contracts.py` | 请求、证据、假设、缺口、意见、资金、候选和覆盖的类型合同 |
| `decision/capabilities.py` | 来源能力、实际覆盖和新鲜度规则 |
| `decision/tools.py` | 白名单工具注册、参数校验、任务预算和来源限流接线 |
| `decision/disclosures.py` | 定向公告发现、PDF/正文解析与定位 |
| `decision/industry.py` | 业务/行业映射、同行可比性、行业指标与日历 |
| `decision/evidence.py` | 原文/片段/派生值关联、去重、时点过滤和检索 |
| `decision/methods.py` | 方法卡注册、版本/适用性检查与输入缺口；不执行任意文本代码 |
| `decision/method_cards.json` | M01–M08可审核配置，内容以配套研究文档为基线 |
| `decision/industry_templates.json` | 设备/制造/封测各自指标、传导与反证配置，逐项启用 |
| `decision/expectations.py` | 有基准的预期比较、带明确假设的逆向估值与敏感性 |
| `decision/sector_selection.py` | 同分类热度计算、覆盖检查、确定性排名与选题记录 |
| `decision/forecasts.py` | 初始判断不可变保存、版本链、到期观察和收益质量降级 |
| `decision/next_session/contracts.py` | 独立日预测协议、快照、预测、slot与结果合同 |
| `decision/next_session/service.py` | 封存后真实模型方向/依据/审核、时限及批次预算 |
| `decision/next_session/store.py` | ResearchStore适配、正式唯一键、失败attempt、追加结果 |
| `decision/next_session/labels.py` | 交易日历、参考价/除权息检查、UP/DOWN/FLAT与可核对状态 |
| `decision/next_session/metrics.py` | 主/非平盘/平衡准确率、分母、三基线与版本分组 |
| `decision/flows.py` | 资金指标规范化及确定性计算 |
| `decision/flow_sources.py` | 融资、ETF、供应商估算等来源适配 |
| `decision/attribution.py` | 事件窗口、同期对比、时间精度和竞争解释检查 |
| `decision/agents.py` | 新模式角色提示、输出合同、后验校验 |
| `decision/service.py` | 有界研究循环、检查点、补证优先级与停止条件 |
| `decision/screening.py` | 时点候选池、资格、排除、分组与排序 |
| `decision/report.py` | 已审核结论到展示模型，意见和更新差异 |
| `decision/api.py` | 新路由、幂等、恢复、刷新、导出和取消 |
| `frontend/src/alpha/DecisionResearchView.vue` | 新研究结果容器 |
| `frontend/src/alpha/ResearchOpinion.vue` | 简明意见和验证条件 |
| `frontend/src/alpha/FlowAnalysis.vue` | 资金图表及口径 |
| `frontend/src/alpha/IndustryCandidates.vue` | 行业扫描与候选卡 |
| `frontend/src/alpha/ForecastTracking.vue` | 首次观点、变更/撤回、固定窗口成熟状态与观察结果 |
| `frontend/src/alpha/NextSessionPrediction.vue` | 顶部下一交易日涨/跌、依据、签发状态与结果 |
| `frontend/src/alpha/PredictionScoreboard.vue` | 实验计划/失败台账、命中与基线比较 |
| `frontend/src/alpha/useDecisionResearch.js` | 请求与状态轮询，取消/刷新/错误处理 |
| `frontend/src/utils/decisionChartOptions.js` | 沿用原型主题的新增图表配置 |
| `tests/unit/quant/decision/` | 纯计算、合同、归因门禁 |
| `tests/integration/quant/decision/` | API、真实服务编排、存储恢复 |
| `tests/fixtures/quant/decision/` | 有许可证/来源说明的隔离夹具及评测样本 |
| `scripts/verify_alpha_decision.py` | 实际服务与浏览器验收入口 |
| `docs/alphaagent-decision-source-audit.md` | 来源试验、口径、真实可用性、缺口 |
| `docs/alphaagent-decision-evaluation.md` | 样本、指标、真实结果、费用延迟 |
| `docs/alphaagent-investment-methodology-and-semiconductor-research.md` | 已整理方法来源、行业选择、卡片内容与领域门禁，不替代实源探测 |
| `docs/alphaagent-next-session-prediction-experiment.md` | 最早交付的日预测协议、真实统计分母与12例门禁 |

可能修改的现有接入位置：`live/store.py`、`live/research_api.py`（只复用必要函数，避免循环导入）、`investigation/server.py`、前端 `App.vue`/`alpha/navigation.js`、`Workbench.vue`、`LibraryView.vue`、`DeliveryView.vue`、`useDelivery.js`、主题扩展样式。先读实际实现再修改，不假定项目使用 vue-router。数据库新增列需要迁移；可用带schema_version的payload时不为新功能无故改旧表。

## 3. 接口约定与测试方法

Python入口计划统一为以下签名；所有类型定义在 `contracts.py`，来源HTTP依赖通过已有适配器/显式注入，测试不能偷偷联网。

```python
resolve_request(request: ResearchRequest) -> ResearchScope
collect_evidence(scope: ResearchScope, needs: list[EvidenceGap]) -> EvidenceBundle
compute_flows(data: FlowInputs, spec: FlowWindowSpec) -> FlowReport
evaluate_event(event: EventRecord, prices: PriceWindow) -> AttributionObservation
screen_candidates(universe: CandidateUniverse, inputs: ScreeningInputs) -> ScreeningResult
verify_claims(claims: list[ResearchClaim], evidence: EvidenceBundle) -> VerificationResult
build_opinion(approved: VerifiedResearch) -> ResearchOpinion
select_methods(scope: ResearchScope, methods: list[MethodCard]) -> list[MethodSelection]
rank_sectors(data: SectorHeatInputs) -> SectorSelectionRecord
evaluate_expectations(data: ExpectationsInputs) -> ExpectationsReport
record_forecast(store: ResearchStore, record: ForecastRecord) -> ForecastRecord
observe_forecast(record: ForecastRecord, prices: OutcomeInputs) -> OutcomeRecord
# service.py 的对象持有 store、模型端口、工具、共享预算和取消状态
DecisionWorkflow.run(run_id: str) -> None
```

类型定义至少包含设计§6字段。补充：`ResearchScope`固定证券/行业ID与窗口；`EvidenceBundle`包含items/sources/coverage；`FlowInputs`包含各源日序列、行业成分及方法；`FlowWindowSpec`包含end_date/windows/definition_version；`EventRecord`包含发布时间及精度；`PriceWindow`包含三类同口径价格；`AttributionObservation`包含观察、窗口和限制；`CandidateUniverse`包含成员、时点和来源；`ScreeningInputs`包含候选各维证据；`ScreeningResult`包含入选/排除/不足；`VerifiedResearch`只接受通过审核的claim及其引用。复杂对象内部结构在P0的JSON Schema和测试中固定，不让各阶段自行发明字段。

新增类型同样在P0固化：`SectorHeatInputs`含分类/成分版本、交易日、同口径成员/市场成交及覆盖；`ExpectationsInputs`含历史/指引/一致预期基准引用、估值输入、固定假设与求解变量，缺少输入不能强求解；`ExpectationsReport`含可用性、比较结果/敏感性、假设及限制；`OutcomeInputs`含观察时点、交易日历、个股/行业/市场价格、公司行为完整性和收益口径。`ResearchStore`复用live/store.py，新增schema_version payload适配。`record_forecast`接收工作流注入的store：同ID同内容返回原记录，冲突内容409，禁止覆盖。`observe_forecast`只计算观察，由工作流追加保存，不修改record。

每个任务遵循：先写能暴露真实错误的测试/固定样本，确认基线失败原因；实现最小闭环；执行针对性测试；更新结果记录。不要写仅照抄实现的测试，也不要为纯文字样式添加无意义测试。

运行方式（PowerShell，命令分别执行，不用跨shell拼接）：

```powershell
uv run --no-sync pytest tests/unit/quant/decision tests/integration/quant/decision -q
uv run --no-sync ruff check src/marketpulse/quant/decision tests/unit/quant/decision tests/integration/quant/decision
```

仅运行已经建立的目录；前端在 `D:\deepsearch\frontend` 运行 `npm test`、`npm run build`。新环境先阅读现有启动说明，不擅自更新锁文件。每次阶段记录所用命令、退出码、失败与处理，不引用旧测试数冒充本次验证。

## P0：基线、半导体来源、方法库和验收样本

**产物：**准确能力表、真实源探测、8张方法卡、行业选题记录、可执行合同和36例评测清单。不是只增加空目录。

最新实验范围：再加入E01–E12，评测清单共48例；P0同时建立 `next_session/contracts.py` 和实验配置/slot合同，但本阶段不宣称已有真实预测准确率。

**Files:** `decision/contracts.py`、`capabilities.py`、`methods.py`、`method_cards.json`、`industry_templates.json`、`sector_selection.py`；`tests/unit/quant/decision/test_contracts.py`、`test_methods.py`、`test_sector_selection.py`；两个审计/评测文档；`tests/fixtures/quant/decision/cases.json`。

- [ ] 读取设计、当前计划、`docs/alphaagent-current-state-and-next-steps.md`及当前源码；记录Git状态、服务进程、数据库/日志目录，不输出密钥。
- [ ] 确认现有真实单股/比较样本可读；原有失败单独登记，不把README早期状态当最新能力。
- [ ] 为每个拟接数据源实测小样本：公告/PDF、行业成员、日行情、融资、ETF份额与净值、供应商资金、行业统计。逐项记录HTTP/字段/期间/单位/许可/缺失。来源未知不猜测endpoint。
- [ ] 按配套资料S01–S11逐个探测；WSTS/SIA共同底层来源去重，SEMI公开新闻与付费月序列分开。至少核查设备两公司原文和制造端材料，核对中微公司/中微半导身份，保留报告审计属性。
- [ ] 将M01–M08制成方法配置，带输入、原始来源、反证、禁止推断和版本；Codex逐卡审核并保存实际身份。方法来源不能充当当期经营证据，不以外部专家尚未参与阻断。
- [ ] 建立设备模板，制造/封测标为未启用直到独立验收；不得由海外存储增长直接认定国内设备公司受益。记录2026-10-12开发选题为provisional，并尝试同口径20/5日热度复算；序列不足保留缺口、不宣称完成全市场热点排名。
- [ ] 形成首期冻结的支持矩阵及新鲜度规则；有阻断的源先返回UNAVAILABLE与原因。无可靠市场行业覆盖时只启用单行业模式。
- [ ] 定义合同和Schema；只允许结构化字段，不允许客户端上传“验证通过”。固定候选池定义、业务相关性规则、流动性观察窗口和排序键，记录为规则v1。
- [ ] 建立24例基础集（12开发/12留出）和配套研究文档§6的12例领域集（6开发/6留出），合计36例；逐例绑定材料/合成夹具、允许/禁止答案和审核身份。先固定材料再评估，不按模型输出倒改答案。
- [ ] 固化MethodCard/MethodSelection、SectorSelectionRecord、方向/情景、ForecastRecord/OutcomeRecord合同；原始判断、观察窗口、基准和质量字段必须可导出；客户端不能提交审核通过标记。
- [ ] 固化NEXT_SESSION_EOD_V1的时间窗口、名单版本、交易日、封存/签发顺序、行情参考价标签、PRIMARY唯一键及失败slot；追加E01–E12并固定6开发/6留出分组。原36例不改，合计48例。
- [ ] 探测两证券真实日行情、前收/除权息参考、交易日历与停牌状态，核对来源身份/价格精度/公告可用时点；不从已舍入的涨幅推标签。最低数据无法取得时P1-N不能标可真实运行。
- [ ] 测试缺时点、错单位、身份冲突、未知状态、非法预算、历史材料无可用性证明等情况。

**明确测试输入：**请求horizon_months=0/4应拒绝；历史cutoff早于证据available_at应排除；无source/locator的事实不能成为有效引用；仅日行情不能启用订单簿方法；20日少1日不能排名；AI审核不能显示EXTERNAL_REVIEWED。

**验收：**把能取得/缺失/未验证三类数据向用户说明；资料探测失败不能以“接口预留完成”标整个P0完成。至少半导体设备样板所需行情、两公司披露及一种适用资金序列取得真实输入，8卡及48例清单可审阅；其余缺口明确降级。日行情活跃度不冒充已取得资金序列。

## P1：单股定向取证与最小报告闭环

**Files:** `decision/disclosures.py`、`industry.py`、`evidence.py`、`tools.py`、`api.py`、初版`service.py`/`report.py`；`test_evidence.py`、`test_decision_api.py`；`DecisionResearchView.vue`、`useDecisionResearch.js`。

**Consumes:** P0合同、动态证券解析、ResearchStore。**Produces:** EvidenceBundle、新任务API、可点击来源的单股报告基础壳。

- [ ] 实现定向资料类型检索：财报业务/经营讨论、业绩变动、投资者关系活动；按问题和期间选材，不能只取最新两篇。
- [ ] 分离原文、解析文本和引用片段；PDF定位页码，正文定位字符；检查主体、报告期、披露与抓取时间，保存哈希。
- [ ] 实现原始来源去重、时点过滤、有限检索；来源冲突并存，不静默覆盖。
- [ ] 新增 `/api/quant/decision` 路由并保留旧路由；新任务幂等、历史读取与导出接线。
- [ ] 接通新页面：动态输入中微公司和北方华创，显示材料、方法选择、范围、缺口和过程；继承原型主题及引用弹窗，回归既有美的研究。
- [ ] 分离方法检索和当期事实检索；按方法/行业/期限过滤，保留来源族和版本。原文指令不得改变工具或预算，方法不适用时展示原因。
- [ ] 在隔离库测试创建/读取/导出/请求冲突及证据损坏；真实源和实际模型连接分开记录。

**明确测试输入：**相同公告被两网站转载，独立来源数为1；PDF页码/文本哈希错误应报完整性错误；2026-10-12抓取且无历史版本的指标不得进入2026-09-01回放。

**用户点击：**输入单股 → 查看材料 → 点原文/片段 → 刷新页面 → 历史回看；此阶段不显示尚未接通的推荐结论。

### P1-N：优先交付下一交易日方向实验

**Files:** `decision/next_session/`上述5模块、`decision/api.py`、既有store必要适配；`tests/unit/quant/decision/test_next_session_labels.py`、`test_next_session_metrics.py`、`test_next_session_contracts.py`；`tests/integration/quant/decision/test_next_session_workflow.py`、`test_next_session_api.py`；`NextSessionPrediction.vue`、`PredictionScoreboard.vue`、`useDecisionResearch.js`、前端`nextSessionPrediction.test.js`。

**Consumes:** P0实验配置/类型、P1证据采集、ResearchStore、现有模型适配器和共享预算。**Produces:** NextSessionPrediction、ExperimentRunSlot、NextSessionOutcome、带基线的scoreboard。NextSessionSnapshot须保存证券/base/target_session、时点、封存哈希、日行情及确定性指标、可得资金/事件引用和缺项；只接受快照中的引用。

- [ ] 先写E01–E12相关测试并确认暴露的错误，再实现交易日解析、精确价格比较和状态转换。普通、节假日、除权息、停牌和未收盘分别验证。
- [ ] 保存实验配置和计划slot（包括未触发日期），取证结束封存快照；方向角色只能读此快照，后发消息不能补到原理由。默认20:00–20:15完成，超时不发主记录；窗口外临时预测另组显示。
- [ ] 用真实LLM生成UP/DOWN、支持/反对理由与引用；确定性引用/数字/时点审核及模型语义审核后签发。无最低数据/审核失败记失败，足够数据但观点冲突仍二选一并标弱支持；不提前填固定UP或用技术指标规则冒充真实模型调用。
- [ ] 正式记录按experiment+security+target_session原子唯一；首个成功方向不变。重复/修改/失败尝试保存为追加事件或辅助记录，不择优替换。封存、签发、模型/提示/方法/名单版本完整导出。
- [ ] 接通独立实验API：POST runs、GET runs/{id}、POST runs/{id}/observe、GET scoreboard；不用horizon_months=0绕过中期请求。服务端取真实标签，拒绝客户端提交审核通过或actual结果。
- [ ] 确定性保存永远UP、永远DOWN、昨日方向延续基线；实现包含FLAT未命中的主准确率、非平盘准确率、涨/跌召回和平衡准确率。显示分子/分母、类别分布、计划/覆盖/未触发/失败/待核对/不可核对，不悄悄丢样本。
- [ ] 结果核对只读取目标收盘后原始数据；除权息参考不能猜，冲突记CONFLICT，停牌UNSCORABLE不顺延；晚到结果可幂等追加，来源修订另存版本与重算原因，不改预测。
- [ ] 顶部先显示日预测方向/目标日期/理由/反对理由，再展示中期研究。未完成时显示取数/审核过程；可跳原预测、来源、台账与基线，原型浅深主题沿用。
- [ ] 相关单元/集成/前端检查通过后，以最新真实数据和真实模型签发一次；次一市场交易日收盘后再核对，首日只能标签发已验收。若不在实验窗口，先演示EXPLORATORY，正式签发待窗口，不伪称已有主准确率。

**固定统计测试：**10例实际8UP/2DOWN且全猜UP，主准确率80%、平衡准确率50%，与永远UP相同；实际FLAT且预测UP记未命中并单列；预收盘100、除息参考99、次收盘99.5标签UP（原表核对另做）；重复observe不增加分母；未触发slot降低覆盖率而不伪算预测错。

**用户点击：**实验首卡看“下一交易日预测涨/跌”→点理由/签发时间→原记录→次日收盘后点核对→看命中、失败与三基线。初期20日检查流程、60日观察稳定性，均不是预测有效性的承诺；先完成本小闭环再进入P2，不因历史标签未成熟阻断其他开发。

## P2：经营/预期差、补证循环与首次前瞻记录

**Files:** `decision/agents.py`、`service.py`、`report.py`、`expectations.py`、`forecasts.py`、`api.py`；`test_claims.py`、`test_workflow.py`、`test_expectations.py`、`test_forecasts.py`、`test_forecast_api.py`；`ResearchOpinion.vue`、`ForecastTracking.vue`；前端`decisionResearch.test.js`。

**Consumes:** EvidenceBundle、方法选择、白名单工具。**Produces:** VerifiedResearch、ResearchOpinion、轮次轨迹、不可变ForecastRecord和初版跟踪入口。

- [ ] 定义角色输出：所用方法版本、候选解释、机制、预期基准、情景、支持/反证、缺口、审核决定；解析失败重试也占总预算。
- [ ] 确定性检查数字/引用/主体/时间；语义Verifier再审解释是否有支持，不允许模型更改计算验证等级。
- [ ] 实现NEED_EVIDENCE → 工具 → 新证据 → 修订的两轮循环；同一来源/参数去重，连续无增益停止。
- [ ] 接入共享预算、输出token预留、900秒总时限、取消与检查点；可恢复步骤不重跑，环境指纹变化禁止混合。
- [ ] Writer只消费已批准结论；新增数字和实体需后验验证。无可用判断时产出明确的INSUFFICIENT报告。
- [ ] 首页展示方向、优先级、期限、理由、反对理由和观察条件；经营、价格位置、估值位置独立。过程显示补查目的、新材料与观点变化，不展示隐藏思维链。
- [ ] 实现带基准的预期比较；逆向DCF固定输入假设、求解变量、边界和敏感性，使用独立数值样本核对。缺必要数据返回UNAVAILABLE，不阻断其他研究，也不填目标价；无共识基准不能写超预期。
- [ ] 首次发布原子保存ForecastRecord与报告引用，固定20/40/60交易日窗口及基准；无法判断也保存。相同ID同内容幂等，冲突内容409，修订另存并关联旧记录；旧记录损坏报完整性错误。
- [ ] 接通GET forecast/outcomes及POST observe；观察早于到期显示PENDING，过期但缺价格显示UNAVAILABLE，与到期收益为零区分；同一数据哈希重复观察不重复写入。用户可查看首次观点和版本链。
- [ ] 实际模型验证至少一个需补证的样例；把轨迹与成本保存，模型质量失败不得用规则文案冒充模型成功。

**明确测试输入：**初始只有利润下降，Analyst提出原材料涨价假设；补证发现披露主因为产品结构变化，最终必须撤回原原因；重复同一公告不得计为补证成功；预算剩余0不能再调用模型。

**新增固定样本：**仅测试夹具：永续现金流10、折现率10%、无增长时价值100，价格100应反推现金流10；改变折现率的敏感性必须显示，不能称市场真实预期。初始方向FAVORABLE，后续UNFAVORABLE另建记录，原内容/哈希不变；20日未到期无实现收益；只拿到价格收益不标TOTAL_RETURN_VERIFIED；研究优先级高但方向UNDETERMINED为合法组合。

**用户点击：**提问“研究中微公司，未来三个月怎么看、什么条件会改变判断” → 看方法和补查 → 点依据 → 看方向/预期/反证 → 查看首次记录。P1+P2构成第一个可用纵向交付，不等待市场扫描；此时尚未接通资金或成熟行业判断的部分必须明确未纳入。

## P3：资金计算、解释与原型图表

**Files:** `decision/flows.py`、`flow_sources.py`、`tools.py`、`agents.py`；`test_flows.py`、`test_flow_integration.py`；`FlowAnalysis.vue`、`decisionChartOptions.js`、前端`decisionFlows.test.js`。

**Consumes:** 实测来源与FlowInputs。**Produces:** FlowReport、带方法/口径的资金证据与图表。

- [x] 复用现有融资与行情；分别接入ETF、供应商估算适配器，返回真实覆盖、日期和来源，暂不能取得则保留显式缺项。
- [x] 实现5/20/60交易日窗口；交易日不齐时不给完整窗口统计。公开支持子窗口时必须显示实际长度，不能假冒20日。
- [x] 实现成交占比、上涨覆盖、相对表现、融资和ETF计算，严格执行设计§5.3；不合并不同资金量。
- [x] 将资金作为证据输入Analyst，输出持续性、广度、分歧和替代解释。拒绝由大单指标推断具体机构意图。
- [x] 增加交易集中度和资金/价格分歧；同一供应商估算与其转载只算一个来源。不得将行业成交占比排名命名为主力净流入排名，方法M04与输入能力绑定。
- [x] 新增图表，沿用原型底色、金色标题、网格、图例、红涨绿跌和青色证据高亮；浅色、深色、窄屏均验证。
- [x] 人工核对一种原始数据到图表的完整计算链；用独立定义/原表核对，区别供应商自算与官方核验。

2026-10-13 已自测并提供真实页面，等待用户验收，详见 [P3 实现与验收记录](2026-10-13-alphaagent-decision-p3-results.md)。真实演示已有融资、行情和供应商估算；ETF连续份额/事件因子、行业完整成员/分母仍缺项，相关计算仅通过隔离样本，不伪装实测齐备。官方定义已核对，本股融资数值尚未获得交易所独立原表核验。完整 quant 回归437项、前端79项通过，旧5.2门禁保留。

**固定数值案例（隔离夹具）：**

| 输入 | 期望 |
| --- | --- |
| 两只行业成员成交额100、200；市场总额1000 | 行业成交占比30%，不是净流入30% |
| ETF份额100→100；净值1→1.1 | 份额变动0，不能把规模增加10当净申购 |
| ETF份额100→110、采用当日净值1.1 | 估算申赎额11，必须带估算标签和单位 |
| 份额因一拆二100→200、净值减半 | 按事件调整后无凭空净申购；事件未知则拒绝估算 |
| 融资余额100→110，买入30、偿还20 | 分别显示余额+10、买入30、偿还20，不称全市场净流入 |
| 同日两个重叠概念都有同一股票 | 各自展示但市场合计不重复计数 |
| 20日窗口缺1日供应商资金值 | 状态缺数据，不补0、不叫完整20日累计 |

**用户点击：**切换资金窗口 → 点日期/口径 → 查看与经营判断的分歧。若资金实源全部失败，P3不得称可真实演示。

## P4：行业、事件与股价变化解释

**Files:** `decision/industry.py`、`attribution.py`、`agents.py`、`report.py`；`test_attribution.py`、`test_industry.py`；现有报告组件内新增时间线和同周期对比。

**Consumes:** EventRecord、PriceWindow、行业指标与业务敞口。**Produces:** AttributionObservation、行业原因和待验证机制。

- [ ] 完整启用半导体设备模板并加入制造端需求对照：终端→客户资本开支→导入/验收→收入/利润→现金回收，各边分别绑定事实/假设；分部/地理区域不匹配时指出不能直接传导。
- [x] 区分WSTS产品销售与SEMI设备、名义金额与数量、月值与移动平均、预测与实现及修订版本；共同底层统计不当独立交叉核对。合同负债/产品验证不能替代完整订单/收入。
- [x] 对齐事件发布、市场/行业/个股同期数据和复权口径，计算观察差异；不把差异当事件造成的收益。（本轮设备行业指数缺项，主题ETF只作明确标记的代理；除权息未核定。）
- [x] 查询同窗口其他事件作为替代解释；资料仅有日期时按保守下一交易日处理。
- [x] 区分公司指引、历史基准、一致预期；没有可靠基准拒绝“超预期”。
- [x] 输出行业景气、个股关联、资金支持/分歧和未来验证点，并呈现依据。
- [x] 把行业结论接入P2情景及前瞻台账，新增证据导致方向变化需另存版本；领域模板经D01–D06用例验收后才标已启用。（开发及隔离样本通过，实源仅条件启用，不等于外部独立验证。）

**P4 实现状态（2026-10-13）：**功能与真实结果已交付待用户验收，记录 `RUN-DEC-d259103fe2f64d84a4e874dcecefbad2`。WSTS实际/预测版本、中芯国际制造端半年报、25条同窗标题及5/20/60日价格对照已封存。SEMI请求403，设备行业价格指数缺项，制造端规范化指标覆盖有限；模板为 `CONDITIONAL_ENABLED`，全部传导边仍为待验证机制，首项“完整启用”保持未完成。457项quant回归（含旧5.2门禁）、79项前端测试通过，原57条完成记录未变。详情与点击步骤见[本轮结果](2026-10-13-alphaagent-decision-p4-results.md)。

**明确测试输入：**10月12日18:00公告不能解释当天14:00下跌；市场/行业同期上涨且个股同步，不能只归因公司新闻；两项同时事件不能强行分配唯一贡献；除息价格下跳不能直接判断经营恶化。

**用户点击：**问“最近这段下跌与行业还是公司有关” → 看同周期对比 → 点事件时间线 → 看到主要解释与替代解释。无足够材料时明确回答无法区分。

## P5：龙头候选、条件化推荐与比较

**Files:** `decision/screening.py`、`contracts.py`、`service.py`、`report.py`；`test_screening.py`、`test_candidates_api.py`；`IndustryCandidates.vue`，连接`LiveComparison.vue`既有入口。

**Consumes:** CandidateUniverse、ScreeningInputs、经营/资金/估值证据。**Produces:** ScreeningResult及最多三只已审核研究候选。

- [x] 动态构建首行业候选池，存行业分类和成分时点；实体与业务相关性检查通过才进入。
- [x] 执行P0固定的资格、缺失/排除规则和分组排序；记录每只候选去向，缺失不等于差或安全。
- [x] 区分经营/细分龙头、交易领先者、大型代表公司；无地位证据不得升级标签。
- [x] 只深研少量候选，复用服务而不递归HTTP；全局预算与活动租约不重置。预算不足则减少候选并显示原因。
- [x] 形成入选和不选同业的比较依据，纳入估值及风险，允许零候选。
- [x] 输出研究倾向、支持/反对理由和触发条件，不给未经验证的综合胜率或自动仓位。

**明确测试输入：**资金最热但无业务相关证据者不能当经营龙头；优质经营但资金偏弱者显示分歧而非自动淘汰；两公司跨期ROE不得直接比较；全体核心资料不足时返回空候选+原因；排序重跑一致。

**用户点击：**问“这个行业未来三个月哪些公司值得优先研究” → 看池和排除原因 → 点候选依据 → 跳转双股比较 → 查看主要反对理由。

**P5 实现状态（2026-10-13）：**开发与自测完成，用户验收待进行。真实记录 RUN-DEC-cfcc09838d704db5b3f964d69bc7f07c；动态25家成分中3家通过定向核查，47次来源请求、4次模型调用、17,027 tokens。501项quant及87项前端测试通过。经营龙头地位、融资完整窗口、估值基准及完整风险档案未核定，保留条件和缺项；未宣称预测有效。页面与过程见[本轮结果](2026-10-13-alphaagent-decision-p5-results.md)。P6进度见2026-10-14的[P6验收记录](2026-10-14-alphaagent-decision-p6-results.md)。

## P6：市场扫描、主动更新和历史版本

**Files:** `decision/industry.py`、`screening.py`、`api.py`、`service.py`、`report.py`；必要的`live/store.py`适配；`test_refresh.py`、`test_market_scope.py`；`Workbench.vue`、`LibraryView.vue`、`navigation.js`、`App.vue`。

**Consumes:** 已验收行业能力。**Produces:** 明确覆盖的市场→行业→候选入口、更新差异、可恢复导出。

- [x] 扩展至少第二个业务不同的子行业，优先制造或封测，独立模板与可比性门禁；不能把设备订单/验收逻辑原样套给制造或封测。仍不能宣称已经覆盖所有热门行业。
- [x] 建立市场扫描：基础指标批量计算，行业材料缓存按新鲜度处理；模型只分析筛出的少量方向，不遍历全市场逐只调用。
- [x] 产品化P0热度计算，按20日占比/5日变化/行业ID排序，保存每次SectorSelectionRecord；覆盖不足只给已覆盖范围排名，无20日完整序列只给单项。热点切换不得暗中更换行业分类或回改开发选题。
- [x] 显示市场/行业覆盖、未接入行业和各指标日期；未覆盖全市场不能命名“全市场最优龙头”。
- [x] 刷新生成新任务，新取证据和计算，比较结论与旧判断；无新信息也记录此次检查，不伪造变化。
- [x] 完整接通20/40/60交易日观察与ForecastTracking；收益、基准、公司行为质量分开，保留未成熟、撤回、退市和获取失败；催化实现需原文，不能只因股价涨就说原因兑现。手动观察，不创建定时任务。
- [x] 贯通取消、超时、恢复、历史回看、JSON导出与新旧引用；旧单股与策略记录回归。
- [x] 不添加定时通知。用户以后明确要求时另设计订阅与权限。

**P6 当前状态（2026-10-14）：软件收尾与真实自测完成，用户验收待进行。** 新增明确的逐日有效成员协议，二十日成交占比与五日变化已用真实来源计算；旧固定当前成员池报告保留原缺口。制造候选融资与单股资金窗口按实际公布日接通；次日预测支持晚一天核对目标日历史行情，原预测不变。真实页面恢复复用 Planner，真实模型完成后三角色。全市场扫描、受限申万指数、完整事实型行业传导、20/40/60成熟结果及旧5.2独立全收益仍不冒充已取得。详见[P6收尾与P7统一验收](2026-10-14-alphaagent-decision-p6-p7-results.md)。

**明确测试输入：**相同幂等键同请求返回同任务、不同请求409；重复刷新不会双跑；取消后迟到HTTP结果不能写完成；旧报告字节和来源哈希保持；没有历史行业成分时拒绝相应历史扫描。

**用户点击：**市场入口 → 行业 → 候选 → 个股意见 → 更新研究 → 对照新旧结论 → 历史库回看导出。

## P7：统一验收、消融评测与交付

**Files:** `scripts/verify_alpha_decision.py`、评测文档、演示说明、`DeliveryView.vue`/`useDelivery.js`、版本化结果记录。

- [x] 运行36例评测，逐条保存模型输出、来源、允许/禁止答案和审核身份；未审核项目显示未审核，AI审核不写外部独立通过。
- [x] 再运行E01–E12实验门禁，共48例；查看真实预测台账、时点、不可变性、各统计分母与三基线。按日期/版本分组保留同日相关性，不把股票数量当独立日期数量。
- [x] 用同一模型和资料比较固定流程与补证流程；至少6例真实需要补证，记录错误支持率、材料覆盖、延迟和token/费用。不将降低拒答率当作提高质量。
- [x] 同模型/资料比较有无方法卡，记录适用性错误、反证发现和错误支持率；审计历史案例的模型后见知识泄漏。功能验收不等待60日标签成熟，但预测价值状态必须保持未验证直到有足够独立证据。
- [x] 固定样本硬门禁：未来信息泄漏、捏造引用/数字、估算冒充机构交易、未审核意见、缺失当零均为0；未通过就不启用推荐入口。
- [x] 运行相关quant单元/集成测试及必要旧路径回归、Ruff、前端测试构建。原有mypy/全量后端问题单独登记，只解决与本次相关的回归，不借机大改。
- [x] 浏览器验证真实单股、资金面板、行业候选、更新/恢复和导出；至少1440与390像素两种宽度、浅/深两主题。主题和图表与原型对照截图存档。
- [x] 完整演示至少一次新取数+真实模型；失败恢复至少一次。模型mock只用于隔离测试，不能作为真实模型已验证的凭据。
- [x] 更新交付页，分开软件、来源、方法审核（AI/程序/外部）与预测价值状态；5.2未通过仍明确标注。用户不必先寻找专家，亦不能把模型互审写成独立专家通过。
- [x] 给用户稳定入口、逐步点击清单和剩余问题，记录用户验收状态；没有点击反馈就写“开发者自测通过，用户验收待进行”。

**建议验收话术：**“当前哪些板块交易关注较高，为什么选半导体设备深研”；“研究中微公司未来三个月：经营为什么变化，市场反映了什么预期，行业与资金是否支持，什么事实会推翻判断”；再问“为什么选这家而没选另一家”，最后刷新、查看原观点和到期观察。答案需要可追证据，不能预录固定文本。

## 4. 交付检查表

| 项目 | 负责阶段 | 当前状态 |
| --- | --- | --- |
| 最新资料与原始出处 | P0–P1 | 真实取证、原文归档与引用自测完成；实际覆盖随任务显示 |
| 8张方法卡、行业选题与适用性 | P0–P1 | 结构化配置、AI审核及适用性门禁已实现；未做外部独立审核 |
| 下一交易日UP/DOWN、先封存后核对、准确率/基线 | P1-N | 真实签发、目标日核对、完整台账与三基线可用；探索组不填正式漏跑 |
| 经营原因和补证Loop | P2 | 已实现有界补证、复核、收束与不可变记录；预算失败保留 |
| 预期差、方向情景与首次判断台账 | P2 | 明确条件方向与三情景可用；一致预期与逆向估值缺项保留 |
| 资金指标与一致口径 | P3 | 真实融资、价格及分渠道计算；共同日期明确，缺失渠道不补零 |
| 行业/价格关联解释 | P4 | 条件传导与竞争解释可用；完整事实准入、指数及受限细分资料仍有缺口 |
| 龙头候选和条件化研究意见 | P5 | 开发与实源自测完成，待用户验收；覆盖及验证缺项保留 |
| 市场扫描、更新、版本、结果观察与恢复 | P6 | 已覆盖范围的实源闭环自测完成，用户验收待进行；不称全市场最优 |
| 48例、真实模型/浏览器/消融/审核记录 | P7 | 统一自测、真实模型、48例及6个补证对照已执行；用户验收待进行，预测价值未验证 |
| 5.2真实全市场档案 | 原独立计划 | 仍未整体完成，不由本计划替代 |

## 5. 执行中遇到缺口的处理

| 情况 | 执行动作 | 是否需要用户介入 |
| --- | --- | --- |
| 免费源超时或字段变更 | 有限重试、实测替代来源、记录口径变化；返回PARTIAL | 常规处理不需要 |
| 不可公开获得的细分数据 | 把具体缺项、影响的判断、可选服务整理成清单 | 有购买/账户选择时需要 |
| 预算不足 | 停止补证或减少候选，返回真实剩余缺口 | 默认不自动提高费用上限 |
| 对原因没有充分支持 | 删除/降级该解释，保留观察和验证条件 | 不要求用户替系统猜答案 |
| 专业口径有争议 | Codex查原文/独立定义/反例；仍不能解决就降级并记录AI审核限制 | 用户无需替系统猜答案或先找专家；若涉及新账户/费用再决定 |
| 新需求改变期限或交易范围 | 保留已批准范围，说明对数据与验收的影响 | 需要用户决定范围 |

## 6. 首个阶段结束后应交给用户什么

每阶段都附：完成内容、实际验证、尚缺数据、页面入口、三至五个点击步骤、已改文件。不要只返回“可以继续下一步”。所有阶段完成后才总结新能力整体交付；数据质量和预测有效性单独说明。

方案文件本身不包含真实买入建议、已批准龙头榜单或实时资金结论；配套资料中的半导体选择是有日期与限制的开发样本，实施时必须重新取数和验证。
