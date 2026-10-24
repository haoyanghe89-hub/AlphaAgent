"""Synthetic boundary tests; current-market acceptance uses the real provider separately."""

import concurrent.futures
import json
from datetime import datetime

import pytest

from marketpulse.quant.live import market_dashboard as m

NOW = datetime.fromisoformat("2026-10-15T14:00:00+08:00")


def raw_quote(symbol="sh600000", stamp="20261015135959", amount="123456789", change="-2.50"):
    fields = [""] * 48
    for index, value in {
        1: "合成甲",
        2: symbol[2:],
        3: "9.75",
        4: "10",
        30: stamp,
        32: change,
        35: "9.75/123/" + amount,
        37: "12345",
        45: "20.5",
    }.items():
        fields[index] = value
    return "v_" + symbol + '="' + "~".join(fields) + '";'


def test_quote_units_timestamp_and_missing_values():
    rows = m.quote_rows(raw_quote(), ["sh600000"], NOW)
    q = rows["sh600000"]
    assert q["amount"] == 123456789 and q["market_cap"] == 2050000000
    assert q["change"] == -2.5 and q["quoted_at"] == "2026-10-15T13:59:59+08:00"
    assert not m.quote_rows(raw_quote(stamp="20261016135959"), ["sh600000"], NOW)
    assert not m.quote_rows(raw_quote(amount="NaN"), ["sh600000"], NOW)
    assert not m.quote_rows(raw_quote(), ["sh600001"], NOW)
    assert (
        m.quote_rows(raw_quote(amount="0", change="0"), ["sh600000"], NOW)["sh600000"]["amount"]
        == 0
    )


def test_catalog_is_json_not_executable_script():
    assert m.catalog('var x={"gn_a":"gn_a,合成概念,3"};', "concept")[0]["name"] == "合成概念"
    for bad in ["var x={};doEvil()", 'var x={"gn_a":"gn_b,错配"}', "var x={}"]:
        with pytest.raises(ValueError):
            m.catalog(bad, "concept")


def test_members_read_every_page_and_bind_count():
    requested = []

    def fetch(url, params):
        requested.append((url, params))
        if url == m.COUNT:
            return '"101"', {}
        rows = (
            [dict(symbol=f"sh{600000 + i:06}") for i in range(100)]
            if params["page"] == 1
            else [dict(symbol="sz000001")]
        )
        return json.dumps(rows), {}

    result = m.members(dict(id="gn_a"), fetch)
    assert len(result["symbols"]) == 101
    assert [p["page"] for u, p in requested if u == m.NODES] == [1, 2]
    assert all(p["num"] == 100 for u, p in requested if u == m.NODES)


def test_members_reject_repeated_or_truncated_pages():
    def repeated(url, params):
        return json.dumps([dict(symbol=f"sh{600000 + i:06}") for i in range(100)]), {}

    with pytest.raises(ValueError, match="repeated"):
        m.members(dict(id="gn_a"), repeated)

    def truncated(url, params):
        return ('"2"' if url == m.COUNT else '[{"symbol":"sh600000"}]'), {}

    with pytest.raises(ValueError, match="truncated"):
        m.members(dict(id="gn_a"), truncated)


def test_rank_excludes_missing_cross_date_and_inactive_quotes():
    q = m.quote_rows(raw_quote(), ["sh600000"], NOW)["sh600000"]
    q2 = dict(q, symbol="sh600001", code="600001", amount=100, market_cap=1, change=5)
    q3 = dict(q, symbol="sh600002", code="600002", amount=0, change=0)
    groups = [
        dict(id="i", name="合成行业", kind="industry", symbols=["sh600000", "sh600001", "sh600002"])
    ]
    quotes = {v["symbol"]: v for v in [q, q2, q3]}
    result = m.rank_groups(groups, quotes, "2026-10-15")[0]
    assert result["change"] == 1.25 and result["amount"] == 123456889
    assert result["active_count"] == 2 and result["up"] == result["down"] == 1
    assert [r["symbol"] for r in result["leaders"]] == ["sh600000", "sh600001"]
    assert len(result["trading_leaders"]) == 2
    assert not m.rank_groups(groups, {"sh600000": q}, "2026-10-15")
    assert not m.rank_groups(groups, quotes, "2026-10-16")


