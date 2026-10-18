"""Sealed real evidence, exact derived observations and reproducible lineage."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import uuid4

from ...contracts import digest
from ...live.sources import KLINE
from ..contracts import CoverageReport, EvidenceBundle, EvidenceItem, Locator, ResearchRequest
from ..service import collect, source_capability
from ..tools import SourceClient
from .contracts import DailyPrice, NextSessionSnapshot
from .labels import close_at, next_session
from .prices import agree, bars, fetch_quotes, quote

METHOD = "next-session-observations-v1"


def indicators(rows, market_rows, reference):
    with localcontext() as ctx:
        ctx.prec = 28
        close = Decimal(rows[-1]["close"])
        market = Decimal(market_rows[-1]["close"])
        returns = [
            Decimal(rows[i]["close"]) / Decimal(rows[i - 1]["close"]) - 1
            for i in range(1, len(rows))
        ]
        mean = sum(returns) / len(returns)
        return [
            dict(
                metric_id="DAY",
                label="基准日行情涨跌幅",
                value=str(close / reference - 1),
                unit="ratio",
            ),
            *[
                dict(
                    metric_id="R" + str(n),
                    label=f"近{n}日未复权价格变化",
                    value=str(close / Decimal(rows[-n - 1]["close"]) - 1),
                    unit="ratio",
                )
                for n in (5, 20)
            ],
            dict(
                metric_id="VOL20",
                label="近20日未复权日价格变化标准差",
                value=str((sum((r - mean) ** 2 for r in returns) / len(returns)).sqrt()),
                unit="ratio",
            ),
            dict(
                metric_id="TURN",
                label="近5日与20日平均成交额之比",
                value=str(
                    (sum(Decimal(r["turnover"]) for r in rows[-5:]) / 5)
                    / (sum(Decimal(r["turnover"]) for r in rows[-20:]) / 20)
                ),
                unit="ratio",
            ),
            dict(
                metric_id="MARKET5",
                label="上证指数近5日价格变化",
                value=str(market / Decimal(market_rows[-6]["close"]) - 1),
                unit="ratio",
            ),
            dict(
                metric_id="REL5",
                label="近5日相对上证指数价格变化差",
                value=str(
                    close / Decimal(rows[-6]["close"]) - market / Decimal(market_rows[-6]["close"])
                ),
                unit="ratio",
            ),
        ]


def normalized_quote(repo, source, value):
    material = dict(
        parser="daily-quote-v1",
        provider=source["source_family"],
        source_ref=source["blob_ref"],
        quote=value,
    )
    ref = repo.blob(material)
    return dict(
        source,
        key=source["key"] + "_" + value["symbol"] + "_parsed",
        blob_ref=ref,
        sha256=digest(material),
        raw_source_ref=source["blob_ref"],
    ), material


def daily_input(repo, client, sources, symbol, base):
    """The quote protocol uses Tencent bars even when disclosure research prefers Tushare."""
    matching = [s for s in sources if s["key"] == "daily"]
    if matching:
        if len(matching) != 1 or matching[0]["source_family"] != "TENCENT":
            raise ValueError("日预测原始日线来源缺失或冲突")
        source = matching[0]
        return source, json.loads(repo.raw_bytes(source["blob_ref"]))
    # Provider-specific Tushare tables cannot be parsed as Tencent's day-array schema.
    return client.fetch(
        "daily",
        KLINE,
        params={"param": f"{symbol},day,{base - timedelta(days=180)},{base},640,"},
        family="TENCENT",
    )


def build_snapshot(repo, state, stop, security, calendar, save):
    budget = state["config"]["budget"]
    remaining = max(1, min(900, int(budget["max_seconds"] - state.get("elapsed_seconds", 0))))
    request = ResearchRequest(
        request_key=uuid4(),
        entity=security,
        question="核查下一交易日价格方向所需的近期经营变化、已披露风险与现金回收；长期资料不能单独证明明日方向",
        budget_profile=dict(max_seconds=remaining),
    )
    child, _ = repo.evidence.create(request.model_dump(mode="json"), state["implementation"])
    state.setdefault("evidence_runs", {})[security] = child["run_id"]
    save()
    try:
        collect(repo.evidence, child, stop)
    except BaseException:
        latest = repo.evidence.get(child["run_id"], with_data=False)
        if latest["status"] == "RUNNING":
            latest.update(status="INTERRUPTED", error="日预测批次取证中断；保留已取得原文。")
            repo.evidence.save(latest)
        raise
    finally:
        state["calls"].extend(dict(c, security_id=security, kind="source") for c in child["calls"])
    child = repo.evidence.get(child["run_id"])
    subject = child["subject"]
    data = child["data"]
    if subject["security_id"] != security:
        raise ValueError("实验证券身份冲突")
    local = datetime.now(UTC).astimezone(close_at(calendar.sessions[0]).tzinfo)
    sessions = calendar.sessions
    completed = tuple(s for s in sessions if close_at(s) <= local)
    if not completed:
        raise ValueError("没有已收盘且已核定的基准交易日")
    base = completed[-1]
    target = next_session(sessions, base)
    if local.date() >= target:
        raise ValueError("下一目标交易日已经开始，当前收盘输入不足")
    client = SourceClient(repo, state, save, stop, max_requests=60, budget_profile=budget)
    symbols = [subject["symbol"], "sh000001"]
    quotes = fetch_quotes(client, symbols)
    observed = agree(quotes, subject["symbol"], base, datetime.now(UTC))
    if observed["trading_status"] != "TRADED":
        raise ValueError("基准日没有可核对成交，不能伪造完整行情")
    sources = list(data["sources"]) + [c["source"] for c in state["calls"] if c.get("source")]
    daily_source, raw_daily = daily_input(repo, client, data["sources"], subject["symbol"], base)
    sources.append(daily_source)
    rows = bars(raw_daily, subject["symbol"], sessions, base)
    if Decimal(rows[-1]["close"]) != Decimal(observed["close"]):
        raise ValueError("日线与两路收盘行情冲突")
    if abs(Decimal(rows[-1]["turnover"]) - Decimal(observed["turnover"])) > 10000:
        raise ValueError("日线成交额单位未通过收盘原表交叉核对")
    market_source, market_raw = client.fetch(
        "market_daily",
        KLINE,
        params={"param": f"sh000001,day,{base - timedelta(days=180)},{base},640,"},
        family="TENCENT",
    )
    market_rows = bars(market_raw, "sh000001", sessions, base)
    if Decimal(market_rows[-1]["close"]) != Decimal(quotes[0][1]["sh000001"]["close"]):
        raise ValueError("市场日线与收盘观察不一致")
    sources.append(market_source)
    items = list(data["bundle"]["items"])
    normalized = []
    for source, values in quotes:
        value = values[subject["symbol"]]
        parsed, material = normalized_quote(repo, source, value)
        normalized.append(parsed)
        sources.append(parsed)
        for field, label in [("close", "收盘价"), ("reference_price", "行情参考价")]:
            items.append(
                EvidenceItem(
                    evidence_id="NS-" + source["source_family"] + "-" + field,
                    subject_id=security,
                    kind="fact",
                    source_family=source["source_family"],
                    original_source_id="quote:" + str(base),
                    url=source["url"],
                    raw_ref=parsed["blob_ref"],
                    content_hash=parsed["sha256"],
                    locator=Locator(kind="json", path="/quote/" + field),
                    text=f"{base} {label} {value[field]} 元",
                    value=value[field],
                    unit="CNY/share",
                    observed_at=value["quoted_at"],
                    fetched_at=source["fetched_at"],
                    available_at=source["fetched_at"],
                    time_precision="second",
                    method_version="daily-quote-v1",
                    quality_status="AVAILABLE",
                )
            )
    derived = indicators(rows, market_rows, Decimal(observed["reference_price"]))
    for observation in derived:
        material = dict(
            algorithm=METHOD,
            daily_ref=daily_source["blob_ref"],
            market_ref=market_source["blob_ref"],
            quote_ref=normalized[0]["blob_ref"],
            symbol=subject["symbol"],
            base_session=str(base),
            sessions=[str(s) for s in sessions],
            **observation,
        )
        ref = repo.blob(material)
        source = dict(
            key=observation["metric_id"],
            url=daily_source["url"],
            source_family="DETERMINISTIC_CALCULATION",
            blob_ref=ref,
            sha256=digest(material),
            fetched_at=datetime.now(UTC).isoformat(),
        )
        sources.append(source)
        items.append(
            EvidenceItem(
                evidence_id="NS-" + observation["metric_id"],
                subject_id=security,
                kind="calculation",
                source_family=source["source_family"],
                original_source_id=METHOD + ":" + observation["metric_id"],
                url=source["url"],
                raw_ref=ref,
                content_hash=source["sha256"],
                locator=Locator(kind="calculation", path="/value"),
                text=f"{observation['label']}：{observation['value']}（比例）",
                value=observation["value"],
                unit="ratio",
                observed_at=close_at(base),
                fetched_at=source["fetched_at"],
                available_at=source["fetched_at"],
                time_precision="second",
                method_version=METHOD,
                quality_status="AVAILABLE",
                limitations=(
                    "未复权价格变化不是全收益，除权可能影响历史价格变化；不据此认定经营原因。",
                ),
            )
        )
    client.check()
    sealed = datetime.now(UTC)
    sources = list({s["blob_ref"]: s for s in sources}.values())
    bundle = EvidenceBundle(
        items=tuple(EvidenceItem.model_validate(e) for e in items),
        sources=tuple(source_capability(s) for s in sources),
        cutoff_at=sealed,
        coverage=CoverageReport(
            universe_total=1,
            attempted=1,
            available=1,
            missing=0,
            excluded=0,
            universe_ref=security,
            member_refs=(security,),
            source_dates=(str(base),),
            method="本标的最低日预测输入通过；不是全市场或全部资金覆盖",
            status="COMPLETE",
        ),
    )
    daily = tuple(
        DailyPrice(
            security_id=security,
            session=r["session"],
            close=r["close"],
            reference_price=observed["reference_price"] if i == len(rows) - 1 else None,
            price_tick="0.01",
            turnover=r["turnover"],
            source_ref=daily_source["blob_ref"],
            reference_basis="EXCHANGE_QUOTE_PREVCLOSE" if i == len(rows) - 1 else "UNAVAILABLE",
            suspended_all_day=False,
        )
        for i, r in enumerate(rows)
    )
    disclosure = [e for e in bundle.items if e.kind == "disclosure"][:3]
    facts = [
        e for e in bundle.items if e.evidence_id.startswith("NS-") and e.source_family != "SINA"
    ]
    flows = [e for e in bundle.items if e.kind == "flow"][:1]
    selected = facts + flows + disclosure
    context = dict(
        security_id=security,
        name=subject["name"],
        base_session=str(base),
        target_session=str(target),
        evidence_sealed_at=sealed.isoformat(),
        evidence=[
            dict(
                evidence_id=e.evidence_id,
                text=e.text[:280],
                kind=e.kind,
                period_end=str(e.period_end) if e.period_end else None,
                limitations=list(e.limitations),
            )
            for e in selected
        ],
        missing_inputs=[
            "行业同口径日行情未接入",
            "供应商资金估算及隔夜未来事件不可得",
            *[g["need"] for g in data["report"]["gaps"]],
        ],
        method_version=METHOD,
    )
    value = dict(
        security_id=security,
        base_session=base,
        target_session=target,
        requested_at=state["created_at"],
        evidence_sealed_at=sealed,
        calendar=calendar,
        daily_prices=daily,
        evidence=bundle,
        indicator_refs=tuple(e.raw_ref for e in bundle.items if e.kind == "calculation"),
        missing_inputs=tuple(context["missing_inputs"]),
        input_hash="0" * 64,
        evidence_run_id=child["run_id"],
        sources=tuple(sources),
        documents=tuple(data["documents"]),
        indicators=tuple(derived),
        model_context=context,
    )
    snap = NextSessionSnapshot(**value)
    serialized = snap.model_dump(mode="json")
    serialized["input_hash"] = digest({k: v for k, v in serialized.items() if k != "input_hash"})
    ref = repo.blob(serialized)
    repo.snapshot(ref)
    return ref, NextSessionSnapshot.model_validate(serialized), subject


def verify_snapshot(repo, snap, data):
    from marketpulse.infrastructure.storage.models import BlobRef

    from .calendar import parse_annual, plain, rule_text

    calendar = snap.calendar
    annual_ref, rules_ref = calendar.source_refs
    sessions = parse_annual(repo.raw_bytes(annual_ref).decode("utf8"), calendar.coverage_start.year)
    material = dict(
        exchange=calendar.exchange,
        year=calendar.coverage_start.year,
        sessions=[str(s) for s in sessions],
        annual_hash=BlobRef.from_uri(annual_ref).sha256,
        rules_hash=BlobRef.from_uri(rules_ref).sha256,
        parser="official-annual-2026-v1",
    )
    rules = plain(rule_text(repo.raw_bytes(rules_ref)))
    if (
        sessions != calendar.sessions
        or digest(material) != calendar.content_hash
        or "每周一至周五" not in rules
    ):
        raise ValueError("交易日历不能按原始公告复核")
    if calendar.verified_at > snap.evidence_sealed_at:
        raise ValueError("日历晚于封存时点")
    # Reuse the P1 original/text/JSON reader for all non-derived evidence.
    original = [e for e in data["evidence"] if e["kind"] != "calculation"]
    ordinary = dict(data, bundle=dict(data["bundle"], items=original), evidence=original)
    repo.evidence.verify_data(ordinary, digest(ordinary))
    for source in snap.sources:
        if source.get("raw_source_ref"):
            value = repo.value(source["blob_ref"])
            parsed = quote(
                repo.raw_bytes(value["source_ref"]).decode("gb18030"),
                value["quote"]["symbol"],
                value["provider"],
            )
            if parsed != value["quote"]:
                raise ValueError("行情解析结果与原始响应冲突")
    normalized = [s for s in snap.sources if s.get("raw_source_ref")]
    quotes = [
        (s, {snap.model_context.get("symbol", ""): repo.value(s["blob_ref"])["quote"]})
        for s in normalized
    ]
    if len(quotes) != 2:
        raise ValueError("最低两路收盘行情不足")
    parsed = [repo.value(s["blob_ref"])["quote"] for s in normalized]
    symbol = parsed[0]["symbol"]
    observed = agree(
        [(s, {symbol: v}) for s, v in zip(normalized, parsed, strict=True)],
        symbol,
        snap.base_session,
        snap.evidence_sealed_at,
    )
    daily_source = next(s for s in snap.sources if s["key"] == "daily")
    rows = bars(
        json.loads(repo.raw_bytes(daily_source["blob_ref"])),
        symbol,
        calendar.sessions,
        snap.base_session,
    )
    expected_daily = [
        DailyPrice(
            security_id=snap.security_id,
            session=r["session"],
            close=r["close"],
            reference_price=observed["reference_price"] if i == len(rows) - 1 else None,
            price_tick="0.01",
            turnover=r["turnover"],
            source_ref=daily_source["blob_ref"],
            reference_basis="EXCHANGE_QUOTE_PREVCLOSE" if i == len(rows) - 1 else "UNAVAILABLE",
            suspended_all_day=False,
        )
        for i, r in enumerate(rows)
    ]
    if tuple(expected_daily) != snap.daily_prices or symbol[2:] != snap.security_id[:6]:
        raise ValueError("封存日线或主体与原始行情不符")
    if (
        Decimal(observed["close"]) != snap.daily_prices[-1].close
        or abs(Decimal(observed["turnover"]) - snap.daily_prices[-1].turnover) > 10000
    ):
        raise ValueError("封存基准行情未能交叉核对")
    child = repo.evidence.get(snap.evidence_run_id)
    base_items = child["data"]["bundle"]["items"]
    if [
        e.model_dump(mode="json")
        for e in snap.evidence.items
        if not e.evidence_id.startswith("NS-")
    ] != base_items:
        raise ValueError("封存披露与原始取证报告不符")
    selected = [
        e
        for e in snap.evidence.items
        if e.evidence_id.startswith("NS-") and e.source_family != "SINA"
    ]
    selected += [e for e in snap.evidence.items if e.kind == "flow"][:1]
    selected += [e for e in snap.evidence.items if e.kind == "disclosure"][:3]
    context = dict(
        security_id=snap.security_id,
        name=child["subject"]["name"],
        base_session=str(snap.base_session),
        target_session=str(snap.target_session),
        evidence_sealed_at=snap.evidence_sealed_at.isoformat(),
        evidence=[
            dict(
                evidence_id=e.evidence_id,
                text=e.text[:280],
                kind=e.kind,
                period_end=str(e.period_end) if e.period_end else None,
                limitations=list(e.limitations),
            )
            for e in selected
        ],
        missing_inputs=[
            "行业同口径日行情未接入",
            "供应商资金估算及隔夜未来事件不可得",
            *[g["need"] for g in child["data"]["report"]["gaps"]],
        ],
        method_version=METHOD,
    )
    if context != snap.model_context or tuple(context["missing_inputs"]) != snap.missing_inputs:
        raise ValueError("模型上下文与封存证据不符")
    if (
        tuple(e.raw_ref for e in snap.evidence.items if e.kind == "calculation")
        != snap.indicator_refs
    ):
        raise ValueError("派生引用列表冲突")
    expected_indicators = []
    for item in snap.evidence.items:
        if item.kind != "calculation":
            continue
        material = repo.value(item.raw_ref)
        if material["algorithm"] != METHOD:
            raise ValueError("未知派生算法")
        sessions = tuple(datetime.fromisoformat(d).date() for d in material["sessions"])
        base = datetime.fromisoformat(material["base_session"]).date()
        rows = bars(
            json.loads(repo.raw_bytes(material["daily_ref"])), material["symbol"], sessions, base
        )
        market = bars(
            json.loads(repo.raw_bytes(material["market_ref"])), "sh000001", sessions, base
        )
        reference = Decimal(repo.value(material["quote_ref"])["quote"]["reference_price"])
        expected = next(
            v
            for v in indicators(rows, market, reference)
            if v["metric_id"] == material["metric_id"]
        )
        expected_indicators.append(expected)
        if Decimal(expected["value"]) != item.value or material["value"] != expected["value"]:
            raise ValueError("派生指标不能按封存原文复算")
    if tuple(expected_indicators) != snap.indicators:
        raise ValueError("派生指标视图冲突")
