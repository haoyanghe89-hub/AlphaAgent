"""Transport failures stay visible and consume the original request budget."""

from threading import Event

import pytest
import requests

from marketpulse.quant.decision.market import number
from marketpulse.quant.decision.tools import SourceClient, SourceUnavailable


def test_connection_retry_is_bounded_and_never_resets_budget(monkeypatch):
    class Repository:
        def blob(self, value):
            return "synthetic-request"

    count = 0

    def unavailable(*args, **kwargs):
        nonlocal count
        count += 1
        raise requests.ConnectTimeout("synthetic timeout")

    monkeypatch.setattr(requests, "request", unavailable)
    state = dict(request=dict(budget_profile=dict(max_seconds=60)), calls=[])
    client = SourceClient(Repository(), state, lambda: None, Event(), max_requests=2)
    with pytest.raises(SourceUnavailable):
        client.fetch(
            "catalog", "https://www.cninfo.com.cn/new/hisAnnouncement/query", family="CNINFO"
        )
    assert count == 2
    assert len(state["calls"]) == 2
    assert all(call["status"] == "FAILED" for call in state["calls"])
    with pytest.raises(SourceUnavailable, match="上限"):
        client.fetch(
            "catalog", "https://www.cninfo.com.cn/new/hisAnnouncement/query", family="CNINFO"
        )
    assert count == 2


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity"])
def test_financing_amounts_reject_invalid_source_values(value):
    with pytest.raises(ValueError):
        number(value, nonnegative=True)
