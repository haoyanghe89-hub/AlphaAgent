"""Manual, post-close observation; no inferred ex-dividend adjustment."""

import re
from datetime import datetime, timedelta
from decimal import Decimal

from ...contracts import digest
from ..disclosures import QUERY, REGISTRY, original_url
from ..evidence import parse_pdf
from .contracts import NextSessionOutcome
from .labels import SHANGHAI, classify_outcome, close_at
from .prices import agree, fetch_quotes


def action_proof(client, repo, prediction, snapshot):
    security = prediction.security_id
    code = security[:6]
    _, registry = client.fetch("action_issuers", REGISTRY, family="CNINFO")
    issuer = next((i for i in registry.get("stockList", []) if i.get("code") == code), None)
    if not issuer:
        raise ValueError("除权息原文主体无法核定")
    _, packet = client.fetch(
        "action_catalog",
        QUERY,
        family="CNINFO",
        data=dict(
            pageNum="1",
            pageSize="30",
            column="szse",
            tabName="fulltext",
            stock=f"{code},{issuer['orgId']}",
            searchkey="权益分派",
            seDate=f"{prediction.target_session - timedelta(days=40)}~{prediction.target_session}",
            isHLtitle="false",
        ),
    )
    for row in packet.get("announcements") or []:
        if row.get("secCode") != code or "实施" not in row.get("announcementTitle", ""):
            continue
        url = original_url(row.get("adjunctUrl"))
        source, raw = client.fetch(
            "action_pdf_" + str(row["announcementId"]), url, family="CNINFO", binary=True
        )
        doc = parse_pdf(
            repo,
            dict(code=code, name=snapshot.model_context["name"], security_id=security),
            dict(row, category="action", original_url=url),
            source,
            raw,
        )
        text = "".join("".join(repo.value(doc["text_ref"])["pages"]).split())
        target = prediction.target_session
        if re.search(
            rf"除权(?:（息）|\(息\))?日[：:]?{target.year}年0?{target.month}月0?{target.day}日",
            text,
        ):
            return doc
    raise ValueError("行情参考价与基准收盘不同，未取得匹配目标日期的除权息实施原文，暂停核对")


def full_suspension(packet, security, target):
    for row in (packet.get("result") or {}).get("data") or []:
        if row.get("SECUCODE") != security:
            continue
        if row.get("SUSPEND_EXPIRE") not in ("全天", "全天停牌", "连续停牌"):
            continue
        start = datetime.fromisoformat(row["SUSPEND_START_TIME"]).replace(tzinfo=SHANGHAI)
        end = (
            datetime.fromisoformat(row["SUSPEND_END_TIME"]).replace(tzinfo=SHANGHAI)
            if row.get("SUSPEND_END_TIME")
            else None
        )
        resume = (
            datetime.fromisoformat(row["PREDICT_RESUME_DATE"]).date()
            if row.get("PREDICT_RESUME_DATE")
            else None
        )
        if start <= datetime.combine(
            target, datetime.min.replace(hour=9, minute=30).time(), SHANGHAI
        ) and (
            end and end >= close_at(target) or end is None and (resume is None or resume > target)
        ):
            return True
    return False


def suspension_result(client, prediction):
    from ...live.sources import EM

    source, packet = client.fetch(
        "suspension",
        EM,
        params=dict(
            reportName="RPT_CUSTOM_SUSPEND_DATA_INTERFACE",
            columns="ALL",
            filter=f"(MARKET=\"全部\")(DATETIME='{prediction.target_session}')",
            pageSize="500",
            pageNumber="1",
            source="WEB",
            client="WEB",
        ),
        family="EASTMONEY_SUSPENSION",
    )
    if not full_suspension(packet, prediction.security_id, prediction.target_session):
        raise ValueError("全日停牌未被明确核定")
    return dict(
        status="UNSCORABLE",
        limitations=["目标日全日停牌，不计平盘，不顺延。"],
        price_source_refs=[source["blob_ref"]],
        source_hashes=[source["sha256"]],
    )


