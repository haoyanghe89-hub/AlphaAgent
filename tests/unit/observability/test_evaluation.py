from pathlib import Path

import pytest

from marketpulse.telemetry.evaluation import seed_cases

from .test_runtime import receiver as receiver
from .test_runtime import runtime as runtime


def test_dataset_has_separate_expectations_and_explicit_uncalibrated_labels():
    source = Path("src/marketpulse/quant/decision/evaluation_cases.json")
    version, items = seed_cases(source)
    assert len(items) == 20
    assert len({item["id"] for item in items}) == 20
    assert seed_cases(source) == (version, items)
    for item in items:
        assert "expectedOutput" not in item["input"]
        assert "forbidden_behavior" not in item["input"]
        assert item["metadata"]["human_calibration"] == "NOT_COMPLETED"
        assert item["expectedOutput"]["forbidden_behavior"]


def test_missing_numeric_score_requires_explicit_unknown_status(runtime):
    with pytest.raises(ValueError):
        runtime.score("a" * 32, "correctness", None, version="v1")


def test_reregister_preserves_existing_human_labels(monkeypatch):
    import httpx

    from marketpulse.telemetry.config import TraceConfig
    from marketpulse.telemetry.evaluation import sync_seed

    source = Path("src/marketpulse/quant/decision/evaluation_cases.json")
    version, items = seed_cases(source)
    writes = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, path):
            return httpx.Response(
                200,
                request=httpx.Request("GET", "https://example.com"),
                json={"metadata": {"case_set_version": version}, "items": items},
            )

        def post(self, *args, **kwargs):
            writes.append(kwargs)
            raise AssertionError("Existing labeled items must not be overwritten")

    monkeypatch.setattr(httpx, "Client", Client)
    result = sync_seed(TraceConfig(), source)
    assert result["created_cases"] == 0 and not writes
