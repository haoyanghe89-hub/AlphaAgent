from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

EXPECTED_TABLES = {
    "inv_audit_events",
    "inv_citations",
    "inv_claim_evidence_relations",
    "inv_claims",
    "inv_call_bindings",
    "inv_conflict_claims",
    "inv_conflict_evidence",
    "inv_conflict_sets",
    "inv_document_artifacts",
    "inv_evidence",
    "inv_execution_steps",
    "inv_investigation_questions",
    "inv_investigations",
    "inv_recorded_human_review_decisions",
    "inv_recorded_model_calls",
    "inv_recorded_tool_calls",
    "inv_release_policy_evaluations",
    "inv_report_input_snapshots",
    "inv_report_projections",
    "inv_report_section_claims",
    "inv_report_sections",
    "inv_report_validation_findings",
    "inv_reports",
    "inv_research_gaps",
    "inv_research_tasks",
    "inv_review_decisions",
    "inv_review_idempotency",
    "inv_review_requests",
    "inv_review_research_requests",
    "inv_reviewer_auth_state",
    "inv_reviewer_rate_buckets",
    "inv_reviewer_sessions",
    "inv_runs",
    "inv_run_budgets",
    "inv_semantic_judgments",
    "inv_source_families",
    "inv_source_family_members",
    "inv_source_snapshots",
    "inv_sources",
    "inv_timeline_events",
    "inv_timeline_evidence",
    "inv_validation_conflicts",
    "inv_validation_results",
}


def _config(database_url: str) -> Config:
    root = Path(__file__).resolve().parents[3]
    config = Config(root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["database_url"] = database_url
    return config


def test_upgrade_and_downgrade_preserve_legacy_schema(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    url = f"sqlite:///{database.as_posix()}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE mp_runs (run_id VARCHAR(100) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO mp_runs (run_id) VALUES ('legacy-run')"))
    engine.dispose()

    config = _config(url)
    # The original business schema remains reversible. Knowledge migrations
    # deliberately refuse destructive downgrades and are tested separately.
    command.upgrade(config, "20261012_11")

    engine = create_engine(url)
    inspector = inspect(engine)
    assert EXPECTED_TABLES <= set(inspector.get_table_names())
    evidence_fks = inspector.get_foreign_keys("inv_evidence")
    assert any(fk["referred_table"] == "inv_source_snapshots" for fk in evidence_fks)
    relation_uniques = inspector.get_unique_constraints("inv_claim_evidence_relations")
    assert any(
        set(item["column_names"]) == {"claim_id", "evidence_id"} for item in relation_uniques
    )
    assert "latest_validation_id" in {item["name"] for item in inspector.get_columns("inv_claims")}
    validation_indexes = {item["name"] for item in inspector.get_indexes("inv_validation_results")}
    assert {
        "ix_inv_validation_input_fingerprint",
        "ix_inv_validation_evidence_set_hash",
        "ix_inv_validation_policy",
    } <= validation_indexes
    assert {"max_sources", "sources_used"} <= {
        item["name"] for item in inspector.get_columns("inv_run_budgets")
    }
    assert {
        "target_claim_id",
        "origin_gap_id",
        "parent_task_id",
        "purpose",
        "preferred_source_types",
        "suggested_queries",
        "round",
    } <= {item["name"] for item in inspector.get_columns("inv_research_tasks")}
    task_fk_tables = {
        item["referred_table"] for item in inspector.get_foreign_keys("inv_research_tasks")
    }
    assert {
        "inv_claims",
        "inv_research_gaps",
        "inv_research_tasks",
    } <= task_fk_tables
    assert {"created_by_step_id", "research_task_id"} <= {
        item["name"] for item in inspector.get_columns("inv_evidence")
    }
    assert {"created_by_step_id", "research_task_id"} <= {
        item["name"] for item in inspector.get_columns("inv_claims")
    }
    assert "origin_validation_id" in {
        item["name"] for item in inspector.get_columns("inv_research_gaps")
    }
    assert {
        "ix_inv_tasks_origin_gap",
        "ix_inv_tasks_target_claim",
    } <= {item["name"] for item in inspector.get_indexes("inv_research_tasks")}
    report_columns = {item["name"] for item in inspector.get_columns("inv_reports")}
    assert {
        "report_input_snapshot_hash",
        "citation_set_hash",
        "schema_version",
    } <= report_columns
    assert not {"review_status", "release_status", "updated_at"} & report_columns
    assert {"origin_run_id", "origin_review_request_id"} <= {
        item["name"] for item in inspector.get_columns("inv_runs")
    }
    assert {
        "review_request_id",
        "reviewer_session_public_id",
        "reviewer_config_fingerprint",
        "decision_origin",
    } <= {item["name"] for item in inspector.get_columns("inv_review_decisions")}
    snapshot_uniques = inspector.get_unique_constraints("inv_report_input_snapshots")
    assert any(item["column_names"] == ["snapshot_hash"] for item in snapshot_uniques)
    citation_fks = {item["referred_table"] for item in inspector.get_foreign_keys("inv_citations")}
    assert {"inv_reports", "inv_claims", "inv_evidence"} <= citation_fks
    family_member_fks = inspector.get_foreign_keys("inv_source_family_members")
    assert {item["referred_table"] for item in family_member_fks} == {
        "inv_source_families",
        "inv_sources",
    }
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT run_id FROM mp_runs")) == "legacy-run"
    engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(url)
    remaining = set(inspect(engine).get_table_names())
    assert "mp_runs" in remaining
    assert not (EXPECTED_TABLES & remaining)
    engine.dispose()


def test_knowledge_head_preserves_legacy_and_refuses_destructive_downgrade(tmp_path):
    url = "sqlite:///" + (tmp_path / "knowledge-migration.db").as_posix()
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE mp_runs (run_id VARCHAR(100) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO mp_runs VALUES ('legacy-run')"))
    config = _config(url)
    command.upgrade(config, "head")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables
    assert {
        "inv_quant_knowledge_document",
        "inv_quant_knowledge_rule",
        "inv_quant_knowledge_supplement",
        "inv_quant_knowledge_supplement_decision",
    } <= tables
    assert {
        fk["referred_table"]
        for fk in inspect(engine).get_foreign_keys("inv_quant_knowledge_supplement_decision")
    } == {"inv_quant_knowledge_supplement"}
    with pytest.raises(RuntimeError, match="immutable"):
        command.downgrade(config, "base")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT run_id FROM mp_runs")) == "legacy-run"
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261016_15"
    assert tables == set(inspect(engine).get_table_names())
    engine.dispose()
