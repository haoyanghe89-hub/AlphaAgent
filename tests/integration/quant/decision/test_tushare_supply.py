"""Synthetic transport/arithmetic fixtures; never published as real market data."""

import copy
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest
import requests
from test_decision_api import setup as setup
from test_flow_integration import ANNUAL

from marketpulse.quant.decision import market, market_scan, tushare, tushare_market
from marketpulse.quant.decision.next_session.calendar import parse_annual
from marketpulse.quant.decision.tools import SourceClient, Stopped

STAMP = "2026-10-13T10:00:00+00:00"


def packet(rows):
    fields = list(rows[0]) if rows else ["ts_code"]
    return dict(code=0, data=dict(fields=fields, items=[list(r.values()) for r in rows]))


def client(repo, stop=None):
    state = dict(request=dict(budget_profile=dict(max_seconds=60)), calls=[])
    return SourceClient(repo, state, lambda: None, stop or threading.Event()), state


def test_credential_never_enters_archive_and_cache_survives_new_task(setup, monkeypatch):
    app, _ = setup
    repo = app.state.decision_store
    secret = "synthetic-private-credential-do-not-archive"
    monkeypatch.setattr(tushare, "load_token", lambda: secret)
    payload = packet([dict(ts_code="688981.SH", trade_date="20261013", close=10)])
    raw = json.dumps(payload).encode()
    calls = []

    class Response:
        is_redirect = False
        url = tushare.URL

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_content(self, size):
            yield raw

    def transport(method, url, **kwargs):
        calls.append((method, url))
        assert url == tushare.URL and kwargs["json"]["token"] == secret
        assert not kwargs["allow_redirects"]
        return Response()

    monkeypatch.setattr(requests, "request", transport)
    first, state = client(repo)
    source, result = tushare.fetch(first, "ignored-role", "daily", dict(trade_date="20261013"))
    assert result[0]["close"] == 10
    assert source["api_name"] == "daily"
    for ref in (state["calls"][0]["request_ref"], source["blob_ref"]):
        assert secret.encode() not in repo.raw_bytes(ref)
    assert secret not in json.dumps(state)
    second, second_state = client(repo)
    same_source, _ = tushare.fetch(second, "another-role", "daily", dict(trade_date="20261013"))
    assert same_source == source and len(calls) == 1
    assert second_state["calls"][0]["cache_hit"] is True
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(Stopped):
        tushare.fetch(client(repo, stopped)[0], "cancelled", "daily", dict(trade_date="20261013"))


def test_rejected_or_credential_echoed_bodies_are_not_archived(setup, monkeypatch):
    app, _ = setup
    repo = app.state.decision_store
    monkeypatch.setattr(tushare, "load_token", lambda: "private-test-token")
    for raw in (
        b'{"code":-2001,"msg":"private-test-token rejected"}',
        b'{"code":0,"data":{"fields":["token"],"items":[["private-test-token"]]}}',
    ):
        with pytest.raises(ValueError):
            tushare.validate_response(raw, "private-test-token")
    with pytest.raises(ValueError):
        tushare.records(dict(code=0, data=dict(fields=["x", "x"], items=[[1, 2]])))
    with pytest.raises(ValueError):
        tushare.records(dict(code=0, data=dict(fields=["x"], items=[[1, 2]])))
    with pytest.raises(ValueError):
        tushare.request_body(dict(family="TUSHARE", url="http://api.tushare.pro", data={}))
    with pytest.raises(ValueError):
        tushare.fetch(client(repo)[0], "bad", "unknown-write-api", {})


def test_cache_expiry_cannot_relabel_old_source_as_fresh(setup):
    app, _ = setup
    repo = app.state.decision_store
    definition = dict(tool="test", url=tushare.URL, params=None, data={}, family="TUSHARE")
    raw_ref = repo.raw(json.dumps(packet([dict(value=1)])).encode())
    source = dict(
        blob_ref=raw_ref,
        sha256=raw_ref.rsplit("/", 1)[-1],
        fetched_at=(datetime.now(UTC) - timedelta(hours=7)).isoformat(),
    )
    tushare.remember(repo, definition, source)
    assert tushare.cached(repo, definition) is None


