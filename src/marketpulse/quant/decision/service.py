"""P1 deterministic evidence workflow. Model reasoning is introduced separately."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from marketpulse.infrastructure.storage.models import BlobError
from marketpulse.telemetry.tracing import stage, workflow

from . import industry, market, report
from .contracts import CoverageReport, EvidenceBundle, EvidenceItem, SourceCapability
from .disclosures import CATEGORIES, LABELS, catalog
from .evidence import deduplicate, parse_pdf, retrieve
from .tools import SourceClient, SourceUnavailable, Stopped, resolve

SHANGHAI = ZoneInfo("Asia/Shanghai")
MATERIALS = (*CATEGORIES, "daily", "margin")


def source_capability(source):
    return SourceCapability(
        source_id=source["key"] + "-" + source["sha256"][:12],
        name=source["key"],
        url=source["url"],
        source_family=source["source_family"],
        acquisition="固定来源、限流HTTPS请求与内容寻址归档",
        authentication="本地Tushare账户凭据；不进入快照或日志"
        if source["source_family"] == "TUSHARE"
        else "公开入口；未使用账户凭据",
        license_note="账户权限仅作本地内部研究；积分不代表对外再分发许可。"
        if source["source_family"] == "TUSHARE"
        else "公开读取不等于再分发许可，原文只在本机私有归档使用。",
        coverage="本任务小样本",
        frequency="按来源披露或交易日",
        tested_at=source["fetched_at"],
        status="AVAILABLE",
        reason="本次响应已取得并归档；不证明全范围覆盖或独立验证。",
        content_hash=source["sha256"],
        raw_ref=source["blob_ref"],
    )


@stage("decision.sources.collect", as_type="retriever")
def collect(repo, state, stop, *, publish=True):
    request = state["request"]
    prior_seconds, attempt_started = state.get("elapsed_seconds", 0), time.monotonic()

    def save():
        if stop.is_set():
            raise Stopped("取证已停止")
        state["elapsed_seconds"] = prior_seconds + time.monotonic() - attempt_started
        repo.save(state)

    def step(key, detail, status="RUNNING"):
        node = next((n for n in state["process"] if n["key"] == key), None)
        if node is None:
            node = dict(key=key, detail=detail)
            state["process"].append(node)
        node.update(status=status, updated_at=datetime.now(UTC).isoformat())
        save()

    client = SourceClient(repo, state, save, stop)
    step("identity", "核对动态证券身份与公司资料")
    subject = resolve(client, request["entity"])
    state["subject"] = subject
    step("identity", "证券身份已与公司资料核对", "COMPLETED")
    fixed_cutoff = (
        datetime.fromisoformat(request["cutoff_at"]) if request.get("cutoff_at") else None
    )
    query_cutoff = fixed_cutoff or datetime.fromisoformat(state["created_at"])
    documents, items, gaps, coverage = [], [], [], []
    if request["mode"] == "historical":
        step("archive", "只读取可证明在截止时点已取得的归档版本")
        prior_bundles = repo.archived(subject["security_id"], query_cutoff)
        for old in prior_bundles:
            documents.extend(
                d
                for d in old["documents"]
                if datetime.fromisoformat(d["available_at"]) <= query_cutoff
            )
            items.extend(
                EvidenceItem.model_validate(e)
                for e in old["bundle"]["items"]
                if e["kind"] != "disclosure"
                and datetime.fromisoformat(e["available_at"]) <= query_cutoff
            )
        archived_sources = [s for old in prior_bundles for s in old["sources"]]
        gaps.append(
            dict(
                category="historical",
                need="截止时点可得性证明",
                reason="只用本机首次取得时点不晚于截止的版本；不以今天取到的旧报告倒填历史。当前证券身份核对不证明历史身份。",
            )
        )
        step("archive", "完成归档时点过滤", "COMPLETED")
    else:
        archived_sources = []
        step("disclosures", "按报告、业绩、投资者关系三类定向检索")
        if state["tool_results"].get("disclosures"):
            checkpoint = repo.value(state["tool_results"]["disclosures"])
            documents, coverage, gaps = (
                checkpoint["documents"],
                checkpoint["coverage"],
                checkpoint["gaps"],
            )
        else:
            try:
                selected, coverage = catalog(client, subject, request["question"], query_cutoff)
                for announcement in selected:
                    client.check()
                    try:
                        source, raw = client.fetch(
                            "pdf_" + str(announcement["announcementId"]),
                            announcement["original_url"],
                            family="CNINFO",
                            binary=True,
                        )
                        documents.append(parse_pdf(repo, subject, announcement, source, raw))
                    except (SourceUnavailable, ValueError) as error:
                        gaps.append(
                            dict(
                                category=announcement["category"],
                                need=announcement["announcementTitle"],
                                reason=str(error)[:200],
                            )
                        )
            except SourceUnavailable as error:
                gaps.append(dict(category="disclosures", need="三类公司披露", reason=str(error)))
            state["tool_results"]["disclosures"] = repo.blob(
                dict(documents=documents, coverage=coverage, gaps=gaps)
            )
            save()
        step("disclosures", f"已保留{len(documents)}份原文与各类检索缺口", "COMPLETED")
        step("market", "取得未复权行情与独立融资渠道数据")
        local = query_cutoff.astimezone(SHANGHAI)
        end = local.date() if local.hour >= 15 else local.date() - timedelta(days=1)
        new_items, market_gaps = market.collect(client, subject, end - timedelta(days=180), end)
        items.extend(new_items)
        gaps.extend(market_gaps)
        step("market", "已整理实际行情/融资样本，缺失不补零", "COMPLETED")
    client.check()
    sealed_at = datetime.now(UTC)
    cutoff = fixed_cutoff or sealed_at
    documents = deduplicate(documents)
    documents = [d for d in documents if datetime.fromisoformat(d["available_at"]) <= cutoff]
    materialized = [dict(d, pages=repo.value(d["text_ref"])["pages"]) for d in documents]
    items.extend(retrieve(materialized, request["question"], cutoff))
    unique = {
        e.evidence_id: e
        for e in items
        if e.available_at <= cutoff and (e.observed_at is None or e.observed_at <= cutoff)
    }
    items = list(unique.values())
    for category in CATEGORIES:
        if not any(d["category"] == category for d in documents):
            gaps.append(
                dict(
                    category=category,
                    need=LABELS[category],
                    reason="本次有限检索未取得在截止时点可用的有效原文，不代表公司未披露。",
                )
            )
    present = {d["category"] for d in documents}
    if any(e.kind == "fact" for e in items):
        present.add("daily")
    if any(e.kind == "flow" for e in items):
        present.add("margin")
    for category in ("daily", "margin"):
        if category not in present and not any(g["category"] == category for g in gaps):
            gaps.append(
                dict(
                    category=category,
                    need=category,
                    reason="没有截止时点可用的归档观察，不回填当前数据。",
                )
            )
    sources = [c["source"] for c in state["calls"] if c["status"] == "COMPLETED"] + archived_sources
    sources = list({(s["blob_ref"], s["key"]): s for s in sources}.values())
    capabilities = [source_capability(s) for s in sources]
    bundle = EvidenceBundle(
        items=tuple(items),
        sources=tuple(capabilities),
        cutoff_at=cutoff,
        coverage=CoverageReport(
            universe_total=5,
            attempted=5,
            available=len(present),
            missing=5 - len(present),
            excluded=0,
            universe_ref="p1-material-types-v1",
            member_refs=MATERIALS,
            source_dates=tuple(sorted({e.period_end.isoformat() for e in items if e.period_end})),
            method="三类披露+日行情+融资五类输入覆盖，不是证券或市场覆盖",
            status="COMPLETE" if len(present) == 5 else "PARTIAL" if present else "UNAVAILABLE",
        ),
    )
    step("verify", "核对引用、证券身份、页码、字符位置与可用时点")
    method_context = industry.context(subject, documents, items, cutoff)
    data = dict(
        schema_version="decision-v1",
        sources=sources,
        documents=documents,
        evidence=[e.model_dump(mode="json") for e in items],
        bundle=bundle.model_dump(mode="json"),
        report=report.build(subject, bundle, documents, coverage, gaps, method_context),
        sealed_at=sealed_at.isoformat(),
        cutoff_policy="EXPLICIT_AS_OF" if fixed_cutoff else "SERVER_SEALED_AFTER_COLLECTION",
    )
    from ..contracts import digest

    result_ref = repo.blob(data)
    # Verify the same artifact readers will consume before publishing completion.
    result_sha256 = digest(data)
    repo.verify_data(data, result_sha256, subject["security_id"])
    client.check()
    if not publish:
        state.update(
            evidence_ref=result_ref, evidence_sha256=result_sha256, sealed_at=sealed_at.isoformat()
        )
        step("verify", "基础证据已封存，进入经营研究与复核", "COMPLETED")
        return state
    step("verify", "原文、解析和引用一致；未运行模型判断", "COMPLETED")
    state.update(
        result_ref=result_ref,
        result_sha256=result_sha256,
        sealed_at=sealed_at.isoformat(),
        status="COMPLETED",
        completed_at=datetime.now(UTC).isoformat(),
        error=None,
    )
    save()
    return state


@workflow("alphaagent.decision_research", id_parameter="run_id")
async def run(repo, run_id, stop, *, collector=collect):
    state = repo.get(run_id, with_data=False)
    started, prior_seconds = time.monotonic(), state.get("elapsed_seconds", 0)
    try:
        async with asyncio.timeout(
            max(0, state["request"]["budget_profile"]["max_seconds"] - prior_seconds)
        ):
            await asyncio.to_thread(collector, repo, state, stop)
    except (asyncio.CancelledError, TimeoutError):
        stop.set()
        latest = repo.get(run_id, with_data=False)
        if latest["status"] == "RUNNING":
            latest.update(
                status="INTERRUPTED",
                error="取证中断或达到时限；成功请求已保留，可继续。",
                elapsed_seconds=prior_seconds + time.monotonic() - started,
            )
            repo.save(latest)
    except (ValueError, OSError, KeyError, BlobError) as error:
        stop.set()
        latest = repo.get(run_id, with_data=False)
        if latest["status"] == "RUNNING":
            latest.update(
                status="FAILED",
                error=str(error)[:250],
                elapsed_seconds=prior_seconds + time.monotonic() - started,
            )
            repo.save(latest)
    except Exception as error:
        stop.set()
        latest = repo.get(run_id, with_data=False)
        if latest["status"] == "RUNNING":
            latest.update(
                status="FAILED",
                error=f"取证执行失败（{type(error).__name__}）；请求与过程已保留。",
                elapsed_seconds=prior_seconds + time.monotonic() - started,
            )
            repo.save(latest)
