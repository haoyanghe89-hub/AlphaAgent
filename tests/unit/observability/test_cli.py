import httpx

from marketpulse.telemetry import cli

from .test_runtime import receiver as receiver
from .test_runtime import runtime as runtime


def test_smoke_keeps_verified_result_when_trace_link_lookup_times_out(runtime, monkeypatch):
    monkeypatch.setattr(cli, "Runtime", lambda config, background: runtime)

    def timeout(**kwargs):
        raise httpx.ReadTimeout("synthetic link lookup timeout")

    monkeypatch.setattr(runtime.client, "get_trace_url", timeout)
    monkeypatch.setattr(
        cli.httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={
                "data": [
                    {"id": "root", "parentObservationId": None},
                    {"id": "a", "parentObservationId": "root"},
                    {"id": "b", "parentObservationId": "root"},
                ]
            },
            request=httpx.Request("GET", "https://example.test/observations"),
        ),
    )
    result = cli.smoke(runtime.config, wait_seconds=1)
    assert result["parenting_verified"] is True
    assert result["model_calls"] == 0
    assert result["trace_url"] is None
