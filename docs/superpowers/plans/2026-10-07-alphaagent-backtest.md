# AlphaAgent 策略回测实施计划

> **For agentic workers:** Use executing-plans to implement the approved plan inline, with a page acceptance checkpoint after each increment.

**Goal:** 独立实现可审计的策略回测；本次先交付 5.1 交易账本内核与真实数据检查闭环。

**Architecture:** 复用 FastAPI、调查档案、冻结存储、审计事件及 Vue3 原型页面。Planner 确认条件，Data 检查数据，Analyst 仅在数据通过后调用确定性计算，Verifier 独立核对，Writer 展示结果与缺项。交易账本模块是纯函数，不接模型或网络。

**Tech Stack:** Python Decimal / Pydantic / SQLAlchemy / FastAPI，Vue3 / ECharts，pytest / Playwright。

**Spec:** `docs/superpowers/specs/2026-10-06-alphaagent-redesign.md`

## 约束与阶段边界

- 原型计算代码不得移植或复制；只复用已批准的展示契约。
- 用户本次选择沿原计划进入策略回测，动态全市场比较接入保持独立待办。
- 当前已有数据只有事后历史行情、部分财报，`DataRequest` 单次最多三年；原型 3–8 年须分段取得，不放宽单次资源边界。
- 当前存储尚无历史股票池、完整公司行为、历史成交限制、独立沪深300基准及完整财报修订档案。不能把当前三只股票当成历史全市场，也不能升级 PARTIAL / UNAVAILABLE 为 STRICT。
- 本次 5.1 检查任务必须真实执行、持久化、可刷新、可从历史库回看和导出。受阻任务不生成绩效、净值和持仓。检查结束与回测成功分别标记。
- 内核输入是显式冻结的逐日原始价格、交易限制及已确认的目标仓位；它不实现或宣称完成历史财务选股。合成测试输入绝不加载到验收数据库。
- 5.2 才接历史股票池、版本化财报与公司行为、年度筛选及基准；5.3 接受数据包后联通隔离计算、指标与独立交叉核对，生成原型四条净值、年度持仓与导出。未完成的任务不勾选。

### Task 1: 现金与持仓交易账本

**Files:** Create `src/marketpulse/quant/backtest/ledger.py`; test `tests/unit/quant/test_backtest_ledger.py`.

**Interface:** `replay(sessions, bars, targets, initial_cash, cost_rate) -> dict`; bars are typed `Bar`, targets typed `Target`. Outputs daily cash/holdings/equity, executed/rejected orders and a canonical input/output hash. Prices are raw CNY/share; `cost_rate` is an explicit all-in scenario assumption, not an inferred historical tax schedule.

- [x] 手算测试：1000 元，10 元买 90 股（10 股一手的合成规则）、1% 成本，现金 91；11 元卖出，卖出成本 9.9 元，期末 1071.1 元；逐笔现金与持仓守恒。
- [x] 边界测试：T+1、暂停买卖、涨跌停方向限制、零选中持现金、未知交易状态与持仓缺价拒绝、信号晚于成交拒绝、重复标的拒绝、相同输入哈希一致。
- [x] 独立实现先卖后买、整数手数、按调仓前权益分配目标、不得透支、拒单不自动补成交；明确暂不处理公司行为，调用者须提供经过确认的无公司行为窗口，公开 API 不接受此声明作为数据核验。
- [x] 全部账本测试通过，使用 Decimal 局部精度使调用者全局上下文不影响输出。

### Task 2: 冻结数据检查及审计任务

**Files:** Create `src/marketpulse/quant/backtest/readiness.py`, `src/marketpulse/quant/backtest/api.py`; modify `src/marketpulse/quant/api.py`; test `tests/integration/quant/test_backtest_readiness_api.py`.

**Interfaces:** `POST /api/quant/backtests` accepts prototype PE/ROE/growth/years/cost conditions and a retry UUID. `GET /api/quant/backtests/{run_id}` restores the frozen check. `GET .../{run_id}/export` returns an integrity-checked JSON report.

- [x] 按当前真实数据库检查目录、快照、时间覆盖、财报 PIT 等级；列出快照 ID、语义哈希、来源、时间、质量标识，不复制原始供应商数据。
- [x] 所有缺项分别显示 UNAVAILABLE / INSUFFICIENT / PARTIAL；未知历史股票池、公司行为、成交限制、基准不因出现某字段而标记通过。
- [x] 请求 UUID 幂等；相同 UUID 不同条件 409；服务重启可恢复，无活动假任务；失败不是 COMPLETED。
- [x] 将条件及检查报告 canonical JSON 绑定内容寻址 blob 和审计记录；读取／导出检查哈希，篡改返回 409；以后新增快照不改写旧检查。
- [x] 历史列表支持 backtest 场景，单股／比较历史回归通过。

### Task 3: 原型页面提交与回看

**Files:** Modify `frontend/src/alpha/BacktestView.vue`, `BacktestReport.vue`, `navigation.js`, `Workbench.vue`, `LibraryView.vue`, `App.vue`, `model.js`; Create `frontend/src/alpha/useBacktest.js`; test `frontend/tests/backtest.test.js`.

