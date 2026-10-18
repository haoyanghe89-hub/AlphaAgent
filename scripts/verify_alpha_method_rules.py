"""Actual K2 API/CAS acceptance, with frozen K1 and research-history assertions."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import requests

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.contracts import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    stage = parser.parse_args().evidence.resolve()
    assert stage.is_relative_to(Path.cwd().resolve() / ".phase3-quant-o")
    baseline = json.loads((stage / "baseline.json").read_text(encoding="utf8"))
    database = Path(baseline["database"])
    blobs = LocalContentAddressedBlobStorage(database.parent / "blobs")
    root = "http://127.0.0.1:8839/api/quant/knowledge"

    def get(path, **params):
        response = requests.get(root + path, params=params, timeout=60)
        response.raise_for_status()
        return response.json()

    catalog = get("/rules")
    assert catalog["rules_published"] >= 24 and catalog["routes_executable"] == 0
    assert catalog["validation_status"] == "UNVALIDATED"
    assert {r["rule"]["kind"] for r in catalog["items"]} == {
        "筛选",
        "入场",
        "持有",
        "退出",
        "风险",
        "容量",
        "估值",
        "市场环境",
    }
    (stage / "rules-api.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf8"
    )
    documents, records, audits = {}, [], {}
    for item in catalog["items"]:
        path = f"/rules/{item['rule_id']}/versions/{item['version']}"
        fixed = get(path)
        rule = fixed["rule"]
        assert digest(rule) == item["version"]
        assert fixed["status"] == "PUBLISHED"
        assert (
            rule["execution_status"] == "REFERENCE_ONLY"
            and rule["validation_status"] == "UNVALIDATED"
        )
        assert rule["counterexamples"] and rule["conditions"] and rule["required_inputs"]
        for citation in rule["citations"]:
            key = (citation["document_id"], citation["version"])
            if key not in documents:
                documents[key] = get(f"/documents/{key[0]}/versions/{key[1]}")
            doc = documents[key]
            body = blobs.get_bytes(BlobRef.from_uri(doc["text_ref"])).decode()
            assert body[citation["start"] : citation["end"]] == citation["quote"]
        for review_id in fixed["review_ids"]:
            review = get("/reviews/" + review_id)
            assert review["verdict"] == "APPROVE" and review["external_verification"] is False
            assert {r["role"] for r in review["reviews"]} == {
                "SupportReviewer",
                "CounterexampleReviewer",
            }
            if review["audit_id"] not in audits:
                audits[review["audit_id"]] = get("/audits/" + review["audit_id"])
            audit = audits[review["audit_id"]]
            assert audit["model_calls"] <= 24 and audit["charged_tokens"] <= 60000
            assert audit["elapsed_seconds"] <= 900
        records.append(
            dict(
                rule_id=item["rule_id"],
                version=item["version"],
                source_families=item["source_families"],
            )
        )
    searches = {}
    for term in ("资本回报", "买点", "资金量", "因子投资", "DCF", "回撤控制"):
        result = get("/rules/search", q=term)
        assert 0 < len(result["items"]) <= 12, term
        families = [f for r in result["items"] for f in r["source_families"]]
        assert len(families) == len(set(families))
        searches[term] = [r["rule_id"] for r in result["items"]]
    assert not get("/rules/search", q="资本回报", horizon="NEXT_SESSION")["items"]
    assert not get("/rules/search", q="资本回报", cutoff=baseline["created_at"])["items"]
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute("select run_id,payload from inv_quant_research_run").fetchall()
        current = {r[0]: hashlib.sha256(r[1].encode()).hexdigest() for r in rows}
        assert all(current.get(k) == v["hash"] for k, v in baseline["runs"].items())
        current_docs = connection.execute(
            "select document_id,version,manifest_ref from inv_quant_knowledge_document"
        ).fetchall()
        assert set(current_docs) == {tuple(row) for row in baseline["documents"]}
        assert connection.execute("pragma integrity_check").fetchone() == ("ok",)
        assert not connection.execute("pragma foreign_key_check").fetchall()
    result = dict(
        status="PASSED",
        published=len(records),
        categories=8,
        source_families=catalog["source_families"],
        preserved_research_runs=len(baseline["runs"]),
        preserved_source_versions=len(baseline["documents"]),
        model_calls=max(a["model_calls"] for a in audits.values()),
        charged_tokens=max(a["charged_tokens"] for a in audits.values()),
        rules=records,
        searches=searches,
        validation_status="UNVALIDATED",
    )
    (stage / "real-acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("rules", "searches")}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
