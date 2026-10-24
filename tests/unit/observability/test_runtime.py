import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from marketpulse.telemetry.config import TraceConfig
from marketpulse.telemetry.tracing import Runtime, set_runtime, stage, workflow


@pytest.fixture
def receiver():
    received = []

    class Handler(BaseHTTPRequestHandler):
        code = 200

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, body))
            self.send_response(type(self).code)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, Handler, received
    server.shutdown()
    thread.join()


@pytest.fixture
def runtime(tmp_path, receiver):
    server, _, _ = receiver
    config = TraceConfig(
        enabled=True,
        public_key="pk-lf-test-" + tmp_path.name,
        secret_key="sk-lf-test",
        base_url=f"http://127.0.0.1:{server.server_port}",
        journal_path=tmp_path / "traces.sqlite3",
        timeout=1,
    )
    runtime = Runtime(config, background=False)
    previous = set_runtime(runtime)
    yield runtime
    set_runtime(previous)
    runtime.close()


def spans(received):
    result = []
    for path, body in received:
        if path.endswith("/traces"):
            packet = ExportTraceServiceRequest.FromString(body)
            for resource in packet.resource_spans:
                for scope in resource.scope_spans:
                    result.extend(scope.spans)
    return result


def test_disabled_does_not_create_files_or_export(tmp_path):
    runtime = Runtime(TraceConfig(journal_path=tmp_path / "disabled.sqlite3"))
    with runtime.observation("disabled"):
        pass
    runtime.close()
    assert not (tmp_path / "disabled.sqlite3").exists()


def test_retry_keeps_original_spans_and_no_secrets(runtime, receiver):
    _, handler, received = receiver
    handler.code = 503
    with runtime.observation("root", as_type="agent"):
        with runtime.observation("child") as child:
            child.update(output={"password": "PRIVATE-MARKER", "shares": 1000})
    runtime.flush()
    assert runtime.outbox.status()["pending"] > 0
    assert runtime.outbox.status()["delivery_errors"] == 1
    handler.code = 200
    runtime.flush()
    assert runtime.outbox.status()["pending"] == 0
    accepted = spans(received)
    assert {s.name for s in accepted} == {"root", "child"}
    root = next(s for s in accepted if s.name == "root")
    child = next(s for s in accepted if s.name == "child")
    assert child.parent_span_id == root.span_id
    assert not any(b"PRIVATE-MARKER" in body for _, body in received)
    assert not any(b"sk-lf-test" in body for _, body in received)


@pytest.mark.asyncio
async def test_parallel_context_and_resume_segments(runtime, receiver):
    class Repo:
        def get(self, rid, with_data=False):
            return {"run_id": rid, "request": {"question": "PRIVATE-QUESTION"}, "status": "RUNNING"}

    @stage("work")
    def work():
        return {"secret": "PRIVATE-RESULT"}

    @workflow("alphaagent.test")
    async def run(repo, run_id):
        await asyncio.gather(asyncio.to_thread(work), asyncio.to_thread(work))
        return {"status": "COMPLETED", "result_ref": "blob://sha256/" + "a" * 64}

    await run(Repo(), "RUN-TEST")
    await run(Repo(), "RUN-TEST")
    runtime.flush()
    rows = runtime.outbox.runs("RUN-TEST")
    assert len(rows) == 2
    assert rows[1]["resumed_from_trace_id"] == rows[0]["trace_id"]
    assert rows[0]["trace_id"] != rows[1]["trace_id"]
    actual = spans(receiver[2])
    assert len(actual) == 6
    roots = [s for s in actual if s.name == "alphaagent.test"]
    for child in (s for s in actual if s.name == "work"):
        parent = next(s for s in roots if s.trace_id == child.trace_id)
        assert child.parent_span_id == parent.span_id
    assert not any(b"PRIVATE-" in body for _, body in receiver[2])


def test_invalid_config_does_not_accept_credential_in_url(tmp_path):
    with pytest.raises(ValueError):
        TraceConfig(
            enabled=True, base_url="https://user:secret@example.com", journal_path=tmp_path / "a"
        )


def test_scores_are_durable_and_unknown_is_categorical(runtime, receiver):
    runtime.score("a" * 32, "grading_status", "UNCERTAIN", version="v1")
    runtime.flush()
    scores = [json.loads(body)[0] for path, body in receiver[2] if path == "/api/public/scores"]
    assert len(scores) == 1
    assert scores[0]["dataType"] == "CATEGORICAL"
    assert scores[0]["value"] == "UNCERTAIN"


def test_async_http_202_is_accepted(runtime, receiver):
    receiver[1].code = 202
    runtime.score("b" * 32, "delivery_success", True, version="v1")
    runtime.flush()
    assert runtime.outbox.status()["pending"] == 0


def test_async_json_otlp_acknowledgement_is_accepted(runtime, monkeypatch):
    import httpx

    runtime.outbox.enqueue("/api/public/otel/v1/traces", "application/x-protobuf", b"test")
    monkeypatch.setattr(
        runtime.outbox.transport,
        "post",
        lambda *a, **k: httpx.Response(200, json={"id": "queue-job", "name": "ingest"}),
    )
    runtime.flush()
    assert runtime.outbox.status()["pending"] == 0


def test_delivery_reuses_one_client_and_closes_after_final_drain(runtime, monkeypatch):
    import httpx

    transport = runtime.outbox.transport
    sent = []

    def post(*args, **kwargs):
        assert not transport.is_closed
        sent.append(args[0])
        return httpx.Response(202)

    monkeypatch.setattr(transport, "post", post)
    for i in range(3):
        runtime.score(str(i) * 32, "delivery", True, version="test")
    runtime.flush()
    assert len(sent) == 3 and not transport.is_closed
    runtime.score("d" * 32, "delivery", True, version="test")
    runtime.close()
    assert len(sent) == 4
    assert transport.is_closed
    assert runtime.outbox.status()["pending"] == 0
    runtime.close()


def test_untraced_history_validation_does_not_create_new_execution_trace(runtime, receiver):
    @stage("history.validate")
    def validate_history():
        return {"status": "COMPLETED"}

    assert validate_history()["status"] == "COMPLETED"
    runtime.flush()
    assert not spans(receiver[2])
