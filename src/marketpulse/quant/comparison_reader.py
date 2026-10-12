"""Versioned read model over immutable comparison children; no new acquisition."""

from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import Field

from .contracts import digest
from .domain import ExactDecimal, FrozenModel
from .live.overview import growth_context, valuation_context

VERSION = "comparison-reader-v1"


class ComparisonMetric(FrozenModel):
    """Optional operator/tool adapter inputs, always presented as unverified."""

    basis: Literal["TTM_PARENT_ROE", "YTD_PARENT_CASH_RATIO"]
    value: ExactDecimal = Field(ge=-1000000000, le=1000000000)
    unit: Literal["%", "倍"]
    start: date
    end: date
    definition: str = Field(min_length=1, max_length=1000)
    source: tuple[str, ...] = Field(min_length=1, max_length=8)


def present(children):
    with localcontext() as ctx:
        ctx.prec = 40
        return _present(children)


def _present(children):
    payloads = [s["data"] for s in children]
    rows = []
    for key, label, unit, caption in [
        ("growth", "成长性", "%", "单季营业收入同比"),
        ("profitability", "盈利能力", "%", "TTM 平均归母权益 ROE"),
        ("quality", "盈利质量", "倍", "同期间经营现金流 / 归母净利"),
        ("valuation", "估值水位", "%", "自算 PE-TTM 历史样本分位"),
        ("momentum", "一年动量", "%", "52周窗口前复权价格收益"),
    ]:
        cells, dates, signatures = [], [], []
        for d in payloads:
            cell, period, signature = None, None, None
            if key == "growth":
                observed = growth_context(d)
                q = observed.get("quarter_comparison")
                if q:
                    period = q["period"]
                    cell = dict(
                        name=caption,
                        value=q["current"],
                        unit=unit,
                        source="lrb",
                        proof=observed["proof"],
                    )
                    signature = (period, d.get("revenue_basis"), d["subject"].get("company_type"))
            elif key == "valuation":
                observed = valuation_context(d)
                if observed["status"] == "AVAILABLE":
                    period = observed["end"]
                    cell = dict(
                        name=caption,
                        value=observed["percentile"],
                        unit=unit,
                        source="lrb / valuation",
                        proof=observed["proof"],
                    )
                    chart = d["valuation"]
                    values = next(s["data"] for s in chart["series"] if s["name"] == "自算 PE-TTM")
                    signature = (
                        tuple(
                            dt
                            for dt, v in zip(chart["x"], values, strict=True)
                            if v is not None and Decimal(str(v)) > 0 and dt <= period
                        ),
                        d["subject"].get("industry"),
                        d["subject"].get("company_type"),
                    )
            elif key in {"profitability", "quality"}:
                try:
                    entry = ComparisonMetric.model_validate(d.get("comparison_metrics", {})[key])
                    expected = (
                        "TTM_PARENT_ROE" if key == "profitability" else "YTD_PARENT_CASH_RATIO"
                    )
                    available_sources = {s["key"] for s in d.get("sources", [])}
                    if (
                        entry.basis == expected
                        and entry.unit == unit
                        and entry.start < entry.end
                        and set(entry.source) <= available_sources
                        and d["subject"].get("company_type")
                    ):
                        period = f"{entry.start} ~ {entry.end}"
                        cell = dict(
                            name=caption,
                            value=float(entry.value),
                            unit=unit,
                            source=" / ".join(entry.source),
                            proof=dict(
                                locator="comparison_metrics/" + key,
                                source=list(entry.source),
                                text=(
                                    f"{caption}：{entry.value} {unit}；"
                                    f"{entry.definition}；尚未独立核验。"
                                ),
                            ),
                        )
                        signature = (
                            period,
                            entry.basis,
                            entry.definition,
                            d["subject"]["company_type"],
                        )
                except (KeyError, ValueError, TypeError):
                    pass
            elif key == "momentum":
                try:
                    end = date.fromisoformat(d["data_dates"]["price"])
                    start = end - timedelta(days=364)
                    sample = [
                        (date.fromisoformat(p["date"]), Decimal(str(p["close"])))
                        for p in d["calculation_inputs"]["prices"]
                        if str(start) <= p["date"] <= str(end)
                    ]
                    if (
                        len(sample) >= 120
                        and [d for d, _ in sample] == sorted({d for d, _ in sample})
                        and sample[0][0] <= start + timedelta(days=14)
                        and sample[-1][0] == end
                        and all(v > 0 and v.is_finite() for _, v in sample)
                    ):
                        period = str(end)
                        value = (sample[-1][1] / sample[0][1] - 1) * 100
                        cell = dict(
                            name=caption,
                            value=float(value),
                            unit=unit,
                            source="prices",
                            proof=dict(
                                locator="comparison/52week-return",
                                source=["prices"],
                                text=(
                                    f"{sample[0][0]} 至 {end}，前复权收盘 "
                                    f"{sample[0][1]} → {sample[-1][1]}；"
                                    "价格收益=(末日/首日-1)×100；不是全收益或未来回报。"
                                ),
                            ),
                        )
                        signature = tuple(str(dt) for dt, _ in sample)
                except (KeyError, ValueError, ArithmeticError, IndexError):
                    pass
            # The current reports have cumulative ROE and cash-flow observations.
            # They cannot be substituted for TTM ROE or a matched net-cash ratio.
            cells.append(cell)
            dates.append(period)
            signatures.append(signature)
        available = all(c is not None for c in cells)
        if key in {"growth", "valuation"} and any(
            not d["subject"].get("company_type")
            or (key == "growth" and not d.get("revenue_basis"))
            or (key == "valuation" and not d["subject"].get("industry"))
            for d in payloads
        ):
            signatures = [None, None]
        aligned = available and signatures[0] == signatures[1] and signatures[0] is not None
        rows.append(
            dict(
                key=key,
                label=label,
                caption=caption,
                unit=unit,
                cells=cells,
                dates=dates,
                difference=float(Decimal(str(cells[0]["value"])) - Decimal(str(cells[1]["value"])))
                if aligned
                else None,
                alignment="ALIGNED"
                if aligned
                else "METHOD_DIFFERENCE"
                if available
                else "UNAVAILABLE",
                note="期间与定义对齐，仍未完成外部独立核验；差值不构成投资排名。"
                if aligned
                else "缺少同口径输入或覆盖不同；不以累计ROE、最新PE或错期现金流替代。",
            )
        )
    result = dict(
        version=VERSION, child_result_refs=[s["result_ref"] for s in children], dimensions=rows
    )
    result["hash"] = digest(result)
    return result
