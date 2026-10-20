import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from marketpulse.config import Settings
from marketpulse.errors import ConfigurationError
from marketpulse.investigation.recording.errors import ModelOutputTruncatedError
from marketpulse.quant.contracts import canonical
from marketpulse.quant.decision import agents
from marketpulse.quant.decision.next_session.agents import DailyRoles


class Memory:
    def __init__(self):
        self.values = []

    def blob(self, value):
        self.values.append(copy.deepcopy(value))
        return str(len(self.values))

    def value(self, ref):
        return copy.deepcopy(self.values[int(ref) - 1])

    def raw(self, value):
        return self.blob(value)


class Port:
    def __init__(self, *, truncated=0):
        self.requests = []
        self.truncated = truncated

    async def generate(self, request):
        self.requests.append(request)
        if len(self.requests) <= self.truncated:
            raise ModelOutputTruncatedError("truncated", input_tokens=10, output_tokens=4096)
        return SimpleNamespace(
            output=agents.Plan(focus_questions=["核查经营变化"]),
            provider="synthetic",
            model=request.model_hint,
            response_id="test",
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        )


def state(*, enhanced=False, max_tokens=60000, max_calls=8):
    config = dict(
        budget=dict(max_tokens=max_tokens, max_model_calls=max_calls), prompt_version="test"
    )
    if enhanced:
        config["agentic_research_v1"] = True
    return dict(config=config, calls=[], model_calls=0, tokens=0, charged_tokens=0)


def roles(repo, current, port, **options):
    return DailyRoles(
        repo,
        current,
        Settings(model="deepseek-synthetic", model_thinking_enabled=True),
        lambda: None,
        lambda: None,
        port,
        prompts={
            r: "只输出 JSON" for r in ("Planner", "Analyst", "Verifier", "Researcher", "Writer")
        },
        output_tokens=1000,
        retry_delay=0,
        **options,
    )


@pytest.mark.parametrize("role", ["Planner", "Analyst", "Verifier", "Researcher", "Writer"])
def test_legacy_task_keeps_thinking_off_and_records_actual_request(role):
    repo, current, port = Memory(), state(), Port()

    async def run():
        async with roles(repo, current, port) as runner:
            return await runner.call(role, agents.Plan, {})

    _, call = asyncio.run(run())
    request = port.requests[0]
    recorded = repo.value(call["request_ref"])
    assert request.thinking_enabled is False and request.reasoning_effort is None
    assert request.max_output_tokens == 1000
    for item in (recorded, call):
        assert item["thinking_enabled"] is False
        assert item["reasoning_effort"] is None
        assert item["reasoning_tokens"] == 0
        assert item["max_output_tokens"] == 1000
    assert "reasoning_profile" not in current["config"]


@pytest.mark.parametrize(
    ("role", "enabled", "effort", "reserve"),
    [
        ("Planner", True, "low", 2048),
        ("Researcher", True, "low", 2048),
        ("Analyst", True, "high", 8192),
        ("Verifier", True, "high", 8192),
        ("Writer", False, None, 0),
    ],
)
def test_enhanced_role_adds_reasoning_and_json_to_the_same_budget(role, enabled, effort, reserve):
    repo, current, port = Memory(), state(enhanced=True), Port()

    async def run():
        async with roles(repo, current, port) as runner:
            return await runner.call(role, agents.Plan, {})

    _, call = asyncio.run(run())
    request = port.requests[0]
    recorded = repo.value(call["request_ref"])
    assert request.thinking_enabled is enabled and request.reasoning_effort == effort
    assert request.max_output_tokens == 1000 + reserve
    assert current["config"]["reasoning_profile"]["version"] == "daily-reasoning-v1"
    for item in (recorded, call):
        assert item["thinking_enabled"] is enabled
        assert item["reasoning_effort"] == effort
        assert item["reasoning_tokens"] == reserve
        assert item["max_output_tokens"] == request.max_output_tokens
    assert current["tokens"] == current["charged_tokens"] == 30
    assert current["config"]["budget"]["max_tokens"] == 60000


def test_fixed_profile_survives_settings_change_and_matches_only_compatible_identity():
    repo, current, port = Memory(), state(enhanced=True), Port()

    async def run():
        async with roles(repo, current, port, reasoning_tokens=8000) as first:
            await first.call("Analyst", agents.Plan, {})
        frozen = copy.deepcopy(current["config"]["reasoning_profile"])
        second = roles(repo, current, port)
        second.settings = Settings(reasoning_effort="max")
        async with second:
            profile = second.request_profile("Analyst")
            assert profile["reasoning_effort"] == "high" and profile["reasoning_tokens"] == 8000
            recorded = repo.value(current["calls"][0]["request_ref"])
            assert second.matches_request_profile(recorded, "Analyst")
            assert not second.matches_request_profile({}, "Analyst")
            assert not second.matches_request_profile(
                dict(recorded, thinking_enabled=False), "Analyst"
            )
            assert not second.matches_request_profile(
                dict(recorded, reasoning_effort="low"), "Analyst"
            )
        assert current["config"]["reasoning_profile"] == frozen

    asyncio.run(run())
    legacy = roles(repo, state(), Port())
    assert legacy.matches_request_profile({}, "Analyst")
    assert not legacy.matches_request_profile(dict(thinking_enabled=True), "Analyst")


def test_reasoning_reservation_blocks_before_dispatch_when_budget_is_insufficient():
    repo, current, port = Memory(), state(enhanced=True, max_tokens=1), Port()

    async def run():
        async with roles(repo, current, port) as runner:
            with pytest.raises(ValueError, match="预算不足"):
                await runner.call("Analyst", agents.Plan, {})

    asyncio.run(run())
    assert not port.requests and current["charged_tokens"] == current["model_calls"] == 0


