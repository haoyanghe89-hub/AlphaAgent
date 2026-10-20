from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr
from pytest import MonkeyPatch

from marketpulse.config import Settings
from marketpulse.investigation.api import ReplayCaseOut
from marketpulse.investigation.case_replay import RECORDING_RUN_ID
from marketpulse.investigation.server import create_app


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=SecretStr(f"sqlite:///{(tmp_path / 'console.db').as_posix()}"),
        review_allowed_origins=("http://testserver",),
        review_allow_insecure_loopback=True,
    )


def test_investigation_server_mounts_console_and_review_routes(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path))

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok",
            "service": "search-report-investigation",
            "database": "ok",
        }

        created = client.post(
            "/api/investigations",
            json={
                "title": "Public event",
                "event_description": "A public event under investigation.",
                "investigation_goal": "Establish what happened.",
                "questions": ["What happened?"],
            },
        )
        assert created.status_code == 201
        investigation_id = created.json()["investigation_id"]

        listed = client.get("/api/investigations")
        assert listed.status_code == 200
        assert [item["investigation_id"] for item in listed.json()] == [investigation_id]

        # Mounted but unauthenticated: the review route must not be a 404.
        review_me = client.get("/api/review/me")
        assert review_me.status_code == 401


def test_server_does_not_require_a_model_credential(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert settings.deepseek_api_key is None

    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/api/health").status_code == 200


def test_frontend_entry_revalidates_after_a_build(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from fastapi.staticfiles import StaticFiles

    from marketpulse.investigation import server

    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>first build</html>", encoding="utf-8")
    monkeypatch.setenv("INVESTIGATION_SERVE_FRONTEND", "true")
    monkeypatch.setattr(
        server, "StaticFiles", lambda **_: StaticFiles(directory=frontend, html=True)
    )
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        first = client.get("/")
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-cache"
        assert client.get("/index.html").headers["cache-control"] == "no-cache"
        assert client.get("/", headers={"If-None-Match": first.headers["etag"]}).status_code == 304
        (frontend / "index.html").write_text(
            "<html>second build with new scripts</html>", encoding="utf-8"
        )
        second = client.get("/", headers={"If-None-Match": first.headers["etag"]})
        assert second.status_code == 200 and "second build" in second.text
        assert second.headers["cache-control"] == "no-cache"


class _ReplayFixture:
    async def run(self) -> ReplayCaseOut:
        return ReplayCaseOut(
            case_id="east-palestine-2023",
            investigation_id="INV-DEMO",
            run_id="RUN-DEMO",
            report_id="REPORT-DEMO",
            run_status="READY_FOR_REPORT",
            release_status="REVIEW_REQUIRED",
        )


def test_built_in_case_replay_endpoint_delegates_to_runner(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), east_palestine_replay=_ReplayFixture())

    with TestClient(app) as client:
        cases = client.get("/api/cases")
        assert cases.status_code == 200
        assert cases.json()[0]["case_id"] == "east-palestine-2023"
        assert cases.json()[0]["replay_ready"] is True

        replay = client.post("/api/cases/east-palestine-2023/replay")
        assert replay.status_code == 202
        assert replay.json()["run_id"] == "RUN-DEMO"


def test_bundled_case_runs_end_to_end_from_replay_to_governed_report(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    case_root = Path(__file__).resolve().parents[3] / "case_data" / "east_palestine_2023"
    monkeypatch.setenv("EAST_PALESTINE_CASE_ROOT", str(case_root))
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))

    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        replay_response = client.post("/api/cases/east-palestine-2023/replay")
        assert replay_response.status_code == 202
        replay = replay_response.json()
        assert replay["run_status"] == "READY_FOR_REPORT"
        assert replay["release_status"] == "REVIEW_REQUIRED"

        run_id = replay["run_id"]
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        assert run["mode"] == "REPLAY"
        assert run["execution_provenance"] == "CURATED_OFFLINE"
        assert run["origin_run_id"] == RECORDING_RUN_ID
        assert "CURATED" in RECORDING_RUN_ID

        sources = client.get(f"/api/runs/{run_id}/sources").json()
        assert len(sources) >= 10
        assert sum(source["is_official"] for source in sources) >= 3
        assert len({source["source_type"] for source in sources}) >= 3
        secondary = [
            source
            for source in sources
            if not source["is_official"] and not source["is_first_hand"]
        ]
        assert len({source["publisher"] for source in secondary}) >= 3
        assert not any("ASPECT response" in source["title"] for source in sources)

        claims = client.get(f"/api/runs/{run_id}/claims").json()
        assert claims
        assert all(claim["latest_validation_id"] for claim in claims)
        assert all(claim["supporting_evidence_ids"] for claim in claims)
        assert all(len(claim["supporting_evidence_ids"]) == 1 for claim in claims)
        assert any("NTSB identified an overheated" in claim["statement"] for claim in claims)
        assert any("16:37" in claim["statement"] for claim in claims)

        conflicts = client.get(f"/api/runs/{run_id}/conflicts").json()
        assert any(
            conflict["status"] == "RESOLVED"
            and conflict["resolution_status"] == "RESOLVED_WITH_SCOPE"
            for conflict in conflicts
        )

        report_id = replay["report_id"]
        report = client.get(f"/api/reports/{report_id}").json()
        assert report["report"]["report_type"] == "FULL_INVESTIGATION"
        assert len(report["sections"]) < 17
        assert all(section["content"]["units"] for section in report["sections"])
        sections = {section["section_type"]: section["content"] for section in report["sections"]}
        assert sections["CORE_FINDINGS"]["units"]
        assert "CONCLUSIONS_AND_NEXT_STEPS" not in sections
        assert "NEXT_STEPS" not in sections
        assert "人工整理" in str(sections["RESEARCH_APPENDIX"]["units"])
        detail = client.get(f"/api/investigations/{replay['investigation_id']}").json()
        assert len(detail["questions"]) == 8

        citations = client.get(f"/api/reports/{report_id}/citations").json()
        assert citations
        citation = client.get(f"/api/citations/{citations[0]['citation_id']}").json()
        assert citation["claim"] is not None
        assert citation["evidence"]["exact_quote"]
        assert citation["source"] is not None
        # Evidence.content is already the exact slice. A non-zero locator must not
        # slice it again, nor return the old whole-document excerpt.
        assert citation["evidence"]["locator_payload"]["start"] > 0
        assert len(citation["evidence"]["exact_quote"]) < 1500
        snapshot = client.get(
            f"/api/snapshots/{citation['evidence']['snapshot_id']}?format=cleaned"
        )
        assert snapshot.status_code == 200
        assert citation["evidence"]["exact_quote"] in snapshot.text
        exported = client.get(f"/api/reports/{report_id}/export?format=markdown")
        assert exported.status_code == 200
        assert citation["source"]["canonical_url"] in exported.text
        assert citation["evidence"]["exact_quote"].splitlines()[0] in exported.text
        assert "待人工审核" in exported.text
        assert "## 结论速览" in exported.text
        assert "原文" in exported.text

        review = client.get(f"/api/reports/{report_id}/review").json()
        assert review["evaluation"]["hard_finding_count"] == 0
        assert review["pending_request"] is not None

        regenerated = client.post(
            f"/api/runs/{run_id}/reports", json={"report_type": "FULL_INVESTIGATION"}
        )
        assert regenerated.status_code == 201
        assert regenerated.json()["report"]["report_id"] != report_id
        assert client.get(f"/api/reports/{report_id}").json() == report
        assert len(sections["EXECUTIVE_SUMMARY"]["units"]) >= 4

        repeated = client.post("/api/cases/east-palestine-2023/replay")
        assert repeated.status_code == 202
        second = repeated.json()
        assert second["run_id"] != run_id
        second_evidence = {
            item["evidence_id"]
            for item in client.get(f"/api/runs/{second['run_id']}/evidence").json()
        }
        second_citations = client.get(f"/api/reports/{second['report_id']}/citations").json()
        assert all(item["evidence_id"] in second_evidence for item in second_citations)
