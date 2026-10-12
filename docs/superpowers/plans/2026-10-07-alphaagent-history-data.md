# AlphaAgent 回测 5.2 历史数据与年度筛选计划

> **For agentic workers:** Use executing-plans to implement the approved plan inline. Preserve the page acceptance checkpoint.

**Goal:** 将5.1静态目录检查推进为真实历史股票池／基准取数及可审计的时点筛选契约，不提前生成缺少财报版本的策略收益。

**Architecture:** 复用冻结blob、append-only审计、运行档案与Vue3原型报告。Data通过有资源上限的SDK子进程读取公开历史查询；按源请求冻结原始响应与规范化摘要。Analyst年度筛选仅接纳版本和可得时间明确的输入；数据未通过仍不生成收益。已交付5.1报告保持不变，新取数创建子任务。

**Tech Stack:** FastAPI / asyncio / subprocess / BaoStock / Pydantic / Decimal / SQLAlchemy / Vue3。

**Spec:** `docs/superpowers/specs/2026-10-06-alphaagent-redesign.md`; `docs/superpowers/plans/2026-10-07-alphaagent-backtest.md`。

## 原计划比对

运行基线、Vue3原型页面、单股闭环、追问版本及已有证券比较已交付。全市场自由比较尚未接入。回测5.1现金账本与检查已交付；5.2历史数据、财报版本与公司行为尚未齐备；5.3完整回测报告未执行。本次完成5.2中的历史名单／日历／价格基准接入与筛选契约，财报版本、公司行为、历史交易限制仍未完成。下方勾选仅表示本轮细分任务完成，不代表原计划5.2或完整回测完成。

## 约束

- 原型计算代码不得复制；现有TTM、ROE等确定性模块复用正确口径。
- 历史名单取指定日期的实际名单，不使用今天的上市状态删去过去成员；停牌股仍保留为成员。
- 证券身份用供应商代码、上市日期、交易所生命周期，不将名称匹配作为历史身份凭据；缺失／冲突生命周期必须列为未知。
- 供应商事后历史名单和报价为PARTIAL；未完成独立全量核对不得标STRICT或VERIFIED。
- 单个行情／日历查询最多三年，3–8年拆段；单次SDK批次最多24请求、每响应20000行、总输出16MiB、180秒，禁止SDK继承模型密钥。
- 当前公共数据源没有完整财报修订归档，不能靠pubDate字段冒充严格PIT。年度选股结果因此可能受阻；未知不是不满足条件，选中数量缺失不是0。
- 原始源blob仅内部存储，不导出整个供应商证券目录；页面／检查导出只含摘要、计数、覆盖、请求、哈希和缺项。
- 继续保持Vue3、多agent职责、原型图表样式、深浅主题；不把数据获取成功当成回测成功。

### Task 1: 历史请求与规范化

**Files:** `src/marketpulse/quant/backtest/history.py`, `history_worker.py`; `tests/unit/quant/test_history_data.py`。

**Interfaces:** `queries(start,end)`先生成分段calendar、stock_basic、benchmark；从实际calendar返回年度5月首个交易日及前一交易日，再生成对应前日universe查询。`summarize(packets,start,end)`验证重复、日期、代码、交易日、生命周期并输出摘要和完整性状态。

- [x] 手工日历样本核对节假日／跨年和首个执行日前一交易日；请求区间连续无重叠，起止日期差≤1096天。
- [x] 同日期股票／指数过滤依据明确证券type与沪深A股代码类别；未知类型单独计数，停牌保留，当前退市状态不排除历史成员。
- [x] 基准只查询`sh.000300`，原始价格指数points；不冒充全收益、不推算股息、不算策略超额。
- [x] 原始响应冻存在内容寻址blob，manifest含query、retrieved_at、provider、SHA及行数；规范化摘要输出可重复。
- [x] SDK仅公开固定查询，无任意代码调用；登录／查询／返回分别受资源边界保护。

