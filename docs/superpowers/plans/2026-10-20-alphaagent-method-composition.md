# AlphaAgent 方法自主组合与执行解耦 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task after the user authorizes execution. Steps use checkbox syntax. 本轮仅整理方案，不启动实现、委派、模型评测或定时任务，不提交、推送或清理工作区。

**Goal:** 让模型按用户问题选择和组合已发布投资方法，逐步摆脱先选两条执行路线的研究限制，同时保留现有程序计算、风控、全部功能与历史。

**Architecture:** 新协议以组合研究计划替代固定路线规划，以独立执行合同连接确定性计算。方法能力旁挂描述、工具白名单和版本化适配器约束输入与输出；旧协议保持原路径。先交付可选的新研究结果，再解耦操作方案、接入其他业务，最后按成本与真实结果决定推广范围。

**Tech Stack:** 现有Python/FastAPI、Pydantic、DeepSeek通道、SQLite/CAS、Vue 3/Vite/ECharts、pytest与Node测试。不新增框架、向量服务或运行依赖。

**Spec:** [设计基线](../specs/2026-10-20-alphaagent-method-composition-design.md)。执行者先读设计及本方案，文中新增模块、字段和测试均为待实现内容，不能当作当前能力。

## Global Constraints

- 正式数据目录沿用`.phase3-quant-o/alpha-history-real-02`，生产入口保持`http://127.0.0.1:8839/#/`；隔离测试不得改成本机日常空库。
- 保留Vue 3、Multi-agent职责和原型风格，普通用户看结论、图表及条件，开发信息进入开发者入口。
- 不伪造实时数据、预测结果或概率；不能把方法审核当作盈利证明，不降低旧5.2回测门禁。
- 保留30条现有规则与原文版本，保持`REFERENCE_ONLY / UNVALIDATED`；能力描述独立版本化，不原地修改规则载荷。
- 保留全部旧请求、报告、模型调用、检查点和旧协议恢复语义，新研究及观察追加保存。
- 账户、资金、成本、持仓、股数及Token不进入公开模型上下文；不为自然语言理解扩大私人原文外发。
- 单投资研究硬上限继续为24次真实模型调用、60000计费/预留Token、900秒；失败和未知用量累计，恢复不清零。
- 各阶段自测通过后提供真实页面链接、点击步骤和预期结果，由用户亲自验收；工程完成不等于预测增益。
- 当前分支为`main`，已有大量暂存改动。此文不授权切分支、清理、提交或推送；开发前重新核对并按用户届时授权处理分支。

## 阶段总览

| 阶段 | 独立交付 | 放开的能力 | 保留的限制与验收重点 |
| --- | --- | --- | --- |
| M0 基线与版本边界 | 原功能、原历史、预算及对照输入清单 | 尚不改变模型行为 | 确定当前失败和限制，不把旧问题算成新改造已解决 |
| M1 方法能力映射 | 30条规则的版本化候选卡和数据能力表 | 候选不再由两条路线裁剪 | 不改原规则，不调用模型、不生成交易信号 |
| M2 自主组合研究 | 新协议可从真实页面生成完整研究报告 | 模型自主选主方法、反证、时机和风险方法 | 数值先沿用明确标识的旧计算适配器；保留标准默认，首次即可验收 |
| M3 执行合同解耦 | 方法组合与本地计算通过独立合同连接 | 多方法可组合受控观察和执行条件 | 不自由生成价位或预算，未知预算桶额度为零 |
| M4 业务贯通 | 四核心功能及市场/候选等业务按序接入 | 同一问题和连续追问沿用组合计划 | 旧页面、历史、盘前时间和数据覆盖保持原边界 |
| M5 效果与成本验证 | 同输入对照、人工证据验收和新前瞻登记 | 验证改造是否值得使用 | 不混旧试验，不以同模型评分替代真实表现 |
| M6 逐步推广 | 用户可选、版本回退及运维验收 | 有证据后扩大使用范围 | 不凭小样本自动切默认，不重写旧记录 |

