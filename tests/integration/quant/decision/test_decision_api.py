import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.contracts import canonical, digest
from marketpulse.quant.decision import api
from marketpulse.quant.decision.store import DecisionStore
from marketpulse.quant.decision.tools import SourceClient
from marketpulse.quant.live.store import ResearchRunRow, ResearchStore


@pytest.fixture
def setup(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "test.sqlite").as_posix(),
        connect_args={"check_same_thread": False},
    )
    ResearchRunRow.__table__.create(engine)
    old = ResearchStore(
        sessionmaker(engine), LocalContentAddressedBlobStorage(tmp_path / "blobs"), tmp_path / "old"
    )
    app = FastAPI()
    app.include_router(api.router)
    app.state.research_store, app.state.research_jobs = old, {}
    app.state.decision_store = DecisionStore(old)
    app.state.settings = SimpleNamespace(model="not-called", deepseek_api_key=None)
    yield app, old
    engine.dispose()


def payload():
    return dict(
        request_key=str(uuid4()), entity="中微公司", question="核查业务与现金回收", horizon_months=3
    )


def test_atomic_request_identity_history_and_public_field_rejection(setup, monkeypatch):
    app, old = setup
    scheduled = []
    monkeypatch.setattr(api, "schedule", lambda request, state: scheduled.append(state["run_id"]))
    p = payload()
    with TestClient(app) as c:
        a = c.post("/api/quant/decision/runs", json=p)
        assert a.status_code == 202
        rid = a.json()["run_id"]
        assert c.post("/api/quant/decision/runs", json=p).json()["run_id"] == rid
        assert (
            c.post("/api/quant/decision/runs", json=dict(p, question="改条件")).status_code == 409
        )
        assert c.get("/api/quant/decision/runs").json()[0]["run_id"] == rid
        assert c.get("/api/quant/decision/runs/RUN-DEC-" + "0" * 32).status_code == 404
        assert (
            c.post("/api/quant/decision/runs", json=dict(payload(), verified=True)).status_code
            == 422
        )
        assert (
            c.post("/api/quant/decision/runs", json=dict(payload(), mode="historical")).status_code
            == 422
        )
    assert len(scheduled) == 1
    assert old.list() == []  # New records never pollute old research history.


def test_completed_and_cancelled_records_cannot_be_overwritten(setup):
    app, old = setup
    repo = app.state.decision_store
    request = dict(payload(), cutoff_at=None)
    state, fresh = repo.create(request, "fp")
    assert fresh
    repo.cancel(state["run_id"])
    with pytest.raises(ValueError):
        repo.save(dict(state, status="COMPLETED"))
    assert repo.get(state["run_id"])["status"] == "CANCELLED"


def test_shared_active_job_limit_is_not_bypassed(setup):
    app, _ = setup
    app.state.research_jobs["old"] = SimpleNamespace(done=lambda: False)
    with TestClient(app) as c:
        assert c.post("/api/quant/decision/runs", json=payload()).status_code == 429


def test_retry_keeps_failed_record_and_is_idempotent_before_capacity(setup, monkeypatch):
    app, _ = setup
    repo = app.state.decision_store
    old, _ = repo.create(payload(), "old-code")
    old.update(status="FAILED", error="Planner failure", model_calls=4, charged_tokens=50936)
    repo.save(old)
    scheduled = []
    monkeypatch.setattr(api, "schedule", lambda request, state: scheduled.append(state["run_id"]))
    key = str(uuid4())
    url = f"/api/quant/decision/runs/{old['run_id']}/retry"
    with TestClient(app) as c:
        first = c.post(url, json={"request_key": key})
        assert first.status_code == 202, first.text
        value = first.json()
        assert value["recovery_of"] == old["run_id"] and value["model_calls"] == 0
        assert "charged_tokens" not in value
        app.state.research_jobs["busy"] = SimpleNamespace(done=lambda: False)
        assert c.post(url, json={"request_key": key}).json()["run_id"] == value["run_id"]
        assert c.post(url, json={"request_key": str(uuid4())}).status_code == 429
        assert c.post(url, json={"request_key": key, "verified": True}).status_code == 422
    assert len(scheduled) == 1 and repo.get(old["run_id"], with_data=False) == old


