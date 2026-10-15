"""Append-only status and linked forecasts on the existing isolated workflow fixture."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from test_decision_api import fixed_sources as fixed_sources
from test_decision_api import setup as setup
from test_p2_workflow import complete
from test_p2_workflow import configured as configured

from marketpulse.quant.decision.contracts import ForecastRecord
from marketpulse.quant.decision.forecasts import record_forecast


def test_withdrawal_keeps_original_and_future_observation(configured):
    app, _, _ = configured
    with TestClient(app) as client:
        state = complete(client)
        rid = state["run_id"]
        path = "/api/quant/decision/runs/" + rid
        original = client.get(path).json()
        response = client.post(
            path + "/withdraw", json=dict(reason="合成用户撤回标记，尚未独立核定触发事实")
        )
        assert response.status_code == 200
        withdrawal = response.json()
        assert withdrawal["identity"] == "USER_MARKED_NOT_FACT_VERIFIED"
        assert (
            client.post(path + "/withdraw", json=dict(reason="不得覆盖首次撤回原因")).json()
            == withdrawal
        )
        assert client.get(path).json() == original
        assert client.post(path + "/observe").status_code == 200
        observations = client.get(path + "/outcomes").json()["observations"]
        assert any(r.get("kind") == "USER_WITHDRAWAL" for r in observations)
        assert all(o["security_return"] is None for r in observations for o in r["outcomes"])
        assert (
            client.post(
                path + "/withdraw", json=dict(reason="", observed_at="2020-01-01")
            ).status_code
            == 422
        )


def test_original_view_can_find_later_forecast_versions(configured):
    app, _, _ = configured
    with TestClient(app) as client:
        state = complete(client)
        record = ForecastRecord.model_validate(state["data"]["forecast"])
        child_id = "RUN-DEC-" + uuid4().hex
        child = record.model_copy(
            update=dict(
                forecast_id="FC-" + child_id,
                run_id=child_id,
                issued_at=datetime.now(UTC),
                supersedes_id=record.forecast_id,
            )
        )
        record_forecast(app.state.decision_store, child)
        chain = client.get("/api/quant/decision/runs/" + state["run_id"] + "/forecast").json()
        assert chain["current"]["forecast_id"] == record.forecast_id
        assert [r["forecast_id"] for r in chain["versions"]] == [
            record.forecast_id,
            child.forecast_id,
        ]
