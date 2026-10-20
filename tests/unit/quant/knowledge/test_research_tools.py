"""Synthetic sealed-source checks; these fixtures are never investment results."""

import copy
import hashlib
import json
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace

import pytest

from marketpulse.quant.contracts import digest
from marketpulse.quant.decision import tushare
from marketpulse.quant.decision.tools import SourceClient
from marketpulse.quant.knowledge.research_tools import capture_valuation, verify_valuation

SID, DAY, CLOSE = "000333.SZ", "20261016", "72.50"
STAMP = "2026-10-16T10:00:00+00:00"
SEAL = "2026-10-16T10:01:00+00:00"


class MemoryRepo:
    def __init__(self):
        self.data = {}

    def raw(self, raw):
        ref = "blob://sha256/" + hashlib.sha256(raw).hexdigest()
        self.data[ref] = raw
        return ref

    def blob(self, value):
        return self.raw(json.dumps(value, sort_keys=True).encode())

    def raw_bytes(self, ref):
        return self.data[ref]

    def value(self, ref):
        return json.loads(self.raw_bytes(ref))


def packet(row=None, fields=None):
    fields = fields or tushare.FIELDS["daily_basic"].split(",")
    row = row or dict(
        ts_code=SID,
        trade_date=DAY,
        close=CLOSE,
        pe_ttm="15.25",
        pb="2.50",
        total_mv="1234567.89",
        circ_mv="1000000.25",
        total_share="17028.5226",
    )
    return dict(code=0, data=dict(fields=fields, items=[[row.get(k) for k in fields]]))


class Client:
    def __init__(self, payload):
        self.repo, self.payload, self.calls = MemoryRepo(), payload, []

    def fetch(self, key, url, *, data, family):
        definition = dict(tool=key, url=url, params=None, data=data, family=family)
        self.calls.append(copy.deepcopy(definition))
        raw = json.dumps(self.payload).encode()
        ref = self.repo.raw(raw)
        source = dict(
            key=key,
            url=url + "/",
            source_family=family,
            blob_ref=ref,
            sha256=hashlib.sha256(raw).hexdigest(),
            fetched_at=STAMP,
            byte_count=len(raw),
            request_ref=self.repo.blob(definition),
            api_name=data["api_name"],
            parameters=data["params"],
        )
        return source, self.payload


def captured(payload=None):
    client = Client(payload or packet())
    return client, capture_valuation(client, SID, DAY, CLOSE)


def test_single_exact_request_reconstructs_units_and_reordered_fields():
    fields = list(reversed(tushare.FIELDS["daily_basic"].split(",")))
    client, result = captured(packet(fields=fields))
    params = dict(ts_code=SID, trade_date=DAY)
    assert client.calls == [
        dict(
            tool="tushare_daily_basic_" + digest(params)[:12],
            url=tushare.URL,
            params=None,
            data=dict(api_name="daily_basic", params=params, fields=tushare.FIELDS["daily_basic"]),
            family="TUSHARE",
        )
    ]
    assert result["tool"] == "VALUATION_SNAPSHOT"
    assert result["version"] == "research-tools-v1" and result["status"] == "AVAILABLE"
    facts = {f["metric_key"]: f for f in result["facts"]}
    assert facts["pe_ttm"]["value"] == "15.25" and facts["pb"]["unit"] == "倍"
    assert facts["total_mv"]["value"] == "1234567.89"
    assert facts["total_mv"]["unit"] == facts["circ_mv"]["unit"] == "万元"
    assert all(f["window"] == dict(start=DAY, end=DAY) for f in result["facts"])
    assert all(f["source_refs"] == [result["source"]["blob_ref"]] for f in result["facts"])
    assert verify_valuation(client.repo, result, SID, DAY, CLOSE, SEAL) == result


def test_null_and_absent_metrics_are_missing_not_zero_and_negative_pe_retains_boundary():
    payload = packet()
    row = dict(zip(payload["data"]["fields"], payload["data"]["items"][0], strict=True))
    row.update(pe_ttm="-8.5", pb=None)
    fields = [f for f in payload["data"]["fields"] if f != "circ_mv"]
    client, result = captured(packet(row, fields))
    facts = {f["metric_key"]: f for f in result["facts"]}
    assert facts["pe_ttm"]["value"] == "-8.5"
    assert facts["pb"]["value"] is None and facts["circ_mv"]["value"] is None
    assert any("负" in text and "市盈率" in text for text in result["limitations"])
    assert any("pb" in text for text in result["limitations"])
    assert verify_valuation(client.repo, result, SID, DAY, CLOSE, SEAL) == result


def test_no_rows_is_genuine_missing_source_not_a_fabricated_snapshot():
    payload = packet()
    payload["data"]["items"] = []
    client, result = captured(payload)
    assert result["status"] == "MISSING" and result["facts"] == []
    assert result["source"]["request_ref"] and result["limitations"]
    assert verify_valuation(client.repo, result, SID, DAY, CLOSE, SEAL) == result


@pytest.mark.parametrize(
    "change",
    [
        dict(ts_code="600036.SH"),
        dict(trade_date="20261015"),
        dict(close="72.51"),
        dict(close=None),
        dict(pe_ttm="NaN"),
        dict(pb="Infinity"),
        dict(total_mv="-Infinity"),
        dict(circ_mv=-1),
        dict(total_share="NaN"),
        dict(pe_ttm=True),
    ],
)
def test_invalid_identity_prices_or_nonfinite_values_are_rejected(change):
    payload = packet()
    row = dict(zip(payload["data"]["fields"], payload["data"]["items"][0], strict=True))
    row.update(change)
    with pytest.raises(ValueError):
        captured(packet(row))


