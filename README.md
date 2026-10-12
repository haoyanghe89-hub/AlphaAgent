# AlphaAgent：AI 量化投研

前端采用 Vue 3、Vite 和 ECharts，按 AlphaAgent 原型重建工作台、研究过程、报告、个股、历史库及策略回测入口，支持浅色／深色切换。后端沿用 deepsearch 的 FastAPI、证据归档、持久化状态、Harness、确定性计算及发布门禁。

统一的本地功能演示入口为 <http://127.0.0.1:8839/>，启动方式、真实冻结窗口、逐步点击顺序与能力边界见 [实际功能演示](docs/alphaagent-demo.md)。5.2仍未整体通过，回测输入检查完成时继续保留策略执行阻断。

原多智能体框架保留。当前单股量化分支通过规划、取数、工具计算、核验和成稿的职责边界执行，过程记录落盘；本分支不调用模型生成数值。模型语义分析的质量需要另行评测。

## 当前闭环

1. 工作台输入已接入的股票名称或代码。
2. 确认标的、数据窗口、复权口径和研究深度；历史冻结窗口由用户显式选择。
3. 读取实际执行过程，完成后打开摘要卡与完整报告。
4. 点击指标、图表原值或正文引用，检查来源、公式、计算单元格与验证记录。
5. 从历史库搜索并重新打开报告；刷新和浏览器返回保留页面路径。

当前展示历史完整收盘数据，不提供实时指数快照。缺失数据保留缺项，报告成稿不代表指标 VERIFIED。现有证券搜索仅覆盖已主数据化标的，空数据库需要先接入证券与数据。

追问会关联父任务，在原冻结期间和真实输入快照上创建新任务与新报告版本，原报告与引用保留。当前支持“重新核对收益与风险”“按前复权重算”“无风险利率按0重算”；缺少相应冻结价格口径或不支持的假设会明确拒绝。重复请求和响应丢失后的重试不会重复生成版本，同研究存在活动版本时不能再创建另一版本。

多股比较、DCF、自由语义追问、融资与筹码数据、交易账本回测按后续阶段接入。回测按原型显示五个参数和三个预设，执行尚未开放；不生成样例净值或持仓。既有事件调查档案可在历史库只读回看；后端原调查接口继续保留。

桌面深色展示直接使用原型 `global.css` 的副本 `frontend/src/styles/prototype.css`，主题、来源弹窗、版本操作与窄屏适配位于独立展示样式中。Vue 3 框架保持不变。指数行情尚未接入时仅显示缺失值，不能将原型截图中的数值冒充当前行情。

**原型计算代码不得移植或复制。** 新增指标必须分别通过人工固定样本、独立权威披露值／口径交叉核对及同输入复算一致性检查。权威基准核对尚未自动接入，现有未验证指标不因可复现而升级。详见 [重构约束](docs/superpowers/specs/2026-10-06-alphaagent-redesign.md) 和 [评测门禁](docs/evaluation.md)。

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