def test_broken_result_is_conflict_and_export_does_not_leak_partial_report(setup):
    app, old = setup
    repo = app.state.decision_store
    state, _ = repo.create(payload(), "fp")
    # Synthetic inconsistent artifact must fail closed.
    state.update(
        status="COMPLETED",
        result_ref=old.blob({"sources": [], "evidence": []}),
        result_sha256="0" * 64,
    )
    repo.save(state)
    with TestClient(app) as c:
        assert c.get(f"/api/quant/decision/runs/{state['run_id']}").status_code == 409
        assert c.get(f"/api/quant/decision/runs/{state['run_id']}/export").status_code == 409


@pytest.fixture
def fixed_sources(monkeypatch):
    """All company and market contents here are explicitly synthetic fixtures."""

    def fake_fetch(self, key, url, *, params=None, data=None, family, encoding=None, binary=False):
        self.check()
        if key == "security_search":
            value = 'v_hint="sh~688012~合成公司~test~GP-A-KCB";'
        elif key == "profile":
            value = {
                "jbzl": [
                    dict(
                        SECURITY_CODE="688012",
                        SECURITY_NAME_ABBR="合成公司",
                        EM2016="合成行业",
                        SECURITY_TYPE="A股",
                    )
                ]
            }
        elif key == "disclosure_issuers":
            value = {"stockList": [dict(code="688012", orgId="123")]}
        elif key.startswith("catalog_"):
            category = key.split("_")[1]
            titles = {
                "periodic": ["2026年半年度报告", "2025年年度报告"],
                "earnings": ["2026年业绩预告"],
                "investor": ["投资者关系活动记录表"],
            }
            rows = [
                dict(
                    secCode="688012",
                    announcementId=str(
                        i + 1 + {"periodic": 10, "earnings": 20, "investor": 30}[category]
                    ),
                    announcementTitle=t,
                    adjunctUrl=(
                        "finalpage/2026-08-20/"
                        + str(i + 1 + {"periodic": 10, "earnings": 20, "investor": 30}[category])
                        + ".PDF"
                    ),
                    announcementTime=1787174400000,
                )
                for i, t in enumerate(titles[category])
            ]
            value = dict(announcements=rows, totalAnnouncement=len(rows))
        elif key.startswith("pdf_"):
            value = b"%PDF-1.4 synthetic " + key.encode()
        elif key == "daily":
            value = {
                "data": {
                    "sh688012": {
                        "day": [["2026-10-09", "100.00", "101.00", "102.00", "99.00", "12"]]
                    }
                }
            }
        elif key == "margin":
            value = {
                "result": {
                    "data": [
                        dict(SCODE="688012", DATE="2026-10-09", RZYE=1000, RZMRE=200, RZCHE=150)
                    ]
                }
            }
        else:
            raise AssertionError(key)
        raw = (
            value if binary else value.encode("gb18030") if encoding else canonical(value).encode()
        )
        ref = self.repo.raw(raw)
        source = dict(
            key=key,
            url=url,
            source_family=family,
            blob_ref=ref,
            sha256=BlobRef.from_uri(ref).sha256,
            fetched_at=datetime.now(UTC).isoformat(),
            byte_count=len(raw),
        )
        self.state["calls"].append(
            dict(tool=key, status="COMPLETED", response_ref=ref, source=source)
        )
        self.save()
        return source, value

    class Page:
        def extract_text(self):
            return (
                "合成公司 688012 2026年 2025年 公司业务与产品需求变化，"
                "收入及经营现金回收情况仍需验证。未经审计。"
            )

    class Reader:
        def __init__(self, raw):
            self.pages = [Page() for _ in range(4)]
            self.is_encrypted = False

    monkeypatch.setattr(SourceClient, "fetch", fake_fetch)
    monkeypatch.setattr("marketpulse.quant.decision.evidence.PdfReader", Reader)


def await_terminal(c, rid):
    for _ in range(100):
        response = c.get("/api/quant/decision/runs/" + rid)
        assert response.status_code == 200, response.text
        if response.json()["status"] != "RUNNING":
            return response.json()
        time.sleep(0.02)
    raise AssertionError("fixture workflow did not finish")


