import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import InternalServerError

from marketpulse.investigation.recording.errors import ProviderCallError
from marketpulse.quant.decision import agents, medium
from marketpulse.quant.decision.next_session.agents import DailyRoles


class Memory:
    def __init__(self):
        self.values = []

    def blob(self, value):
        self.values.append(value)
        return str(len(self.values))

    def raw(self, value):
        return self.blob(value)


def unavailable():
    return InternalServerError(
        "sensitive body must never be archived",
        response=httpx.Response(503, request=httpx.Request("POST", "https://example.invalid")),
        body=None,
    )


def test_transient_failure_uses_fallback_without_format_correction_or_unbounded_budget():
    repo = Memory()
    state = dict(
        config=dict(budget=dict(max_model_calls=4, max_tokens=60000), prompt_version="synthetic"),
        calls=[],
        model_calls=0,
        tokens=0,
        charged_tokens=0,
    )
    requests = []

    class Port:
        async def generate(self, request):
            requests.append(request)
            if len(requests) < 3:
                raise ProviderCallError("provider unavailable") from unavailable()
            return SimpleNamespace(
                output=agents.Plan(focus_questions=["核对经营变化"]),
                provider="synthetic",
                model=request.model_hint,
                response_id="test",
                usage=SimpleNamespace(input_tokens=10, output_tokens=10),
            )

    async def run():
        async with DailyRoles(
            repo,
            state,
            SimpleNamespace(model="primary", model_read_timeout_seconds=1),
            lambda: None,
            lambda: None,
            Port(),
            prompts={"Planner": "输出 JSON"},
            fallback_model="backup",
            transport_attempts=3,
            retry_delay=0,
        ) as roles:
            return await roles.call("Planner", agents.Plan, {"subject": "合成"})

    result, call = asyncio.run(run())
    assert result.focus_questions and call["model"] == "backup"
    assert [r.model_hint for r in requests] == ["primary", "backup", "backup"]
    assert len({r.messages[-1].content for r in requests}) == 1
    assert state["calls"][0]["diagnostics"]["http_status"] == 503
    assert state["charged_tokens"] > 20 and state["model_calls"] == 3
    assert "sensitive" not in str(state) and "sensitive" not in str(repo.values)


def test_primary_budget_gate_still_stops_transport_retries():
    repo = Memory()
    state = dict(
        config=dict(budget=dict(max_model_calls=1, max_tokens=60000), prompt_version="synthetic"),
        calls=[],
        model_calls=0,
        tokens=0,
        charged_tokens=0,
    )

    class Port:
        async def generate(self, request):
            raise ProviderCallError("unavailable") from unavailable()

    async def run():
        async with DailyRoles(
            repo,
            state,
            SimpleNamespace(model="primary", model_read_timeout_seconds=1),
            lambda: None,
            lambda: None,
            Port(),
            prompts={"Planner": "输出 JSON"},
            fallback_model="backup",
            transport_attempts=3,
            retry_delay=0,
        ) as roles:
            with pytest.raises(ValueError, match="预算不足"):
                await roles.call("Planner", agents.Plan, {})

    asyncio.run(run())
    assert state["model_calls"] == 1 and state["charged_tokens"] > 0


def test_planner_does_not_repeat_full_market_or_policy_payloads():
    context = dict(
        subject={"name": "合成公司"},
        question="研究经营",
        horizon_months=3,
        routing_context={"allowed_primary": ["Q-DISCLOSURE"], "methods": []},
        flow_context={"private": "full windows"},
        policy_context={"private": "full originals"},
        knowledge_context={"private": "full notes"},
        evidence=[{"text": "raw excerpt"}],
    )
    result = medium.planner_context(context)
    assert result["routing_context"] == context["routing_context"]
    assert result["subject"] == context["subject"] and result["question"] == context["question"]
    assert not {"flow_context", "policy_context", "knowledge_context", "evidence"} & result.keys()


