import copy

import pytest

from marketpulse.quant.knowledge.budget import release_unconnected_reservation, verify_releases


def test_proven_connect_failure_releases_reserved_tokens_once_without_erasing_attempt():
    state = dict(
        charged_tokens=100,
        calls=[
            dict(
                status="FAILED",
                diagnostics=dict(category="connection_error"),
                reserved_tokens=100,
                request_ref="SYNTHETIC",
            )
        ],
    )
    proof = dict(
        client_max_retries=0, failure_log="httpcore2.ConnectError: All connection attempts failed"
    )
    before = copy.deepcopy(state["calls"])
    release_unconnected_reservation(state, 0, proof, "SYNTHETIC-PROOF")
    release_unconnected_reservation(state, 0, proof, "SYNTHETIC-PROOF")
    assert state["charged_tokens"] == 0 and state["calls"] == before
    assert len(state["reservation_releases"]) == 1
    verify_releases(state, lambda _: proof)
    state["charged_tokens"] = -1
    with pytest.raises(ValueError, match="BUDGET_ACCOUNTING_MISMATCH"):
        verify_releases(state, lambda _: proof)


@pytest.mark.parametrize(
    "change",
    [
        dict(response_ref="EXISTS"),
        dict(raw_model_response_ref="EXISTS"),
        dict(diagnostics=dict(category="timeout")),
        dict(status="COMPLETED"),
    ],
)
def test_ambiguous_or_responded_attempts_cannot_release_budget(change):
    state = dict(
        charged_tokens=100,
        calls=[
            dict(
                status="FAILED",
                diagnostics=dict(category="connection_error"),
                reserved_tokens=100,
                request_ref="SYNTHETIC",
                **{},
            )
        ],
    )
    state["calls"][0].update(change)
    proof = dict(
        client_max_retries=0, failure_log="httpcore2.ConnectError: All connection attempts failed"
    )
    with pytest.raises(ValueError, match="DEFINITE_PRE_SEND_FAILURE_PROOF_REQUIRED"):
        release_unconnected_reservation(state, 0, proof, "SYNTHETIC-PROOF")
    assert state["charged_tokens"] == 100
