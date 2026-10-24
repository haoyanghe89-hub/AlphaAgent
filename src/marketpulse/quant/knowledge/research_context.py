"""Public-only model context and fixed knowledge/citation snapshots."""

from marketpulse.telemetry import investment as trace_content
from marketpulse.telemetry.content import PUBLIC_CONTENT_POLICY
from marketpulse.telemetry.tracing import stage

LABELS = dict(
    close="收盘价",
    ma_20="二十日均线",
    ma_60="六十日均线",
    return_20="二十交易日收益率",
    benchmark_return_20="沪深三百同期收益率",
    relative_return_20="相对沪深三百收益率差",
    annualized_volatility_20="二十日收益样本标准差年化",
    turnover_ratio_5_20="近五日与二十日平均成交额之比",
    average_turnover_20="二十日平均成交额",
    revenue_growth_ttm="收入滚动全年同比",
    profit_growth_ttm="合并净利润滚动全年同比",
    cash_conversion="经营现金流与合并净利润之比",
    roe_ttm="归属股东权益平均余额口径回报",
)


def analysis_basis(context, plan=None, *, scoped=False):
    """Route only approved method fragments; all stock facts remain available."""
    import copy

    result = copy.deepcopy(context)
    if not scoped:
        return result
    selected = {s["security_id"]: s["route_id"] for s in plan["selections"]}
    # Account capacity is applied locally; the public analyst has no private position inputs.
    required = {"KR-AVOID-RUIN", "KR-EXPECTATION-EXIT"}
    required.update(r for s in context.get("research_selection", []) for r in s["method_rule_ids"])
    for rid in selected.values():
        required.update(
            ("KR-TREND-MARKET", "KR-TREND-ENTRY-DISCIPLINE")
            if rid == "TREND_SWING"
            else ("KR-QUALITY-ECONOMICS", "KR-GROWTH-RETURN")
        )
    result["methods"] = [
        {k: v for k, v in method.items() if k != "quote"}
        for method in result["methods"]
        if method["rule_id"] in required
    ]
    for stock in result["stocks"]:
        stock["routes"] = [
            r for r in stock["routes"] if r["route_id"] == selected[stock["security_id"]]
        ]
    return result


def analysis_plan(plan, compact=False):
    if not compact:
        return plan
    return dict(
        selections=[{k: s[k] for k in ("security_id", "route_id")} for s in plan["selections"]]
    )


@stage(
    "context.analyst_pack",
    as_type="chain",
    content_policy=PUBLIC_CONTENT_POLICY,
    inputs=trace_content.context_input,
    outputs=trace_content.context_output,
)
def analyst_context(context, version, plan=None, *, scoped=False, compact=False, structured=False):
    """Lossless window interning for the new technical prompt's bounded input."""

    from ..contracts import canonical
    from .research_agents import (
        CHECKS_PROMPT_VERSION,
        DIRECTION_PROMPT_VERSION,
        TECHNICAL_PROMPT_VERSION,
    )

    if version in {DIRECTION_PROMPT_VERSION, CHECKS_PROMPT_VERSION}:
        version = TECHNICAL_PROMPT_VERSION

    result = analysis_basis(context, plan, scoped=scoped)
    from . import numeric_grounding

    if numeric_grounding.enabled(result):
        result["numeric_quotes"] = {
            f["evidence_id"]: numeric_grounding.quotes(f, result.get("windows"))
            for f in result["evidence"]
        }
        result["route_numeric_quotes"] = {
            s["security_id"]: numeric_grounding.route_quotes(s) for s in result["stocks"]
        }
    if structured:
        from .structured_contract import requirements

        result["citation_requirements"] = requirements(result)
    if result.get("active_research_version"):
        from .active_research import model_view

        result = model_view(result)
    if version in {TECHNICAL_PROMPT_VERSION, "investment-technicals-roles-v2"}:
        windows, keys = {}, {}
        for fact in result["evidence"]:
            window = fact["window"]
            key = keys.setdefault(canonical(window), "W" + str(len(keys) + 1))
            windows[key] = window
            fact["window"] = key
        result["windows"] = windows
    if version == TECHNICAL_PROMPT_VERSION:
        result["evidence"] = [
            {k: v for k, v in f.items() if k not in {"label", "metric_key", "security_id"}}
            for f in result["evidence"]
        ]
    if compact:
        # Full methods and Planner rationale stay sealed; only execution rules enter analysis.
        result["methods"] = [
            {k: m[k] for k in ("rule_id", "system_rule")} for m in result["methods"]
        ]
        result.pop("technical_guidance", None)
    return result


