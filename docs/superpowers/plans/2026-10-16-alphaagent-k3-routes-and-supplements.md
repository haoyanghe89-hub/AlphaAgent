# AlphaAgent K3 路线、组合约束与知识补充 Implementation Plan

> 按已获用户授权的设计在当前工作区直接执行，使用 executing-plans；不创建子代理、工作树、提交或推送。保留 K1/K2 和所有旧研究。

**Goal:** 用真实数据打通趋势波段、质量／成长两条研究路线及组合资金试算，并提供供 Codex 使用的本地知识补充接口。

**Architecture:** SQLite / CAS 继续保存不可变输入、来源、规则和结果。新增独立 `investment-plan-v1` 请求，不更改旧预测合同。确定性工具评估路线条件和组合约束；K3 页面呈现真实条件及资金试算，K4 再接模型解释与完整工作台研究链。

**Tech Stack:** 现有 Python、Pydantic、Decimal、SQLAlchemy、Tushare SourceClient、Vue 3 和 ECharts；不新增数据库或 Agent 框架。

**Spec:** D:/deepsearch/docs/superpowers/specs/2026-10-15-alphaagent-investment-knowledge-system-design.md；以下目标澄清来自 2026-10-16 用户逐项确认。

## 全局约束与已确认终态

- 自有投资体系，作者及书籍作为可追溯来源；完整个性化计划，用户决定交易。
- 多路线并行，分配资金和纪律，组合层统筹风险；防守与等待是明确行动。
- 终态包含盘前计划、盘中条件监测、盘后复盘和按需问答，本阶段不创建定时采集、提醒或自动交易。
- A 股优先，先波段与中期，后续扩展短线和长线；国家政策以中国为主，海外政策按业务关联。
- 保留旧下一交易日 UP/DOWN、旧中期方向、旧数字约束及旧 5.2 正式回测门禁。
- 每次最多四只证券、八十次有界来源尝试、九百秒；本阶段的条件计算不调用模型，后续模型仍遵守二十四次／六万令牌预算。
- 程序研究参数是系统提案，不冒充作者原始参数或已验证盈利参数。研究条件与投资效果分开。
- 知识补充只写本地候选；不自动访问用户提交的 URL，不把文章指令当工具指令，不开无授权 HTTP 发布口。

## Task 1：真实输入与指标合同

**Files:** `quant/knowledge/inputs.py`、`features.py`、定向扩展 `decision/tushare.py`、`tests/unit/quant/knowledge/test_features.py`。

**Interfaces:** `capture_inputs(client, entities, captured_at)` 返回身份、原始日线／复权因子／指数／行业／财报／交易规则及来源；`compute_features(packet)` 返回带公式、单位和窗口的指标，缺项保持缺失。

- [x] 执行前在线备份 SQLite，记录旧研究和 K1/K2 所有版本哈希。
- [x] 新增白名单 `adj_factor`、`index_daily`；旧财报字段默认保持不变，新合同显式请求合并净利润、归属股东权益及主数据行业。
- [x] 从实际回包探测权限；复权因子不缺省为一；日线、指数和交易日历按同窗对齐。
- [x] TTM 使用当前年累计＋上年全年－上年同期，收入、净利润、现金流各自一致；公告当日无时间戳时保守到下一交易日可用。
- [x] 阈值、均线和窗口是带版本的系统研究参数；相对强弱仅相对声明指数，不发布全市场排名。
- [x] 交易单位绑定取得的现行交易所原文及暂缓条款；科创与主板分别取整。

```python
def test_adjustment_is_not_silently_one():
    with pytest.raises(ValueError, match='adjustment'):
        compute_features(packet_without_one_adjustment_factor)

def test_negative_comparison_base_has_no_growth_rate():
    assert growth(Decimal('10'), Decimal('-5')) is None
```

运行：`python -m pytest tests/unit/quant/knowledge/test_features.py -q`。

## Task 2：两条路线和组合资金约束

**Files:** `routing.py`、`playbook.py`、`planning_contracts.py`；`test_routing.py`、`test_playbook.py`。

**Interfaces:** `evaluate_routes(features, knowledge_versions, horizon)` 返回两条路线的准入、条件与原文依据；`allocate_plans(candidates, profile)` 顺序分配条件计划额度，累计更新现金、单股、行业和路线暴露。

- [x] 趋势使用市场状态、复权价格、声明指数相对强弱和量能；质量／成长使用同口径 TTM 增长、现金回收及价格确认。
- [x] 金融企业不套用非金融现金回收；仍可评估具备输入的趋势路线。
- [x] 每笔持仓绑定原路线，不能把趋势失败换成长线补仓理由。
- [x] 组合同时约束现金、风险金额、单股、行业、路线和流动性；费用为用户可改的估算假设，不声称实际券商费率。
- [x] 风险参考价和条件触发价由工具计算，注明不保证成交或最大损失；等待状态不生成立即交易指令。
- [x] 未填写画像不生成个性化金额和股数；五万、五十万、五百万元仅作为明确测试画像。

```python
def test_multiple_routes_share_one_cash_limit():
    plans = allocate_plans(two_ready_candidates, profile)
    assert sum(Decimal(p['reserved_cash']) for p in plans) <= profile.available_cash
```

运行：`python -m pytest tests/unit/quant/knowledge/test_routing.py tests/unit/quant/knowledge/test_playbook.py -q`。

## Task 3：知识补充接口

**Files:** `supplements.py`、模型与迁移 `20261016_14`、`scripts/manage_alpha_knowledge_supplements.py`、`test_supplements.py`。

**Interfaces:** `submit(submission)` 幂等保存原文／链接／作者声明／日期／用户理解；`record_decision(id, decision)` 保存用途、反例、数据要求和出处核查；`link_published_rule(id, rule_id, version)` 只关联已通过 K2 审核且引用核定原文的固定规则。

- [x] 区分长期方法、短期市场观点和政策材料；作者及发表日期未核实就保留未核实状态。
- [x] 候选与决策追加保存，默认不进入正式检索；同内容与同作者重复投稿不得算独立证据。
- [x] 有用但无原文的想法只保留为候选；短期观点不能不加有效期变成永久方法。
- [x] 提供本地 CLI、Python 接口、只读核验 API 和输入样例合同；普通页面不展示知识管理。
- [x] 测试重复、乱码、注入、缺反例、误归因、未审核发布和错误版本关联。

运行：`python -m pytest tests/unit/quant/knowledge/test_supplements.py -q`。

## Task 4：保存、页面与实际验收

**Files:** `planning_store.py`、`planning_service.py`、`planning_api.py`；`InvestmentPlan.vue` 和控制器；工作台／导航／历史的定向接入；相关前后端测试及 `scripts/verify_alpha_investment_routes.py`。

- [x] 新命名空间保存请求、画像、调用、原始输入、指标、路线版本及结果；完成记录不覆盖。
- [x] 工作台提供投资计划入口，用户可输入股票及可选画像，结果先展示行动、条件、图表、组合约束，再提供开发者核验。
- [x] 幂等请求、已有材料恢复、原文完整性与旧 API 兼容测试通过。
- [x] 四个不同申万一级行业的实际股票验证输入与条件，不伪造金融质量指标；三组资金画像解释上限差异。
- [x] 停止源码编辑后执行完整 quant 回归，避免原指纹门禁因并行编辑而触发。
- [x] 前端测试、生产构建、桌面与手机点击、历史哈希、密钥输出扫描全部验证后，给用户链接和验收步骤。

本阶段不声明完整主动服务、模型投资解释、收益优势或正式回测已经完成；这些仍按 K4–K6 单独验收。
