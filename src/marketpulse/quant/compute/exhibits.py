"""Worker-only exhibit cells: no front-end financial computation."""

import json
from decimal import Decimal, localcontext

from .metrics import metric, prices


def price_cells(series, spec):
    result, high, incomplete = [], None, False
    with localcontext() as context:
        context.prec = 40
        for point, price in zip(series.points, prices(series, spec), strict=True):
            incomplete = incomplete or price is None
            high = max(high, price) if high is not None and price is not None else price
            drop = (
                price / high - 1
                if high is not None and price is not None and not incomplete
                else None
            )
            for kind, value, unit, definition in (
                ("price", price, "CNY/share", "frozen_close;suspension=" + spec.suspension_policy),
                ("drawdown", drop, "fraction", "P/running_max(P)-1;missing=propagate"),
            ):
                result.append(
                    metric(
                        series,
                        f"chart_{kind}_{series.adjustment}",
                        value,
                        unit,
                        definition,
                        reason="missing_price" if value is None else None,
                        count=1,
                        row_keys=(point.session_date.isoformat(),),
                    ).model_copy(update={"start": point.session_date, "end": point.session_date})
                )
    return result


def kline_cells(series, snapshot):
    """Actual frozen OHLC only; volume remains in shares in the artifact."""
    rows = {r["date"]: r for r in json.loads(snapshot.result.records_json)}
    units = dict(snapshot.result.units)
    if not any(
        all(r.get(k) is not None for k in ("open", "high", "low", "close")) for r in rows.values()
    ):
        return []
    if (
        any(units.get(k) != "CNY/share" for k in ("open", "high", "low", "close"))
        or units.get("volume") != "share"
    ):
        raise ValueError("OHLC/volume unit mismatch")
    result, closes = [], []
    with localcontext() as context:
        context.prec = 40
        for point in series.points:
            row = rows.get(point.session_date.isoformat(), {})
            ohlc = [
                Decimal(str(row[k])) if row.get(k) is not None else None
                for k in ("open", "close", "low", "high")
            ]
            valid = (
                all(v is not None and v > 0 for v in ohlc)
                and not point.suspended
                and not point.provisional
            )
            if valid and not (ohlc[2] <= min(ohlc[:2]) <= max(ohlc[:2]) <= ohlc[3]):
                raise ValueError("invalid OHLC ordering")
            closes.append(ohlc[1] if valid else None)
            entries = [
                (k, v if valid else None, "CNY/share", "frozen_OHLC;no_synthesis")
                for k, v in zip(("open", "close", "low", "high"), ohlc, strict=True)
            ]
            volume = (
                Decimal(str(row["volume"])) if valid and row.get("volume") is not None else None
            )
            if volume is not None and volume < 0:
                raise ValueError("negative volume")
            entries.append(("volume", volume, "share", "frozen_volume;shares_not_lots"))
            for window in (20, 60):
                sample = closes[-window:]
                ma = (
                    sum(sample) / window
                    if len(sample) == window and all(v is not None for v in sample)
                    else None
                )
                entries.append(
                    (
                        f"ma{window}",
                        ma,
                        "CNY/share",
                        f"SMA({window});complete_session_window;missing=propagate",
                    )
                )
            for kind, value, unit, definition in entries:
                result.append(
                    metric(
                        series,
                        f"chart_kline_{kind}_{series.adjustment}",
                        value,
                        unit,
                        definition,
                        reason="warmup_or_missing_OHLC" if value is None else None,
                        count=1,
                        row_keys=(point.session_date.isoformat(), snapshot.snapshot_id),
                    ).model_copy(update={"start": point.session_date, "end": point.session_date})
                )
    return result
