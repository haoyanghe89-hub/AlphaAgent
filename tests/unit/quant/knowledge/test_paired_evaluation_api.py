from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketpulse.quant.knowledge import paired_evaluation_api as api
from marketpulse.quant.knowledge.paired_evaluation import EvaluationStore

from .test_research import runtime  # noqa: F401


@pytest.fixture
def client(request, monkeypatch):
    repo, _, settings = request.getfixturevalue("runtime")
    app = FastAPI()
    app.include_router(api.router)
    app.state.research_store = repo.base
    app.state.settings = settings
    app.state.research_jobs = {}
    scheduled = []
    monkeypatch.setattr(api, "schedule", lambda request, state: scheduled.append(state["run_id"]))
    return TestClient(app), EvaluationStore(repo.base), scheduled


def test_public_local_create_idempotency_export_and_seal(client):
    browser, repo, scheduled = client
    root = "/api/quant/research-evaluation/runs"
    request = dict(request_key=str(uuid4()))
    created = browser.post(root, json=request)
    assert created.status_code == 202
    rid = created.json()["run_id"]
    assert len(scheduled) == 1
    assert browser.post(root, json=request).json()["run_id"] == rid
    assert len(scheduled) == 1
    assert browser.get(root + "/" + rid).json()["data"]["prediction"]["accuracy_delta"] is None
    assert browser.post(root + "/" + rid + "/seal").status_code == 409
    state = repo.get(rid, with_data=False)
    state.update(status="INTERRUPTED", error="SYNTHETIC external interruption")
    repo.save(state)
    sealed = browser.post(root + "/" + rid + "/seal")
    assert sealed.status_code == 200
    assert sealed.json()["data"]["round_summary"]["completed_studies"] == 0
    assert sealed.json()["data"]["round_summary"]["declared_studies"] == 4
    artifact = browser.get(root + "/" + rid + "/export")
    assert artifact.status_code == 200
    assert artifact.json()["sealed_report"]["prediction"]["accuracy_delta"] is None
    assert "attachment;" in artifact.headers["content-disposition"]
    assert browser.get(root).json()[0]["run_id"] == rid
    assert browser.post(root + "/" + rid + "/resume").status_code == 202
    assert len(scheduled) == 1  # Sealed trial never starts another model pass.


def test_private_fields_and_cross_origin_writes_rejected(client):
    browser, _, scheduled = client
    root = "/api/quant/research-evaluation/runs"
    request = dict(request_key=str(uuid4()))
    assert browser.post(root, json=dict(request, holdings=[100])).status_code == 422
    assert (
        browser.post(
            root, json=request, headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert scheduled == []


@pytest.mark.parametrize(
    "version",
    [
        "paired-public-research-v3",
        "paired-public-research-v4",
        "paired-public-research-v5",
        "paired-public-research-v6",
        "paired-public-research-v7",
    ],
)
def test_new_protocol_is_registered_before_calls_and_legacy_round_unchanged(client, version):
    browser, repo, scheduled = client
    root = "/api/quant/research-evaluation/runs"
    old = browser.post(root, json=dict(request_key=str(uuid4()))).json()
    old_manifest = repo.value(old["manifest_ref"])
    result = browser.post(root, json=dict(request_key=str(uuid4()), protocol_version=version))
    assert result.status_code == 202
    state = result.json()
    manifest = repo.value(state["manifest_ref"])
    assert state["calls"] == [] and len(scheduled) == 2
    assert manifest["version"] == version
    if version.endswith(("v6", "v7")):
        assert manifest["researcher_output_policy"]["total_output_limit"] == 6144
        assert all(
            c["request"]["input_reservation"] == "deepseek-v4-tokenizer-v1"
            for c in manifest["children"]
        )
    assert manifest["method_protocol"]["version"] == "investment-method-ablation-" + (
        "v6"
        if version.endswith("v7")
        else "v5"
        if version.endswith("v6")
        else version.rsplit("-", 1)[-1]
    )
    assert manifest["method_protocol"]["shared_evidence_review"] == (not version.endswith("v7"))
    assert manifest["method_protocol"]["independent_analysts"]
    assert manifest["method_protocol"]["budget"] == dict(
        max_model_calls=24, max_tokens=60000, max_seconds=900
    )
    assert all(
        c["request"]["execution_contract"] == "investment-structured-v2"
        for c in manifest["children"]
    )
    assert repo.value(old["manifest_ref"]) == old_manifest
    assert old_manifest["version"] == "paired-public-research-v1"
    assert all("execution_contract" not in c["request"] for c in old_manifest["children"])
