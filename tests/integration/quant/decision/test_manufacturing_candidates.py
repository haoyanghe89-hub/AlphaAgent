"""Manufacturing publication and business-boundary checks, all sources/models synthetic."""

import pytest
from fastapi.testclient import TestClient
from test_candidates_api import Port, completed, payload, source_fixture
from test_decision_api import setup as setup

from marketpulse.quant.decision import screening


class ManufacturingPort(Port):
    async def generate(self, request):
        result = await super().generate(request)
        if request.response_model is screening.CandidateAnalysis:
            values = result.output.model_dump(mode="json")
            for value in values["candidates"]:
                value.update(
                    reason="晶圆制造有业务依据，利用率与资本开支资料尚缺，保留条件。",
                    supporting_reasons=["公司披露晶圆制造代工业务。"],
                    verification_conditions=["核查产能利用率、资本开支与折旧口径。"],
                )
            result.output = screening.CandidateAnalysis.model_validate(values)
        return result


def test_manufacturing_template_publication_does_not_reuse_equipment_chain(setup):
    app, _ = setup
    app.state.decision_model_port = ManufacturingPort()
    app.state.settings.model_read_timeout_seconds = 30
    app.state.decision_candidate_collector = lambda repo, *args: source_fixture(
        repo, manufacturing=True
    )
    with TestClient(app) as client:
        state = completed(client, payload() | {"entity": "集成电路制造"})
        assert state["status"] == "COMPLETED", state.get("error")
        data = state["data"]
        ctx = app.state.decision_store.value(data["analysis_context_ref"])
        assert ctx["business_profile"]["role"] == "MANUFACTURING"
        assert all(r["business_group"] == "MANUFACTURING_FOUNDRY" for r in data["members"])
        assert "统一口径产能利用率" in ctx["business_profile"]["missing_inputs"]
        assert state["model_calls"] == 4
        assert (
            client.get("/api/quant/decision/runs/" + state["run_id"] + "/export").status_code == 200
        )
        proposal = screening.CandidateAnalysis.model_validate(
            dict(summary=data["report"]["summary"], candidates=data["report"]["candidates"])
        )
        changed = proposal.candidates[0].model_copy(
            update=dict(reason="公司已经满产，产能利用率提升。")
        )
        with pytest.raises(ValueError, match="manufacturing operating"):
            screening.guard_analysis(
                proposal.model_copy(update=dict(candidates=(changed, *proposal.candidates[1:]))),
                ctx,
            )
