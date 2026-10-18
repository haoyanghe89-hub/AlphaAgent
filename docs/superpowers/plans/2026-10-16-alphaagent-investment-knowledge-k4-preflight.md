# K4 本地开发及待实源验收状态

> 历史预检记录：下文描述用户追加授权之前的状态。2026-10-16 用户随后明确授权“此功能及后续同类公开数据研究”，审批阻塞已解除，四行业实源研究及离线核验已完成。最终交付以 `2026-10-16-alphaagent-investment-knowledge-k4-results.md` 为准，不再以本页的“尚无真实结果”为当前状态。

本地实现与测试已经完成，尚未执行四行业真实模型验收，因此不标记 K4 完成交付。

## 已实现

- 独立 `investment-research-v1`、`RUN-IR-` 与交易日期限合同，旧预测、旧中期和 K3 记录继续使用各自原合同。
- Planner、Analyst、Verifier、Writer 四职责，有界预算及固定版本输出；明确方向、支持、反证、失效条件与等待条件分开展示。
- 模型只消费公开证券数据及已审核方法片段；资金、账户、持仓、金额及股数留在本地计算。
- 来源、固定知识快照、公开模型上下文、每次失败及完成调用、恢复检查点进入 SQLite / CAS。
- 重启续跑复用已完成调用；拒绝后使用新轮次修订；完成结果不可覆盖。
- 用户结果与开发核验视图、工作台及历史库接入，导出私有核验 ZIP 包及离线验证脚本。
- 没有创建定时采集、通知、自动交易或新的公开知识发布操作。

## 当前本地证据

`.phase3-quant-o/investment-knowledge-20261016-04/`：

- `backend-preflight-final.log`：688 passed、2 skipped、1 warning。两个跳过为 Windows 符号链接权限，警告为既有 Starlette 弃用提示。
- `frontend-local-release.log`：113 passed；`frontend-build-local-release.log`：生产构建通过。
- `recovery-green.log`：恢复、跨证券引用、未准入路线、未经审核发布、幂等、跨站写入保护和导出测试通过。
- `offline-export-verification.json`：18个合成测试 Blob 哈希通过，指标、组合额度及批准内容离线核验通过。
- `synthetic-desktop.png`、`synthetic-mobile.png`：只用于 UI 点验的合成测试，不是真实预测，未写入正式数据库。390px手机布局无横向溢出，普通用户不显示开发校验，引用可展开。
- `preflight-integrity.json`：原有全部148份研究和知识表保持不变，正式库尚无 RUN-IR 记录。
- `public-model-payload-preview.json`：可核查的公开载荷预览，不含资金、持仓或数量。

## 实源验收的审批阻塞

自动审批拒绝了向外部 DeepSeek 发送四行业行情、财报和知识片段的验收调用，理由是先前明确授权只覆盖特定24条候选规则及公开原文审核。本次扩大后的载荷需要用户明确授权。拒绝后没有绕过审批，尚无真实模型结果。

用户授权后，执行 `scripts/verify_alpha_investment_research.py` 完成四行业实际分析、原文核对、预算核对、核验包导出与历史不变对比，再给正式验收链接。不能用合成测试页面代替这一步。

离线核验工具：`scripts/verify_investment_research_export.py <导出ZIP路径>`，只读取本地文件，不外发。
