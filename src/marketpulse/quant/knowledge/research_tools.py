"""Bounded public valuation read, reconstructed from the sealed request and bytes."""

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from ..contracts import digest
from ..decision import tushare

VERSION = "research-tools-v1"
TOOL = "VALUATION_SNAPSHOT"
METRICS = (
    ("pe_ttm", "滚动市盈率", "倍"),
    ("pb", "市净率", "倍"),
    ("total_mv", "总市值", "万元"),
    ("circ_mv", "流通市值", "万元"),
)
LIMITATIONS = [
    "供应商单日估值快照，非实时价格；市值保留原始万元单位。",
    "未进行同行、历史分位或盈利质量比较，不能仅凭估值高低作出买卖判断。",
]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("估值数值类型无效")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("估值数值无效") from error
    if not number.is_finite():
        raise ValueError("估值数值必须有限")
    return number


def _text(number):
    if not number:
        return "0"
    value = format(number, "f")
    return value.rstrip("0").rstrip(".") if "." in value else value


def _arguments(security_id, market_date, expected_close):
    if not isinstance(security_id, str) or not re.fullmatch(r"\d{6}\.(SH|SZ)", security_id):
        raise ValueError("估值工具仅接受明确的沪深证券代码")
    if not isinstance(market_date, str) or not re.fullmatch(r"\d{8}", market_date):
        raise ValueError("估值基准日格式必须为YYYYMMDD")
    datetime.strptime(market_date, "%Y%m%d")
    close = _number(expected_close)
    if close <= 0:
        raise ValueError("估值校验收盘价必须为正")
    return _text(close)


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError("估值来源时间缺失")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("估值来源时间格式无效") from error
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("估值来源时间必须包含时区")
    return stamp


def _rebuild(repo, source, security_id, market_date, expected_close, sealed_at):
    close = _arguments(security_id, market_date, expected_close)
    params = dict(ts_code=security_id, trade_date=market_date)
    key = "tushare_daily_basic_" + digest(params)[:12]
    definition = dict(
        tool=key,
        url=tushare.URL,
        params=None,
        data=dict(api_name="daily_basic", params=params, fields=tushare.FIELDS["daily_basic"]),
        family="TUSHARE",
    )
    if (
        source["key"] != key
        or source["url"] not in {tushare.URL, tushare.URL + "/"}
        or source["source_family"] != "TUSHARE"
        or source["api_name"] != "daily_basic"
        or source["parameters"] != params
        or repo.value(source["request_ref"]) != definition
    ):
        raise ValueError("估值来源请求与受控证券、日期或字段不一致")
    if _stamp(source["fetched_at"]) > _stamp(sealed_at):
        raise ValueError("估值来源晚于封存截止")
    raw = repo.raw_bytes(source["blob_ref"])
    sha256 = hashlib.sha256(raw).hexdigest()
    if (
        source["sha256"] != sha256
        or source["blob_ref"] != "blob://sha256/" + sha256
        or source["byte_count"] != len(raw)
    ):
        raise ValueError("估值来源原文哈希或字节数不一致")
    packet = json.loads(raw)
    rows = tushare.records(packet)
    fields = set(packet["data"]["fields"])
    if (
        not {"ts_code", "trade_date", "close"} <= fields
        or not fields <= set(tushare.FIELDS["daily_basic"].split(","))
        or len(rows) > 1
    ):
        raise ValueError("估值原文关键字段、额外字段或重复记录不一致")
    result = dict(
        tool=TOOL,
        version=VERSION,
        security_id=security_id,
        market_date=market_date,
        expected_close=close,
        status="AVAILABLE" if rows else "MISSING",
        source=copy.deepcopy(source),
        facts=[],
        limitations=list(LIMITATIONS),
    )
    if not rows:
        result["limitations"].append("该证券该基准日原始响应无记录，不能填补或改用其他交易日。")
        return result
    row = rows[0]
    if (
        row["ts_code"] != security_id
        or row["trade_date"] != market_date
        or _number(row["close"]) != _number(close)
    ):
        raise ValueError("估值原文证券、基准日或收盘价不一致")
    for name in fields - {"ts_code", "trade_date", "close"}:
        if row.get(name) is not None:
            value = _number(row[name])
            if name in {"total_mv", "circ_mv", "total_share"} and value < 0:
                raise ValueError("市值或股本不能为负")
    for name, label, unit in METRICS:
        value = row.get(name)
        if value is None:
            result["limitations"].append(f"{label}（{name}）原文缺失，保留为空，不补零。")
        elif name == "pe_ttm" and _number(value) < 0:
            result["limitations"].append("负市盈率反映滚动盈利口径异常或亏损，不能解释为低估。")
        elif name == "pb" and _number(value) < 0:
            result["limitations"].append("负市净率涉及负净资产等口径，不能解释为便宜。")
        result["facts"].append(
            dict(
                metric_key=name,
                label=label,
                value=_text(_number(value)) if value is not None else None,
                unit=unit,
                window=dict(start=market_date, end=market_date),
                source_refs=[source["blob_ref"]],
            )
        )
    return result


def capture_valuation(client, security_id: str, market_date: str, expected_close: str) -> dict:
    """Use SourceClient's request/time budget and retain the original cache provenance."""
    _arguments(security_id, market_date, expected_close)
    source, _ = tushare.fetch(
        client,
        "valuation_snapshot",
        "daily_basic",
        dict(ts_code=security_id, trade_date=market_date),
    )
    return _rebuild(
        client.repo,
        source,
        security_id,
        market_date,
        expected_close,
        datetime.now(UTC).isoformat(),
    )


def verify_valuation(
    repo, result: dict, security_id: str, market_date: str, expected_close: str, sealed_at: str
) -> dict:
    """Recompute every fact and limitation; normalized results cannot verify themselves."""
    try:
        rebuilt = _rebuild(
            repo, result["source"], security_id, market_date, expected_close, sealed_at
        )
        if rebuilt != result:
            raise ValueError("估值工具结果与封存原文复算不一致")
        return rebuilt
    except (KeyError, TypeError, UnicodeError) as error:
        raise ValueError("估值工具封存资料缺失或无效") from error
