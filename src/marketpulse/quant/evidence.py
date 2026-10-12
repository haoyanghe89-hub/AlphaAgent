from __future__ import annotations

import json

from pydantic import AwareDatetime

from .contracts import digest
from .domain import ArtifactBundle, ExactDecimal, FrozenModel, MetricValue
from .execution.service import check_bundle


class ComputationEvidence(FrozenModel):
    evidence_id: str
    artifact_id: str
    artifact_hash: str
    manifest_hash: str
    input_snapshot_hashes: tuple[tuple[str, str], ...]
    metric_path: str
    row_keys: tuple[str, ...]
    value: ExactDecimal | None
    unit: str
    definition: str
    scope: str
    start: str
    end: str
    asof: AwareDatetime
    cell_hash: str
    producer_families: tuple[str, ...]


def build_computation_evidence(bundle: ArtifactBundle, *, metric_path: str) -> ComputationEvidence:
    check_bundle(bundle)
    return _build_cell(bundle, metric_path, json.loads(bundle.manifest_json))


def computation_evidence_cells(bundle: ArtifactBundle):
    """One full integrity check per immutable bundle, independent hash per cell.

    No caller-controlled skip flag: this batch boundary always validates first.
    Bundle and its tuple of cells are frozen; unchecked input is never admitted.
    """
    check_bundle(bundle)
    manifest = json.loads(bundle.manifest_json)
    return tuple(_build_cell(bundle, f"/values/{i}", manifest) for i in range(len(bundle.values)))


def _build_cell(bundle, metric_path, manifest):
    pieces = metric_path.split("/")
    if len(pieces) != 3 or pieces[0] != "" or pieces[1] != "values" or not pieces[2].isdigit():
        raise ValueError("metric path must be a stable values ordinal")
    index = int(pieces[2])
    if str(index) != pieces[2] or index >= len(bundle.values):
        raise ValueError("unknown cell")
    cell: MetricValue = bundle.values[index]
    material = {
        "artifact_id": bundle.artifact_id,
        "artifact_hash": bundle.output_hash,
        "manifest_hash": bundle.manifest_hash,
        "input_snapshot_hashes": tuple(
            (s["snapshot_id"], s["semantic_hash"]) for s in manifest["inputs"]
        ),
        "metric_path": metric_path,
        "row_keys": cell.row_keys,
        "value": cell.model_dump(mode="json")["value"],
        "unit": cell.unit,
        "definition": cell.definition,
        "scope": cell.instrument_id,
        "start": cell.start.isoformat(),
        "end": cell.end.isoformat(),
        "asof": manifest["asof"],
        "producer_families": tuple(
            sorted(
                {
                    s["family"] if s["lineage"] == "verified" else "UNKNOWN"
                    for s in manifest["inputs"]
                }
            )
        ),
        "cell": cell.model_dump(mode="json"),
    }
    cell_hash = digest(material)
    material.pop("cell")
    return ComputationEvidence(evidence_id="qev_" + cell_hash, cell_hash=cell_hash, **material)
