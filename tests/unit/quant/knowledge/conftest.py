"""Synthetic rule fixtures: no calls to real models or user databases."""

from datetime import UTC, datetime

import pytest

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.investigation.persistence.base import (
    create_investigation_engine,
    create_session_factory,
)
from marketpulse.investigation.server import _upgrade_database
from marketpulse.quant.knowledge.contracts import SourceDefinition
from marketpulse.quant.knowledge.review import PROMPT_VERSION, PROMPTS
from marketpulse.quant.knowledge.rule_store import RuleStore
from marketpulse.quant.knowledge.rules import Citation, MethodRule, ReviewBatch
from marketpulse.quant.knowledge.store import KnowledgeStore


@pytest.fixture
def rules(tmp_path):
    url = "sqlite:///" + (tmp_path / "rules.sqlite").as_posix()
    _upgrade_database(url)
    engine = create_investigation_engine(url)
    store = RuleStore(
        KnowledgeStore(
            create_session_factory(engine), LocalContentAddressedBlobStorage(tmp_path / "blobs")
        )
    )
    yield store
    engine.dispose()


@pytest.fixture
def candidate(rules):
    source = SourceDefinition(
        source_id="SYNTHETIC-RULE-SOURCE",
        title="合成原文测试资料",
        authors=("SYNTHETIC",),
        source_family="SYNTHETIC-FAMILY",
        category="测试",
        tags=("测试",),
        url="https://www.berkshirehathaway.com/letters/1992.html",
        selector="main",
        anchors=("capital return", "cash collection"),
        reading_note="测试合成正文，不是实际作者观点，也不代表真实市场研究。",
        publication_label="TEST ONLY",
    )
    body = "TEST ONLY. Capital return and cash collection must be examined together. " * 10
    body += "Source market uses a 25% threshold only as a synthetic fixture. " * 3
    doc = rules.library.ingest_document(
        source, ("<main>" + body + "</main>").encode(), datetime.now(UTC)
    )
    body = rules.library.blobs.get_bytes(BlobRef.from_uri(doc["text_ref"])).decode()
    return MethodRule(
        rule_id="KR-SYNTHETIC-CAPITAL",
        title="测试：资本回报需核查现金回收",
        kind="筛选",
        source_claim="合成原文将资本回报和现金回收列为共同检查事项。",
        system_rule="核查资本回报并同时检查现金回收，缺少真实输入时保留数据需求。",
        adaptation="合成测试用改写，不代表真实投资方法或任何市场效果。",
        horizons=("MEDIUM",),
        required_inputs=("资本回报", "现金回收"),
        conditions=("必须有同时点和统一口径的真实财务输入。",),
        steps=("核查真实财务输入与现金回收差异。",),
        counterexamples=("一次性会计收益可能提高利润但未形成现金流入。",),
        forbidden_inferences=("不能据此推断下一交易日方向或保证盈利。",),
        keywords=("资本回报", "现金回收"),
        conflict_group="测试基本面",
        citations=(
            Citation(
                document_id=doc["document_id"],
                version=doc["version"],
                start=0,
                end=len(body),
                quote=body,
            ),
        ),
    )


def reviewed(rules, candidate, *, reject=False, omit_role=False):
    draft = rules.draft(candidate)
    state = dict(
        config=dict(prompt_version=PROMPT_VERSION),
        model_calls=2,
        charged_tokens=50,
        elapsed_seconds=1,
        calls=[],
    )
    for role in list(PROMPTS)[: 1 if omit_role else 2]:
        request = dict(
            instructions=PROMPTS[role],
            prompt_version=PROMPT_VERSION,
            schema=ReviewBatch.model_json_schema(),
            context=dict(rules=[dict(version=draft["version"], rule=draft["rule"])]),
        )
        response = dict(
            reviews=[
                dict(
                    rule_id=candidate.rule_id,
                    version=draft["version"],
                    verdict="REJECT" if reject else "APPROVE",
                    supported=not reject,
                    parameters_supported=True,
                    conditions_complete=True,
                    counterexamples_useful=True,
                    reason="合成审核测试；实际模型未调用，不是生产审核记录。",
                )
            ]
        )
        state["calls"].append(
            dict(
                role=role,
                status="COMPLETED",
                request_ref=rules.blob(request),
                response_ref=rules.blob(response),
            )
        )
    audit_id = rules.archive_audit(state)
    review = rules.record_review(candidate.rule_id, draft["version"], audit_id)
    return draft, review
