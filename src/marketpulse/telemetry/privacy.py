"""Export-stage defense for SDK or third-party attributes bypassing application scopes."""

import json

from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

from .content import PUBLIC_CONTENT_POLICY, public_payload
from .tracing import _FIELDS, projection


def mask_spans(*, params):
    patches = {}
    for identifier, span in params.spans.items():
        deleted = []
        replaced = {}
        policy = span.attributes.get("langfuse.observation.metadata.content_policy")
        try:
            policy = json.loads(policy) if isinstance(policy, str) else policy
        except ValueError:
            pass
        for key, value in span.attributes.items():
            if key in {"langfuse.observation.input", "langfuse.observation.output"}:
                try:
                    payload = json.loads(value)
                    encoded_json = True
                except (ValueError, TypeError):
                    if policy != PUBLIC_CONTENT_POLICY or not isinstance(value, str):
                        deleted.append(key)
                        continue
                    payload, encoded_json = value, False
                try:
                    safe = (
                        public_payload(payload)[0]
                        if policy == PUBLIC_CONTENT_POLICY
                        else projection(payload)
                        if isinstance(payload, dict)
                        else {}
                    )
                    replaced[key] = json.dumps(safe, ensure_ascii=False) if encoded_json else safe
                except (ValueError, TypeError):
                    deleted.append(key)
            elif key.startswith(("langfuse.trace.metadata.", "langfuse.observation.metadata.")):
                if key.rsplit(".", 1)[-1] not in _FIELDS:
                    deleted.append(key)
            elif key.startswith(
                (
                    "exception.",
                    "http.request.header.",
                    "http.response.header.",
                    "gen_ai.prompt.",
                    "gen_ai.completion.",
                )
            ):
                deleted.append(key)
        if deleted or replaced:
            patches[identifier] = OtelSpanPatch(
                delete_attributes=tuple(deleted), set_attributes=replaced
            )
    return MaskOtelSpansResult(span_patches=patches)
