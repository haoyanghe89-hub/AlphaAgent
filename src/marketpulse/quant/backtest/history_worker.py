"""Fixed public BaoStock historical methods, no calculation or arbitrary calls."""

import json
import re
import sys
from datetime import UTC, date, datetime


def execute(query):
    if set(query) - {"kind", "start", "end", "day", "code", "year", "quarter"}:
        raise ValueError("unsupported historical query fields")
    kind = query.get("kind")
    if kind not in {
        "calendar",
        "basic",
        "members",
        "benchmark",
        "stock_daily",
        "dividends",
        "profit",
    }:
        raise ValueError("unsupported historical source")
    expected = (
        {"kind", "start", "end", "code"}
        if kind == "stock_daily"
        else {"kind", "code", "year", "quarter"}
        if kind == "profit"
        else {"kind", "code", "year"}
        if kind == "dividends"
        else {"kind", "start", "end"}
        if kind in {"calendar", "benchmark"}
        else {"kind", "day"}
        if kind == "members"
        else {"kind"}
    )
    if set(query) != expected:
        raise ValueError("historical query shape mismatch")
    if kind in {"stock_daily", "dividends", "profit"}:
        if not re.fullmatch(r"(?:sh\.6\d{5}|sz\.[03]\d{5})", query["code"]):
            raise ValueError("bounded Shanghai/Shenzhen stock required")
    if kind in {"dividends", "profit"}:
        if type(query["year"]) is not int or not 1990 <= query["year"] <= datetime.now(UTC).year:
            raise ValueError("historical year required")
        if kind == "profit" and (
            type(query["quarter"]) is not int or query["quarter"] not in {1, 2, 3, 4}
        ):
            raise ValueError("explicit financial quarter required")
    if kind in {"calendar", "benchmark", "stock_daily"}:
        start, end = date.fromisoformat(query["start"]), date.fromisoformat(query["end"])
        if end < start or (end - start).days > 1096 or end >= datetime.now(UTC).date():
            raise ValueError("bounded historical segment required")
    elif kind == "members" and date.fromisoformat(query["day"]) >= datetime.now(UTC).date():
        raise ValueError("historical membership day required")
    import baostock as bs

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError("public source login failed")
    try:
        if kind == "basic":
            response = bs.query_stock_basic()
        elif kind == "calendar":
            response = bs.query_trade_dates(query["start"], query["end"])
        elif kind == "members":
            response = bs.query_all_stock(day=query["day"])
        elif kind == "dividends":
            response = bs.query_dividend_data(query["code"], query["year"], yearType="operate")
        elif kind == "profit":
            response = bs.query_profit_data(query["code"], query["year"], query["quarter"])
        elif kind == "stock_daily":
            response = bs.query_history_k_data_plus(
                query["code"],
                "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST",
                start_date=query["start"],
                end_date=query["end"],
                frequency="d",
                adjustflag="3",
            )
        else:
            response = bs.query_history_k_data_plus(
                "sh.000300",
                "date,code,open,high,low,close",
                start_date=query["start"],
                end_date=query["end"],
                frequency="d",
                adjustflag="3",
            )
        rows = []
        if response.error_code != "0":
            raise RuntimeError("historical query failed")
        while response.next():
            rows.append(dict(zip(response.fields, response.get_row_data(), strict=True)))
            if len(rows) > 20000:
                raise ValueError("historical row bound exceeded")
        if response.error_code != "0":
            raise RuntimeError("historical stream failed")
        if not rows and kind not in {"dividends", "profit"}:
            raise ValueError("historical source returned no rows")
        return dict(
            provider="baostock", query=query, rows=rows, retrieved_at=datetime.now(UTC).isoformat()
        )
    finally:
        bs.logout()


if __name__ == "__main__":
    try:
        result = execute(json.loads(sys.stdin.buffer.read(65536)))
    except Exception as error:
        result = {"error": type(error).__name__}
    print("HISTORY_JSON:" + json.dumps(result, ensure_ascii=False), flush=True)
