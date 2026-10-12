"""Official financial crosschecks; document evidence never proves complete PIT archives."""

from datetime import date
from decimal import Decimal, localcontext

from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.investigation.api import _error

from ..compute.financials import ttm_profit, yoy_growth
from ..contracts import digest
from ..data.statement_official import ISSUER, PROFILES, extract_statement
from ..domain import FinancialFact, decimal_text


def compare_value(value, reference, basis, reference_basis, tolerance):
    result = dict(
        value=value,
        reference=reference,
        basis=basis,
        reference_basis=reference_basis,
        tolerance=tolerance,
    )
    if value is None or reference is None:
        return {**result, "status": "UNAVAILABLE"}
    with localcontext() as context:
        context.prec = 40
        a, b, t = Decimal(value), Decimal(reference), Decimal(tolerance)
        if not all(v.is_finite() for v in (a, b, t)) or t < 0:
            raise ValueError("finite crosscheck and nonnegative tolerance required")
        if basis != reference_basis:
            return {**result, "status": "METHOD_DIFFERENCE"}
        return {**result, "status": "MATCH" if abs(a - b) <= t else "CONFLICT"}


def derive_financials(observations):
    facts = []
    for row in sorted(observations, key=lambda r: r["period_end"]):
        end = date.fromisoformat(row["period_end"])
        for metric, scope in [("parent_net_profit", "parent"), ("revenue", "consolidated")]:
            facts.append(
                FinancialFact(
                    metric=metric,
                    period_end=end,
                    period_start=date(end.year, 1, 1),
                    available_at=None,
                    value=row[metric],
                    unit="CNY",
                    share_basis="not_applicable",
                    statement_basis=scope,
                    period_basis="annual" if end.month == 12 else "ytd",
                    revision="reviewed:" + row["source_hash"],
                    source_hash=row["source_hash"],
                )
            )
    ttm = ttm_profit(facts)
    growth, missing = yoy_growth(facts, "revenue")
    result = dict(
        metric_status="UNVERIFIED",
        input_hash=digest([f.model_dump(mode="json") for f in facts]),
        ttm_parent_profit=dict(
            value=decimal_text(ttm.value) if ttm else None,
            unit="CNY",
            formula="上一年全年 − 上一年同期累计 + 本期累计",
            crosscheck_status="UNAVAILABLE",
            reason="三期官方基础数值可定位；未取得独立公布的同口径TTM基准，且历史版本未准入。",
        ),
        revenue_yoy=dict(
            value=decimal_text(growth) if growth is not None else None,
            unit="fraction",
            formula="本期累计营业收入 ÷ 上年同期累计营业收入 − 1",
            missing_reason=missing,
        ),
    )
    result["output_hash"] = digest(result)
    return result


def filing_target(runtime, parent_id):
    from .acquisition import packets_for
    from .api import load_check

    parent = load_check(runtime, parent_id)
    audit = parent.get("input_audit")
    if parent["check_status"] != "COMPLETED" or not audit:
        raise _error(422, "FILING_PARENT_INCOMPLETE", "请先完成股票历史输入核验。")
    scope = audit["scope"]
    if scope["instrument_id"] != ISSUER or scope["signal_year"] != 2024:
        raise _error(
            422,
            "FILING_PROFILE_UNAVAILABLE",
            "该证券及年度尚无已复核的官方原文提取配置，不能猜测财报口径。",
        )
    with runtime.service.sessions() as session:
        raw = packets_for(session, runtime.service.store, parent_id)
    profits = [p for p in raw if p["query"]["kind"] == "profit"]
    if len(profits) != 3:
        raise _error(422, "FILING_NATIVE_INPUT_MISSING", "父版本缺少三期冻结财务响应。")
    return dict(
        scope=scope,
        profile_ids=list(PROFILES),
        profile_hash=digest(PROFILES),
        native_packets=profits,
        parent_id=parent_id,
    )


