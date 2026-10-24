"""Versioned dataset registration; labels are never sent to a tested agent."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

import httpx

DATASET_NAME = "alphaagent-core-regression-v1"


def seed_cases(path, *, count=20):
    cases = json.loads(path.read_text(encoding="utf-8"))[:count]
    version = hashlib.sha256(
        json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return version, [
        {
            "id": str(uuid5(NAMESPACE_URL, DATASET_NAME + ":" + version + ":" + case["case_id"])),
            "datasetName": DATASET_NAME,
            "input": {
                "case_id": case["case_id"],
                "family": case["family"],
                "material_kind": case["material_kind"],
                "material": json.loads(case["fixture_json"]),
            },
            "expectedOutput": {
                "required_behavior": case["allowed"],
                "forbidden_behavior": case["forbidden"],
                "expected_gaps": case["expected_gaps"],
            },
            "metadata": {
                "case_set_version": version,
                "original_split": case["split"],
                "label_provenance": case["review_identity"],
                "execution_status": "REGISTERED_NOT_EXECUTED",
                "human_calibration": "NOT_COMPLETED",
            },
        }
        for case in cases
    ]


def sync_seed(config, path):
    version, items = seed_cases(path)
    with httpx.Client(
        base_url=config.base_url, auth=(config.public_key, config.secret_key), timeout=10
    ) as client:
        existing = client.get("/api/public/datasets/" + DATASET_NAME)
        if existing.status_code == 404:
            client.post(
                "/api/public/datasets",
                json={
                    "name": DATASET_NAME,
                    "description": "Synthetic candidate regression; AI labels, not human gold.",
                    "metadata": {
                        "case_set_version": version,
                        "material_kind": "SYNTHETIC_GATE_FIXTURE",
                    },
                },
            ).raise_for_status()
            known = set()
        else:
            existing.raise_for_status()
            snapshot = existing.json()
            if snapshot.get("metadata", {}).get("case_set_version") != version:
                raise ValueError("Dataset version differs; register a new named version")
            known = {item["id"] for item in snapshot.get("items", [])}
        created = 0
        for item in items:
            if item["id"] not in known:
                client.post("/api/public/dataset-items", json=item).raise_for_status()
                created += 1
        stored = client.get("/api/public/datasets/" + DATASET_NAME)
        stored.raise_for_status()
        stored_ids = {item["id"] for item in stored.json().get("items", [])}
        if not {item["id"] for item in items} <= stored_ids:
            raise ValueError("Dataset registration is not yet visible in server readback")
    return {
        "dataset_name": DATASET_NAME,
        "registered_cases": len(items),
        "created_cases": created,
        "case_set_version": version,
        "business_trials_executed": 0,
        "human_calibration": "NOT_COMPLETED",
    }