@stage(
    "context.verifier_pack",
    as_type="chain",
    content_policy=PUBLIC_CONTENT_POLICY,
    inputs=trace_content.context_input,
    outputs=trace_content.context_output,
)
def review_context(
    context, plan, analysis, review_round, *, compact=False, portfolio_compact=False
):
    """Review every proposed citation without resending unrelated retrieved material."""
    ids = {
        e
        for o in analysis["opinions"]
        for c in o["claims"] + o["opposing"]
        for e in c["evidence_ids"]
    }
    rules = {r for o in analysis["opinions"] for r in o["method_rule_ids"]}
    result = dict(
        context,
        evidence=[e for e in context["evidence"] if e["evidence_id"] in ids],
        methods=[m for m in context["methods"] if m["rule_id"] in rules],
        plan=plan,
        proposal=analysis,
        review_round=review_round,
    )
    if result.get("active_research_version"):
        from .active_research import model_view

        result = model_view(result)
    if compact:
        from ..contracts import canonical

        selected = {s["security_id"]: s["route_id"] for s in plan["selections"]}
        result["stocks"] = [
            {
                k: v
                for k, v in s.items()
                if k in ("security_id", "market_regime", "financial_scope", "routes")
            }
            for s in result["stocks"]
        ]
        for stock in result["stocks"]:
            stock["routes"] = [
                r for r in stock["routes"] if r["route_id"] == selected[stock["security_id"]]
            ]
        result["evidence"] = [
            {
                k: v
                for k, v in e.items()
                if k in ("evidence_id", "value", "unit", "window", "text", "policy_state")
            }
            for e in result["evidence"]
        ]
        # K2 already reviewed the method originals; this role checks their fixed system rules.
        result["methods"] = [
            {k: v for k, v in m.items() if k not in ("title", "quote")} for m in result["methods"]
        ]
        windows, keys = {}, {}
        for fact in result["evidence"]:
            window = fact["window"]
            key = keys.setdefault(canonical(window), "W" + str(len(keys) + 1))
            windows[key] = window
            fact["window"] = key
        result["windows"] = windows
    if portfolio_compact:
        result["methods"] = [
            {k: m[k] for k in ("rule_id", "system_rule")} for m in result["methods"]
        ]
        result["plan"] = analysis_plan(plan, True)
        result.pop("technical_guidance", None)
    return result


def direction_check_context(previous_context, previous_review):
    result = {
        k: previous_context[k]
        for k in (
            "cutoff",
            "horizon",
            "horizon_sessions",
            "market_date",
            "evidence",
            "windows",
            "proposal",
            "plan",
            "review_round",
            "original_research",
        )
        if k in previous_context
    }
    result["stocks"] = [
        {k: s[k] for k in ("security_id", "market_regime", "financial_scope") if k in s}
        for s in previous_context["stocks"]
    ]
    result["proposal"] = dict(
        opinions=[
            {k: v for k, v in o.items() if k not in {"invalidation", "next_checks"}}
            for o in previous_context["proposal"]["opinions"]
        ]
    )
    result["previous_checks"] = [
        dict(
            security_id=r.security_id,
            facts_approved=all(d.verdict == "APPROVE" for d in r.decisions),
            conditions_respected=r.conditions_respected,
        )
        for r in previous_review.reviews
    ]
    return result


