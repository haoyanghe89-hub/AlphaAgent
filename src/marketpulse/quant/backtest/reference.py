"""Independent result checkpoint adapter, separate from same-engine reproduction."""

import hashlib
from datetime import date
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import Field

from ..contracts import digest
from ..domain import ExactDecimal, FrozenModel
from .package import Review


class ResultReference(FrozenModel):
    basis: Literal["strategy", "benchmark"]
    parameter_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    points: tuple[tuple[date, ExactDecimal], ...] = Field(max_length=2200)
    metrics: dict[str, ExactDecimal | None]
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_family: str = Field(min_length=1, max_length=100)
    review: Review


def parameter_hash(result):
    return digest(
        dict(
            package_hash=result["admission"]["package_hash"],
            conditions=result["admission"]["conditions_hash"],
            initial_cash=result["assumptions"]["initial_cash"],
            risk_free_annual=result["assumptions"]["risk_free_annual"],
            cost_pct_per_side=result["conditions"]["cost_pct_per_side"],
        )
    )


def check(result, references, resolve):
    references = tuple(ResultReference.model_validate(r) for r in references)
    if len({r.basis for r in references}) != len(references):
        raise ValueError("duplicate independent result reference")
    checks = []
    with localcontext() as ctx:
        ctx.prec = 40
        for basis in ("strategy", "benchmark"):
            ref = next((r for r in references if r.basis == basis), None)
            outcome = dict(
                basis=basis,
                status="UNAVAILABLE",
                note="缺少独立引擎／权威同口径的逐日净值及五项绩效参考",
            )
            if ref:
                review = ref.review
                valid_review = (
                    review.domain == "strategy_result"
                    and review.outcome == "MATCH"
                    and review.payload_hash
                    == digest(ref.model_dump(mode="json", exclude={"review"}))
                    and review.source_hash == ref.source_hash
                    and review.scope_hash == result["admission"]["scope_hash"]
                    and ref.source_family != review.reference_family
                    and ref.source_hash != review.reference_hash
                )
                for sha in (ref.source_hash, review.reference_hash):
                    raw = resolve(sha)
                    if raw is None:
                        valid_review = False
                    elif not raw or hashlib.sha256(raw).hexdigest() != sha:
                        raise ValueError("independent reference source hash mismatch")
                compatible = (
                    ref.parameter_hash == parameter_hash(result)
                    and [str(d) for d, _ in ref.points] == result["nav"]["dates"]
                    and set(ref.metrics) == set(result["metrics"])
                )
                if valid_review and compatible:
                    metrics = (
                        result["metrics"] if basis == "strategy" else result["benchmark_metrics"]
                    )
                    match = all(
                        v > 0 and abs(v - Decimal(x)) <= Decimal("1e-8")
                        for (_, v), x in zip(ref.points, result["nav"][basis], strict=True)
                    )
                    match = match and all(
                        (v is None and metrics[k] is None)
                        or (
                            v is not None
                            and metrics[k] is not None
                            and abs(v - Decimal(metrics[k])) <= Decimal("0.0001")
                        )
                        for k, v in ref.metrics.items()
                    )
                    outcome.update(
                        status="MATCH" if match else "CONFLICT",
                        note="逐日净值绝对容差1e-8；绩效绝对容差0.0001，百分比按百分点比较。",
                    )
                else:
                    outcome.update(
                        status="METHOD_DIFFERENCE" if valid_review else "UNAVAILABLE",
                        note="参数／日期／方法不同"
                        if valid_review
                        else "独立审核凭证或来源原文缺失",
                    )
                outcome.update(source_hash=ref.source_hash, reference_hash=review.reference_hash)
            checks.append(outcome)
    return dict(
        status="MATCH"
        if all(c["status"] == "MATCH" for c in checks)
        else "CONFLICT"
        if any(c["status"] == "CONFLICT" for c in checks)
        else "UNAVAILABLE",
        checks=checks,
        note="独立参考核对与同引擎复算分开，缺少参考不升级指标验证等级。",
    )