主线顺序为M0→M1→M2→M3→M4→M5→M6。M0即确定M5的评测协议与成本清单；M2先做小规模真实验收，不等待全部迁移后才交付。任何阶段失败都停止推广，保留其新失败记录和原功能。

## 模块分工与拟改文件

以下相对路径以`D:/deepsearch`为根。新文件名称为本方案选定的实现接口，不表示本轮已创建这些代码。

| 模块 | 拟改现有文件 | 拟新增文件及职责 |
| --- | --- | --- |
| 规则与能力 | `src/marketpulse/quant/knowledge/research_context.py`、`rule_retrieval.py` | `method_capabilities.py`：规则版本对应的数据能力、作用、适用性与候选卡 |
| 方法组合 | `active_research.py`、`research_agents.py`、`research_context.py`、`research_service.py`、`structured_contract.py` | `composition_contracts.py`：计划、方法使用与新输出合同；`method_composition.py`：候选/计划/台账校验；`composition_service.py`：新规划与工具回填编排 |
| 数值执行 | `planning_contracts.py`、`routing.py`、`playbook.py`、`advisor_engine.py`、`portfolio_engine.py` | `execution_contracts.py`：执行合同；`execution_bridge.py`：适配器、数值引用和预算映射；旧公式实现不复制 |
| 版本与存储 | `research_contracts.py`、`research_store.py`、`research_api.py`、`evaluation.py`、`observations.py`，以及持仓/组合的contracts、service、store | 在现有存储中追加有根引用；不新建第二个日常历史库 |
| 其他研究分支 | `src/marketpulse/quant/decision/next_session/enhanced.py`、对应contracts/store；`market_enhanced.py`、`candidate_enhanced.py`；`src/marketpulse/quant/daily/runtime.py` | 按业务适配计划，不复制投资账户执行规则到明日预测或市场观察 |
| 前端与历史 | `frontend/src/App.vue`、`alpha/questionUnderstanding.js`、`researchDepth.js`、`InvestmentPlan.vue`、`InvestmentOpinion.vue`、`HoldingAdvisor.vue`、`PortfolioAdvisor.vue`、`DeveloperWorkspace.vue`、`EvaluationComparison.vue`、`EffectivenessResults.vue`，以及对应请求和图表模块 | 保留用户主界面；方法应用、版本和预算台账仅在开发者视图展开 |
| 评测 | `paired_evaluation.py`、`paired_evaluation_api.py`、`effectiveness.py`、`forward_pairs.py` | `composition_evaluation.py`：新对照协议与汇总；旧协议分派保持不变 |

只在阶段实际需要时改相应文件，不趁机重写整个投研服务。M2集中在投资研究，M4才逐个接入其他场景。

## M0：建立基线和隔离验收清单

**Files:** 读取现有`research_store.py`、`planning_contracts.py`、`playbook.py`、`advisor_engine.py`、`portfolio_engine.py`、`question_understanding.py`、`forward_pairs.py`；新增回归测试`tests/unit/quant/knowledge/test_composition_legacy.py`。

**Interfaces:** 输入为旧请求、原报告、版本和Blob引用；输出为验收清单及仅追加的基线快照，不产生新正式预测。

- [ ] 记录分支、HEAD、暂存区及工作区哈希；选定四核心功能的已完成、未完成、带旧协议样例，记录原序列化与引用。
- [ ] 使用既有配套备份机制生成数据库与CAS一致快照，在隔离目录做恢复核对；正式服务不改目录，不靠直接复制运行中的SQLite文件证明一致。
- [ ] 明确行情、财报、政策、资金、附注和执行能力的实际覆盖，区分“源无数据”“未接工具”“计算失败”和“模型拒绝”。
- [ ] 固定下文M5的案例、对照臂、成本范围、停止条件和评分表；后续变更协议另存版本，不按结果改验收门槛。
- [ ] 增加有意义的兼容断言：旧请求解析后再序列化字节不变；未知协议拒绝；旧未完成研究恢复沿用原预算；旧5.2缺项仍拒绝收益计算。

