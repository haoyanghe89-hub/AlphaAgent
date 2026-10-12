"""Industry applicability and statement identity without live upstream dependencies."""

import pytest

from marketpulse.quant.live.report import dcf_for_subject, financial_statements


def test_bank_parent_profit_and_equity_aliases_preserve_the_actual_values():
    packet = dict(
        result=dict(
            data=dict(
                report_list={
                    "20260630": dict(
                        publish_date="20260829",
                        rCurrency="CNY",
                        rType="合并期末",
                        data=[
                            dict(item_title="归属于母公司的净利润", item_value="100"),
                            dict(item_title="归属于母公司股东的权益", item_value="1000"),
                        ],
                    )
                }
            )
        )
    )
    report = financial_statements(packet, "2026-10-10")["20260630"]
    assert report["fields"]["归属于母公司所有者的净利润"] == ["100"]
    assert report["fields"]["归属于母公司股东权益合计"] == ["1000"]


@pytest.mark.parametrize("company_type", ["1", "2", "3"])
def test_financial_company_cannot_be_given_an_industrial_dcf(company_type):
    result = dcf_for_subject(dict(company_type=company_type), {}, {}, "2026-10-10")
    assert result["status"] == "NOT_APPLICABLE"
    assert "per_share" not in result


def test_missing_industrial_inputs_are_never_filled_with_zero():
    result = dcf_for_subject(dict(company_type="4", code="000651"), {}, {}, "2026-10-10")
    assert result["status"] == "UNAVAILABLE"
    assert "per_share" not in result
