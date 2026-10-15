from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketpulse.quant.decision.api import router
from marketpulse.quant.decision.capabilities import source_audit


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_routes_expose_prediction_software_without_auto_activating_experiment():
    with client() as api:
        response = api.get("/api/quant/decision/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert data["stage"] == "P7"
        assert data["audit"]["kind"] == "REAL_SOURCE_PROBE_SNAPSHOT"
        assert data["features"]["single_evidence"]
        assert data["features"]["next_session"]
        assert data["features"]["industry_screening"]
        assert data["features"]["market_scan"]
        assert "非全市场" in data["features"]["market_scope"]
        assert data["features"]["refresh_versions"]
        assert len(data["candidate_business_profiles"]) == 2
        assert data["features"]["medium_term"]
        assert not data["experiment"]["enabled"]
        assert data["evaluation"]["executed"] == 0
        assert len(api.get("/api/quant/decision/methods").json()) == 8
        assert len(api.get("/api/quant/decision/evaluation-cases").json()) == 48
        schemas = api.get("/api/quant/decision/schema").json()["contracts"]
        assert {
            "EvidenceItem",
            "ForecastRecord",
            "NextSessionPrediction",
            "NextSessionOutcome",
            "ExperimentRunSlot",
            "ResearchRequest",
        } <= set(schemas)
        assert schemas["ResearchRequest"]["additionalProperties"] is False
        assert schemas["NextSessionRunRequest"]["additionalProperties"] is False


def test_missing_private_archives_on_another_host_are_explicit(tmp_path):
    data = source_audit(tmp_path)
    assert any(s["archive_status"] == "MISSING_ON_THIS_HOST" for s in data["sources"])


def test_corrupt_audit_is_a_conflict_not_a_success(monkeypatch):
    def corrupt(*args, **kwargs):
        raise ValueError("archive hash mismatch")

    monkeypatch.setattr("marketpulse.quant.decision.api.capabilities", corrupt)
    with client() as api:
        response = api.get("/api/quant/decision/capabilities")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "DECISION_AUDIT_INTEGRITY"


def test_versioned_case_registry_matches_frozen_test_materials():
    root = Path(__file__).resolve().parents[4]
    assert (root / "tests/fixtures/quant/decision/cases.json").read_bytes() == (
        root / "src/marketpulse/quant/decision/evaluation_cases.json"
    ).read_bytes()
