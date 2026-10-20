"""Optional public research wrapped around an unchanged, verified daily snapshot."""

import asyncio
import copy
import json
import re
from datetime import UTC, datetime
from decimal import Decimal

from ...contracts import digest
from ...knowledge import active_research as active
from ...knowledge.research_context import knowledge_snapshot
from ...knowledge.rule_store import RuleStore
from ...knowledge.store import KnowledgeStore
from ..contracts import EvidenceBundle, EvidenceItem, Locator
from ..service import source_capability
from .agents import SELECTED_PROMPTS
from .contracts import EnhancedNextSessionSnapshot

VERSION = "next-session-enhanced-v1"
PROMPT_VERSION = "NEXT_SESSION_ACTIVE_RESEARCH_V1"
METHOD_VERSION = "NEXT_SESSION_ACTIVE_METHOD_V1"
PROMPTS = dict(SELECTED_PROMPTS)
PROMPTS["Analyst"] += (
    "主动研究协议：结合research_selection中的假设与反证检查，优先核查新工具结果是否改变判断。"
    "methods仅为参考方法及风险边界，不能当作明日涨跌事实或已验证策略。"
    "估值原表只是基准日快照，不能用低估值直接推出明天上涨。"
    "各事实只引用evidence内ID，工具检索未命中和失败不是相反事实。"
)
PROMPTS["Verifier"] += (
    "同时检查主动研究的假设与反证是否被回应，参考方法不能替代真实股票证据。"
    "单日市盈率、市净率或市值不能单独证明下一交易日方向。"
)


def enable(state):
    if state["request"].get("enhanced_research") is not True:
        return
    state["config"].update(
        agentic_research_v1=True,
        bounded_context_v1=True,
        prompt_version=PROMPT_VERSION,
        method_version=METHOD_VERSION,
    )


def analysis_context(context, config):
    if not config.get("bounded_context_v1"):
        return context
    result = active.model_view(context)
    required = {"KR-AVOID-RUIN", "KR-EXPECTATION-EXIT"} | {
        r for s in context["research_selection"] for r in s["method_rule_ids"]
    }
    result["methods"] = [m for m in context["methods"] if m["rule_id"] in required]
    return result


def rules_for(repo):
    return RuleStore(KnowledgeStore(repo.base.sessions, repo.base.blobs))


def basis(snapshot):
    items = {e.evidence_id: e for e in snapshot.evidence.items}
    evidence = []
    for item in snapshot.model_context["evidence"]:
        original = items[item["evidence_id"]]
        fact = dict(item, security_id=snapshot.security_id)
        if original.value is not None:
            fact.update(value=str(original.value), unit=original.unit)
        if original.evidence_id == "NS-TENCENT-close":
            fact["metric_key"] = "close"
        evidence.append(fact)
    return dict(
        horizon="NEXT_SESSION",
        market_date=snapshot.base_session.strftime("%Y%m%d"),
        cutoff=snapshot.evidence_sealed_at.isoformat(),
        stocks=[dict(security_id=snapshot.security_id, name=snapshot.model_context["name"])],
        evidence=evidence,
        methods=[],
        limitations=list(snapshot.missing_inputs),
    )


def tool_evidence(repo, base, enriched):
    items, sources = [], []
    for tool in enriched["tool_results"]:
        if tool["tool"] != "VALUATION_SNAPSHOT" or not tool.get("source"):
            continue
        source = tool["source"]
        sources.append(source)
        fields = repo.value(source["blob_ref"])["data"]["fields"]
        for fact in tool["facts"]:
            if fact["value"] is None:
                continue
            metric = fact["metric_key"]
            is_value = metric in {"total_mv", "circ_mv"}
            value = Decimal(fact["value"]) * (10000 if is_value else 1)
            items.append(
                EvidenceItem(
                    evidence_id="R-" + base.security_id.replace(".", "") + "-" + metric,
                    subject_id=base.security_id,
                    kind="fact",
                    source_family=source["source_family"],
                    original_source_id="daily_basic:" + str(base.base_session),
                    url=source["url"],
                    raw_ref=source["blob_ref"],
                    content_hash=source["sha256"],
                    locator=Locator(kind="json", path="/data/items/0/" + str(fields.index(metric))),
                    text=f"{base.base_session} {fact['label']}：{fact['value']} {fact['unit']}",
                    value=str(value),
                    unit="CNY" if is_value else "ratio",
                    observed_at=base.daily_prices[-1].session.isoformat() + "T15:00:00+08:00",
                    fetched_at=source["fetched_at"],
                    available_at=source["fetched_at"],
                    time_precision="day",
                    method_version=tool["version"],
                    quality_status="AVAILABLE",
                    limitations=tuple(tool["limitations"]),
                )
            )
    return items, sources