def inspect_filings(packets, target, store):
    if target["profile_hash"] != digest(PROFILES):
        raise ValueError("reviewed official profile drift")
    observations, sources, checks, seen = [], [], [], set()
    native = {p["rows"][0]["statDate"]: p for p in target["native_packets"] if len(p["rows"]) == 1}
    for packet in packets:
        profile_id = packet["query"].get("profile_id")
        if (
            packet["query"] != dict(kind="official_filing", profile_id=profile_id)
            or profile_id not in target["profile_ids"]
            or profile_id in seen
            or packet["provider"] != "cninfo_official_reviewed"
        ):
            raise ValueError("official response scope mismatch")
        seen.add(profile_id)
        profile = PROFILES[profile_id]
        document = store.blobs.get_bytes(BlobRef(profile["sha256"]))
        if len(document) != packet["document_size"]:
            raise ValueError("official document size mismatch")
        row = extract_statement(document, profile_id)
        if packet["rows"] != [row]:
            raise ValueError("official extraction cannot reproduce")
        observation = {
            **row,
            "date_observation": "SAME_DAY_TIME_UNKNOWN"
            if row["publication_date"] == target["scope"]["signal_day"]
            else "BEFORE_SIGNAL"
            if row["publication_date"] < target["scope"]["signal_day"]
            else "AFTER_SIGNAL",
            "pit_level": "PARTIAL",
            "version_status": "REVISION_ARCHIVE_INCOMPLETE",
        }
        observations.append(observation)
        sources.append(
            dict(
                profile_id=profile_id,
                source_url=profile["url"],
                document_sha256=profile["sha256"],
                packet_sha256=digest(packet),
                retrieved_at=packet["retrieved_at"],
                source_pages=row["source_pages"],
            )
        )
        supplier = native.get(row["period_end"])
        values = supplier["rows"][0] if supplier else {}
        supplier_hash = digest(supplier) if supplier else None
        for name, field, ref, basis, ref_basis, tolerance in [
            (
                "供应商净利润与官方合并净利润",
                "netProfit",
                "consolidated_net_profit",
                "consolidated",
                "consolidated",
                "1000",
            ),
            (
                "将供应商netProfit用于归母利润",
                "netProfit",
                "parent_net_profit",
                "consolidated",
                "parent",
                "1000",
            ),
            (
                "供应商平均ROE与公告加权ROE",
                "roeAvg",
                "weighted_roe",
                "vendor_average_unconfirmed",
                "issuer_weighted",
                "0.00005",
            ),
        ]:
            checks.append(
                dict(
                    name=name,
                    period_end=row["period_end"],
                    source_hash=supplier_hash,
                    document_sha256=profile["sha256"],
                    **compare_value(
                        values.get(field) or None, row[ref], basis, ref_basis, tolerance
                    ),
                )
            )
        # Independent summary-versus-formula check of the disclosed YoY, with a
        # predeclared half-unit rounding tolerance at two percentage decimals.
        with localcontext() as context:
            context.prec = 40
            computed = decimal_text(Decimal(row["revenue"]) / Decimal(row["previous_revenue"]) - 1)
        checks.append(
            dict(
                name="自算营收同比与公告披露同比",
                period_end=row["period_end"],
                source_hash=profile["sha256"],
                document_sha256=profile["sha256"],
                **compare_value(
                    computed, row["revenue_yoy"], "consolidated_ytd", "consolidated_ytd", "0.00005"
                ),
            )
        )
    observations.sort(key=lambda r: r["period_end"])
    derived = derive_financials(observations if len(seen) == len(target["profile_ids"]) else [])
    return dict(
        schema_version="backtest-filing-audit-v1",
        scope=target["scope"],
        documents=observations,
        sources=sources,
        checks=checks,
        derived=derived,
        complete=len(seen) == len(target["profile_ids"]),
        execution_status="BLOCKED",
        metric_status="UNVERIFIED",
        original_revision_archive_complete=False,
        gaps=[
            "仅覆盖美的集团2024年信号的三份已复核中文官方财报，不是全市场覆盖。",
            "原文可定位不等于最初及各次修订档案完整；未证明历史准确可得时间，同日披露仍未知。",
            "netProfit数值与合并利润匹配也不能用作归母利润；ROE平均与加权口径不自动等同。",
            "TTM只按官方归母累计数复算，独立同口径TTM基准缺失；营收同比核对MATCH不代表整体VERIFIED。",
            "PE、平均权益ROE、选股、交易、净值和绩效均未执行；公司行为与成交规则仍须准入。",
        ],
    )
