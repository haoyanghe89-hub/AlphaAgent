"""Exact, raw daily and margin observations; no return or direction inference."""

import json
from datetime import date
from decimal import Decimal, InvalidOperation

from ..live.sources import EM, KLINE, em_params
from .contracts import EvidenceItem, Locator
from .tools import SourceUnavailable


def number(value, *, positive=False, nonnegative=False):
    result = Decimal(str(value))
    if not result.is_finite() or positive and result <= 0 or nonnegative and result < 0:
        raise ValueError("invalid source number")
    return result


def collect(client, subject, start, end):
    from . import tushare

    if not tushare.load_token():
        return collect_public(client, subject, start, end)
    items, gaps = collect_tushare(client, subject, start, end)
    present = {e.kind for e in items}
    if present != {"fact", "flow"}:
        fallback, fallback_gaps = collect_public(client, subject, start, end)
        items.extend(e for e in fallback if e.kind not in present)
        gaps.extend(fallback_gaps)
    return items, gaps


def collect_tushare(client, subject, start, end):
    from . import tushare

    items, gaps = [], []
    for api, kind, fields in (
        ("daily", "fact", (("close", "未复权收盘价", "CNY/share"),)),
        (
            "margin_detail",
            "flow",
            (
                ("rzye", "融资余额", "CNY"),
                ("rzmre", "融资买入额", "CNY"),
                ("rzche", "融资偿还额", "CNY"),
            ),
        ),
    ):
        try:
            channel_items = []
            source, rows = tushare.fetch(
                client,
                "single_" + api,
                api,
                dict(
                    ts_code=subject["security_id"],
                    start_date=str(start).replace("-", ""),
                    end_date=str(end).replace("-", ""),
                ),
            )
            valid = []
            for index, row in enumerate(rows):
                if row["ts_code"] != subject["security_id"]:
                    raise ValueError("Tushare security mismatch")
                stamp = date.fromisoformat(
                    row["trade_date"][:4]
                    + "-"
                    + row["trade_date"][4:6]
                    + "-"
                    + row["trade_date"][6:8]
                )
                if start <= stamp <= end:
                    valid.append((stamp, index, row))
            if not valid or len(rows) >= 6000 or len({v[0] for v in valid}) != len(valid):
                raise ValueError("empty, truncated or duplicate Tushare observations")
            valid.sort()
            packet_fields = json.loads(client.repo.raw_bytes(source["blob_ref"]))["data"]["fields"]
            for stamp, index, row in valid[-5:] if kind == "fact" else valid[-1:]:
                for field, label, unit in fields:
                    if row.get(field) is None:
                        continue
                    value = number(row[field], positive=kind == "fact", nonnegative=True)
                    channel_items.append(
                        EvidenceItem(
                            evidence_id="EV-" + field + "-" + str(stamp),
                            subject_id=subject["security_id"],
                            kind=kind,
                            source_family="TUSHARE",
                            original_source_id=source["key"] + ":" + str(stamp),
                            url=source["url"],
                            raw_ref=source["blob_ref"],
                            content_hash=source["sha256"],
                            locator=Locator(
                                kind="json",
                                path=f"/data/items/{index}/{packet_fields.index(field)}",
                            ),
                            text=f"{stamp} {label}：{value} {unit}",
                            value=value,
                            unit=unit,
                            observed_at=str(stamp) + "T15:00:00+08:00",
                            period_start=stamp,
                            period_end=stamp,
                            fetched_at=source["fetched_at"],
                            available_at=source["fetched_at"],
                            time_precision="day",
                            method_version="raw-tushare-daily-margin-v2",
                            quality_status="AVAILABLE",
                            limitations=("未复权价格观察，不是全收益。",)
                            if kind == "fact"
                            else ("融资渠道观察，不等于全市场资金或机构意图。",),
                        )
                    )
            items.extend(channel_items)
        except (SourceUnavailable, ValueError, KeyError, TypeError, InvalidOperation) as error:
            gaps.append(
                dict(
                    category="daily" if kind == "fact" else "margin",
                    need="Tushare" + api,
                    reason=str(error)[:150],
                )
            )
    return items, gaps


