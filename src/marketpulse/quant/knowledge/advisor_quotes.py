"""Archive the observed quote separately from the approved end-of-day research."""

from datetime import datetime

from ..decision.next_session.labels import SHANGHAI
from ..decision.next_session.prices import TENCENT, quote


def observed(source, text, security_id):
    symbol = ("sh" if security_id.endswith(".SH") else "sz") + security_id[:6]
    value = quote(text, symbol, "TENCENT")
    return dict(
        security_id=security_id,
        price=value["close"],
        previous_close=value["reference_price"],
        quoted_at=value["quoted_at"],
        trading_status=value["trading_status"],
        kind="OBSERVED_QUOTE",
        source=source,
    )


def collect(client, report):
    sid = report["candidates"][0]["security_id"]
    symbol = ("sh" if sid.endswith(".SH") else "sz") + sid[:6]
    source, text = client.fetch(
        "holding_advisor_quote", TENCENT, params={"q": symbol}, family="TENCENT", encoding="gb18030"
    )
    return observed(source, text, sid)


def fallback(report):
    c = report["candidates"][0]
    day = datetime.strptime(report["market_date"], "%Y%m%d").replace(hour=15, tzinfo=SHANGHAI)
    return dict(
        security_id=c["security_id"],
        price=c["metrics"]["close"]["value"],
        previous_close=c["metrics"]["close"]["value"],
        quoted_at=day.isoformat(),
        trading_status="RESEARCH_CLOSE",
        kind="RESEARCH_CLOSE",
        source=None,
    )


def verify(repo, value, report, issued_at):
    if value["kind"] == "RESEARCH_CLOSE":
        if value != fallback(report):
            raise ValueError("收盘参考与固定研究不一致。")
    else:
        source = value["source"]
        if (
            source["source_family"] != "TENCENT"
            or source["url"]
            != TENCENT
            + "?q="
            + ("sh" if value["security_id"].endswith(".SH") else "sz")
            + value["security_id"][:6]
            or source["blob_ref"] != "blob://sha256/" + source["sha256"]
        ):
            raise ValueError("报价来源身份不一致。")
        parsed = observed(
            source, repo.raw_bytes(source["blob_ref"]).decode("gb18030"), value["security_id"]
        )
        if parsed != value:
            raise ValueError("报价与封存原始来源不一致。")
    stamp = datetime.fromisoformat(value["quoted_at"])
    issued = datetime.fromisoformat(issued_at)
    if stamp > issued or stamp.strftime("%Y%m%d") < report["market_date"]:
        raise ValueError("报价时间早于研究或晚于本轮封存时间。")