def test_minimum_json_allowance_never_consumes_reserved_reasoning_tokens_or_leaks_to_next_role():
    repo, current, port = Memory(), state(enhanced=True), Port()
    input_reserve = len(
        (canonical({}) + "只输出 JSON" + canonical(agents.Plan.model_json_schema())).encode("utf8")
    )
    current["config"]["budget"]["max_tokens"] = input_reserve + 8192 + 600

    async def run():
        async with roles(repo, current, port, min_output_tokens=500) as runner:
            _, call = await runner.call("Analyst", agents.Plan, {})
            assert runner.output_tokens == 1000
            assert runner.matches_request_profile(repo.value(call["request_ref"]), "Analyst")

    asyncio.run(run())
    assert port.requests[0].max_output_tokens == 8192 + 600
    assert current["calls"][0]["reserved_tokens"] == current["config"]["budget"]["max_tokens"]


def test_truncation_is_charged_including_reasoning_and_retry_remains_bounded():
    repo, current, port = Memory(), state(enhanced=True), Port(truncated=1)

    async def run():
        async with roles(repo, current, port, transport_attempts=2) as runner:
            await runner.call("Analyst", agents.Plan, {})

    asyncio.run(run())
    assert len(port.requests) == current["model_calls"] == 2
    assert current["tokens"] == current["charged_tokens"] == 10 + 4096 + 30
    assert current["calls"][0]["charged_tokens"] == 4106
    assert "上次检查失败" in port.requests[1].messages[-1].content


def test_single_attempt_does_not_retry_truncation_and_resume_does_not_reset_spend():
    repo, current, port = Memory(), state(enhanced=True, max_calls=1), Port(truncated=5)

    async def run():
        async with roles(repo, current, port, transport_attempts=1) as runner:
            with pytest.raises(ValueError, match="输出未通过"):
                await runner.call("Analyst", agents.Plan, {})
        async with roles(repo, current, port) as resumed:
            with pytest.raises(ValueError, match="预算不足"):
                await resumed.call("Analyst", agents.Plan, {})

    asyncio.run(run())
    assert len(port.requests) == 1 and current["charged_tokens"] == 4106


@pytest.mark.parametrize("reserve", [0, 1023, 16001, True])
def test_reasoning_reserve_has_strict_bounds(reserve):
    with pytest.raises((ValueError, ConfigurationError)):
        roles(Memory(), state(enhanced=True), Port(), reasoning_tokens=reserve)


def test_legacy_calls_cannot_be_relabelled_as_enhanced_research():
    current = state(enhanced=True)
    current["calls"] = [dict(kind="model", role="Analyst", status="COMPLETED")]

    async def run():
        with pytest.raises(ConfigurationError, match="新任务"):
            async with roles(Memory(), current, Port()):
                pass

    asyncio.run(run())


def test_source_capture_before_reasoning_initialization_is_allowed():
    current = state(enhanced=True)
    current["calls"] = [dict(tool="price", status="COMPLETED", request_ref="public-source")]

    async def run():
        async with roles(Memory(), current, Port()) as runner:
            assert runner.request_profile("Analyst")["thinking_enabled"]

    asyncio.run(run())


@pytest.mark.parametrize("effort", ["low", "high", "max"])
def test_saved_profile_uses_valid_settings_effort_for_critical_roles(effort):
    current = state(enhanced=True)
    runner = roles(Memory(), current, Port())
    runner.settings = Settings(reasoning_effort=effort)
    assert runner.request_profile("Analyst")["reasoning_effort"] == effort
    assert runner.request_profile("Verifier")["reasoning_effort"] == effort
    assert runner.request_profile("Researcher")["reasoning_effort"] == "low"


def test_invalid_saved_profile_blocks_before_dispatch():
    repo, current, port = Memory(), state(enhanced=True), Port()
    runner = roles(repo, current, port)
    runner.request_profile("Analyst")
    current["config"]["reasoning_profile"]["roles"]["Analyst"]["reasoning_tokens"] = 100000

    async def run():
        with pytest.raises(ConfigurationError, match="推理配置不完整"):
            async with runner:
                pass

    asyncio.run(run())
    assert not port.requests


@pytest.mark.parametrize("enhanced", [False, True])
def test_real_adapter_receives_profile_and_never_archives_hidden_reasoning(monkeypatch, enhanced):
    from marketpulse.quant.decision.next_session import agents as daily

    repo, current = Memory(), state(enhanced=enhanced)
    calls, closed = [], []

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)

        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps({"focus_questions": ["核查现金流"]}),
                            reasoning_content="HIDDEN_REASONING_MUST_NOT_BE_STORED",
                        ),
                        finish_reason="stop",
                    )
                ],
                id="synthetic-response",
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=100),
            )

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(daily, "configure_deepseek", lambda _: Client())

    async def run():
        async with roles(repo, current, None) as runner:
            await runner.call("Analyst", agents.Plan, {})

    asyncio.run(run())
    assert calls[0]["extra_body"]["thinking"]["type"] == ("enabled" if enhanced else "disabled")
    assert calls[0]["max_tokens"] == 1000 + (8192 if enhanced else 0)
    assert calls[0].get("reasoning_effort") == ("high" if enhanced else None)
    assert ("temperature" not in calls[0]) if enhanced else calls[0]["temperature"] == 0
    assert closed == [True]
    assert current["tokens"] == current["charged_tokens"] == 110
    assert "HIDDEN_REASONING" not in str(repo.values) and "HIDDEN_REASONING" not in str(current)
