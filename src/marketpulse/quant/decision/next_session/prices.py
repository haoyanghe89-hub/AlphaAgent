"""Two provider quote channels preserve raw exchange-reference fields."""

import re
from datetime import date, datetime
from decimal import Decimal

from .labels import SHANGHAI

TENCENT = "https://qt.gtimg.cn/"
SINA = "https://hq.sinajs.cn/"


def decimal(value, *, positive=False):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0 or positive and result <= 0:
        raise ValueError("行情数值缺失或无效")
    return result


def quote(text, symbol, family):
    if family == "TENCENT":
        match = re.search(r"(?:^|\n)v_" + re.escape(symbol) + r'="([^"]*)"', text)
        if not match:
            raise ValueError("腾讯行情主体缺失")
        p = match[1].split("~")
        if len(p) < 47 or p[2] != symbol[2:]:
            raise ValueError("腾讯行情字段或证券代码冲突")
        stamp = datetime.strptime(p[30], "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)
        amount = p[35].split("/")
        if len(amount) != 3:
            raise ValueError("腾讯成交额原始字段缺失")
        close, reference, turnover = p[3], p[4], amount[2]
        name = p[1]
        status = "TRADED" if decimal(turnover) > 0 else "NO_TRADES"
        indexes = dict(close=3, reference=4, turnover=35)
    else:
        match = re.search(r"(?:^|\n)var hq_str_" + re.escape(symbol) + r'="([^"]*)"', text)
        if not match:
            raise ValueError("新浪行情主体缺失")
        p = match[1].split(",")
        if len(p) < 33:
            raise ValueError("新浪行情字段缺失")
        stamp = datetime.fromisoformat(p[30] + "T" + p[31]).replace(tzinfo=SHANGHAI)
        close, reference, turnover = p[3], p[2], p[9]
        name = p[0]
        status = "TRADED" if decimal(p[8]) > 0 and decimal(turnover) > 0 else "NO_TRADES"
        indexes = dict(close=3, reference=2, turnover=9)
    return dict(
        symbol=symbol,
        name=name,
        session=stamp.date().isoformat(),
        quoted_at=stamp.isoformat(),
        close=str(decimal(close, positive=True)),
        reference_price=str(decimal(reference, positive=True)),
        turnover=str(decimal(turnover)),
        trading_status=status,
        indexes=indexes,
    )


def fetch_quotes(client, symbols):
    joined = ",".join(symbols)
    result = []
    for key, url, params, family in [
        ("quote_tencent", TENCENT, {"q": joined}, "TENCENT"),
        ("quote_sina", SINA + "?list=" + joined, None, "SINA"),
    ]:
        source, text = client.fetch(key, url, params=params, family=family, encoding="gb18030")
        result.append((source, {s: quote(text, s, family) for s in symbols}))
    return result


def agree(quotes, symbol, session, now):
    values = [q[symbol] for _, q in quotes]
    for value in values:
        stamp = datetime.fromisoformat(value["quoted_at"])
        if (
            value["session"] != str(session)
            or stamp > now
            or stamp.time().replace(tzinfo=None) < datetime.min.replace(hour=15).time()
        ):
            raise ValueError("没有目标交易日收盘后的两路有效行情")
    for field in ("close", "reference_price", "trading_status"):
        if len({v[field] if field == "trading_status" else Decimal(v[field]) for v in values}) != 1:
            raise ValueError("两路收盘价、参考价或成交状态冲突")
    return values[0]


def bars(packet, symbol, sessions, base):
    rows = packet["data"][symbol]["day"]
    values = {}
    for index, row in enumerate(rows):
        d = date.fromisoformat(row[0])
        if d > base:
            continue
        if d in values:
            raise ValueError("日行情包含重复日期")
        prices = [decimal(row[j], positive=True) for j in (1, 2, 3, 4)]
        if not prices[3] <= min(prices[:2]) <= max(prices[:2]) <= prices[2]:
            raise ValueError("日行情 OHLC 范围冲突")
        values[d] = dict(
            session=str(d), close=str(prices[1]), turnover=str(decimal(row[8]) * 10000), index=index
        )
    required = tuple(s for s in sessions if s <= base)[-21:]
    if len(required) < 21 or any(s not in values for s in required):
        raise ValueError("必要的二十一个交易日日行情不完整，不补零或跨缺口计算")
    return [values[s] for s in required]
