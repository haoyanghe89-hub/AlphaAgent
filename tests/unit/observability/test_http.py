import asyncio

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from marketpulse.telemetry.http import install_http_tracing
from marketpulse.telemetry.tracing import workflow

from .test_runtime import receiver as receiver
from .test_runtime import runtime as runtime
from .test_runtime import spans


def test_http_202_and_background_run_have_linked_independent_lifetimes(runtime, receiver):
    app = FastAPI()
    install_http_tracing(app)
    jobs = []
    gate = None

    @workflow("alphaagent.background")
    async def background(repo, run_id):
        await gate.wait()
        return {"status": "COMPLETED"}

    @app.post("/api/quant/trace-test")
    async def start():
        nonlocal gate
        gate = asyncio.Event()
        jobs.append(asyncio.create_task(background(None, "RUN-BACKGROUND")))
        return JSONResponse({"status": "RUNNING"}, status_code=202)

    @app.post("/api/quant/release")
    async def release():
        gate.set()
        await asyncio.gather(*jobs)
        return {"status": "COMPLETED"}

    with TestClient(app) as client:
        response = client.post("/api/quant/trace-test")
        assert response.status_code == 202
        request_trace = response.headers["X-Langfuse-Request-Trace-Id"]
        assert client.post("/api/quant/release").status_code == 200
    runtime.flush()
    business = next(s for s in spans(receiver[2]) if s.name == "alphaagent.background")
    assert business.trace_id.hex() != request_trace
    assert not business.parent_span_id
    assert runtime.outbox.runs("RUN-BACKGROUND")[-1]["state"] == "COMPLETED"