def test_independent_provider_remains_selected_for_following_roles():
    state = dict(
        config=dict(budget=dict(max_model_calls=5, max_tokens=60000), prompt_version="test"),
        calls=[],
        model_calls=0,
        tokens=0,
        charged_tokens=0,
    )
    seen = []

    class Primary:
        async def generate(self, request):
            seen.append("primary")
            raise ProviderCallError("unavailable") from unavailable()

    class Backup:
        async def generate(self, request):
            seen.append("backup")
            return SimpleNamespace(
                output=agents.Plan(focus_questions=["核查原文"]),
                provider="independent",
                model=request.model_hint,
                response_id="test",
                usage=SimpleNamespace(input_tokens=10, output_tokens=10),
            )

    async def run():
        async with DailyRoles(
            Memory(),
            state,
            SimpleNamespace(model="primary", model_read_timeout_seconds=1),
            lambda: None,
            lambda: None,
            Primary(),
            prompts={"Planner": "JSON", "Analyst": "JSON"},
            fallback_model="backup-model",
            fallback_port=Backup(),
            transport_attempts=3,
            retry_delay=0,
        ) as roles:
            await roles.call("Planner", agents.Plan, {})
            await roles.call("Analyst", agents.Plan, {})

    asyncio.run(run())
    assert seen == ["primary", "backup", "backup"] and state["independent_backup_used"]
    assert [c.get("provider") for c in state["calls"] if c["status"] == "COMPLETED"] == [
        "independent",
        "independent",
    ]


def test_backup_configuration_is_optional_complete_and_masks_credentials(monkeypatch, tmp_path):
    from marketpulse.errors import ConfigurationError
    from marketpulse.quant.decision.backup_model import configuration

    monkeypatch.setenv("MARKETPULSE_ENV_FILE", str(tmp_path / "empty.env"))
    for key in ["DECISION_BACKUP_BASE_URL", "DECISION_BACKUP_API_KEY", "DECISION_BACKUP_MODEL"]:
        monkeypatch.delenv(key, raising=False)
    assert configuration() is None
    monkeypatch.setenv("DECISION_BACKUP_MODEL", "synthetic")
    with pytest.raises(ConfigurationError):
        configuration()
    monkeypatch.setenv("DECISION_BACKUP_BASE_URL", "https://backup.example/v1")
    monkeypatch.setenv("DECISION_BACKUP_API_KEY", "synthetic-secret")
    config = configuration()
    assert config.model == "synthetic" and "synthetic-secret" not in str(config)
    monkeypatch.setenv("DECISION_BACKUP_BASE_URL", "https://key@backup.example/v1")
    with pytest.raises(ConfigurationError):
        configuration()


def test_backup_archives_actual_model_content_and_closes_client(monkeypatch):
    import json

    from pydantic import SecretStr

    from marketpulse.config import Settings
    from marketpulse.investigation.ports.external import ModelMessage, ModelRequest
    from marketpulse.quant.decision import backup_model

    content = json.dumps({"focus_questions": ["核查公司原文"]})
    closed = []

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)

        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
                ],
                model="synthetic-backup",
                id="test",
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
            )

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(
        backup_model,
        "configuration",
        lambda: SimpleNamespace(
            base_url="https://backup.example/v1",
            api_key=SecretStr("synthetic-secret"),
            model="synthetic-backup",
            provider="backup.example",
        ),
    )
    monkeypatch.setattr(backup_model, "configure_deepseek", lambda settings: Client())
    repo = Memory()
    state = {"calls": [{}]}

    async def run():
        async with backup_model.open_backup(Settings(), repo=repo, state=state) as backup:
            return await backup.port.generate(
                ModelRequest(
                    messages=(ModelMessage(role="user", content="JSON"),),
                    response_model=agents.Plan,
                    response_schema_version="test",
                    prompt_version="test",
                )
            )

    result = asyncio.run(run())
    assert result.model == "synthetic-backup" and closed == [True]
    assert state["calls"][0]["raw_model_response_ref"] and content.encode() in repo.values
    assert "synthetic-secret" not in str(repo.values)
