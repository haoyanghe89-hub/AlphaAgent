"""Release only proven pre-send connection reservations; keep all attempt history."""


def validate_release(call, proof):
    if (
        call.get("status") != "FAILED"
        or call.get("diagnostics", {}).get("category") != "connection_error"
        or call.get("response_ref")
        or call.get("raw_model_response_ref")
        or proof.get("client_max_retries") != 0
        or "httpcore2.ConnectError: All connection attempts failed"
        not in proof.get("failure_log", "")
    ):
        raise ValueError("DEFINITE_PRE_SEND_FAILURE_PROOF_REQUIRED")


def release_unconnected_reservation(state, index, proof, proof_ref):
    call = state["calls"][index]
    validate_release(call, proof)
    receipts = state.setdefault("reservation_releases", [])
    if any(r["call_index"] == index for r in receipts):
        return
    if call.get("charged_tokens") is not None:
        raise ValueError("SETTLED_USAGE_CANNOT_BE_RELEASED")
    amount = call["reserved_tokens"]
    receipts.append(
        dict(
            call_index=index,
            request_ref=call["request_ref"],
            amount=amount,
            proof_ref=proof_ref,
            reason="NO_CONNECTION_ESTABLISHED",
        )
    )
    state["charged_tokens"] -= amount
    # Calls and their original reservations remain unchanged. No timeout or
    # unknown-usage provider response qualifies for this release.


def verify_releases(state, value):
    seen = set()
    for receipt in state.get("reservation_releases", []):
        index = receipt["call_index"]
        if index in seen or not 0 <= index < len(state["calls"]):
            raise ValueError("DUPLICATED_OR_INVALID_BUDGET_RELEASE")
        seen.add(index)
        call = state["calls"][index]
        validate_release(call, value(receipt["proof_ref"]))
        if (
            receipt["amount"] != call["reserved_tokens"]
            or receipt["request_ref"] != call["request_ref"]
        ):
            raise ValueError("BUDGET_RELEASE_MISMATCH")
    if seen:
        expected = sum(c.get("charged_tokens", c["reserved_tokens"]) for c in state["calls"])
        expected -= sum(r["amount"] for r in state["reservation_releases"])
        if state["charged_tokens"] != expected:
            raise ValueError("BUDGET_ACCOUNTING_MISMATCH")