**验证命令：**在项目根运行`uv run --no-sync pytest tests/unit/quant/knowledge/test_composition_legacy.py tests/unit/quant/knowledge/test_research_recovery.py tests/integration/quant/test_backtest_readiness_api.py -q`。M0断言现有兼容和拒绝行为，不提前要求尚未实现的v5成功；新协议分派失败用例放到M2。保留原测试结果，不宣称全库类型检查通过。

**验收：**列出已核对旧链接和原载荷哈希；所有基线问题显式记录。没有模型新能力，不能将此阶段称为预测升级。

## M1：30条方法能力描述与候选形成

**Files:** Create `src/marketpulse/quant/knowledge/method_capabilities.py`、`tests/unit/quant/knowledge/test_method_capabilities.py`；Modify `research_context.py`。原`rules.py`、`rule_store.py`合同不直接放宽。

**Interfaces:** `build_candidates(knowledge_snapshot, public_context, capability_snapshot) -> CandidateSet`。CandidateSet含允许短卡、排除原因和固定哈希，`by_rule(rule_id)`可读取包含不可用/排除状态的完整审计卡；能力映射与知识快照分别固定。

- [ ] 对30条原规则逐项配置作用、必需数据、周期、主体和财报口径、证据依赖组、允许工具与执行能力。`required_inputs`自由文字不能直接被程序当作能力已具备。
- [ ] 先写并运行失败用例：非金融现金规则不能套银行；下一交易日长期规则只能用于风险解释；未发布/未来版本规则拒绝；附注未取得不能标为可用。
- [ ] 实现AVAILABLE/FETCHABLE/UNAVAILABLE/EXCLUDED四状态；注册表未知能力默认不可用，不从股票名称猜指标。
- [ ] 当前30条规模提供全部合格短卡；不按旧route_id筛卡，不用关键词前三名决定研究方法；完整规则在选用后按需展开。
- [ ] 固定候选和能力映射引用，证明原30条规则、原文和审核状态未改变。

**代表性测试合同，以下只写入文档，尚未实现：**

```python
def test_reference_rule_cannot_claim_missing_financial_notes():
    context, knowledge, capabilities = notes_missing_case()
    result = build_candidates(knowledge, context, capabilities)
    assert result.by_rule("KR-PROFIT-CASH-BRIDGE").input_state == "UNAVAILABLE"
    assert result.by_rule("KR-PROFIT-CASH-BRIDGE").can_size is False
```

`notes_missing_case()`定义在新测试文件本地，构造无附注且无注册取证能力的合成边界数据；它不是实源效果样本。该用例与金融主体、未来版本、短周期不适用、候选哈希变化用例共同验收。

**验证：**`uv run --no-sync pytest tests/unit/quant/knowledge/test_method_capabilities.py tests/unit/quant/knowledge/test_library.py -q`。

**页面验收：**开发者入口查看只读候选卡与缺口原因；用户页面及默认行为不变，本阶段不消耗新的模型调用。

## M2：自主方法组合与首个真实页面交付

**Files:** Create `composition_contracts.py`、`method_composition.py`、`composition_service.py`及`tests/unit/quant/knowledge/test_method_composition.py`、`test_composition_workflow.py`；Modify `research_contracts.py`、`research_agents.py`、`research_context.py`、`research_service.py`、`research_store.py`、`research_api.py`、`structured_contract.py`；前端修改投资研究请求及开发者展示，新增`frontend/tests/methodComposition.test.js`。

**Interfaces:** `CompositionPlan`正常状态下每证券含1—4个不同方法版本、主作用、证据问题、反证、关系及受控工具请求；`validate_plan(plan, candidates, public_context)`输出合法计划或具体错误；`MethodUse`将方法绑定到实际判断和证据，不再要求先填route_id。空方法只允许程序认可的`NO_APPLICABLE_METHOD`状态。