@pytest.mark.parametrize("kind", ["duplicate", "extra_field", "duplicate_field", "row_width"])
def test_ambiguous_or_unrequested_original_tables_are_rejected(kind):
    payload = packet()
    if kind == "duplicate":
        payload["data"]["items"] *= 2
    elif kind == "extra_field":
        payload["data"]["fields"].append("invented_probability")
        payload["data"]["items"][0].append("0.99")
    elif kind == "duplicate_field":
        payload["data"]["fields"][-1] = "pb"
    else:
        payload["data"]["items"][0].pop()
    with pytest.raises(ValueError):
        captured(payload)


@pytest.mark.parametrize(
    "mutation",
    ["value", "unit", "window", "source_ref", "status", "limitations", "security", "version"],
)
def test_facts_must_equal_independent_original_reconstruction(mutation):
    client, result = captured()
    if mutation in {"value", "unit", "window"}:
        result["facts"][0][mutation] = "altered"
    elif mutation == "source_ref":
        result["facts"][0]["source_refs"] = []
    elif mutation == "status":
        result["status"] = "MISSING"
    elif mutation == "limitations":
        result["limitations"] = []
    elif mutation == "security":
        result["security_id"] = "600036.SH"
    else:
        result["version"] = "untrusted-version"
    with pytest.raises(ValueError):
        verify_valuation(client.repo, result, SID, DAY, CLOSE, SEAL)


@pytest.mark.parametrize(
    "mutation", ["extra_param", "fields", "url", "family", "query", "tool", "body_token"]
)
def test_request_manifest_cannot_inject_parameters_fields_or_transport(mutation):
    client, result = captured()
    source = result["source"]
    definition = client.repo.value(source["request_ref"])
    if mutation == "extra_param":
        definition["data"]["params"]["start_date"] = "20200101"
    elif mutation == "fields":
        definition["data"]["fields"] = "ts_code,pe"
    elif mutation == "url":
        definition["url"] = "http://api.tushare.pro"
    elif mutation == "family":
        definition["family"] = "UNTRUSTED"
    elif mutation == "query":
        definition["params"] = dict(trade_date=DAY)
    elif mutation == "tool":
        definition["tool"] = "untrusted"
    else:
        definition["data"]["token"] = "synthetic-credential"
    source["request_ref"] = client.repo.blob(definition)
    with pytest.raises(ValueError):
        verify_valuation(client.repo, result, SID, DAY, CLOSE, SEAL)


@pytest.mark.parametrize("mutation", ["future", "naive", "sha256", "bytes", "source_url"])
def test_seal_time_hash_and_transport_metadata_are_enforced(mutation):
    client, result = captured()
    source = result["source"]
    if mutation == "future":
        source["fetched_at"] = "2026-10-16T10:01:01+00:00"
    elif mutation == "naive":
        source["fetched_at"] = "2026-10-16T10:00:00"
    elif mutation == "sha256":
        source["sha256"] = "a" * 64
    elif mutation == "bytes":
        client.repo.data[source["blob_ref"]] = b"{}"
    else:
        source["url"] = "https://api.tushare.pro/?token=synthetic"
    with pytest.raises(ValueError):
        verify_valuation(client.repo, result, SID, DAY, CLOSE, SEAL)


def test_historical_cache_keeps_original_fetch_time_and_request_ref():
    client, result = captured()
    assert datetime.fromisoformat(result["source"]["fetched_at"]) < datetime.now(UTC)
    assert result["source"]["request_ref"] in client.repo.data
    rebuilt = verify_valuation(client.repo, result, SID, DAY, "72.500", SEAL)
    assert rebuilt["expected_close"] == "72.5"


def test_actual_source_client_cache_reuses_original_request_and_fetch_time(monkeypatch, tmp_path):
    repo = MemoryRepo()
    repo.base = SimpleNamespace(legacy_root=tmp_path / "legacy")
    raw = json.dumps(packet()).encode()
    calls = []

    class Response:
        url = tushare.URL + "/"
        is_redirect = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, *_):
            yield raw

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs["json"]))
        return Response()

    monkeypatch.setattr("requests.request", request)
    monkeypatch.setattr(tushare, "load_token", lambda: "synthetic-transport-token")

    def client():
        state = dict(request=dict(budget_profile=dict(max_seconds=60)), calls=[])
        return SourceClient(repo, state, lambda: None, Event(), max_requests=1)

    first_client = client()
    first = capture_valuation(first_client, SID, DAY, CLOSE)
    second_client = client()
    second = capture_valuation(second_client, SID, DAY, CLOSE)
    assert first == second and len(calls) == 1
    assert second_client.state["calls"][0]["cache_hit"] is True
    assert calls[0][:2] == ("POST", tushare.URL)
    assert calls[0][2]["params"] == dict(ts_code=SID, trade_date=DAY)
    definition = repo.value(second["source"]["request_ref"])
    assert "token" not in definition["data"]
    assert verify_valuation(repo, second, SID, DAY, CLOSE, datetime.now(UTC).isoformat()) == second


@pytest.mark.parametrize(
    "sid,day,close",
    [("000333;DROP", DAY, CLOSE), (SID, "20260230", CLOSE), (SID, DAY, "NaN"), (SID, DAY, "0")],
)
def test_invalid_tool_arguments_never_reach_transport(sid, day, close):
    client = Client(packet())
    with pytest.raises(ValueError):
        capture_valuation(client, sid, day, close)
    assert client.calls == []
