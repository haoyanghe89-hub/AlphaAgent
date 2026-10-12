# AlphaAgent 5.2 财报／公司行为／交易输入核验实施计划

> **For agentic workers:** Use executing-plans inline for the already authorized next step. Preserve the user page acceptance checkpoint.

**Goal:** 为已接入证券提供真实、可追溯的全窗口原始行情／分红送转查询和单个年度信号日的财报样本检查，明确严格PIT及可成交性缺项。

**Architecture:** 复用历史采集子任务、固定SDK子进程、资源预算、append-only审计、内容寻址blob和原型Vue3报告。在同工作流中冻结输入核验target，保留父历史数据报告；核验摘要单独呈现，不把样本覆盖升级为全市场或成功回测。

**Tech Stack:** FastAPI / asyncio / BaoStock / Decimal / SQLAlchemy / Vue3。

**Spec:** `docs/superpowers/specs/2026-10-06-alphaagent-redesign.md`; `2026-10-07-alphaagent-backtest.md`。

## 约束与范围

- 原型计算代码禁止复制；不新增未经权威核对的PE／ROE／策略收益。Vue3、多职责边界与原型现有版块不改变。
- 一次用户选择一个已主数据化证券及父历史报告中的一个年度信号。原始行情覆盖父报告完整3–8年区间，分红按除权年份逐年查询；财报样本是信号年前一年Q1、全年Q4和信号年Q1。
- 这只是逐证券的历史输入核验，不宣称全市场财报／公司行为／交易限制覆盖。缺少财报最初／修订原文、权益／同日股数、完整配股与退市处置、涨跌停和成交约束时，仍BLOCKED / PARTIAL / UNVERIFIED。
- `tradestatus`和`isST`不推导buy_allowed／sell_allowed；涨跌幅不推导涨跌停。一日无成交或零行情不能当成可交易，也不能自动生成交易。
- 单批最多24实际查询、180秒、16MiB原始响应；单段起止差≤1096日。空财报／分红返回只证明供应商未返回记录，不证明不存在记录。
- 元数据和摘要可导出，完整供应商响应仅内部冻结。新任务绑定父报告、主数据定义哈希、代码生命周期、范围、年度信号和日历源；旧报告不改写。
- 用户数据源偏好已异步询问，未取得授权档案时继续公开来源，并保留严格PIT受阻。不得请求或输出密钥。

### Task 1: 固定真实查询与独立摘要

**Files:** extend `backtest/history_worker.py`; create `backtest/inputs.py`; `tests/unit/quant/test_backtest_inputs.py`。

**Interfaces:** `input_queries(target)` returns fixed stock_daily / dividends / profit queries. `inspect_inputs(packets,target)` validates code, dates, OHLC/status, dividend units/dates, financial period/publication dates and returns source manifests, coverage, event summaries, financial availability observations and explicit gaps.

- [x] 单元测试正常原始行情、停牌零值、未知ST、不完整日历覆盖、错误代码／复权／重复／不合理OHLC拒绝。
- [x] 测试每股税前分红、红股／转增比例和除权／支付／上市日分开；缺值不补0，空返回不当作无事件；查询是operate年份。
- [x] 财务按实际statDate与pubDate核验，未来披露和信号日时间未知分别标记；没有版本归档不得STRICT，不将供应商ROE和净利润当成自算归母口径。
- [x] SDK白名单扩展为固定方法，字段／代码／日期／年份／季度严格有界；网络异常和空响应保留真实状态。

### Task 2: 子任务／完整性／生命周期

**Files:** extend `backtest/acquisition.py`, `backtest/api.py`; `tests/integration/quant/test_backtest_inputs_api.py`。

- [x] `POST /api/quant/backtests/{id}/inputs`接受request_key、instrument_id、signal_year；必须是父报告目录及年度列表内的选择，不接受任意供应商代码或扩大期间。
- [x] 冻结代码别名在整个期间内生效且唯一；日历来自已冻结父源；目标blob及主数据哈希验证，幂等UUID绑定所有选择。
- [x] 共用查询预算、进度、停止、重启中断与审计；GET／导出重验原始源、目标、父报告和摘要。失败保留部分真实输入，旧报告字节不变。
- [x] 保持旧history-data-v1/v2读取和原历史采集回归，不把核验任务恢复为网页任务。

### Task 3: 页面与真实验收

**Files:** modify `useBacktest.js`, `BacktestView.vue`; create `BacktestInputs.vue`; create `scripts/verify_alpha_inputs.py` and results doc。

- [x] 选择已接入证券和年度，点击“核验股票历史输入”；新报告展示完整区间行情覆盖、停牌/ST/未知数、分红明细、三期财报披露观察、源哈希与准入缺项。
- [x] 条件编辑不冒充冻结结果，重复点击／响应丢失同UUID，重新核验生成新版本；刷新、导出、父版本回看、停止支持。
- [x] 合成数据只用于测试；实际页面使用公开查询和已有真实冻结源。相关后端／前端测试、构建、Ruff、六种布局及点击验收。

## 尚未完成的第五阶段

该闭环完成后，仍须接入全市场财报原始与修订档案、全量公司行为、按生效日期的交易规则、退市处置和独立权威核对，之后才允许年度选股、真实成交及5.3绩效曲线。当前下一步不能宣称这些数据已经齐备。

## 本轮结果

以上单股核验闭环已完成自测，真实页面等待用户点击验收。证据与剩余边界见 `2026-10-07-alphaagent-input-audit-results.md`；5.2整体及5.3仍未完成。
