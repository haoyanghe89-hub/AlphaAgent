"""Calendar-bounded issue times and exact exchange-reference labels."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def next_session(sessions, base):
    if base not in sessions or sessions.index(base) + 1 >= len(sessions):
        raise ValueError("下一交易日超出已核对的交易所日历")
    return sessions[sessions.index(base) + 1]


def cohort_for(now, base, target):
    local = now.astimezone(SHANGHAI)
    if local.date() >= target:
        raise ValueError("目标交易日已经开始，不能补签原预测")
    return (
        "PRIMARY"
        if local.date() == base and time(20) <= local.time() < time(20, 15)
        else "EXPLORATORY"
    )


def close_at(session):
    return datetime.combine(session, time(15), SHANGHAI)


def classify_outcome(prediction, close, reference):
    if (
        prediction not in ("UP", "DOWN")
        or close <= 0
        or reference <= 0
        or not close.is_finite()
        or not reference.is_finite()
    ):
        raise ValueError("核对需要有效方向、收盘价与行情参考价")
    direction = "UP" if close > reference else "DOWN" if close < reference else "FLAT"
    return direction, prediction == direction
