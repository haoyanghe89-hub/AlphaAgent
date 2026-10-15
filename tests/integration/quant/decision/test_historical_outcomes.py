"""Late observation must use target-day data, never today's replacement quote."""

import json
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_next_session_experiment import prediction
from test_next_session_experiment import repo as repo

from marketpulse.quant.decision import tushare
from marketpulse.quant.decision.next_session import historical_prices, outcomes
from marketpulse.quant.decision.next_session.contracts import NextSessionOutcome

NOW = datetime.fromisoformat("2026-10-14T20:00:00+08:00")
PREDICTION = SimpleNamespace(
    security_id="688012.SH",
    base_session=date(2026, 10, 12),
    target_session=date(2026, 10, 13),
    prediction="UP",
)
SNAPSHOT = SimpleNamespace(daily_prices=[SimpleNamespace(close=Decimal("100"))])


def inputs(monkeypatch, *, close="101", reference="100", other="101", sid="688012.SH"):
    source = dict(blob_ref="synthetic-ts-source", sha256="a" * 64)
    row = dict(
        ts_code=sid,
        trade_date="20261013",
        close=close,
        pre_close=reference,
        open="100",
        high="102",
        low="98",
        amount="50",
        vol="10",
    )
    monkeypatch.setattr(tushare, "load_token", lambda: "synthetic")
    monkeypatch.setattr(tushare, "fetch", lambda *args: (source, [row]))

    class Client:
        def fetch(self, key, url, **kwargs):
            assert "2026-10-13" in kwargs["params"]["param"]
            return dict(blob_ref="synthetic-qq-source", sha256="b" * 64), dict(
                data=dict(
                    sh688012=dict(day=[["2026-10-12", "100", "100"], ["2026-10-13", other, other]])
                )
            )

    return Client()


def test_late_target_day_crosscheck_and_before_close_refusal(monkeypatch):
    client = inputs(monkeypatch)
    result = historical_prices.observe(client, PREDICTION, SNAPSHOT, NOW)
    assert result["return_direction"] == "UP" and result["correct"]
    assert result["close"] == "101" and len(result["price_source_refs"]) == 2
    with pytest.raises(ValueError, match="尚未收盘"):
        historical_prices.observe(
            client, PREDICTION, SNAPSHOT, datetime.fromisoformat("2026-10-13T14:00:00+08:00")
        )


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"other": "99"}, "冲突"),
        ({"reference": "99"}, "独立除权息"),
        ({"sid": "002371.SZ"}, "证券"),
    ],
)
def test_late_conflict_actions_and_identity_cannot_be_scored(monkeypatch, kwargs, reason):
    with pytest.raises(ValueError, match=reason):
        historical_prices.observe(inputs(monkeypatch, **kwargs), PREDICTION, SNAPSHOT, NOW)


def test_quote_rollover_uses_history_but_real_conflict_is_not_overridden(monkeypatch):
    client = inputs(monkeypatch)
    monkeypatch.setattr(
        outcomes,
        "fetch_quotes",
        lambda *args: (_ for _ in ()).throw(ValueError("没有目标交易日收盘后的两路有效行情")),
    )
    assert outcomes.observe_one(None, PREDICTION, SNAPSHOT, client, NOW)["status"] == "SCORABLE"
    monkeypatch.setattr(
        outcomes, "fetch_quotes", lambda *args: (_ for _ in ()).throw(ValueError("两路收盘价冲突"))
    )
    assert outcomes.observe_one(None, PREDICTION, SNAPSHOT, client, NOW)["status"] == "CONFLICT"


@pytest.mark.parametrize("close,actual", [("100", "FLAT"), ("101", "UP")])
def test_saved_historical_result_replays_originals_without_quote_decoder(
    repo, monkeypatch, close, actual
):
    p = repo.publish(prediction())
    monkeypatch.setattr(repo, "snapshot_for_prediction", lambda _: SNAPSHOT)
    row = dict(
        ts_code=p.security_id,
        trade_date="20261013",
        open="100",
        close=close,
        pre_close="100",
        high="102",
        low="98",
        amount="50",
        vol="10",
    )
    first = repo.raw(
        json.dumps(dict(code=0, data=dict(fields=list(row), items=[list(row.values())]))).encode()
    )
    second = repo.raw(
        json.dumps(
            dict(
                data=dict(
                    sh688012=dict(day=[["2026-10-12", "100", "100"], ["2026-10-13", close, close]])
                )
            )
        ).encode()
    )
    value = NextSessionOutcome(
        outcome_id="DEC-OUT-synthetic-late",
        version=1,
        prediction_id=p.prediction_id,
        observed_at=NOW,
        status="SCORABLE",
        close=close,
        reference_price="100",
        return_direction=actual,
        correct=actual == "UP",
        price_source_refs=[first, second],
        source_hashes=[r.rsplit("/", 1)[-1] for r in (first, second)],
        limitations=["synthetic late replay"],
    )
    repo.add_outcome(value)
    assert repo.outcome_versions(p.prediction_id)[0]["return_direction"] == actual
