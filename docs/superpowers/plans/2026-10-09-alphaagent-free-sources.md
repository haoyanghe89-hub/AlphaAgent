# AlphaAgent 免费来源第一批实施计划

> **For agentic workers:** 使用 executing-plans 在现有分支内执行。用户已授权按免费来源推进；沿用已有5.2设计及准入要求，不另购数据、不提交或推送。

**Goal:** 将沪深交易所免费生命周期资料接入已冻结股票池核对，交付可点击、可复算的新报告版本。

**Architecture:** 复用持久化历史采集任务、24次／180秒／16MiB预算及父版本绑定。固定五类官方请求，保留原始JSON/XLSX字节，提取结果每次读取都验证。按上市／摘牌日期回溯观察并逐年比较BaoStock历史名单，但当前下载的生命周期资料不是当年完整档案，不升级STRICT。

**Tech Stack:** Python / FastAPI / SQLAlchemy / stdlib ZIP/XML；Vue3原型组件；pytest / Playwright。

**Spec:** `docs/superpowers/specs/2026-10-06-alphaagent-redesign.md`；`2026-10-09-alphaagent-52-completion.md`。

## 边界

- 沪深A股；过滤B股，不覆盖北交所。不得将当前目录直接冒充历史股票池。
- 五份官方资料：上海主板、科创板、终止上市；深圳A股、终止上市。
- 保留两个交易所原字段语义及查询参数；摘牌日不当作终止上市决定公布日。
- 无匹配、重复生命周期、来源日期冲突分别呈现；不以名字相近合并身份，不修改父报告未知计数。
- 原财报／单股／官方基准结果保持不变；只新增lifecycle_audit，不执行策略。
- 此批只补生命周期回溯证据，财报修订、公司行为、执行限制、全收益独立核对仍待后续免费来源调查。

## Task A：来源及比对

Files: `src/marketpulse/quant/backtest/lifecycles.py`；`tests/unit/quant/test_backtest_lifecycles.py`。

- [x] 固定HTTPS请求、字节／行数／ZIP解压边界，校验分页完整性及字段，冻结原文与提取包。
- [x] 将原父名单／生命周期绑定目标，逐年比较共同成员、双方独有、生命周期歧义及日期冲突；全部保持PARTIAL。
- [x] 自动测试覆盖缺字段／截断／重复／哈希篡改／空响应、固定查询及A/B股类型；实际摘牌边界差异保留，不自动修正名单。

## Task B：持久化及Vue3页面

Files: `acquisition.py`、`api.py`、`frontend/src/alpha/{useBacktest,BacktestView,BacktestReport,BacktestLifecycles}.vue/js`；集成及前端测试。

- [x] 新POST仅接收UUID；父版本、预算、停止、失败、重启和原文完整性复用原流程。
- [x] 原型表格显示五份来源、各年集合差异和证券明细；深浅色及内部横向滚动；继承来源不混计新请求次数。
- [x] 隔离合成测试验证UUID、拒绝任意URL、父报告不变、导出、停止、失败和重启／篡改。

## Task C：真实验收

- [x] 实际页面点击取得五份官方响应；保存真实报告、导出、原文SHA及六种布局截图。
- [x] 运行针对性后端回归、前端测试及构建、Ruff、重启回放；记录实际差异及未完成项。
- [x] 更新5.2结果文档，保持整体验收及5.3未完成。

开发者自测见 `2026-10-09-alphaagent-free-sources-results.md`；用户页面验收待进行。
