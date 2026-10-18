"""Local AlphaAgent backups: existing DB/CAS snapshot plus private runtime files."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from marketpulse.investigation.operations.backup import (
    BackupError,
    _record,
    _safe_path,
    backup,
    verify,
)

ZONE = ZoneInfo("Asia/Shanghai")


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + "." + uuid.uuid4().hex + ".pending")
    with pending.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


@contextmanager
def backup_lock(path: Path):
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def due(last: dict | None, now: datetime, data: Path, hour: int) -> bool:
    if not last or last.get("source_data") != str(data):
        return True
    cutoff = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now < cutoff:
        cutoff -= timedelta(days=1)
    try:
        previous = datetime.fromisoformat(last["completed_at"])
        return previous.tzinfo is None or previous < cutoff
    except (KeyError, ValueError, TypeError):
        return True


def verify_bundle(bundle: Path) -> dict:
    bundle = _safe_path(bundle)
    value = json.loads((bundle / "bundle-manifest.json").read_bytes())
    if value.get("version") not in (1, 2):
        raise BackupError("Unsupported local backup bundle")
    manifest = verify(bundle / "history")
    expected = {"bundle-manifest.json", "history/manifest.json"}
    expected.update("history/" + name for name in manifest.files)
    for relative, record in value["supplements"].items():
        target = _safe_path(bundle / relative)
        if not target.is_relative_to(bundle) or not relative.startswith(("config/", "runtime/")):
            raise BackupError("Invalid supplemental backup path")
        if _record(target).model_dump() != record:
            raise BackupError("Supplemental backup hash mismatch")
        expected.add(relative)
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    if actual != expected:
        raise BackupError("Unexpected or missing local backup files")
    return value


def run_backup(
    project: Path,
    destination: Path | None = None,
    *,
    scheduled: bool = False,
    hour: int = 21,
    now: datetime | None = None,
) -> dict:
    if not 0 <= hour <= 23:
        raise BackupError("Backup hour must be between 0 and 23")
    project = _safe_path(project)
    config_path = project / "alphaagent.local.json"
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    data = _safe_path(project / config["data"])
    destination = _safe_path(destination or project / "backups/alphaagent")
    if data.is_relative_to(destination) or destination.is_relative_to(data):
        raise BackupError("Backup destination must be separate from active history")
    if not (data / "metadata.sqlite").is_file() or not (data / "blobs").is_dir():
        raise BackupError("Existing active database and blobs are required")
    destination.mkdir(parents=True, exist_ok=True)
    now = (now or datetime.now(ZONE)).astimezone(ZONE)
    state_path = destination / "last-run.json"
    last_path = destination / "last-success.json"
    with backup_lock(destination / ".backup.lock") as acquired:
        if not acquired:
            return {"status": "ALREADY_RUNNING"}
        try:
            last = json.loads(last_path.read_bytes())
        except (OSError, ValueError):
            last = None
        if (
            scheduled
            and last
            and last.get("bundle_version") == 2
            and not due(last, now, data, hour)
        ):
            archive = Path(last["archive"])
            if archive.is_dir() and (archive / "bundle-manifest.json").is_file():
                return {"status": "NOT_DUE", "last_success": last["completed_at"]}
        bundle = destination / (now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        bundle.mkdir(exist_ok=False)
        state = {
            "status": "RUNNING",
            "source_data": str(data),
            "archive": str(bundle),
            "started_at": now.isoformat(),
        }
        write_json(state_path, state)
        try:
            sources = [(config_path, "config/alphaagent.local.json")]
            for name in (".env", "start-alphaagent.ps1"):
                if (project / name).is_file():
                    sources.append((project / name, "config/" + name))
            for path in data.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(data)
                if relative.parts[0] == "blobs" or path.suffix == ".lock":
                    continue
                if relative.as_posix() in {
                    "metadata.sqlite",
                    "metadata.sqlite-wal",
                    "metadata.sqlite-shm",
                    "metadata.sqlite-journal",
                }:
                    continue
                sources.append((path, "runtime/data/" + relative.as_posix()))
            captured = {"config/alphaagent.local.json": config_bytes}
            for source, relative in sources:
                source = _safe_path(source)
                if not (source.is_relative_to(project) or source.is_relative_to(data)):
                    raise BackupError("Supplemental source escaped the project or data directory")
                if relative not in captured:
                    captured[relative] = source.read_bytes()
            # Keep all pre-existing CAS bytes, including release files outside the SQL graph.
            blob_files = [_safe_path(p) for p in (data / "blobs/sha256").rglob("*") if p.is_file()]
            required = (data / "metadata.sqlite").stat().st_size
            required += sum(p.stat().st_size for p in blob_files)
            required += sum(len(raw) for raw in captured.values()) + 256 * 1024**2
            if shutil.disk_usage(destination).free < required:
                raise BackupError("Not enough free space; existing history and backups retained")
            manifest = backup(
                "sqlite:///" + (data / "metadata.sqlite").as_posix(),
                data / "blobs",
                bundle / "history",
            )
            supplements = {}
            for relative, raw in captured.items():
                target = bundle / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                supplements[relative] = _record(target).model_dump()
            referenced = set(manifest.blob_hashes)
            for source in blob_files:
                if source.name in referenced:
                    continue
                relative = (
                    "runtime/unreferenced-blobs/" + source.relative_to(data / "blobs").as_posix()
                )
                target = bundle / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open("rb") as read, target.open("xb") as write:
                    shutil.copyfileobj(read, write)
                    write.flush()
                    os.fsync(write.fileno())
                record = _record(target)
                if record.sha256 != source.name:
                    raise BackupError("Supplemental CAS content does not match its identity")
                supplements[relative] = record.model_dump()
            write_json(
                bundle / "bundle-manifest.json",
                {
                    "version": 2,
                    "source_data": str(data),
                    "snapshot_at": manifest.created_at,
                    "supplements": supplements,
                    "restore_requires_reconciliation": True,
                },
            )
            verify_bundle(bundle)
            state.update(
                status="COMPLETED",
                bundle_version=2,
                completed_at=datetime.now(ZONE).isoformat(),
                blob_count=len(manifest.blob_hashes),
                supplemental_blob_count=len(blob_files)
                - len(referenced.intersection(p.name for p in blob_files)),
                verified=True,
            )
            write_json(last_path, state)
            write_json(state_path, state)
            return state
        except Exception as error:
            state.update(
                status="FAILED",
                error_type=type(error).__name__,
                error=str(error)
                if isinstance(error, BackupError)
                else "Backup failed; previous successful archives retained",
            )
            write_json(state_path, state)
            raise


def main(project: Path) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--hour", type=int, default=21)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            verify_bundle(args.verify)
            result = {"status": "VERIFIED", "archive": str(args.verify)}
        else:
            result = run_backup(project, args.backup_root, scheduled=args.scheduled, hour=args.hour)
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=True))
    except Exception:
        if sys.stderr is not None:
            print("Local backup failed; inspect backups/alphaagent/last-run.json.", file=sys.stderr)
        raise SystemExit(1) from None
