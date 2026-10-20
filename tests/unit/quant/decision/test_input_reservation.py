import asyncio
import copy
from types import SimpleNamespace

import pytest
from test_daily_reasoning_profiles import Memory, roles, state

from marketpulse.investigation.recording.errors import (
    InvalidProviderResponseError,
    ProviderCallError,
)
from marketpulse.quant.decision import agents
from marketpulse.quant.decision.next_session import input_reservation as r
from marketpulse.quant.knowledge.active_research import verify_profile


class Port:
    def __init__(self, unknown=False, drift=False, invalid_drift=False):
        self.requests = []
        self.unknown, self.drift = unknown, drift
        self.invalid_drift = invalid_drift

    async def generate(self, request):
        self.requests.append(request)
        if self.unknown:
            raise ProviderCallError("SYNTHETIC missing usage")
        if self.invalid_drift:
            raise InvalidProviderResponseError(
                "SYNTHETIC invalid JSON with measured drift", input_tokens=99999, output_tokens=20
            )
        return SimpleNamespace(
            output=agents.Plan(focus_questions=["合成测试核对现金流。"]),
            provider="SYNTHETIC",
            model=request.model_hint,
            response_id="SYNTHETIC",
            usage=SimpleNamespace(input_tokens=99999 if self.drift else 100, output_tokens=20),
        )


def run(port):
    current, repo = state(enhanced=True, max_tokens=60000), Memory()
    current["config"]["input_reservation"] = dict(r.POLICY)
    # Simulate earlier already charged usage: exact provider token accounting stays untouched.
    current["charged_tokens"] = 30000

    async def task():
        async with roles(repo, current, port, reasoning_tokens=1024) as runner:
            runner.settings.model = "deepseek-flash"
            return await runner.call(
                "Verifier", agents.Plan, {"public": "真实资料内容核对。" * 1400}
            )

    return current, repo, task


def test_local_official_reservation_unlocks_large_utf8_input_and_replays_exactly():
    port = Port()
    current, repo, task = run(port)
    _, call = asyncio.run(task())
    request = repo.value(call["request_ref"])
    assert len(port.requests) == 1 and request["input_estimate"]["reserved_input_tokens"] < 30000
    assert current["charged_tokens"] == 30120
    assert current["config"]["budget"]["max_tokens"] == 60000
    verify_profile(current, request, call, "Verifier", repo=repo)
    changed = copy.deepcopy(call)
    changed["input_estimate"]["margin_tokens"] = 0
    with pytest.raises(ValueError, match="预留"):
        r.verify(current, request, changed)


def test_unknown_usage_preserves_reservation_and_drift_stops_further_calls():
    port = Port(unknown=True)
    current, repo, task = run(port)
    with pytest.raises(ValueError):
        asyncio.run(task())
    assert current["charged_tokens"] == 30000 + sum(c["reserved_tokens"] for c in current["calls"])
    assert current["tokens"] == 0
    port = Port(drift=True)
    current, repo, task = run(port)
    with pytest.raises(ValueError):
        asyncio.run(task())
    assert current["input_reservation_drift"] and len(port.requests) == 1
    assert current["calls"][0]["input_tokens"] == 99999


def test_unpinned_model_or_policy_is_not_discounted():
    with pytest.raises(ValueError, match="未固定"):
        r.estimate(dict(model="unverified-model"), r.POLICY)
    with pytest.raises(ValueError, match="未固定"):
        r.estimate(dict(model="deepseek-flash"), dict(r.POLICY, framing_margin=0))


def test_schema_failure_with_measured_drift_does_not_retry_or_erase_usage():
    port = Port(invalid_drift=True)
    current, repo, task = run(port)
    with pytest.raises(ValueError):
        asyncio.run(task())
    assert current["input_reservation_drift"] and len(port.requests) == 1
    assert current["charged_tokens"] == 130019
    assert current["calls"][0]["input_tokens"] == 99999
