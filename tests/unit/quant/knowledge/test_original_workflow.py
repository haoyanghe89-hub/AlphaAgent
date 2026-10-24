"""Imported method paragraphs reach real workflow contracts, with synthetic models."""

# ruff: noqa: F811

import asyncio
import io
import zipfile
from threading import Event
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketpulse.quant.knowledge import original_search
from marketpulse.quant.knowledge.research_api import router
from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.research_service import run
from marketpulse.quant.knowledge.store import KnowledgeStore

from .test_active_workflow import ActivePort
from .test_original_search import ingest
from .test_research import SyntheticPort, knowledge, runtime  # noqa: F401


@pytest.mark.parametrize("enhanced", [False, True])
def test_new_studies_send_task_originals_to_analysis_review_and_export(
    runtime, monkeypatch, enhanced
):
    repo, packet, settings = runtime
    snapshot = knowledge()
    for item in snapshot["rules"]:
        item["rule"].update(
            system_rule="核对实际证据并保留适用条件。",
            horizons=["SWING", "MEDIUM"],
            required_inputs=["行情"],
            conditions=["同一截止时点。"],
            counterexamples=["趋势可能反复。"],
            forbidden_inferences=["不能保证收益。"],
            execution_status="REFERENCE_ONLY",
            validation_status="UNVALIDATED",
        )
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.research_service.knowledge_snapshot", lambda *a, **kw: snapshot
    )
    library = KnowledgeStore(repo.base.sessions, repo.base.blobs)
    doc = ingest(library)
    request = InvestmentRequest(
        request_key=uuid4(), entities=["000333"], enhanced_research=enhanced,
        analysis_focus=["CASHFLOW"],
        profile=dict(capital="987654.32", available_cash="987654.32"),
    )
    state, _ = repo.create(request.model_dump(mode="json"))
    assert state["config"]["original_search_version"] == original_search.VERSION
    port = ActivePort() if enhanced else SyntheticPort()
    result = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    assert result["status"] == "COMPLETED", result.get("error")
    sealed = repo.get(result["run_id"])
    material = sealed["data"]["original_research"]
    assert material["status"] == "AVAILABLE"
    assert material["passages"][0]["document_id"] == doc["document_id"]
    assert material["passages"][0]["start"] > doc["locators"][0]["end"]
    assert "cash" in material["passages"][0]["quote"].lower()
    for role, ctx in zip(port.roles, port.contexts, strict=True):
        if role in {"Planner", "Analyst", "Verifier"}:
            assert ctx["original_research"] == material
    assert "987654.32" not in str(port.contexts)
    assert doc["raw_ref"] in sealed["source_refs"]
    app = FastAPI()
    app.include_router(router)
    app.state.research_store = repo.base
    with TestClient(app) as client:
        url = "/api/quant/investment-research/runs/" + result["run_id"]
        assert client.get(url + "/audit").json()["public_context"]["original_research"] == material
        response = client.get(url + "/export")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            refs = (material["snapshot_ref"], doc["raw_ref"], doc["text_ref"], doc["manifest_ref"])
            for ref in refs:
                name = "blobs/" + ref.rsplit("/", 1)[1] + ".bin"
                assert archive.read(name) == repo.raw_bytes(ref)
    # Later imported text cannot change the original task or its approved role inputs.
    ingest(library, " Subsequently revised observations about costs and cash collection.")
    assert repo.get(result["run_id"])["data"]["original_research"] == material
