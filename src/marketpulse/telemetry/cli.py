"""Operational commands; only synthetic diagnostics are emitted by smoke."""

import argparse
import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx

from .config import TraceConfig
from .tracing import Runtime, set_runtime, stage, workflow


@stage("diagnostic.local_tool", as_type="tool")
def local_tool():
    return {"status": "OK", "count": 1}


@workflow("alphaagent.langfuse.acceptance")
async def diagnostic(repo, run_id):
    await asyncio.gather(asyncio.to_thread(local_tool), asyncio.to_thread(local_tool))
    return {"status": "COMPLETED", "schema_version": "synthetic-observability-v1"}


def check(config):
    import httpx

    response = httpx.get(
        config.base_url + "/api/public/projects",
        auth=(config.public_key, config.secret_key),
        timeout=config.timeout,
    )
    response.raise_for_status()
    return len(response.json().get("data", []))


@workflow("alphaagent.langfuse.model_acceptance")
async def model_diagnostic(repo, run_id, settings):
    from typing import Literal

    from pydantic import BaseModel

    from marketpulse.agents.factory import configure_deepseek
    from marketpulse.investigation.adapters.model import OpenAICompatibleModelAdapter
    from marketpulse.investigation.ports.external import ModelMessage, ModelRequest

    from .model import ObservedModelPort
    from .tracing import observation

    class Reply(BaseModel):
        status: Literal["OK"]

    client = configure_deepseek(settings)
    try:
        port = ObservedModelPort(
            OpenAICompatibleModelAdapter(
                client, provider="deepseek", default_model=settings.model, thinking_enabled=False
            ),
            {"run_id": run_id, "calls": [{"role": "Diagnostics", "attempt": 1}]},
        )
        with observation("agent.diagnostics", as_type="agent"):
            response = await port.generate(
                ModelRequest(
                    messages=(
                        ModelMessage(
                            role="user",
                            content=(
                                'This is a synthetic connectivity test. Return {"status":"OK"}.'
                            ),
                        ),
                    ),
                    response_model=Reply,
                    response_schema_version="diagnostic-v1",
                    prompt_version="diagnostic-v1",
                    model_hint=settings.model,
                    max_output_tokens=128,
                    thinking_enabled=False,
                )
            )
            with observation("output.validate"):
                assert response.output.status == "OK"
        return {
            "status": "COMPLETED",
            "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
    finally:
        await client.close()


def smoke(config, *, wait_seconds=90, model_settings=None):
    runtime = Runtime(config, background=True)
    previous = set_runtime(runtime)
    run_id = "TRACE-ACCEPTANCE-" + uuid4().hex
    try:
        if model_settings is None:
            diagnostic_result = asyncio.run(diagnostic(None, run_id))
        else:
            diagnostic_result = asyncio.run(model_diagnostic(None, run_id, model_settings))
        expected_observations = 3 if model_settings is None else 4
        runtime.flush()
        row = runtime.outbox.runs(run_id)[-1]
        trace_id = row["trace_id"]
        deadline = time.monotonic() + wait_seconds
        last_error = None
        data = []
        while time.monotonic() < deadline:
            try:
                response = httpx.get(
                    config.base_url + "/api/public/v2/observations",
                    params={"traceId": trace_id, "fields": "core,basic", "limit": 100},
                    auth=(config.public_key, config.secret_key),
                    timeout=10,
                    follow_redirects=False,
                )
                response.raise_for_status()
                data = list({item["id"]: item for item in response.json().get("data", [])}.values())
                if len(data) >= expected_observations:
                    break
            except Exception as error:
                last_error = type(error).__name__
            time.sleep(min(3, max(0, deadline - time.monotonic())))
        roots = [item for item in data if not item.get("parentObservationId")]
        valid = (
            len(data) == expected_observations
            and len(roots) == 1
            and all(
                item.get("parentObservationId") in {x["id"] for x in data}
                for item in data
                if item["id"] != roots[0]["id"]
            )
        )
        result = {
            "run_id": run_id,
            "trace_id": trace_id,
            "synthetic": True,
            "model_calls": 0 if model_settings is None else 1,
            "diagnostic_result": diagnostic_result,
            "observations_read_back": len(data),
            "parenting_verified": valid,
            "outbox": runtime.outbox.status(),
            "trace_url": runtime.trace_url(trace_id),
        }
        if not valid:
            result["readback_error"] = last_error or "INCOMPLETE_OBSERVATION_TREE"
        return result
    finally:
        set_runtime(previous)
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["check", "status", "flush", "smoke", "model-smoke", "seed-regression"]
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wait-seconds", type=int, default=90)
    args = parser.parse_args()
    config = TraceConfig.from_env(args.env_file)
    if args.journal:
        config = replace(config, journal_path=args.journal)
    if args.command == "seed-regression":
        from .evaluation import sync_seed

        result = sync_seed(
            config, Path(__file__).parents[1] / "quant" / "decision" / "evaluation_cases.json"
        )
    elif args.command == "check":
        result = {"authenticated_projects": check(config)}
    elif args.command in {"smoke", "model-smoke"}:
        if not config.configured:
            raise SystemExit("Configure Langfuse project keys before smoke.")
        model_settings = None
        if args.command == "model-smoke":
            from marketpulse.config import Settings

            model_settings = Settings.from_env(env_file=args.env_file, require_api_key=True)
        result = smoke(config, wait_seconds=args.wait_seconds, model_settings=model_settings)
    else:
        runtime = Runtime(config, background=False)
        try:
            if not runtime.outbox:
                result = {"enabled": False, "configured": config.configured}
            else:
                if args.command == "flush":
                    runtime.flush()
                result = runtime.outbox.status()
        finally:
            runtime.close()
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
    if args.command in {"smoke", "model-smoke"} and not result["parenting_verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