@stage(
    "knowledge.snapshot",
    as_type="retriever",
    content_policy=PUBLIC_CONTENT_POLICY,
    inputs=trace_content.knowledge_input,
    outputs=trace_content.knowledge_output,
)
def knowledge_snapshot(rules, cutoff, *, dynamic=False):
    from .routing import RULE_IDS

    selected = [r for r in rules.current(cutoff) if dynamic or r["rule_id"] in RULE_IDS]
    if not set(RULE_IDS) <= {r["rule_id"] for r in selected}:
        raise ValueError("必要方法的发布版本不可用")
    documents = {}
    fragments = []
    for item in selected:
        cite = item["rule"]["citations"][0]
        key = (cite["document_id"], cite["version"])
        if key not in documents:
            documents[key] = rules.library.document(*key)
        doc = documents[key]
        quote = cite["quote"][:600]
        fragments.append(
            dict(
                rule_id=item["rule_id"],
                version=item["version"],
                title=item["rule"]["title"],
                source_claim=item["rule"]["source_claim"],
                adaptation=item["rule"]["adaptation"],
                system_rule=item["rule"]["system_rule"],
                counterexamples=item["rule"]["counterexamples"][:2],
                authors=doc["source"]["authors"],
                source_title=doc["source"]["title"],
                url=doc["source"]["url"],
                document_id=key[0],
                document_version=key[1],
                start=cite["start"],
                end=cite["start"] + len(quote),
                quote=quote,
                execution_status="REFERENCE_ONLY",
                validation_status="UNVALIDATED",
            )
        )
    return dict(rules=selected, documents=list(documents.values()), fragments=fragments)


@stage(
    "context.public_projection",
    as_type="chain",
    content_policy=PUBLIC_CONTENT_POLICY,
    inputs=trace_content.context_input,
    outputs=trace_content.context_output,
)
def public_context(core, packet, knowledge, sessions, *, numeric_grounding=None):
    evidence, stocks = [], []
    for c in core["candidates"]:
        if c["status"] != "COMPUTED":
            raise ValueError(
                c["security_id"] + " 行情计算未通过；保留原始数据，可另选交易活跃标的。"
            )
        sid = c["security_id"]
        metrics = dict(c["metrics"], **c["financials"]["metrics"])
        for key, metric in metrics.items():
            if key not in LABELS or metric["value"] is None:
                continue
            roles = ("plan_daily_", "plan_adjustment_", "plan_benchmark")
            if key in ("revenue_growth_ttm", "profit_growth_ttm"):
                roles = ("plan_income_",)
            elif key == "cash_conversion":
                roles = ("plan_income_", "plan_cashflow_")
            elif key == "roe_ttm":
                roles = ("plan_income_", "plan_balancesheet_")
            elif key == "benchmark_return_20":
                roles = ("plan_benchmark",)
            metric_sources = [
                s["source"]["blob_ref"]
                for s in packet["sources"]
                if s["role"].startswith(roles)
                and (sid in s["role"] or s["role"] == "plan_benchmark")
            ]
            evidence.append(
                dict(
                    evidence_id="F-" + sid.replace(".", "") + "-" + key,
                    security_id=sid,
                    metric_key=key,
                    label=LABELS[key],
                    value=metric["value"],
                    unit=metric["unit"],
                    formula=metric["formula"],
                    window=metric["window"],
                    source_refs=metric_sources,
                )
            )
        stocks.append(
            dict(
                security_id=sid,
                name=c["name"],
                industry=c["industry_name"],
                financial_scope=c["financials"]["scope"],
                market_regime=c["market_regime"],
                eligible_routes=[r["route_id"] for r in c["routes"] if r["eligible"]],
                preferred_route=c["selected_route"],
                routes=[
                    {
                        k: r[k]
                        for k in ("route_id", "eligible", "status", "conditions", "invalidation")
                    }
                    for r in c["routes"]
                ],
            )
        )
    # Account details and local position calculations never enter this public object.
    return dict(
        **({"numeric_grounding": numeric_grounding} if numeric_grounding is not None else {}),
        protocol="public-investment-context-v1",
        cutoff=packet["captured_at"],
        market_date=packet["market_date"],
        horizon=core["request"]["horizon"],
        horizon_sessions=sessions,
        stocks=stocks,
        evidence=[
            {k: v for k, v in e.items() if k not in ("source_refs", "formula")} for e in evidence
        ],
        methods=[
            {
                k: f[k][:360] if k == "quote" else f[k]
                for k in (
                    "rule_id",
                    "title",
                    "system_rule",
                    "adaptation",
                    "counterexamples",
                    "quote",
                )
            }
            for f in knowledge["fragments"]
        ],
        limitations=[
            "仅为真实供应商行情与财报转录，不是完整独立财报审计。",
            "没有本次政策、主力资金、新闻与完整估值输入，不能评价相关事实。",
            "参考知识及程序参数未证明盈利效果，方向须保留反证与失效条件。",
        ],
    ), evidence
