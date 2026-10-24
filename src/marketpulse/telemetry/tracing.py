"""Real SDK observation scopes, isolated from business state and legacy logging."""

import functools
import inspect
import json
import logging
import math
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import uuid4

from .config import TraceConfig
from .content import PUBLIC_CONTENT_POLICY, public_payload, validation_failure

logger = logging.getLogger(__name__)
_current_run = ContextVar("alphaagent_trace_run", default=None)
_current_session = ContextVar("alphaagent_trace_session", default=None)

# Default metadata/reference policy; public content requires explicit opt-in.
_FIELDS = frozenset(
    (
        "run_id root_run_id parent_run_id source_report_version session_id "
        "schema_version workflow prompt_version knowledge_version data_as_of role "
        "iteration attempt call_id call_index request_ref response_ref result_ref "
        "result_sha256 input_ref context_ref context_hash sha256 status stage model "
        "provider response_id error_type error_code retryable count evidence_count "
        "evidence_ids rule_ids retrieval_version source_family host method cache_hit "
        "purpose input_tokens output_tokens usage_known resume_segment "
        "resumed_from_trace_id function field_count request_trace_id route http_status "
        "content_policy input_redactions output_redactions input_truncated output_truncated "
        "raw_model_response_ref"
    ).split()
)


def projection(value):
    if isinstance(value, dict):
        return {str(k): projection(v) for k, v in value.items() if k in _FIELDS}
    if isinstance(value, (list, tuple)):
        return [projection(v) for v in value[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]


class SafeObservation:
    def __init__(self, actual=None, content_policy=None):
        self.actual = actual
        self.content_policy = content_policy

    @property
    def trace_id(self):
        return self.actual.trace_id if self.actual else None

    @property
    def id(self):
        return self.actual.id if self.actual else None

    def update(self, **fields):
        if self.actual is None:
            return
        safe = {}
        content_metadata = {}
        for key, value in fields.items():
            if key in {"input", "output"} and self.content_policy == PUBLIC_CONTENT_POLICY:
                safe[key], info = public_payload(value)
                content_metadata.update({key + "_" + k: v for k, v in info.items()})
            elif key in {"input", "output", "metadata"}:
                safe[key] = (
                    projection(value)
                    if isinstance(value, dict)
                    else {"field_count": len(value) if isinstance(value, (list, tuple)) else 1}
                )
            elif key == "usage_details":
                safe[key] = {k: v for k, v in value.items() if type(v) is int and v >= 0}
            elif key in {
                "model",
                "model_parameters",
                "completion_start_time",
                "level",
                "status_message",
            }:
                safe[key] = value
        if content_metadata:
            safe.setdefault("metadata", {}).update(content_metadata)
            safe["metadata"]["content_policy"] = self.content_policy
        try:
            self.actual.update(**safe)
        except Exception as error:
            logger.warning("Observation update failed: %s", type(error).__name__)


class Runtime:
    def __init__(self, config=None, *, background=True):
        self.config = config or TraceConfig()
        self.client = None
        self.outbox = None
        self.provider = None
        self._stop = threading.Event()
        self._thread = None
        if not self.config.enabled:
            return
        from langfuse import Langfuse
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        from .outbox import Outbox
        from .privacy import mask_spans

        self.outbox = Outbox(self.config)
        self.provider = TracerProvider(resource=Resource({"service.name": "alphaagent"}))
        self.client = Langfuse(
            public_key=self.config.public_key or "pk-lf-local-" + uuid4().hex,
            secret_key=self.config.secret_key or "sk-lf-local",
            base_url=self.config.base_url,
            environment=self.config.environment,
            tracer_provider=self.provider,
            span_exporter=self.outbox,
            mask_otel_spans=mask_spans,
            should_export_span=lambda span: (
                span.instrumentation_scope is not None
                and span.instrumentation_scope.name == "langfuse-sdk"
            ),
            sample_rate=1.0,
            flush_at=32,
            flush_interval=0.5,
            timeout=3,
        )
        if background and self.config.configured:
            self._thread = threading.Thread(
                target=self._deliver, name="langfuse-outbox", daemon=True
            )
            self._thread.start()

    def _deliver(self):
        while not self._stop.wait(1):
            try:
                self.outbox.drain(limit=4, force=False)
            except Exception as error:
                logger.warning("Observation delivery failed: %s", type(error).__name__)

    @contextmanager
    def observation(self, name, *, as_type="span", content_policy=None, **attributes):
        if self.client is None:
            yield SafeObservation()
            return
        manager = None
        metadata = projection(attributes.pop("metadata", {}))
        if content_policy == PUBLIC_CONTENT_POLICY:
            metadata["content_policy"] = content_policy
        for key in ("input", "output"):
            if key in attributes:
                if content_policy == PUBLIC_CONTENT_POLICY:
                    attributes[key], info = public_payload(attributes[key])
                    metadata.update({key + "_" + k: v for k, v in info.items()})
                else:
                    attributes[key] = (
                        projection(attributes[key]) if isinstance(attributes[key], dict) else {}
                    )
        try:
            manager = self.client.start_as_current_observation(
                name=name,
                as_type=as_type,
                metadata=metadata,
                **attributes,
            )
            actual = manager.__enter__()
        except Exception as error:
            logger.warning("Observation creation failed: %s", type(error).__name__)
            yield SafeObservation()
            return
        observation = SafeObservation(actual, content_policy)
        try:
            yield observation
        except BaseException as error:
            observation.update(
                level="ERROR",
                status_message=type(error).__name__,
                metadata={"error_type": type(error).__name__},
            )
            raise
        finally:
            try:
                # Never let the SDK capture exception messages/stack locals containing account data.
                manager.__exit__(None, None, None)
            except Exception as error:
                logger.warning("Observation close failed: %s", type(error).__name__)

    def score(self, trace_id, name, value, *, version, observation_id=None):
        if self.outbox is None:
            return False
        if (
            not isinstance(value, (bool, int, float, str))
            or isinstance(value, float)
            and not math.isfinite(value)
        ):
            raise ValueError("Score needs a finite value or an explicit categorical status")
        kind = (
            "BOOLEAN"
            if isinstance(value, bool)
            else "NUMERIC"
            if isinstance(value, (int, float))
            else "CATEGORICAL"
        )
        if kind == "BOOLEAN":
            value = int(value)
        payload = {
            "id": uuid4().hex,
            "traceId": trace_id,
            "name": name,
            "value": float(value) if kind in {"BOOLEAN", "NUMERIC"} else value,
            "timestamp": datetime.now(UTC).isoformat(),
            "dataType": kind,
            "comment": "grader_version=" + version,
        }
        if observation_id:
            payload["observationId"] = observation_id
        try:
            return self.outbox.enqueue(
                "/api/public/scores", "application/json", json.dumps([payload]).encode()
            )
        except Exception as error:
            logger.warning("Score journal failed: %s", type(error).__name__)
            return False

    def trace_url(self, trace_id):
        if not self.client or not self.config.configured:
            return None
        try:
            return self.client.get_trace_url(trace_id=trace_id)
        except Exception as error:
            logger.warning("Trace link lookup failed: %s", type(error).__name__)
            return None

    def flush(self):
        if self.provider:
            self.provider.force_flush(timeout_millis=5000)
        if self.outbox:
            return self.outbox.drain(limit=8)
        return 0

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=4 * self.config.timeout + 1)
        if self.provider:
            self.provider.shutdown()
            self.provider = None
        if self.outbox:
            self.outbox.drain(limit=1)
            self.outbox.close()


