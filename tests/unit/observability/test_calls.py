from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from marketpulse.investigation.ports.external import ModelMessage, ModelRequest
from marketpulse.telemetry.model import ObservedModelPort
from marketpulse.telemetry.tracing import observation

from .test_runtime import receiver as receiver
from .test_runtime import runtime as runtime
from .test_runtime import spans


class Answer(BaseModel):
    status: str


def request():
    return ModelRequest(
        messages=(ModelMessage(role="user", content="PRIVATE-PROMPT"),),
        response_model=Answer,
        response_schema_version="test-v1",
        prompt_version="test-v1",
        model_hint="synthetic",
    )


@pytest.mark.asyncio
async def test_model_retries_are_separate_and_usage_not_duplicated(runtime, receiver):
    class Port:
        count = 0

        async def generate(self, request):
            self.count += 1
            if self.count == 1:
                raise ValueError("PRIVATE-EXCEPTION")
            return SimpleNamespace(
                model="synthetic",
                provider="synthetic",
                response_id="test-id",
                usage=SimpleNamespace(input_tokens=10, output_tokens=4),
                output=Answer(status="OK"),
            )

    port = Port()
    wrapped = ObservedModelPort(
        port, {"run_id": "RUN-TEST", "calls": [{"request_ref": "blob://sha256/" + "a" * 64}]}
    )
    with observation("test.agent", as_type="agent"):
        with pytest.raises(ValueError):
            await wrapped.generate(request())
        result = await wrapped.generate(request())
    runtime.flush()
    assert result.output.status == "OK" and port.count == 2
    generations = [s for s in spans(receiver[2]) if s.name == "model.call"]
    assert len(generations) == 2
    assert len({s.span_id for s in generations}) == 2
    assert not any(b"PRIVATE-" in body for _, body in receiver[2])


def test_export_filter_blocks_direct_sdk_account_fields(runtime, receiver):
    with runtime.client.start_as_current_observation(
        name="direct.sdk",
        input={"shares": "PRIVATE-SHARES"},
        metadata={"password": "PRIVATE-KEY", "run_id": "safe-run"},
    ) as raw:
        raw.update(output={"account": "PRIVATE-ACCOUNT"})
    runtime.flush()
    assert len(spans(receiver[2])) == 1
    assert not any(b"PRIVATE-" in body for _, body in receiver[2])


def test_delivery_failure_never_raises_into_business(runtime, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("PRIVATE-FILESYSTEM")

    monkeypatch.setattr(runtime.outbox, "enqueue", unavailable)
    assert runtime.score("a" * 32, "delivery_success", True, version="v1") is False


@pytest.mark.asyncio
async def test_call_identity_is_durable_before_provider_execution(runtime):
    class Port:
        async def generate(self, request):
            with runtime.outbox.connection() as db:
                row = db.execute(
                    "SELECT state,observation_id FROM calls WHERE run_id='RUN-DURABLE'"
                ).fetchone()
                assert row["state"] == "RUNNING" and row["observation_id"]
            return SimpleNamespace(
                model="synthetic",
                provider="synthetic",
                response_id="durable-id",
                usage=SimpleNamespace(input_tokens=None, output_tokens=None),
                output=Answer(status="OK"),
            )

    state = {"run_id": "RUN-DURABLE", "calls": [{"request_ref": "blob://sha256/" + "c" * 64}]}
    await ObservedModelPort(Port(), state).generate(request())
    with runtime.outbox.connection() as db:
        assert (
            db.execute("SELECT state FROM calls WHERE run_id='RUN-DURABLE'").fetchone()[0]
            == "COMPLETED"
        )


def test_raw_sdk_exception_events_do_not_export_private_messages(runtime, receiver):
    with pytest.raises(ValueError):
        with runtime.client.start_as_current_observation(name="direct.error"):
            raise ValueError("PRIVATE-EXCEPTION-MESSAGE")
    runtime.flush()
    assert not any(b"PRIVATE-EXCEPTION-MESSAGE" in body for _, body in receiver[2])
