from marketpulse.quant.live.securities import parse_candidates


def test_tencent_star_market_security_type_is_an_a_share():
    # Actual provider type captured during 2026-10-12 P0 source audit.
    raw = 'v_hint="sh~688012~中微公司~zwgs~GP-A-KCB"'
    result = parse_candidates(raw)
    assert len(result) == 1
    assert result[0]["code"] == "688012"
    assert result[0]["name"] == "中微公司"


def test_star_type_does_not_authorize_unknown_market_or_non_a_share():
    for raw in [
        'v_hint="hk~688012~中微公司~zwgs~GP-A-KCB"',
        'v_hint="sh~688012~中微公司~zwgs~FUND"',
    ]:
        assert parse_candidates(raw) == []