def test_actual_workflow_freezes_readable_evidence_and_exports_without_model(setup, fixed_sources):
    app, old = setup
    p = payload()
    with TestClient(app) as c:
        rid = c.post("/api/quant/decision/runs", json=p).json()["run_id"]
        state = await_terminal(c, rid)
        assert state["status"] == "COMPLETED", state.get("error")
        assert state["model_calls"] == 0
        assert len(state["data"]["documents"]) == 4
        assert state["data"]["bundle"]["coverage"]["available"] == 5
        assert state["data"]["report"]["model_execution"] == "NOT_RUN_IN_P1"
        eid = next(
            e["evidence_id"] for e in state["data"]["bundle"]["items"] if e["kind"] == "disclosure"
        )
        assert c.get(f"/api/quant/decision/runs/{rid}/evidence/{eid}").status_code == 200
        assert c.get(f"/api/quant/decision/runs/{rid}/evidence/not-in-this-run").status_code == 404
        assert c.get(f"/api/quant/decision/runs/{rid}/export").json()["data"] == state["data"]
        assert c.post("/api/quant/decision/runs", json=p).json()["data"] == state["data"]
        historical = dict(payload(), mode="historical", cutoff_at="2026-09-01T00:00:00+08:00")
        past = await_terminal(
            c, c.post("/api/quant/decision/runs", json=historical).json()["run_id"]
        )
        assert past["status"] == "COMPLETED"
        assert past["data"]["bundle"]["items"] == []
        assert past["data"]["report"]["research_status"] == "INSUFFICIENT"
    assert old.list() == []


def test_retry_reuses_verified_materials_and_refuses_corrupt_input(
    setup, fixed_sources, monkeypatch
):
    from threading import Event

    from marketpulse.quant.decision.contracts import ResearchRequest
    from marketpulse.quant.decision.service import collect

    app, _ = setup
    repo = app.state.decision_store
    definition = ResearchRequest(**dict(payload(), research_mode="medium_term")).model_dump(
        mode="json"
    )
    state, _ = repo.create(definition, "old-implementation")
    collect(repo, state, Event(), publish=False)
    state.update(status="FAILED", error="Planner unavailable")
    repo.save(state)
    monkeypatch.setattr(api, "schedule", lambda *a: None)
    with TestClient(app) as c:
        old = c.get(f"/api/quant/decision/runs/{state['run_id']}")
        assert old.status_code == 200 and old.json()["available_materials"]["disclosures"]
        child = c.post(
            f"/api/quant/decision/runs/{state['run_id']}/retry", json={"request_key": str(uuid4())}
        )
        assert child.status_code == 202, child.text
        value = child.json()
        assert (
            value["evidence_ref"] == state["evidence_ref"] and value["subject"] == state["subject"]
        )
        assert value["model_calls"] == 0 and value["calls"] == state["calls"]
        bad = repo.base.blobs._path(BlobRef.from_uri(state["evidence_ref"]))
        bad.chmod(0o600)
        bad.write_bytes(b"corrupt")
        assert c.get(f"/api/quant/decision/runs/{state['run_id']}").status_code == 409
        key = str(uuid4())
        assert (
            c.post(
                f"/api/quant/decision/runs/{state['run_id']}/retry", json={"request_key": key}
            ).status_code
            == 409
        )
        assert not repo.exists("RUN-DEC-" + key.replace("-", ""))


def test_valid_hash_does_not_make_a_forged_pdf_page_valid(setup, fixed_sources):
    app, old = setup
    with TestClient(app) as c:
        rid = c.post("/api/quant/decision/runs", json=payload()).json()["run_id"]
        state = await_terminal(c, rid)
        data = state.pop("data")
        next(e for e in data["bundle"]["items"] if e["kind"] == "disclosure")["locator"]["page"] = (
            999
        )
        next(e for e in data["evidence"] if e["kind"] == "disclosure")["locator"]["page"] = 999
        state.update(result_ref=old.blob(data), result_sha256=digest(data))
        with old.sessions.begin() as session:
            session.get(ResearchRunRow, rid).payload = state
        assert c.get(f"/api/quant/decision/runs/{rid}").status_code == 409
        assert c.get(f"/api/quant/decision/runs/{rid}/export").status_code == 409
