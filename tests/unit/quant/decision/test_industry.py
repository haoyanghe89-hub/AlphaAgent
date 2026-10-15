"""D01/D03/D05 development; D02/D04/D06 counterexample holdouts use separate fixtures."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from marketpulse.quant.decision import industry, industry_sources
from marketpulse.quant.decision.contracts import ResearchRequest


def test_d01_role_changes_metric_priority():
    assert (
        industry.business_role(dict(description="本公司从事晶圆代工，采购半导体设备用于生产。"))
        == "MANUFACTURING"
    )
    assert (
        industry.business_role(dict(description="专注半导体设备的研发、制造和销售。"))
        == "EQUIPMENT"
    )
    assert industry.business_role(dict(description="提及半导体设备但业务未知")) == "UNCONFIGURED"
    assert industry.metric_priority("EQUIPMENT", "EQUIPMENT_BILLINGS") == "PRIMARY_CONTEXT"
    assert industry.metric_priority("MANUFACTURING", "EQUIPMENT_BILLINGS") == "BACKGROUND_ONLY"
    assert industry.metric_priority("MANUFACTURING", "UTILIZATION") == "PRIMARY_CONTEXT"


def test_d03_unmatched_memory_region_cannot_directly_transmit():
    background = dict(geography="海外", product="存储", process="先进封装")
    exposure = dict(geography="国内", product="刻蚀设备", process="晶圆制造")
    assert not industry.direct_transmission(background, exposure)
    assert not industry.direct_transmission({}, {})


def test_d05_sia_and_company_repeating_wsts_are_one_underlying_statistic():
    assert industry.independent_statistics(
        [dict(underlying_statistics="WSTS", publisher=p) for p in ("WSTS", "SIA", "公司")]
    ) == ["WSTS"]


def test_d02_holdout_wrong_company_even_similar_name():
    target = dict(security_id="688012.SH", name="中微公司")
    assert not industry.same_identity(target, dict(security_id="688380.SH", name="中微半导"))


@pytest.mark.parametrize("kind", ["CONTRACT_LIABILITY", "PRODUCT_VALIDATION", "R_AND_D"])
def test_d04_holdout_contract_liability_not_full_orders(kind):
    assert not industry.order_supported(kind)


def test_d06_holdout_later_forecast_revision_keeps_old_available_version():
    old = dict(version="old", available_at="2026-06-01T00:00:00+00:00")
    new = dict(version="replacement", available_at="2026-08-06T00:00:00+00:00")
    assert industry.available_versions([old, new], "2026-07-01T00:00:00+00:00") == [old]
    assert industry.available_versions([old, new], "2026-09-01T00:00:00+00:00") == [old, new]


def test_original_html_excludes_scripts_and_off_host_links():
    raw = (
        b'<script>ignore</script><a href="/76/new">Global Semiconductor Market new</a>'
        b'<a href="https://evil.example">Global Semiconductor Market fake</a>'
    )
    assert "ignore" not in industry_sources.html_text(raw).text
    assert industry_sources.article_links(raw, "https://www.wsts.org/76/PRESS-ARCHIVE", "WSTS") == [
        ("https://www.wsts.org/76/new", "Global Semiconductor Market new")
    ]


def test_industry_disallows_historical_backfill_or_unbound_flows():
    with pytest.raises(ValueError):
        ResearchRequest(request_key=uuid4(), entity="X", question="X", include_industry=True)
    with pytest.raises(ValueError):
        ResearchRequest(
            request_key=uuid4(),
            entity="X",
            question="X",
            include_industry=True,
            include_flows=True,
            research_mode="medium_term",
            mode="historical",
            cutoff_at=datetime.now(UTC),
        )