def fixture(repo, missing=False):
    entries, sources = [], []

    def source(key, raw):
        ref = repo.raw(raw)
        value = dict(
            key=key,
            url=tushare.URL + "/",
            source_family="TUSHARE",
            blob_ref=ref,
            sha256=ref.rsplit("/", 1)[-1],
            fetched_at=STAMP,
        )
        sources.append(value)
        return value

    def entry(role, rows):
        entries.append(dict(role=role, source=source(role, json.dumps(packet(rows)).encode())))

    annual, rule = source("annual", ANNUAL.encode()), source("rule", b"synthetic rules")
    sessions = [str(d) for d in parse_annual(ANNUAL, 2026)]
    calendar = dict(sessions=sessions, source_refs=[annual["blob_ref"], rule["blob_ref"]])
    days = tushare_market.ready_sessions(calendar, datetime.fromisoformat(STAMP))
    boards = [
        dict(index_code="SYNTHETIC1.SI", industry_name="半导体设备", level="L3", src="SW2021"),
        dict(index_code="SYNTHETIC2.SI", industry_name="集成电路制造", level="L3", src="SW2021"),
    ]
    entry("catalog", boards)
    entry(
        "master",
        [
            dict(ts_code=sid, name=name, list_status="L", list_date="20180101")
            for sid, name in (
                ("688012.SH", "合成设备"),
                ("688981.SH", "合成制造"),
                ("920001.BJ", "合成北交"),
            )
        ],
    )
    for board, sid, name in zip(
        boards, ("688012.SH", "688981.SH"), ("合成设备", "合成制造"), strict=True
    ):
        members = [
            dict(
                l3_code=board["index_code"],
                l3_name=board["industry_name"],
                ts_code=sid,
                name=name,
                is_new="Y",
                out_date=None,
            )
        ]
        if sid == "688012.SH":
            members.append(dict(members[0], ts_code="920001.BJ", name="合成北交"))
        entry("members:" + board["index_code"], members)
    entry("calendar", [dict(cal_date=d.replace("-", ""), is_open=1) for d in days])
    for i, day in enumerate(days):
        values = [
            dict(
                ts_code=sid,
                trade_date=day.replace("-", ""),
                open=10,
                close=10,
                pre_close=10,
                high=11,
                low=9,
                amount=amount,
            )
            for sid, amount in (("688012.SH", 3), ("688981.SH", 1))
        ]
        if missing and i == 3:
            values.pop()
        entry("daily:" + day, values)
    return dict(
        version=tushare_market.VERSION,
        entries=entries,
        sources=sources,
        calendar=calendar,
        market_at=STAMP,
        cutoff_at=STAMP,
        sessions=days,
        gaps=[],
    )


def test_sw_scope_fixed_denominator_units_exclusions_and_recomputation(setup):
    app, _ = setup
    repo = app.state.decision_store
    snapshot = fixture(repo)
    data = market_scan.rebuild(repo, snapshot)
    assert data["coverage"]["members_total"] == 2
    assert len(data["coverage"]["excluded_market_members"]) == 1
    assert data["metrics"][0]["mean_20_share"] == "0.75"
    assert data["heat_inputs"]["market_turnover"][0]["value"] == "4000"
    data["market_snapshot_ref"] = repo.blob(snapshot)
    market_scan.verify(repo, data)
    data["metrics"][0]["mean_20_share"] = "0.9"
    with pytest.raises(ValueError):
        market_scan.verify(repo, data)
    partial = market_scan.rebuild(repo, fixture(repo, missing=True))
    assert partial["selection"]["ranking"] == []
    assert partial["coverage"]["members_total"] == 2
    assert all(r["value"] is None for r in partial["heat_inputs"]["market_turnover"])


def test_sw_calendar_and_classification_cannot_be_mixed(setup):
    app, _ = setup
    repo = app.state.decision_store
    snapshot = fixture(repo)
    tampered = copy.deepcopy(snapshot)
    tampered["sessions"].pop()
    with pytest.raises(ValueError):
        market_scan.rebuild(repo, tampered)


def test_single_tushare_values_bind_actual_field_order_and_issuer(setup, monkeypatch):
    from datetime import date

    repo = setup[0].state.decision_store
    monkeypatch.setattr(tushare, "load_token", lambda: "synthetic-not-used")
    wrong = False

    def fetch(source_client, key, api, params):
        row = dict(ts_code="002371.SZ" if wrong else "688012.SH", trade_date="20261013")
        row.update(dict(close=10) if api == "daily" else dict(rzye=100, rzmre=30, rzche=20))
        fields = list(reversed(row))
        value = dict(code=0, data=dict(fields=fields, items=[[row[f] for f in fields]]))
        raw = repo.blob(value)
        source = dict(
            key=api, url=tushare.URL, blob_ref=raw, sha256=raw.rsplit("/", 1)[-1], fetched_at=STAMP
        )
        return source, tushare.records(value)

    monkeypatch.setattr(tushare, "fetch", fetch)
    monkeypatch.setattr(market, "collect_public", lambda *args: ([], []))
    values, gaps = market.collect(
        client(repo)[0], dict(security_id="688012.SH"), date(2026, 10, 1), date(2026, 10, 13)
    )
    assert len(values) == 4 and not gaps
    for value in values:
        raw = json.loads(repo.raw_bytes(value.raw_ref))
        index = int(value.locator.path.rsplit("/", 1)[-1])
        assert str(raw["data"]["items"][0][index]) == str(value.value)
    wrong = True
    values, gaps = market.collect(
        client(repo)[0], dict(security_id="688012.SH"), date(2026, 10, 1), date(2026, 10, 13)
    )
    assert not values and len(gaps) == 2
