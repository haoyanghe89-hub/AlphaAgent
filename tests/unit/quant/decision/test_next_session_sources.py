import json
from datetime import date
from types import SimpleNamespace

import pytest

from marketpulse.quant.decision.next_session.snapshot import daily_input
from marketpulse.quant.live.sources import KLINE


def test_tushare_disclosure_inputs_get_separate_tencent_bars():
    observed = []
    original = dict(key="daily", source_family="TENCENT", blob_ref="test")
    packet = dict(data=dict(SYNTHETIC="unadjusted day rows"))

    def fetch(*args, **kwargs):
        observed.append((args, kwargs))
        return original, packet

    source, result = daily_input(
        None,
        SimpleNamespace(fetch=fetch),
        [dict(key="tushare_daily_public", source_family="TUSHARE")],
        "sz000725",
        date(2026, 10, 16),
    )
    assert source == original and result == packet
    assert observed[0][0] == ("daily", KLINE)
    assert observed[0][1]["family"] == "TENCENT"
    assert observed[0][1]["params"]["param"].startswith("sz000725,day,")


def test_old_archived_tencent_source_is_reused_without_network():
    source = dict(key="daily", source_family="TENCENT", blob_ref="test")
    packet = dict(data=dict(SYNTHETIC="original bytes"))
    repo = SimpleNamespace(raw_bytes=lambda _: json.dumps(packet).encode())
    client = SimpleNamespace(fetch=lambda *_: pytest.fail("old source must not change"))
    assert daily_input(repo, client, [source], "sz000725", date(2026, 10, 16)) == (source, packet)
    with pytest.raises(ValueError, match="冲突"):
        daily_input(repo, client, [source, source], "sz000725", date(2026, 10, 16))