- [ ] 先写失败测试：未知方法、跨证券、过期版本、重复方法、可用候选存在却无主方法、强行凑齐四角色、未注册工具、任意数值参数、将同一价格事实重复计票；另验证无适用方法时如实记录，不编造规则或默改旧流程。
- [ ] 新增显式v5请求与组合协议；旧序列化不增加默认字段，新协议字段从旧计算请求中剥离。新增输出允许4项方法引用，与旧3项上限隔离。
- [ ] 新模式用一次MethodPlanner替代Researcher+Planner的重复规划，保留Analyst、Verifier与确定性发布。标准深度不追加外部取证，深入模式至多两项不同工具请求、一次规划修订。
- [ ] 所有已封存股票事实仍可用于分析，不因选主方法就删除反证；复核额外检查计划中的未解决反证问题及方法台账，不能只看到最终支持句。
- [ ] 对APPLIED/PARTIAL/NOT_APPLIED分别校验：没有证据不能标APPLIED；需披露缺口；不能仅凭规则原文代替个股事实。保持明确方向、反证和失效条件。
- [ ] M3前的数值继续复用core中旧适配器的确定性参考，明确标记为兼容试算依据，不伪装成模型已选择的新策略。无合法参考时发布已复核研究及观察条件，不生成虚假数值。
- [ ] 在开发者入口可选新协议，首页理解和用户表单确认保留。进行两组实源配对冒烟：美的集团中期、招商银行中期；记录双方基础输入一致、方法使用、调用与预算，银行不启用非金融质量计算。

**验证：**`uv run --no-sync pytest tests/unit/quant/knowledge/test_method_composition.py tests/unit/quant/knowledge/test_composition_workflow.py tests/unit/quant/knowledge/test_active_research.py tests/unit/quant/knowledge/test_structured_contract.py -q`；在`frontend`运行`npm test`、`npm run build`。

**首次用户验收：**首页明确股票→投资计划→确认表单→选新协议→生成→看结论、反证、观察条件→F5→历史回看。交付实际生成的`#/investment-research/<run_id>`链接，开发者展开方法组合及台账。冒烟失败保留，不换股票凑成功；运行前按成本章节预登记额度。

## M3：执行合同和预算映射解耦

**Files:** Create `execution_contracts.py`、`execution_bridge.py`及`tests/unit/quant/knowledge/test_execution_envelope.py`、`test_execution_risk_equivalence.py`；Modify `planning_contracts.py`、`routing.py`、`playbook.py`和研究结果组装。旧数学函数复用，禁止复制一份再逐渐分叉。

**Interfaces:** `compile_envelope(approved_research, sealed_facts, registered_components) -> ExecutionEnvelope`；`validate_envelope(envelope, public_context)`只检查公开引用与条件；账户侧`size_envelope(envelope, profile, existing_constraints)`本地核算，不把profile回传模型。

- [ ] 先写风险等价与拒绝测试：旧输入新适配器得到相同entry/risk/shares/fees；未知类别不能落入quality额度；无预算映射、无效风险距离、跨证券价位、未确认政策事实不能得到SIZABLE。
- [ ] 定义OBSERVE_ONLY、WAIT_CONDITION、SIZABLE三状态；程序错误不能改成观察状态伪装完成，合法证据缺口则是明确研究边界。
- [ ] 旧两路线注册为执行适配器；新EVIDENCE_CONDITION_V1只组合已注册观察条件与数值引用，首期不引入新的定价公式或未经验证止损算法。
- [ ] 把预算映射从研究风格名称分离。旧trend/quality值不改、不转移；新增通用桶必须新协议、用户显式配置，未配置额度为零，所有桶合计和旧总风险限制持续成立。
- [ ] 核对现金、已有持仓、单股、行业、适用相关群、流动性、费用和交易单位。多方法只能形成一份按证券汇总的新增额度，不得分别算四次仓位。
- [ ] 已有持仓的减仓/退出继续遵守可卖数量、T+1及报价口径；参考位不是成交保证。方向看多与等待可以并存，不能把两者混作审核矛盾。