_runtime = Runtime()


def get_runtime():
    return _runtime


def current_trace_id():
    from opentelemetry import trace

    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None


def set_runtime(value):
    global _runtime
    previous = _runtime
    _runtime = value
    return previous


def configure(config=None):
    runtime = Runtime(config or TraceConfig.from_env())
    previous = set_runtime(runtime)
    previous.close()
    return runtime


@contextmanager
def observation(name, *, as_type="span", content_policy=None, **attributes):
    with get_runtime().observation(
        name, as_type=as_type, content_policy=content_policy, **attributes
    ) as current:
        yield current


def summary(value):
    if not isinstance(value, dict):
        return {}
    result = projection(value)
    if isinstance(value.get("evidence"), list):
        result["evidence_count"] = len(value["evidence"])
        result["evidence_ids"] = [
            e["evidence_id"]
            for e in value["evidence"][:100]
            if isinstance(e, dict) and "evidence_id" in e
        ]
    return result


def capture_content(function):
    try:
        return function()
    except Exception as error:
        logger.warning("Observation content unavailable: %s", type(error).__name__)
        return "OBSERVABILITY_GAP：内容快照构建失败，业务执行结果不受影响。"


def stage(name, *, as_type="span", content_policy=None, inputs=None, outputs=None):
    def decorate(function):
        signature = inspect.signature(function)

        def scope(args, kwargs):
            arguments = signature.bind_partial(*args, **kwargs).arguments
            role = arguments.get("role")
            current_name = (
                "agent." + role.lower()
                if as_type == "agent" and isinstance(role, str)
                else "tool." + str(arguments["key"])
                if as_type == "tool" and "key" in arguments
                else name
            )
            metadata = {"function": function.__name__}
            if role:
                metadata["role"] = role
            attributes = {"metadata": metadata}
            if inputs:
                attributes["input"] = capture(lambda: inputs(arguments))
            return observation(
                current_name, as_type=as_type, content_policy=content_policy, **attributes
            ), arguments

        def capture(function):
            try:
                return function()
            except Exception as error:
                logger.warning("Observation content unavailable: %s", type(error).__name__)
                return "OBSERVABILITY_GAP：内容快照构建失败，业务执行结果不受影响。"

        def output(result, arguments):
            return capture(lambda: outputs(result, arguments)) if outputs else summary(result)

        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def asynchronous(*args, **kwargs):
                runtime = get_runtime()
                if runtime.client is None or current_trace_id() is None:
                    return await function(*args, **kwargs)
                manager, arguments = scope(args, kwargs)
                with manager as current:
                    try:
                        result = await function(*args, **kwargs)
                    except Exception as error:
                        if content_policy == PUBLIC_CONTENT_POLICY:
                            current.update(output=validation_failure(error))
                        raise
                    current.update(output=output(result, arguments))
                    return result

            return asynchronous

        @functools.wraps(function)
        def synchronous(*args, **kwargs):
            runtime = get_runtime()
            if runtime.client is None or current_trace_id() is None:
                return function(*args, **kwargs)
            manager, arguments = scope(args, kwargs)
            with manager as current:
                try:
                    result = function(*args, **kwargs)
                except Exception as error:
                    if content_policy == PUBLIC_CONTENT_POLICY:
                        current.update(output=validation_failure(error))
                    raise
                current.update(output=output(result, arguments))
                return result

        return synchronous

    return decorate