def example():
    return dict(
        quoted_to="2026-10-15T14:00:00+08:00",
        retrieved_at="2026-10-15T06:00:00+00:00",
        industries=[dict(name="合成")],
    )


def test_cache_single_flight_and_failures_keep_original_times(tmp_path):
    feed = m.MarketDashboard(tmp_path)
    calls = []

    def collect():
        calls.append(1)
        return example()

    feed.collect = collect
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: feed.snapshot(), range(5)))
    assert len(calls) == 1 and len(results) == 5
    feed.attempted = 0

    def fail():
        raise ValueError("synthetic service down")

    feed.collect = fail
    value = feed.snapshot()
    assert value["refresh_error"] and value["freshness"] == "DELAYED"
    assert value["retrieved_at"] == example()["retrieved_at"]
    fresh = m.MarketDashboard(tmp_path)
    fresh.collect = fail
    assert fresh.snapshot()["quoted_to"] == example()["quoted_to"]


def test_first_load_failure_does_not_invent_rows(tmp_path):
    feed = m.MarketDashboard(tmp_path)
    feed.collect = lambda: (_ for _ in ()).throw(ValueError("synthetic"))
    with pytest.raises(ValueError, match="unavailable"):
        feed.snapshot()


def test_partial_quote_refresh_never_erases_complete_market(tmp_path):
    feed = m.MarketDashboard(tmp_path)
    complete = dict(
        example(),
        coverage=dict(
            industries_ranked=31,
            industries_expected=31,
            stocks_quoted=5000,
            stocks_expected=5000,
            quote_batches_failed=0,
        ),
    )
    feed.collect = lambda: complete
    feed.snapshot()
    feed.attempted = 0
    feed.collect = lambda: dict(
        complete,
        coverage=dict(
            complete["coverage"], industries_ranked=1, stocks_quoted=4700, quote_batches_failed=1
        ),
    )
    result = feed.snapshot()
    assert result["coverage"]["industries_ranked"] == 31
    assert result["refresh_error"] and result["freshness"] == "DELAYED"
    assert result["retrieved_at"] == complete["retrieved_at"]


@pytest.fixture
def preopen_market(monkeypatch, tmp_path):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat("2026-10-15T09:20:00+08:00").astimezone(tz)

    monkeypatch.setattr(m, "datetime", Clock)
    symbols = ["sh600000", "sz000001"]
    feed = m.MarketDashboard(tmp_path)
    feed.classification = lambda: dict(
        groups=[
            dict(id="i", name="合成行业", kind="industry", symbols=symbols),
            dict(id="c", name="合成概念", kind="concept", symbols=symbols),
        ],
        sources=[],
        expected=2,
        industry_expected=1,
        concept_expected=1,
        unclassified=0,
        retrieved_at="2026-10-15T00:00:00+00:00",
    )
    feed.fetch = lambda url: (
        "".join(raw_quote(s, stamp="20261015091959", amount="0") for s in symbols),
        {},
    )
    previous = dict(example(), quoted_to="2026-10-14T15:00:00+08:00")
    m.write(tmp_path / "snapshot.json", previous)
    return feed, previous


def test_preopen_zero_turnover_retains_snapshot_without_refresh_error(preopen_market):
    feed, previous = preopen_market
    feed.error = "previous refresh failed"
    result = feed.snapshot()
    assert result["refresh_error"] is None
    assert result["freshness"] == "LATEST_QUOTE"
    assert result["quoted_to"] == previous["quoted_to"]
    assert result["retrieved_at"] == previous["retrieved_at"]
    assert json.loads((feed.root / "snapshot.json").read_text(encoding="utf8")) == previous