### Task 2: 财报版本与年度筛选

**Files:** `src/marketpulse/quant/backtest/selection.py`; `tests/unit/quant/test_backtest_selection.py`。

**Interfaces:** `select_at(candidates,cutoff,conditions)`接受带`FinancialFact`、明确版本可得时间和STRICT来源约束的候选，不从事后最新财报推断历史。输出eligible／excluded／unavailable及具体来源和缺项。

- [x] 按cutoff选择最后已知版本，同可得时间的冲突版本拒绝；未来修订不覆盖历史已知输入。
- [x] PE需要信号日原始收盘和当日发行股数；ROE归母TTM利润除平均归母权益；营收同比按相同期间，零／负基期不可直接同比。
- [x] 手算样本TTM=100−20+30=110、权益200／220、价格10×100股，PE=1000/110、ROE=110/210。最新未来修订不进入输入。
- [x] PARTIAL来源、缺股数、未知历史成员、缺基期、单位冲突均unavailable；不误判为不满足策略。
- [x] 所有自算值仍UNVERIFIED，权威交叉核对单独门禁；本轮不把测试财报放入真实数据库。

### Task 3: 新取数任务与报告版本

**Files:** `src/marketpulse/quant/backtest/acquisition.py`; modify `backtest/api.py`, `quant/api.py`, `investigation/live_runtime.py`, `operations/watchdog.py`; `tests/integration/quant/test_history_acquisition_api.py`。

- [x] `POST /api/quant/backtests/{id}/history`以UUID幂等启动子任务，绑定原始冻结条件和窗口；GET恢复运行状态，停止／中断保留缺项。
- [x] 每个实际源查询前后记录审计状态；不可显示虚构百分比进度。完成后新检查报告加入真实历史名单、退休成员、基准覆盖及源manifest。
- [x] GET／导出重验源blob和manifest；损坏409。旧检查报告不改写。
- [x] 共用档案的新运行保留parent_run_id，历史可回看；不自动恢复为网页调查。重启后无活动执行器的历史任务显示中断并提示新取数。
- [x] 合成测试只验证接口、取消、重复提交与损坏；真实验收使用实际公开数据。

### Task 4: 页面与实际验收

**Files:** modify `useBacktest.js`, `BacktestView.vue`, `BacktestReport.vue`; add `scripts/verify_alpha_history.py`, `docs/superpowers/plans/2026-10-07-alphaagent-history-data-results.md`。

- [x] 加“获取历史股票池与基准”，展示真实源请求状态、冻结时点、年度执行日／信号日、成员／停牌／当前退市数及来源哈希，不生成假持仓。
- [x] 取数完成／失败／中断及刷新、历史回看、旧条件绑定、重复点击与重试处理。
- [x] 单元及接口测试、恢复／比较回归、前端测试／构建／Ruff／diff检查。
- [x] 新隔离真实数据库页面，获取实际3–8年所选区间的历史名单／日历／基准，六种宽度主题、导出、刷新、旧报告保留。

## 尚不能宣称完成的原计划项

全市场财报原始与修订档案、完整公司行为及历史交易限制、退市处置、基准独立交叉核对与完整回测仍须后续接入。当前供应商历史列表可证明实际返回成员，不证明其全市场覆盖完备。

SDK核对参考包内源实现与[供应商Python API](https://github.com/zxygithub/baostock/blob/master/docs/pythonAPI.md)；当前安装BaoStock0.9.4，官方主页为[baostock.com](https://baostock.com/)。不使用第三方转述作字段单位或历史版本保证。

## 本轮验收结论

执行记录见 `docs/superpowers/plans/2026-10-07-alphaagent-history-data-results.md`。股票／指数分类经真实数据回查后修正为 `history-data-v2`，既有v1报告按原分类版本复现；新任务冻结所用normalizer版本。全量生命周期查询单次等待上限75秒，其余45秒，仍受180秒总时长约束。
