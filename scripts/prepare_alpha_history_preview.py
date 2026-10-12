"""Copy real frozen source inputs into a fresh private preview, never old execution jobs."""

import argparse
import json
import os
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from marketpulse.config import Settings
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.investigation.server import create_app
from marketpulse.quant.domain import Instrument
from marketpulse.quant.storage.models import InstrumentRow
from marketpulse.quant.storage.snapshots import SnapshotStore


def prepare(source, target):
    private = Path.cwd().resolve() / ".phase3-quant-o"
    source, target = source.resolve(), target.resolve()
    if not source.is_relative_to(private) or not target.is_relative_to(private) or target.exists():
        raise ValueError("existing private source and new private target required")
    if not (source / "metadata.sqlite").is_file():
        raise ValueError("real input database required")
    target.mkdir()
    os.environ["INVESTIGATION_BLOB_ROOT"] = str(target / "blobs")
    os.environ["QUANT_CALL_JOURNAL_ROOT"] = str(target / "calls")
    old = SnapshotStore(
        sessionmaker(create_engine("sqlite:///" + (source / "metadata.sqlite").as_posix())),
        LocalContentAddressedBlobStorage(source / "blobs"),
    )
    app = create_app(
        settings=Settings(
            database_url=SecretStr("sqlite:///" + (target / "metadata.sqlite").as_posix()),
            review_allow_insecure_loopback=True,
        )
    )
    with TestClient(app):
        new = app.state.quant_runtime.service.store
        with old.sessions() as session:
            for row in session.scalars(select(InstrumentRow)).all():
                new.register(Instrument.model_validate(row.definition))
        pins = set()
        for path in source.glob("request-*.json"):
            request = json.loads(path.read_text(encoding="utf-8"))
            pins.update(request["frozen_snapshot_ids"])
            shutil.copyfile(path, target / path.name)
        for sid in pins:
            snapshot = old.load(sid)
            if snapshot.result.request.normalizer_version == "dcf-official-v1":
                for row in json.loads(snapshot.result.records_json):
                    ref = BlobRef(row["document_sha256"])
                    assert new.blobs.put_bytes(old.blobs.get_bytes(ref)).ref == ref
            assert new.freeze(snapshot.result).snapshot_id == sid
            new.load(sid)
    print(json.dumps(dict(data=str(target), snapshots=len(pins))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source, args.target)
