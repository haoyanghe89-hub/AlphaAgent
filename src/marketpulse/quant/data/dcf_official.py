"""Reviewed, document-bound Midea 2023 annual input profile.

This is a statement extraction profile, not prototype calculation code. New
issuers require their own scope and statement review before admission.
"""

import hashlib
import re
from datetime import UTC, datetime
from decimal import Decimal

from ..compute.dcf import FIELDS
from ..contracts import canonical
from ..domain import DataRequest, DataResult, DataRights, PITLevel

URL = "https://static.cninfo.com.cn/finalpage/2024-03-28/1219429374.PDF"
DOCUMENT_HASH = "22f4915d91ada74a37eab8d074aef4c15420f3b1a8b5f5bd5777c00f97d58b86"
PROFILE = {
    "profit_before_tax": (161, r"三、利润总额\s+([\d,]+)", 1),
    "income_tax": (161, r"所得税费用\s+四\(61\)\s+\(([\d,]+)\)", 1),
    "interest_expense": (161, r"其中：利息费用\s+([\d,]+)", 1),
    "interest_income": (161, r"利息收入\s+([\d,]+)", 1),
    "investment_income": (161, r"投资收益\s+四\(58\),\s+十八\(4\)\s+([\d,]+)", 1),
    "fair_value_gain": (161, r"四\(57\)\s+\(([\d,]+)\)", -1),
    "nonoperating_income": (161, r"营业外收入\s+([\d,]+)", 1),
    "nonoperating_expense": (161, r"营业外支出\s+\(([\d,]+)\)", 1),
    "depreciation": (254, r"折旧和摊销\s+([\d,]+)", 1),
    "capex": (163, r"购建固定资产、无形资产和其他长期资产支付的\s+现金\s+\(([\d,]+)\)", 1),
    "inventory_decrease": (254, r"存货的减少/\(增加\)\s+([\d,]+)", 1),
    "receivables_increase": (254, r"经营性应收项目的增加\s+\(([\d,]+)\)", 1),
    "payables_increase": (254, r"经营性应付项目的增加\s+([\d,]+)", 1),
    "cash": (163, r"六、年末现金及现金等价物余额\s+四\(63\)\(i\)\s+([\d,]+)", 1),
    "short_debt": (160, r"短期借款\s+四\(27\)\s+([\d,]+)", 1),
    "long_debt": (160, r"长期借款\s+四\(37\)\s+([\d,]+)", 1),
    "bonds": (160, r"应付债券\s+四\(38\)\s+([\d,]+)", 1),
    "leases": (160, r"租赁负债\s+四\(39\)\s+([\d,]+)", 1),
    "current_debt": (160, r"一年内到期的非流动负债\s+四\(35\)\s+([\d,]+)", 1),
    "minority_equity": (160, r"少数股东权益\s+([\d,]+)", 1),
    "issued_shares": (141, r"三、股份总数[^\n]*?([\d,]+)\s+100\.00\s*$", 1),
    "treasury_shares": (144, r"回购专用证券账户报告期末持有的普通股数量为\s+([\d,]+)", 1),
}


def extract_midea_2023(document, instrument_id, asof):
    from io import BytesIO

    from pypdf import PdfReader

    if instrument_id != "ins_c317bc1b5fc95003aa6e556c8045a5b8":
        raise ValueError("reviewed profile only applies to Midea")
    if hashlib.sha256(document).hexdigest() != DOCUMENT_HASH:
        raise ValueError("annual document differs from reviewed version")
    pages = PdfReader(BytesIO(document)).pages
    raw, normalized = {}, {}
    for field, (page, pattern, sign) in PROFILE.items():
        text = pages[page - 1].extract_text()
        if field not in {"issued_shares", "treasury_shares"} and "人民币千元" not in text:
            raise ValueError("annual monetary unit mismatch")
        matches = re.findall(pattern, text, re.M)
        if len(matches) != 1:
            raise ValueError("ambiguous annual extraction: " + field)
        amount = Decimal(matches[0].replace(",", "")) * sign
        raw[field] = str(amount)
        normalized[field] = str(amount * (1 if field.endswith("shares") else 1000))
    # Fixed, independently transcribed checkpoints prevent confident parser mistakes.
    checkpoints = dict(
        profit_before_tax="40277163",
        income_tax="6531811",
        depreciation="7348921",
        capex="6314051",
        cash="59887260",
        issued_shares="7025769025",
        treasury_shares="118366752",
        current_debt="14457710",
        minority_equity="11420824",
    )
    if any(raw[k] != v for k, v in checkpoints.items()):
        raise ValueError("authoritative annual checkpoints differ")
    row = dict(
        instrument_id=instrument_id,
        period_start="2023-01-01",
        period_end="2023-12-31",
        available_at="2024-03-29T09:30:00+08:00",
        model_scope="industrial_consolidated_proxy",
        source_url=URL,
        document_sha256=DOCUMENT_HASH,
        source_pages={k: v[0] for k, v in PROFILE.items()},
        **normalized,
    )
    request = DataRequest(
        instruments=(instrument_id,),
        dataset="financial",
        start="2023-01-01",
        end="2023-12-31",
        asof=asof,
        fields=("dcf_annual_industrial",),
        normalizer_version="dcf-official-v1",
    )
    return DataResult(
        provider="cninfo_official_reviewed",
        upstream="Midea_2023_annual",
        source_family="midea_issuer",
        lineage_status="verified",
        request=request,
        retrieved_at=datetime.now(UTC),
        records_json=canonical([row]),
        raw_records_json=canonical([raw]),
        units=tuple((k, "share" if k.endswith("shares") else "CNY") for k in FIELDS),
        pit=PITLevel.PARTIAL,
        quality_flags=(
            "manual_reviewed_annual_profile",
            "financial_subsidiary_not_separated",
            "NCWC_cash_reconciliation_proxy",
            "book_value_bridge_not_market_value",
        ),
        crosscheck_json=canonical(
            dict(
                status="OFFICIAL_INPUT_MATCH",
                checks=checkpoints,
                independent_producer=False,
                projection_verified=False,
            )
        ),
        provenance_json=canonical(
            dict(
                document_sha256=DOCUMENT_HASH,
                source_url=URL,
                monetary_unit="RMB_thousand_x1000",
                share_unit="share",
            )
        ),
        rights=DataRights(
            policy_id="public_filing_internal_v1",
            provider="cninfo_official_reviewed",
            upstream="Midea_2023_annual",
            entitlement="public_filing_internal",
            attribution="美的2023年报 / 巨潮资讯",
            license_reference=URL,
            raw_export=False,
            external_share=False,
        ),
    )
