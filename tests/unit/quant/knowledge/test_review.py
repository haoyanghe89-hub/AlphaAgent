from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from marketpulse.quant.knowledge.api import router
from marketpulse.quant.knowledge.rule_retrieval import retrieve

from .conftest import reviewed


def test_two_review_roles_are_required_and_rejection_cannot_publish(rules, candidate):
    with pytest.raises(ValueError, match="TWO_ROLE_REVIEWS_REQUIRED"):
        reviewed(rules, candidate, omit_role=True)
    draft, review = reviewed(rules, candidate, reject=True)
    assert review["verdict"] == "REJECT"
    with pytest.raises(ValueError, match="APPROVED_EXACT_VERSION_REQUIRED"):
        rules.publish(draft["rule_id"], draft["version"], review["review_id"])
    assert not rules.current()


def test_publication_is_reference_only_and_review_does_not_prove_effect(rules, candidate):
    draft, review = reviewed(rules, candidate)
    assert rules.rule(draft["rule_id"], draft["version"])["status"] == "REVIEWED"
    result = rules.publish(draft["rule_id"], draft["version"], review["review_id"])
    assert result["status"] == "PUBLISHED"
    assert result["validation_status"] == result["rule"]["validation_status"] == "UNVALIDATED"
    assert review["external_verification"] is False
    assert rules.publish(draft["rule_id"], draft["version"], review["review_id"]) == result


def test_retired_rules_are_excluded_without_destroying_historical_retrieval(rules, candidate):
    draft, review = reviewed(rules, candidate)
    rules.publish(draft["rule_id"], draft["version"], review["review_id"])
    cutoff = datetime.now(UTC)
    rules.retire(draft["rule_id"], draft["version"], "合成测试发现适用前提发生变化，撤回当前版本。")
    assert not rules.current()
    assert rules.current(cutoff)[0]["status"] == "PUBLISHED"
    assert {e["action"] for e in rules.current(cutoff)[0]["events"]} == {"PUBLISHED"}
    assert not retrieve(rules, "资本回报")["items"]
    with pytest.raises(ValueError, match="RETIRED_VERSION_CANNOT_REPUBLISH"):
        rules.publish(draft["rule_id"], draft["version"], review["review_id"])


def test_retrieval_respects_scope_time_aliases_and_families(rules, candidate):
    from .test_distillation import changed

    for rule in (
        candidate,
        changed(candidate, rule_id="KR-SYNTHETIC-OTHER", title="测试：其他资本回报条件"),
    ):
        draft, review = reviewed(rules, rule)
        rules.publish(draft["rule_id"], draft["version"], review["review_id"])
    assert len(retrieve(rules, "ROIC")["items"]) == 1
    assert len(retrieve(rules, "ROIC", diversify=False)["items"]) == 2
    assert not retrieve(rules, "ROIC", horizon="NEXT_SESSION")["items"]
    assert not retrieve(rules, "ROIC", cutoff=datetime.now(UTC) - timedelta(days=1))["items"]
    assert not retrieve(rules, "不存在的方法")["items"]
    with pytest.raises(ValueError):
        retrieve(rules, "资本回报", limit=13)


def test_unreviewed_wrong_version_and_unknown_review_cannot_publish(rules, candidate):
    from .test_distillation import changed

    draft, review = reviewed(rules, candidate)
    newer = rules.draft(changed(candidate, title="测试：新版资本回报规则"))
    with pytest.raises(ValueError, match="APPROVED_EXACT_VERSION_REQUIRED"):
        rules.publish(newer["rule_id"], newer["version"], review["review_id"])
    with pytest.raises(KeyError):
        rules.publish(draft["rule_id"], draft["version"], "0" * 64)


@pytest.mark.parametrize("table", ["rule", "review", "publication", "audit"])
def test_rule_review_and_publication_rows_cannot_be_mutated(rules, candidate, table):
    draft, review = reviewed(rules, candidate)
    rules.publish(draft["rule_id"], draft["version"], review["review_id"])
    for sql in (
        f"DELETE FROM inv_quant_knowledge_{table}",
        f"UPDATE inv_quant_knowledge_{table} SET created_at='bad'",
    ):
        with pytest.raises(Exception, match="knowledge record is immutable"):
            with rules.sessions.begin() as session:
                session.execute(text(sql))


def test_audit_blobs_are_gc_roots_and_corruption_blocks_reads(rules, candidate):
    from sqlalchemy import select

    from marketpulse.infrastructure.storage.models import BlobIntegrityError, BlobRef
    from marketpulse.investigation.deletion import _refs
    from marketpulse.quant.knowledge.models import KnowledgeAuditRow

    draft, review = reviewed(rules, candidate)
    rules.publish(draft["rule_id"], draft["version"], review["review_id"])
    with rules.sessions() as session:
        row = session.scalar(select(KnowledgeAuditRow))
    state = rules.audit(row.audit_id)
    response_ref = state["calls"][0]["response_ref"]
    assert response_ref in _refs(row.blob_refs_json)
    rules.library.blobs._path(BlobRef.from_uri(response_ref)).write_bytes(b"TEST CORRUPTION")
    with pytest.raises(BlobIntegrityError):
        rules.current()


def test_presentation_flag_never_authorizes_http_rule_publication(rules, candidate):
    app = FastAPI()
    app.state.knowledge_store = rules.library
    app.state.knowledge_rules = rules
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(
            "/api/quant/knowledge/rules/publish?developer=1", json={"rule_id": candidate.rule_id}
        )
        assert response.status_code in (404, 405)


def test_read_only_rule_api_uses_exact_versions_and_time_scope(rules, candidate):
    draft, review = reviewed(rules, candidate)
    rules.publish(draft["rule_id"], draft["version"], review["review_id"])
    app = FastAPI()
    app.state.knowledge_store = rules.library
    app.state.knowledge_rules = rules
    app.include_router(router)
    with TestClient(app) as client:
        root = "/api/quant/knowledge"
        catalog = client.get(root + "/rules").json()
        assert catalog["rules_published"] == 1 and catalog["routes_executable"] == 0
        fixed = root + f"/rules/{draft['rule_id']}/versions/{draft['version']}"
        assert client.get(fixed).json()["rule"] == draft["rule"]
        assert client.get(root + "/reviews/" + review["review_id"]).json()["verdict"] == "APPROVE"
        assert client.get(root + "/rules/search", params={"q": "ROIC"}).json()["items"]
        assert not client.get(
            root + "/rules/search", params={"q": "ROIC", "horizon": "NEXT_SESSION"}
        ).json()["items"]
        assert (
            client.get(root + "/rules/search", params={"q": "ROIC", "limit": 13}).status_code == 422
        )
        assert client.get(fixed.replace(draft["version"], "0" * 64)).status_code == 404
        assert client.post(fixed + "?developer=1", json={}).status_code == 405
