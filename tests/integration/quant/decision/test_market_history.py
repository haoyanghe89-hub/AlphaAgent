"""Isolated dated-member arithmetic; synthetic rows never enter real research."""

import json

import pytest
from test_decision_api import setup as setup
from test_tushare_supply import fixture, packet

from marketpulse.quant.decision import market_history, market_scan, tushare


def replace(repo, snapshot, role, rows):
    previous = next(e for e in snapshot["entries"] if e["role"] == role)
    raw = repo.raw(json.dumps(packet(rows)).encode())
    previous["source"] = dict(previous["source"], blob_ref=raw, sha256=raw.rsplit("/", 1)[-1])


def dated_fixture(repo, missing=False):
    snapshot = fixture(repo, missing=missing)
    for entry in list(snapshot["entries"]):
        if entry["role"].startswith("members:"):
            rows = tushare.records(json.loads(repo.raw_bytes(entry["source"]["blob_ref"])))
            replace(repo, snapshot, entry["role"], [dict(r, in_date="20180101") for r in rows])
            raw = repo.raw(json.dumps(packet([])).encode())
            source = dict(entry["source"], blob_ref=raw, sha256=raw.rsplit("/", 1)[-1])
            snapshot["entries"].append(dict(role="past_" + entry["role"], source=source))
    snapshot["version"] = market_history.VERSION
    return snapshot


def test_dated_scan_has_unchanged_scope_and_reproducible_shares(setup):
    repo = setup[0].state.decision_store
    snapshot = dated_fixture(repo)
    result = market_scan.rebuild(repo, snapshot)
    assert result["metrics"][0]["mean_20_share"] == "0.75"
    assert result["coverage"]["full_market_share_status"] == "UNAVAILABLE"
    assert result["coverage"]["membership_protocol"] == market_history.MEMBERSHIP_PROTOCOL
    assert result["heat_inputs"]["market_turnover"][0]["value"] == "4000"
    result["market_snapshot_ref"] = repo.blob(snapshot)
    market_scan.verify(repo, result)
    result["dated_membership"][0]["days"][0]["members"] = []
    with pytest.raises(ValueError):
        market_scan.verify(repo, result)


def test_missing_quote_cannot_shrink_dated_denominator(setup):
    repo = setup[0].state.decision_store
    result = market_scan.rebuild(repo, dated_fixture(repo, missing=True))
    assert not result["coverage"]["complete_union"]
    assert result["selection"]["ranking"] == []


def test_ipo_eligibility_is_not_a_zero_and_ambiguous_exit_is_blocked():
    master = {"688981.SH": dict(name="合成制造", list_date="20261013")}
    rows = [dict(ts_code="688981.SH", name="合成制造", in_date="20261012", out_date=None)]
    assert market_history.dated_members(rows, master, "20261012", "20260910", "20261014") == []
    assert market_history.dated_members(rows, master, "20261013", "20260910", "20261014") == [
        "688981.SH"
    ]
    rows[0]["out_date"] = "20261014"
    with pytest.raises(ValueError, match="exit boundary"):
        market_history.dated_members(rows, master, "20261013", "20260910", "20261014")
