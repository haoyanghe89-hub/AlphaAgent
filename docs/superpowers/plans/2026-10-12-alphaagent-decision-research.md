# AlphaAgent 研究判断、资金分析与龙头筛选实施计划

> **For agentic workers:** 使用当前可用的 `executing-plans` 技能逐任务实施。用户将另开 thread 执行；本 thread 只生成方案。除非用户或适用指令明确授权，不派生子 Agent。各阶段有自测与用户页面验收检查点。

**Goal:** 面向未来1–3个月，完成“市场/行业观察 → 经营与资金原因分析 → 龙头候选 → 个股研究 → 条件化意见 → 新证据更新”的真实可追溯闭环。

**Architecture:** 保留 deepsearch 底盘、Vue 3、多 Agent 与确定性计算。新增独立 decision 包，在统一证据合同上实现有限补证循环、资金解释、候选筛选和报告；复用旧存储与比较服务，保留旧 API。

**Tech Stack:** Python 3.12–3.13、FastAPI、Pydantic、SQLAlchemy/SQLite、现有内容寻址存储与模型适配器；Vue 3、Vite、ECharts；pytest、Ruff、Node test。

**Spec:** [完整设计与合同](../specs/2026-10-12-alphaagent-decision-research-design.md)。执行者必须先读设计，再读本计划。

**状态：**所有实施任务尚未开始。本文的接口、文件名和预算为拟实施方案，不能向用户宣称已可用。

## 0. 新 thread 启动说明（可直接复制）

```text
请在 D:\deepsearch 执行以下方案：
1. docs/superpowers/specs/2026-10-12-alphaagent-decision-research-design.md
2. docs/superpowers/plans/2026-10-12-alphaagent-decision-research.md

目标：未来1–3个月的经营/行业原因分析、资金行为解释、龙头候选筛选及有证据的条件化研究意见。
先核对当前源码和工作区，从 P0、P1 开始，按纵向闭环推进。已确定免费来源优先、Vue 3、多 Agent、原型样式、不移植原型计算代码。不需要重新确认这些方向。
先以家电/美的为首个验收样例，实体和候选必须动态解析，不写死支持范围。保留既有单股、DCF、K线、比较、历史和策略功能。数字必须由工具计算，不能伪造数据/资金流/龙头依据/涨跌概率。
原5.2真实全市场档案尚未整体通过；本次不降低正式回测门禁。
每阶段先自测并报告实际结果，再给我页面链接和点击步骤验收。来源缺失明确降级，不把部分完成写成整体完成。
工作区有大量既有改动，禁止 reset、覆盖、批量清理；不自动提交或推送。不为每阶段另开端口，优先沿用8839并识别已有进程，必要重启先保留记录。
先完成一个行业可演示闭环，再扩展市场扫描。按计划执行，不无限开放工具或自动交易。不要仅写计划后停下，开始已授权的 P0、P1 工作。
```

## 1. 全局约束