**代表性拒绝测试：**

```python
def test_unknown_budget_bucket_never_uses_quality_allocation():
    envelope, profile, constraints = unconfigured_bucket_case()
    result = size_envelope(envelope, profile, constraints)
    assert result.shares == 0
    assert result.reason_code == "BUDGET_BUCKET_UNASSIGNED"
```

`unconfigured_bucket_case()`在新测试文件中明确构造有现金但未配置该桶的账户，证明“有现金”不能绕过策略额度；再用单股/行业/相关群已满及交易单位边界样例验证约束保留。

**验证：**`uv run --no-sync pytest tests/unit/quant/knowledge/test_execution_envelope.py tests/unit/quant/knowledge/test_execution_risk_equivalence.py tests/unit/quant/knowledge/test_research.py tests/unit/quant/knowledge/test_playbook.py tests/unit/quant/knowledge/test_advisor.py tests/unit/quant/knowledge/test_portfolio.py tests/unit/quant/knowledge/test_portfolio_constraints.py -q`。

**用户验收：**相同假设账户对照旧、新兼容适配器数值；再验收无映射的观察结论和明确配置后的条件性试算。账户均标注验收假设，不冒充真实用户资产。

## M4：业务接入、连续追问和历史恢复

**Files:** 按模块表修改持仓/组合contracts、service、store、engine；修改下一交易日enhanced/contracts/store、市场和候选enhanced、每日运行器；修改前端对应请求、结果及历史模块。新增`tests/unit/quant/knowledge/test_composed_advisors.py`、`tests/integration/quant/decision/test_composed_flows.py`，扩展现有前端问答与刷新测试。

**Interfaces:** 所有新业务显式携带组合协议版本和公共研究引用，私人账户保留本地；历史阅读根据保存版本分派，不根据当前界面默认值重解释旧报告。

- [ ] 先接单股持仓，再接账户调仓。固定股票身份与已有路线/预算映射；新增研究方法不能改变原成本、股数、现金。多期限研究仍按原容量分组，总预算在父任务汇总，不能每子任务各自扩大。
- [ ] 连续追问默认引用原研究计划；只改变解释重点时不重取整份研究。用户明确更新资料或重新研究才新建版本，并保留父子关系。
- [ ] 下一交易日仅允许方法解释与风险边界，保持UP/DOWN、封存窗口、真实核对及基线；长期规则不得单独成为明日涨跌信号，不混入原正式预测协议。
- [ ] 接市场与行业候选时使用各自证据能力，保留行业目录、候选池和原确定性筛选边界；不能借开放方法宣称已覆盖全部行业。
- [ ] 盘前清单与自动核对最后适配；未经本阶段验收，不自动给日常调度启用新协议。保留实际发布时间、数据截止与错过窗口记录。
- [ ] 记录新计划、能力映射、工具、执行合同的CAS根引用，验证导出和配套备份恢复；后端get核验对新旧版本分别执行。
- [ ] 普通页面保持结论、图表和条件优先，开发者展开方法角色、缺口、复核与调用；旧报告不补写新组合台账。

**验证：**在根目录运行`uv run --no-sync pytest tests/unit/quant/knowledge/test_composed_advisors.py tests/integration/quant/decision/test_composed_flows.py tests/unit/quant/knowledge/test_enhanced_advisors.py tests/unit/quant/knowledge/test_question_understanding.py tests/integration/quant/decision/test_next_session_enhanced.py -q`；前端运行`npm test`、`npm run build`。在隔离服务控制断连和恢复故障，不给正式服务注入错误。