def material(repo, base_ref, base, knowledge_ref, research_state_ref, sealed_at):
    knowledge = repo.value(knowledge_ref)
    research = repo.value(research_state_ref)
    if (
        research["config"].get("agentic_research_v1") is not True
        or research["config"].get("prompt_version") != PROMPT_VERSION
        or research["config"].get("method_version") != METHOD_VERSION
    ):
        raise ValueError("主动研究没有绑定增强推理配置")
    manifest = repo.value(research["active_manifest_ref"])
    if not base.evidence_sealed_at <= datetime.fromisoformat(manifest["sealed_at"]) <= sealed_at:
        raise ValueError("主动研究与最终快照封存顺序不一致")
    enriched = active.verify(repo, research, basis(base), knowledge)
    additional, extra_sources = tool_evidence(repo, base, enriched)
    context = dict(
        base.model_context,
        evidence_sealed_at=sealed_at.isoformat(),
        evidence=enriched["evidence"],
        methods=enriched["methods"],
        research_selection=enriched["research_selection"],
        tool_results=enriched["tool_results"],
        limitations=enriched["limitations"],
        active_research_version=active.VERSION,
        method_version=METHOD_VERSION,
    )
    # Presentation facts retain the same IDs as the model's verified tool evidence.
    presentation = {e.evidence_id: e for e in additional}
    for fact in context["evidence"]:
        if fact["evidence_id"] in presentation:
            fact["text"] = presentation[fact["evidence_id"]].text
            fact["kind"] = "fact"
            fact["limitations"] = list(presentation[fact["evidence_id"]].limitations)
    sources = list({s["blob_ref"]: s for s in [*base.sources, *extra_sources]}.values())
    bundle = EvidenceBundle(
        items=(*base.evidence.items, *additional),
        sources=tuple(source_capability(s) for s in sources),
        cutoff_at=sealed_at,
        coverage=base.evidence.coverage,
    )
    value = dict(
        base.model_dump(mode="json"),
        schema_version=VERSION,
        base_snapshot_ref=base_ref,
        knowledge_ref=knowledge_ref,
        research_state_ref=research_state_ref,
        evidence_sealed_at=sealed_at.isoformat(),
        evidence=bundle.model_dump(mode="json"),
        sources=sources,
        model_context=context,
    )
    value = EnhancedNextSessionSnapshot.model_validate(value).model_dump(mode="json")
    value["input_hash"] = digest({k: v for k, v in value.items() if k != "input_hash"})
    return value


async def build(repo, state, base_ref, base, roles, client, save):
    binding = state.setdefault("enhanced_research_by_security", {}).setdefault(base.security_id, {})
    if "knowledge_ref" not in binding:
        knowledge = await asyncio.to_thread(
            knowledge_snapshot,
            rules_for(repo),
            datetime.fromisoformat(state["created_at"]),
            dynamic=True,
        )
        binding["knowledge_ref"] = repo.blob(knowledge)
        save()
    knowledge = repo.value(binding["knowledge_ref"])
    worker = dict(binding, config=state["config"], calls=state["calls"])

    def checkpoint():
        for key in ("active_research", "active_manifest_ref"):
            if key in worker:
                binding[key] = worker[key]
        save()

    output = roles.output_tokens, roles.min_output_tokens
    try:
        await active.execute(repo, worker, checkpoint, roles, basis(base), knowledge, client)
    finally:
        roles.output_tokens, roles.min_output_tokens = output
    research_ref = repo.blob(
        dict(
            active_manifest_ref=worker["active_manifest_ref"],
            config=copy.deepcopy(state["config"]),
            calls=copy.deepcopy(state["calls"]),
        )
    )
    value = material(
        repo, base_ref, base, binding["knowledge_ref"], research_ref, datetime.now(UTC)
    )
    ref = repo.blob(value)
    snapshot = repo.snapshot(ref)
    binding["snapshot_ref"] = ref
    # Root transitive refs in run metadata for the existing backup/GC scanners.
    state["source_refs"] = sorted(
        set(state.get("source_refs", []))
        | {base_ref, research_ref}
        | set(
            re.findall(
                r"blob://sha256/[0-9a-f]{64}",
                json.dumps(
                    [knowledge, repo.value(research_ref), repo.value(worker["active_manifest_ref"])]
                ),
            )
        )
    )
    save()
    return ref, snapshot


def verify(repo, snapshot):
    base = repo.snapshot(snapshot.base_snapshot_ref)
    if base.schema_version != "next-session-v1":
        raise ValueError("增强快照必须绑定原始日预测快照")
    expected = material(
        repo,
        snapshot.base_snapshot_ref,
        base,
        snapshot.knowledge_ref,
        snapshot.research_state_ref,
        snapshot.evidence_sealed_at,
    )
    if expected != snapshot.model_dump(mode="json"):
        raise ValueError("增强快照、工具证据或模型上下文与封存输入不一致")
    knowledge = repo.value(snapshot.knowledge_ref)
    expected_knowledge = knowledge_snapshot(rules_for(repo), snapshot.requested_at, dynamic=True)
    if knowledge != expected_knowledge:
        raise ValueError("研究知识不是请求创建时已发布的固定版本")
