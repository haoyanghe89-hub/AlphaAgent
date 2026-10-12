# 策略回测 5.1 交付与验收

本轮遵循用户“先按照原计划”的选择进入第五阶段。完成的是 **5.1 现金／持仓账本内核及历史数据准入检查**，不是完整策略回测。动态全市场股票比较继续作为独立待办。

## 可点击页面

- 新工作台：<http://127.0.0.1:8837/#/backtest>
- 已自测任务：<http://127.0.0.1:8837/#/backtest/RUN-BT-56f89435677d45de>
- 来源数据库：`.phase3-quant-o/alpha-backtest-check-02`；后台 PID 46900。
- 深浅主题、375/768/1440 截图：`.phase3-quant-o/alpha-backtest-browser-03`。
- 页面原型表单、选股条件、五项绩效、四系列净值、年度表现／持仓和方法说明保留。数据检查与任务记录为新增真实状态，不改图表配置。

人工点击：

1. 打开新工作台，切换预设或修改 PE、ROE、营收同比、年数及成本，点击“确认条件并执行”。
2. 查看“数据检查完成 · 回测受阻”，展开“查看冻结输入与来源”，核对真实快照、期间、PIT 状态、哈希；导出检查报告。
3. 刷新页面，进入历史库，再打开该回测记录。修改表单但不提交时，下方旧报告仍显示原任务条件；重新提交创建新任务。

## 已完成

### 独立现金交易账本

`src/marketpulse/quant/backtest/ledger.py` 使用 Decimal，在明确的原始开／收盘价格、目标权重、有效手数及方向限制下先卖后买，保留实际现金、持仓、成交与拒单原因。禁止杠杆；一次调仓／交易日，信号必须早于执行日；不得根据未来收盘选股。缺少持仓标的价格、未知交易状态、重复交易日／标的与不合法权重拒绝执行。

人工合成样本：1000 元初始资金，10 元买入90股，单侧1%综合情景成本，买入成本9元，现金91元；次日11元全部卖出，成本9.9元，期末1071.1元。零成本重算为1100元。相同输入输出及哈希一致，低精度调用者 Decimal 环境不会改变结果。**测试样本没有进入真实验收数据库。**

这是无公司行为窗口的内核；公共接口不接受用户声明“无公司行为”来绕过历史数据核验。尚未实现公司行为账务、历史财务筛选、独立基准及完整回测计算。综合情景成本也不是历史实际佣金、印花税或滑点。内核结果始终 UNVERIFIED，手算和复算不能替代权威口径核对。

### 真实数据检查任务

接口：`POST /api/quant/backtests`、`GET /api/quant/backtests/{run_id}`、`GET /api/quant/backtests/{run_id}/export`。

检查读取真实冻结存储并验证快照清单、分区和源哈希。条件、时点、报告采用 canonical JSON 内容寻址与不可变审计；源快照附带档案所有权，报告 blob 使用显式 blob URI，支持档案共享保留和垃圾回收扫描。相同请求 UUID 重试返回原任务；不同条件复用 UUID 返回409。后续新增证券目录不会改写旧报告。审计篡改被数据库 append-only 约束拒绝，损坏 blob 时读取和导出均409。

该任务真实检查了3个证券定义和22份区间相关快照，发现七项准入限制：历史股票池、财报最初／修订版本、完整行情覆盖、公司行为、历史成交限制、沪深300基准、独立指标核对。财报为部分可用、行情覆盖不足，其他尚无准入输入。没有生成绩效、净值、持仓、胜率或 Sharpe。

检查职责完成与回测成功分别记录：`check_status=COMPLETED`、`execution_status=BLOCKED`；调查运行终态为FAILED，解释为数据尚未准入。Planner、Data、Writer 的实际本地职责已完成；Analyst 明示未执行，Verifier 明示受阻。不伪造进行中的研究任务或进度。

额外修复：watchdog 不自动把该终态数据检查任务恢复为网页／模型调查；手动通用恢复也返回 `BACKTEST_INPUTS_UNAVAILABLE`。补齐数据必须创建新检查，保留原有不可变报告。

## 新鲜验证

1. `pytest tests/unit/quant tests/integration/quant tests/integration/investigation/test_phase5_assembler.py tests/unit/investigation/test_phase5_reporting_models.py -q --basetemp=.phase3-quant-o/backtest-all-01`：**127 passed**，228.64秒。
2. 随后发现并修复 watchdog 对新任务的误判，针对最终相关代码运行：`pytest tests/unit/quant/test_backtest_ledger.py tests/integration/quant/test_backtest_readiness_api.py tests/integration/investigation/test_auto_recovery.py tests/integration/investigation/test_live_runtime.py tests/integration/investigation/test_phase5_report_pipeline.py tests/unit/investigation/test_phase5_writer_validation.py -q --basetemp=.phase3-quant-o/backtest-recovery-regression-01`：**54 passed**，47.90秒。两组包含重叠测试，不相加冒充独立测试数。
3. 前端 `npm test`：**50 passed**；`npm run build` 成功，最终 JS `index-BVbiSDBE.js`，CSS `index-D4QJpVYk.css`。
4. 本次变更 Python 文件 Ruff 通过；`git diff --check` 通过，只有既有 LF/CRLF 提示。
5. 最终 `scripts/verify_alpha_backtest.py --url http://127.0.0.1:8837 --output .phase3-quant-o/alpha-backtest-browser-03`：真实条件提交、原条件绑定、刷新、历史回看、幂等重试、冲突拒绝和导出相等均通过；**6种宽度／主题，0页面脚本错误**，无文档横向溢出。已人工查看1440深色、375浅色截图。
6. 最终后台无数据检查被自动恢复的告警。只有22份原有真实快照、3个证券定义及原始官方年报被复制，快照ID相同；不复制旧计算环境、旧计算结果或任何测试生成的交易结果。

pytest 保留一项已有的 anyio BlockingPortal 弃用提示。没有宣称后端全量测试或 mypy 全部通过；它们的历史问题仍按原计划单独记录。没有提交或推送本轮代码。

## 剩余阶段

5.2 必须取得并审核历史证券范围／退市样本、财报可得版本、公司行为、按生效日期的成交规则及同区间基准。5.3 才将年度选股、完整账本、分红与税费、三组成本独立重算、五项绩效和权威交叉核对联通至原型报告。当前页面受阻准确反映数据能力，不能换成假数据来满足图表。

规则参考采用官方资料：[上交所交易规则（2026年修订）](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)。历史回测需按当时有效版本，不能将2026规则回填所有历史年份。基准收益口径参考 [中证沪深300指数编制方案](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000300_Index_Methodology_cn.pdf)，价格与全收益分开准入；当前尚未取得该区间独立基准序列。