**用户验收：**四核心功能各走“新问题生成、追问/更新、刷新/中断恢复”三条路径，合计至少12条页面流程；旧报告逐项回看并校验原载荷。市场、候选、盘前清单另列业务验收，不拿核心四项通过代替全覆盖。

## M5：方法质量、成本和真实结果对照

**Files:** Create `composition_evaluation.py`、`tests/unit/quant/knowledge/test_composition_evaluation.py`；Modify既有评测API、观测登记、forward_pairs协议分派及开发者评测页。所有新协议另立版本，不修改已登记试验的样本、日程和预算。

- [ ] 离线固定24个问题/资料案例，分别覆盖普通非金融、金融口径、证据冲突、缺数据、退出、组合约束、否定与指代。使用封存真实资料检验方法适用性；合成用例只验证安全边界，不当作实源预测成绩。
- [ ] 人工标注“可接受方法集合、必要反证、不可得输入和禁止推断”，允许多个合理组合。检验选择适用性、反证完成率、引用支持和冲突处理，不以命中唯一方法或选得更多计分。
- [ ] 先完成M2两配对冒烟，再预登记正式12个问题配对：现有六证券各20/60交易日期限，标准旧研究对比组合方法研究，两臂共享封存基础输入、截止、模型与知识版本，不新增外部材料，以分离方法组合的作用。
- [ ] 两臂完成预登记范围后，再用独立协议比较“组合仅方法”与“组合加受控补查”；工具新增资料、时间与费用单列，不把多取资料的收益全部归因于选方法。
- [ ] 隐去流程身份，人工查看完整公开输出和证据，包含本地条件与限制；盲评不能看到私人账户，同模型评阅仅作另计费辅助，不作为主要效果结论。
- [ ] 新登记20个未来交易日、每日3证券，共60个下一交易日配对，轮换既有六证券。真实日历确定开始和结束，不补过去样本。与原20配对协议分别保存、签发和计分；使用共同基础输入，迟签、失败、停牌、平盘、缺数与冲突分别记录。
- [ ] 输出配对方向差异、成熟分母、失败覆盖、始终涨/始终跌/前日延续基线、配对胜负及不确定性；同日与重复股票相关，60配对仍属试点，不推导收益优越性。

**验证：**`uv run --no-sync pytest tests/unit/quant/knowledge/test_composition_evaluation.py tests/unit/quant/knowledge/test_paired_evaluation.py tests/unit/quant/knowledge/test_effectiveness.py -q`。检查无成熟结果时准确率差为空、失败不被替换、费用未知不归零、新旧前瞻登记互不改变。

**分层通过标准：**

1. 机制门禁：兼容与风控边界测试全部通过，无已知严重事实、账户隐私或数值越界；旧5.2仍保持原拒绝与准入行为。
2. 页面门禁：M4指定页面流程全部完成，新增任务刷新与历史恢复正确；有真实外部失败则保留并修复复验，不以待核条目代替验收成功。
3. 可选发布：正式12配对中完成率不低于旧臂，人工发现方法组合解决具体证据/反证问题，且没有新增严重错误。真实成本全部可解释，不能只展示成功样本。
4. 默认推广：至少等待新前瞻计划到期核对，报告配对区间和相关性限制，预登记口径下有可支持的预测增益，再结合成本与用户验收决定；小样本评分改善不自动切默认。

可选模式的建议成本目标为端到端Token配对中位数不超过旧臂1.10倍、延迟配对中位数不超过1.15倍；这两项是本方案提议的门槛，不是已测结果或SLA。样本不足时仅报告分布，不宣称P95改善。若超出且没有可核查的质量或预测收益，减少上下文、重复规划与工具动作后另开新试验，不改原成绩。

计量未知同时给已知用量下界和保留预留上界；区间不能支持门槛就标为成本尚不能判断，不只选择成功且有完整usage的样本计算达标比例。

## 调用成本：执行前必须登记的账本