def collect_public(client, subject, start, end):
    items, gaps = [], []
    code, symbol = subject["code"], subject["symbol"]

    def evidence(source, key, value, unit, stamp, path, label, kind):
        return EvidenceItem(
            evidence_id="EV-" + key + "-" + stamp,
            subject_id=subject["security_id"],
            kind=kind,
            source_family=source["source_family"],
            original_source_id=source["key"] + ":" + stamp,
            url=source["url"],
            raw_ref=source["blob_ref"],
            content_hash=source["sha256"],
            locator=Locator(kind="json", path=path),
            text=f"{stamp} {label}：{value} {unit}",
            value=value,
            unit=unit,
            observed_at=stamp + "T15:00:00+08:00",
            period_start=stamp,
            period_end=stamp,
            fetched_at=source["fetched_at"],
            available_at=source["fetched_at"],
            time_precision="day",
            method_version="raw-daily-margin-v1",
            quality_status="AVAILABLE",
            limitations=("不复权收盘观察，不是除权息参考价或全收益。",)
            if kind == "fact"
            else ("融资渠道观察，不等于全市场资金净流入或机构意图。",),
        )

    try:
        source, packet = client.fetch(
            "daily", KLINE, params={"param": f"{symbol},day,{start},{end},640,"}, family="TENCENT"
        )
        raw_rows = packet["data"][symbol]["day"]
        rows = []
        for i, row in enumerate(raw_rows):
            stamp = date.fromisoformat(row[0])
            if not start <= stamp <= end:
                continue
            prices = [number(row[j], positive=True) for j in (1, 2, 3, 4)]
            if not prices[3] <= min(prices[:2]) <= max(prices[:2]) <= prices[2]:
                raise ValueError("OHLC bounds mismatch")
            rows.append((stamp, i, prices[1]))
        if not rows or len({r[0] for r in rows}) != len(rows):
            raise ValueError("empty/duplicate daily observations")
        rows.sort()
        for stamp, i, value in rows[-5:]:
            items.append(
                evidence(
                    source,
                    "close",
                    value,
                    "CNY/share",
                    str(stamp),
                    f"/data/{symbol}/day/{i}/2",
                    "未复权收盘价",
                    "fact",
                )
            )
    except (SourceUnavailable, ValueError, KeyError, IndexError, InvalidOperation) as error:
        gaps.append(dict(category="daily", need="证券身份一致的日行情", reason=str(error)[:200]))
    try:
        source, packet = client.fetch(
            "margin",
            EM,
            params=em_params("RPTA_WEB_RZRQ_GGMX", "DATE", 60, "SCODE", code),
            family="EASTMONEY_MARGIN",
        )
        rows = packet["result"]["data"]
        valid = []
        for i, row in enumerate(rows):
            if str(row.get("SCODE")) != code:
                raise ValueError("margin security mismatch")
            stamp = date.fromisoformat(row["DATE"][:10])
            if start <= stamp <= end:
                valid.append((stamp, i, row))
        if not valid:
            raise ValueError("no margin observations in requested interval")
        stamp, i, row = max(valid)
        for field, label in (
            ("RZYE", "融资余额"),
            ("RZMRE", "融资买入额"),
            ("RZCHE", "融资偿还额"),
        ):
            if row.get(field) is None:
                gaps.append(
                    dict(category="margin", need=label, reason="来源未返回该字段，不补零。")
                )
                continue
            items.append(
                evidence(
                    source,
                    field,
                    number(row[field], nonnegative=True),
                    "CNY",
                    str(stamp),
                    f"/result/data/{i}/{field}",
                    label,
                    "flow",
                )
            )
    except (SourceUnavailable, ValueError, KeyError, TypeError, InvalidOperation) as error:
        gaps.append(dict(category="margin", need="真实融资渠道数据", reason=str(error)[:200]))
    return items, gaps