- 用户当前只要求方案；实施发生在新 thread。不得在本文交付时创建新 thread、启动开发或发起定时任务。
- 业务范围、证据合同、资金定义、预算及错误码以设计文档为准，所有阶段适用。
- 项目根 `D:\deepsearch`；读取适用 AGENTS.md 和当前技能。现有 dirty tree 是前期工作，不是可删除杂项。
- 不自动提交/推送。记录每阶段实际文件和差异，避免把无关改动归入当前任务。
- 新接口不占用旧路由；旧报告不重新编号证据、不回填修改结果。页面新能力通过 capability/feature flag 渐进启用。
- 数据收集用有限速、超时和退避的适配器。凭据不回显；下载URL仅允许可信源并校验重定向、大小和类型，不接受模型任意URL。
- 初始总预算24模型调用、60,000总token、900秒，覆盖子任务与重试；最多两轮/八次新增补证工具调用、三只候选深研。调用前预留输出预算。
- 数值算法不得复制原型计算代码；手算样本、独立口径核对、同输入复算分别记录。
- 演示使用新采集的真实数据；测试夹具仅存在隔离测试环境。原始响应和完整性记录可冻结作为回归输入，但不能冒充实时源。

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
| `frontend/src/alpha/useDecisionResearch.js` | 请求与状态轮询，取消/刷新/错误处理 |
| `frontend/src/utils/decisionChartOptions.js` | 沿用原型主题的新增图表配置 |
| `tests/unit/quant/decision/` | 纯计算、合同、归因门禁 |
| `tests/integration/quant/decision/` | API、真实服务编排、存储恢复 |
| `tests/fixtures/quant/decision/` | 有许可证/来源说明的隔离夹具及评测样本 |
| `scripts/verify_alpha_decision.py` | 实际服务与浏览器验收入口 |
| `docs/alphaagent-decision-source-audit.md` | 来源试验、口径、真实可用性、缺口 |
| `docs/alphaagent-decision-evaluation.md` | 样本、指标、真实结果、费用延迟 |

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
# service.py 的对象持有 store、模型端口、工具、共享预算和取消状态
DecisionWorkflow.run(run_id: str) -> None
```

类型定义至少包含设计§6字段。补充：`ResearchScope`固定证券/行业ID与窗口；`EvidenceBundle`包含items/sources/coverage；`FlowInputs`包含各源日序列、行业成分及方法；`FlowWindowSpec`包含end_date/windows/definition_version；`EventRecord`包含发布时间及精度；`PriceWindow`包含三类同口径价格；`AttributionObservation`包含观察、窗口和限制；`CandidateUniverse`包含成员、时点和来源；`ScreeningInputs`包含候选各维证据；`ScreeningResult`包含入选/排除/不足；`VerifiedResearch`只接受通过审核的claim及其引用。复杂对象内部结构在P0的JSON Schema和测试中固定，不让各阶段自行发明字段。

每个任务遵循：先写能暴露真实错误的测试/固定样本，确认基线失败原因；实现最小闭环；执行针对性测试；更新结果记录。不要写仅照抄实现的测试，也不要为纯文字样式添加无意义测试。

运行方式（PowerShell，命令分别执行，不用跨shell拼接）：

```powershell
uv run --no-sync pytest tests/unit/quant/decision tests/integration/quant/decision -q
uv run --no-sync ruff check src/marketpulse/quant/decision tests/unit/quant/decision tests/integration/quant/decision
```

仅运行已经建立的目录；前端在 `D:\deepsearch\frontend` 运行 `npm test`、`npm run build`。新环境先阅读现有启动说明，不擅自更新锁文件。每次阶段记录所用命令、退出码、失败与处理，不引用旧测试数冒充本次验证。

## P0：基线、来源试验和验收样本

**产物：**准确能力表、真实源探测、可执行合同和24例评测清单。不是只增加空目录。

**Files:** `decision/contracts.py`、`capabilities.py`；`tests/unit/quant/decision/test_contracts.py`；两个审计/评测文档；`tests/fixtures/quant/decision/cases.json`。

- [ ] 读取设计、当前计划、`docs/alphaagent-current-state-and-next-steps.md`及当前源码；记录Git状态、服务进程、数据库/日志目录，不输出密钥。
- [ ] 确认现有真实单股/比较样本可读；原有失败单独登记，不把README早期状态当最新能力。
- [ ] 为每个拟接数据源实测小样本：公告/PDF、行业成员、日行情、融资、ETF份额与净值、供应商资金、行业统计。逐项记录HTTP/字段/期间/单位/许可/缺失。来源未知不猜测endpoint。
- [ ] 形成首期冻结的支持矩阵及新鲜度规则；有阻断的源先返回UNAVAILABLE与原因。无可靠市场行业覆盖时只启用单行业模式。
- [ ] 定义合同和Schema；只允许结构化字段，不允许客户端上传“验证通过”。固定候选池定义、业务相关性规则、流动性观察窗口和排序键，记录为规则v1。
- [ ] 建立24例清单及允许/禁止答案；12例开发、12例留出，每类均匀分布。先固定材料再评估，不按模型输出倒改答案。
- [ ] 测试缺时点、错单位、身份冲突、未知状态、非法预算、历史材料无可用性证明等情况。

**明确测试输入：**请求horizon_months=0/4应拒绝；历史cutoff早于证据available_at应排除；无source/locator的事实不能成为有效引用。

**验收：**把能取得/缺失/未验证三类数据向用户说明；资料探测失败不能以“接口预留完成”标整个P0完成。至少家电样板所需行情、公司披露及一种适用资金序列取得真实输入；其余缺口明确降级。

## P1：单股定向取证与最小报告闭环

**Files:** `decision/disclosures.py`、`industry.py`、`evidence.py`、`tools.py`、`api.py`、初版`service.py`/`report.py`；`test_evidence.py`、`test_decision_api.py`；`DecisionResearchView.vue`、`useDecisionResearch.js`。

**Consumes:** P0合同、动态证券解析、ResearchStore。**Produces:** EvidenceBundle、新任务API、可点击来源的单股报告基础壳。

- [ ] 实现定向资料类型检索：财报业务/经营讨论、业绩变动、投资者关系活动；按问题和期间选材，不能只取最新两篇。
- [ ] 分离原文、解析文本和引用片段；PDF定位页码，正文定位字符；检查主体、报告期、披露与抓取时间，保存哈希。
- [ ] 实现原始来源去重、时点过滤、有限检索；来源冲突并存，不静默覆盖。
- [ ] 新增 `/api/quant/decision` 路由并保留旧路由；新任务幂等、历史读取与导出接线。
- [ ] 接通新页面：动态输入美的和另一证券，显示材料、范围、缺口和过程；继承原型主题及引用弹窗。
- [ ] 在隔离库测试创建/读取/导出/请求冲突及证据损坏；真实源和实际模型连接分开记录。

**明确测试输入：**相同公告被两网站转载，独立来源数为1；PDF页码/文本哈希错误应报完整性错误；2026-10-12抓取且无历史版本的指标不得进入2026-09-01回放。

**用户点击：**输入单股 → 查看材料 → 点原文/片段 → 刷新页面 → 历史回看；此阶段不显示尚未接通的推荐结论。

## P2：经营解释与真正补证循环

**Files:** `decision/agents.py`、`service.py`、`report.py`；`test_claims.py`、`test_workflow.py`；`ResearchOpinion.vue`；前端`decisionResearch.test.js`。

**Consumes:** EvidenceBundle、白名单工具。**Produces:** VerifiedResearch、ResearchOpinion、轮次轨迹。

- [ ] 定义角色输出：候选解释、机制、支持/反证、缺口、审核决定；解析失败重试也占总预算。
- [ ] 确定性检查数字/引用/主体/时间；语义Verifier再审解释是否有支持，不允许模型更改计算验证等级。
- [ ] 实现NEED_EVIDENCE → 工具 → 新证据 → 修订的两轮循环；同一来源/参数去重，连续无增益停止。
- [ ] 接入共享预算、输出token预留、900秒总时限、取消与检查点；可恢复步骤不重跑，环境指纹变化禁止混合。
- [ ] Writer只消费已批准结论；新增数字和实体需后验验证。无可用判断时产出明确的INSUFFICIENT报告。
- [ ] 首页展示判断、理由、反对理由和观察条件；过程显示补查目的、新材料与观点变化，不展示隐藏思维链。
- [ ] 实际模型验证至少一个需补证的样例；把轨迹与成本保存，模型质量失败不得用规则文案冒充模型成功。

**明确测试输入：**初始只有利润下降，Analyst提出原材料涨价假设；补证发现披露主因为产品结构变化，最终必须撤回原原因；重复同一公告不得计为补证成功；预算剩余0不能再调用模型。

**用户点击：**提问“利润变化主要是什么原因，未来三个月看什么” → 看补查过程 → 点击依据 → 看到支持与反对理由。P1+P2构成第一个可用纵向交付，不等待市场扫描完成。

## P3：资金计算、解释与原型图表

**Files:** `decision/flows.py`、`flow_sources.py`、`tools.py`、`agents.py`；`test_flows.py`、`test_flow_integration.py`；`FlowAnalysis.vue`、`decisionChartOptions.js`、前端`decisionFlows.test.js`。

**Consumes:** 实测来源与FlowInputs。**Produces:** FlowReport、带方法/口径的资金证据与图表。

- [ ] 复用现有融资与行情；分别接入ETF、供应商估算适配器，返回真实覆盖、日期和来源，暂不能取得则保留显式缺项。
- [ ] 实现5/20/60交易日窗口；交易日不齐时不给完整窗口统计。公开支持子窗口时必须显示实际长度，不能假冒20日。
- [ ] 实现成交占比、上涨覆盖、相对表现、融资和ETF计算，严格执行设计§5.3；不合并不同资金量。
- [ ] 将资金作为证据输入Analyst，输出持续性、广度、分歧和替代解释。拒绝由大单指标推断具体机构意图。
- [ ] 新增图表，沿用原型底色、金色标题、网格、图例、红涨绿跌和青色证据高亮；浅色、深色、窄屏均验证。
- [ ] 人工核对一种原始数据到图表的完整计算链；用独立定义/原表核对，区别供应商自算与官方核验。

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

- [ ] 建立家电行业模板和同行可比性；分部/地理区域不匹配时指出不能直接传导。
- [ ] 对齐事件发布、市场/行业/个股同期数据和复权口径，计算观察差异；不把差异当事件造成的收益。
- [ ] 查询同窗口其他事件作为替代解释；资料仅有日期时按保守下一交易日处理。
- [ ] 区分公司指引、历史基准、一致预期；没有可靠基准拒绝“超预期”。
- [ ] 输出行业景气、个股关联、资金支持/分歧和未来验证点，并呈现依据。

**明确测试输入：**10月12日18:00公告不能解释当天14:00下跌；市场/行业同期上涨且个股同步，不能只归因公司新闻；两项同时事件不能强行分配唯一贡献；除息价格下跳不能直接判断经营恶化。

**用户点击：**问“最近这段下跌与行业还是公司有关” → 看同周期对比 → 点事件时间线 → 看到主要解释与替代解释。无足够材料时明确回答无法区分。

## P5：龙头候选、条件化推荐与比较

**Files:** `decision/screening.py`、`contracts.py`、`service.py`、`report.py`；`test_screening.py`、`test_candidates_api.py`；`IndustryCandidates.vue`，连接`LiveComparison.vue`既有入口。

**Consumes:** CandidateUniverse、ScreeningInputs、经营/资金/估值证据。**Produces:** ScreeningResult及最多三只已审核研究候选。

- [ ] 动态构建首行业候选池，存行业分类和成分时点；实体与业务相关性检查通过才进入。
- [ ] 执行P0固定的资格、缺失/排除规则和分组排序；记录每只候选去向，缺失不等于差或安全。
- [ ] 区分经营/细分龙头、交易领先者、大型代表公司；无地位证据不得升级标签。
- [ ] 只深研少量候选，复用服务而不递归HTTP；全局预算与活动租约不重置。预算不足则减少候选并显示原因。
- [ ] 形成入选和不选同业的比较依据，纳入估值及风险，允许零候选。
- [ ] 输出研究倾向、支持/反对理由和触发条件，不给未经验证的综合胜率或自动仓位。

**明确测试输入：**资金最热但无业务相关证据者不能当经营龙头；优质经营但资金偏弱者显示分歧而非自动淘汰；两公司跨期ROE不得直接比较；全体核心资料不足时返回空候选+原因；排序重跑一致。

**用户点击：**问“这个行业未来三个月哪些公司值得优先研究” → 看池和排除原因 → 点候选依据 → 跳转双股比较 → 查看主要反对理由。

## P6：市场扫描、主动更新和历史版本

**Files:** `decision/industry.py`、`screening.py`、`api.py`、`service.py`、`report.py`；必要的`live/store.py`适配；`test_refresh.py`、`test_market_scope.py`；`Workbench.vue`、`LibraryView.vue`、`navigation.js`、`App.vue`。

**Consumes:** 已验收行业能力。**Produces:** 明确覆盖的市场→行业→候选入口、更新差异、可恢复导出。

- [ ] 扩展至少第二个业务不同的行业，采用独立行业模板；不能把家电解释原样套给银行/光模块。
- [ ] 建立市场扫描：基础指标批量计算，行业材料缓存按新鲜度处理；模型只分析筛出的少量方向，不遍历全市场逐只调用。
- [ ] 显示市场/行业覆盖、未接入行业和各指标日期；未覆盖全市场不能命名“全市场最优龙头”。
- [ ] 刷新生成新任务，新取证据和计算，比较结论与旧判断；无新信息也记录此次检查，不伪造变化。
- [ ] 贯通取消、超时、恢复、历史回看、JSON导出与新旧引用；旧单股与策略记录回归。
- [ ] 不添加定时通知。用户以后明确要求时另设计订阅与权限。

**明确测试输入：**相同幂等键同请求返回同任务、不同请求409；重复刷新不会双跑；取消后迟到HTTP结果不能写完成；旧报告字节和来源哈希保持；没有历史行业成分时拒绝相应历史扫描。

**用户点击：**市场入口 → 行业 → 候选 → 个股意见 → 更新研究 → 对照新旧结论 → 历史库回看导出。

## P7：统一验收、消融评测与交付

**Files:** `scripts/verify_alpha_decision.py`、评测文档、演示说明、`DeliveryView.vue`/`useDelivery.js`、版本化结果记录。

- [ ] 运行24例评测，逐条保存模型输出、来源、允许/禁止答案、人工结论；未评审项目显示未评审。
- [ ] 用同一模型和资料比较固定流程与补证流程；至少6例真实需要补证，记录错误支持率、材料覆盖、延迟和token/费用。不将降低拒答率当作提高质量。
- [ ] 固定样本硬门禁：未来信息泄漏、捏造引用/数字、估算冒充机构交易、未审核意见、缺失当零均为0；未通过就不启用推荐入口。
- [ ] 运行相关quant单元/集成测试及必要旧路径回归、Ruff、前端测试构建。原有mypy/全量后端问题单独登记，只解决与本次相关的回归，不借机大改。
- [ ] 浏览器验证真实单股、资金面板、行业候选、更新/恢复和导出；至少1440与390像素两种宽度、浅/深两主题。主题和图表与原型对照截图存档。
- [ ] 完整演示至少一次新取数+真实模型；失败恢复至少一次。模型mock只用于隔离测试，不能作为真实模型已验证的凭据。
- [ ] 更新交付页，分开软件、来源、专业评审、预测价值四个状态；5.2未通过仍明确标注。
- [ ] 给用户稳定入口、逐步点击清单和剩余问题，记录用户验收状态；没有点击反馈就写“开发者自测通过，用户验收待进行”。

**建议验收话术：**“研究美的未来三个月：经营为什么变化、行业与资金是否支持、目前值得关注还是等待、什么事实会推翻判断”；再问“同业哪些公司值得优先研究，为什么没有选另一家”。答案需要可追证据，不能预录固定文本。

## 4. 交付检查表

| 项目 | 负责阶段 | 当前状态 |
| --- | --- | --- |
| 最新资料与原始出处 | P0–P1 | 未实施 |
| 经营原因和补证Loop | P2 | 未实施 |
| 资金指标与一致口径 | P3 | 未实施 |
| 行业/价格关联解释 | P4 | 未实施 |
| 龙头候选和条件化研究意见 | P5 | 未实施 |
| 市场扫描、更新、版本与恢复 | P6 | 未实施 |
| 真实模型/浏览器/消融/人工评审 | P7 | 未实施 |
| 5.2真实全市场档案 | 原独立计划 | 仍未整体完成，不由本计划替代 |

## 5. 执行中遇到缺口的处理

| 情况 | 执行动作 | 是否需要用户介入 |
| --- | --- | --- |
| 免费源超时或字段变更 | 有限重试、实测替代来源、记录口径变化；返回PARTIAL | 常规处理不需要 |
| 不可公开获得的细分数据 | 把具体缺项、影响的判断、可选服务整理成清单 | 有购买/账户选择时需要 |
| 预算不足 | 停止补证或减少候选，返回真实剩余缺口 | 默认不自动提高费用上限 |
| 对原因没有充分支持 | 删除/降级该解释，保留观察和验证条件 | 不要求用户替系统猜答案 |
| 专业口径有争议 | 提供原文和计算，记录待审核，不升级已验证 | 可请用户或专业人员复核 |
| 新需求改变期限或交易范围 | 保留已批准范围，说明对数据与验收的影响 | 需要用户决定范围 |

## 6. 首个阶段结束后应交给用户什么

每阶段都附：完成内容、实际验证、尚缺数据、页面入口、三至五个点击步骤、已改文件。不要只返回“可以继续下一步”。所有阶段完成后才总结新能力整体交付；数据质量和预测有效性单独说明。

方案文件本身不包含任何真实买入建议、当前龙头榜单或实时资金结论；实施时必须重新取数和验证。