| 范围 | 限制与计量 |
| --- | --- |
| 单研究 | 保留24调用/60000Token/900秒硬上限；常规目标3个研究模型步骤，深入至多一次规划修订；最多两轮Analyst纠错，每次仍完整复核 |
| 工具 | 每研究至多两项不同工具请求，缓存键固定证券、日期、字段、来源和版本；不把一个旧日快照当作今日实时材料 |
| 首页理解 | 当前独立上限3调用/24000Token/120秒，本轮不扩大；与研究调用分列后再汇总到端到端总成本 |
| 父级账户任务 | 沿用现有分组和总容量，汇总所有子研究及语义调用；缓存必须匹配证券、期限、截止和版本，不泄漏账户资料 |
| 冒烟/正式评测 | 执行前先用封存请求和固定分词器预估完整输入、正文、复核与纠错余量；登记整轮最大调用、Token、金额估计和截止，不在执行后补额 |
| 失败与未知 | 已发送失败、传输重试、计量未知均保留调用和预留；达到父级额度停止未启动任务，登记未执行原因，不把它们从计划分母删除 |

批次预算用明确任务清单计算：研究上限为`N × 60000 Token`，如每任务另有首页理解，其上限另加`N × 24000 Token`；这是最坏边界，不是预计实际花费。M2冒烟N=4份研究，M5正式方法对照N=24份研究；两批分别登记，不能共用清零后的预算。自动前瞻每个配对签发日和整轮也登记父级上限。

执行前由当时实际模型配置、可核查供应商价格与本地校准给出预计/最坏费用，超出用户当时同意的范围先缩小**尚未登记**的试验规模再登记，不降低单任务审核。无实际账单对账时不写“实扣金额”。本轮没有运行以上批次或购买资源。

## M6：版本回退与日常使用验收

**Files:** 修改对应新协议开关、默认选择及文档；不改变旧schema的默认值和已保存请求。正式推广前复核备份、启动和日常调度说明。

- [ ] 开发者可选→用户可选→满足M5后再讨论默认；任何时候保留原标准与深入模式，新增方法组合与研究深度的两个维度在界面和请求中说明清楚。
- [ ] 关闭新任务开关后，新协议历史仍读得出，未完成任务仍按原合同和预算恢复；不降级重写原报告，不静默换模型或协议。
- [ ] 在隔离副本验证重启、导出、配套备份恢复和CAS损坏拒绝，再于正式服务按既有运维方式部署；本轮不实际部署。
- [ ] 每阶段结果记录实际页面链接、点击步骤、预期与实测、失败、费用、原载荷保留和未覆盖边界。计划中的`<run_id>`必须在执行结果中替换为真实ID，不伪造验收链接。
- [ ] 用户完成日常使用验收后再结束改造。若效果证据不支持默认升级，明确保留可选功能或缩减动作，不把“已实现”当“已证明提升”。

## 未来执行时的检查命令与评审交付

先按阶段运行新增失败测试、实现最小改动、复跑指定测试；本方案不要求对低风险文案添加镜像实现的测试。算法、版本、预算、隐私和恢复等高风险边界必须有针对性用例。

阶段合并验证在根目录运行：

```powershell
uv run --no-sync pytest tests/unit/quant tests/integration/quant -q
uv run --no-sync ruff check src/marketpulse/quant tests/unit/quant tests/integration/quant
```

在`frontend`目录分别运行：

```powershell
npm test
npm run build
```

这些是未来执行命令，本轮未运行，不宣称通过。现有mypy和容器quant安装限制按README保留；无新证据不宣称全库类型或容器验证成功。真实页面按现有浏览器验收方式操作，不用API mock替代；故障注入仅在隔离实例。

每阶段交付固定五项：**实际改动、测试证据、真实页面链接与步骤、成本/风险限制、原历史保留核对**。不自动提交或推送。当前本轮完成标准只包括方案内容、路径与文档完整性核对，不包括任何实现或效果收益。
