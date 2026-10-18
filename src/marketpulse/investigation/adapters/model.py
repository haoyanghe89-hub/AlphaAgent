from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from marketpulse.investigation.agents.contracts import AnalysisInput, AnalysisProposal
from marketpulse.investigation.agents.normalization import normalize_existing_claim_references
from marketpulse.investigation.ports.external import (
    ModelRequest,
    ModelUsage,
    StructuredModelResult,
)
from marketpulse.investigation.recording.errors import (
    InvalidProviderResponseError,
    ModelOutputTruncatedError,
    ProviderCallError,
    RateLimitedError,
)

T = TypeVar("T", bound=BaseModel)


def _schema_fields(schema: Any) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    fields = set(schema.get("properties", {}))
    for value in schema.values():
        if isinstance(value, dict):
            fields.update(_schema_fields(value))
        elif isinstance(value, list):
            for item in value:
                fields.update(_schema_fields(item))
    return fields


def _json_payload(content: str) -> str:
    value = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, re.DOTALL)
    if fenced:
        value = fenced.group(1).strip()
    return value


class OpenAICompatibleModelAdapter:
    """Generic structured-output adapter; prompts remain supplied by callers."""

    def __init__(
        self,
        client: Any,
        *,
        provider: str,
        default_model: str,
        thinking_enabled: bool | None = None,
        response_observer: Callable[[ModelRequest[Any], str], None] | None = None,
    ) -> None:
        self._client = client
        self._provider = provider
        self._default_model = default_model
        self._thinking_enabled = thinking_enabled
        # Explicit opt-in diagnostics only; production never logs raw provider content.
        self._response_observer = response_observer

    async def generate(self, request: ModelRequest[T]) -> StructuredModelResult[T]:
        model = request.model_hint or self._default_model
        schema = json.dumps(request.response_model.model_json_schema(), ensure_ascii=False)
        messages = [message.model_dump() for message in request.messages]
        messages.append(
            {
                "role": "system",
                "content": f"Return only one JSON object matching this schema: {schema}",
            }
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        thinking = request.thinking_enabled
        if thinking is None:
            thinking = self._thinking_enabled
        if thinking is not True:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_tokens"] = request.max_output_tokens
        if thinking is not None and model.startswith("deepseek-"):
            kwargs["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}
            if thinking and request.reasoning_effort:
                kwargs["reasoning_effort"] = request.reasoning_effort
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as error:
            if getattr(error, "status_code", None) == 429:
                raise RateLimitedError("model provider rate limited the request") from error
            raise ProviderCallError("model provider request failed") from error
        usage = getattr(response, "usage", None)
        usage_fields = dict(
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )
        try:
            content = response.choices[0].message.content
            if isinstance(content, str) and self._response_observer is not None:
                self._response_observer(request, content)
            if getattr(response.choices[0], "finish_reason", None) == "length":
                raise ModelOutputTruncatedError("model output token limit reached", **usage_fields)
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty model content")
            payload = _json_payload(content)
            if request.response_model is AnalysisProposal:
                for message in request.messages:
                    if message.role != "user":
                        continue
                    try:
                        context = json.loads(message.content)["bounded_context"]
                    except (ValueError, KeyError, TypeError):
                        continue
                    normalized = normalize_existing_claim_references(
                        json.loads(payload), AnalysisInput.model_validate(context)
                    )
                    payload = json.dumps(normalized, ensure_ascii=False)
                    break
            output = request.response_model.model_validate_json(payload)
        except ValidationError as error:
            # Only schema paths and error codes, never response text or rejected values.
            fields = _schema_fields(request.response_model.model_json_schema())
            issues = tuple(
                ".".join(
                    str(part) if isinstance(part, int) or part in fields else "<field>"
                    for part in item["loc"]
                )
                + ": "
                + item["type"]
                for item in error.errors(include_input=False, include_context=False)[:12]
            )
            raise InvalidProviderResponseError(
                "model response failed typed validation", validation_issues=issues, **usage_fields
            ) from error
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise InvalidProviderResponseError(
                "model response failed typed validation", **usage_fields
            ) from error
        usage = getattr(response, "usage", None)
        return StructuredModelResult[T](
            output=output,
            provider=self._provider,
            model=model,
            usage=ModelUsage(
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
            ),
            response_id=getattr(response, "id", None),
        )
