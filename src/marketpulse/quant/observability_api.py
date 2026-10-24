"""Local developer access to export health and business-to-Langfuse links."""

from fastapi import APIRouter, Request

from marketpulse.telemetry.tracing import get_runtime

from .knowledge.planning_api import local_write

router = APIRouter(prefix="/api/quant/observability", tags=["Observability"])


@router.get("/status")
def status(request: Request):
    runtime = get_runtime()
    return {
        "enabled": runtime.client is not None,
        "active_jobs": sum(
            not task.done() for task in getattr(request.app.state, "research_jobs", {}).values()
        ),
        **(runtime.outbox.status() if runtime.outbox else {"configured": False}),
    }


@router.get("/runs/{run_id}")
def run_traces(run_id: str):
    runtime = get_runtime()
    rows = runtime.outbox.runs(run_id) if runtime.outbox else []
    for row in rows:
        row["trace_url"] = runtime.trace_url(row["trace_id"])
    return {"run_id": run_id, "segments": rows}


@router.post("/flush")
async def flush(request: Request):
    import asyncio

    local_write(request)
    await asyncio.to_thread(get_runtime().flush)
    return status(request)
