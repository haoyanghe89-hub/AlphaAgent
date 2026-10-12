import pytest

from marketpulse.quant.live.securities import choose, financial_type, parse_candidates


def test_same_code_index_does_not_hide_the_stock():
    text = 'v_hint="sh~000001~上证指数~szzs~ZS^sz~000001~平安银行~payh~GP-A^jj~000001~基金~jj~KJ"'
    candidates = parse_candidates(text)
    assert choose(candidates, "000001")["symbol"] == "sz000001"


def test_ambiguous_name_requires_user_selection_and_exact_code_is_safe():
    candidates = [
        dict(code="600000", name="浦发银行", symbol="sh600000"),
        dict(code="000001", name="平安银行", symbol="sz000001"),
    ]
    with pytest.raises(ValueError, match="多只"):
        choose(candidates, "银行")
    assert choose(candidates, "平安银行")["code"] == "000001"
    with pytest.raises(ValueError):
        choose([], "../../etc")


def test_industry_is_a_method_boundary_not_a_name_guess():
    assert financial_type("金融-银行-股份制与城商行") == "1"
    assert financial_type("金融-非银行金融-保险") == "2"
    assert financial_type("金融-非银行金融-证券") == "3"
    assert financial_type("家电-白色家电") == "4"
