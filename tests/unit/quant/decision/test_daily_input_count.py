import asyncio
from types import SimpleNamespace

import pytest
from test_daily_reasoning_profiles import Memory, roles, state

from marketpulse.investigation.recording.errors import ModelOutputTruncatedError, ProviderCallError
from marketpulse.quant.decision import agents
from marketpulse.quant.knowledge.active_research import verify_profile


class MeteredPort:
    def __init__(self, unknown=False):
        self.requests = []
        self.unknown = unknown

    async def generate(self, request):
        self.requests.append(request)
        if request.max_output_tokens == 1:
            if self.unknown:
                raise ProviderCallError("unknown usage")
            raise ModelOutputTruncatedError("one token probe", input_tokens=50, output_tokens=1)
        return SimpleNamespace(
            output=agents.Plan(focus_questions=["核查现金流"]),
            provider="synthetic",
            model=request.model_hint,
            response_id="test",
            usage=SimpleNamespace(input_tokens=50, output_tokens=30),
        )


def run(current, port, repo=None):
    repo = repo or Memory()

    async def task():
        async with roles(repo, current, port, reasoning_tokens=1024) as runner:
            return await runner.call("Verifier", agents.Plan, {"public": "汉" * 2000})

    return repo, asyncio.run(task())


def test_exact_provider_input_count_unlocks_call_without_increasing_total_budget():
    current, port = state(enhanced=True, max_tokens=7000), MeteredPort()
    repo, (_, final) = run(current, port)
    assert [r.max_output_tokens for r in port.requests] == [1, 2024]
    assert port.requests[0].model_copy(update={"max_output_tokens": 2024}) == port.requests[1]
    probe = current["calls"][0]
    assert probe["status"] == "MEASURED" and probe["purpose"] == "input_count"
    assert current["model_calls"] == 2 and current["charged_tokens"] == current["tokens"] == 131
    assert current["config"]["budget"]["max_tokens"] == 7000
    assert final["reserved_tokens"] == 50 + 2024
    assert final["input_count_ref"] == probe["request_ref"]
    verify_profile(current, repo.value(probe["request_ref"]), probe, "Verifier", repo=repo)
    verify_profile(current, repo.value(final["request_ref"]), final, "Verifier", repo=repo)


def test_large_batch_measures_when_per_call_reserve_exceeds_limit_before_total_budget():
    current, port, repo = state(enhanced=True, max_tokens=60000), MeteredPort(), Memory()

    async def task():
        async with roles(repo, current, port, reasoning_tokens=1024) as runner:
            runner.reserve_limit = 7500
            return await runner.call("Verifier", agents.Plan, {"public": "汉" * 2000})

    asyncio.run(task())
    assert [r.max_output_tokens for r in port.requests] == [1, 2024]
    assert current["charged_tokens"] == 131
    assert current["config"]["budget"]["max_tokens"] == 60000


def test_unknown_probe_failure_keeps_worst_reservation_and_cannot_publish():
    current, port, repo = state(enhanced=True, max_tokens=7000), MeteredPort(True), Memory()
    with pytest.raises(ValueError, match="输入计量未完成"):
        run(current, port, repo)
    assert len(port.requests) == current["model_calls"] == 1
    probe = current["calls"][0]
    assert probe["status"] == "FAILED" and current["charged_tokens"] == probe["reserved_tokens"]
    assert current["tokens"] == 0
    verify_profile(current, repo.value(probe["request_ref"]), probe, "Verifier", repo=repo)


def test_resume_reuses_only_completed_measurement_and_keeps_its_charge():
    current, port = state(enhanced=True, max_tokens=7000), MeteredPort()
    repo, _ = run(current, port)
    current["calls"] = current["calls"][:1]
    current.update(model_calls=1, charged_tokens=51, tokens=51)
    resumed = MeteredPort()
    run(current, resumed, repo)
    assert len(resumed.requests) == 1 and resumed.requests[0].max_output_tokens == 2024
    assert current["model_calls"] == 2 and current["charged_tokens"] == 131


def test_real_adapter_truncation_reports_exact_input_without_archiving_reasoning(monkeypatch):
    from marketpulse.quant.decision.next_session import agents as daily

    current, repo, requests = state(enhanced=True, max_tokens=7000), Memory(), []

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)

        async def create(self, **kwargs):
            requests.append(kwargs)
            probe = kwargs["max_tokens"] == 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="" if probe else '{"focus_questions":["核查现金流"]}',
                            reasoning_content="PRIVATE_INTERNAL_REASONING",
                        ),
                        finish_reason="length" if probe else "stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=50, completion_tokens=1 if probe else 30),
                id="synthetic",
            )

        async def close(self):
            pass

    monkeypatch.setattr(daily, "configure_deepseek", lambda _: Client())
    run(current, None, repo)
    assert [r["max_tokens"] for r in requests] == [1, 2024]
    assert requests[0]["messages"] == requests[1]["messages"]
    assert current["charged_tokens"] == 131
    assert "PRIVATE_INTERNAL_REASONING" not in str(repo.values)


@pytest.mark.parametrize(
    "enhanced,max_tokens,max_calls", [(False, 7000, 8), (True, 5000, 8), (True, 7000, 1)]
)
def test_probe_never_bypasses_legacy_or_shared_reservation_gates(enhanced, max_tokens, max_calls):
    current, port = (
        state(enhanced=enhanced, max_tokens=max_tokens, max_calls=max_calls),
        MeteredPort(),
    )
    with pytest.raises(ValueError, match="预算不足"):
        run(current, port)
    assert not port.requests and current["model_calls"] == 0


@pytest.mark.parametrize("mutation", ["messages", "key", "input", "index", "reserve"])
def test_formal_call_cannot_reuse_different_or_forged_measurement(mutation):
    current, port = state(enhanced=True, max_tokens=7000), MeteredPort()
    repo, (_, final) = run(current, port)
    request = repo.value(final["request_ref"])
    if mutation == "messages":
        request["messages"][-1]["content"] += "changed"
    elif mutation == "key":
        request["input_count_key"] = "forged"
    elif mutation == "input":
        current["calls"][0]["input_tokens"] = 49
    elif mutation == "index":
        request["input_count_call_index"] = 1
    else:
        final["reserved_tokens"] -= 1
    with pytest.raises(ValueError, match="计量"):
        verify_profile(current, request, final, "Verifier", repo=repo)
