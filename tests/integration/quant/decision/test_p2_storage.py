"""P2 schema adoption and legacy archive/shared-blob regression."""

from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.investigation.deletion import delete_archive
from marketpulse.investigation.persistence.base import (
    create_investigation_engine,
    create_session_factory,
)
from marketpulse.investigation.persistence.models import InvestigationRow
from marketpulse.quant.decision.forecasts import ForecastRow


def test_migration_adopts_existing_forecast_and_archive_retains_shared_blob(tmp_path):
    url = "sqlite:///" + (tmp_path / "p2.db").as_posix()
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "20261010_10")
    engine = create_investigation_engine(url)
    sessions = create_session_factory(engine)
    blobs = LocalContentAddressedBlobStorage(tmp_path / "blobs")
    saved = blobs.put_bytes(b"synthetic shared original")
    # Simulate an early P2 development database; migration must adopt, not rebuild.
    ForecastRow.__table__.create(engine)
    payload = {"original": saved.ref.uri}
    with sessions.begin() as session:
        session.add(
            ForecastRow(
                forecast_id="synthetic-forecast",
                run_id="synthetic-p2-run",
                content_hash="a" * 64,
                record_ref=saved.ref.uri,
                report_ref=saved.ref.uri,
                payload=payload,
            )
        )
        now = datetime.now(UTC)
        session.add(
            InvestigationRow(
                investigation_id="synthetic-archive",
                title="synthetic archive",
                event_description="synthetic",
                investigation_goal="synthetic",
                scope={"original": saved.ref.uri},
                created_at=now,
                updated_at=now,
            )
        )
    command.upgrade(config, "head")
    assert {
        "inv_quant_decision_forecast",
        "inv_quant_decision_observation",
    } <= set(inspect(engine).get_table_names())
    outcome = delete_archive(sessions, "synthetic-archive", blobs)
    assert outcome["deleted"] and outcome["deleted_blobs"] == 0
    with sessions() as session:
        row = session.scalar(select(ForecastRow))
        assert row.payload == payload and row.content_hash == "a" * 64
    assert blobs.get_bytes(saved.ref) == b"synthetic shared original"
    engine.dispose()
