"""Funding definitions, minute windows, integrity and conservative recovery."""

import hashlib
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.quant.live import main_flow as m
from marketpulse.quant.live.store import ResearchStore

NOW = datetime.fromisoformat("2026-10-15T15:01:00+08:00")


def packet():
    start = NOW.replace(hour=14, minute=40)
    points = []
    for index in range(21):
        points.append(
            dict(
                time=(start + timedelta(minutes=index)).strftime("%Y%m%d%H%M"),
                MainNetInflow=str(index * 100),
                BigNetInflow=str(index * 60),
                SuperNetInflow=str(index * 40),
                MainInflow=str(index * 200),
                MainOutflow=str(index * 100),
                Price=str(10 - index / 100),
            )
        )
    return dict(
        code=0,
        data=dict(
            ffHide="",
            todayFundFlow=dict(stockCode="sh600000"),
            todayFundTrend=dict(stockCode="sh600000", minList=points),
        ),
    )


def test_minute_net_is_cumulative_and_window_is_difference():
    result = m.minute_projection(packet(), "sh600000", NOW)
    assert result["latest"]["main"] == 2000 and len(result["points"]) == 21
    assert [w["main"] for w in result["windows"]] == [500, 1500]
    assert result["windows"][0]["divergence"] == "PRICE_DOWN_FLOW_IN"
    assert result["windows"][0]["price_change"] < 0


@pytest.mark.parametrize("change", ["identity", "duplicate", "future", "arithmetic", "nan"])
def test_minute_provider_boundary_rejections(change):
    value = packet()
    rows = value["data"]["todayFundTrend"]["minList"]
    if change == "identity":
        value["data"]["todayFundFlow"]["stockCode"] = "sh600001"
    elif change == "duplicate":
        rows.append(rows[-1])
    elif change == "future":
        rows[-1]["time"] = "202610161500"
    elif change == "arithmetic":
        rows[-1]["MainNetInflow"] = "99999"
    else:
        rows[-1]["Price"] = "NaN"
    with pytest.raises(ValueError):
        m.minute_projection(value, "sh600000", NOW)


def test_missing_minute_and_lunch_never_create_short_windows():
    value = packet()
    value["data"]["todayFundTrend"]["minList"].pop(-3)
    result = m.minute_projection(value, "sh600000", NOW)
    assert all(w["main"] is None for w in result["windows"])


def daily_inputs():
    rows = [
        dict(
            ts_code="600000.SH",
            trade_date="20261015",
            buy_lg_amount="1.50",
            sell_lg_amount="2",
            buy_elg_amount="0",
            sell_elg_amount="1",
            net_mf_amount="999999",
        )
    ]
    master = [dict(ts_code="600000.SH", name="合成甲", list_status="L")]
    classification = dict(
        groups=[dict(id="industry1", kind="industry", name="合成行业", symbols=["sh600000"])]
    )
    return rows, master, classification


def test_daily_main_proxy_uses_large_extra_not_total_net_and_converts_units():
    result = m.daily_projection(*daily_inputs(), "2026-10-15")
    assert result["stocks"][0]["main"] == -15000
    assert result["industries"][0]["main"] == -15000
    assert result["stocks"][0]["inflow"] == 15000
    assert result["coverage"] == dict(industries=1, stocks=1)
    assert result["asof"] == "2026-10-15"  # The provider supplies a date, not a quote time.


@pytest.mark.parametrize("change", ["missing", "duplicate", "day", "overlap", "negative"])
def test_daily_partial_and_mismatched_values_cannot_rank(change):
    rows, master, classification = daily_inputs()
    if change == "missing":
        classification["groups"][0]["symbols"].append("sh600001")
    elif change == "duplicate":
        rows.append(rows[0])
    elif change == "day":
        rows[0]["trade_date"] = "20261016"
    elif change == "overlap":
        classification["groups"].append(dict(classification["groups"][0], id="industry2"))
    else:
        rows[0]["buy_lg_amount"] = "-1"
    with pytest.raises(ValueError):
        m.daily_projection(rows, master, classification, "2026-10-15")


def test_cache_replays_real_original_and_failed_refresh_never_moves_timestamp(tmp_path):
    repo = ResearchStore(
        None, LocalContentAddressedBlobStorage(tmp_path / "blobs"), tmp_path / "old"
    )
    feed = m.MainFlowFeed(repo, None)
    raw = b'{"synthetic":"original"}'
    source = dict(blob_ref=repo.raw(raw), sha256=hashlib.sha256(raw).hexdigest())
    value = dict(
        asof="2026-10-15T15:00:00+08:00", retrieved_at="2026-10-15T07:00:00+00:00", sources=[source]
    )
    result = feed.cached("sh600000", lambda: value, 25)
    assert result["asof"] == value["asof"]
    feed.attempted["sh600000"] = 0

    def fail():
        raise ValueError("sensitive upstream error")

    after = feed.cached("sh600000", fail, 25)
    assert after["retrieved_at"] == value["retrieved_at"] and after["refresh_error"]
    assert "sensitive" not in str(after)
    feed2 = m.MainFlowFeed(repo, None)
    assert feed2.load("sh600000") == value


def test_read_only_api_scope_and_safe_failure(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from marketpulse.quant.live.research_api import router

    app = FastAPI()
    app.include_router(router)

    def fail(*args):
        raise ValueError("sensitive transport")

    app.state.main_flow = SimpleNamespace(daily=fail, stock=fail)
    with TestClient(app) as client:
        assert client.get("/api/quant/research/main-flow/invalid").status_code == 422
        for path in ["/api/quant/research/main-flow", "/api/quant/research/main-flow/sh600000"]:
            response = client.get(path)
            assert response.status_code == 503 and "sensitive" not in response.text
