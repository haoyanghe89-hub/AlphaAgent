from marketpulse.quant.knowledge.rule_retrieval import retrieve
from marketpulse.telemetry.tracing import observation

from .test_runtime import receiver as receiver
from .test_runtime import runtime as runtime
from .test_runtime import spans


def test_lexical_retrieval_records_actual_stages_without_invented_vector_search(runtime, receiver):
    class Store:
        def current(self, cutoff):
            return [
                {
                    "rule_id": "KR-CASH",
                    "version": "a" * 64,
                    "source_families": ["public"],
                    "rule": {
                        "title": "cash",
                        "keywords": ["cash"],
                        "system_rule": "cash",
                        "required_inputs": [],
                        "conditions": [],
                        "counterexamples": [],
                        "horizons": ["MEDIUM"],
                    },
                }
            ]

    with observation("research", as_type="agent"):
        result = retrieve(Store(), "cash", horizon="MEDIUM")
    runtime.flush()
    assert [item["rule_id"] for item in result["items"]] == ["KR-CASH"]
    names = {item.name for item in spans(receiver[2])}
    assert {
        "knowledge.retrieve",
        "retrieval.query_expand",
        "retrieval.filter_and_score",
        "retrieval.rank",
        "retrieval.diversify_and_limit",
    } <= names
    assert not any("embedding" in name or "dense" in name for name in names)