def observe_one(repo, prediction, snapshot, client, now):
    status = "PENDING" if now < close_at(prediction.target_session) else "PENDING_DATA"
    result = dict(
        status=status,
        limitations=[
            "目标交易日尚未收盘。"
            if status == "PENDING"
            else "收盘后尚未取得可核对的目标日两路行情。"
        ],
    )
    if status == "PENDING":
        return result
    symbol = ("sh" if prediction.security_id.endswith(".SH") else "sz") + prediction.security_id[:6]
    try:
        quotes = fetch_quotes(client, [symbol])
        value = agree(quotes, symbol, prediction.target_session, now)
        sources = [s for s, _ in quotes]
        if value["trading_status"] == "NO_TRADES":
            return suspension_result(client, prediction)
        reference = Decimal(value["reference_price"])
        close = Decimal(value["close"])
        if close % Decimal("0.01") or reference % Decimal("0.01"):
            raise ValueError("原始价格精度不符")
        action = None
        if reference != snapshot.daily_prices[-1].close:
            action = action_proof(client, repo, prediction, snapshot)
        direction, correct = classify_outcome(prediction.prediction, close, reference)
        result = dict(
            status="SCORABLE",
            close=str(close),
            reference_price=str(reference),
            return_direction=direction,
            correct=correct,
            price_source_refs=[s["blob_ref"] for s in sources]
            + ([action["raw_ref"]] if action else []),
            source_hashes=[s["sha256"] for s in sources]
            + ([action["content_hash"]] if action else []),
            limitations=["按两路行情原始收盘与参考字段核对；不是全收益或可执行交易胜率。"],
        )
    except (ValueError, KeyError, OSError) as error:
        from .. import tushare
        from . import historical_prices

        if (
            client
            and tushare.load_token()
            and now.astimezone(SHANGHAI).date() > prediction.target_session
            and "冲突" not in str(error)
        ):
            try:
                return historical_prices.observe(client, prediction, snapshot, now)
            except (ValueError, KeyError, OSError) as historical_error:
                error = historical_error
        if client and "冲突" not in str(error):
            try:
                return suspension_result(client, prediction)
            except (ValueError, KeyError, OSError):
                pass
        result = dict(
            status="CONFLICT" if "冲突" in str(error) else "PENDING_DATA",
            limitations=[str(error)[:300]],
        )
    return result


def append_result(repo, prediction, result, now):
    previous = repo.outcome_versions(prediction.prediction_id)
    latest = previous[-1] if previous else None

    # Changing observation time alone is not a new outcome or a new scoring sample.
    def comparable(v):
        return dict(
            status=v.get("status"),
            close=Decimal(str(v["close"])) if v.get("close") is not None else None,
            reference_price=Decimal(str(v["reference_price"]))
            if v.get("reference_price") is not None
            else None,
            return_direction=v.get("return_direction"),
            correct=v.get("correct"),
            limitations=list(v.get("limitations", [])),
        )

    if (
        latest
        and latest["status"] in ("SCORABLE", "UNSCORABLE")
        and result["status"] in ("PENDING", "PENDING_DATA")
    ):
        return latest
    if latest and comparable(latest) == comparable(result):
        return latest
    version = latest["version"] + 1 if latest else 1
    value = dict(
        prediction_id=prediction.prediction_id,
        observed_at=now,
        version=version,
        supersedes_id=latest["outcome_id"] if latest else None,
        revision_reason="手动重新取得目标日结果或来源修订，保留前一版本。" if latest else None,
        **result,
    )
    oid = "DEC-OUT-" + digest(
        dict(prediction_id=prediction.prediction_id, version=version, result=result)
    )
    outcome = NextSessionOutcome(outcome_id=oid, **value)
    return repo.add_outcome(outcome)
