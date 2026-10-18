import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from marketpulse.investigation.operations import local_backup as local
from marketpulse.investigation.operations.backup import BackupError, drill

ZONE = ZoneInfo("Asia/Shanghai")


def source(root: Path) -> Path:
    data = root / "live"
    raw = b'{"history":"retained"}'
    digest = hashlib.sha256(raw).hexdigest()
    target = data / "blobs/sha256" / digest[:2] / digest[2:4] / digest
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    db = sqlite3.connect(data / "metadata.sqlite")
    db.execute("create table history (value text)")
    db.execute("insert into history values (?)", ("blob://sha256/" + digest,))
    db.commit()
    db.close()
    (root / "alphaagent.local.json").write_text(json.dumps({"data": "live", "port": 8839}))
    (root / ".env").write_text("EXAMPLE_LOCAL_SECRET=not-a-real-key")
    (data / "knowledge-release.json").write_text('{"version":1}')
    (data / "calls").mkdir()
    (data / "calls/intent.json").write_text('{"status":"UNKNOWN"}')
    legacy = data / "live-research/old-report"
    legacy.mkdir(parents=True)
    (legacy / "state.json").write_text('{"status":"COMPLETED"}')
    orphan = b'{"release":"outside the SQL graph"}'
    orphan_digest = hashlib.sha256(orphan).hexdigest()
    orphan_path = data / "blobs/sha256" / orphan_digest[:2] / orphan_digest[2:4] / orphan_digest
    orphan_path.parent.mkdir(parents=True)
    orphan_path.write_bytes(orphan)
    return data


def test_backup_preserves_history_private_configuration_and_restores_offline(tmp_path):
    data = source(tmp_path)
    original = (data / "metadata.sqlite").read_bytes()
    result = local.run_backup(tmp_path)
    bundle = Path(result["archive"])
    assert result["verified"]
    assert (bundle / "config/.env").read_bytes() == (tmp_path / ".env").read_bytes()
    assert (bundle / "runtime/data/calls/intent.json").is_file()
    assert (bundle / "runtime/data/live-research/old-report/state.json").is_file()
    assert result["supplemental_blob_count"] == 1
    assert (data / "metadata.sqlite").read_bytes() == original
    local.verify_bundle(bundle)
    report = drill(bundle / "history", tmp_path / "isolated-restore")
    assert report["executor_started"] is False
    assert (tmp_path / "isolated-restore/blobs/.restore-quarantine.json").is_file()
    (bundle / "config/.env").write_text("tampered")
    with pytest.raises(BackupError, match="hash mismatch"):
        local.verify_bundle(bundle)


def test_scheduled_window_catches_missed_evening_without_duplicate_archives(tmp_path):
    data = source(tmp_path)
    last = {"source_data": str(data), "completed_at": "2026-10-16T21:10:00+08:00"}
    assert not local.due(last, datetime(2026, 10, 17, 9, tzinfo=ZONE), data, 21)
    assert local.due(last, datetime(2026, 10, 17, 21, tzinfo=ZONE), data, 21)
    assert local.due(last, datetime(2026, 10, 18, 9, tzinfo=ZONE), data, 21)
    first = local.run_backup(tmp_path)
    second = local.run_backup(tmp_path, scheduled=True)
    assert second["status"] == "NOT_DUE"
    assert local.verify_bundle(Path(first["archive"]))
    assert len(list((tmp_path / "backups/alphaagent").glob("*/bundle-manifest.json"))) == 1


def test_failed_backup_retains_previous_success_and_never_deletes_archive(tmp_path, monkeypatch):
    source(tmp_path)
    first = local.run_backup(tmp_path)

    def failed(*args, **kwargs):
        raise BackupError("source unavailable")

    monkeypatch.setattr(local, "backup", failed)
    with pytest.raises(BackupError):
        local.run_backup(tmp_path)
    destination = tmp_path / "backups/alphaagent"
    assert json.loads((destination / "last-run.json").read_text())["status"] == "FAILED"
    assert (
        json.loads((destination / "last-success.json").read_text())["archive"] == first["archive"]
    )
    local.verify_bundle(Path(first["archive"]))


def test_overlap_and_missing_history_cannot_create_an_empty_replacement(tmp_path):
    data = source(tmp_path)
    with pytest.raises(BackupError, match="separate"):
        local.run_backup(tmp_path, data / "backup")
    (data / "metadata.sqlite").unlink()
    with pytest.raises(BackupError, match="Existing active"):
        local.run_backup(tmp_path)
    assert not (data / "metadata.sqlite").exists()


def test_runtime_release_pointer_is_captured_before_database_snapshot(tmp_path, monkeypatch):
    data = source(tmp_path)
    original = local.backup

    def publish_during_snapshot(*args, **kwargs):
        (data / "knowledge-release.json").write_text('{"version":2}')
        return original(*args, **kwargs)

    monkeypatch.setattr(local, "backup", publish_during_snapshot)
    result = local.run_backup(tmp_path)
    pointer = Path(result["archive"]) / "runtime/data/knowledge-release.json"
    assert json.loads(pointer.read_bytes()) == {"version": 1}


def test_low_space_fails_without_removing_previous_success(tmp_path, monkeypatch):
    from types import SimpleNamespace

    data = source(tmp_path)
    result = local.run_backup(tmp_path)
    original = (data / "metadata.sqlite").read_bytes()
    monkeypatch.setattr(local.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    with pytest.raises(BackupError, match="free space"):
        local.run_backup(tmp_path)
    assert (data / "metadata.sqlite").read_bytes() == original
    assert Path(result["archive"]).is_dir()
    last = tmp_path / "backups/alphaagent/last-success.json"
    assert json.loads(last.read_bytes())["archive"] == result["archive"]
