"""Bounded lexical retrieval of published rules, with aliases and family diversity."""

from marketpulse.telemetry.tracing import observation, stage

from ..contracts import digest
from .retrieval import tokens

ALIASES = {
    "买点": "入场时机",
    "止损": "退出风险",
    "仓位": "容量资金风险",
    "资金量": "容量流动性",
    "ROIC": "资本回报",
    "基本面": "质量成长",
    "因子投资": "因子价值动量盈利投资",
    "DCF": "现金流折现估值",
}


@stage("knowledge.retrieve", as_type="retriever")
def retrieve(store, query, *, horizon=None, cutoff=None, limit=12, diversify=True):
    if not 1 <= len(query.strip()) <= 120 or not 1 <= limit <= 12:
        raise ValueError("bounded query and context required")
    if horizon not in (None, "SWING", "MEDIUM", "LONG", "NEXT_SESSION"):
        raise ValueError("unsupported horizon")
    with observation("retrieval.query_expand") as span:
        expanded = (
            query + " " + " ".join(v for k, v in ALIASES.items() if k.lower() in query.lower())
        )
        terms = set(tokens(expanded))
    with observation("retrieval.filter_and_score") as span:
        scored = []
        for item in store.current(cutoff):
            rule = item["rule"]
            if horizon and horizon not in rule["horizons"]:
                continue
            primary = set(tokens(rule["title"] + " " + " ".join(rule["keywords"])))
            secondary = set(
                tokens(
                    " ".join(
                        (
                            rule["system_rule"],
                            *rule["required_inputs"],
                            *rule["conditions"],
                            *rule["counterexamples"],
                        )
                    )
                )
            )
            score = 3 * len(terms & primary) + len(terms & secondary)
            if score:
                scored.append(dict(item, lexical_score=score))
        span.update(output={"count": len(scored)})
    with observation("retrieval.rank") as span:
        scored.sort(key=lambda r: (-r["lexical_score"], r["rule_id"], r["version"]))
    with observation("retrieval.diversify_and_limit") as span:
        result, families = [], set()
        for item in scored:
            if diversify and families.intersection(item["source_families"]):
                continue
            families.update(item["source_families"])
            result.append(item)
            if len(result) == limit:
                break
        span.update(output={"count": len(result), "rule_ids": [item["rule_id"] for item in result]})
    identity = dict(
        query=query,
        retrieval_version="rule-lexical-v1",
        limit=limit,
        family_diversity=diversify,
        horizon=horizon,
        cutoff=cutoff.isoformat() if cutoff else None,
        versions=[(r["rule_id"], r["version"]) for r in result],
    )
    return dict(
        items=result,
        retrieval_version="rule-lexical-v1",
        context_hash=digest(identity),
        family_diversity=diversify,
        source_families=len(families),
        interpretation="词法相关度，不是收益或方向概率；同来源族不算独立证据。",
        execution_status="REFERENCE_ONLY",
        validation_status="UNVALIDATED",
    )
