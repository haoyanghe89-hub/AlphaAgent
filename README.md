# AlphaAgent：AI 量化投研

前端采用 Vue 3、Vite 和 ECharts，按 AlphaAgent 原型重建工作台、研究过程、报告、个股、历史库及策略回测入口，支持浅色／深色切换。后端沿用 deepsearch 的 FastAPI、证据归档、持久化状态、Harness、确定性计算及发布门禁。

日常入口为 **http://127.0.0.1:8839/#/**。本机已配置Windows登录后后台自启、每天21:00完整备份及错过补做，沿用`alphaagent.local.json`绑定的已有SQLite／CAS历史库。手动启动仍可运行`start-alphaagent.ps1`，不会为新开发阶段另建空库。完整启动、每日更新、备份与迁移说明见 [日常运行](docs/alphaagent-daily-operation.md)。

四项核心功能的真实页面全流程记录见 [统一系统验收](docs/superpowers/plans/2026-10-17-alphaagent-system-e2e-results.md)。日常使用统一入口和历史库，无需阶段或验收参数。

展示统一后再次从首页输入新问题，四项生成、刷新恢复、历史回看及账户追问记录见 [新问题实跑验收](docs/superpowers/plans/2026-10-17-alphaagent-daily-use-e2e-results.md)。

用户页面按结论、操作条件和图表组织；完整理由、反证和来源可展开。已有结果的输入区默认折叠，修改后仍另存新记录。右上角 [开发者入口](http://127.0.0.1:8839/?developer=1#/developer) 集中显示调用、核验、知识版本及数据接入；页脚可在同一报告的用户／开发者视图间切换。展示验收见 [日常展示统一](docs/superpowers/plans/2026-10-17-alphaagent-presentation-results.md)。

## 日常使用

- 工作台可提问任意可核实沪深A股的明日预测、未来几个月研究、市场观察、行业候选，也可进入投资计划填写周期和可选的本地资金／持仓约束。
- 持仓顾问沿用统一知识体系，结合本地成本、股数和仓位回答持有、减仓与退出条件，支持价格情景追问、输入更新和历史回看，详见 [持仓顾问](docs/alphaagent-holding-advisor.md)。
- 账户调仓联合分析 2–8 只资产的总仓位、行业集中度与日收益相关性，给出累计减仓、条件加仓与调仓顺序，逐轮保留历史，详见 [账户调仓](docs/alphaagent-portfolio-advisor.md)。
- 首页展示真实指数、行业涨跌与活跃板块、行业代表股及主力资金。区分行情日期、读取时间、分钟估算和盘后口径；数据源异常时保留原时间及明确状态。
- 服务运行时自动刷新，交易日09:10准备、09:25展示4–6个重点行业的关注清单；明日预测和投资计划由用户提问触发。关注不等于买入，清单明确覆盖、理由、反证和确认条件。
- 到期预测与已登记投资研究自动核对，未到期不填结果，失败、停牌、缺项和修订分别保留；原预测及输入不覆盖。历史库支持跨功能分页搜索，旧链接持续可回看。

模型执行规划、分析、复核与发布，数字来自真实来源和确定性计算。内置知识保留来源、条件、反例及固定审核版本；新增博主观点先进入候选。账户、资金、持仓、股数和Token不进入公开研究模型上下文。Vue 3、多智能体职责与原型风格保留，开发核验仅在开发者入口展示。

策略回测继续沿用既有5.2准入门禁；研究完成、引用可复算或方法审核通过不代表预测有效、策略盈利或正式回测准入。原有单股估值、比较、策略及调查档案继续可读。**原型计算代码不得移植或复制**，新增计算须遵守原有 [重构约束](docs/superpowers/specs/2026-10-06-alphaagent-redesign.md) 和 [评测门禁](docs/evaluation.md)。

Langfuse 全链路观测与运行检查见 [Langfuse 接入说明](docs/alphaagent-langfuse.md)。已有业务记录和账户资料继续保存在本地，Trace 使用结构化摘要、调用身份和证据引用。

## 本地开发

量化运行使用 Python 3.12–3.13。Windows 时区数据库由 `tzdata` 基础依赖提供。

```powershell
uv sync --locked --extra dev --extra web --extra server --extra quant
# 尚无 .env 时复制；已有配置不要覆盖。
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv run --no-sync search-report-api
```

另一个终端：

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。默认 API 为 8000；`INVESTIGATION_API_PORT` 和 `INVESTIGATION_PROXY_TARGET` 可分别配置服务与 Vite 代理。API 文档在 `/docs`。生产前端先执行 `npm run build`，后端设置 `INVESTIGATION_SERVE_FRONTEND=true` 可单端口托管静态资源。

## 本轮隔离验收

本地已有冻结数据时，先运行 `scripts/verify_quant_o.py --output .phase3-quant-o/新的验收目录`，再运行 `scripts/serve_quant_o.py --data .phase3-quant-o/新的验收目录 --port 8832`。脚本仅接受指定目录内的隔离库，数据来自已有历史归档；不伪造行情或进行付费模型调用。

页面回归：`scripts/verify_alpha_ui.py --url http://127.0.0.1:8832 --output .phase3-quant-o/新的页面验收目录`。该脚本使用本机已有的 Playwright 运行时，检查实际 HTTP 任务、来源弹窗、主题与窄屏，不以 API mock 替代。

第三步独立服务使用端口 8833，追问验收脚本为 `scripts/verify_alpha_followups.py --url http://127.0.0.1:8833 --output .phase3-quant-o/新的追问验收目录`；验证 v1 → v2 → v3、旧报告字节与引用、刷新、历史恢复及原型关键布局。

## 检查与运行边界

```powershell
uv run --no-sync pytest tests/unit/quant tests/integration/quant -q
uv run --no-sync ruff check src tests scripts/verify_alpha_ui.py
uv run --no-sync mypy src
cd frontend
npm test
npm run build
```

mypy 的依赖兼容与既有类型错误仍待处理，不宣称全库类型检查通过。当前 Dockerfile 尚未安装 quant extra，本轮验收使用本机完整虚拟环境，尚未验证容器量化运行。

运行恢复、未知调用与预算边界见 [恢复说明](docs/model-call-recovery.md)、[自动恢复](docs/automatic-recovery.md)；数据库与 Blob 配套备份见 [备份与恢复](docs/paired-backup.md)。原调查平台的设计和能力边界见 [ARCHITECTURE.md](ARCHITECTURE.md)、[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) 和 [并行调查说明](docs/PARALLEL_RESEARCH.md)。
