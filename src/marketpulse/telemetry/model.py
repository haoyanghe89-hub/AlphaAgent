"""Instrument actual model requests without changing schemas, retry or token accounting."""

from marketpulse.investigation.recording.errors import InvalidProviderResponseError

from .content import PUBLIC_CONTENT_POLICY, public_research, validation_failure
from .investment import text
from .tracing import get_runtime, link_call, observation


class ObservedModelPort:
    def __init__(self, port, state):
        self.port = port
        self.state = state

    async def generate(self, request):
        if get_runtime().client is None:
            return await self.port.generate(request)
        calls = self.state.get("calls", [])
        call_index = len(calls) - 1
        call = calls[-1] if calls else {}
        public = public_research(self.state)
        metadata = {
            "run_id": self.state.get("run_id"),
            "call_id": str(self.state.get("run_id", "")) + ":" + str(call_index),
            "call_index": call_index,
            "role": call.get("role"),
            "attempt": call.get("attempt"),
            "purpose": call.get("purpose"),
            "prompt_version": request.prompt_version,
            "schema_version": request.response_schema_version,
            "request_ref": call.get("request_ref"),
        }
        with observation(
            "model.call",
            as_type="generation",
            model=request.model_hint,
            content_policy=PUBLIC_CONTENT_POLICY if public else None,
            metadata=metadata,
            input=(
                [{"role": message.role, "content": message.content} for message in request.messages]
                if public
                else {"request_ref": call.get("request_ref")}
            ),
            model_parameters={
                "temperature": request.temperature,
                "max_output_tokens": request.max_output_tokens,
                "thinking_enabled": request.thinking_enabled,
                "reasoning_effort": request.reasoning_effort,
            },
        ) as current:
            link_call(
                self.state,
                call_index,
                "model",
                "RUNNING",
                trace_id=current.trace_id,
                observation_id=current.id,
            )
            try:
                response = await self.port.generate(request)
            except BaseException as error:
                link_call(
                    self.state,
                    call_index,
                    "model",
                    "FAILED" if isinstance(error, InvalidProviderResponseError) else "UNKNOWN",
                    trace_id=current.trace_id,
                    observation_id=current.id,
                )
                usage = {
                    k: v
                    for k, v in {
                        "input": getattr(error, "input_tokens", None),
                        "output": getattr(error, "output_tokens", None),
                    }.items()
                    if type(v) is int and v >= 0
                }
                current.update(
                    usage_details=usage,
                    output=validation_failure(error) if public else {},
                    metadata={
                        "usage_known": len(usage) == 2,
                        "raw_model_response_ref": call.get("raw_model_response_ref"),
                    },
                )
                raise
            usage = {
                k: v
                for k, v in {
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens,
                }.items()
                if type(v) is int and v >= 0
            }
            current.update(
                model=response.model,
                usage_details=usage,
                output=(
                    [
                        {
                            "role": "assistant",
                            "content": text(response.output.model_dump(mode="json")),
                        }
                    ]
                    if public
                    else {"response_id": response.response_id}
                ),
                metadata={
                    "provider": response.provider,
                    "usage_known": len(usage) == 2,
                    "response_id": response.response_id,
                    "raw_model_response_ref": call.get("raw_model_response_ref"),
                },
            )
            link_call(
                self.state,
                call_index,
                "model",
                "COMPLETED",
                trace_id=current.trace_id,
                observation_id=current.id,
            )
            return response


def wrap_model(port, state):
    return port if isinstance(port, ObservedModelPort) else ObservedModelPort(port, state)
