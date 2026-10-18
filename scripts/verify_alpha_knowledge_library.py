"""Actual API / CAS acceptance and old-history preservation, without creating research."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import requests

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    stage = args.evidence.resolve()
    assert stage.is_relative_to(Path.cwd().resolve() / ".phase3-quant-o")
    baseline = json.loads((stage / "baseline.json").read_text(encoding="utf8"))
    database = Path(baseline["database"])
    blobs = LocalContentAddressedBlobStorage(database.parent / "blobs")
    base = "http://127.0.0.1:8839/api/quant/knowledge"

    def get(path, **kwargs):
        response = requests.get(base + path, timeout=30, **kwargs)
        response.raise_for_status()
        return response

    catalog = get("/catalog").json()
    assert catalog["document_count"] >= 12 and catalog["rules_published"] == 0
    assert catalog["routes_executable"] == 0
    records = []
    for summary in catalog["documents"]:
        path = f"/documents/{summary['document_id']}/versions/{summary['version']}"
        doc = get(path).json()
        raw = get(path + "/original")
        assert raw.headers["content-disposition"].startswith("attachment;")
        assert hashlib.sha256(raw.content).hexdigest() == BlobRef.from_uri(doc["raw_ref"]).sha256
        original_text = blobs.get_bytes(BlobRef.from_uri(doc["text_ref"])).decode()
        assert len(original_text) == doc["body_characters"]
        assert doc["reading_scope"] == "EXCERPT_CAPTURED"
        identity = {
            key: doc[key]
            for key in ("source", "raw_ref", "text_ref", "locators", "extractor_version")
        }
        encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert hashlib.sha256(encoded.encode()).hexdigest() == doc["version"]
        for passage in doc["excerpts"]:
            assert original_text[passage["start"] : passage["end"]] == passage["text"]
            assert passage["anchor"] in passage["text"]
        versions = get(f"/documents/{doc['document_id']}/versions").json()["versions"]
        if len(versions) > 1:
            old = get(f"/documents/{doc['document_id']}/versions/{versions[-1]['version']}").json()
            assert old["version"] != doc["version"]
        records.append(
            dict(
                document_id=doc["document_id"],
                version=doc["version"],
                raw_sha256=BlobRef.from_uri(doc["raw_ref"]).sha256,
                versions=len(versions),
                locators=len(doc["excerpts"]),
            )
        )
    searches = {}
    for term in ("资本回报", "入场时机", "回撤控制", "因子投资", "Howard Marks"):
        result = get("/search", params={"q": term}).json()
        assert result["items"], term
        searches[term] = [row["document_id"] for row in result["items"]]
    old_time = get(
        "/search", params={"q": "资本回报", "cutoff": "2026-10-14T00:00:00+08:00"}
    ).json()
    assert not old_time["items"]
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "select run_id,status,payload from inv_quant_research_run"
        ).fetchall()
        current = {row[0]: hashlib.sha256(row[2].encode()).hexdigest() for row in rows}
        assert all(current.get(key) == value["hash"] for key, value in baseline["runs"].items())
        assert connection.execute("pragma integrity_check").fetchone() == ("ok",)
    result = dict(
        status="PASSED",
        document_count=catalog["document_count"],
        source_families=catalog["source_families"],
        records=records,
        searches=searches,
        preserved_runs=len(baseline["runs"]),
        model_calls=0,
    )
    (stage / "real-acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("records", "searches")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
