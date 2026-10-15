"""Target-date observation after quote feeds have moved to a later session."""

import json
from datetime import datetime
from decimal import Decimal

from ...live.sources import KLINE
from .. import tushare
from .labels import classify_outcome, close_at
from .prices import decimal


def prices(rows, packet, prediction, snapshot):
    target = str(prediction.target_session)
    security = prediction.security_id
    if (
        len(rows) != 1
        or rows[0]["ts_code"] != security
        or rows[0]["trade_date"] != target.replace("-", "")
    ):
        raise ValueError("目标日期或证券历史行情缺失/冲突")
    row = rows[0]
    close, reference = (
        decimal(row["close"], positive=True),
        decimal(row["pre_close"], positive=True),
    )
    prices = [decimal(row[f], positive=True) for f in ("open", "close", "high", "low")]
    if not prices[3] <= min(prices[:2]) <= max(prices[:2]) <= prices[2]:
        raise ValueError("目标日OHLC冲突")
    if close % Decimal("0.01") or reference % Decimal("0.01"):
        raise ValueError("目标日原始价格精度冲突")
    if decimal(row["amount"]) <= 0 or decimal(row["vol"]) <= 0:
        raise ValueError("目标日成交状态未知，不能当平盘")
    if reference != snapshot.daily_prices[-1].close:
        raise ValueError("历史参考价与封存基准不一致；需独立除权息参考价原始依据，暂停核对")
    symbol = ("sh" if security.endswith(".SH") else "sz") + security[:6]
    values = {}
    for value in packet["data"][symbol]["day"]:
        if value[0] in values:
            raise ValueError("独立历史行情重复日期")
        values[value[0]] = decimal(value[2], positive=True)
    if target not in values or str(prediction.base_session) not in values:
        raise ValueError("独立来源缺目标或基准收盘，未用最新行情代替")
    if values[target] != close or values[str(prediction.base_session)] != reference:
        raise ValueError("两路目标收盘或基准参考价冲突")
    return close, reference


def verify(repo, prediction, value):
    if datetime.fromisoformat(value["observed_at"]) < close_at(prediction.target_session):
        raise ValueError("历史核对不能早于目标日收盘")
    refs = value["price_source_refs"]
    if len(refs) != 2:
        raise ValueError("历史核对需要恰好两路原文")
    rows = tushare.records(json.loads(repo.raw_bytes(refs[0])))
    packet = json.loads(repo.raw_bytes(refs[1]))
    snapshot = repo.snapshot_for_prediction(prediction)
    close, reference = prices(rows, packet, prediction, snapshot)
    return dict(close=str(close), reference_price=str(reference))


def observe(client, prediction, snapshot, now):
    if now < close_at(prediction.target_session):
        raise ValueError("目标日尚未收盘，不能读取未来核对值")
    target, security = str(prediction.target_session), prediction.security_id
    source, rows = tushare.fetch(
        client,
        "outcome_target_daily",
        "daily",
        dict(ts_code=security, trade_date=target.replace("-", "")),
    )
    symbol = ("sh" if security.endswith(".SH") else "sz") + security[:6]
    other, packet = client.fetch(
        "outcome_target_tencent_" + target,
        KLINE,
        params=dict(param=f"{symbol},day,{prediction.base_session},{target},10,"),
        family="TENCENT",
    )
    close, reference = prices(rows, packet, prediction, snapshot)
    direction, correct = classify_outcome(prediction.prediction, close, reference)
    return dict(
        status="SCORABLE",
        close=str(close),
        reference_price=str(reference),
        return_direction=direction,
        correct=correct,
        price_source_refs=[s["blob_ref"] for s in (source, other)],
        source_hashes=[s["sha256"] for s in (source, other)],
        limitations=[
            "按Tushare目标日日线收盘/前收盘参考字段与腾讯目标及基准收盘核对；仅参考价与封存基准一致时核对。不是全收益或可执行胜率。"
        ],
    )
