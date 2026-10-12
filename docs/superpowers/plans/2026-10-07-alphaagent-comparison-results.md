# 阶段 4：双标的比较闭环验收

日期：2026-10-07。沿用 Vue3 和 deepsearch 多职责支路；没有复制原型计算代码，没有提交或推送。

## 实现与范围

- 工作台识别两只股票，保留问题中出现的顺序；要求选择两股起止日、研究时点和复权口径一致的历史冻结窗口。指定年份没有共同数据时，不自动改成另一年份。
- Supervisor 协调两个独立研究支路，复用各自 Data / Analyst / Verifier / Writer 执行与预算。取消主任务会停止活动支路，不启动下一支路。支路受阻会生成部分报告，不拼凑比较数值。
- 按原型报告结构提供结论速览、关键指标五维对照、两个标的的深研要点／财务图、风险与局限、左侧元信息／目录。沿用原型 CSS 与图表组件契约，并保留浅深主题与窄屏适配。
- 比较层并列已有数值，不产生新财务指标、综合评分或“领先维度计数”。期间、单位、公式一致为口径 MATCH；不一致为 METHOD_DIFFERENCE；缺项为 UNAVAILABLE。这些与原数值 VERIFIED / UNVERIFIED 分开显示。
- 第一季度的累计同比可作为单季同比；其他累计期间不能冒充单季。所选期间不满足一年时，不用区间收益冒充一年动量。净现比、PE 历史分位和单季金额仍明确显示缺项。
- 每个可点击数值保留所属原支路的 artifact、metric_path、cell_hash、输入与 citation。比较材料内容寻址归档，报告输入快照绑定材料；读取或导出时核对原支路与最新验证链，漂移返回 409。
- 主比较任务进入历史库，子任务从过程／报告页回看；Markdown 导出包含原精度、期间、定义、验证状态及源报告与验证记录链接。

## 实际页面

新预览：`http://127.0.0.1:8835/`。旧 8834 预览保留。

私有数据库：`.phase3-quant-o/alpha-comparison-real-01/metadata.sqlite`；新建数据库仅复制既有真实冻结输入，没有伪造行情或复制旧任务的执行环境。预览辅助进程 PID 24372。

- 主比较：`RUN-QUANT-3719721404934bba`；报告 `RPT-db743bbefb64`。
- 美的支路：`RUN-QUANT-6a6d3f77ddb84a91`。
- 茅台支路：`RUN-QUANT-2ee9233ac37c424f`。
- 共同窗口：2023-06-15 至 2024-06-14，241 个交易日；研究时点 2024-06-15；前复权冻结代理。属于历史回溯观察，不是当前行情或 STRICT PIT。
- 两股成长性、TTM 平均归母权益 ROE 与一年价格收益的期间／单位／定义对齐；所有原验证状态仍为 UNVERIFIED。净现比、历史 PE 分位未生成。

## 自测证据

1. `pytest tests/unit/quant tests/integration/quant tests/integration/investigation/test_phase5_assembler.py tests/unit/investigation/test_phase5_reporting_models.py`：119 passed，321.16 秒。
2. `pytest tests/integration/investigation/test_phase5_report_pipeline.py tests/integration/investigation/test_live_runtime.py tests/unit/investigation/test_phase5_writer_validation.py`：34 passed，40.69 秒。以上合计 153 项相关后端测试；仅既有 anyio 弃用警告。
3. `npm run test`：48 passed；`npm run build`：通过。最终 CSS `index-CSF4bNoF.css`、主 JS `index-CMLfLHDs.js`。
4. Ruff 检查相关量化、报告、运行服务、测试与浏览器脚本通过；`git diff --check` 无空白错误，仅既有 LF/CRLF 提示。
5. 浏览器 `scripts/verify_alpha_comparison.py` 从工作台实际提交比较，完整跑通执行→报告。375 / 768 / 1440 × dark / light 六种布局，每种点击两股来源弹窗，检查对应 manifest 与输入，刷新与历史回看，Markdown 导出；无页面 JavaScript 错误、无文档横向溢出。所有比较单元格逐项等于原支路单元格，验证状态没有升级。
6. 两股独立离线重算，产物 JSON 与 SHA256 逐字节一致：美的 `9721c3d3b987ebcbc681b5750a535c6061c90af08fa5c1cc82a78670aea074ac`；茅台 `90c56f4f566e4f2fc25f635c349f689d4ad475717ddd6f78409e81921b8d0dd8`。复现性不等于财务口径已获权威验证。

浏览器记录／截图：`.phase3-quant-o/alpha-comparison-browser-03/summary.json` 及同目录 PNG；离线重算记录：`.phase3-quant-o/alpha-comparison-real-01/reproduction.json`。

本步不新增财务指标或权威核验结论，继续保留原始校准与来源局限。DCF／K线可从各自原研究回看；比较追问与更多股票、现金流和 PE 历史域、策略回测尚未在本步扩展。

## 用户点击验收

1. 打开主比较报告，点击五维表中两股数字，查看不同来源、期间与公式。
2. 展开补充观察与两个标的依据链；从链接回看完整原研究、K线与 DCF 适用状态。
3. 切换主题、刷新、在历史库回看和导出 Markdown。
4. 工作台输入“贵州茅台 vs 美的集团”，确认双方顺序与共同冻结窗口；指定无数据的年份应禁止启动，不静默换期。

等待用户点击验收，再进入策略回测账本阶段。