def test_preopen_executed_trades_refresh_rankings(preopen_market):
    feed, _ = preopen_market
    feed.fetch = lambda url: (
        "".join(raw_quote(s, stamp="20261015091959") for s in ["sh600000", "sz000001"]),
        {},
    )
    result = feed.snapshot()
    assert result["refresh_error"] is None
    assert result["quote_date"] == "2026-10-15"
    assert result["coverage"]["stocks_quoted"] == 2
    assert len(result["industries"]) == len(result["hot_sectors"]) == 1


@pytest.mark.parametrize("failure", ["missing", "network", "past_date", "after_open"])
def test_zero_turnover_does_not_hide_feed_failures(preopen_market, monkeypatch, failure):
    feed, previous = preopen_market
    if failure == "network":
        def fetch(url):
            raise m.requests.ConnectionError("synthetic outage")

        feed.fetch = fetch
    elif failure == "after_open":
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromisoformat("2026-10-15T09:30:00+08:00").astimezone(tz)

        monkeypatch.setattr(m, "datetime", Clock)
    else:
        symbols = ["sh600000"] if failure == "missing" else ["sh600000", "sz000001"]
        stamp = "20261014091959" if failure == "past_date" else "20261015091959"
        feed.fetch = lambda url: (
            "".join(raw_quote(s, stamp=stamp, amount="0") for s in symbols),
            {},
        )
    result = feed.snapshot()
    assert result["refresh_error"]
    assert result["freshness"] == "DELAYED"
    assert result["quoted_to"] == previous["quoted_to"]


def test_preopen_without_history_does_not_invent_rankings(preopen_market):
    feed, _ = preopen_market
    (feed.root / "snapshot.json").unlink()
    with pytest.raises(ValueError, match="unavailable"):
        feed.snapshot()


def test_industry_master_membership_and_private_cache(monkeypatch, tmp_path):

    calls = []
    tables = {
        "index_classify": [
            dict(index_code="801010.SI", industry_name="合成行业", level="L1", src="SW2021")
        ],
        "stock_basic": [
            dict(ts_code="600000.SH", list_status="L"),
            dict(ts_code="000001.SZ", list_status="L"),
            dict(ts_code="920001.BJ", list_status="L"),
        ],
        "index_member_all": [
            dict(ts_code="600000.SH", l1_code="801010.SI", is_new="Y", out_date=None),
            dict(ts_code="000001.SZ", l1_code="801010.SI", is_new="Y", out_date=None),
        ],
    }
    monkeypatch.setattr(m.tushare, "load_token", lambda: "synthetic-token")

    class Response:
        def __init__(self, api):
            rows = tables[api]
            fields = list(rows[0])
            self.content = json.dumps(
                dict(code=0, data=dict(fields=fields, items=[[r[f] for f in fields] for r in rows]))
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

    def post(url, **kwargs):
        body = kwargs["json"]
        assert body["token"] == "synthetic-token" and url == m.tushare.URL
        calls.append(body["api_name"])
        return Response(body["api_name"])

    monkeypatch.setattr(m.requests, "post", post)
    groups, sources, unclassified = m.industry_members(tmp_path)
    assert groups[0]["symbols"] == ["sh600000", "sz000001"] and unclassified == 0
    assert len(calls) == 3 and "synthetic-token" not in str(sources)
    m.industry_members(tmp_path)
    assert len(calls) == 3
    assert all(
        "synthetic-token" not in p.read_text(encoding="utf8") for p in tmp_path.glob("*.json")
    )


def test_read_only_dashboard_api(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from marketpulse.quant.live.research_api import router

    app = FastAPI()
    app.include_router(router)
    app.state.research_store = SimpleNamespace(legacy_root=tmp_path / "old")
    feed = m.MarketDashboard(tmp_path)
    feed.collect = example
    app.state.market_dashboard = feed
    with TestClient(app) as client:
        response = client.get("/api/quant/research/market-dashboard")
        assert response.status_code == 200 and response.json()["industries"][0]["name"] == "合成"
        feed.last, feed.attempted = None, 0
        (tmp_path / "snapshot.json").unlink()
        feed.collect = lambda: (_ for _ in ()).throw(ValueError("private upstream error"))
        response = client.get("/api/quant/research/market-dashboard")
        assert response.status_code == 503 and "private" not in response.text