def workflow(name, *, id_parameter="run_id"):
    def decorate(function):
        signature = inspect.signature(function)

        @functools.wraps(function)
        async def execute(*args, **kwargs):
            runtime = get_runtime()
            if runtime.client is None:
                return await function(*args, **kwargs)
            arguments = signature.bind_partial(*args, **kwargs).arguments
            run_id = arguments[id_parameter]
            parent = _current_run.get()
            if parent == run_id:
                with observation("execution.continue", as_type="chain"):
                    return await function(*args, **kwargs)
            token = _current_run.set(run_id)
            segment = None
            session_token = None
            otel_token = None
            terminal = "INTERRUPTED"
            try:
                from langfuse import propagate_attributes

                upstream = current_trace_id() if not parent else None
                if upstream:
                    from opentelemetry import context as otel_context

                    # SDK trace_context without a parent creates a synthetic remote parent.
                    otel_token = otel_context.attach(otel_context.Context())
                with runtime.observation(
                    name,
                    as_type="agent",
                    content_policy=(
                        PUBLIC_CONTENT_POLICY if name == "alphaagent.investment_research" else None
                    ),
                    metadata={"run_id": run_id, "request_trace_id": upstream},
                ) as root:
                    if root.actual is None:
                        return await function(*args, **kwargs)
                    session_id = _current_session.get() or parent or run_id
                    try:
                        repo = arguments.get("repo")
                        state = (
                            repo.get(run_id, with_data=False)
                            if repo and hasattr(repo, "get")
                            else {}
                        )
                        request = state.get("request", {})
                        previous = request.get("previous_run_id") or request.get("parent_run_id")
                        linked = runtime.outbox.runs(previous) if previous else []
                        if linked:
                            session_id = linked[-1]["session_id"]
                        segment, resumed = runtime.outbox.start_run(
                            run_id,
                            root.trace_id,
                            root.id,
                            session_id=session_id,
                            parent_run_id=parent or previous,
                        )
                        from .investment import request_input

                        root.update(
                            input=(
                                request_input(state)
                                if name == "alphaagent.investment_research"
                                else {"schema_version": state.get("schema_version")}
                            ),
                            metadata={
                                "run_id": run_id,
                                "parent_run_id": parent or previous,
                                "resume_segment": segment,
                                "resumed_from_trace_id": resumed,
                            },
                        )
                    except Exception as error:
                        logger.warning("Trace identity journal failed: %s", type(error).__name__)
                    session_token = _current_session.set(session_id)
                    with propagate_attributes(
                        session_id=session_id, metadata={"run_id": str(run_id), "workflow": name}
                    ):
                        result = await function(*args, **kwargs)
                        terminal = (
                            result.get("status", "UNKNOWN")
                            if isinstance(result, dict)
                            else "UNKNOWN"
                        )
                        from .investment import report_output

                        root.update(
                            output=(
                                capture_content(lambda: report_output(result))
                                if name == "alphaagent.investment_research"
                                else summary(result)
                            ),
                            status_message=terminal,
                            level="DEFAULT" if terminal == "COMPLETED" else "WARNING",
                        )
                        runtime.score(
                            root.trace_id,
                            "delivery_success",
                            terminal == "COMPLETED",
                            version="business-terminal-v1",
                            observation_id=root.id if parent else None,
                        )
                        return result
            finally:
                _current_run.reset(token)
                if session_token is not None:
                    _current_session.reset(session_token)
                if otel_token is not None:
                    otel_context.detach(otel_token)
                if segment is not None:
                    try:
                        runtime.outbox.finish_run(run_id, segment, terminal)
                    except Exception as error:
                        logger.warning("Trace terminal journal failed: %s", type(error).__name__)

        return execute

    return decorate


def link_call(state, index, kind, status, *, trace_id=None, observation_id=None):
    runtime = get_runtime()
    if runtime.outbox is None or not state.get("run_id"):
        return
    try:
        call = state["calls"][index]
        runtime.outbox.record_call(
            state.get("run_id", "unbound"),
            index,
            kind,
            trace_id or runtime.client.get_current_trace_id(),
            observation_id or runtime.client.get_current_observation_id(),
            call.get("request_ref"),
            status,
        )
    except Exception as error:
        runtime.outbox.export_errors += 1
        logger.warning("Call observation journal failed: %s", type(error).__name__)
