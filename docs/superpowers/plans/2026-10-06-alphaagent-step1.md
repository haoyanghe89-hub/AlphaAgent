# AlphaAgent 第一步：运行基线实施计划

> 执行方式：在已授权的当前会话中按步骤执行；完成后等待用户页面验收。

**目标：** 在标准项目虚拟环境中恢复量化运行和可点击验收基线，保留现有未验证标签。

**设计依据：** [重构约束](../specs/2026-10-06-alphaagent-redesign.md)。后端保留 deepsearch，前端后续按原型重构；禁止移植原型计算代码；新增指标必须进行独立权威口径交叉核对。

**技术栈：** Python 3.13、uv、FastAPI、SQLite 隔离验收库、Vue 3、Vite、Node test runner、Playwright。

## Task 1：定位并修复环境差异

- 对比默认 `.venv` 与既有完整量化环境，记录相同测试集结果。
- 修改 `pyproject.toml`，显式声明 Windows 的 `tzdata` 依赖；更新 `uv.lock`，不升级其他锁定版本。
- 用 `uv sync --locked --extra dev --extra web --extra server --extra quant` 安装项目本地依赖。
- 更新 README，说明量化环境需要 Python 3.12–3.13 和 quant extra。

## Task 2：验证环境修复

- `.venv/Scripts/python.exe -m pytest tests/unit/quant tests/integration/quant -q -p no:cacheprovider --basetemp .phase3-quant-o/alpha-step1-tests`
- `.venv/Scripts/python.exe -m ruff check src tests`
- 在 `frontend` 执行 `npm test` 与 `npm run build`。
- 记录 mypy 原配置及 Python 3.13 的既有失败，不通过关闭类型检查消除错误；后续更改按模块收敛，不宣称全库类型检查通过。

## Task 3：实际 HTTP 和浏览器验收

- 用 `scripts/verify_quant_o.py --output .phase3-quant-o/alpha-step1-manual` 从本地冻结真实历史数据复制隔离库，生成新的三只股票任务、报告、引用与复算记录。
- 用 `scripts/serve_quant_o.py --data .phase3-quant-o/alpha-step1-manual --port 8831` 启动回环验收服务，托管新构建前端。8830 被已有服务占用，不终止该服务。
- 用 `scripts/verify_quant_ui_o.py` 检查实际页面的三图、单元格来源、引用弹窗、明暗主题、窄屏及新建量化任务。不得以 API mock 替代。
- 在 Codex 打开 `http://127.0.0.1:8831`，留下服务，提供用户点击清单。

## 完成边界

本步不重写原型 UI，不迁移计算算法，不修改原业务数据库，不提交／推送。本步通过只表示运行基线恢复；独立财务口径校准、原型界面与新的 multi-agent 产品闭环在后续阶段实现并验收。