- [x] 保留原型表单、五项绩效、四条曲线、年度表现／持仓、方法说明及深浅主题；开放执行按钮，明确此次执行是回测前数据检查。
- [x] 条件提交、重复点击／响应丢失重试、后续条件变化新 UUID、加载与失败、刷新恢复处理；条件与旧任务的输入绑定，不将新表单条件冒充旧结果。
- [x] `#/backtest/{run_id}` 深链接及历史回看；报告显示实际检查时间、范围、源快照及缺项，未生成数字保持空缺。
- [x] 检查受阻不显示虚构进度；界面显示 Data 的真实检查完成、Analyst 未执行、Verifier 未验证和 Writer 的检查报告记录。

### Task 4: 验收与真实页面

**Files:** Create `scripts/verify_alpha_backtest.py`, `docs/superpowers/plans/2026-10-07-alphaagent-backtest-results.md`.

- [x] 复用已有真实冻结输入建立独立验收数据库，不迁旧计算环境／结果，不添加合成交易结果。
- [x] pytest（账本／API／单股比较回归）、前端测试、构建、Ruff、diff 检查。
- [x] Playwright：真实条件提交、缺项结果、重复请求、刷新、历史回看、导出；375/768/1440 深浅主题六种版式无页面错误。
- [x] 提供可点击页面与步骤，报告 5.1 完成项、真实数据受阻项及后续未完成项；不宣称第五阶段全部完成。

### Task 5.2: 历史数据包与筛选（历史名单／日历／价格基准及筛选契约已接入，整体未完成）

单股历史输入核验闭环已接入：完整窗口原始价格、按除权年份分红、所选年度三期财报披露日期观察；详见 `2026-10-07-alphaagent-input-audit-results.md`。这是已接入证券的样本核验，不替代以下整体准入。

- [ ] 历史全市场范围及完整性准入仍待完成。已接入BaoStock指定日期沪深名单与当前生命周期目录，停牌、退市和冲突分别保留；原响应冻结为PARTIAL，不能据此声明全市场完整。细分验收见 `2026-10-07-alphaagent-history-data-results.md`。
- [ ] 冻结逐期财报最初披露和各次修订，使用成交前可得版本；TTM、平均权益 ROE、营收同比独立手算及原披露交叉核对。
- [ ] 公司行为、有效日期交易规则、逐日停牌／涨跌停／开盘成交限制仍待接入；沪深300官方价格／全收益各1211日已冻结，价格与父BaoStock逐日1211项MATCH；全收益独立核对仍缺失。成本继续是情景假设。

2026-10-09补充：完整分区包的纯函数准入内核与年度筛选契约已完成开发及合成测试；真实全市场档案尚未取得，没有完整包通过准入。官方基准核对及本轮缺项详见 `2026-10-09-alphaagent-52-results.md`；不能将该内核、单股或基准通过等同5.2整体验收。

### Task 5.2A: 官方财报口径核对（2026-10-09继续）

在现有单股输入核验版本上创建子任务，冻结已复核的美的2023Q1、2023全年、2024Q1中文官方PDF。复用原历史采集预算、停止、重启中断、内容寻址、父版本与UUID绑定。其他证券及年度缺少复核配置时明确拒绝，不套用该样本。

- [x] 官方原文金额、千元转元、归母／合并／少数股东、报告期和页码分别核对；文档哈希及独立转录检查点不符即拒绝。
- [x] 核对结果分别MATCH、CONFLICT、METHOD_DIFFERENCE、UNAVAILABLE；同比容差固定为0.00005（比例），披露货币舍入容差固定1000元。
- [x] 复用独立累计TTM及同比引擎，手算固定样本、官方披露同比、同输入哈希复算分别验收。TTM无独立已公布基准时保持UNAVAILABLE／UNVERIFIED；不放行历史选股。
- [ ] 页面入口、三期原文、口径表、公式和版本缺项已实现，父报告不变，刷新／导出／重试／停止和六种布局开发者自测通过；待用户点击验收。见 `2026-10-09-alphaagent-filings-results.md`。5.2整体及5.3不因此完成。

### Task 5.3: 完整回测报告（2026-10-11软件已接通，真实全市场数据仍待准入）

用户明确允许缺失数据预留，先完成原计划软件功能，替代此前“全部5.2档案取得后才能写5.3”的顺序限制；严格数据准入保持。

- [x] 年度信号按上一交易日收盘已知数据筛选、下一交易日开盘执行，空选股持现金；含退市的数据结构和现金结算执行路径已实现。合成样本验证不替代真实退市样本验收。
- [x] 固定禁网子进程执行公司行为账本、费用及基准；0／用户成本／0.3% 各自重算。登记、除权、应收、支付、待上市股份和零股规则分别处理；特殊条款不明继续拒绝。
- [x] 五项绩效、跨年基点、年度名单、逐笔账本、来源哈希、原型四曲线与深浅主题已接通；无风险假设显式。
- [x] 独立参考适配、预设容差和同输入离线复算分开实现；通过冻结原文复算，不依赖可变目录。隔离测试服务已覆盖完整页面路径，合成包不进入演示库。
- [ ] 全市场真实完整包、全收益与策略的外部独立参考、用户页面点击验收。未知持有期税额、碎股／特殊合并条款不能称为已支持。

详见 [功能收尾与验证](2026-10-11-alphaagent-functional-results.md)。

2026-10-09免费来源第一批已接入五份官方生命周期资料，页面自测及重启回放通过。当前目录只是回溯观察，5.2整体及5.3状态不变；用户页面验收待进行。详见 `2026-10-09-alphaagent-free-sources-results.md`。
